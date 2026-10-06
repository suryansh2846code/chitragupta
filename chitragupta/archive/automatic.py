"""Backing up without being asked, and not keeping every one forever.

**Why this exists at all.** Everything before it made a backup *possible*; a
backup you have to remember to take is one nobody takes, so the gap that
started this work — a user loses their Mac and loses their agents, their
permissions and a year of measurements — was only half closed. This is the half
that closes it: you are not "able to back up", you are backed up.

**It needs no secret, and that is the whole trick.** `identity.Keyring` holds
the master key *and* the wraps minted from the user's passphrase at setup, so an
automatic archive is encrypted such that their passphrase and recovery code both
still open it while neither is stored anywhere. See `identity` — the wraps are
not secrets, they already travel in plaintext inside every archive header.

**It refuses rather than degrades.** If the keyring cannot do an unattended
backup (an install upgraded from the older shape, which has no passphrase wrap),
nothing is written. The alternative is archives only a recovery code can open,
from a user who believes their passphrase works — a backup that fails on the
worst day of their year.

**Retention keeps generations, never a mirror.** A mirror would propagate
`POST /api/brain/reset` and destroy the only copy of what it deleted, which is
the accident backups exist for. So each run writes a new file and the oldest are
pruned past a window — and **pruning only ever deletes files this feature
wrote**, matched by name, because a folder the user chose is not ours to tidy.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..log import get_logger, suppressed
from . import identity, job

log = get_logger(__name__)

#: Where automatic backups go. Inside the home so this works with no
#: configuration, which is the case that matters: a feature nobody set up is
#: the one that has to work.
FOLDER = "backups"
SUFFIX = ".cgarch"

#: Only files matching this are ever pruned. The user may keep anything else in
#: the same folder and it is not ours to touch.
NAME_PREFIX = "chitragupta-"

#: State lives beside the archives rather than in a database, so a backup
#: works before anything else in the app has been opened.
STATE_FILE = "backup-schedule.json"


def _state_path() -> Path:
    return get_settings().home / STATE_FILE


def _read() -> dict[str, Any]:
    import json
    with suppressed("reading the backup schedule"):
        return json.loads(_state_path().read_text())
    return {}


def _write(data: dict[str, Any]) -> None:
    import json
    with suppressed("writing the backup schedule"):
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))


@dataclass
class Plan:
    """Whether an automatic backup should run, and why not when it should not.

    `reason` is written for a person: it is what the Backup screen shows under
    the toggle, so "off" and "on but it cannot run yet" are never the same
    sentence.
    """
    enabled: bool
    possible: bool
    due: bool
    reason: str
    every_hours: int
    last_at: float = 0.0
    last_path: str = ""
    keep: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "possible": self.possible,
            "due": self.due, "reason": self.reason,
            "every_hours": self.every_hours, "keep": self.keep,
            "last_at": self.last_at, "last_path": self.last_path,
        }


def enabled() -> bool:
    """Has the user asked for automatic backups?

    **Default off.** A feature that starts writing hundreds of megabytes to
    somebody's disk because they installed an update is not a feature they
    chose. It is offered on the Backup screen, next to the sentence saying what
    it will and will not contain.
    """
    saved = _read().get("enabled")
    if saved is None:
        return bool(get_settings().auto_backup)
    return bool(saved)


def every_hours() -> int:
    saved = _read().get("every_hours")
    try:
        return max(1, int(saved if saved is not None
                          else get_settings().auto_backup_hours))
    except (TypeError, ValueError):
        return 24


def keep() -> int:
    """How many generations to keep. 0 means keep everything."""
    saved = _read().get("keep")
    try:
        return max(0, int(saved if saved is not None
                          else get_settings().auto_backup_keep))
    except (TypeError, ValueError):
        return 7


def plan(now: float | None = None) -> Plan:
    """The decision, with the sentence explaining it. Never raises."""
    state = _read()
    last = float(state.get("last_at") or 0)
    interval = every_hours() * 3600
    on = enabled()
    possible = identity.can_run_unattended()

    if not on:
        reason = "Automatic backups are off."
    elif not identity.is_set_up():
        reason = ("Make one backup yourself first — that is when your "
                  "passphrase and recovery code are set up.")
    elif not possible:
        reason = ("Back up once with your passphrase to let this run on its "
                  "own. Backups made before this version cannot be rewritten "
                  "without it.")
    elif not last:
        reason = "Will run shortly."
    else:
        reason = f"Last backup {_ago(last, now)}."

    return Plan(
        enabled=on,
        possible=possible,
        due=bool(on and possible and (now or time.time()) - last >= interval),
        reason=reason,
        every_hours=every_hours(),
        last_at=last,
        last_path=str(state.get("last_path") or ""),
        keep=keep(),
    )


def _ago(when: float, now: float | None = None) -> str:
    minutes = int(((now or time.time()) - when) / 60)
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'' if hours == 1 else 's'} ago"
    days = hours // 24
    return f"{days} day{'' if days == 1 else 's'} ago"


def next_path(now: datetime | None = None) -> Path:
    """Where the next automatic backup goes. Dated, so a folder of them reads."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return get_settings().home / FOLDER / f"{NAME_PREFIX}{stamp}{SUFFIX}"


