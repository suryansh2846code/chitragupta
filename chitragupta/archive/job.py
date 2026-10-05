"""One backup or restore at a time, in the background, reportable and stoppable.

Why a job rather than a request that returns when it is done: a real home is
~560 MB once a brain, an agents database and an action log are in it, so a
backup is tens of seconds of disk and CPU. CLAUDE.md:

> **Never make the user wait without telling them.** Long work is a background
> job with progress that survives a refresh.

The state lives in this module, so a page reload re-reads it and finds the job
still running — the same shape `brain.start_enrich` uses, for the same reason.

**Exactly one at a time, machine-wide.** Not a convenience: a restore running
while a backup reads the same eleven databases would capture a half-replaced
home and call it a backup. The lock makes the second request a clear refusal
rather than a race, and a refusal naming what is already running is something a
user can act on.

**A finished job's result is kept until the next one starts**, because the UI
polls: the request that observes completion is a different request from the one
that started it, and a result discarded on completion would be a job that
succeeded and reported nothing.

The recovery code is deliberately *not* in here. `identity.begin_setup` returns
it to the caller that will show it once; storing it in job state would leave a
secret that defeats the user's passphrase sitting in our own memory for as long
as the process lives.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from ..log import get_logger
from . import crypto, identity, progress, reader, writer

log = get_logger(__name__)

BACKUP = "backup"
RESTORE = "restore"

_lock = threading.Lock()
_state: dict[str, Any] = {}


def _blank() -> dict[str, Any]:
    return {
        "kind": "",
        "running": False,
        "stop": False,
        "started": 0.0,
        "finished": 0.0,
        "progress": {"phase": "", "detail": "", "done": 0, "total": 0,
                     "fraction": 0.0},
        "result": None,
        "error": "",
        "cancelled": False,
    }


_state.update(_blank())


def status() -> dict[str, Any]:
    """What the status endpoint returns. Safe to poll."""
    with _lock:
        snapshot = dict(_state)
    snapshot.pop("stop", None)
    snapshot["elapsed"] = round(
        (snapshot["finished"] or time.time()) - snapshot["started"], 1
    ) if snapshot["started"] else 0.0
    return snapshot


def is_running() -> bool:
    with _lock:
        return bool(_state["running"])


def stop() -> dict[str, Any]:
    """Ask the running job to stop. Idempotent, and never blocks.

    Cooperative: `Progress.step` is what notices, so the job stops at the next
    file or block rather than being killed mid-write. That is the difference
    between "stopped" and "left a half-written file somewhere".
    """
    with _lock:
        if _state["running"]:
            _state["stop"] = True
            log.info("stop requested for the running %s job", _state["kind"])
    return status()


def _claim(kind: str) -> progress.Progress:
    """Take the single job slot, or raise if something else holds it."""
    with _lock:
        if _state["running"]:
            raise RuntimeError(
                f"a {_state['kind']} is already running — wait for it to "
                f"finish or stop it first")
        _state.update(_blank())
        _state.update({"kind": kind, "running": True, "started": time.time()})

    def mirror(p: progress.Progress) -> None:
        with _lock:
            _state["progress"] = p.snapshot()

    def should_stop() -> bool:
        with _lock:
            return bool(_state["stop"])

    return progress.Progress(on_change=mirror, should_stop=should_stop)


def _release(result: dict[str, Any] | None, error: str = "",
             cancelled: bool = False) -> None:
    with _lock:
        _state.update({
            "running": False, "stop": False, "finished": time.time(),
            "result": result, "error": error, "cancelled": cancelled,
        })


def _run(kind: str, work, prog: progress.Progress) -> None:
    """The one place a job's outcome is decided, so all three ends look alike."""
    try:
        _release(work(prog))
        log.info("%s finished", kind)
    except progress.CancelledError:
        _release(None, cancelled=True)
        log.info("%s was stopped by the user", kind)
    except (crypto.ArchiveError, OSError, ValueError) as exc:
        # Deliberately narrow: these are the failures with something useful to
        # say to a user. Anything else is a bug and must not be flattened into
        # a tidy error message that hides it.
        _release(None, error=str(exc))
        log.warning("%s failed: %s", kind, exc)


def start_backup(home: Path, out: Path, passphrase: str) -> dict[str, Any]:
    """Begin a backup. Returns immediately; poll `status()`.

    Sets backup up on first use, and the returned `recovery_code` is the only
    time it is ever available — see `identity.begin_setup`. On every later
    backup it is `None`, because the code the user already has still works.
    """
    key = identity.master_key()
    code: str | None = None
    wrap: str | None = None
    if key is None:
        key, code, wrap = identity.begin_setup()
    else:
        wrap = identity.recovery_wrap()

    prog = _claim(BACKUP)
    master, reuse_wrap = key, wrap

    def work(p: progress.Progress) -> dict[str, Any]:
        packed = writer.pack(home, out, passphrase, master_key=master,
                             recovery_wrap=reuse_wrap, prog=p)
        return {
            "path": str(packed.path),
            "bytes": packed.bytes_written,
            "summary": packed.summary,
            "manifest": packed.manifest,
            "withheld": packed.withheld,
        }

    threading.Thread(target=_run, args=(BACKUP, work, prog),
                     daemon=True, name="chitragupta-backup").start()
    out_status = status()
    out_status["recovery_code"] = code
    out_status["first_backup"] = code is not None
    return out_status


def start_restore(path: Path, home: Path, *, passphrase: str | None = None,
                  recovery_code: str | None = None,
                  replace_existing: bool = True) -> dict[str, Any]:
    """Begin a restore. Returns immediately; poll `status()`.

    The secret is verified **before** the thread starts, so a wrong passphrase
    is an immediate, obvious refusal rather than a job that appears to run and
    then reports a failure the user has to go looking for.
    """
    with Path(path).open("rb") as src:
        header, _, _ = crypto.read_header(src)
        master = reader.master_key_from(header, passphrase, recovery_code)

    prog = _claim(RESTORE)
    opened = header

    def work(p: progress.Progress) -> dict[str, Any]:
        placed = reader.restore(path, home, passphrase=passphrase,
                                recovery_code=recovery_code,
                                replace_existing=replace_existing, prog=p)
        # Only now: a master key remembered before the restore succeeded would
        # leave this machine claiming to be part of a backup set it does not
        # hold. It is what makes the NEXT backup from this Mac the same set.
        # The wrap comes out of the archive we just opened, so the recovery
        # code the user already has keeps working for backups this machine
        # writes from now on.
        identity.remember_master_key(
            master, opened.wrapped_mk.get(crypto.BY_RECOVERY))
        identity.acknowledge_recovery_code()
        return placed

    threading.Thread(target=_run, args=(RESTORE, work, prog),
                     daemon=True, name="chitragupta-restore").start()
    return status()


def reset_for_tests() -> None:
    """Clear job state between tests. Module state is otherwise process-wide."""
    with _lock:
        _state.update(_blank())
