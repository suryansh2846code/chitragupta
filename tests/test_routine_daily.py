"""Routines that run at a wall-clock time — "weekdays at 8:00 AM".

`schedule` could not say it. "Every morning" meant `interval_min=1440`, which
fires 24 hours after whenever you happened to create it and then drifts by
however long each run takes — so the morning brief arrives at 8:04, then 8:11,
then some time in the afternoon. It is the routine people want first and the
schema had no way to express it, which made the unattended half of every job
unreliable.

The clock is driven rather than waited on, so the whole week is checked in
milliseconds and a test cannot pass because it happened to run at the right
hour. Three properties carry the feature:

* **It fires once a day**, whatever the sweep cadence — 288 sweeps and one run.
* **Late is better than never.** A laptop asleep at 08:00 and opened at 11:00
  still gets its morning brief.
* **The card promises what the handler builds.** A user who approves "weekdays
  at 8:00 AM" and gets "every 60 min" was not asked about the thing that
  happened.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chitragupta.routines import (
    WEEKDAYS,
    daily_due,
    describe_schedule,
    parse_days,
    parse_time,
)

#: 2026-09-21 is a Monday, so `+ day` indexes the week from it.
LOCAL = datetime.now().astimezone().tzinfo


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A local wall clock, handed over in UTC the way the store keeps it."""
    return datetime(2026, 9, 21 + day, hour, minute, tzinfo=LOCAL).astimezone(UTC)


WEEKLY = {"trigger": "daily", "at_time": "08:00",
          "days": "mon,tue,wed,thu,fri", "last_run": None}


# ── reading what a person typed ────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("8am", "08:00"), ("08:00", "08:00"), ("8", "08:00"), ("20:30", "20:30"),
    ("7.15pm", "19:15"), ("12am", "00:00"), ("12pm", "12:00"),
    ("  9:05 AM ", "09:05"),
])
def test_a_time_of_day_is_understood(raw, want):
    assert parse_time(raw) == want


@pytest.mark.parametrize("raw", ["", "noon", "later", "25:00", "8:99", None,
                                 "tomorrow at 3"])
def test_something_that_is_not_a_time_of_day_is_refused(raw):
    """`reminders.parse_when` understands "tomorrow at 3" and that is the wrong
    question here — a daily routine has no date, and a parser that returns one
    turns "every morning" into a single reminder for tomorrow."""
    assert parse_time(raw) == ""


@pytest.mark.parametrize("raw,want", [
    ("weekdays", "mon,tue,wed,thu,fri"), ("Weekday", "mon,tue,wed,thu,fri"),
    ("weekends", "sat,sun"), ("daily", ""), ("every day", ""),
    ("Monday, Wednesday", "mon,wed"), ("mon,tue", "mon,tue"),
    ("sat/sun", "sat,sun"), (["Tue", "Thu"], "tue,thu"),
    ("garbage", ""), ("", ""), (None, ""),
])
def test_days_are_understood_however_they_are_written(raw, want):
    assert parse_days(raw) == want


def test_days_are_stored_in_week_order_not_typing_order():
    """So two routines on the same days compare equal and render the same."""
    assert parse_days("fri,mon,wed") == "mon,wed,fri"


def test_an_unrecognised_day_is_dropped_rather_than_guessed():
    """A routine that runs on the wrong days is worse than one that runs on
    all of them."""
    assert parse_days("mon,blursday") == "mon"


# ── when it fires ──────────────────────────────────────────────────────────

def test_it_does_not_fire_before_its_time():
    assert not daily_due(WEEKLY, at(0, 7, 59))


def test_it_fires_at_its_time():
    assert daily_due(WEEKLY, at(0, 8, 0))


def test_a_laptop_opened_at_eleven_still_gets_its_morning_brief():
    """Late is better than never: the user wanted it and did not get one."""
    assert daily_due(WEEKLY, at(0, 11))


def test_it_does_not_fire_twice_in_one_morning():
    ran = {**WEEKLY, "last_run": at(0, 8, 1).isoformat()}
    assert not daily_due(ran, at(0, 8, 6))
    assert not daily_due(ran, at(0, 23, 59))


