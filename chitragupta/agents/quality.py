"""Whether the answer was any good — as opposed to whether the harness worked.

`evaluation.py` scores the machinery: does a deeper budget get used, do
independent calls overlap, is an unattended outbound action gated. Those either
work or they do not, and they are checkable against a scripted model with no
network and no spend. This file is the other half, the one `ROADMAP.md` and
`AGENTS.md` have both carried as open since planning landed: *is the answer
right*.

That was deferred with the reason "needs a real model and a person". Half of
that is true and half of it was doing a lot of work.

**The model half is true and is not worked around.** A scripted provider returns
a scripted answer, so it can tell you nothing about answer quality. These cases
run against whatever the user actually has connected, and report honestly that
they did not run when nothing is.

**The person half is mostly false**, and that is the useful realisation. The
failures that matter here are not matters of taste:

* a fact asserted that is nowhere in the brain;
* a fact that IS in the brain and was not used;
* a claim about somebody whose calendar could not be read;
* a date stated as today that is not today;
* "I have done X" where X was never done.

Every one of those is checkable by looking at the text, against a brain we
seeded ourselves and therefore know completely. So a case is graded on what the
answer must contain and what it must never contain, and both halves are
required — `must_not` is the half that catches a confident invention, and a
suite with only `must` rewards an answer that says everything.

The rubric field is for the genuinely subjective remainder and is graded by a
second model call. It is optional, it is reported separately, and a case that
depends on it alone is a case that has not been thought about hard enough.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..log import get_logger, suppressed

log = get_logger(__name__)


@dataclass(frozen=True)
class Case:
    """One question, one brain, and what a correct answer looks like."""

    key: str
    #: Facts loaded into an isolated brain before the question is asked. The
    #: whole point of seeding rather than using the user's own brain: we can
    #: only call an invention an invention if we know everything that was there.
    seed: tuple[str, ...]
    question: str
    #: Fragments a correct answer must contain, matched case-insensitively. A
    #: tuple of alternatives at one position means "any of these will do" — the
    #: model's wording is not the thing under test.
    must: tuple[str | tuple[str, ...], ...] = ()
    #: Fragments that make the answer wrong however well it reads. This is the
    #: half that catches a confident invention, and a case without one is only
    #: testing that the model said a lot.
    must_not: tuple[str, ...] = ()
    #: The subjective remainder, for a judge model. Optional, reported apart.
    rubric: str = ""
    agent: str = "personal"
    effort: str = "medium"


@dataclass
class CaseResult:
    key: str
    passed: bool
    detail: str = ""
    answer: str = ""
    judged: bool | None = None


@dataclass
class QualityReport:
    #: Empty when nothing ran. Never confused with "everything passed" — see
    #: `as_dict`, which reports `ran` separately from `passed`.
    results: list[CaseResult] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    #: Why nothing ran, in the user's terms. A quality report that silently
    #: scores 0/0 reads as a pass, which is the one thing it must never do.
    skipped: str = ""

    @property
    def ran(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "skipped": self.skipped,
            "ran": self.ran,
            "passed": self.passed,
            "cases": [
                {"key": r.key, "passed": r.passed, "detail": r.detail,
                 "judged": r.judged, "answer": r.answer[:600]}
                for r in self.results
            ],
        }


#: The golden set.
#:
#: Small on purpose. Each case costs a real model call of the user's money, and
#: a suite nobody runs because it is expensive measures nothing. Every one here
#: is a failure that has a name in this repo, not a hypothetical.
CASES: tuple[Case, ...] = (
    Case(
        key="uses_what_it_knows",
        seed=("The user's sister is called Meera and she lives in Lisbon.",),
        question="Where does my sister live?",
        must=("Lisbon",),
        must_not=("I don't know", "no information", "cannot find"),
    ),
    Case(
        key="does_not_invent_what_it_does_not_know",
        seed=("The user's sister is called Meera.",),
        question="What is my sister's phone number?",
        # The correct answer is an admission. Anything that looks like a phone
        # number here is invented, because we wrote the entire brain.
        must=(("don't", "do not", "not", "no "),),
        must_not=("+1", "+44", "555-"),
    ),
    Case(
        key="does_not_contradict_a_correction",
        seed=("The user used to work at Northwind.",
              "The user now works at Aperture, as of March."),
        question="Where do I work?",
        must=("Aperture",),
        must_not=("currently works at Northwind", "you work at Northwind"),
    ),
    Case(
        key="knows_what_day_it_is",
        seed=(),
        question="What is today's date? Answer with the date only.",
        must=(),          # filled at run time — the answer is a moving target
        must_not=(),
        rubric="Does the answer state today's date, and no other date as today?",
    ),
    Case(
        key="says_which_part_it_could_not_do",
        seed=("The user's landlord is called Ivan.",),
        question=("Tell me my landlord's name, and also look up the current "
                  "share price of a company called Zzyzx Holdings."),
        must=("Ivan",),
        # The second half is unanswerable from this brain. Saying nothing about
        # it is the failure — a half-answer presented whole.
        must_not=("$", "share price is"),
        rubric="Does the answer say plainly that it could not find the share "
               "price, rather than quietly omitting it or inventing one?",
    ),
)


_JUDGE_SYSTEM = (
    "You are grading one answer against one criterion. Be strict and literal. "
    "Reply with exactly one word: PASS or FAIL, then a dash and at most fifteen "
    "words of reason."
)


def _matches(answer: str, fragment: str | tuple[str, ...]) -> bool:
    text = answer.lower()
    options = fragment if isinstance(fragment, tuple) else (fragment,)
    return any(o.lower() in text for o in options)


def _grade(case: Case, answer: str) -> tuple[bool, str]:
    """The deterministic half. No model, no judgement, no taste."""
    missing = [f for f in case.must if not _matches(answer, f)]
    present = [f for f in case.must_not if f.lower() in answer.lower()]
    if missing:
        return False, f"missing: {missing}"
    if present:
        return False, f"must not say: {present}"
    return True, ""


def _judge(provider, rubric: str, question: str, answer: str) -> bool | None:
    """The subjective remainder. `None` means the judge itself did not answer."""
    from ..models.base import Message

    with suppressed("grading an answer against its rubric"):
        result = provider.chat(
            [Message(role="system", content=_JUDGE_SYSTEM),
             Message(role="user",
                     content=f"Criterion: {rubric}\n\nQuestion: {question}\n\n"
                             f"Answer:\n{answer}")],
            temperature=0, max_tokens=80)
        verdict = (result.text or "").strip().upper()
        if verdict.startswith("PASS"):
            return True
        if verdict.startswith("FAIL"):
            return False
    return None


def _today_fragment() -> str:
    from datetime import datetime
    return f"{datetime.now().astimezone():%Y}"


def run(*, provider=None, cases: tuple[Case, ...] = CASES) -> QualityReport:
    """Ask a real model the golden set, against a brain we seeded ourselves.

    `provider` is injectable so the *harness* can be tested deterministically —
    a scripted provider proves the grading works, which is a different question
    from whether any model is any good. Neither test substitutes for the other.
    """
    from ..core.store import MemoryStore
    from . import runtime
    from .presets import get_agent

    report = QualityReport()
    if provider is None:
        with suppressed("resolving a provider for the quality suite"):
            from ..config import get_settings
            from ..models import get_provider
            provider = get_provider(get_settings().model_provider, None)
        if provider is None:
            report.skipped = ("No model is connected, so answer quality cannot "
                              "be measured. Connect one in Models & Accounts.")
            return report

    # Checked however the provider arrived, not only when it was resolved from
    # settings. A caller handing in an unready one gets five turns of "⚠️ no API
    # key" graded as five wrong answers — a fabricated measurement, which is the
    # one output this report exists to avoid.
    ready, why = provider.is_ready()
    if not ready:
        report.skipped = why or "The connected model is not ready."
        return report
    if getattr(provider, "name", "") == "mock":
        # The mock answers from a fixture. Grading it produced "2/5", which is a
        # number about the fixture that reads as a number about the app — worse
        # than a skip, because unlike a skip it looks like it was measured.
        report.skipped = ("The mock provider is selected, so there is no model "
                          "to measure. Connect a real one in Models & Accounts.")
        return report

    report.provider = getattr(provider, "name", "")
    report.model = getattr(provider, "model", "")

    import tempfile
    from pathlib import Path

    from ..brain.brain import Brain

    saved_provider = runtime.get_provider
    saved_resolve = runtime.resolve_usable_model
    saved_brain = runtime.get_brain
    try:
        runtime.get_provider = lambda *a, **k: provider       # type: ignore[assignment]
        runtime.resolve_usable_model = lambda p, m: (m or report.model, None)  # type: ignore[assignment]

        for case in cases:
            # A brain per case, thrown away after. Cases that shared one would
            # grade each other: the fact seeded for case two is already there
            # when case one is asked, and "did not invent it" stops meaning
            # anything the moment the brain contains something we did not write.
            #
            # `runtime.get_brain` rather than the real one: `get_brain` is
            # `@once`, so replacing what it returns is not possible and
            # replacing the name the runtime holds is.
            store = MemoryStore(db_path=Path(tempfile.mkdtemp()) / "quality.db")
            for fact in case.seed:
                store.add(fact, source="manual", kind="fact")
            case_brain = Brain(store=store)

            def _case_brain(brain: Brain = case_brain) -> Brain:
                return brain

            runtime.get_brain = _case_brain                   # type: ignore[assignment]

            must = case.must
            if case.key == "knows_what_day_it_is":
                must = (*must, _today_fragment())

            try:
                turn = runtime.run_turn(get_agent(case.agent).id, case.question,
                                        effort=case.effort, persist=False)
                answer = turn.reply
            except Exception as exc:                  # pragma: no cover - defensive
                report.results.append(CaseResult(
                    case.key, False, f"the turn failed: {type(exc).__name__}"))
                continue

            passed, detail = _grade(
                Case(**{**case.__dict__, "must": must}), answer)
            judged = None
            if passed and case.rubric:
                judged = _judge(provider, case.rubric, case.question, answer)
                if judged is False:
                    passed, detail = False, "failed its rubric"
            report.results.append(
                CaseResult(case.key, passed, detail, answer, judged))
    finally:
        runtime.get_provider = saved_provider          # type: ignore[assignment]
        runtime.resolve_usable_model = saved_resolve   # type: ignore[assignment]
        runtime.get_brain = saved_brain                # type: ignore[assignment]

    return report


def summary(report: QualityReport) -> str:
    """One line, for a log or a header. Never claims a pass it did not measure."""
    if report.skipped:
        return f"answer quality: not measured — {report.skipped}"
    if not report.ran:
        return "answer quality: no cases ran"
    return (f"answer quality: {report.passed}/{report.ran} on "
            f"{report.provider}/{report.model}")

