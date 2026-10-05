"""Turn a Chitragupta home into one encrypted file.

What the shipped export did and why this exists: `/api/brain/export` wrote
**memories and nothing else** — one table out of forty-four — so a user moving
to a new Mac lost their custom agents, personas, every tool and connector
grant, their automations and routines, their tasks and reminders, the canonical
claims layer, and a year of accumulated health measurements. All of it was
already sitting in eleven SQLite files that nobody copied.

So this module does not enumerate tables. It copies **files**, and asks
`core/exclusions.py` what each one is — which means a database added next year
is in the backup without anybody remembering to add it, and a credential file
added next year is not.

**Snapshots use `VACUUM INTO`.** Copying a live SQLite file byte-for-byte can
capture a torn write or miss a page still in the WAL; `VACUUM INTO` asks SQLite
for a consistent copy and is safe while the app is running, which matters
because a backup that requires quitting the app is a backup nobody takes.

**The payload is staged to a temporary file before it is encrypted.** The
plaintext hash and the chunk count go in the header, the header is the AAD for
every chunk, so both have to be final before the first byte is sealed. Nothing
streams end-to-end here, and that is a deliberate trade for a format where a
truncated archive cannot restore partially.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core import exclusions
from ..log import get_logger
from . import crypto

log = get_logger(__name__)

#: Where the staged payload puts the user's files inside the tar.
PAYLOAD_DIR = "payload"
MANIFEST_NAME = "manifest.json"


@dataclass
class Packed:
    """The result of a backup, as the UI needs to report it."""
    path: Path
    bytes_written: int
    plaintext_bytes: int
    manifest: list[dict[str, Any]]
    withheld: dict[str, str]
    recovery_code: str | None

    @property
    def summary(self) -> str:
        files = len(self.manifest)
        mb = self.bytes_written / (1024 * 1024)
        return f"{files} item(s), {mb:.1f} MB"


def _app_version() -> str:
    try:
        from .. import __version__
        return str(__version__)
    except Exception:
        return "unknown"


def snapshot_database(src: Path, dest: Path) -> None:
    """A consistent copy of a live SQLite database.

    `VACUUM INTO` refuses an existing target, which is why `dest` is always
    inside a fresh staging directory.
    """
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        con.execute("VACUUM INTO ?", (str(dest),))
    finally:
        con.close()


def stage(home: Path, into: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Copy everything archivable out of `home`, and report what was withheld.

    Returns `(manifest, withheld)`. `withheld` is Tier 0 — the things that may
    never leave the machine — and it is carried into the archive header so the
    restore screen can tell the user exactly what they will need to reconnect,
    rather than letting them find out when an agent fails.
    """
    into.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    withheld: dict[str, str] = {}

    for entry in sorted(home.iterdir(), key=lambda p: p.name):
        verdict = exclusions.classify(entry.name, is_dir=entry.is_dir())
        if verdict.refused:
            withheld[entry.name] = verdict.reason
            log.debug("withheld from backup: %s (%s)", entry.name, verdict.reason)
            continue
        if not verdict.archived:
            continue

        target = into / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
            kind = "folder"
        elif entry.suffix == ".db":
            snapshot_database(entry, target)
            kind = "database"
        else:
            _copy_file(entry, target)
            kind = "file"

        manifest.append({
            "name": entry.name,
            "kind": kind,
            "bytes": _size_of(target),
            "why": verdict.reason,
        })

    return manifest, withheld


def _copy_file(src: Path, dest: Path) -> None:
    """Copy one file, stripping any keys `exclusions` says must not travel.

    The case this exists for is `mcp_servers.json`: tokens used to be stored
    inline in it and are migrated to the Keychain only when the Connectors
    screen is opened, so an install where that never happened still has
    credentials on disk. Stripping the key keeps the configuration — which the
    user would otherwise rebuild by hand — without copying the secret.
    """
    strip = exclusions.strip_keys_for(src.name)
    if not strip:
        shutil.copy2(src, dest)
        return
    try:
        data = json.loads(src.read_text())
    except Exception:
        log.warning("could not parse %s to sanitise it — leaving it out", src.name)
        return
    removed = _strip_recursive(data, set(strip))
    dest.write_text(json.dumps(data, indent=2))
    if removed:
        log.info("stripped %d sensitive key(s) from %s before backing it up",
                 removed, src.name)


