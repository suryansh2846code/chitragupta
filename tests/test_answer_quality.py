"""The answer-quality harness — tested on the harness, not on a model.

Two different questions live here and conflating them is how a quality suite
becomes decoration:

1. **Is the grading correct?** Deterministic, no model, runs on every commit.
   That is almost everything in this file.
2. **Are the answers good?** Needs a real model and the user's own money, so it
   runs against whatever is connected and reports honestly when nothing is.
   `tests/` cannot answer it and does not pretend to.

The behaviour that matters most is the one at the bottom: a report that could
not measure anything must never be readable as a pass. "0/0" and "2/5 against a
mock" are both worse than "not measured", because only the last one is true.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import quality
from chitragupta.agents.quality import Case, QualityReport


def _case(**kw):
    base = {"key": "k", "seed": (), "question": "q"}
    return Case(**{**base, **kw})


# ── the grading ────────────────────────────────────────────────────────────

def test_an_answer_with_the_fact_passes():
    ok, detail = quality._grade(_case(must=("Lisbon",)), "She lives in Lisbon.")
    assert ok and detail == ""


def test_a_missing_fact_fails_and_says_which():
    ok, detail = quality._grade(_case(must=("Lisbon",)), "I'm not sure.")
    assert not ok and "Lisbon" in detail


def test_matching_ignores_case_because_wording_is_not_under_test():
    ok, _ = quality._grade(_case(must=("lisbon",)), "She lives in LISBON.")
    assert ok


def test_alternatives_at_one_position_all_count():
    """The model's phrasing is not the thing being measured. "I don't know"
    and "I do not have that" are the same answer."""
    case = _case(must=(("don't", "do not", "no record"),))
    for phrasing in ("I don't have that.", "I do not have that.",
                     "There is no record of it."):
        ok, _ = quality._grade(case, phrasing)
        assert ok, phrasing


def test_a_forbidden_phrase_fails_however_well_it_reads():
    """The half that catches a confident invention. A suite with only `must`
    rewards an answer that says everything."""
    ok, detail = quality._grade(
        _case(must=("Meera",), must_not=("+44",)),
        "Meera's number is +44 7700 900123.")
    assert not ok and "+44" in detail


def test_must_not_is_checked_even_when_must_is_satisfied():
    """Getting the right fact does not excuse inventing a second one."""
    ok, _ = quality._grade(
        _case(must=("Aperture",), must_not=("Northwind",)),
        "You work at Aperture, having left Northwind.")
    assert not ok


# ── the golden set is actually a set of traps ──────────────────────────────

def test_every_case_can_fail_in_both_directions():
    """A case with no `must_not` and no rubric only checks that the model said
    something. Each of these is a named failure from this repo, and a named
    failure has a wrong answer as well as a right one."""
    thin = [c.key for c in quality.CASES if not c.must_not and not c.rubric]
    assert not thin, f"cases that cannot catch an invention: {thin}"


def test_every_case_seeds_its_own_brain_or_needs_none():
    """A case that reads the user's real brain cannot tell an invention from a
    recollection, because we do not know what was in there."""
    for case in quality.CASES:
        assert isinstance(case.seed, tuple)


def test_the_case_keys_are_unique():
    keys = [c.key for c in quality.CASES]
    assert len(keys) == len(set(keys))


# ── never claim a pass you did not measure ─────────────────────────────────

def test_nothing_connected_reports_not_measured_rather_than_zero():
    report = QualityReport(skipped="No model is connected.")
    assert report.ran == 0 and report.passed == 0
    assert "not measured" in quality.summary(report)


def test_a_mock_provider_is_refused(monkeypatch):
    """Grading the mock produced "2/5". That is a number about a fixture that
    reads as a number about the app — worse than a skip, because unlike a skip
    it looks like it was measured."""
    class Mock:
        name = "mock"
        model = "mock-1"

        def is_ready(self):
            return True, ""

    report = quality.run(provider=Mock())
    assert report.ran == 0
    assert "mock" in report.skipped.lower()
    assert "not measured" in quality.summary(report)


def test_an_unready_provider_says_why_in_the_users_terms():
    class NotReady:
        name = "anthropic"
        model = "claude-sonnet-5"

        def is_ready(self):
            return False, "Set ANTHROPIC_API_KEY or connect a Claude account"

    report = quality.run(provider=NotReady())
    assert report.ran == 0
    assert "ANTHROPIC_API_KEY" in report.skipped


def test_the_report_separates_ran_from_passed():
    """`passed == ran == 0` is the shape a silent failure takes. Anything
    reading this has to be able to tell it from a clean sweep."""
    payload = QualityReport(skipped="nothing connected").as_dict()
    assert payload["ran"] == 0
    assert payload["skipped"]


# ── it runs end to end against an injected model ───────────────────────────

class _Fixed:
    """A provider with one answer, so the pipeline can be exercised without a
    real model. This proves the HARNESS works. It says nothing at all about
    answer quality, which is the whole point of keeping the two separate."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, answer: str):
        self.answer = answer

    def is_ready(self):
        return True, ""

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=4000):
        from chitragupta.models.base import ChatResult
        return ChatResult(text=self.answer)

    def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=4000):
        from chitragupta.models.streaming import from_result
        yield from from_result(self.chat(messages, tools=tools))


