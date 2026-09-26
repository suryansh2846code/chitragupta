"""The scorecard, and the thing it must never do: grade prose.

An automation is judged on whether the intended external state was achieved and
verified. These check that the *grader* holds that line — a scorecard that can
be satisfied by a plausible answer is worse than none, because it reports a
number nobody should trust.
"""
from __future__ import annotations

from chitragupta.automation import evaluation


def test_every_case_passes_on_this_build():
    report = evaluation.run()
    failures = [(s.key, s.detail) for s in report.scores if not s.passed]
    assert not failures, failures
    assert report.total >= 10


def test_the_adversarial_cases_are_scored_the_same_way():
    """Not a bonus section. A suite that treats safety as extra credit will
    eventually trade it for a higher score."""
    report = evaluation.run()
    assert report.adversarial_total >= 4
    assert report.adversarial_passed == report.adversarial_total


def test_it_counts_what_reached_the_world_not_what_was_said():
    """The case that makes the whole suite meaningful: the agent *claims* it
    sent the thing, verification says otherwise, and the score is a failure."""
    case = next(c for c in evaluation.CASES
                if c.key == "hallucinated_success_is_caught")
    assert "Done!" in case.reply
    score = next(s for s in evaluation.run((case,)).scores)
    assert score.passed, "the grader accepted a claim it should have checked"
    assert score.key == "hallucinated_success_is_caught"


def test_a_case_that_does_a_forbidden_thing_fails():
    """The grader's own fail-first: bend a case so the right answer is wrong,
    and the score must notice."""
    good = next(c for c in evaluation.CASES if c.key == "acts_when_it_should")
    bent = evaluation.Case(**{**good.__dict__,
                             "forbid_effects": ("create_task",)})
    score = evaluation.run((bent,)).scores[0]
    assert not score.passed
    assert "must not" in score.detail


def test_a_case_that_never_acts_fails_when_it_should_have():
    good = next(c for c in evaluation.CASES if c.key == "no_action_is_still_success")
    bent = evaluation.Case(**{**good.__dict__,
                             "expect_effects": ("create_task",)})
    score = evaluation.run((bent,)).scores[0]
    assert not score.passed
    assert "never did create_task" in score.detail


def test_cost_is_reported_per_case():
    """An automation that is correct and spends nine model calls is a finding,
    not a pass. A suite that only says pass/fail cannot surface that."""
    report = evaluation.run()
    assert all(s.model_calls >= 0 for s in report.scores)
    assert any(s.model_calls for s in report.scores)
    assert all(s.seconds >= 0 for s in report.scores)


def test_the_summary_names_the_adversarial_score_separately():
    """Averaging safety into one number lets it be traded away invisibly."""
    line = evaluation.summary(evaluation.run())
    assert "adversarial" in line and "duplicates" in line


def test_each_case_gets_its_own_database():
    """Cases sharing one would grade each other through the idempotency
    ledger — which is exactly what that ledger is for."""
    first = evaluation.run()
    second = evaluation.run()
    assert first.passed == second.passed
    assert sum(s.duplicate_actions for s in second.scores) == 0
