"""The background sync loop, which nobody watches run.

`scheduler.py` is the only code in the app that executes unattended on every
machine, on a timer, with no user in front of it. That combination makes its
failures silent by construction: the loop swallows what it must in order to
survive one bad connector, and the symptom a user eventually reports is "my
brain stopped updating", days later.

It sat at 30% coverage (`AUDIT.md` → A12) — `_sync_all`, the 99-line function
that drives every source, almost entirely unexercised. What follows tests the
things *only this module* does, and deliberately not the connectors it calls,
which have their own suite in `tests/connectors/`:

* cancellation actually stopping a pass, and the token reaching *inside* a
  connector rather than only being checked between them
* one failing source not taking the others down with it
* a failed pass staying visible in what the scheduler reports afterwards
* two sweeps never running at once
* the loop surviving stop-then-start, which it did not
* the minute tick — the path that sends email with nobody present

No real clock and no real connector. A test that sleeps is a test nobody runs,
so the loop is driven through `DrivenStop` rather than waited on, and the whole
file finishes in under a second.
"""
from __future__ import annotations

import threading

import pytest

from chitragupta.connectors.base import Connector, SyncResult
from chitragupta.scheduler import Scheduler


class FakeSource(Connector):
    """A connector that records how it was called and does nothing else.

    It is a real `Connector` subclass rather than a stub object because the
    scheduler reads `auto_sync`, `supported_here()` and `is_configured()` off
    the class, and a duck-typed fake would pass while the real contract drifted.
    """

    auto_sync = True
    always_available = True

    #: Filled in per subclass by `_source()`.
    added = 1
    raises: str | None = None
    on_sync = None

    def __init__(self, store=None) -> None:
        self.store = store
        self.fix = None
        self.calls: list[dict] = []

    def is_configured(self) -> tuple[bool, str]:
        return True, ""

    def sync(self, *, cancel=None, interactive=True, **kwargs) -> SyncResult:
        type(self).calls_seen.append(self.name)
        type(self).cancel_seen.append(cancel)
        if type(self).on_sync is not None:
            type(self).on_sync(cancel)
        if type(self).raises:
            raise RuntimeError(type(self).raises)
        return SyncResult(connector=self.name, added=type(self).added,
                          cancelled=bool(cancel is not None and cancel.is_set()))


def _source(name: str, *, added: int = 1, raises: str | None = None, on_sync=None):
    """Build a one-off FakeSource class. Class-level, because the scheduler
    instantiates connectors itself and hands the test no reference to them."""
    return type(f"Fake_{name}", (FakeSource,), {
        "name": name, "label": name.title(), "added": added, "raises": raises,
        "on_sync": staticmethod(on_sync) if on_sync else None,
        "calls_seen": FakeSource.calls_seen, "cancel_seen": FakeSource.cancel_seen,
    })


@pytest.fixture
def isolated(monkeypatch):
    """Everything `_sync_all` reaches that is not a REGISTRY connector, silenced.

    Files, MCP servers and custom apps each have their own loop in `_sync_all`
    and their own tests elsewhere; leaving them live would make every assertion
    below depend on the developer's own machine.
    """
    FakeSource.calls_seen = []
    FakeSource.cancel_seen = []

    monkeypatch.setattr("chitragupta.scheduler._mcp_servers", list)
    monkeypatch.setattr("chitragupta.scheduler._custom_apps", list)
    monkeypatch.setattr("chitragupta.scheduler.FilesConnector.synced_paths",
                        classmethod(lambda cls, store: []))

    class _Store:
        def dedupe(self) -> int:
            return 0

    monkeypatch.setattr("chitragupta.core.store.get_store", lambda: _Store())

    class _Brain:
        def enrich_until_done(self, **_):
            return {}

    monkeypatch.setattr("chitragupta.brain.get_brain", lambda: _Brain())

    # What a sync produced reaches the automation layer. It used to reach it as
    # `sweep(new_email_count=N)` — a count, with no event identity, so a
    # redelivered message was indistinguishable from a new one. Now the summary
    # itself goes to `engine.after_sync`, which turns rows into events. The
    # property under test is unchanged; its shape is not.
    swept: list[dict] = []
    monkeypatch.setattr("chitragupta.automation.engine.after_sync",
                        lambda summary, **kw: swept.append(summary))
    monkeypatch.setattr("chitragupta.automation.engine.tick",
                        lambda **kw: {})
    return swept


