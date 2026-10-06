"""The keyring that makes every backup on this Mac one set — and lets the
scheduler write one without asking anybody for a passphrase.

Without this module each backup would mint its own master key, and therefore
its own recovery code — so a user who backed up weekly would be handed a new
32-character code to keep safe every Monday, and the one they wrote down in
January would open exactly one January file.

So one `Keyring` is generated at setup and kept in the **macOS Keychain**
(encrypted at rest by the OS). One passphrase and one recovery code then open
every backup this machine has ever written.

**Why a whole keyring rather than just the key.** A backup needs four things:
the master key, the salt, and the master key wrapped once per unlock method.
Three of those were separate optional arguments to `writer.pack` at one point
and they are only meaningful *together* — a wrap made against a different
master key unwraps to nothing, and a salt that does not match its wrap derives
the wrong key. Passing them as one object is what makes "pass all or none"
impossible to get wrong rather than something a guard has to catch.

**The wraps are not secrets, and this is the load-bearing fact.** A wrap is the
master key encrypted under a key derived from the passphrase (or the recovery
code). Every one of them already travels in plaintext inside every archive
header — that is how a restore on a new Mac works at all. And anyone who can
read this machine's Keychain already has the master key *itself*, so the wraps
are strictly less valuable than what they would already hold. Storing them
therefore costs nothing and buys the thing a scheduled backup needs:

> **An automatic backup can be encrypted so that the user's passphrase still
> opens it, without the passphrase ever being stored.**

The passphrase is used exactly once, at setup, to make a wrap. It is never
written down and never asked for again — not by the scheduler, not by us.

**Restoring on a new Mac is where the keyring comes back.** There is no
Keychain entry there, so the master key is unwrapped out of the archive with
the user's passphrase or recovery code; `job.start_restore` then stores it along
with the wraps out of that archive's own header, which is what makes the *next*
backup from the recovered machine part of the same set.

`config` is a leaf, so reading the Keychain from here does not point an arrow
the wrong way ([`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §3).
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from ..config import get_settings
from ..log import get_logger
from . import crypto

log = get_logger(__name__)

#: One Keychain entry holding the whole keyring, rather than four that could
#: disagree with each other. Visible in Keychain Access as something
#: Chitragupta owns.
KEYRING_SECRET = "ARCHIVE_KEYRING"

#: Set once the user has been shown their recovery code and confirmed they
#: saved it. Not a secret — a fact about what the user has seen.
CODE_ACKNOWLEDGED_SECRET = "ARCHIVE_RECOVERY_ACKNOWLEDGED"

#: The key the keyring was stored under before it held the wraps. Read once, so
#: an install that backed up under the old shape is upgraded rather than being
#: told to start again — its master key is still the right one.
LEGACY_MASTER_KEY_SECRET = "ARCHIVE_MASTER_KEY"
LEGACY_RECOVERY_WRAP_SECRET = "ARCHIVE_RECOVERY_WRAP"


@dataclass(frozen=True)
class Keyring:
    """Everything needed to write a backup the user's own secrets can open.

    Frozen, because a half-updated keyring is the failure this type exists to
    make unrepresentable.
    """
    master_key: bytes
    salt: bytes
    #: {"passphrase": "<wrap>", "recovery": "<wrap>"} — the same mapping that
    #: goes into an archive header, so `writer.pack` can use it verbatim.
    wraps: dict[str, str]

    @property
    def unlock_methods(self) -> list[str]:
        return sorted(self.wraps)

    def to_json(self) -> str:
        return json.dumps({
            "v": 1,
            "mk": base64.b64encode(self.master_key).decode("ascii"),
            "salt": base64.b64encode(self.salt).decode("ascii"),
            "wraps": dict(self.wraps),
        })

    @classmethod
    def from_json(cls, raw: str) -> Keyring | None:
        try:
            data: dict[str, Any] = json.loads(raw)
            keyring = cls(
                master_key=base64.b64decode(data["mk"], validate=True),
                salt=base64.b64decode(data["salt"], validate=True),
                wraps={str(k): str(v) for k, v in (data.get("wraps") or {}).items()},
            )
        except Exception:
            log.warning("the stored backup keyring is unreadable — treating "
                        "backup as not yet set up")
            return None
        if len(keyring.master_key) != crypto.KEY_BYTES or not keyring.wraps:
            log.warning("the stored backup keyring is incomplete — treating "
                        "backup as not yet set up")
            return None
        return keyring


def keyring() -> Keyring | None:
    """This machine's keyring, or None if backup was never set up."""
    raw = get_settings().get_secret(KEYRING_SECRET)
    if raw:
        return Keyring.from_json(raw)
    return _upgraded_legacy()


