"""The cryptography behind a recoverable backup.

One job: turn the user's brain into bytes that are useless to anyone who does
not hold the user's own secret — including us, if we ever host them.

**Why there is a passphrase at all, when the user already signs in.** Social
login (Google, Apple, Microsoft) *authenticates*: it proves an identity
provider vouches for this person. It does not produce a secret only they know,
so it cannot protect data from the party holding the data. If signing in with
Google were enough to decrypt a backup, then anyone who could mint a Google
session — us, or whoever compromises that account — could read it. So identity
and encryption are separate, and the user holds one secret.
See [`docs/ACCOUNTS-DESIGN.md`](../../docs/ACCOUNTS-DESIGN.md) §4 and §9.

**The key hierarchy**, and why it has three levels rather than one:

    passphrase ─scrypt─┐
                       ├─ wraps ─▶ MK ─ wraps ─▶ DEK ─ encrypts ─▶ archive
    recovery code ─HKDF┘

* **DEK** is fresh per archive, so a generation can be expired or rotated
  without re-encrypting anything else.
* **MK** is the long-lived identity of "this user's backups", generated
  on-device and **never transmitted**.
* Wrapping MK once per unlock method is what lets a user recover with *either*
  the passphrase or the recovery code. Encrypting the archive directly with a
  passphrase-derived key would mean a changed passphrase re-encrypts every
  byte, and a lost one has no second door.

Primitives are chosen for "available without adding a dependency": `scrypt` is
memory-hard and in the Python standard library, so no Argon2 package; AES-GCM
comes from `cryptography`, which is already in the tree. There is no AEAD in
the standard library, which is why that one is needed.

**The file layout**, which is what the streaming rules below follow from:

    MAGIC(8) │ header_len(4) │ header │ nonce_base(8) │ [len(4)│sealed]…

`nonce_base` sits *before* the chunks deliberately. Putting it after them —
the first version of this module did — means a reader cannot decrypt anything
until it has read the entire file, so a multi-gigabyte restore has to be held
in memory whole and the chunking buys nothing. Everything needed to decrypt
chunk *n* is now known before chunk *n* is read.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: File format. `MAGIC` is checked before anything else so a wrong file is
#: refused with "this is not a Chitragupta backup" rather than a crypto error.
MAGIC = b"CGARCH01"
FORMAT_VERSION = 1

KEY_BYTES = 32            # AES-256
NONCE_BYTES = 12          # AES-GCM standard
NONCE_BASE_BYTES = 8      # the rest is a big-endian chunk counter
SALT_BYTES = 32

#: scrypt cost. 0.24s and ~134 MiB on an M-series Mac — measured, not guessed.
#: A backup passphrase is entered rarely, so this can be far more expensive
#: than a login would tolerate. `maxmem` must be raised explicitly or CPython
#: refuses an `n` this large.
SCRYPT_N = 1 << 17
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 1 << 29

#: 1 MiB plaintext per chunk, so a large brain never has to be held in memory
#: whole — in either direction.
CHUNK_SIZE = 1 << 20

#: Recovery code: 160 bits, far past brute force, in **Crockford base32** —
#: 32 symbols, so 20 bytes encode to exactly 32 characters with no padding and
#: no remainder. `I`, `L`, `O` and `U` are absent: the first three because they
#: are misread as `1`, `1` and `0`, and `U` by Crockford's convention so a
#: random code cannot spell something unfortunate. The decode map below is what
#: makes a misread harmless rather than a support case.
RECOVERY_BYTES = 20
RECOVERY_CHARS = 32
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {ch: i for i, ch in enumerate(_ALPHABET)}
_DECODE.update({"O": 0, "I": 1, "L": 1})

#: Bound into each wrap so a blob wrapped for one purpose cannot be presented
#: as another — AES-GCM authenticates this, so a swap fails rather than
#: silently decrypting the wrong key.
WRAP_PASSPHRASE = b"chitragupta/wrap/mk/passphrase/v1"
WRAP_RECOVERY = b"chitragupta/wrap/mk/recovery/v1"
WRAP_DEK = b"chitragupta/wrap/dek/v1"
_HKDF_INFO = b"chitragupta/recovery-code/v1"

#: The two names a wrapped MK is filed under in the header.
BY_PASSPHRASE = "passphrase"
BY_RECOVERY = "recovery"


class ArchiveError(Exception):
    """Something is wrong with the archive itself."""


class WrongSecretError(ArchiveError):
    """The passphrase or recovery code did not unwrap the key.

    Deliberately indistinguishable from a tampered wrap: both mean "this secret
    does not open this archive", and saying which would tell someone holding a
    stolen file whether they had guessed the right secret.
    """


class CorruptArchiveError(ArchiveError):
    """The archive is damaged, truncated or was modified."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except Exception as exc:                       # malformed header field
        raise CorruptArchiveError("the archive header is malformed") from exc