def _registry(monkeypatch, *classes) -> None:
    monkeypatch.setattr("chitragupta.scheduler.REGISTRY",
                        {c.name: c for c in classes})


# ── cancellation ─────────────────────────────────────────────────────────────

def test_cancelling_mid_pass_stops_before_the_next_source(isolated, monkeypatch):
    """The user pressed Stop while source two was running.

    Three and four must never start. Checking the flag only at the top of the
    loop is what makes this testable at all, and it is also the thing that was
    never verified: the loop could have been rewritten to check nothing and the
    suite would not have noticed.
    """
    sched = Scheduler()

    def stop_everything(cancel):
        sched.cancel_sync()

    _registry(monkeypatch,
              _source("one"),
              _source("two", on_sync=stop_everything),
              _source("three"),
              _source("four"))

    summary = sched.sync_all()

    assert FakeSource.calls_seen == ["one", "two"], "three and four must not start"
    assert summary["_cancelled"] is True
    assert "three" not in summary and "four" not in summary


def test_the_cancel_token_reaches_inside_the_connector(isolated, monkeypatch):
    """`_sync_all` passes its cancel event *into* `sync()`.

    The comment in the scheduler says so, and the reason is concrete: checking
    the flag only between sources meant cancelling mid-Gmail still waited for
    every remaining message. Nothing tested that the token was actually handed
    over, so the behaviour could regress to a between-sources-only check with
    every test still green.
    """
    sched = Scheduler()
    _registry(monkeypatch, _source("one"))

    sched.sync_all()

    handed_over = FakeSource.cancel_seen[0]
    assert handed_over is not None, "the connector was given no way to stop"
    assert hasattr(handed_over, "is_set"), "must satisfy the Cancellable protocol"


def test_a_connector_reporting_itself_cancelled_marks_the_pass(isolated, monkeypatch):
    """A connector that notices the flag itself and returns early is believed.

    This is the Gmail-mid-message path: the source stops on its own and says so
    through `SyncResult.cancelled`, rather than the loop inferring it.
    """
    sched = Scheduler()

    def cancel_from_inside(cancel):
        cancel.set()

    _registry(monkeypatch, _source("one", on_sync=cancel_from_inside))

    assert sched.sync_all()["_cancelled"] is True


def test_cancel_sync_reports_whether_anything_was_running():
    """The Stop button needs a truthful answer — it is wired to this return
    value, and "nothing was running" is a different message from "stopped"."""
    sched = Scheduler()

    assert sched.cancel_sync() is False, "nothing is running"

    sched.syncing = True
    assert sched.cancel_sync() is True


def test_the_cancel_flag_does_not_leak_into_the_next_pass(isolated, monkeypatch):
    """A cancelled sweep must not poison the one after it.

    `sync_all` clears the event on the way in *and* on the way out. Were either
    missing, one Stop would silently disable background syncing until restart —
    the exact shape of failure this module is prone to, because nothing in the
    UI would say so.
    """
    sched = Scheduler()

    def stop_everything(cancel):
        sched.cancel_sync()

    _registry(monkeypatch, _source("one", on_sync=stop_everything), _source("two"))
    sched.sync_all()
    assert FakeSource.calls_seen == ["one"]

    FakeSource.calls_seen.clear()
    _registry(monkeypatch, _source("one"), _source("two"))
    second = sched.sync_all()

    assert FakeSource.calls_seen == ["one", "two"], "the second pass ran fully"
    assert "_cancelled" not in second


