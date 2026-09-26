"""Where a result goes, and who decides there is one.

An automation ran, produced a genuinely useful summary of the user's mail, and
the user found it by opening Automations → History and expanding a run. The
Inbox said "Nothing yet". A result somebody has to go looking for is a result
nobody reads, because nobody goes looking for something they do not know
happened.

Two claims here.

**The agent decides whether there is anything to report.** It is the only thing
that looked. Counting actions instead — the first attempt — gets a watch exactly
backwards: "tell me what arrived" is *answered by the reply* and uses no actions
at all, so the one run that found something was filed as the quiet one.

**The result lands in the chat of the agent that ran it.** Where the user
already is, as the agent's own message, so it can be replied to — and the agent
has the run in its history when they do.
"""
from __future__ import annotations

import pytest
from automation_harness import FakeAgent, automation, build_deps, drive, fresh_store

from chitragupta.automation import engine
from chitragupta.automation.model import (
    NOTHING_TO_REPORT,
    Execution,
    said_nothing,
    worth_delivering,
)
from chitragupta.core.automation_store import RunState


@pytest.fixture(autouse=True)
def _own_database():
    fresh_store()


@pytest.fixture
def an_agent(request):
    """An agent of this test's own, named after it.

    A conversation outlives the agent it belonged to and ids are reused, so a
    fixture that always made "Desk" handed the next test the previous one's
    messages — and the assertions about an empty chat passed or failed
    depending on what ran before them.
    """
    from chitragupta.agents.custom import get_custom_store

    store = get_custom_store()
    agent = store.create(f"Desk {request.node.name[-24:]}",
                         role="watches things")
    yield agent
    store.delete(agent.id)


def chat(agent_id: str) -> list[str]:
    from chitragupta.agents.agent import AgentMemory

    return [m["content"] for m in AgentMemory().history(agent_id, limit=20)]


# ── who decides there is a result ──────────────────────────────────────────

def test_a_report_is_a_result_even_though_it_used_no_actions():
    """The bug this file is named after. A run that summarised the user's mail
    used zero actions — the summary *was* the output — and was filed as a quiet
    run that nobody needed to see."""
    reported = {"state": "completed", "actions_used": 0,
                "outcome": "3 unread replies in the Chitragupta thread."}
    assert worth_delivering(Execution(), reported) is True


def test_the_agent_saying_there_is_nothing_is_believed():
    """The other half. A watch running every two minutes that reported
    "nothing new" each time would bury the one that mattered in its own
    reports."""
    quiet = {"state": "completed", "actions_used": 0,
             "outcome": NOTHING_TO_REPORT}
    assert worth_delivering(Execution(), quiet) is False


def test_the_phrase_is_read_past_punctuation_and_case():
    """Models add a full stop and choose their own capitals."""
    assert said_nothing(f"{NOTHING_TO_REPORT}.")
    assert said_nothing("nothing to report")
    assert said_nothing("  Nothing to report…  ")


def test_anything_beyond_the_phrase_is_treated_as_a_report():
    """Including an explanation that is probably harmless.

    The agent is asked for exactly the phrase and nothing else, so a reply that
    wanders is ambiguous — and the two ways of being wrong are not equal. A
    wrong "this is a report" costs one line in a chat; a wrong "nothing
    happened" loses the thing the automation exists for. Ambiguity resolves
    towards telling the user.
    """
    assert not said_nothing(f"{NOTHING_TO_REPORT} — no new mail since 09:04.")
    assert not said_nothing("Nothing to report from Ana, but Rahul replied.")


def test_a_run_that_did_nothing_and_said_nothing_is_quiet():
    assert said_nothing("")
    assert said_nothing("ran, no action needed")
    assert worth_delivering(Execution(), {"state": "completed",
                                          "actions_used": 0,
                                          "outcome": ""}) is False