# ── key derivation ──────────────────────────────────────────────────────────

def scrypt_params() -> dict[str, int]:
    return {"n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}


def derive_from_passphrase(passphrase: str, salt: bytes,
                           params: dict[str, int] | None = None) -> bytes:
    """A key-encryption key from a human passphrase.

    `params` comes from the archive header rather than from the constants
    above, so raising the cost later does not orphan archives written at the
    old one.
    """
    if not passphrase:
        raise WrongSecretError("a passphrase is required")
    p = params or scrypt_params()
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt,
        n=int(p["n"]), r=int(p["r"]), p=int(p["p"]),
        maxmem=SCRYPT_MAXMEM, dklen=KEY_BYTES,
    )


def new_recovery_code() -> str:
    """A fresh recovery code, grouped in fours for reading aloud.

    Shown once, at setup, and never recoverable afterwards — rotation issues a
    new one instead. Generated independently of the passphrase, because a
    second door derived from the first is not a second door.
    """
    value = int.from_bytes(secrets.token_bytes(RECOVERY_BYTES), "big")
    body = "".join(
        _ALPHABET[(value >> (5 * i)) & 31] for i in reversed(range(RECOVERY_CHARS))
    )
    return "-".join(body[i:i + 4] for i in range(0, RECOVERY_CHARS, 4))


def normalise_recovery_code(code: str) -> str:
    """What a person typed, as the 32 symbols we meant.

    Accepts any case and any grouping, and repairs the three substitutions the
    alphabet was designed around (`O`→`0`, `I`/`L`→`1`). Anything else is left
    alone so that `derive_from_recovery_code` can reject it by name.
    """
    cleaned = "".join(
        ch for ch in (code or "").upper() if not ch.isspace() and ch != "-"
    )
    return "".join(
        _ALPHABET[_DECODE[ch]] if ch in _DECODE else ch for ch in cleaned
    )


def derive_from_recovery_code(code: str) -> bytes:
    """A key-encryption key from a recovery code.

    HKDF, not scrypt: the code is already 160 bits of machine-generated
    entropy, so stretching it buys nothing. A passphrase is stretched because a
    human chose it.
    """
    normalised = normalise_recovery_code(code)
    if len(normalised) != RECOVERY_CHARS:
        raise WrongSecretError(
            f"a recovery code is {RECOVERY_CHARS} characters; that one is "
            f"{len(normalised)}")
    if any(ch not in _ALPHABET for ch in normalised):
        raise WrongSecretError("that is not a valid recovery code")
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES,
                salt=None, info=_HKDF_INFO).derive(normalised.encode("ascii"))


# ── key wrapping ────────────────────────────────────────────────────────────

def new_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def wrap(kek: bytes, key: bytes, label: bytes) -> str:
    """Encrypt `key` under `kek`, binding it to `label`."""
    nonce = os.urandom(NONCE_BYTES)
    return _b64(nonce + AESGCM(kek).encrypt(nonce, key, label))


def unwrap(kek: bytes, wrapped: str, label: bytes) -> bytes:
    """Recover a key from `wrap`. Raises `WrongSecretError` on any failure."""
    blob = _unb64(wrapped)
    if len(blob) <= NONCE_BYTES:
        raise CorruptArchiveError("a wrapped key is truncated")
    try:
        return AESGCM(kek).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], label)
    except InvalidTag as exc:
        raise WrongSecretError("that secret does not open this archive") from exc


# ── the archive header ──────────────────────────────────────────────────────

