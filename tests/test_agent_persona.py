"""Choosing how an agent works, instead of writing a system prompt.

The picker is the easy half. What is worth testing is the seam underneath it:
the choices are the source and `persona.md` is what they produce, so every way
that could silently destroy what somebody had — an empty selection rendering an
empty document over a shipped preset, a re-render eating hand-written prose, a
level that claims more than the gate will give — is a case below.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import persona, presets
from chitragupta.agents import profile_files as pf

AGENT = "inbox"


@pytest.fixture(autouse=True)
def _clean():
    persona.forget(AGENT)
    pf.forget(AGENT)
    from chitragupta.agents.tool_overrides import get_tool_overrides
    get_tool_overrides().clear(AGENT)
    yield
    persona.forget(AGENT)
    pf.forget(AGENT)
    get_tool_overrides().clear(AGENT)


# ── the vocabulary ─────────────────────────────────────────────────────────

def test_the_screen_is_sent_the_vocabulary_rather_than_keeping_its_own():
    """A second copy of these lists in JavaScript is a second copy to keep
    current, and the one that drifts is the one somebody is choosing from."""
    vocab = persona.vocabulary()
    assert "Supportive" in vocab["traits"]
    assert "Concise" in vocab["communication"]
    assert "First principles" in vocab["thinking"]
    assert [lvl["key"] for lvl in vocab["autonomy"]] == [
        "read_only", "ask_first", "on_its_own"]
    assert vocab["default_autonomy"] == "ask_first"


def test_every_option_has_a_label_and_a_sentence_fragment():
    """They are not the same string. Reusing the label produced "communicate in
    a way that is concise and bullet points", which is what the model would
    then have been asked to act on."""
    for group in (persona.TRAITS, persona.COMMUNICATION, persona.THINKING):
        for label, fragment in group:
            assert label and fragment
            assert label != fragment or label.islower()


# ── what gets written ──────────────────────────────────────────────────────

def test_choices_become_the_agents_instructions():
    persona.set_persona(AGENT, traits=["Supportive", "Warm"],
                        communication=["Concise"], thinking=["First principles"])
    text = pf.read(AGENT, pf.PERSONA)
    assert "Be supportive and warm." in text
    assert "keep answers short" in text
    assert "reason from first principles" in text


def test_the_instructions_reach_the_agent():
    persona.set_persona(AGENT, traits=["Witty"])
    assert "witty" in presets.get_agent(AGENT).system_message()


def test_choosing_nothing_leaves_the_shipped_instructions_alone():
    """The failure this exists to stop: a persona row holding nothing but the
    default autonomy would render a document and replace a preset's own prompt
    with three lines about asking first."""
    shipped = presets.PRESETS[AGENT].system_prompt
    persona.set_persona(AGENT, traits=[], communication=[], thinking=[],
                        autonomy="ask_first", extra="")
    assert pf.read(AGENT, pf.PERSONA) is None
    assert presets.get_agent(AGENT).system_prompt == shipped


def test_clearing_every_choice_hands_the_agent_back_its_shipped_prompt():
    shipped = presets.PRESETS[AGENT].system_prompt
    persona.set_persona(AGENT, traits=["Witty"])
    assert pf.read(AGENT, pf.PERSONA) is not None
    persona.set_persona(AGENT, traits=[])
    assert presets.get_agent(AGENT).system_prompt == shipped


def test_free_text_survives_every_re_render():
    """The one place hand-written prose is safe, because the file itself is
    rewritten from the choices on every save."""
    persona.set_persona(AGENT, extra="Always sign off as Suryansh.")
    persona.set_persona(AGENT, traits=["Calm"])
    text = pf.read(AGENT, pf.PERSONA)
    assert "Always sign off as Suryansh." in text
    assert "calm" in text


def test_the_users_own_words_come_last():
    """Anything they wrote outranks a sentence generated from a chip."""
    persona.set_persona(AGENT, traits=["Calm"], extra="Never use bullet points.")
    text = pf.read(AGENT, pf.PERSONA)
    assert text.index("Never use bullet points.") > text.index("calm")


def test_one_field_at_a_time_leaves_the_others_alone():
    """The screen saves a field at a time, so an absent one has to mean
    "unchanged" rather than "cleared"."""
    persona.set_persona(AGENT, traits=["Calm"], communication=["Concise"])
    persona.set_persona(AGENT, extra="One more thing.")
    stored = persona.get(AGENT)
    assert stored["traits"] == ["Calm"]
    assert stored["communication"] == ["Concise"]


def test_the_order_chips_were_pressed_in_does_not_change_the_document():
    """Otherwise a save that chose the same things rewrites the file and the
    agent's cached prefix for nothing."""
    persona.set_persona(AGENT, traits=["Warm", "Supportive"])
    first = pf.read(AGENT, pf.PERSONA)
    persona.set_persona(AGENT, traits=["Supportive", "Warm"])
    assert pf.read(AGENT, pf.PERSONA) == first


