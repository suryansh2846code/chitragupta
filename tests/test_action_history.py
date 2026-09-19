"""“What did you do this week?” — and “chase this in two days”.

Two jobs that needed almost no new mechanism, which is why they were left
until the mechanism existed.

**Job 18.** `action_log` has recorded every action since the loop landed, and
only the Inbox screen ever read it back. But "what did you do this week" is a
question asked in chat, of whichever agent is open — and an agent that answers
it with "check the Inbox panel" cannot answer a question about itself. Worse,
an agent with no way to check its own record has nothing but the conversation
to go on, which is exactly what it should not trust when claiming something
was done.

**Job 11.** `create_followup` already stored a `due`, and `awaiting_reply`
ignored it — so "chase this in two days if nothing happens" was accepted,
written down, and then governed by the global three-day default. A date the
user named is a decision about *that* thread and has to beat the default in
both directions: chase when they said, and stay quiet until then.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chitragupta import action_log, actions
from chitragupta.agents import followup_tools
from chitragupta.agents.approvals import describe
from chitragupta.agents.tools import TOOL_DEFS, TOOL_IMPLS


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    import chitragupta.brain as brain_pkg
    import chitragupta.core.store as store_mod
    from chitragupta.config import get_settings

    monkeypatch.setattr(get_settings(), "home", tmp_path, raising=False)
    store_mod.get_store.cache_clear()
    brain_pkg.get_brain.cache_clear()
    action_log.reset_for_tests(tmp_path / "actions.db")
    yield
    action_log.reset_for_tests()
    store_mod.get_store.cache_clear()
    brain_pkg.get_brain.cache_clear()


def _history(**kw):
    return str(TOOL_IMPLS["what_i_did"](**kw))


# ── job 18: what did you do this week ──────────────────────────────────────

def test_an_empty_week_says_nothing_happened():
    assert "Nothing in the last 7 day(s)" in _history()


def test_it_counts_what_worked_what_failed_and_what_was_taken_back():
    """Three outcomes, not two. An undone action is neither a success the user
    should still see as done nor a failure needing attention."""
    actions.run_now("set_reminder", {"message": "call Rahul", "at": "tomorrow 9am"})
    actions.run_now("set_reminder", {"message": "", "at": "nope"})
    undone = actions.run_now("create_followup", {"about": "Priya: the contract"})
    actions.undo(undone["log_id"])

    said = _history()
    assert "1 done" in said
    assert "1 failed" in said
    assert "1 taken back" in said


def test_a_clean_week_mentions_neither_failures_nor_undos():
    """Saying "0 failed" to somebody whose week went fine is noise."""
    actions.run_now("set_reminder", {"message": "x", "at": "tomorrow 9am"})
    said = _history()
    assert "failed" not in said
    assert "taken back" not in said


def test_the_rows_use_the_words_the_card_used():
    actions.run_now("set_reminder", {"message": "call Rahul", "at": "tomorrow 9am"})
    assert "Reminder: call Rahul" in _history()


def test_a_failure_says_why_and_a_success_does_not_repeat_itself():
    actions.run_now("set_reminder", {"message": "", "at": "nope"})
    assert "reminder message required" in _history()


def test_the_window_can_be_narrowed():
    actions.run_now("set_reminder", {"message": "x", "at": "tomorrow 9am"})
    assert "last 1 day(s)" in _history(days=1)


@pytest.mark.parametrize("days", [0, -5, 9999, None])
def test_a_silly_window_is_clamped_rather_than_refused(days):
    assert "day(s)" in _history(days=days)


def test_every_agent_can_answer_it():
    """It is asked of whichever agent is open, not of a specific one."""
    from chitragupta.agents.library import BASE_TOOLS

    assert "what_i_did" in BASE_TOOLS


def test_the_tool_tells_the_agent_not_to_trust_its_own_memory():
    """An agent about to claim it sent something should check the record."""
    assert "your own memory" in TOOL_DEFS["what_i_did"].description


def test_a_tracked_follow_up_is_named_in_the_log():
    """"Track a follow-up" tells a person nothing about which one, and this
    row is read a week later when they have forgotten there was a thread."""
    line = describe("create_followup", {"about": "Priya: the contract",
                                        "due": "in 3 days"})
    assert "Priya: the contract" in line
    assert "chase after in 3 days" in line


def test_a_follow_up_with_only_a_person_still_reads():
    assert "Waiting on Rahul" in describe("create_followup", {"who": "Rahul"})


# ── job 11: chase this in two days if nothing happens ──────────────────────

class FakeGmail:
    """Every thread one day old, unanswered — so only the date decides."""

    def is_configured(self):
        return True, ""

    def thread_reply_state(self, thread_id, interactive=False):
        return {"answered": False, "last_from": "me@x.test", "waiting_days": 1}


@pytest.fixture
def gmail(monkeypatch):
    monkeypatch.setattr(followup_tools, "_gmail", lambda: FakeGmail())


def _track(about, due=""):
    params = {"about": about, "who": about.split(":")[0], "thread_id": "t1"}
    if due:
        params["due"] = due
    out = actions.run_now("create_followup", params)
    assert out["ok"], out.get("error")
    return out


def _waiting():
    return str(followup_tools.awaiting_reply())


def test_a_date_that_has_passed_beats_the_default(gmail):
    """One day old, and the user asked to be chased yesterday."""
    _track("Priya: the contract", due="yesterday 9am")
    said = _waiting()
    assert "WORTH CHASING" in said
    assert "you asked to chase this by now" in said


def test_a_date_still_ahead_keeps_it_quiet(gmail):
    _track("Sam: the deck", due="tomorrow 9am")
    said = _waiting()
    assert "WORTH CHASING" not in said
    assert "you said chase after" in said


def test_with_no_date_the_default_still_governs(gmail):
    _track("Lee: the invoice")
    said = _waiting()
    assert "STILL RECENT" in said
    assert "WORTH CHASING" not in said


def test_the_heading_describes_both_reasons_something_is_in_it(gmail):
    """A heading saying "3+ days" over a one-day-old item the user asked to
    chase today would be describing the wrong rule."""
    _track("Priya: the contract", due="yesterday 9am")
    assert "or a date you set" in _waiting()


def test_a_date_does_not_override_an_answer(monkeypatch):
    """Somebody replying beats a date the user set — chasing them anyway is
    the exact failure the whole feature exists to prevent."""
    class Answered(FakeGmail):
        def thread_reply_state(self, thread_id, interactive=False):
            return {"answered": True, "last_from": "Priya <p@w.test>",
                    "waiting_days": 1}

    monkeypatch.setattr(followup_tools, "_gmail", lambda: Answered())
    _track("Priya: the contract", due="yesterday 9am")
    said = _waiting()
    assert "ANSWERED" in said
    assert "WORTH CHASING" not in said


def test_a_date_does_not_override_not_knowing(monkeypatch):
    """Nor does it turn unverified silence into a chase."""
    monkeypatch.setattr(followup_tools, "_gmail", lambda: None)
    _track("Lee: the invoice", due="yesterday 9am")
    said = _waiting()
    assert "COULD NOT CHECK" in said
    assert "WORTH CHASING" not in said


def test_a_stored_date_survives_the_round_trip():
    out = _track("Priya: the contract", due="in 2 days")
    from chitragupta.brain import get_brain

    loop = get_brain().store.get_open_loop(out["id"])
    assert loop.due_at
    due = datetime.fromisoformat(loop.due_at)
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    assert due > datetime.now(UTC) + timedelta(days=1)


@pytest.mark.parametrize("stamp", ["", None, "not a date", "12345"])
def test_an_unreadable_stored_date_falls_back_to_the_default(stamp):
    assert followup_tools._due_verdict(stamp) is None
