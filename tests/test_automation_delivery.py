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

**The result lands in Messages**, the one list of things an agent wants to tell
the user. Not a desktop notification, which is gone if they looked away. Not the
agent's chat — that was tried and undone: a result is not part of a conversation
somebody was having, and putting it there also put the automation's whole prompt
in beside it, attributed to a user who typed none of it.
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
def _own_database(tmp_path, monkeypatch):
    """A message store of this test's own.

    They are read newest-first across every agent, so one test's message is the
    next test's "there is already something here".
    """
    from chitragupta import messages
    from chitragupta.config import get_settings

    fresh_store()
    # Through the environment and the cache, because `home` is resolved once
    # and held — setting it on the class looks like it works and does nothing.
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path))
    get_settings.cache_clear()
    messages.reset_for_tests()
    assert get_settings().home == tmp_path, "the home did not take"
    yield
    messages.reset_for_tests()
    get_settings.cache_clear()


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


def sent(agent_id: str = "") -> list[dict]:
    from chitragupta import messages

    rows = messages.get_messages().recent(limit=50)
    return [r for r in rows if not agent_id or r["agent_id"] == agent_id]


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

def test_a_result_arrives_as_a_message_from_the_agent(an_agent):
    """Named, because a list of messages with no sender is a list of things
    that happened to somebody."""
    auto = automation(agent_id=an_agent.id, name="Watch the mail")
    run = {"id": "r1", "state": str(RunState.COMPLETED), "actions_used": 0,
           "outcome": "3 unread replies in the Chitragupta thread."}

    assert engine.deliver_result(auto, run) is True

    posted = sent(an_agent.id)
    assert len(posted) == 1
    assert posted[0]["title"] == "Watch the mail"
    assert "3 unread replies" in posted[0]["body"]
    assert posted[0]["agent_id"] == an_agent.id


def test_it_can_be_opened_back_to_the_run_it_came_from(an_agent):
    """A result you cannot trace is a result you have to take on faith."""
    engine.deliver_result(
        automation(agent_id=an_agent.id, name="Watch"),
        {"id": "run-77", "state": str(RunState.COMPLETED), "actions_used": 1,
         "outcome": "Sent the summary."})

    posted = sent(an_agent.id)[0]
    assert posted["source"] == "automation"
    assert posted["source_id"] == "run-77"


def test_a_quiet_run_says_nothing(an_agent):
    delivered = engine.deliver_result(
        automation(agent_id=an_agent.id),
        {"state": str(RunState.COMPLETED), "actions_used": 0,
         "outcome": NOTHING_TO_REPORT})

    assert delivered is False
    assert sent() == []


def test_a_run_that_stopped_is_marked_as_needing_them(an_agent):
    """A failure the user has to act on is exactly what must not be quiet, and
    it is a different kind of thing from a result they can read later."""
    from chitragupta import messages

    engine.deliver_result(
        automation(agent_id=an_agent.id, name="Watch"),
        {"state": str(RunState.ESCALATED), "actions_used": 0,
         "reason": "no recipient on your allowed list"})

    posted = sent(an_agent.id)[0]
    assert posted["kind"] == messages.NEEDS_YOU
    assert "needs you" in posted["title"]
    assert "no recipient" in posted["body"]


def test_never_means_never_here_too(an_agent):
    engine.deliver_result(
        automation(agent_id=an_agent.id, execution=Execution(deliver="never")),
        {"state": str(RunState.COMPLETED), "actions_used": 1,
         "outcome": "Sent the summary."})
    assert sent() == []


def test_delivering_never_breaks_a_run(an_agent, monkeypatch):
    """The message is a nicety; the run is the work. A store that cannot be
    written is a result that did not arrive, not an automation that failed."""
    def broken(*a, **kw):
        raise RuntimeError("the message store is gone")

    monkeypatch.setattr("chitragupta.messages.get_messages", broken)
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
