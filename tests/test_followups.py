"""“Follow up with whoever hasn't replied” — and nobody else.

An open loop saying *"waiting on Rahul"* was true from the moment it was
written until somebody deleted it by hand. Nothing in this app ever went and
looked to see whether Rahul replied, so a week later the user would be chased
about a thing settled on Tuesday.

**The failure this whole feature exists to prevent is chasing somebody who
already answered.** It costs more than not chasing at all: the mail goes out in
the user's name and cannot be taken back. And it is what an agent does by
default, because the commitment list is right there in the brain and reads as
current.

So three states, not two:

* somebody else wrote last → **answered**, and the loop closes itself
* we wrote last → **waiting**, with how many days
* the thread could not be read → **unknown**, and it is reported as unknown

The third is the one worth being careful about. A Gmail outage that reads as
silence would turn into a round of chasing emails to people who already
replied. Unknown says so and is left alone.
"""
from __future__ import annotations

import pytest

from chitragupta import action_log, actions
from chitragupta.actions import REGISTRY, Risk
from chitragupta.agents import followup_tools, permissions

STATES = {
    "t_answered": {"answered": True, "last_from": "Rahul <rahul@work.test>",
                   "waiting_days": 1},
    "t_stale": {"answered": False, "last_from": "me@x.test", "waiting_days": 9},
    "t_fresh": {"answered": False, "last_from": "me@x.test", "waiting_days": 1},
    "t_broken": {"answered": None, "last_from": "", "waiting_days": 0},
}


class FakeGmail:
    def __init__(self):
        self.asked = []

    def is_configured(self):
        return True, ""

    def thread_reply_state(self, thread_id, interactive=False):
        self.asked.append(thread_id)
        return STATES.get(thread_id, {"answered": None, "waiting_days": 0})


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    """A brain per test — these write open loops the next test would read."""
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


@pytest.fixture
def gmail(monkeypatch):
    fake = FakeGmail()
    monkeypatch.setattr(followup_tools, "_gmail", lambda: fake)
    return fake


def _track(about, thread=""):
    return actions.run_now("create_followup", {
        "about": about, "who": about.split(":")[0], "thread_id": thread})


def _text(result):
    return getattr(result, "text", str(result))


def _open_now():
    from chitragupta.brain import get_brain

    return [x["description"] for x in get_brain().get_open_loops(status="open")]


# ── the action ─────────────────────────────────────────────────────────────

def test_tracking_a_follow_up_reaches_nobody():
    assert REGISTRY["create_followup"].risk is Risk.GREEN
    assert permissions.check("create_followup", {"who": "anyone@nowhere"}).allowed


def test_it_stores_the_thread_that_makes_it_self_closing():
    """Without a thread it is a note that stays true forever."""
    from chitragupta.brain import get_brain

    out = _track("Rahul: the proposal", "t_stale")
    assert out["ok"]
    loop = get_brain().store.get_open_loop(out["id"])
    assert loop.metadata["thread_id"] == "t_stale"
    assert loop.metadata["who"] == "Rahul"


def test_a_due_date_is_understood_and_said_back():
    out = _track("Chase Priya", "t_stale")
    assert out["ok"]
    dated = actions.run_now("create_followup", {
        "about": "Chase Priya", "due": "in 3 days"})
    assert "chase after" in dated["detail"]


def test_a_time_it_cannot_read_is_refused_rather_than_ignored():
    out = actions.run_now("create_followup", {"about": "x", "due": "whenever"})
    assert not out["ok"]
    assert "couldn't understand" in out["error"]


def test_it_needs_something_to_be_about():
    assert not actions.run_now("create_followup", {})["ok"]


def test_who_alone_is_enough():
    out = actions.run_now("create_followup", {"who": "Rahul"})
    assert out["ok"] and "Waiting on Rahul" in out["detail"]


def test_tracking_can_be_undone():
    out = _track("Rahul: the proposal", "t_stale")
    assert actions.undo(out["log_id"])["ok"]
    assert "Rahul: the proposal" not in _open_now()


def test_undo_cancels_rather_than_deletes():
    """That the user was once waiting on this is true whether or not they
    still want chasing about it."""
    from chitragupta.brain import get_brain

    out = _track("Rahul: the proposal", "t_stale")
    actions.undo(out["log_id"])
    assert get_brain().store.get_open_loop(out["id"]).status == "cancelled"


# ── the three states ───────────────────────────────────────────────────────

def test_a_reply_closes_the_loop_and_says_so(gmail):
    _track("Rahul: the proposal", "t_answered")
    said = _text(followup_tools.awaiting_reply())
    assert "ANSWERED" in said and "Rahul replied" in said
    assert "Rahul: the proposal" not in _open_now(), "it stayed open"


def test_a_stale_thread_is_worth_chasing(gmail):
    _track("Priya: the contract", "t_stale")
    said = _text(followup_tools.awaiting_reply())
    assert "WORTH CHASING" in said
    assert "9 day(s)" in said
    assert "t_stale" in said, "the chase has no thread to reply into"


def test_a_recent_thread_is_left_alone(gmail):
    """Chasing after one day reads as impatience, in the user's name."""
    _track("Sam: the deck", "t_fresh")
    said = _text(followup_tools.awaiting_reply())
    assert "STILL RECENT" in said
    assert "WORTH CHASING" not in said


