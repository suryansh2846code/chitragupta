"""Backing the brain up, and putting it back.

Every path here is literal, so nothing in this module can shadow or be shadowed
by another router's parameterised routes — see the registration note in
`routes/__init__.py`.

**Two endpoints are deliberately cheap and the rest are deliberately not.**
`/status` is polled every second while a job runs, so it stays an ordinary
handler that reads a dict. Starting a job returns immediately — the work is a
background thread — so those are ordinary handlers too. Only `/inspect` reads
the disk synchronously, and it reads a header, not a payload.

Nothing here runs in the model lane: no model is called. The single-job lock
lives in `archive/job.py`, where it belongs — it protects the *home directory*
from two concurrent operations, which is a fact about the data rather than
about HTTP.

**Re-running migrations and re-embedding is this module's job**, not
`archive/`'s: a restored database may have been written by an older version,
and vectors are derived so they were never in the archive. `archive/` is a
sibling of `brain/` and may not import it
([`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md) §3 rule 2), so the
seam is here, at the layer that is allowed to know about both.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...archive import crypto, identity, job, reader
from ...config import get_settings
from ...core import exclusions
from ...log import get_logger, suppressed

log = get_logger(__name__)
router = APIRouter()

#: Where a backup goes unless the user names somewhere else. Inside the home so
#: it works with no configuration, which is the first-run case — and the
#: Downloads folder is not ours to write to uninvited.
DEFAULT_DIR = "backups"
SUFFIX = ".cgarch"


def _default_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return get_settings().home / DEFAULT_DIR / f"chitragupta-{stamp}{SUFFIX}"


def _checked(raw: str | None) -> Path:
    """A user-supplied path, or the default.

    `expanduser` and `resolve` are applied so `~/Desktop/x.cgarch` works and so
    the path in any error message is the one the filesystem will actually use.
    """
    if not raw or not raw.strip():
        return _default_path()
    return Path(raw.strip()).expanduser().resolve()


class BackupIn(BaseModel):
    passphrase: str = Field(min_length=1, max_length=1024)
    path: str | None = None


class RestoreIn(BaseModel):
    path: str = Field(min_length=1)
    passphrase: str | None = Field(default=None, max_length=1024)
    recovery_code: str | None = Field(default=None, max_length=128)
    replace_existing: bool = True


class InspectIn(BaseModel):
    path: str = Field(min_length=1)


@router.get("/api/backup/state")
def backup_state() -> dict[str, Any]:
    """Whether backup is set up on this Mac, and where backups land.

    `recovery_code_saved` is false until the user confirms they kept the code.
    The UI uses it to keep nagging: the code cannot be shown twice, so somebody
    who dismissed that dialog has one door to their backups and does not know.
    """
    settings = get_settings()
    folder = settings.home / DEFAULT_DIR
    existing: list[dict[str, Any]] = []
    with suppressed("listing previous backups"):
        for found in sorted(folder.glob(f"*{SUFFIX}"), reverse=True):
            stat = found.stat()
            existing.append({"path": str(found), "name": found.name,
                             "bytes": stat.st_size, "modified": stat.st_mtime})
    return {
        "set_up": identity.is_set_up(),
        "recovery_code_saved": identity.recovery_code_acknowledged(),
        "default_dir": str(folder),
        "backups": existing[:50],
        "withheld": exclusions.refusals(),
    }


@router.post("/api/backup/start")
def backup_start(body: BackupIn) -> dict[str, Any]:
    """Begin a backup. Returns at once — poll `/api/backup/status`.

    `recovery_code` is present exactly once, on the first backup this Mac ever
    makes. It is not stored anywhere, so it cannot be re-sent.
    """
    out = _checked(body.path)
    if out.suffix != SUFFIX:
        out = out.with_name(out.name + SUFFIX)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        return job.start_backup(get_settings().home, out, body.passphrase)
    except RuntimeError as exc:                    # the single-job lock
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"could not write a backup to {out.parent}: {exc}") from exc


@router.post("/api/backup/inspect")
def backup_inspect(body: InspectIn) -> dict[str, Any]:
    """What a backup file says about itself. No passphrase required.

    This is what lets the restore screen show the date, the contents and the
    reconnect list *before* asking for a secret — the opposite order would make
    the user type a passphrase to find out they picked the wrong file.
    """
    path = _checked(body.path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no file at {path}")
    try:
        seen = reader.inspect(path)
    except crypto.ArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "path": str(path),
        "created_at": seen.created_at,
        "app_version": seen.app_version,
        "bytes": seen.plaintext_bytes,
        "manifest": seen.manifest,
        "withheld": seen.withheld,
        "reconnect_needed": seen.reconnect_needed,
        "unlock_methods": seen.unlock_methods,
    }


@router.post("/api/backup/restore")
def backup_restore(body: RestoreIn) -> dict[str, Any]:
    """Begin a restore. Returns at once — poll `/api/backup/status`.

    The secret is checked here, synchronously, so a wrong passphrase is a 400
    the user sees immediately rather than a job that starts and then fails.
    """
    path = _checked(body.path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no file at {path}")
    if not body.passphrase and not body.recovery_code:
        raise HTTPException(status_code=400,
                            detail="a passphrase or recovery code is required")
    try:
        return job.start_restore(
            path, get_settings().home, passphrase=body.passphrase,
            recovery_code=body.recovery_code,
            replace_existing=body.replace_existing)
    except crypto.WrongSecretError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except crypto.ArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:                    # the single-job lock
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/backup/status")
def backup_status() -> dict[str, Any]:
    """Where the running job is. Cheap, and polled about once a second."""
    return job.status()


@router.post("/api/backup/stop")
def backup_stop() -> dict[str, Any]:
    """Stop the running job. Cooperative — it stops at the next file or block."""
    return job.stop()


@router.post("/api/backup/recovery-code/saved")
def backup_code_saved() -> dict[str, Any]:
    """The user says they have kept their recovery code."""
    identity.acknowledge_recovery_code()
    return {"recovery_code_saved": True}


@router.post("/api/backup/finish-restore")
def backup_finish_restore() -> dict[str, Any]:
    """Bring a freshly restored home up to date.

    Two things the archive could not carry, both deliberately:

    * **Migrations.** A restored database may have been written by an older
      version of the app. `run_migrations` is idempotent and version-aware, so
      calling it on an already-current home is a no-op.
    * **Vectors.** Embeddings are derived and were never in the archive — the
      existing memory export omits them for the same reason. They are rebuilt
      lazily on the first recall, and the enrichment queue is left alone so the
      user chooses when to spend model calls.

    Separate from `/restore` because the restore writes *files* and this reads
    the *brain*, and `archive/` may not import `brain/`.
    """
    from ...brain import get_brain

    out: dict[str, Any] = {"migrated": {}}
    with suppressed("running migrations on a restored brain"):
        out["migrated"] = get_brain().run_migrations()
    with suppressed("reading stats from a restored brain"):
        out["stats"] = get_brain().stats()
    return out
