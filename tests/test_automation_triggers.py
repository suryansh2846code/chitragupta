"""When an automation fires, and the clock problems that make that hard.

Trigger matching used to be an `if/elif` chain inside the engine, so every new
connector event meant another branch in the thing that is supposed to be
generic. Half these tests exist to prove that is gone: a Linear issue, a
webhook, a file change and one automation finishing are all `Event`s, and none
of them appears anywhere in `triggers.py`.

The other half is time, which is where the bugs actually are — a morning brief
that moves by the timezone offset, or twice a year, or fires seven times after a
week with the laptop shut.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from automation_harness import automation, fresh_store

from chitragupta.automation import triggers
from chitragupta.automation.model import Automation
from chitragupta.core.events import Event


@pytest.fixture(autouse=True)
def _own_database():
    fresh_store()
    yield


MONDAY_9AM_UTC = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


def tick(when: datetime = MONDAY_9AM_UTC) -> Event:
    return Event(kind=triggers.TICK, source="scheduler",
                 occurred_at=when.isoformat())


# ── the extension point ────────────────────────────────────────────────────

def test_a_connector_event_needs_no_code_in_the_engine():
    """The whole point. A Linear issue is an `Event`; the engine has never
    heard of Linear and does not need to."""
    auto = automation(trigger={"type": "event", "kind": "issue.opened",
                               "source": "linear"})
    event = Event(kind="issue.opened", source="linear", external_id="LIN-1",
                  data={"repository": "acme/api"})
    assert triggers.matches(auto, event).matched


def test_no_connector_is_named_in_the_automation_core():
    """The architectural claim, checked rather than asserted in a comment.

    Walks the AST so **docstrings do not count** — these modules explain
    themselves with examples, and "Linear" appearing in a sentence about how
    extensibility works is the opposite of the problem. What must not appear is
    a connector name in code: a string compared against, a branch taken.
    """
    import ast
    import pathlib

    from chitragupta.automation import conditions, context, executor, router

    connectors = {"gmail", "github", "linear", "slack", "notion", "gdrive",
                  "imessage", "telegram", "gcal", "apple_mail"}
    offenders: list[str] = []
    for module in (triggers, router, conditions, context, executor):
        tree = ast.parse(pathlib.Path(module.__file__).read_text())
        docstrings = {id(ast.get_docstring(n, clean=False))
                      for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef,
                                        ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node.value) in docstrings:
                continue
            for name in connectors:
                if name in node.value.lower() and len(node.value) < 200:
                    offenders.append(
                        f"{pathlib.Path(module.__file__).name}:{node.lineno} "
                        f"names {name!r}")
    assert not offenders, offenders


@pytest.mark.parametrize("kind,source", [
    ("email.received", "gmail"),
    ("file.changed", "files"),
    ("webhook.received", "webhook"),
    ("task.completed", "tasks"),
    ("calendar.conflict", "gcal"),
    ("automation.completed", "automation"),
    ("automation.failed", "automation"),
    ("push.received", "github"),
])
def test_every_required_event_kind_works_through_one_trigger(kind, source):
    auto = automation(trigger={"type": "event", "kind": kind, "source": source})
    assert triggers.matches(auto, Event(kind=kind, source=source)).matched


def test_a_source_narrows_the_match():
    auto = automation(trigger={"type": "event", "kind": "email.received",
                               "source": "gmail"})
    assert not triggers.matches(
        auto, Event(kind="email.received", source="apple_mail")).matched


def test_a_where_clause_narrows_further():
    auto = automation(trigger={"type": "event", "kind": "push.received",
                               "source": "github",
                               "where": {"repository": "acme/api"}})
    assert triggers.matches(auto, Event(
        kind="push.received", source="github",
        data={"repository": "acme/api"})).matched
    assert not triggers.matches(auto, Event(
        kind="push.received", source="github",
        data={"repository": "acme/www"})).matched


def test_an_unknown_trigger_type_does_not_fire():
    """A spec this build cannot evaluate is one we must not act on."""
    auto = automation(trigger={"type": "quantum"})
    match = triggers.matches(auto, tick())
    assert not match.matched and "unknown trigger" in match.detail


def test_a_malformed_event_does_not_raise():
    auto = automation(trigger={"type": "event", "kind": "x", "source": "y"})
    assert not triggers.matches(auto, Event(kind="", source="")).matched


# ── schedules ──────────────────────────────────────────────────────────────

def test_a_daily_schedule_fires_once_its_time_has_passed():
    auto = automation(trigger={"type": "schedule", "at_time": "08:00"},
                      last_run="")
    assert triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched


def test_it_does_not_fire_before_its_time():
    auto = automation(trigger={"type": "schedule", "at_time": "23:00"})
    assert not triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched


def test_it_fires_once_not_once_per_tick():
    """`last_run` at or after today's target is what says today is answered."""
    auto = automation(trigger={"type": "schedule", "at_time": "08:00"},
                      last_run=MONDAY_9AM_UTC.isoformat())
    assert not triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched


def test_a_missed_run_still_fires_once_when_the_machine_wakes_up():
    """A laptop asleep at 08:00 and opened at 11:00 still produces the brief —
    the user wanted it and did not get one."""
    auto = automation(trigger={"type": "schedule", "at_time": "08:00"})
    late = MONDAY_9AM_UTC.replace(hour=11)
    assert triggers.matches(auto, tick(late), now=late).matched


