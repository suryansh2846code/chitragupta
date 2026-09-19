"""Several actions, one judgement, one button.

*"Clear the emails that don't need my attention"* is seventeen decisions and
one intention. Rendering it as seventeen cards asks a person to make the same
judgement seventeen times, and the second one is already being made without
reading — so a model may wrap its proposals in `<plan>`.

The claims that matter, and what each one is guarding against:

* **A plan is as risky as its worst step.** Nine green archives beside one
  amber send is a card that sends an email.
* **`parse_actions` still finds every action inside a plan.** The unattended
  path judges each one on its own merits through `run_or_queue`, and a wrapper
  the *model* wrote must not be able to widen that. This is the security claim
  in the feature and it is the one worth breaking on purpose.
* **It stops at the first failure and says exactly how far it got.** The steps
  after a failure were written assuming the one before worked. `_mail_triage`
  learned the reporting half first: "It failed" after eight of twelve moved is
  a worse answer than the truth.
* **Undo runs newest-first**, because the steps ran forwards.
"""
from __future__ import annotations

import pytest

from chitragupta import action_log, actions
from chitragupta.actions import ActionPlan, Risk


@pytest.fixture(autouse=True)
def _fresh_log(tmp_path):
    """A log per test, and no reminders left behind.

    `set_reminder` is the convenient green action to build a plan out of, and
    these tests make dozens of them — in the session-wide store every other
    module shares. `reminders.upcoming()` returns the twenty soonest, so a
    module that created one for 2030 and then looked for it found twenty of
    ours instead. The reminder it made was fine; ours had buried it.

    So: snapshot, then remove exactly what this test added. Clearing the table
    would also delete a row somebody else was relying on.
    """
    from chitragupta.reminders import get_reminders

    store = get_reminders()
    before = {r["id"] for r in store.upcoming(limit=500)}
    action_log.reset_for_tests(tmp_path / "actions.db")
    yield
    action_log.reset_for_tests()
    for row in store.upcoming(limit=500):
        if row["id"] not in before:
            store.delete(row["id"])


PLAN_REPLY = """Here is what I found.
<plan rationale="17 emails; 2 need you">
<action type="set_reminder" at="tomorrow 9am">chase Rahul</action>
<action type="set_reminder" at="tomorrow 10am">review the deck</action>
</plan>"""


def _reminders(*messages):
    return [{"type": "set_reminder",
             "params": {"message": m, "at": "tomorrow 9am"}} for m in messages]


# ── parsing ────────────────────────────────────────────────────────────────

def test_a_plan_carries_its_rationale_and_its_steps():
    (plan,) = actions.parse_plans(PLAN_REPLY)
    assert plan.rationale == "17 emails; 2 need you"
    assert [s["type"] for s in plan.steps] == ["set_reminder", "set_reminder"]
    assert plan.steps[0]["params"]["message"] == "chase Rahul"


def test_the_unattended_parser_still_sees_every_action_inside_a_plan():
    """The security claim. A routine puts each action through the gate on its
    own; a `<plan>` wrapper is written by the *model*, and if it could hide
    actions from `parse_actions` it would be a way to smuggle one past the
    check that `run_or_queue` performs."""
    assert len(actions.parse_actions(PLAN_REPLY)) == 2


def test_a_plan_with_no_usable_steps_is_dropped_not_shown_empty():
    assert actions.parse_plans("<plan>nothing here</plan>") == []
    assert actions.parse_plans(
        '<plan><action type="mail_triage">{oops</action></plan>') == []


def test_a_reply_with_no_plan_parses_as_it_always_did():
    reply = '<action type="set_reminder" at="tomorrow">x</action>'
    assert actions.parse_plans(reply) == []
    assert len(actions.parse_actions(reply)) == 1


def test_stripping_a_plan_leaves_its_actions_where_they_were():
    stripped = actions.strip_plans(PLAN_REPLY)
    assert "<plan" not in stripped
    assert stripped.count("<action") == 2


@pytest.mark.parametrize("text", [
    "<plan>", "</plan>", "<plan rationale=", "<plan><plan></plan>",
    '<plan rationale="a"><action type="nope">x</action></plan>',
])
def test_malformed_plans_never_crash(text):
    assert isinstance(actions.parse_plans(text), list)


# ── risk is the worst step ─────────────────────────────────────────────────

def test_a_plan_is_as_risky_as_its_worst_step():
    green = ActionPlan("", [{"type": "set_reminder", "params": {}}])
    mixed = ActionPlan("", [{"type": "set_reminder", "params": {}},
                            {"type": "send_email", "params": {}}])
    with_red = ActionPlan("", [{"type": "set_reminder", "params": {}},
                               {"type": "mail_triage", "params": {}}])
    assert green.risk() is Risk.GREEN
    assert mixed.risk() is Risk.AMBER, (
        "nine green steps beside one amber send is a card that sends an email")
    assert with_red.risk() is Risk.RED


def test_an_unknown_step_does_not_quietly_lower_the_tier():
    plan = ActionPlan("", [{"type": "send_email", "params": {}},
                           {"type": "not_a_real_action", "params": {}}])
    assert plan.risk() is Risk.AMBER


# ── running ────────────────────────────────────────────────────────────────

