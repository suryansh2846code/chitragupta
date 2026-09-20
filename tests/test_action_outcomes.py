"""The agent finds out what its own proposal did.

An agent proposes an action, the user taps Confirm, the frontend runs it, and
the result lands on the card. The agent's turn ended long before that, so it
never learned whether the thing worked — and when a connector refused one, it
could not fix its own call. The best it managed was to apologise, guess at the
argument shape, and ask the user to paste the error back in.

The gate is untouched: none of this runs unless a person pressed something.
"""
from __future__ import annotations

import pytest
from agent_harness import ScriptedProvider

from chitragupta.agents import outcomes, runtime
from chitragupta.agents.agent import AgentMemory
from chitragupta.agents.outcomes import PREFIX, describe, record, settle

FAILED = {"ok": False, "error": "Notion refused that: body.in_trash should be defined."}
WORKED = {"ok": True, "detail": "Archived."}
ACTION = {"connector": "Notion", "tool": "notion-update-page", "server_id": "notion"}


@pytest.fixture
def scripted(monkeypatch):
    def make(reply="I see — Notion cannot delete pages."):
        provider = ScriptedProvider(script=[], final_answer=reply)
        monkeypatch.setattr(runtime, "get_provider", lambda p, m: provider)
        monkeypatch.setattr(runtime, "resolve_usable_model",
                            lambda p, m: (m or "x", None))
        return provider
    return make


def _history(agent_id):
    return [r["content"] for r in AgentMemory().history(agent_id, limit=20)]


# ── what the agent is told ───────────────────────────────────────────────
def test_a_failure_carries_the_connectors_own_words():
    line = describe("mcp_action", ACTION, FAILED)
    assert "FAILED" in line
    assert "body.in_trash should be defined" in line, "the reason was dropped again"


def test_a_success_says_so_without_the_alarm():
    line = describe("mcp_action", ACTION, WORKED)
    assert "done" in line and "FAILED" not in line


def test_the_outcome_is_worded_the_way_the_card_was():
    """One phrasing for an action, or the agent describes it differently from
    the button the user pressed."""
    assert "notion-update-page" in describe("mcp_action", ACTION, WORKED)
    assert "Hi" in describe("send_email", {"to": "a@b.test", "subject": "Hi"}, WORKED)


def test_it_is_recorded_as_the_agents_note_not_as_the_user_speaking(scripted):
    scripted()
    record("research", "mcp_action", ACTION, WORKED)
    rows = AgentMemory().history("research", limit=5)
    assert rows[-1]["role"] == "assistant", "an approval was stored as user speech"
    assert rows[-1]["content"].startswith(PREFIX)


# ── success costs nothing extra ──────────────────────────────────────────
def test_a_success_does_not_spend_a_model_call(scripted):
    """Saying "yes, that worked" costs the user money to repeat the card."""
    provider = scripted()
    out = settle("research", "mcp_action", ACTION, WORKED)
    assert provider.rounds_used == 0, "a successful action ran a follow-up turn"
    assert "agent_note" not in out
    assert any(PREFIX in c for c in _history("research"))


# ── a failure gets exactly one answer ────────────────────────────────────
def test_a_failure_lets_the_agent_answer_for_it(scripted):
    provider = scripted("Notion's connector has no delete — do it in Notion.")
    out = settle("research", "mcp_action", ACTION, FAILED)

    assert provider.rounds_used >= 1, "the agent was never told it had failed"
    assert "no delete" in out["agent_note"]
    assert out["ok"] is False, "the real result must not be rewritten"


def test_the_agent_is_shown_the_reason_not_just_that_it_failed(scripted):
    provider = scripted()
    settle("research", "mcp_action", ACTION, FAILED)
    sent = "\n".join((m.content or "") for round_ in provider.calls for m in round_)
    assert "body.in_trash should be defined" in sent


def test_the_agent_is_allowed_to_say_it_cannot_be_done(scripted):
    """An agent told only to fix it keeps inventing arguments — which is the
    behaviour this replaces."""
    assert "cannot do what the user asked" in outcomes.RETRY_BRIEF
    assert "Do not propose the same call again" in outcomes.RETRY_BRIEF


def test_the_reaction_is_kept_but_the_brief_is_not(scripted):
    """The transcript should read as the agent reacting to its own action, not
    as a turn the user started."""
    scripted("Here is what I would do instead.")
    settle("research", "mcp_action", ACTION, FAILED)
    history = _history("research")
    assert any("what I would do instead" in c for c in history)
    assert not any("An action you proposed has now run" in c for c in history), (
        "the internal brief was stored as conversation"
    )


def test_one_reaction_never_a_loop(scripted):
    """A corrected proposal is a new card, so a person is back in the middle."""
    provider = scripted()
    settle("research", "mcp_action", ACTION, FAILED)
    first = provider.rounds_used
    assert first <= 2, f"a single failure cost {first} model calls"


# ── the gate is untouched ────────────────────────────────────────────────
def test_nothing_happens_without_an_agent_to_tell(scripted):
    provider = scripted()
    out = settle("", "mcp_action", ACTION, FAILED)
    assert provider.rounds_used == 0
    assert "agent_note" not in out


def test_connector_writes_are_still_refused_unattended_by_default():
    """Reporting an outcome back to the agent must not have taught anything
    upstream that a connector write may run on its own.

    Asserted as behaviour rather than as `"mcp_action" in NEVER_UNATTENDED`:
    that membership stopped being the mechanism when connector writes became
    grantable per `server:tool`, and a test pinned to the mechanism would have
    gone green against a gate that no longer refuses anything.
    """
    from chitragupta.agents.permissions import NEVER_UNATTENDED, check

    assert "create_routine" in NEVER_UNATTENDED
    verdict = check("mcp_action", {"server_id": "linear", "tool": "create_issue"})
    assert not verdict.allowed, "a connector write ran with nothing granted"
    assert verdict.blocked_recipients == ("linear:create_issue",)
