"""The post-turn learner routes three ways: the brain, this agent, or nowhere.

The mechanism already existed and already decided brain-or-nothing. What is
new is the third answer and the gate that reaches it — and the gate is the part
worth testing hardest, because what it skips is never seen again by anything.
"""
from __future__ import annotations

import json

import pytest

from chitragupta.agents import notes, runtime
from chitragupta.agents import profile_files as pf

AGENT = "learner-test-agent"


@pytest.fixture(autouse=True)
def _clean():
    pf.forget(AGENT)
    notes.forget(AGENT)
    yield
    pf.forget(AGENT)
    notes.forget(AGENT)


class FakeProvider:
    """A provider that answers the extraction call with whatever we hand it."""

    name = "fake"
    model = "fake-1"

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def is_ready(self):
        return True, ""

    def chat(self, messages, **kw):
        self.calls.append(messages)

        class R:
            text = json.dumps(self.payload)
        return R()


def memory():
    return pf.read(AGENT, pf.MEMORY) or ""


# ── the gate ────────────────────────────────────────────────────────────────

#: Phrasings that contain no self-disclosure at all — no "I", no "my". These
#: are the ones the old gate dropped silently, and the only ones that prove the
#: hole is closed: "don't sign off with *my* full name" matches the old cue on
#: the word "my" and would pass this test without any change being made.
NO_DISCLOSURE = [
    "never reply to recruiters",
    "from now on keep the summaries shorter",
    "stop using the full name on replies",
    "always send it on a Friday instead",
]


@pytest.mark.parametrize("said", NO_DISCLOSURE)
def test_an_instruction_with_no_self_disclosure_now_reaches_the_model(said):
    """The hole this landing exists to close. The old gate was tuned for
    somebody talking about themselves, so an instruction phrased as an
    instruction matched nothing, **no model call was made at all**, and what
    the user said was dropped on the floor rather than filed anywhere."""
    assert not runtime._DISCLOSURE.search(said), "this phrasing must not disclose"
    p = FakeProvider({"facts": [], "notes": [
        {"heading": "How you like this done",
         "note": "Replies sign off as 'Suryansh', not the full name"}]})

    runtime._auto_learn(AGENT, said, p)

    assert p.calls, "the extraction call was never made"
    assert "Suryansh" in memory()


def test_small_talk_still_costs_nothing():
    """The gate is the only thing between this and a model call every turn."""
    p = FakeProvider({"facts": [], "notes": []})
    runtime._auto_learn(AGENT, "thanks, that's great", p)
    assert p.calls == []


def test_the_agent_is_told_which_notes_it_already_has():
    """Superseding is impossible without ids, and two contradicting standing
    instructions is the failure that follows."""
    notes.record(AGENT, [{"note": "Replies sign off as 'Suryansh Singh'",
                          "heading": "How you like this done"}])
    p = FakeProvider({"facts": [], "notes": []})

    runtime._auto_learn(AGENT, "from now on keep it shorter", p)

    sent = p.calls[0][-1].content
    assert "NOTES THIS AGENT ALREADY KEEPS" in sent
    assert "n1 [How you like this done] Replies sign off as 'Suryansh Singh'" in sent


# ── the three destinations ──────────────────────────────────────────────────

def test_a_life_fact_goes_to_the_brain_and_not_into_the_notes():
    """The brain is shared, versioned and superseded; notes are none of those.
    A life fact filed as a note is invisible to every other agent."""
    p = FakeProvider({"facts": ["The user's favourite fruit is apple"],
                      "notes": []})

    runtime._auto_learn(AGENT, "my favourite fruit is apple", p)

    assert "apple" not in memory()


def test_a_working_instruction_goes_to_the_notes():
    p = FakeProvider({"facts": [], "notes": [
        {"heading": "Standing instructions",
         "note": "The weekly summary goes out on Friday afternoon"}]})

    runtime._auto_learn(AGENT, "from now on send the summary on Friday", p)

    assert "Friday afternoon" in memory()
    assert "## Standing instructions" in memory()


def test_both_can_come_out_of_one_call():
    """One call, not two — the whole reason this rides on the existing
    extraction rather than adding a pass of its own."""
    p = FakeProvider({
        "facts": ["The user works at Acme"],
        "notes": [{"heading": "How you like this done",
                   "note": "Replies stay under five sentences"}]})

    runtime._auto_learn(AGENT, "i work at Acme, and always keep replies short", p)

    assert len(p.calls) == 1
    assert "five sentences" in memory()


def test_nothing_is_filed_when_the_model_says_nothing_qualifies():
    p = FakeProvider({"facts": [], "notes": []})
    runtime._auto_learn(AGENT, "always nice talking to you", p)
    assert pf.read(AGENT, pf.MEMORY) is None


# ── the filters hold even when the model ignores them ───────────────────────

def test_a_measurement_the_model_proposed_as_a_note_is_still_refused():
    """A prompt is not a guarantee, and *a number over time is not a memory* —
    that is `metrics.py`'s job, where it is a series with a unit."""
    p = FakeProvider({"facts": [], "notes": [
        {"heading": "How you like this done",
         "note": "They weighed 72 kg on Tuesday morning"}]})

    runtime._auto_learn(AGENT, "always log my weight, i weighed 72 kg", p)

    assert "72" not in memory()


def test_a_credential_the_model_proposed_as_a_note_is_still_refused():
    p = FakeProvider({"facts": [], "notes": [
        {"heading": "Standing instructions",
         "note": "Their api_key is sk-abcdefghijklmnopqrstuvwxyz"}]})

    runtime._auto_learn(AGENT, "always use my key sk-abcdefghijklmnopqrstuvwxyz", p)

    assert "sk-" not in memory()


# ── no model, no notes ──────────────────────────────────────────────────────

class NotReady(FakeProvider):
    def is_ready(self):
        return False, "no key"


def test_without_a_model_nothing_is_guessed_into_the_notes():
    """A fact guessed from a regex is a sentence the user actually wrote, and
    the brain can supersede it. A standing instruction guessed from a regex is
    a rule this agent follows every turn, derived from nothing."""
    runtime._auto_learn(AGENT, "i always want short replies, never long ones",
                        NotReady({"facts": [], "notes": []}))
    assert pf.read(AGENT, pf.MEMORY) is None


def test_a_broken_extraction_does_not_break_the_turn():
    class Broken(FakeProvider):
        def chat(self, messages, **kw):
            raise RuntimeError("provider exploded")

    runtime._auto_learn(AGENT, "from now on keep it short",
                        Broken({"facts": [], "notes": []}))
    assert pf.read(AGENT, pf.MEMORY) is None


def test_a_sentence_that_is_only_an_instruction_is_not_stored_as_a_user_fact():
    """The fallback exists for facts, and only `_DISCLOSURE` sentences are
    facts. An instruction stored as a fact about the user is exactly the
    misfiling the split exists to prevent."""
    class NoJson(FakeProvider):
        def chat(self, messages, **kw):
            class R:
                text = "not json at all"
            return R()

    stored = runtime._auto_learn(AGENT, "never reply to recruiters",
                                 NoJson({"facts": [], "notes": []}))
    assert stored == 0