def test_it_comes_round_again_tomorrow():
    ran = {**WEEKLY, "last_run": at(0, 8, 1).isoformat()}
    assert daily_due(ran, at(1, 8, 0))


@pytest.mark.parametrize("day", [5, 6])
def test_weekdays_means_weekdays(day):
    assert not daily_due(WEEKLY, at(day, 9))


def test_no_day_list_means_every_day():
    every = {"trigger": "daily", "at_time": "20:30", "days": "", "last_run": None}
    assert daily_due(every, at(5, 20, 30))
    assert not daily_due(every, at(5, 20, 29))


def test_it_fires_once_across_a_whole_day_of_sweeps():
    """The property that matters: the scheduler sweeps every few minutes, and
    "is it 8am yet" is true for the rest of the day."""
    fired, last = 0, None
    for minute in range(0, 24 * 60, 5):
        now = at(0, 0) + timedelta(minutes=minute)
        if daily_due({**WEEKLY, "last_run": last}, now):
            fired += 1
            last = now.isoformat()
    assert fired == 1


def test_a_full_week_fires_five_times():
    fired, last = 0, None
    for minute in range(0, 7 * 24 * 60, 15):
        now = at(0, 0) + timedelta(minutes=minute)
        if daily_due({**WEEKLY, "last_run": last}, now):
            fired += 1
            last = now.isoformat()
    assert fired == 5, "weekdays fired on a weekend, or missed a day"


def test_weekday_names_line_up_with_pythons_own_index():
    """`WEEKDAYS` is Monday-first because `datetime.weekday()` is, and the name
    is mapped by position — a reordering here moves every routine by a day."""
    for index, name in enumerate(WEEKDAYS):
        assert datetime(2026, 9, 21 + index, 12, tzinfo=UTC).weekday() == index
        assert name == WEEKDAYS[index]


# ── it never raises on a bad row ───────────────────────────────────────────

@pytest.mark.parametrize("row", [
    {"trigger": "daily", "at_time": "", "last_run": None},
    {"trigger": "daily", "at_time": "lunch", "last_run": None},
    {"trigger": "daily", "at_time": "08:00", "last_run": "not a date"},
    {"trigger": "daily"},
])
def test_a_broken_row_is_not_due_rather_than_a_crash(row):
    assert isinstance(daily_due(row, at(0, 12)), bool)


def test_a_naive_last_run_is_read_as_utc():
    """Older rows were written before the store was consistent about this."""
    naive = at(0, 8, 1).replace(tzinfo=None).isoformat()
    assert not daily_due({**WEEKLY, "last_run": naive}, at(0, 8, 6))


# ── what it is called ──────────────────────────────────────────────────────

@pytest.mark.parametrize("row,want", [
    ({"trigger": "daily", "at_time": "08:00", "days": "mon,tue,wed,thu,fri"},
     "weekdays at 8:00 AM"),
    ({"trigger": "daily", "at_time": "20:30", "days": ""}, "every day at 8:30 PM"),
    ({"trigger": "daily", "at_time": "09:00", "days": "sat,sun"},
     "weekends at 9:00 AM"),
    ({"trigger": "daily", "at_time": "09:00", "days": "tue,thu"},
     "Tue, Thu at 9:00 AM"),
    ({"trigger": "schedule", "interval_min": 60}, "every hour"),
    ({"trigger": "schedule", "interval_min": 15}, "every 15 min"),
    ({"trigger": "new_email"}, "on every new email"),
])
def test_the_schedule_reads_as_a_person_would_say_it(row, want):
    assert describe_schedule(row) == want


# ── the store ──────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    import chitragupta.routines as routines_mod
    from chitragupta.config import get_settings

    monkeypatch.setattr(get_settings(), "home", tmp_path, raising=False)
    monkeypatch.setattr(routines_mod, "_store", None)
    made = routines_mod.RoutineStore()
    monkeypatch.setattr(routines_mod, "_store", made)
    return made


def test_a_daily_routine_round_trips(store):
    row = store.create("Morning brief", "inbox", "daily", "clear my inbox",
                       at_time="8am", days="weekdays")
    assert row["trigger"] == "daily"
    assert row["at_time"] == "08:00"
    assert row["days"] == "mon,tue,wed,thu,fri"
    assert describe_schedule(row) == "weekdays at 8:00 AM"