# ── what it refuses ────────────────────────────────────────────────────────

def test_a_value_outside_the_vocabulary_is_refused_not_dropped():
    """Dropping it would make the screen show a choice that was never stored."""
    with pytest.raises(persona.PersonaRejectedError):
        persona.set_persona(AGENT, traits=["Sarcastic"])


def test_more_than_the_cap_is_refused_with_a_sentence():
    too_many = [label for label, _ in persona.TRAITS][:persona.MAX_TRAITS + 1]
    with pytest.raises(persona.PersonaRejectedError) as exc:
        persona.set_persona(AGENT, traits=too_many)
    assert "at most" in str(exc.value)


def test_an_unknown_autonomy_level_is_refused():
    with pytest.raises(persona.PersonaRejectedError):
        persona.set_persona(AGENT, autonomy="do_whatever")


# ── autonomy is the permission system, not a second one ────────────────────

def test_read_only_actually_takes_the_changing_tools_away():
    """A level that only changed the prompt would be a control that does not
    work — the agent would still hold every tool it had."""
    from chitragupta.agents.tool_facts import tool_access

    before = presets.get_agent(AGENT).tools
    assert any(str(tool_access(t)) != "read" for t in before), "fixture is wrong"

    persona.set_persona(AGENT, autonomy="read_only")

    after = presets.get_agent(AGENT).tools
    assert after, "it was left with nothing at all"
    assert all(str(tool_access(t)) == "read" for t in after)


def test_leaving_read_only_puts_back_what_was_there():
    """The difference between a switch and a trapdoor."""
    before = list(presets.get_agent(AGENT).tools)
    persona.set_persona(AGENT, autonomy="read_only")
    persona.set_persona(AGENT, autonomy="ask_first")
    assert presets.get_agent(AGENT).tools == before


def test_read_only_twice_does_not_lose_the_original_list():
    """The second save must not stash the already-stripped list as the one to
    restore — that is how the tools would never come back."""
    before = list(presets.get_agent(AGENT).tools)
    persona.set_persona(AGENT, autonomy="read_only")
    persona.set_persona(AGENT, autonomy="read_only", traits=["Calm"])
    persona.set_persona(AGENT, autonomy="ask_first")
    assert presets.get_agent(AGENT).tools == before


def test_acting_on_its_own_does_not_touch_the_tool_list():
    """What an agent MAY use is the Permissions tab's question. This one is how
    much it decides for itself with what it already has."""
    before = list(presets.get_agent(AGENT).tools)
    persona.set_persona(AGENT, autonomy="on_its_own")
    assert presets.get_agent(AGENT).tools == before


def test_the_most_autonomous_level_still_says_what_always_asks():
    """A level that implied otherwise would promise something the gate refuses
    — and the agent would be the one telling the user so."""
    prompt = next(x for x in persona.AUTONOMY if x["key"] == "on_its_own")["prompt"]
    assert "irreversible" in prompt
    assert "spends money" in prompt
    assert "reaches a person other than the user" in prompt


def test_deleting_an_agent_forgets_its_persona():
    """Ids are slugs, so the next agent built with that name would otherwise be
    born wearing a deleted agent's character."""
    persona.set_persona(AGENT, traits=["Playful"])
    persona.forget(AGENT)
    assert persona.get(AGENT) is None