# ── one bad source must not take the others down ─────────────────────────────

def test_one_failing_connector_does_not_stop_the_others(isolated, monkeypatch):
    """Decision H2, at the scheduler level rather than the item level.

    A connector whose service is down, whose token expired, or which raises on
    an unexpected payload is the common case on a real machine — not the edge
    one. If it took the sweep with it, a single broken source would stop the
    brain updating from every other source, invisibly.
    """
    _registry(monkeypatch,
              _source("one"),
              _source("two", raises="its API is down"),
              _source("three"))

    summary = Scheduler().sync_all()

    assert FakeSource.calls_seen == ["one", "two", "three"]
    assert summary["one"]["added"] == 1
    assert summary["three"]["added"] == 1
    assert summary["two"]["added"] == 0


def test_a_failure_is_still_reported_after_the_pass(isolated, monkeypatch):
    """The failure has to survive into `last_result`, because that is the only
    place anything downstream could ever learn about it — the loop deliberately
    does not raise, and nobody is watching the log."""
    _registry(monkeypatch, _source("two", raises="its API is down"))
    sched = Scheduler()

    summary = sched.sync_all()

    assert "its API is down" in summary["two"]["errors"][0]
    assert sched.last_result is summary, "the pass is readable after it ends"
    assert sched.last_run, "and stamped, so a caller can tell how stale it is"


def test_a_long_error_is_cut_before_it_is_stored(isolated, monkeypatch):
    """A provider that returns an HTML error page would otherwise put the whole
    page into `last_result`, which is serialised to the UI on every poll."""
    _registry(monkeypatch, _source("two", raises="x" * 5_000))

    assert len(Scheduler().sync_all()["two"]["errors"][0]) <= 120


# ── two sweeps never run at once ─────────────────────────────────────────────

def test_a_second_sync_is_refused_while_one_is_running(isolated, monkeypatch):
    """The manual Sync button and the 30-minute timer can land together.

    Both paths call `sync_all`, and two concurrent sweeps would have every
    connector writing the same rows twice and racing each other's watermarks.
    The lock is non-blocking on purpose: the second caller gets an immediate,
    honest answer rather than a request that hangs until the first finishes.
    """
    sched = Scheduler()
    second_result: dict = {}
    reached = threading.Event()
    release = threading.Event()

    def hold(cancel):
        reached.set()
        release.wait(timeout=5)

    _registry(monkeypatch, _source("one", on_sync=hold))

    first = threading.Thread(target=sched.sync_all, daemon=True)
    first.start()
    assert reached.wait(timeout=5), "the first sync never started"

    second_result.update(sched.sync_all())
    release.set()
    first.join(timeout=5)

    assert second_result == {"skipped": "a sync is already running"}
    assert FakeSource.calls_seen == ["one"], "the second sweep ran no connector"


def test_the_lock_is_released_even_when_a_pass_blows_up(isolated, monkeypatch):
    """`_sync_all` catches per-connector failures, but the bookkeeping around it
    must survive an unexpected one too — a scheduler that leaks its lock stops
    syncing forever and says nothing."""
    sched = Scheduler()
    real = sched._sync_all
    exploded: list[bool] = []

    def blow_up_once(interactive):
        if not exploded:
            exploded.append(True)
            raise RuntimeError("boom")
        return real(interactive)

    monkeypatch.setattr(sched, "_sync_all", blow_up_once)

    with pytest.raises(RuntimeError):
        sched.sync_all()

    assert sched.syncing is False
    _registry(monkeypatch, _source("one"))
    assert sched.sync_all()["one"]["added"] == 1, "the next pass can still run"


# ── the loop's own lifecycle ─────────────────────────────────────────────────