def _upgraded_legacy() -> Keyring | None:
    """A keyring rebuilt from the two secrets an older install stored.

    That shape kept the master key and the recovery wrap and **not** the
    passphrase wrap or the salt, so a backup written from it can still be
    opened with the recovery code but cannot be rewritten with a passphrase
    wrap nobody can reconstruct. Carrying the master key forward is what
    matters: the user's existing backups stay openable, and the next manual
    backup mints the missing wrap from the passphrase they type.
    """
    settings = get_settings()
    raw_key = settings.get_secret(LEGACY_MASTER_KEY_SECRET)
    if not raw_key:
        return None
    try:
        master = base64.b64decode(raw_key, validate=True)
    except Exception:
        return None
    if len(master) != crypto.KEY_BYTES:
        return None
    wrap = settings.get_secret(LEGACY_RECOVERY_WRAP_SECRET)
    if not wrap:
        return None
    log.info("upgrading a backup keyring stored under the older shape")
    return Keyring(master_key=master, salt=b"", wraps={crypto.BY_RECOVERY: wrap})


def remember(keys: Keyring) -> None:
    """Store a keyring, whether freshly minted or recovered from an archive."""
    settings = get_settings()
    settings.set_secret(KEYRING_SECRET, keys.to_json())
    # The old pair would otherwise shadow the new keyring on the next read.
    settings.set_secret(LEGACY_MASTER_KEY_SECRET, None)
    settings.set_secret(LEGACY_RECOVERY_WRAP_SECRET, None)


def is_set_up() -> bool:
    return keyring() is not None


def can_run_unattended() -> bool:
    """Can a backup be written with no secret from the user?

    True once the keyring holds a passphrase wrap: the scheduler reuses it, so
    an automatic backup is openable by the passphrase the user chose without
    that passphrase existing anywhere on disk. False on an install upgraded
    from the older shape until the next manual backup mints the wrap.
    """
    keys = keyring()
    return bool(keys and crypto.BY_PASSPHRASE in keys.wraps and keys.salt)


def recovery_code_acknowledged() -> bool:
    """Has the user confirmed they saved their recovery code?

    Backup is deliberately not finished until they have. The code cannot be
    shown again — nothing stores it — so a user who dismissed that dialog has
    one door to their backups and does not know it.
    """
    return bool(get_settings().get_secret(CODE_ACKNOWLEDGED_SECRET))


def acknowledge_recovery_code() -> None:
    get_settings().set_secret(CODE_ACKNOWLEDGED_SECRET, "1")


def mint(passphrase: str, recovery_code: str | None = None) -> tuple[Keyring, str]:
    """A fresh keyring and its one recovery code. Stores nothing.

    Split from `begin_setup` so a keyring can be built without touching the
    Keychain — which is what the format's own tests need, and the alternative
    was them rebuilding these two wraps by hand and becoming a second place
    the rule lived.
    """
    if not passphrase:
        raise crypto.WrongSecretError("a passphrase is required to set up backup")
    master = crypto.new_key()
    salt = crypto.new_salt()
    code = recovery_code or crypto.new_recovery_code()
    keys = Keyring(
        master_key=master,
        salt=salt,
        wraps={
            crypto.BY_PASSPHRASE: crypto.wrap(
                crypto.derive_from_passphrase(passphrase, salt), master,
                crypto.WRAP_PASSPHRASE),
            crypto.BY_RECOVERY: crypto.wrap(
                crypto.derive_from_recovery_code(code), master,
                crypto.WRAP_RECOVERY),
        },
    )
    return keys, code


def begin_setup(passphrase: str) -> tuple[Keyring, str]:
    """Mint a keyring from the user's passphrase, and keep it.

    Returns `(keyring, code)`. The keyring is stored; **the code is not**, and
    neither is the passphrase. Both wraps are made in `mint`, which is the only
    moment the passphrase is ever needed — see the module docstring.
    """
    keys, code = mint(passphrase)
    remember(keys)
    get_settings().set_secret(CODE_ACKNOWLEDGED_SECRET, None)
    log.info("backup set up: a keyring is stored in the Keychain")
    return keys, code


def from_archive(master_key: bytes, header: Any) -> Keyring:
    """The keyring to keep after a restore, taken from the archive just opened.

    The wraps come out of that header, so the passphrase and recovery code the
    user already has keep working for every backup this machine writes from now
    on — including the automatic ones.
    """
    salt = b""
    try:
        salt = crypto._unb64(header.salt)
    except Exception:
        log.info("the restored archive had no readable salt")
    return Keyring(master_key=master_key, salt=salt,
                   wraps=dict(header.wrapped_mk or {}))


def forget() -> None:
    """Remove the keyring from this machine.

    Does **not** touch any backup file. An existing backup stays openable with
    the passphrase or recovery code it was written with — this clears the
    convenience, never the recovery.
    """
    settings = get_settings()
    for key in (KEYRING_SECRET, LEGACY_MASTER_KEY_SECRET,
                LEGACY_RECOVERY_WRAP_SECRET, CODE_ACKNOWLEDGED_SECRET):
        settings.set_secret(key, None)
    log.info("backup keyring removed from this machine")