def test_a_week_of_missed_runs_produces_one_brief_not_seven():
    """Catch up once, not once per missed occurrence."""
    auto = automation(trigger={"type": "schedule", "at_time": "08:00"},
                      last_run=(MONDAY_9AM_UTC - timedelta(days=7)).isoformat())
    assert triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched
    ran = Automation(**{**auto.__dict__, "last_run": MONDAY_9AM_UTC.isoformat()})
    assert not triggers.matches(ran, tick(), now=MONDAY_9AM_UTC).matched


def test_days_restrict_which_weekdays_fire():
    auto = automation(trigger={"type": "schedule", "at_time": "08:00",
                               "days": "sat,sun"})
    assert not triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched
    saturday = MONDAY_9AM_UTC + timedelta(days=5)
    assert triggers.matches(auto, tick(saturday), now=saturday).matched


def test_an_unreadable_time_does_not_fire_and_says_why():
    auto = automation(trigger={"type": "schedule", "at_time": "half eight"})
    match = triggers.matches(auto, tick(), now=MONDAY_9AM_UTC)
    assert not match.matched and "unreadable" in match.detail


# ── timezones ──────────────────────────────────────────────────────────────

def test_a_schedule_is_evaluated_in_its_own_timezone():
    """"Every morning at 8" means eight where the user is. Building the target
    in UTC moves it by the offset, and again twice a year."""
    auto = automation(trigger={"type": "schedule", "at_time": "08:00",
                               "timezone": "Asia/Tokyo"})
    # 00:00 UTC is 09:00 in Tokyo — past 08:00, so it is due.
    due = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    assert triggers.matches(auto, tick(due), now=due).matched
    # 22:00 UTC the previous day is 07:00 Tokyo — not yet.
    early = datetime(2026, 9, 27, 22, 0, tzinfo=UTC)
    assert not triggers.matches(auto, tick(early), now=early).matched


def test_an_unknown_timezone_falls_back_rather_than_breaking_the_automation():
    """A typo in a timezone must not stop an automation from ever running."""
    auto = automation(trigger={"type": "schedule", "at_time": "00:01",
                               "timezone": "Mars/Olympus"})
    assert triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched


def test_next_due_is_exposed_and_is_in_the_future():
    auto = automation(trigger={"type": "schedule", "at_time": "08:00",
                               "days": "mon"})
    upcoming = triggers.next_due(auto, now=MONDAY_9AM_UTC)
    assert upcoming is not None and upcoming > MONDAY_9AM_UTC
    assert upcoming.astimezone().strftime("%a") == "Mon"


# ── intervals and manual ───────────────────────────────────────────────────

def test_an_interval_fires_first_time_then_waits():
    auto = automation(trigger={"type": "interval", "interval_min": 60})
    assert triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched
    just_ran = Automation(**{**auto.__dict__,
                             "last_run": MONDAY_9AM_UTC.isoformat()})
    assert not triggers.matches(just_ran, tick(), now=MONDAY_9AM_UTC).matched
    later = MONDAY_9AM_UTC + timedelta(minutes=61)
    assert triggers.matches(just_ran, tick(later), now=later).matched


def test_an_interval_of_zero_never_fires():
    auto = automation(trigger={"type": "interval", "interval_min": 0})
    assert not triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched


def test_a_manual_automation_only_runs_when_it_is_named():
    auto = automation(id="mine", trigger={"type": "manual"})
    assert triggers.matches(auto, Event(
        kind="automation.requested", source="user",
        data={"automation_id": "mine"})).matched
    assert not triggers.matches(auto, Event(
        kind="automation.requested", source="user",
        data={"automation_id": "someone-else"})).matched


def test_a_manual_automation_ignores_the_clock():
    auto = automation(trigger={"type": "manual"})
    assert not triggers.matches(auto, tick(), now=MONDAY_9AM_UTC).matched


# ── migration from the old shape ───────────────────────────────────────────

@pytest.mark.parametrize("row,expected", [
    ({"trigger": "daily", "at_time": "08:00", "days": "mon"},
     {"type": "schedule", "at_time": "08:00", "days": "mon"}),
    ({"trigger": "schedule", "interval_min": 30},
     {"type": "interval", "interval_min": 30}),
    ({"trigger": "new_email"},
     {"type": "event", "kind": "email.received", "source": "gmail"}),
])
def test_an_existing_routine_keeps_working_without_being_rewritten(row, expected):
    """Back-compatibility is read at load time rather than migrated in the
    database. A migration that rewrites a user's routines is one that can
    corrupt them."""
    auto = Automation.from_row({"id": "r1", "name": "old", **row})
    assert auto.trigger == expected


def test_an_unrecognised_legacy_trigger_becomes_manual():
    """A routine nobody can explain must not run unattended."""
    auto = Automation.from_row({"id": "r", "name": "x", "trigger": "mystery"})
    assert auto.trigger == {"type": "manual"}


def test_a_new_trigger_spec_wins_over_the_legacy_columns():
    auto = Automation.from_row({
        "id": "r", "name": "x", "trigger": "daily", "at_time": "08:00",
        "trigger_json": '{"type": "event", "kind": "issue.opened"}'})
    assert auto.trigger["type"] == "event"