def test_the_agent_is_told_the_convention():
    """A convention nobody was told about is a convention nobody follows."""

    deps, fakes = build_deps(agent=FakeAgent(default="ok"))
    drive(automation(), deps)

    prompt = fakes["agent"].prompts[-1]
    assert NOTHING_TO_REPORT in prompt
    assert "nothing worth telling" in prompt


# ── where it goes ──────────────────────────────────────────────────────────

def test_a_result_lands_in_the_chat_of_the_agent_that_ran_it(an_agent):
    """Where the user already is. The Automations screen is where you go to ask
    why something happened, not to find out that it did."""
    auto = automation(agent_id=an_agent.id, name="Watch the mail")
    run = {"state": str(RunState.COMPLETED), "actions_used": 0,
           "outcome": "3 unread replies in the Chitragupta thread."}

    assert engine.deliver_result(auto, run) is True

    said = chat(an_agent.id)
    assert any("Watch the mail" in m for m in said)
    assert any("3 unread replies" in m for m in said)


def test_it_is_the_agents_own_message(an_agent):
    """Not the user's, who said nothing, and not a system notice — which is a
    thing to dismiss rather than a thing to answer."""
    from chitragupta.agents.agent import AgentMemory

    engine.deliver_result(
        automation(agent_id=an_agent.id, name="Watch"),
        {"state": str(RunState.COMPLETED), "actions_used": 1,
         "outcome": "Sent the summary."})

    roles = [m["role"] for m in AgentMemory().history(an_agent.id, limit=5)]
    assert roles and roles[-1] == "assistant"


def test_a_quiet_run_leaves_no_message(an_agent):
    delivered = engine.deliver_result(
        automation(agent_id=an_agent.id),
        {"state": str(RunState.COMPLETED), "actions_used": 0,
         "outcome": NOTHING_TO_REPORT})

    assert delivered is False
    assert chat(an_agent.id) == []


def test_a_run_that_stopped_says_so_in_the_chat(an_agent):
    """A failure the user has to act on is exactly what must not be silent."""
    engine.deliver_result(
        automation(agent_id=an_agent.id, name="Watch"),
        {"state": str(RunState.ESCALATED), "actions_used": 0,
         "reason": "no recipient on your allowed list"})

    said = " ".join(chat(an_agent.id))
    assert "needs you" in said
    assert "no recipient" in said


def test_never_means_never_here_too(an_agent):
    engine.deliver_result(
        automation(agent_id=an_agent.id, execution=Execution(deliver="never")),
        {"state": str(RunState.COMPLETED), "actions_used": 1,
         "outcome": "Sent the summary."})
    assert chat(an_agent.id) == []


def test_delivering_never_breaks_a_run(an_agent, monkeypatch):
    """The chat is a nicety; the run is the work. A conversation store that
    cannot be written must not turn a completed automation into a failed one."""
    def broken():
        raise RuntimeError("the conversation store is gone")

    monkeypatch.setattr("chitragupta.agents.agent.AgentMemory", broken)
    assert engine.deliver_result(
        automation(agent_id=an_agent.id),
        {"state": str(RunState.COMPLETED), "actions_used": 1,
         "outcome": "did it"}) is False


def test_the_turn_itself_leaves_nothing_in_the_chat():
    """An automation's prompt is not something the user said.

    Persisting the turn put the whole thing — goal, fenced context, the lot —
    into the agent's conversation as a message attributed to the USER, who did
    not type 1,590 characters about running an automation unattended. Every run
    added another, and the raw reply landed beside it whether or not there was
    anything worth saying.

    The delivered result is the one message that belongs there.
    """
    from types import SimpleNamespace
    from unittest.mock import patch

    seen = {}

    def fake_turn(agent_id, text, **kw):
        seen.update(kw)
        return SimpleNamespace(reply="ok", trace=[])

    with patch("chitragupta.agents.run_turn", fake_turn):
        engine._plan("someone", "the prompt", {})

    assert seen.get("persist") is False
