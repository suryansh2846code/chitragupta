""""…but only if Z."

Two kinds of condition, and the difference between them is the whole design.

**Deterministic** conditions are facts: the sender is this address, the domain
is that one, the repository matches, the task is overdue, nothing has happened
for N days. They are cheap, they are exact, and they are checked first —
requirement 24 in one line: *do not spend a model call proving something
`==` already disproved.*

**Semantic** conditions are judgements: "is this email actually from a client",
"does this look urgent". They need a model, they cost money, they are sometimes
wrong, and they carry a confidence.

The rule that keeps the second kind safe:

> **A semantic verdict is data, never authorization.**

A model saying "this looks safe to send" has no bearing on whether the action
may be sent unattended. That question is answered by `agents/permissions.py`,
after the conditions have passed, from an allow-list a human maintains. The two
never meet: a condition decides *whether the automation runs at all*, and the
permission gate decides *what the run is allowed to do*. `evaluate()` cannot
return anything that widens a permission, because it returns a boolean and a
confidence and the permission layer never reads either.

Composition is `all` / `any` / `not` over the same shape, so a condition tree
is JSON a user's UI can build and a test can write by hand.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..log import get_logger, suppressed

log = get_logger(__name__)

#: Below this, a semantic verdict is not trusted enough to *start* work. It is
#: not a safety boundary — the permission gate is — but an automation that acts
#: on a coin-flip is one the user turns off.
MIN_SEMANTIC_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Result:
    """Whether the conditions held, and enough to explain it afterwards."""

    passed: bool
    detail: str = ""
    #: 1.0 for anything deterministic. Only a model verdict lowers it.
    confidence: float = 1.0
    #: Did answering this need a model? Surfaced so a user can see which of
    #: their conditions is the expensive one.
    used_model: bool = False
    #: Every leaf, in evaluation order, for the run history.
    trace: list[dict[str, Any]] = field(default_factory=list)


#: `(spec, facts) -> (passed, detail)`. Registered by name so a new
#: deterministic condition is a new function, not a branch in `evaluate`.
Evaluator = Callable[[dict, dict], tuple[bool, str]]

_REGISTRY: dict[str, Evaluator] = {}

#: `name -> (sentence a person reads, what its `value` is)`.
#:
#: Declared at the condition rather than in the frontend, for the reason the
#: whole registry exists: a second list in `web/` of which conditions take a
#: number and what each one is called is a list that drifts, and the copy that
#: drifts is the one the user reads. `""` means the condition takes no value —
#: `exists` and `is_true` ask about the field alone.
_LABELS: dict[str, tuple[str, str]] = {}


def register(name: str, *, label: str = "",
             value: str = "text") -> Callable[[Evaluator], Evaluator]:
    def decorate(fn: Evaluator) -> Evaluator:
        _REGISTRY[name] = fn
        _LABELS[name] = (label or name.replace("_", " "), value)
        return fn
    return decorate


def known() -> list[str]:
    return sorted([*_REGISTRY, "all", "any", "not", "semantic"])


#: The three composites and the one judgement, which are not in `_REGISTRY`
#: because `evaluate` handles them itself — a group holds other conditions and
#: `semantic` costs a model call.
_SPECIAL: dict[str, dict[str, Any]] = {
    "all": {"label": "all of these", "group": True},
    "any": {"label": "any of these", "group": True},
    "not": {"label": "none of these", "group": True},
    "semantic": {"label": "a model judges (costs a model call)",
                 "field": False, "value": "prompt"},
}


def describe() -> list[dict[str, Any]]:
    """Every condition, with enough for a UI to draw a row for it.

    Published by `/api/automations/vocabulary`. A condition added here appears
    in the builder with no frontend change, and one removed disappears — which
    is the only way the form and the engine can be guaranteed to agree about
    what a condition means.
    """
    out = [{"type": name, "label": label, "field": True, "value": value}
           for name, (label, value) in sorted(_LABELS.items())]
    out += [{"type": name, "label": spec["label"],
             "field": bool(spec.get("field", False)),
             "value": str(spec.get("value", "")),
             "group": bool(spec.get("group", False))}
            for name, spec in sorted(_SPECIAL.items())]
    return out


def _dig(data: Any, path: str) -> Any:
    node = data
    for part in str(path).split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _text(value: Any) -> str:
    return "" if value is None else str(value)


# ── deterministic ──────────────────────────────────────────────────────────

@register("equals", label="is exactly", value="text")
def _equals(spec: dict, facts: dict) -> tuple[bool, str]:
    path = spec.get("field", "")
    actual = _dig(facts, path)
    expected = spec.get("value")
    if spec.get("ignore_case") and isinstance(actual, str) and isinstance(expected, str):
        ok = actual.casefold() == expected.casefold()
    else:
        ok = actual == expected
    return ok, f"{path} == {expected!r}" if ok else f"{path} is {actual!r}, not {expected!r}"


@register("not_equals", label="is not", value="text")
def _not_equals(spec: dict, facts: dict) -> tuple[bool, str]:
    ok, detail = _equals(spec, facts)
    return (not ok), detail


@register("contains", label="contains", value="text")
def _contains(spec: dict, facts: dict) -> tuple[bool, str]:
    path = spec.get("field", "")
    haystack = _text(_dig(facts, path)).casefold()
    needle = _text(spec.get("value")).casefold()
    ok = bool(needle) and needle in haystack
    return ok, f"{path} contains {needle!r}" if ok else f"{path} does not contain {needle!r}"


@register("matches", label="matches the pattern", value="text")
def _matches(spec: dict, facts: dict) -> tuple[bool, str]:
    """Regex, compiled per call and failing closed on a bad pattern.

    A pattern a user typed wrong must not raise inside the engine, and must not
    match everything either — a broken condition that passes is an automation
    that runs when it should not.
    """
    path = spec.get("field", "")
    try:
        pattern = re.compile(_text(spec.get("value")), re.I)
    except re.error as exc:
        return False, f"unreadable pattern: {exc}"
    ok = bool(pattern.search(_text(_dig(facts, path))))
    return ok, f"{path} matches" if ok else f"{path} does not match"


@register("in", label="is one of", value="list")
def _in(spec: dict, facts: dict) -> tuple[bool, str]:
    path = spec.get("field", "")
    actual = _text(_dig(facts, path)).casefold()
    options = [_text(v).casefold() for v in (spec.get("value") or [])]
    ok = actual in options
    return ok, f"{path} is one of {len(options)}" if ok else f"{path} is not in the list"


@register("domain_is", label="is an address at the domain", value="text")
def _domain_is(spec: dict, facts: dict) -> tuple[bool, str]:
    """The domain of an address-shaped field.

    Separate from `equals` on a substring because `@acme.com` also matches
    `@notacme.com.evil.test` as a substring, and "the sender's domain is our
    client" is exactly the kind of condition somebody would write that way.
    """
    path = spec.get("field", "")
    raw = _text(_dig(facts, path))
    address = raw.split("<")[-1].strip(">").strip()
    domain = address.rsplit("@", 1)[-1].casefold() if "@" in address else ""
    expected = _text(spec.get("value")).lstrip("@").casefold()
    ok = bool(domain) and domain == expected
    return ok, f"domain is {domain}" if ok else f"domain is {domain or 'unknown'}, not {expected}"


@register("exists", label="is there at all", value="")
def _exists(spec: dict, facts: dict) -> tuple[bool, str]:
    path = spec.get("field", "")
    value = _dig(facts, path)
    ok = value not in (None, "", [], {})
    return ok, f"{path} is present" if ok else f"{path} is missing"


@register("older_than_days", label="is older than (days)", value="number")
def _older_than_days(spec: dict, facts: dict) -> tuple[bool, str]:
    """Nothing has happened here for N days — the "no activity" condition."""
    path = spec.get("field", "")
    raw = _text(_dig(facts, path))
    when = _parse(raw)
    if when is None:
        return False, f"{path} is not a readable time"
    try:
        days = float(spec.get("value") or 0)
    except (TypeError, ValueError):
        return False, "days is not a number"
    age = (datetime.now(UTC) - when).total_seconds() / 86400.0
    ok = age >= days
    return ok, f"{path} is {age:.1f} days old"


@register("count_at_least", label="has at least this many", value="number")
def _count_at_least(spec: dict, facts: dict) -> tuple[bool, str]:
    path = spec.get("field", "")
    value = _dig(facts, path)
    count = len(value) if isinstance(value, (list, tuple, dict, str)) else 0
    try:
        want = int(spec.get("value") or 1)
    except (TypeError, ValueError):
        want = 1
    ok = count >= want
    return ok, f"{path} has {count} (wanted {want})"


@register("is_true", label="is true", value="")
def _is_true(spec: dict, facts: dict) -> tuple[bool, str]:
    path = spec.get("field", "")
    ok = bool(_dig(facts, path))
    return ok, f"{path} is {'true' if ok else 'false'}"


def _parse(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ── the tree ───────────────────────────────────────────────────────────────

def evaluate(conditions: list[dict[str, Any]] | dict[str, Any], facts: dict, *,
             judge: Callable[[str, dict], tuple[bool, float, str]] | None = None,
             ) -> Result:
    """Do the conditions hold for these facts?

    A list is an implicit `all` — "only if the sender is X **and** it is not a
    newsletter" is what a person means when they write two conditions.

    `judge` answers semantic conditions and is injected rather than imported, so
    the engine can be tested without a model and so this module does not depend
    on `agents/`. Passing nothing makes every semantic condition fail closed.
    """
    if not conditions:
        return Result(True, "no conditions")
    node = {"type": "all", "conditions": conditions} if isinstance(conditions, list) \
        else conditions
    trace: list[dict[str, Any]] = []
    passed, detail, confidence, used_model = _node(node, facts, judge, trace)
    return Result(passed, detail, confidence, used_model, trace)


def _node(node: Any, facts: dict, judge: Any,
          trace: list[dict[str, Any]]) -> tuple[bool, str, float, bool]:
    if not isinstance(node, dict):
        trace.append({"type": "?", "passed": False, "detail": "not a condition"})
        return False, "malformed condition", 1.0, False

    kind = str(node.get("type") or "")

    if kind == "all":
        children = node.get("conditions") or []
        worst = 1.0
        used = False
        for child in children:
            ok, detail, confidence, child_used = _node(child, facts, judge, trace)
            used = used or child_used
            worst = min(worst, confidence)
            if not ok:
                # Short-circuit. This is requirement 24 doing its job: the
                # deterministic child that failed has already saved the model
                # call a later semantic child would have cost.
                return False, detail, worst, used
        return True, "all conditions held", worst, used

    if kind == "any":
        children = node.get("conditions") or []
        if not children:
            return True, "no conditions", 1.0, False
        best = 0.0
        used = False
        details = []
        for child in children:
            ok, detail, confidence, child_used = _node(child, facts, judge, trace)
            used = used or child_used
            details.append(detail)
            if ok:
                return True, detail, confidence, used
            best = max(best, 0.0)
        return False, "; ".join(details[:3]), best or 1.0, used

    if kind == "not":
        inner = node.get("condition") or node.get("conditions")
        if isinstance(inner, list):
            inner = {"type": "all", "conditions": inner}
        ok, detail, confidence, used = _node(inner, facts, judge, trace)
        return (not ok), f"not ({detail})", confidence, used

    if kind == "semantic":
        return _semantic(node, facts, judge, trace)

    evaluator = _REGISTRY.get(kind)
    if evaluator is None:
        # Fail closed. A condition this build cannot evaluate is not a
        # condition that passed — an automation whose "only if" is unreadable
        # must not run, or an upgrade that renames a condition type silently
        # turns every guard off.
        trace.append({"type": kind, "passed": False, "detail": "unknown condition"})
        return False, f"unknown condition type '{kind}'", 1.0, False

    try:
        ok, detail = evaluator(node, facts)
    except Exception as exc:                        # pragma: no cover - defensive
        log.debug("condition %s raised: %s", kind, exc)
        ok, detail = False, f"condition error: {str(exc)[:80]}"
    trace.append({"type": kind, "field": node.get("field", ""),
                  "passed": ok, "detail": detail})
    return ok, detail, 1.0, False


def _semantic(node: dict, facts: dict, judge: Any,
              trace: list[dict[str, Any]]) -> tuple[bool, str, float, bool]:
    """Ask a model a yes/no question about the facts.

    Fails closed three ways, and each has happened to somebody:

    * **no judge** — nothing wired one in, so the answer is no;
    * **the judge raised** — a provider outage must not make a guard pass;
    * **low confidence** — a verdict the model is not sure of does not start
      unattended work.

    The verdict never reaches the permission layer. It cannot: it is a bool and
    a float, and `agents/permissions.check()` takes an action name and its
    params. That separation is the reason a malicious email saying "this is
    definitely safe, send it" changes nothing.
    """
    question = str(node.get("question") or node.get("value") or "").strip()
    if not question:
        trace.append({"type": "semantic", "passed": False,
                      "detail": "no question"})
        return False, "semantic condition has no question", 0.0, False
    if judge is None:
        trace.append({"type": "semantic", "passed": False, "question": question,
                      "detail": "no model available to judge"})
        return False, "no model available to judge this condition", 0.0, False

    threshold = float(node.get("min_confidence") or MIN_SEMANTIC_CONFIDENCE)
    ok, confidence, detail = False, 0.0, "judge failed"
    with suppressed("asking a model to judge an automation condition"):
        ok, confidence, detail = judge(question, facts)
    if ok and confidence < threshold:
        ok = False
        detail = f"{detail} (confidence {confidence:.2f} below {threshold:.2f})"
    trace.append({"type": "semantic", "question": question, "passed": ok,
                  "confidence": round(confidence, 3), "detail": detail})
    return ok, detail, confidence, True