@pytest.fixture
def no_real_loop(monkeypatch):
    """Start the thread, but not the 30-minute sweep inside it.

    These tests are about `start`/`stop` bookkeeping. Running the real `_loop`
    would execute the startup migration and then idle for 20 seconds, which is
    neither what is under test nor something a suite should wait for.
    """
    tokens: list[threading.Event] = []
    monkeypatch.setattr(Scheduler, "_loop",
                        lambda self, stop: tokens.append(stop))
    return tokens


def test_stop_then_start_syncs_again(no_real_loop, monkeypatch):
    """`--dev` reloads, and `api/app.py` stops the scheduler on shutdown.

    Both mean `stop()` and `start()` can happen inside one process. A scheduler
    that can only ever be started once looks identical to a working one — it
    reports `running = True` — and simply never syncs again.
    """
    sched = Scheduler()
    sched.start()
    sched.stop()

    sched.start()

    assert sched.running is True
    assert not sched._stop.is_set(), (
        "a restarted loop is asked to stop before it has done anything")


def test_restarting_does_not_leave_two_loops_on_one_timer(no_real_loop):
    """The other half of the same fix.

    Un-stopping the shared event would have revived a previous thread still
    inside its 60-second wait. Each thread holds the token it was started with,
    so the old one stays stopped without `start()` having to block on a join.
    """
    sched = Scheduler()
    sched.start()
    sched.stop()
    sched.start()

    first_token, second_token = no_real_loop
    assert first_token is not second_token
    assert first_token.is_set(), "the previous loop must still be told to stop"
    assert not second_token.is_set()


def test_start_is_idempotent(no_real_loop, monkeypatch):
    """Two callers starting the scheduler must not produce two daemon threads
    both sweeping on the same timer."""
    sched = Scheduler()
    sched.start()
    first = sched._thread

    sched.start()

    assert sched._thread is first
    sched.stop()


def test_a_disabled_scheduler_never_starts(no_real_loop, monkeypatch):
    """`sync_enabled` is a user setting, and it has to mean it.

    `get_settings` is replaced rather than the field patched: `Settings` is a
    pydantic model, so `sync_enabled` is not a class attribute to set.
    """
    class _Off:
        sync_enabled = False
        sync_interval_minutes = 30

    monkeypatch.setattr("chitragupta.scheduler.get_settings", lambda: _Off())
    sched = Scheduler()

    sched.start()

    assert sched.running is False
    assert sched._thread is None


# ── the loop body, driven rather than waited on ──────────────────────────────

class DrivenStop:
    """A stop token that answers `wait()` from a script instead of a clock.

    The real loop waits 20 seconds and then 60 at a time. Testing it against a
    real clock would mean a suite that takes minutes, so it is never tested at
    all — which is how 99 lines of unattended code reached 30% coverage.
    """

    def __init__(self, answers: list[bool]) -> None:
        self._answers = list(answers)
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout if timeout is not None else -1)
        return self._answers.pop(0) if self._answers else True

    def is_set(self) -> bool:
        return False


@pytest.fixture
def quiet_loop(monkeypatch):
    """The loop's startup self-heal and its two per-cycle jobs, stubbed out."""
    class _Store:
        @staticmethod
        def dedupe() -> int:
            return 0

    class _Brain:
        store = _Store()

        def run_migrations(self) -> dict:
            return {}

    monkeypatch.setattr("chitragupta.brain.get_brain", lambda: _Brain())
    monkeypatch.setattr(Scheduler, "_fire_reminders", lambda self: None)
    synced: list[bool] = []
    monkeypatch.setattr(Scheduler, "sync_all",
                        lambda self, interactive=False: synced.append(interactive))
    return synced


def test_the_loop_syncs_once_it_has_settled(quiet_loop):
    """A first sync with no CLI command and no button press — the whole point of
    the background loop. It happens after a short settle, so a launch is not
    competing with the first sweep for the same machine."""
    stop = DrivenStop([False, True])

    Scheduler()._loop(stop)

    assert quiet_loop == [False], "one sync, and not an interactive one"
    assert stop.waits[0] == 20, "the settle before the first sweep"


