"""`persona.md` replaces an agent's instructions; `memory.md` rides beside them.

These are the two seams that make the files real rather than decorative:

* `presets._with_user_edits` is the one place "the shipped agent plus what the
  user changed" is assembled, so the persona override belongs there and
  nowhere else — an override applied in `get_agent` but not `list_agents` is an
  agent whose instructions depend on which call site asked.
* `prompt.build` is where the notes reach the model, inside the system message,
  which is the cached prefix. Anywhere else and they would be re-sent and
  re-billed on every round of every turn.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import presets
from chitragupta.agents import profile_files as pf

PRESET = "inbox"


@pytest.fixture(autouse=True)
def _clean():
    pf.forget(PRESET)
    yield
    pf.forget(PRESET)


# ── persona.md ──────────────────────────────────────────────────────────────

def test_a_preset_with_no_persona_file_keeps_its_shipped_instructions():
    shipped = presets.PRESETS[PRESET].system_prompt
    assert presets.get_agent(PRESET).system_prompt == shipped
    assert shipped.strip()


def test_a_persona_file_replaces_the_shipped_instructions():
    pf.write(PRESET, pf.PERSONA, "Answer only in haiku.")
    assert presets.get_agent(PRESET).system_prompt == "Answer only in haiku."


def test_deleting_the_persona_file_restores_the_shipped_instructions():
    """Reset-to-default is deleting a file, which is why the preset stays a
    code constant: nothing has to remember what the default used to be."""
    shipped = presets.PRESETS[PRESET].system_prompt
    pf.write(PRESET, pf.PERSONA, "Answer only in haiku.")
    pf.clear(PRESET, pf.PERSONA)
    assert presets.get_agent(PRESET).system_prompt == shipped


def test_an_emptied_persona_is_honoured_and_does_not_restore_the_preset():
    """An empty file is a deliberate act — "this agent gets no instructions of
    its own" — and must not be read as "no file"."""
    pf.write(PRESET, pf.PERSONA, "")
    assert presets.get_agent(PRESET).system_prompt == ""


def test_the_override_applies_through_list_agents_too():
    """Both readers come through one seam, or an agent's instructions depend on
    which call site asked for it — "works in the sidebar but not in the turn"."""
    from chitragupta.agents import library

    was = list(library.roster())
    library.add_to_roster(PRESET)
    try:
        pf.write(PRESET, pf.PERSONA, "Answer only in haiku.")
        listed = {a.id: a for a in presets.list_agents()}
        assert PRESET in listed
        assert listed[PRESET].system_prompt == "Answer only in haiku."
    finally:
        if PRESET not in was:
            library.remove_from_roster(PRESET)


def test_the_persona_reaches_the_system_message():
    pf.write(PRESET, pf.PERSONA, "Answer only in haiku.")
    assert "Answer only in haiku." in presets.get_agent(PRESET).system_message()


# ── memory.md ───────────────────────────────────────────────────────────────

def test_notes_reach_the_system_message():
    pf.write(PRESET, pf.MEMORY,
             "## How you like this done\n- Replies sign off as 'Suryansh'.\n")
    msg = presets.get_agent(PRESET).system_message()
    assert "Replies sign off as 'Suryansh'." in msg


def test_an_agent_with_no_notes_is_told_nothing_about_notes():
    """An empty heading invites invention: an agent shown "your notes:" with
    nothing under it will fill the gap. The block is absent, not blank."""
    assert "in your profile" not in presets.get_agent(PRESET).system_message()


def test_an_empty_notes_file_adds_no_block_either():
    pf.write(PRESET, pf.MEMORY, "   \n\n")
    assert "in your profile" not in presets.get_agent(PRESET).system_message()


def test_the_notes_say_they_are_not_facts_about_the_user():
    """The brain holds facts about the user's life and recall is how an agent
    reaches them. Notes that read as user facts are a second brain nobody
    searches — and the one place this distinction can be made is here."""
    pf.write(PRESET, pf.MEMORY, "- Replies are short.")
    msg = presets.get_agent(PRESET).system_message()
    assert "brain" in msg.lower()
    assert "profile" in msg.lower()


def test_what_the_user_says_now_outranks_a_written_note():
    """A stale standing instruction must never beat a live one. Without this
    sentence the model has two instructions and no rule for choosing."""
    pf.write(PRESET, pf.MEMORY, "- Replies are short.")
    msg = presets.get_agent(PRESET).system_message()
    assert "unless the user says otherwise" in msg.lower()


def test_a_truncated_notes_file_says_so_rather_than_pretending():
    path = pf.agent_dir(PRESET) / pf.MEMORY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- a standing instruction, at length\n" * 300,
                    encoding="utf-8")
    msg = presets.get_agent(PRESET).system_message()
    assert "only the first part" in msg.lower()


def test_the_notes_block_is_bounded_by_the_file_limit():
    """The system message is the cached prefix. A note file nobody capped is a
    turn that costs more every time it runs."""
    path = pf.agent_dir(PRESET) / pf.MEMORY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- a standing instruction, at length\n" * 2000,
                    encoding="utf-8")
    clean = len(presets.PRESETS[PRESET].system_message().encode("utf-8"))
    grown = len(presets.get_agent(PRESET).system_message().encode("utf-8"))
    assert grown - clean < pf.LIMITS[pf.MEMORY] + 1024
