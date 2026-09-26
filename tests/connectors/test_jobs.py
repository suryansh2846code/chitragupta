"""A sync the user started runs in the background, reports progress, and stops.

`POST /api/connectors/{name}/sync` ran the whole pass inside the request. For a
first Gmail pass that is a request held open for minutes — in the six-wide lane
the Connectors page itself loads through, so starting a sync could make the page
that started it slow. And nothing could stop one: only the scheduler's own sweep
gets a cancel token.

Both are the same missing thing, and `/CLAUDE.md` names it twice: *"long work is
a background job with progress that survives a refresh"*, and *"anything the
user starts, they can stop"*.

Two properties are load-bearing and easy to get wrong:

* **A thread that dies must not leave a job on RUNNING.** That is the spinner
  with no end state in its worst form — permanent, and pointing at nothing.
* **One at a time.** Not a simplification: the brain is one SQLite connection,
  so two connectors ingesting at once means two writers on it
  (`docs/ARCHITECTURE.md` §6.9). The scheduler is serial for the same reason.
"""
from __future__ import annotations

import threading
import time

import pytest

from chitragupta.connectors import jobs
from chitragupta.connectors.base import SyncResult
from chitragupta.connectors.jobs import BusyError, JobState


@pytest.fixture(autouse=True)
def _no_jobs_left_running():
    jobs.reset_for_tests()
    yield
    jobs.reset_for_tests()