def test_the_loop_leaves_immediately_if_stopped_during_the_settle(quiet_loop):
    """Quitting during launch must not start a sync that outlives the window."""
    Scheduler()._loop(DrivenStop([True]))

    assert quiet_loop == []


def test_the_loop_does_not_resync_every_minute(quiet_loop):
    """It ticks each minute for reminders but syncs on the long interval.

    Confusing the two would re-sync every connector sixty times an hour, which
    on a metered API is a bill rather than a bug report.
    """
    stop = DrivenStop([False, False, False, True])

    Scheduler()._loop(stop)

    assert quiet_loop == [False], "still only the one sync"
    assert stop.waits[1:] == [60, 60, 60], "but a minute tick each time round"


def test_a_startup_migration_failure_does_not_stop_the_loop(monkeypatch, quiet_loop):
    """The self-heal runs before the first sync on every launch.

    If a failing migration took the loop with it, one bad upgrade would stop
    every future sync on that machine, with the app still opening normally.
    """
    def broken():
        raise RuntimeError("the graph rebuild failed")

    monkeypatch.setattr("chitragupta.brain.get_brain", broken)

    Scheduler()._loop(DrivenStop([False, True]))

    assert quiet_loop == [False], "the sync still happened"


def test_a_sync_that_raises_does_not_kill_the_loop(monkeypatch, quiet_loop):
    """`sync_all` already catches per-connector failures, so anything reaching
    here is unexpected — which is exactly when a background thread dying
    silently is worst."""
    monkeypatch.setattr(Scheduler, "sync_all",
                        lambda self, interactive=False: (_ for _ in ()).throw(
                            RuntimeError("unexpected")))
    stop = DrivenStop([False, False, True])

    Scheduler()._loop(stop)

    assert stop.waits[1:] == [60, 60], "it kept ticking"


# ── the minute tick: reminders and scheduled actions ─────────────────────────

@pytest.fixture
def minute_tick(monkeypatch):
    """`_fire_reminders` with its three collaborators faked.

    This is the highest-stakes unattended code in the app: it is the path that
    **sends email and creates calendar events with nobody present**, once a
    minute, forever. It was entirely uncovered.
    """
    notes: list[tuple[str, str]] = []
    monkeypatch.setattr("chitragupta.notify.desktop_notify",
                        lambda title, message: notes.append((title, message)) or True)

    class _Reminders:
        rows: list[dict] = []
        fired: list[str] = []

        def due(self):
            return list(self.rows)

        def mark_fired(self, rid):
            self.fired.append(rid)

    class _Scheduled:
        rows: list[dict] = []
        done: list[tuple[str, str]] = []

        def due(self):
            return list(self.rows)

        def mark_done(self, sid, result):
            self.done.append((sid, result))

    reminders, scheduled = _Reminders(), _Scheduled()
    monkeypatch.setattr("chitragupta.reminders.get_reminders", lambda: reminders)
    monkeypatch.setattr("chitragupta.scheduled.get_scheduled", lambda: scheduled)
    monkeypatch.setattr("chitragupta.routines.sweep", lambda new_email_count=0: None)
    return notes, reminders, scheduled, monkeypatch


def test_a_due_reminder_notifies_once(minute_tick):
    """Marked fired in the same tick that notified.

    The loop runs every 60 seconds against the same store, so a reminder that
    is notified but not marked would notify again every minute until the user
    force-quit the app.
    """
    notes, reminders, _scheduled, _mp = minute_tick
    reminders.rows = [{"id": "r1", "message": "call the dentist", "agent_id": None}]

    Scheduler()._fire_reminders()

    assert len(notes) == 1
    assert "call the dentist" in notes[0][1]
    assert reminders.fired == ["r1"]


