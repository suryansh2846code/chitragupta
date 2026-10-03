"""Renaming an agent — including one we ship.

The id never moves. Everything an agent owns is keyed by it: its chat history,
its files, its tool overrides, its connector grants. A rename that changed the
id would be a delete and a create wearing the same face.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import identity, presets

PRESET = "inbox"


@pytest.fixture(autouse=True)
def _clean():
    identity.clear(PRESET)
    yield
    identity.clear(PRESET)


def test_an_untouched_agent_is_the_one_we_ship():
    shipped = presets.PRESETS[PRESET]
    got = presets.get_agent(PRESET)
    assert (got.name, got.role) == (shipped.name, shipped.role)
    assert identity.get(PRESET) is None


def test_a_preset_can_be_renamed():
    identity.set_identity(PRESET, name="Mail", role="my inbox, triaged")
    got = presets.get_agent(PRESET)
    assert got.name == "Mail"
    assert got.role == "my inbox, triaged"


def test_renaming_never_moves_the_id():
    """Everything the agent owns is keyed by it — notes, chat, grants."""
    identity.set_identity(PRESET, name="Something Else")
    assert presets.get_agent(PRESET).id == PRESET


def test_only_what_was_given_is_overridden():
    """A rename that silently blanked the role would be a worse control than
    no control: the role is what the rail shows under the name."""
    shipped = presets.PRESETS[PRESET]
    identity.set_identity(PRESET, name="Mail")
    got = presets.get_agent(PRESET)
    assert got.name == "Mail"
    assert got.role == shipped.role


def test_resetting_returns_the_shipped_name():
    identity.set_identity(PRESET, name="Mail", role="x")
    identity.clear(PRESET)
    got = presets.get_agent(PRESET)
    assert (got.name, got.role) == (presets.PRESETS[PRESET].name,
                                    presets.PRESETS[PRESET].role)


def test_an_agent_cannot_be_left_without_a_name():
    """A nameless agent is one the user cannot pick out of the rail to fix."""
    with pytest.raises(identity.IdentityRejectedError):
        identity.set_identity(PRESET, name="   ")


def test_a_name_longer_than_the_rail_can_show_is_refused():
    with pytest.raises(identity.IdentityRejectedError):
        identity.set_identity(PRESET, name="x" * 200)


def test_the_override_applies_through_list_agents_too():
    from chitragupta.agents import library

    was = list(library.roster())
    library.add_to_roster(PRESET)
    try:
        identity.set_identity(PRESET, name="Mail")
        listed = {a.id: a for a in presets.list_agents()}
        assert listed[PRESET].name == "Mail"
    finally:
        if PRESET not in was:
            library.remove_from_roster(PRESET)


def test_a_renamed_agent_introduces_itself_by_its_new_name():
    """The name is in the system prompt, so a rename that did not reach it
    would produce an agent the user calls Mail and that calls itself Inbox."""
    identity.set_identity(PRESET, name="Mail")
    assert "'Mail'" in presets.get_agent(PRESET).system_message()


def test_deleting_a_custom_agent_forgets_its_name():
    """Ids are slugs, so the next agent built with that name lands on the same
    id — and would wear the deleted one's rename."""
    from chitragupta.agents.custom import get_custom_store

    store = get_custom_store()
    store.delete("identity-test-agent")
    agent = store.create("Identity Test Agent", role="testing")
    identity.set_identity(agent.id, name="Renamed")
    store.delete(agent.id)

    assert identity.get(agent.id) is None
    rebuilt = store.create("Identity Test Agent", role="testing")
    try:
        assert rebuilt.name == "Identity Test Agent"
    finally:
        store.delete(rebuilt.id)
