"""Open an encrypted backup and put a Chitragupta home back.

Two entry points, and the split between them is the point:

* `inspect()` needs **no secret**. A restore screen has to say what it is about
  to open — when it was made, what is inside, what will need reconnecting —
  before the user types a passphrase. Asking for the secret first and then
  reporting "that backup is empty" is the wrong order.
* `restore()` needs one of the two secrets, and either succeeds completely or
  changes nothing it cannot undo.

**Nothing here imports `brain/`.** Re-running migrations and re-embedding is
the caller's job (`api/routes`), because `archive/` is a sibling of `brain/`
and `models/` — see [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §3
rule 2. This module restores *files*; what the brain then does with them is
above it.

**Tar members are validated by hand rather than with `tarfile`'s filter.**
`filter="data"` arrived in 3.11.4 and `requires-python` is `>=3.11`, so on a
3.11.0 install it would be silently absent — and the thing it protects against
is an archive whose member names escape the directory they extract into. An
archive is attacker-supplied data the moment a user restores one somebody sent
them, so the check is explicit and always runs.
"""
from __future__ import annotations

import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core import exclusions
from ..log import get_logger
from . import crypto, progress
from .writer import MANIFEST_NAME, PAYLOAD_DIR

log = get_logger(__name__)

#: Suffix given to a file that was already in the home when a restore replaced
#: it. Never deleted by us — a restore that silently destroyed the brain it was
#: supposed to repair is the one failure this whole feature exists to prevent.
REPLACED_SUFFIX = ".replaced"


@dataclass
class Inspection:
    """What a backup says about itself, readable without any secret."""
    created_at: str
    app_version: str
    plaintext_bytes: int
    manifest: list[dict[str, Any]]
    withheld: dict[str, str]
    unlock_methods: list[str]

    @property
    def reconnect_needed(self) -> list[str]:
        """What the user will have to re-authorise after this restore.

        Tier 0 never leaves the machine, so these are the doors that have to be
        reopened by hand. Surfaced here so the restore screen can show a
        checklist instead of letting the user discover it when an agent fails.
        """
        return sorted(self.withheld)


def inspect(path: Path) -> Inspection:
    """Read a backup's header. No secret required, nothing decrypted."""
    with Path(path).open("rb") as src:
        header, _, _ = crypto.read_header(src)
    return Inspection(
        created_at=header.created_at,
        app_version=header.app_version,
        plaintext_bytes=header.plaintext_bytes,
        manifest=header.manifest,
        withheld=header.withheld,
        unlock_methods=header.unlock_methods(),
    )


def master_key_from(header: crypto.Header, passphrase: str | None,
                    recovery_code: str | None) -> bytes:
    """Unwrap the master key with whichever secret was offered.

    Public because `job.start_restore` calls it *before* spawning a thread, so
    a wrong passphrase is refused on the spot instead of becoming a background
    job that appears to start and then fails somewhere the user has to go
    looking for. It is also what the restore then remembers as this machine's
    master key, so the next backup from a recovered Mac joins the same set.
    """
    wraps = header.wrapped_mk
    if passphrase:
        wrapped = wraps.get(crypto.BY_PASSPHRASE)
        if not wrapped:
            raise crypto.WrongSecretError(
                "this backup cannot be opened with a passphrase — use its "
                "recovery code")
        salt = crypto._unb64(header.salt)
        kek = crypto.derive_from_passphrase(passphrase, salt, header.kdf_params)
        return crypto.unwrap(kek, wrapped, crypto.WRAP_PASSPHRASE)

    if recovery_code:
        wrapped = wraps.get(crypto.BY_RECOVERY)
        if not wrapped:
            raise crypto.WrongSecretError(
                "this backup has no recovery code — use its passphrase")
        kek = crypto.derive_from_recovery_code(recovery_code)
        return crypto.unwrap(kek, wrapped, crypto.WRAP_RECOVERY)

    raise crypto.WrongSecretError("a passphrase or recovery code is required")


def _safe_members(tar: tarfile.TarFile, root: str) -> list[tarfile.TarInfo]:
    """Only the members that are plain files and folders inside `root`.

    Rejects absolute paths, anything that climbs out with `..`, and every
    non-regular member — symlinks, hard links, devices and FIFOs. A symlink in
    an archive is how an extraction writes outside the directory it was given,
    and none of what we pack needs one.
    """
    safe: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts:
            raise crypto.CorruptArchiveError(f"the backup contains an unsafe path: {name}")
        if not (member.isfile() or member.isdir()):
            raise crypto.CorruptArchiveError(
                f"the backup contains something that is not a file: {name}")
        if name != root and not name.startswith(root + "/"):
            raise crypto.CorruptArchiveError(f"the backup contains an unexpected path: {name}")
        safe.append(member)
    return safe