def test_the_threshold_can_be_moved(gmail):
    _track("Sam: the deck", "t_fresh")
    assert "WORTH CHASING" in _text(followup_tools.awaiting_reply(stale_days=1))


# ── the state that matters most ────────────────────────────────────────────

def test_a_thread_it_cannot_read_is_unknown_not_unanswered(gmail):
    """A Gmail outage that read as silence would become a round of chasing
    emails to people who already replied."""
    _track("Lee: the invoice", "t_broken")
    said = _text(followup_tools.awaiting_reply())
    assert "COULD NOT CHECK" in said
    assert "do not chase" in said
    assert "WORTH CHASING" not in said


def test_gmail_being_gone_entirely_is_also_unknown(monkeypatch):
    monkeypatch.setattr(followup_tools, "_gmail", lambda: None)
    _track("Lee: the invoice", "t_stale")
    said = _text(followup_tools.awaiting_reply())
    assert "COULD NOT CHECK" in said
    assert "WORTH CHASING" not in said


def test_a_follow_up_with_no_thread_is_unknown_rather_than_stale(gmail):
    """It was never checkable, so it is not evidence of silence."""
    _track("Something I typed myself")
    said = _text(followup_tools.awaiting_reply())
    assert "no thread to check" in said
    assert "WORTH CHASING" not in said


def test_one_broken_thread_does_not_hide_a_real_one(gmail):
    _track("Lee: the invoice", "t_broken")
    _track("Priya: the contract", "t_stale")
    said = _text(followup_tools.awaiting_reply())
    assert "WORTH CHASING" in said and "COULD NOT CHECK" in said


def test_a_thread_that_raises_is_caught(monkeypatch):
    class Exploding(FakeGmail):
        def thread_reply_state(self, thread_id, interactive=False):
            raise RuntimeError("network")

    monkeypatch.setattr(followup_tools, "_gmail", lambda: Exploding())
    _track("Lee: the invoice", "t_stale")
    assert "COULD NOT CHECK" in _text(followup_tools.awaiting_reply())


# ── nothing to say ─────────────────────────────────────────────────────────

def test_waiting_on_nobody_says_so(gmail):
    assert "not waiting on anybody" in _text(followup_tools.awaiting_reply())


def test_everything_answered_is_a_different_answer_from_nothing_tracked(gmail):
    """"Nothing is overdue" and "you are waiting on nobody" are different
    answers, and a person asking this wants to tell them apart."""
    _track("Rahul: the proposal", "t_answered")
    said = _text(followup_tools.awaiting_reply())
    assert "not waiting on anybody" not in said
    assert "ANSWERED" in said


# ── who is taught to chase ─────────────────────────────────────────────────

def test_an_agent_that_can_check_and_chase_gets_the_recipe():
    from chitragupta.agents.prompt import _followup_recipe

    assert _followup_recipe(["awaiting_reply"], ["create_draft"])


@pytest.mark.parametrize("tools,actions_", [
    ([], ["create_draft"]),                      # cannot check
    (["awaiting_reply"], []),                    # cannot chase
    (["list_open_loops"], ["create_draft"]),     # the wrong tool
])
def test_an_agent_that_cannot_check_is_not_told_to_chase(tools, actions_):
    """An agent told to follow up with no way to check is the exact agent that
    chases people who already answered."""
    from chitragupta.agents.prompt import _followup_recipe

    assert _followup_recipe(tools, actions_) == ""


def test_the_recipe_forbids_chasing_from_memory():
    from chitragupta.agents.prompt import _FOLLOWUP_RECIPE

    assert "Never chase from memory" in _FOLLOWUP_RECIPE
    assert "list_open_loops" in _FOLLOWUP_RECIPE, (
        "nothing warned against the list that reads as current and is not")


def test_the_recipe_forbids_chasing_what_it_could_not_check():
    from chitragupta.agents.prompt import _FOLLOWUP_RECIPE

    assert "COULD NOT CHECK" in _FOLLOWUP_RECIPE
    assert "unverified silence" in _FOLLOWUP_RECIPE


def test_the_shipped_inbox_agent_can_do_the_whole_job():
    from chitragupta.agents.presets import get_agent
    from chitragupta.agents.prompt import build

    agent = get_agent("inbox")
    assert "awaiting_reply" in agent.tools
    assert "create_followup" in agent.actions
    prompt = build(name=agent.name, role=agent.role,
                   system_prompt=agent.system_prompt, tools=agent.tools,
                   actions=agent.actions, agent_id=agent.id)
    assert "FOLLOWING UP" in prompt


def test_a_follow_up_is_not_confused_with_a_reminder():
    """A reminder pings the user at a time; a follow-up tracks somebody else's
    answer. An agent that reaches for the wrong one leaves the user with a
    notification instead of a thing that closes itself."""
    from chitragupta.agents.prompt import _BLOCKS

    assert "Not the same as set_reminder" in _BLOCKS["create_followup"]


# ── the scorecard ──────────────────────────────────────────────────────────

def test_chasing_is_on_the_scorecard():
    from chitragupta.agents import evaluation

    card = evaluation.run(include_slow=False)
    keys = {c.key for c in card.checks}
    assert {"followup_checks_first", "followup_spares_the_answered",
            "followup_spares_the_unchecked"} <= keys
    failed = [c.detail for c in card.checks
              if c.key.startswith("followup") and not c.passed]
    assert not failed, failed