def _strip_recursive(node: Any, keys: set[str]) -> int:
    """Remove `keys` anywhere in a nested structure. Returns how many went.

    Recursive rather than top-level because `mcp_servers.json` is a mapping of
    server id to spec, so the key to strip is one level down — and the next
    file to need this may nest differently again.
    """
    removed = 0
    if isinstance(node, dict):
        for key in list(node):
            if key in keys:
                del node[key]
                removed += 1
            else:
                removed += _strip_recursive(node[key], keys)
    elif isinstance(node, list):
        for item in node:
            removed += _strip_recursive(item, keys)
    return removed


def _size_of(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size


def pack(home: Path, out: Path, passphrase: str, *,
         recovery_code: str | None = None,
         master_key: bytes | None = None) -> Packed:
    """Write an encrypted backup of `home` to `out`.

    `passphrase` and the recovery code are two independent doors to the same
    master key — see `crypto`. A fresh recovery code is generated unless one is
    supplied, and it is returned **once**: it cannot be recovered from the
    archive afterwards, by design.

    `master_key` lets a later backup reuse the key of an earlier one, so one
    passphrase opens every generation. Omit it for a first backup.
    """
    if not passphrase:
        raise crypto.WrongSecretError("a passphrase is required to encrypt a backup")
    home = Path(home)
    out = Path(out)
    if not home.is_dir():
        raise FileNotFoundError(f"no Chitragupta home at {home}")

    code = recovery_code or crypto.new_recovery_code()
    mk = master_key or crypto.new_key()
    dek = crypto.new_key()
    salt = crypto.new_salt()

    with tempfile.TemporaryDirectory(prefix="chitragupta-backup-") as tmp:
        tmpdir = Path(tmp)
        payload_root = tmpdir / PAYLOAD_DIR
        manifest, withheld = stage(home, payload_root)
        (payload_root / MANIFEST_NAME).write_text(json.dumps({
            "manifest": manifest,
            "withheld": withheld,
            "app_version": _app_version(),
            "created_at": datetime.now(UTC).isoformat(),
        }, indent=2))

        tarball = tmpdir / "payload.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(payload_root, arcname=PAYLOAD_DIR)

        plaintext_bytes = tarball.stat().st_size
        digest = _sha256_of(tarball)
        chunks = max(1, -(-plaintext_bytes // crypto.CHUNK_SIZE))

        header = crypto.Header(
            salt=crypto._b64(salt),
            wrapped_dek=crypto.wrap(mk, dek, crypto.WRAP_DEK),
            wrapped_mk={
                crypto.BY_PASSPHRASE: crypto.wrap(
                    crypto.derive_from_passphrase(passphrase, salt), mk,
                    crypto.WRAP_PASSPHRASE),
                crypto.BY_RECOVERY: crypto.wrap(
                    crypto.derive_from_recovery_code(code), mk,
                    crypto.WRAP_RECOVERY),
            },
            total_chunks=chunks,
            plaintext_sha256=digest,
            plaintext_bytes=plaintext_bytes,
            manifest=manifest,
            withheld=withheld,
            created_at=datetime.now(UTC).isoformat(),
            app_version=_app_version(),
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        # Written to a sibling and moved into place, so an interrupted backup
        # never leaves a half-file where a user would later try to restore it.
        staging_out = out.with_name(out.name + ".partial")
        try:
            with tarball.open("rb") as payload, staging_out.open("wb") as sink:
                crypto.seal(payload, sink, header, dek)
            staging_out.replace(out)
        except BaseException:
            staging_out.unlink(missing_ok=True)
            raise

    written = out.stat().st_size
    log.info("backup written: %s (%d bytes, %d item(s), %d withheld)",
             out.name, written, len(manifest), len(withheld))
    return Packed(path=out, bytes_written=written, plaintext_bytes=plaintext_bytes,
                  manifest=manifest, withheld=withheld, recovery_code=code)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
