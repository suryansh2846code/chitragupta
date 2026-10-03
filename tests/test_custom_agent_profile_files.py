"""An agent the user built keeps its prose in the same place a preset does.

Two things have to be true, and neither is automatic:

* **An agent born today is file-backed.** `custom_agents.system_prompt` is the
  column that used to be the only answer; a new agent writes `persona.md` at
  creation so the file is the answer from the first turn and the column is only
  ever read for agents that predate it. One live copy, not two.
* **Deleting an agent deletes its notes.** Ids are slugs of the name, so
  deleting "Chotu" and building another "Chotu" lands on the same id — the
  hazard `custom.delete` already names for tool overrides, and worse here,
  because notes are prose the new agent will act on.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import profile_files as pf
from chitragupta.agents.custom import get_custom_store

NAME = "Profile Test Agent"
AGENT_ID = "profile-test-agent"


@pytest.fixture(autouse=True)
def _clean():
    store = get_custom_store()
    store.delete(AGENT_ID)
    pf.forget(AGENT_ID)
    yield
    store.delete(AGENT_ID)
    pf.forget(AGENT_ID)


def test_a_new_agent_is_born_with_its_persona_in_a_file():
    get_custom_store().create(NAME, role="testing",
                              system_prompt="Be exceptionally brief.")
    assert pf.read(AGENT_ID, pf.PERSONA) == "Be exceptionally brief."


def test_an_agent_built_without_instructions_gets_no_persona_file():
    """No file means "nothing of its own", which is the truthful state — an
    empty file would read as "the user deliberately cleared it"."""
    get_custom_store().create(NAME, role="testing", system_prompt="")
    assert pf.read(AGENT_ID, pf.PERSONA) is None


def test_the_file_is_what_the_agent_answers_with():
    from chitragupta.agents import presets

    get_custom_store().create(NAME, role="testing", system_prompt="Original.")
    pf.write(AGENT_ID, pf.PERSONA, "Edited in the profile.")
    assert presets.get_agent(AGENT_ID).system_prompt == "Edited in the profile."


def test_deleting_an_agent_takes_its_notes_with_it():
    store = get_custom_store()
    store.create(NAME, role="testing", system_prompt="Original.")
    pf.write(AGENT_ID, pf.MEMORY, "- the deleted agent's standing instruction")

    store.delete(AGENT_ID)

    assert pf.read(AGENT_ID, pf.MEMORY) is None
    assert pf.read(AGENT_ID, pf.PERSONA) is None


def test_an_agent_rebuilt_with_the_same_name_inherits_nothing():
    """The whole point: the id is deterministic, so this is the collision that
    actually happens rather than one that theoretically could."""
    store = get_custom_store()
    store.create(NAME, role="testing", system_prompt="First.")
    pf.write(AGENT_ID, pf.MEMORY, "- learned by the first agent")
    store.delete(AGENT_ID)

    rebuilt = store.create(NAME, role="testing", system_prompt="Second.")

    assert rebuilt.id == AGENT_ID          # the collision is real
    assert pf.read(AGENT_ID, pf.MEMORY) is None
    assert rebuilt.system_prompt == "Second."