def settle(predicate, *, timeout: float = 3.0) -> bool:
    """Wait for a background thread to reach a state, briefly.

    Polled rather than slept: a fixed sleep is either flaky or slow, and this is
    the one place a test genuinely has to wait for another thread.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def finishing(**fields):
    """A sync that returns immediately."""
    def run(*, cancel=None, progress=None, interactive=False, **params):
        return SyncResult(connector="demo", **fields)
    return run


# ── it runs in the background ─────────────────────────────────────────────


def test_starting_a_sync_returns_before_it_finishes():
    """The whole point: the request does not hold the lane open."""
    release = threading.Event()

    def slow(*, cancel=None, progress=None, interactive=False, **params):
        release.wait(3.0)
        return SyncResult(connector="demo", added=7)

    job = jobs.start("demo", label="Demo", run=slow)

    assert job.running
    assert job.state is JobState.RUNNING
    release.set()
    assert settle(lambda: not job.running)
    assert job.added == 7


def test_a_finished_job_says_what_it_did():
    job = jobs.start("demo", run=finishing(added=3, skipped=1,
                                           detail="3 new, 1 already had"))

    assert settle(lambda: not job.running)
    assert job.state is JobState.DONE
    assert job.says == "3 new, 1 already had"


def test_a_job_survives_being_looked_up_again():
    """What makes progress survive a page refresh: the browser asks again and
    the answer is still there."""
    job = jobs.start("demo", run=finishing(added=1))
    settle(lambda: not job.running)

    assert jobs.for_connector("demo") is job
    assert [j.id for j in jobs.all_jobs()] == [job.id]


# ── progress ──────────────────────────────────────────────────────────────


def test_progress_reaches_the_job_as_the_sync_reports_it():
    seen = threading.Event()

    def counting(*, cancel=None, progress=None, interactive=False, **params):
        progress(120, 600, "Gmail")
        seen.set()
        return SyncResult(connector="demo", added=120)

    job = jobs.start("demo", run=counting)
    assert seen.wait(3.0)
    assert settle(lambda: job.done == 120)

    assert job.total == 600
    assert job.doing == "Gmail"
    assert job.percent == 20


def test_a_source_that_never_says_how_much_there_is_reports_no_percentage():
    """None rather than 0: a bar sitting at 0% for two minutes is the spinner
    with no end state this exists to remove. A caller shows a count instead."""
    def counting(*, cancel=None, progress=None, interactive=False, **params):
        progress(40, 0, "Drive")
        return SyncResult(connector="demo", added=40)

    job = jobs.start("demo", run=counting)
    assert settle(lambda: not job.running)

    assert job.percent is None


def test_what_it_says_while_running_is_readable_either_way():
    from chitragupta.connectors.jobs import Job

    assert Job(connector="d", label="D").says == "Syncing…"
    assert Job(connector="d", label="D", done=40).says == "Syncing… 40"
    assert Job(connector="d", label="D", done=40,
               total=600).says == "Syncing… 40 of 600"


# ── stopping ──────────────────────────────────────────────────────────────


def test_a_running_sync_can_be_stopped():
    started, stopped = threading.Event(), threading.Event()

    def watching(*, cancel=None, progress=None, interactive=False, **params):
        started.set()
        while not cancel.is_set():
            time.sleep(0.01)
        stopped.set()
        return SyncResult(connector="demo", added=2, cancelled=True)

    job = jobs.start("demo", run=watching)
    assert started.wait(3.0)

    assert jobs.stop("demo") is True
    assert stopped.wait(3.0)
    assert settle(lambda: not job.running)
    assert job.state is JobState.CANCELLED


def test_stopping_is_not_the_same_as_failing():
    """A pass somebody deliberately ended is not a fault and must not read as
    one — a user who sees enough red badges stops reading them."""
    job = jobs.start("demo", run=finishing(added=2, cancelled=True))
    assert settle(lambda: not job.running)

    assert job.state is JobState.CANCELLED
    assert job.state is not JobState.FAILED
    assert "Stopped" in job.says


def test_stopping_nothing_says_so_rather_than_pretending():
    assert jobs.stop("demo") is False


# ── one at a time ─────────────────────────────────────────────────────────


def test_a_second_connector_is_refused_while_one_is_running():
    """The bound `ARCHITECTURE.md` §6.9 records: two connectors ingesting at
    once means two writers on the one SQLite connection."""
    release = threading.Event()

    def slow(*, cancel=None, progress=None, interactive=False, **params):
        release.wait(3.0)
        return SyncResult(connector="demo")

    jobs.start("gmail", label="Gmail", run=slow)
    try:
        with pytest.raises(BusyError) as caught:
            jobs.start("gdrive", label="Drive", run=slow)
        assert "Gmail" in str(caught.value), "it names which source holds the slot"
    finally:
        release.set()


def test_pressing_sync_twice_on_the_same_source_is_idempotent():
    """They asked for this sync and it is happening. An error here would be a
    refusal the user has to read about something already true."""
    release = threading.Event()

    def slow(*, cancel=None, progress=None, interactive=False, **params):
        release.wait(3.0)
        return SyncResult(connector="demo")

    first = jobs.start("gmail", label="Gmail", run=slow)
    try:
        again = jobs.start("gmail", label="Gmail", run=slow)
        assert again is first
    finally:
        release.set()


def test_the_slot_frees_when_the_job_ends():
    job = jobs.start("gmail", label="Gmail", run=finishing(added=1))
    assert settle(lambda: not job.running)

    other = jobs.start("gdrive", label="Drive", run=finishing(added=1))

    assert settle(lambda: not other.running)


# ── a thread that dies must not leave a job running forever ───────────────


def test_a_sync_that_raises_ends_the_job_rather_than_stranding_it():
    """The spinner with no end state in its worst form: permanent, and pointing
    at nothing."""
    def exploding(*, cancel=None, progress=None, interactive=False, **params):
        raise RuntimeError("the connector blew up")

    job = jobs.start("demo", label="Demo", run=exploding)

    assert settle(lambda: not job.running), "the job never left RUNNING"
    assert job.state is JobState.FAILED
    assert job.finished_at


def test_a_failure_is_reported_as_a_sentence_not_a_repr():
    def exploding(*, cancel=None, progress=None, interactive=False, **params):
        raise RuntimeError("internal detail nobody should read")

    job = jobs.start("demo", label="Demo", run=exploding)
    assert settle(lambda: not job.running)

    assert job.errors
    assert "internal detail" not in job.errors[0]
    assert "Demo" in job.errors[0]


def test_a_sync_that_reports_errors_is_failed_not_done():
    job = jobs.start("demo", run=finishing(errors=["Gmail needs you to sign in."]))
    assert settle(lambda: not job.running)

    assert job.state is JobState.FAILED
    assert job.says == "Gmail needs you to sign in."


# ── the list stays bounded ────────────────────────────────────────────────


def test_finished_jobs_do_not_accumulate_forever():
    for index in range(jobs.KEEP_FINISHED + 6):
        job = jobs.start(f"demo{index}", run=finishing(added=1))
        assert settle(lambda j=job: not j.running)

    assert len(jobs.all_jobs()) <= jobs.KEEP_FINISHED + 1


def test_a_job_never_carries_the_cancel_token_across_the_wire():
    """`threading.Event` is not JSON, and a caller that saw one could set it."""
    job = jobs.start("demo", run=finishing(added=1))
    settle(lambda: not job.running)

    payload = job.as_dict()

    assert "_cancel" not in payload
    import json
    assert json.loads(json.dumps(payload)) == payload