def restore(path: Path, home: Path, *, passphrase: str | None = None,
            recovery_code: str | None = None,
            replace_existing: bool = True,
            prog: progress.Progress | None = None) -> dict[str, Any]:
    """Decrypt `path` and put its contents into `home`.

    Order matters, and it is the opposite of the obvious one: **everything is
    decrypted, verified and unpacked to a temporary directory before a single
    file in `home` is touched.** A wrong passphrase, a truncated download or a
    tampered byte therefore costs the user nothing — the brain they still have
    is untouched. Only once the whole payload has checked out does anything
    move.

    Files already in `home` are renamed aside rather than deleted, so a restore
    onto a populated home is recoverable too.
    """
    path, home = Path(path), Path(home)
    prog = prog or progress.inert()
    with path.open("rb") as src:
        header, raw_header, base = crypto.read_header(src)
        mk = master_key_from(header, passphrase, recovery_code)
        dek = crypto.unwrap(mk, header.wrapped_dek, crypto.WRAP_DEK)

        with tempfile.TemporaryDirectory(prefix="chitragupta-restore-") as tmp:
            tmpdir = Path(tmp)
            prog.enter(progress.VERIFYING, total=header.total_chunks)
            tarball = tmpdir / "payload.tar.gz"
            with tarball.open("wb") as sink:
                crypto.open_payload(src, sink, header, raw_header, base, dek,
                                    on_chunk=lambda _n: prog.step())

            prog.enter(progress.WRITING)
            unpacked = tmpdir / "unpacked"
            unpacked.mkdir()
            with tarfile.open(tarball, "r:gz") as tar:
                tar.extractall(unpacked, members=_safe_members(tar, PAYLOAD_DIR))

            payload = unpacked / PAYLOAD_DIR
            if not payload.is_dir():
                raise crypto.CorruptArchiveError("the backup has no payload")

            placed = _place(payload, home, header, replace_existing, prog)
            prog.finish()
            return placed


def _clear_sidecars(database: Path) -> None:
    """Remove the stale `-wal` / `-shm` sitting beside a replaced database.

    **Without this a restore silently does nothing.** Every database runs in
    WAL mode, so a home that has been open has a `-wal` beside each file. Write
    a different database over the main file and leave the old log there, and
    SQLite applies that log on the next open — measured: a fresh read after the
    restore returned **the old rows**, while the app reported success. A backup
    feature that says "Restored" and leaves the old brain in place is worse
    than one that refuses.

    Safe to do unconditionally: `VACUUM INTO` writes a fully checkpointed
    database, so a restored file never has outstanding log to apply. Named by
    `core/exclusions.is_sidecar`, so the rule about which files these are lives
    in one place.
    """
    if database.suffix != ".db":
        return
    for suffix in ("-wal", "-shm", "-journal"):
        companion = database.with_name(database.name + suffix)
        if companion.exists() and exclusions.is_sidecar(companion.name):
            companion.unlink()
            log.debug("cleared stale %s before using the restored database",
                      companion.name)


def _place(payload: Path, home: Path, header: crypto.Header,
           replace_existing: bool, prog: progress.Progress) -> dict[str, Any]:
    """Move a verified payload into the home. The last step, and the only
    destructive one."""
    home.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    restored: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []

    entries = sorted(payload.iterdir(), key=lambda p: p.name)
    prog.enter(progress.WRITING, total=len(entries))
    for entry in entries:
        prog.step(entry.name)
        if entry.name == MANIFEST_NAME:
            continue
        target = home / entry.name
        if target.exists():
            if not replace_existing:
                skipped.append(entry.name)
                continue
            aside = home / f"{entry.name}{REPLACED_SUFFIX}-{stamp}"
            target.replace(aside)
            replaced.append(entry.name)
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)
            _clear_sidecars(target)
        restored.append(entry.name)

    log.info("restore complete: %d restored, %d replaced, %d skipped",
             len(restored), len(replaced), len(skipped))
    return {
        "restored": restored,
        "replaced": replaced,
        "skipped": skipped,
        "replaced_suffix": f"{REPLACED_SUFFIX}-{stamp}" if replaced else "",
        "reconnect_needed": sorted(header.withheld),
        "withheld": header.withheld,
        "created_at": header.created_at,
        # The caller re-runs migrations and re-embeds: vectors are derived and
        # were never in the archive, and `archive/` must not import `brain/`.
        "needs_migrations": True,
        "needs_reembed": True,
    }