def test_a_daily_routine_with_no_readable_time_falls_back_to_an_interval(store):
    """A routine that can never fire looks exactly like one that is broken."""
    row = store.create("Broken", "inbox", "daily", "do a thing", at_time="soon")
    assert row["trigger"] == "schedule"
    assert describe_schedule(row) == "every hour"


def test_the_days_can_be_edited_to_every_day(store):
    """"" is meaningful here — it is *every day* — so unlike a name, an empty
    day list is a real edit and must not be skipped as "nothing sent"."""
    row = store.create("X", "inbox", "daily", "y", at_time="8am", days="weekdays")
    updated = store.update(row["id"], days="")
    assert updated["days"] == ""
    assert describe_schedule(updated) == "every day at 8:00 AM"


def test_an_unreadable_time_is_not_an_edit(store):
    row = store.create("X", "inbox", "daily", "y", at_time="8am")
    assert store.update(row["id"], at_time="whenever")["at_time"] == "08:00"


def test_an_old_database_gains_the_new_columns(tmp_path, monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` does nothing to a table that already
    exists, so a user upgrading in place would keep the old shape and every
    read of `at_time` would raise."""
    import sqlite3

    import chitragupta.routines as routines_mod
    from chitragupta.config import get_settings

    path = tmp_path / "routines.db"
    old = sqlite3.connect(str(path))
    old.execute("CREATE TABLE routines (id TEXT PRIMARY KEY, name TEXT NOT NULL,"
                " agent_id TEXT NOT NULL DEFAULT 'personal', trigger TEXT NOT NULL,"
                " interval_min INTEGER NOT NULL DEFAULT 60, instruction TEXT NOT NULL,"
                " enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,"
                " last_run TEXT, last_result TEXT)")
    old.execute("INSERT INTO routines (id,name,trigger,interval_min,instruction,"
                "created_at) VALUES ('r1','Old','schedule',30,'do it','2026-01-01')")
    old.commit()
    old.close()

    monkeypatch.setattr(get_settings(), "home", tmp_path, raising=False)
    upgraded = routines_mod.RoutineStore()
    row = upgraded.get("r1")
    assert row["at_time"] == "" and row["days"] == ""
    assert describe_schedule(row) == "every 30 min"


# ── the action, and the card the user approves ─────────────────────────────

def test_a_time_wins_over_the_trigger_the_model_reached_for(store):
    """Models reach for the trigger they were shown first and then attach
    at="8am" to it. Honouring the trigger over the time turns "every morning
    at 8" into "every 60 minutes" — not late, wrong all day."""
    from chitragupta import actions

    out = actions.run_now("create_routine", {
        "name": "Morning brief", "trigger": "schedule", "at": "8am",
        "days": "weekdays", "instruction": "clear my inbox", "agent": "inbox"})
    assert out["ok"]
    assert store.get(out["id"])["trigger"] == "daily"
    assert "weekdays at 8:00 AM" in out["detail"]


def test_the_card_promises_what_the_handler_will_build():
    """A user who approves "weekdays at 8:00 AM" and gets "every 60 min" was
    not asked about the thing that happened."""
    from chitragupta.agents.approvals import describe

    proposed = {"name": "Morning brief", "trigger": "schedule", "at": "8am",
                "days": "weekdays"}
    assert describe("create_routine", proposed) == (
        "New automation “Morning brief” — weekdays at 8:00 AM")


def test_the_card_still_describes_the_other_two_triggers():
    from chitragupta.agents.approvals import describe

    assert "every hour" in describe(
        "create_routine", {"name": "X", "trigger": "schedule", "interval_min": 60})
    assert "on every new email" in describe(
        "create_routine", {"name": "X", "trigger": "new_email"})


def test_the_prompt_tells_the_agent_which_one_to_reach_for():
    from chitragupta.agents.prompt import _BLOCKS

    block = _BLOCKS["create_routine"]
    assert 'at="8am"' in block
    assert "every morning" in block
    assert "drifts" in block, "nothing said why an interval is not a time"
