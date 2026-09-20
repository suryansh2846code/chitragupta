"""Job 9 — "prep me for my next meeting".

One question, five lookups: which meeting, who is coming, what was said last
time, what is still open with those people, what the user owes them. An agent
can make all five calls and the one it skips is always the same one, because
the model stops as soon as it can write a paragraph that *sounds* prepared. So
the assembly is a tool.

Stops at rung 3 — understand, then prepare. Nothing here acts.

The assertions worth having are the three ways a brief is worse than nothing:

* a brief for a meeting that already finished, which reads perfectly well and
  is never caught;
* "nobody else is coming", invented out of an event that simply carries no
  attendee list;
* a fact about Rahul printed under another attendee's name, because recall is
  semantic and will cheerfully return the neighbour.

The last one was live when this file was written: the brief attributed a
memory naming Rahul to a second attendee it did not mention.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chitragupta.agents import prep_tools


@pytest.fixture(autouse=True)
def clean_slate():
    """Put back exactly what this test added, and nothing else.

    The brain and the task list are shared for the whole session, and both
    are inputs to the thing under test — a calendar entry left behind by the
    test above becomes somebody else's "next meeting", which is how this file
    first went red for reasons that had nothing to do with the code.

    **Removing what we added, rather than emptying the tables.** The first
    version of this fixture did `DELETE FROM memories`, which fixed this file
    and broke `test_brain_v15_end_to_end_loop.py` — that suite builds up
    memories across its own tests, and under random ordering one of these ran
    in the middle of it. A fixture that tidies more than it dirtied is not
    isolation, it is a different leak pointing the other way.
    """
    from chitragupta.brain import get_brain
    from chitragupta.tasks import get_tasks

    store = get_brain().store
    before = {row[0] for row in store._conn.execute("SELECT id FROM memories")}
    tasks = get_tasks()
    tasks_before = {t["id"] for t in tasks.list(include_done=True)}

    yield

    added = [row[0] for row in store._conn.execute("SELECT id FROM memories")
             if row[0] not in before]
    for memory_id in added:
        store.delete(memory_id)
    for task in tasks.list(include_done=True):
        if task["id"] not in tasks_before:
            tasks.delete(task["id"])


def _event(brain, title, *, hours_from_now, attendees="", event_id="e1"):
    start = datetime.now(UTC) + timedelta(hours=hours_from_now)
    text = (f"Event: {title}\nWhen: {start.isoformat()} → "
            f"{(start + timedelta(hours=1)).isoformat()}"
            + (f"\nWith: {attendees}" if attendees else ""))
    brain.ingest(text, source="gcal", kind="event", title=title, fast=True,
                 event_date=start.date().isoformat(),
                 metadata={"start": start.isoformat(), "event_id": event_id})
    return start


@pytest.fixture
def brain():
    from chitragupta.brain import get_brain

    return get_brain()


# ── which meeting ────────────────────────────────────────────────────────
def test_a_meeting_that_already_started_is_not_the_next_one(brain):
    """Today's calendar read at 4pm is mostly history.

    This is the failure nobody catches: a brief for the 11am stand-up reads
    exactly as well as a brief for the 5pm review.
    """
    _event(brain, "Morning stand-up", hours_from_now=-3, event_id="past")
    _event(brain, "Afternoon review", hours_from_now=+3, event_id="soon")

    out = prep_tools.meeting_prep()

    assert "Afternoon review" in out
    assert "NEXT: “Morning stand-up”" not in out


def test_the_soonest_upcoming_meeting_wins(brain):
    _event(brain, "Later thing", hours_from_now=+50, event_id="later")
    _event(brain, "Sooner thing", hours_from_now=+2, event_id="sooner")

    out = prep_tools.meeting_prep()

    assert "NEXT: “Sooner thing”" in out
    assert "After that" in out and "Later thing" in out


def test_an_empty_calendar_does_not_claim_you_are_free(brain):
    """It says the calendar may be stale, which is the other possibility and
    the one the user can do something about."""
    out = prep_tools.meeting_prep()

    assert "Nothing is on your calendar" in out or "No calendar is connected" in out
    assert "synced" in out or "Connectors" in out


# ── who is coming ────────────────────────────────────────────────────────
def test_an_event_with_no_attendee_list_is_not_a_meeting_alone(brain):
    """Plenty of synced events carry no attendees. "Nobody else is coming" is
    a fact we do not have."""
    _event(brain, "Focus block", hours_from_now=+2, attendees="")

    out = prep_tools.meeting_prep()

    assert "does not list who is coming" in out
    assert "WHO:" not in out


def test_the_attendees_are_named(brain):
    _event(brain, "Budget review", hours_from_now=+2,
           attendees="rahul@work.test, zephyrine@nowhere.test")

    out = prep_tools.meeting_prep()

    assert "rahul@work.test" in out
    assert "zephyrine@nowhere.test" in out


# ── what you know, and what you do not ───────────────────────────────────
def test_a_memory_is_never_attributed_to_somebody_it_does_not_mention(brain):
    """The bug this guard exists for, and it was live.

    Recall is semantic, so asking about the second attendee returns a memory
    about Rahul on the same project. Printing it under their name invents a
    fact about a person the user is about to be in a room with — the worst
    possible place to be confidently wrong.

    The address is deliberately one nothing else in the suite uses: written
    with `dana@work.test`, this test went red under the full suite because
    another file had ingested a real memory naming Dana, and the assertion
    "we know nothing about them" stopped being true for reasons that had
    nothing to do with the guard.
    """
    _event(brain, "Budget review", hours_from_now=+2,
           attendees="rahul@work.test, zephyrine@nowhere.test")
    brain.ingest("Rahul asked for the revised figures before Friday.",
                 source="gmail", kind="email", title="Figures", fast=True)

    out = prep_tools.meeting_prep()

    theirs = out.split("zephyrine@nowhere.test:", 1)[1].split("\n\n", 1)[0]
    assert "revised figures" not in theirs, (
        "a memory naming Rahul was printed as context about somebody else")
    assert "nothing in the brain about them" in theirs


def test_somebody_we_know_nothing_about_is_said_so_rather_than_left_out(brain):
    """A name missing from the list reads as "nothing to know", and the user
    cannot tell that apart from "we never synced their mail"."""
    _event(brain, "Intro call", hours_from_now=+2, attendees="stranger@new.test")

    out = prep_tools.meeting_prep()

    assert "stranger@new.test" in out
    assert "nothing in the brain about them yet" in out


def test_what_is_known_about_an_attendee_is_shown(brain):
    _event(brain, "Budget review", hours_from_now=+2, attendees="rahul@work.test")
    brain.ingest("Rahul at rahul@work.test runs procurement.",
                 source="gmail", kind="email", title="Rahul", fast=True)

    assert "procurement" in prep_tools.meeting_prep()


def test_the_calendar_entry_is_not_read_back_as_context(brain):
    """Answering "what do you know about this meeting" with "you have a
    meeting" is the tool quoting its own input."""
    _event(brain, "Budget review", hours_from_now=+2, attendees="rahul@work.test")

    out = prep_tools.meeting_prep()

    assert out.count("Budget review") <= 2, out


