"""Retiring an agent, which is not deleting it.

A preset has had both since the roster existed: leaving the roster is retiring,
and the template is always in the Library to add again. A custom agent had only
the destructive one, so "I am not using this right now" and "erase everything
it learned" were the same button — and the one a person reaches for first is
the one that cannot be taken back.

What makes retiring worth having is what survives it, so that is what is
asserted here: the notes, the persona, the conversation and the name all come
back with the agent.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import notes, persona, presets
from chitragupta.agents import profile_files as pf
from chitragupta.agents.agent import AgentMemory
from chitragupta.agents.custom import get_custom_store

NAME = "Retire Test Agent"
AGENT = "retire-test-agent"


@pytest.fixture(autouse=True)
def _clean():
    store = get_custom_store()
    store.delete(AGENT)
    pf.forget(AGENT)
    notes.forget(AGENT)
    persona.forget(AGENT)
    yield
    store.delete(AGENT)
    pf.forget(AGENT)
    notes.forget(AGENT)
    persona.forget(AGENT)


def _built():
    return get_custom_store().create(NAME, role="testing",
                                     system_prompt="Original.")


def test_a_retired_agent_leaves_the_rail():
    store = _built()
    assert any(a.id == AGENT for a in presets.list_agents())

    get_custom_store().retire(AGENT)

    assert not any(a.id == AGENT for a in presets.list_agents())
    assert store is not None


def test_a_retired_agent_is_offered_back():
    _built()
    get_custom_store().retire(AGENT)
    offered = {r["id"]: r for r in get_custom_store().retired()}
    assert AGENT in offered
    assert offered[AGENT]["name"] == NAME
    assert offered[AGENT]["retired_at"]


def test_retiring_keeps_everything_and_restoring_brings_it_back():
    """The whole reason the two acts are different."""
    _built()
    notes.record(AGENT, [{"note": "Replies stay short", "heading": "How you like this done"}])
    persona.set_persona(AGENT, traits=["Witty"])
    AgentMemory().append(AGENT, "user", "hello")

    get_custom_store().retire(AGENT)
    get_custom_store().restore(AGENT)

    assert any(a.id == AGENT for a in presets.list_agents())
    assert "Replies stay short" in (pf.read(AGENT, pf.MEMORY) or "")
    assert persona.get(AGENT)["traits"] == ["Witty"]
    assert AgentMemory().history(AGENT)


def test_a_retired_agent_still_resolves():
    """Its conversation is still readable and a routine that named it must not
    break because somebody put it away — the same reason a template that has
    left the roster still resolves."""
    _built()
    get_custom_store().retire(AGENT)
    assert presets.get_agent(AGENT).id == AGENT


def test_restoring_takes_it_off_the_retired_shelf():
    _built()
    get_custom_store().retire(AGENT)
    get_custom_store().restore(AGENT)
    assert AGENT not in {r["id"] for r in get_custom_store().retired()}


def test_retiring_twice_is_not_a_second_retirement():
    """Otherwise the second press would overwrite the date it was put away."""
    _built()
    assert get_custom_store().retire(AGENT) is True
    assert get_custom_store().retire(AGENT) is False


def test_deleting_is_still_permanent():
    """The other half. Nothing comes back, and it is not on the shelf."""
    _built()
    notes.record(AGENT, [{"note": "Replies stay short", "heading": "How you like this done"}])

    get_custom_store().delete(AGENT)

    assert AGENT not in {r["id"] for r in get_custom_store().retired()}
    assert get_custom_store().get(AGENT) is None
    assert pf.read(AGENT, pf.MEMORY) is None
    assert persona.get(AGENT) is None


def test_deleting_a_retired_agent_still_destroys_it():
    """Retiring is not a safe harbour that makes delete stop working."""
    _built()
    get_custom_store().retire(AGENT)
    assert get_custom_store().delete(AGENT) is True
    assert get_custom_store().get(AGENT) is None


def test_restoring_something_that_was_never_retired_says_so():
    _built()
    assert get_custom_store().restore(AGENT) is True   # a no-op update is fine
    assert get_custom_store().restore("no-such-agent") is False


# ── deleting an agent we ship ──────────────────────────────────────────────

def test_deleting_a_shipped_agent_erases_what_it_accumulated():
    """It has no row to destroy, so "delete" means start again: everything it
    learned and was given goes, and adding it back gives a new agent rather
    than the old one wearing a fresh coat."""
    from chitragupta.agents import erase, identity, library

    PRESET = "inbox"
    was = list(library.roster())
    library.add_to_roster(PRESET)
    try:
        notes.record(PRESET, [{"note": "Replies stay short",
                               "heading": "How you like this done"}])
        persona.set_persona(PRESET, traits=["Witty"])
        identity.set_identity(PRESET, name="Mail")
        AgentMemory().append(PRESET, "user", "hello")

        erase.reset_shipped(PRESET)

        assert pf.read(PRESET, pf.MEMORY) is None
        assert persona.get(PRESET) is None
        assert identity.get(PRESET) is None
        assert AgentMemory().history(PRESET) == []
        # And off the team, so adding it again is a deliberate act.
        assert PRESET not in library.roster()
        # The template itself is untouched — it is what you get back.
        assert presets.PRESETS[PRESET].name == "Inbox"
    finally:
        notes.forget(PRESET); persona.forget(PRESET); pf.forget(PRESET)
        identity.clear(PRESET); AgentMemory().clear(PRESET)
        library.remove_from_roster(PRESET)
        for aid in was:
            library.add_to_roster(aid)
