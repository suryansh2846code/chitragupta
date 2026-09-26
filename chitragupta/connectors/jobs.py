"""A sync the user started, running in the background with progress.

`POST /api/connectors/{name}/sync` runs the whole sync inside the request. For a
first Gmail pass that is a request held open for minutes, in the six-wide lane
the Connectors page itself uses — so starting a sync could make the page that
started it slow to load. And there was no way to stop one: only the scheduler's
own sweep gets a cancel token, so a user-initiated sync ran to completion
whatever they did.

Both are the same missing thing, and `/CLAUDE.md` names it twice:

* *"Never make the user wait without telling them. Long work is a background job
  with progress that survives a refresh. A spinner with no end state is a bug."*
* *"Anything the user starts, they can stop."*

`/api/sync/now` already had the right shape for the *whole* sweep — a thread plus
a status endpoint to poll. This is that shape for one connector, with the two
things the sweep's version does not have: a count of where it has got to, and a
stop.

## One at a time, on purpose

A job is refused while another is running. That is not a simplification — it is
the bound `docs/ARCHITECTURE.md` §6.9 records: the brain is one SQLite
connection, so two connectors ingesting at once means two writers on it, and
making that safe is a change to the store rather than to this file. The
scheduler is serial for the same reason, so this matches what already happens
rather than quietly widening it.

## What survives what

A job survives a **page refresh**, which is what the rule asks for and what the
registry being in the process gives for free. It does not survive the *app*
restarting — and it does not need to, because the durable position is the sync
checkpoint: `sync_state.recover()` clears the in-flight marker on launch and the
next pass resumes from the last committed page. A job is the *view* of a pass,
not the record of one.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..log import get_logger, suppressed

log = get_logger(__name__)

#: Finished jobs kept so a page that reloads just after one ended can still
#: report how it went. Small: this is a view, and the durable record is the
#: checkpoint plus `observability`.
KEEP_FINISHED = 12


class JobState(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    #: The user stopped it. Distinct from failed, because a pass somebody
    #: deliberately ended is not a fault and must not read as one.
    CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Job:
    """One sync the user asked for, and how far it has got."""

    connector: str
    label: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: JobState = JobState.RUNNING
    started_at: str = field(default_factory=_now)
    finished_at: str = ""

    #: Where it has got to. `total` is 0 until the source says how much there
    #: is — which for a paged read is never, so a caller must render 0 as
    #: "working" rather than as "0%".
    done: int = 0
    total: int = 0
    #: What it is working through right now, in the user's terms.
    doing: str = ""

    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    detail: str = ""

    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def running(self) -> bool:
        return self.state is JobState.RUNNING

    @property
    def percent(self) -> int | None:
        """0–100, or None when the source never said how much there was.

        None rather than 0, because a progress bar sitting at 0% for two minutes
        is the spinner-with-no-end-state this module exists to remove. A caller
        that gets None shows a count instead.
        """
        if self.total <= 0:
            return None
        return min(100, int(self.done / self.total * 100))

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "connector": self.connector, "label": self.label,
                "state": self.state.value, "running": self.running,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "done": self.done, "total": self.total, "doing": self.doing,
                "percent": self.percent, "added": self.added,
                "skipped": self.skipped, "errors": list(self.errors),
                "detail": self.detail, "says": self.says}

    @property
    def says(self) -> str:
        """One line for the row. Never a bare percentage — *"Syncing…"* with a
        count is more use than *"0%"*, and a finished job should say what it
        did rather than that it finished."""
        if self.running:
            if self.total > 0:
                return f"Syncing… {self.done} of {self.total}"
            return f"Syncing… {self.done}" if self.done else "Syncing…"
        if self.state is JobState.CANCELLED:
            return f"Stopped after {self.done}" if self.done else "Stopped"
        if self.state is JobState.FAILED:
            return self.errors[0] if self.errors else "Could not sync."
        return self.detail or f"{self.added} new, {self.skipped} already had"


_JOBS: dict[str, Job] = {}
_ORDER: list[str] = []
_LOCK = threading.Lock()


class BusyError(RuntimeError):
    """Another connector is already syncing.

    Its own type rather than a False return, so a caller cannot ignore it by
    accident — and the message names the connector that holds the slot, because
    *"busy"* on its own is a refusal the user cannot act on.
    """


def _prune() -> None:
    """Drop the oldest finished jobs past the ceiling. Caller holds the lock."""
    finished = [jid for jid in _ORDER
                if jid in _JOBS and not _JOBS[jid].running]
    for jid in finished[:-KEEP_FINISHED] if len(finished) > KEEP_FINISHED else []:
        _JOBS.pop(jid, None)
        with suppressed("forgetting a finished sync job"):
            _ORDER.remove(jid)


def running_job() -> Job | None:
    with _LOCK:
        for jid in reversed(_ORDER):
            job = _JOBS.get(jid)
            if job is not None and job.running:
                return job
    return None


def for_connector(connector: str) -> Job | None:
    """The newest job for one connector, running or not."""
    with _LOCK:
        for jid in reversed(_ORDER):
            job = _JOBS.get(jid)
            if job is not None and job.connector == connector:
                return job
    return None


def all_jobs() -> list[Job]:
    """Newest first. What a page reads to restore progress after a refresh."""
    with _LOCK:
        return [_JOBS[jid] for jid in reversed(_ORDER) if jid in _JOBS]


def start(connector: str, *, label: str = "", params: dict[str, Any] | None = None,
          run: Any = None) -> Job:
    """Begin a sync in the background and return the job watching it.

    `run` is injected so a test does not need a real connector: it is called as
    `run(cancel=…, progress=…, **params)` and must return a `SyncResult`. The
    default reaches `get_connector(connector).sync`.

    Raises `BusyError` when a sync is already in flight — see the module note on
    why that is a bound rather than a simplification.
    """
    existing = for_connector(connector)
    if existing is not None and existing.running:
        # The same connector, already going. Handing back the existing job means
        # a second click is idempotent rather than an error the user has to
        # read — they asked for this sync and it is happening.
        return existing

    busy = running_job()
    if busy is not None:
        raise BusyError(f"{busy.label or busy.connector} is syncing right now. "
                        f"Wait for it to finish, or stop it first.")

    job = Job(connector=connector, label=label or connector)
    with _LOCK:
        _JOBS[job.id] = job
        _ORDER.append(job.id)
        _prune()

    worker = threading.Thread(target=_run, args=(job, params or {}, run),
                              name=f"sync:{connector}", daemon=True)
    worker.start()
    return job


def _run(job: Job, params: dict[str, Any], run: Any) -> None:
    """The thread body. **Never raises** — a thread that dies with an exception
    leaves a job stuck on RUNNING forever, which is the spinner with no end
    state in its worst form."""
    def progress(done: int, total: int, label: str) -> None:
        job.done, job.total, job.doing = done, total, label

    try:
        if run is None:
            from . import get_connector
            run = get_connector(job.connector).sync
        result = run(cancel=job._cancel, progress=progress,
                     interactive=False, **params)
    except Exception as exc:
        log.exception("%s: background sync failed", job.connector)
        job.state = JobState.FAILED
        # Classified, so the row says what happened rather than a library repr.
        from .errors import classify_exception
        job.errors = [classify_exception(job.connector, exc,
                                         label=job.label).message]
        job.finished_at = _now()
        return

    job.added = getattr(result, "added", 0)
    job.skipped = getattr(result, "skipped", 0)
    job.errors = list(getattr(result, "errors", []) or [])
    job.detail = getattr(result, "detail", "") or ""
    job.state = (JobState.CANCELLED if getattr(result, "cancelled", False)
                 else JobState.FAILED if job.errors else JobState.DONE)
    job.finished_at = _now()


def stop(connector: str) -> bool:
    """Ask a connector's sync to stop. Returns False if none was running.

    Cooperative, and it reaches all the way in: the token is the one
    `base.each_guarded`, `pagination.walk` and `retry._wait` all check, so a
    stop lands within one record, one page, or a quarter of a second of a
    backoff — rather than at the end of the pass.
    """
    job = for_connector(connector)
    if job is None or not job.running:
        return False
    job._cancel.set()
    return True


def reset_for_tests() -> None:
    with _LOCK:
        for job in _JOBS.values():
            job._cancel.set()
        _JOBS.clear()
        _ORDER.clear()