# ── what is open ─────────────────────────────────────────────────────────
def test_a_task_you_owe_one_of_them_is_surfaced(brain):
    """The single most useful line in a prep brief: the thing you said you
    would do and have not."""
    from chitragupta.tasks import get_tasks

    _event(brain, "Budget review", hours_from_now=+2, attendees="rahul@work.test")
    get_tasks().add("Send rahul@work.test the revised figures", "friday")

    out = prep_tools.meeting_prep()

    assert "YOU OWE" in out
    assert "revised figures" in out


def test_a_task_about_somebody_else_is_not_surfaced(brain):
    from chitragupta.tasks import get_tasks

    _event(brain, "Budget review", hours_from_now=+2, attendees="rahul@work.test")
    get_tasks().add("Book the dentist", "friday")

    assert "dentist" not in prep_tools.meeting_prep()


# ── the handles that make attribution work ───────────────────────────────
@pytest.mark.parametrize("person,expected", [
    ("rahul@work.test", "rahul"),
    ("Rahul Mehta", "rahul"),
])
def test_a_person_is_matched_by_more_than_their_full_address(person, expected):
    assert expected in prep_tools._handles(person)


def test_a_two_letter_token_is_never_a_handle():
    """It would match half the brain, and every match would be attributed to
    a person by name."""
    assert all(len(h) >= 3 for h in prep_tools._handles("Al Bo <ab@x.test>"))