def run_if_due(now: float | None = None) -> dict[str, Any]:
    """Start a backup if one is due. Called once a minute by the scheduler.

    Returns what it decided, so a test can drive it without a clock and the
    scheduler can log it. **Never raises**: a background backup must not be
    able to take the scheduler thread down with it.
    """
    decided = plan(now)
    if not decided.due:
        return {"started": False, **decided.as_dict()}
    if job.is_running():
        # Not an error and not a missed slot: `last_at` is untouched, so the
        # next tick tries again a minute from now.
        return {"started": False, "reason": "Another backup or restore is "
                                            "already running.",
                **{k: v for k, v in decided.as_dict().items()
                   if k != "reason"}}

    out = next_path()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        job.start_backup(get_settings().home, out)
    except Exception as exc:
        log.warning("an automatic backup could not start: %s", exc)
        return {"started": False, **decided.as_dict(),
                "error": str(exc)}

    # Recorded when it STARTS, not when it finishes. A crash halfway through
    # must not make the next tick try again immediately and again and again —
    # the partial file is cleaned up by `writer.pack` either way.
    state = _read()
    state["last_at"] = now or time.time()
    state["last_path"] = str(out)
    _write(state)
    log.info("automatic backup started: %s", out.name)
    return {"started": True, "path": str(out), **decided.as_dict()}


def prune(keep_count: int | None = None) -> list[str]:
    """Delete the oldest automatic backups past the window. Returns what went.

    Only files this feature wrote — `chitragupta-*.cgarch` in the backups
    folder — are ever considered. A user may keep anything they like beside
    them, including a backup they renamed to mean "do not delete this", and
    none of it is ours to tidy.

    `keep() == 0` keeps everything, which is the honest answer for somebody who
    would rather spend the disk.
    """
    limit = keep() if keep_count is None else max(0, int(keep_count))
    if limit <= 0:
        return []
    folder = get_settings().home / FOLDER
    if not folder.is_dir():
        return []

    ours = sorted(
        (p for p in folder.glob(f"{NAME_PREFIX}*{SUFFIX}") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed: list[str] = []
    for old in ours[limit:]:
        # A constant label, never an f-string:
        # `test_every_suppression_says_what_it_was_attempting` requires one it
        # can read statically, which is the point of the rule — a label
        # assembled at runtime cannot be audited by reading the source.
        with suppressed("pruning an old backup"):
            old.unlink()
            removed.append(old.name)
    if removed:
        log.info("pruned %d backup(s) past the last %d", len(removed), limit)
    return removed


def set_settings(on: bool | None = None, hours: int | None = None,
                 keep_count: int | None = None) -> dict[str, Any]:
    """Change what the user asked for. Only the given fields move."""
    state = _read()
    if on is not None:
        state["enabled"] = bool(on)
    if hours is not None:
        state["every_hours"] = max(1, int(hours))
    if keep_count is not None:
        state["keep"] = max(0, int(keep_count))
    _write(state)
    return plan().as_dict()