ONE_CASE = (Case(key="seeded_fact", seed=("The user's sister lives in Lisbon.",),
                 question="Where does my sister live?",
                 must=("Lisbon",), must_not=("Madrid",)),)


def test_a_right_answer_scores():
    report = quality.run(provider=_Fixed("Your sister lives in Lisbon."),
                         cases=ONE_CASE)
    assert report.ran == 1 and report.passed == 1


def test_a_wrong_answer_does_not():
    report = quality.run(provider=_Fixed("Your sister lives in Madrid."),
                         cases=ONE_CASE)
    assert report.ran == 1 and report.passed == 0
    assert report.results[0].answer


class _Echo:
    """Answers with whatever the brain put in front of it.

    A provider with a fixed answer cannot test brain isolation at all — it
    returns the same string whatever was recalled, so the case passes with the
    leak present. This one makes the recalled context observable, which is the
    only way the assertion means anything.
    """

    name = "scripted"
    model = "scripted-1"

    def is_ready(self):
        return True, ""

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=4000):
        from chitragupta.models.base import ChatResult
        return ChatResult(text=" ".join(
            m.content for m in messages if m.role == "system"))

    def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=4000):
        from chitragupta.models.streaming import from_result
        yield from from_result(self.chat(messages, tools=tools))


def test_each_case_gets_a_brain_of_its_own():
    """Cases that shared one would grade each other — the fact seeded for case
    two is already there when case one is asked, and "did not invent it" stops
    meaning anything the moment the brain holds something we did not write."""
    two = (
        Case(key="a", seed=("The user's dog is called Rufus.",),
             question="What is my dog called?", must=("Rufus",)),
        Case(key="b", seed=("The user's cat is called Milo.",),
             question="What is my cat called?",
             must=("Milo",), must_not=("Rufus",)),
    )
    report = quality.run(provider=_Echo(), cases=two)
    assert report.ran == 2
    assert report.results[0].passed, report.results[0].detail
    assert report.results[1].passed, (
        "case b saw case a's brain — the stores are shared")


def test_the_shared_brain_is_put_back_afterwards():
    """A suite that leaves the runtime pointing at a temporary store poisons
    every turn after it."""
    from chitragupta.agents import runtime
    before = runtime.get_brain
    quality.run(provider=_Fixed("anything"), cases=ONE_CASE)
    assert runtime.get_brain is before


def test_the_provider_is_put_back_afterwards():
    from chitragupta.agents import runtime
    before = runtime.get_provider
    quality.run(provider=_Fixed("anything"), cases=ONE_CASE)
    assert runtime.get_provider is before


# ── it is reachable ────────────────────────────────────────────────────────

def test_the_endpoint_is_pinned():
    import json
    from pathlib import Path
    surface = json.loads(
        (Path(__file__).parent / "api_surface.json").read_text())
    assert "GET /api/agents/quality" in surface


@pytest.mark.parametrize("name", ["run", "summary", "CASES", "Case"])
def test_the_module_exposes_what_the_route_and_docs_name(name):
    assert hasattr(quality, name)