def test_a_reminder_from_an_agent_is_attributed_to_it(minute_tick):
    """The user gets a notification with no app in front of them; whose reminder
    it was is the only context available in it."""
    notes, reminders, _scheduled, _mp = minute_tick
    reminders.rows = [{"id": "r1", "message": "stand up", "agent_id": "chief_of_staff"}]

    Scheduler()._fire_reminders()

    assert "Chief_Of_Staff" in notes[0][0]


def test_a_scheduled_action_runs_and_is_marked_done(minute_tick):
    """An action the user approved for later. Marking it done is what stops it
    running again on the next tick — an email sent every 60 seconds forever is
    the worst failure this file can produce."""
    notes, _reminders, scheduled, mp = minute_tick
    scheduled.rows = [{"id": "s1", "type": "send_email", "params": '{"to": "a@b.test"}'}]
    mp.setattr("chitragupta.actions.run_now",
               lambda t, p: {"ok": True, "detail": "Sent to a@b.test"})

    Scheduler()._fire_reminders()

    assert [sid for sid, _ in scheduled.done] == ["s1"]
    assert "Sent to a@b.test" in notes[0][1]


def test_a_failing_scheduled_action_tells_the_user_and_stops(minute_tick):
    """A failure has to be both visible and final.

    Silent would mean the user believes an email went out. Retried forever would
    mean a notification every minute. Neither is acceptable, so it notifies once
    and is marked done.
    """
    notes, _reminders, scheduled, mp = minute_tick
    scheduled.rows = [{"id": "s1", "type": "send_email", "params": "{}"}]
    mp.setattr("chitragupta.actions.run_now",
               lambda t, p: {"ok": False, "error": "no recipient"})

    Scheduler()._fire_reminders()

    assert "no recipient" in notes[0][1]
    assert [sid for sid, _ in scheduled.done] == ["s1"]


def test_a_broken_reminder_store_does_not_stop_scheduled_actions(minute_tick):
    """The three blocks in the tick are independently guarded on purpose.

    They share nothing but the timer, so a failure in one must not silently
    disable the other two — which is precisely how an unattended loop degrades
    without anybody noticing.
    """
    notes, reminders, scheduled, mp = minute_tick

    def explode():
        raise RuntimeError("the reminder table is locked")

    reminders.due = explode
    scheduled.rows = [{"id": "s1", "type": "send_email", "params": "{}"}]
    mp.setattr("chitragupta.actions.run_now", lambda t, p: {"ok": True, "detail": "sent"})

    Scheduler()._fire_reminders()

    assert [sid for sid, _ in scheduled.done] == ["s1"]


def test_a_tick_with_nothing_due_is_silent(minute_tick):
    """No notification when there is nothing to say. An app that pings you to
    report that it has nothing to report is an app people turn off."""
    notes, _reminders, _scheduled, _mp = minute_tick

    Scheduler()._fire_reminders()

    assert notes == []


# ── routines ─────────────────────────────────────────────────────────────────

def test_what_a_sync_added_reaches_the_automation_engine(isolated, monkeypatch):
    """An automation triggered by new mail fires on what Gmail actually added.

    Losing this is the difference between an automation that triggers on new
    mail and one that never triggers at all, and neither state is visible from
    anywhere else.
    """
    _registry(monkeypatch, _source("gmail", added=4))

    Scheduler().sync_all()

    assert len(isolated) == 1
    assert isolated[0]["gmail"]["added"] == 4


def test_every_connector_that_added_something_is_handed_over(isolated, monkeypatch):
    """Not just mail. The old call could only say "some email arrived", so a
    Notion page or a GitHub push could not trigger anything at all — the whole
    reason the engine takes a summary now rather than a count."""
    _registry(monkeypatch, _source("notion", added=3))

    Scheduler().sync_all()

    assert len(isolated) == 1
    assert isolated[0]["notion"]["added"] == 3