@dataclass
class Header:
    """The archive's plaintext header — and its authenticated associated data.

    It is readable without any secret on purpose: a restore has to show *which*
    generation it is about to open, what is inside it, and which unlock methods
    exist, before the user has typed anything.

    Readable is not the same as malleable. The exact header bytes are the AAD
    for **every** chunk, so changing one byte of it — the chunk count, a wrap,
    the manifest — makes all decryption fail. `total_chunks` living in here is
    what turns a truncated archive into a loud failure rather than a brain that
    restores three quarters of the way.
    """
    salt: str
    wrapped_dek: str
    wrapped_mk: dict[str, str]
    total_chunks: int
    plaintext_sha256: str
    plaintext_bytes: int
    manifest: list[dict[str, Any]] = field(default_factory=list)
    withheld: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    app_version: str = ""
    version: int = FORMAT_VERSION
    kdf: str = "scrypt"
    kdf_params: dict[str, int] = field(default_factory=scrypt_params)
    chunk_size: int = CHUNK_SIZE

    def to_bytes(self) -> bytes:
        """Canonical encoding — sorted keys, no whitespace.

        It must round-trip byte-identically, because these bytes are the AAD.
        """
        return json.dumps(self.__dict__, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")

    @classmethod
    def parse(cls, raw: bytes) -> Header:
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise CorruptArchiveError("the archive header is not readable") from exc
        if not isinstance(data, dict):
            raise CorruptArchiveError("the archive header is not readable")
        # Checked before anything else about the contents: a newer format is
        # not corruption, and telling the user to update the app is a different
        # message from telling them their backup is damaged.
        if data.get("version") != FORMAT_VERSION:
            raise CorruptArchiveError(
                f"this backup was written by a newer version of Chitragupta "
                f"(format {data.get('version')}). Update the app to open it.")
        missing = {"salt", "wrapped_dek", "wrapped_mk", "total_chunks",
                   "plaintext_sha256", "plaintext_bytes"} - set(data)
        if missing:
            raise CorruptArchiveError("the archive header is incomplete")
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def unlock_methods(self) -> list[str]:
        """Which secrets can open this archive — shown before asking for one."""
        return sorted(self.wrapped_mk)


# ── sealing and opening the payload ─────────────────────────────────────────

def seal(payload: BinaryIO, out: BinaryIO, header: Header, dek: bytes) -> None:
    """Encrypt `payload` into `out`, chunk by chunk.

    `header` must already be final — it is the AAD, so nothing in it can be
    decided after the first chunk is written. That is why `pack` stages the
    payload to a temporary file first: the plaintext hash and the chunk count
    have to be known before a single byte is encrypted.
    """
    raw = header.to_bytes()
    base = os.urandom(NONCE_BASE_BYTES)
    out.write(MAGIC)
    out.write(len(raw).to_bytes(4, "big"))
    out.write(raw)
    out.write(base)

    aead = AESGCM(dek)
    written = 0
    while True:
        block = payload.read(header.chunk_size)
        if not block:
            break
        sealed = aead.encrypt(base + written.to_bytes(4, "big"), block, raw)
        out.write(len(sealed).to_bytes(4, "big"))
        out.write(sealed)
        written += 1
    if written != header.total_chunks:
        raise CorruptArchiveError(
            f"the backup changed while it was being written "
            f"(expected {header.total_chunks} blocks, wrote {written})")


def read_header(src: BinaryIO) -> tuple[Header, bytes, bytes]:
    """The header, its exact bytes, and the nonce base — no secret needed."""
    if src.read(len(MAGIC)) != MAGIC:
        raise CorruptArchiveError("this is not a Chitragupta backup")
    size_raw = src.read(4)
    if len(size_raw) != 4:
        raise CorruptArchiveError("the archive is truncated")
    size = int.from_bytes(size_raw, "big")
    if not 0 < size <= (1 << 24):
        raise CorruptArchiveError("the archive header is implausible")
    raw = src.read(size)
    if len(raw) != size:
        raise CorruptArchiveError("the archive is truncated")
    base = src.read(NONCE_BASE_BYTES)
    if len(base) != NONCE_BASE_BYTES:
        raise CorruptArchiveError("the archive is truncated")
    return Header.parse(raw), raw, base


def open_payload(src: BinaryIO, out: BinaryIO, header: Header,
                 raw_header: bytes, base: bytes, dek: bytes) -> None:
    """Decrypt into `out`, verifying as it goes, one chunk at a time.

    Three independent checks, because a backup that restores *most* of a brain
    is worse than one that refuses outright: every chunk's AEAD tag, the chunk
    count from the header, and a SHA-256 over the whole plaintext. The hash is
    compared with `compare_digest` — not because anyone is timing us, but
    because there is no reason to write the version that could be.
    """
    aead = AESGCM(dek)
    digest = hashlib.sha256()
    for index in range(header.total_chunks):
        size_raw = src.read(4)
        if len(size_raw) != 4:
            raise CorruptArchiveError("the backup is incomplete — part of it is missing")
        size = int.from_bytes(size_raw, "big")
        if size <= 0 or size > header.chunk_size + (1 << 16):
            raise CorruptArchiveError("the backup is damaged")
        sealed = src.read(size)
        if len(sealed) != size:
            raise CorruptArchiveError("the backup is incomplete — part of it is missing")
        try:
            block = aead.decrypt(base + index.to_bytes(4, "big"), sealed, raw_header)
        except InvalidTag as exc:
            raise CorruptArchiveError(
                f"the backup is damaged or was modified "
                f"(block {index + 1} of {header.total_chunks})") from exc
        digest.update(block)
        out.write(block)

    if not hmac.compare_digest(digest.hexdigest(), header.plaintext_sha256):
        raise CorruptArchiveError("the backup did not match its own checksum")