def test_a_plan_that_works_reports_every_step():
    out = actions.run_plan(_reminders("one", "two", "three"))
    assert out["ok"]
    assert len(out["steps"]) == 3
    assert out["skipped"] == []
    assert "3" in out["detail"]


def test_a_plan_stops_at_the_first_failure_and_names_what_never_started():
    """"Six of nine" does not tell anybody WHICH three did not happen, and a
    plan that half-ran is exactly when a person needs to know."""
    steps = [*_reminders("one"),
             {"type": "set_reminder", "params": {"message": "", "at": "x"}},
             *_reminders("three", "four")]
    out = actions.run_plan(steps)

    assert out["ok"] is False
    assert len(out["steps"]) == 2, "it kept going after a failure"
    assert [s["summary"] for s in out["skipped"]] == ["Reminder: three",
                                                      "Reminder: four"]
    assert "1 of 4 done" in out["detail"]
    assert "2 not started" in out["detail"]


def test_a_plan_whose_first_step_fails_says_nothing_was_done():
    steps = [{"type": "set_reminder", "params": {"message": "", "at": "x"}},
             *_reminders("two")]
    out = actions.run_plan(steps)
    assert "Nothing was done" in out["detail"]
    assert out["undoable"] == []


def test_every_step_gets_its_own_log_entry():
    """Undo is per action, so the log has to be per action too — one row for a
    plan would leave nothing addressable to take back."""
    actions.run_plan(_reminders("one", "two"))
    assert len(action_log.recent()) == 2


def test_an_empty_plan_is_not_a_success():
    out = actions.run_plan([])
    assert out["steps"] == []
    assert out["undoable"] == []


# ── undoing ────────────────────────────────────────────────────────────────

def test_a_plan_is_undone_newest_first():
    """The steps ran forwards, so they are unwound backwards — a plan that
    created a thing and then referred to it has to come apart the way it went
    together."""
    out = actions.run_plan(_reminders("one", "two", "three"))
    ran = [s["result"]["log_id"] for s in out["steps"]]
    assert out["undoable"] == list(reversed(ran))


def test_undoing_a_plan_reports_per_step_not_as_one_verdict():
    """Some of a plan is undoable and some is not. A single "undone" over a
    sent email among four archived threads would be claiming something untrue
    about the email."""
    out = actions.run_plan(_reminders("one", "two"))
    undone = actions.undo_plan(out["undoable"])
    assert undone["reversed"] == 2
    assert [s["ok"] for s in undone["steps"]] == [True, True]
    assert len(undone["steps"]) == 2


def test_undoing_a_plan_with_nothing_reversible_says_so():
    out = actions.undo_plan([])
    assert out["ok"] is False
    assert "nothing to take back" in out["detail"].lower()


def test_only_the_steps_that_succeeded_are_offered_for_undo():
    steps = [*_reminders("one"),
             {"type": "set_reminder", "params": {"message": "", "at": "x"}},
             *_reminders("three")]
    out = actions.run_plan(steps)
    assert len(out["undoable"]) == 1


# ── what the agent is told ─────────────────────────────────────────────────

def test_the_agent_hears_about_every_step_including_the_ones_skipped():
    from chitragupta.agents.agent import AgentMemory
    from chitragupta.agents.outcomes import settle_plan

    steps = [*_reminders("one"),
             {"type": "set_reminder", "params": {"message": "", "at": "x"}},
             *_reminders("three")]
    out = actions.run_plan(steps, agent_id="personal")
    settle_plan("personal", out)

    said = " ".join(r["content"] for r
                    in AgentMemory().history("personal", limit=40))
    assert "Reminder: one" in said
    assert "FAILED" in said
    assert "not started" in said, (
        "the agent was left to infer which steps never ran, from a count")


def test_a_failed_plan_spends_exactly_one_follow_up_turn(monkeypatch):
    """The asymmetry that governs a single action governs a plan too, one level
    up: a nine-step plan that fails on the seventh must not become seven model
    calls, each apologising for the same thing.

    Asserts `== 1`, not `<= 1`. The looser form passes at zero, which is the
    version of this test that proves nothing.
    """
    import chitragupta.agents.runtime as runtime
    from chitragupta.agents import outcomes

    turns = []
    monkeypatch.setattr(runtime, "run_turn",
                        lambda *a, **k: (turns.append(a), _FakeTurn())[1])

    steps = [*_reminders(*"abcdef"),
             {"type": "set_reminder", "params": {"message": "", "at": "x"}},
             *_reminders("h", "i")]
    out = actions.run_plan(steps, agent_id="personal")
    settled = outcomes.settle_plan("personal", out)

    assert len(turns) == 1
    assert settled["agent_note"], "the agent answered and nobody kept it"


def test_a_plan_that_worked_spends_no_follow_up_at_all(monkeypatch):
    """Spending a model call to say "yes, that worked" bills the user to tell
    them what the card already says."""
    import chitragupta.agents.runtime as runtime
    from chitragupta.agents import outcomes

    turns = []
    monkeypatch.setattr(runtime, "run_turn",
                        lambda *a, **k: (turns.append(a), _FakeTurn())[1])

    out = actions.run_plan(_reminders("one", "two"), agent_id="personal")
    outcomes.settle_plan("personal", out)
    assert turns == []


class _FakeTurn:
    reply = "I see — the seventh reminder had no message."
