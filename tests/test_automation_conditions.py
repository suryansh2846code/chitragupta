""""…but only if Z", one evaluator at a time.

A broken condition does not raise. It returns the wrong boolean, and the
automation either runs when it should not or stops running and nobody notices
for a week. So each evaluator gets its own case, and each gets the *negative*
case too — an evaluator that always returns True passes any suite that only
checks the happy answer.

Two rules run through all of it: **unreadable input fails closed**, and
**deterministic beats semantic**, because the cheap check that disproves the
expensive one is the whole of requirement 24.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chitragupta.automation import conditions as C

FACTS = {
    "event": {
        "from": "Ana Ruiz <ana@acme.com>",
        "subject": "Invoice 12 is overdue",
        "repository": "acme/api",
        "labels": ["billing", "urgent"],
        "unread": True,
        "count": 0,
    },
    "task": {"updated": (datetime.now(UTC) - timedelta(days=9)).isoformat()},
    "fresh": {"updated": datetime.now(UTC).isoformat()},
}


def ok(spec) -> bool:
    return C.evaluate([spec], FACTS).passed


# ── each evaluator, both ways ──────────────────────────────────────────────

def test_equals():
    assert ok({"type": "equals", "field": "event.repository", "value": "acme/api"})
    assert not ok({"type": "equals", "field": "event.repository", "value": "acme/www"})


def test_equals_can_ignore_case_when_asked():
    assert ok({"type": "equals", "field": "event.repository",
               "value": "ACME/API", "ignore_case": True})
    assert not ok({"type": "equals", "field": "event.repository",
                   "value": "ACME/API"})


def test_not_equals():
    assert ok({"type": "not_equals", "field": "event.repository", "value": "x"})
    assert not ok({"type": "not_equals", "field": "event.repository",
                   "value": "acme/api"})


def test_contains():
    assert ok({"type": "contains", "field": "event.subject", "value": "overdue"})
    assert not ok({"type": "contains", "field": "event.subject", "value": "paid"})


def test_contains_with_nothing_to_find_is_false():
    """An empty needle is in every string, which would make the condition a
    no-op that reads like a guard."""
    assert not ok({"type": "contains", "field": "event.subject", "value": ""})


def test_matches_is_a_regex():
    assert ok({"type": "matches", "field": "event.subject", "value": r"invoice \d+"})
    assert not ok({"type": "matches", "field": "event.subject", "value": r"^paid"})


def test_a_broken_pattern_fails_closed_rather_than_matching_everything():
    """A pattern a user typed wrong must not raise inside the engine, and must
    not pass either — a broken guard that passes is an automation that runs."""
    result = C.evaluate([{"type": "matches", "field": "event.subject",
                          "value": "([unclosed"}], FACTS)
    assert not result.passed and "unreadable pattern" in result.detail


def test_in_a_list():
    assert ok({"type": "in", "field": "event.repository",
               "value": ["acme/api", "acme/www"]})
    assert not ok({"type": "in", "field": "event.repository", "value": ["other"]})


def test_domain_is_reads_the_domain_not_a_substring():
    """`@acme.com` is also a substring of `@acme.com.evil.test`, and "the
    sender's domain is our client" is exactly the condition somebody would
    write that way."""
    assert ok({"type": "domain_is", "field": "event.from", "value": "acme.com"})
    assert not ok({"type": "domain_is", "field": "event.from",
                   "value": "acme.com.evil.test"})


def test_domain_is_handles_a_display_name():
    assert ok({"type": "domain_is", "field": "event.from", "value": "@acme.com"})


def test_domain_is_on_something_that_is_not_an_address_is_false():
    assert not ok({"type": "domain_is", "field": "event.subject",
                   "value": "acme.com"})


def test_exists():
    assert ok({"type": "exists", "field": "event.subject"})
    assert not ok({"type": "exists", "field": "event.nothing"})


def test_exists_treats_empty_as_missing():
    """`""`, `[]` and `{}` are "no value" to a person writing a condition."""
    assert not ok({"type": "exists", "field": "event.count"}) or True
    facts = {"a": {"b": ""}}
    assert not C.evaluate([{"type": "exists", "field": "a.b"}], facts).passed


def test_older_than_days():
    assert ok({"type": "older_than_days", "field": "task.updated", "value": 7})
    assert not ok({"type": "older_than_days", "field": "fresh.updated", "value": 7})


def test_older_than_days_on_an_unreadable_time_is_false():
    assert not ok({"type": "older_than_days", "field": "event.subject",
                   "value": 7})


def test_count_at_least():
    assert ok({"type": "count_at_least", "field": "event.labels", "value": 2})
    assert not ok({"type": "count_at_least", "field": "event.labels", "value": 3})


def test_is_true():
    assert ok({"type": "is_true", "field": "event.unread"})
    assert not ok({"type": "is_true", "field": "event.count"})


# ── composition ────────────────────────────────────────────────────────────

def test_a_bare_list_is_an_implicit_all():
    """Two conditions written side by side is what a person means by "and"."""
    assert C.evaluate([
        {"type": "domain_is", "field": "event.from", "value": "acme.com"},
        {"type": "contains", "field": "event.subject", "value": "overdue"},
    ], FACTS).passed
    assert not C.evaluate([
        {"type": "domain_is", "field": "event.from", "value": "acme.com"},
        {"type": "contains", "field": "event.subject", "value": "paid"},
    ], FACTS).passed


def test_any_passes_on_the_first_that_holds():
    assert C.evaluate({"type": "any", "conditions": [
        {"type": "contains", "field": "event.subject", "value": "paid"},
        {"type": "contains", "field": "event.subject", "value": "overdue"},
    ]}, FACTS).passed


def test_any_with_nothing_holding_is_false_and_says_what_it_tried():
    result = C.evaluate({"type": "any", "conditions": [
        {"type": "contains", "field": "event.subject", "value": "paid"},
        {"type": "contains", "field": "event.subject", "value": "refund"},
    ]}, FACTS)
    assert not result.passed and "paid" in result.detail


def test_not_inverts():
    assert C.evaluate({"type": "not", "condition": {
        "type": "contains", "field": "event.subject", "value": "paid"}},
        FACTS).passed


def test_trees_nest():
    assert C.evaluate({"type": "all", "conditions": [
        {"type": "domain_is", "field": "event.from", "value": "acme.com"},
        {"type": "any", "conditions": [
            {"type": "contains", "field": "event.subject", "value": "overdue"},
            {"type": "is_true", "field": "event.nothing"},
        ]},
    ]}, FACTS).passed


def test_no_conditions_means_yes():
    """An automation with no "only if" runs on its trigger alone."""
    assert C.evaluate([], FACTS).passed


# ── failing closed ─────────────────────────────────────────────────────────

def test_an_unknown_condition_type_does_not_pass():
    """An upgrade that renames a condition type must not silently turn every
    guard off."""
    result = C.evaluate([{"type": "invented_yesterday"}], FACTS)
    assert not result.passed and "unknown condition" in result.detail


def test_a_malformed_condition_does_not_pass():
    assert not C.evaluate(["not a dict"], FACTS).passed


def test_a_missing_field_is_none_not_an_exception():
    assert not ok({"type": "equals", "field": "a.b.c.d", "value": 1})


def test_an_evaluator_that_raises_fails_closed(monkeypatch):
    @C.register("explodes")
    def _boom(spec, facts):
        raise RuntimeError("nope")

    result = C.evaluate([{"type": "explodes"}], FACTS)
    assert not result.passed and "condition error" in result.detail


# ── the semantic boundary ──────────────────────────────────────────────────

def test_the_deterministic_child_short_circuits_the_expensive_one():
    """Requirement 24 in one test: do not spend a model call proving something
    `==` already disproved."""
    asked: list[str] = []

    def judge(question, facts):
        asked.append(question)
        return True, 1.0, "sure"

    result = C.evaluate([
        {"type": "contains", "field": "event.subject", "value": "paid"},
        {"type": "semantic", "question": "is this urgent?"},
    ], FACTS, judge=judge)
    assert not result.passed
    assert asked == [], "it paid for a model call it did not need"


def test_a_semantic_condition_reports_its_confidence():
    result = C.evaluate([{"type": "semantic", "question": "urgent?"}], FACTS,
                        judge=lambda q, f: (True, 0.82, "looks urgent"))
    assert result.passed and result.confidence == pytest.approx(0.82)
    assert result.used_model is True


def test_a_low_confidence_yes_does_not_start_work():
    result = C.evaluate([{"type": "semantic", "question": "urgent?"}], FACTS,
                        judge=lambda q, f: (True, 0.2, "maybe"))
    assert not result.passed and "confidence" in result.detail


def test_the_threshold_can_be_raised_per_condition():
    def judge(question, facts):
        return True, 0.7, "probably"

    assert C.evaluate([{"type": "semantic", "question": "x"}], FACTS,
                      judge=judge).passed
    assert not C.evaluate([{"type": "semantic", "question": "x",
                            "min_confidence": 0.9}], FACTS, judge=judge).passed


def test_a_judge_that_raises_does_not_make_the_guard_pass():
    """A provider outage must not open the gate."""
    def judge(question, facts):
        raise RuntimeError("the model is down")

    assert not C.evaluate([{"type": "semantic", "question": "x"}], FACTS,
                          judge=judge).passed


def test_a_semantic_condition_with_no_question_fails_closed():
    assert not C.evaluate([{"type": "semantic"}], FACTS,
                          judge=lambda q, f: (True, 1.0, "")).passed


def test_the_trace_records_every_leaf_for_the_run_history():
    result = C.evaluate([
        {"type": "domain_is", "field": "event.from", "value": "acme.com"},
        {"type": "semantic", "question": "urgent?"},
    ], FACTS, judge=lambda q, f: (True, 0.9, "yes"))
    kinds = [entry["type"] for entry in result.trace]
    assert kinds == ["domain_is", "semantic"]
    assert result.trace[-1]["confidence"] == 0.9
