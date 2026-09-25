"""A plan the agent keeps while it works, and what it does when a tool fails.

A flat loop answers a flat question well. Asked something with parts — *compare
these three vendors, check which we already emailed, and draft the reply* — it
tends to do the first part thoroughly and forget the rest, because nothing in
the loop is holding the shape of the task.

So on harder work the agent writes down what it is going to do and ticks items
off. The plan is not enforced: it is a note to itself, echoed back on every
round so the shape of the task stays in front of it, and surfaced in the trace
so the user can see what it thinks it is doing rather than watching a spinner.

The plan lives in a `ContextVar` rather than on the runner, for the same reason
the delegation chain does: tool calls execute in a thread pool, and a sub-agent
runs a whole turn of its own. Each turn needs its own plan, and neither should
be able to overwrite the other's.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

#: Enough to structure real work, few enough that the plan stays readable and
#: cheap to resend on every round.
MAX_STEPS_IN_PLAN = 10


@dataclass
class PlanStep:
    text: str
    done: bool = False


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)

    def render(self) -> str:
        if not self.steps:
            return ""
        lines = [f"{'x' if s.done else ' '} {i + 1}. {s.text}"
                 for i, s in enumerate(self.steps)]
        remaining = sum(1 for s in self.steps if not s.done)
        head = ("Your plan for this turn (keep it current with update_plan; "
                f"{remaining} step(s) left):")
        return head + "\n" + "\n".join(f"[{ln}" if ln[0] in "x " else ln
                                       for ln in lines)

    def as_list(self) -> list[dict]:
        return [{"text": s.text, "done": s.done} for s in self.steps]


_PLAN: contextvars.ContextVar[Plan | None] = contextvars.ContextVar(
    "chitragupta_agent_plan", default=None)


def start() -> object:
    """Begin a fresh plan for this turn. Returns a token for `finish`."""
    return _PLAN.set(Plan())


def finish(token: object) -> None:
    _PLAN.reset(token)                                    # type: ignore[arg-type]


def current() -> Plan | None:
    return _PLAN.get()


def update(steps: list[str], done_through: int = 0) -> str:
    """Replace the plan, marking the first `done_through` steps complete.

    Replacing rather than patching is deliberate: a model that has learned
    something usually wants to change what is left, and asking it to express
    that as an edit costs a round trip and gets it wrong.
    """
    plan = _PLAN.get()
    if plan is None:
        return "There is no plan to update on this turn."

    clean = [str(s).strip() for s in (steps or []) if str(s).strip()]
    if not clean:
        return "A plan needs at least one step."
    clean = clean[:MAX_STEPS_IN_PLAN]

    plan.steps = [PlanStep(text=t, done=i < max(0, done_through))
                  for i, t in enumerate(clean)]
    remaining = [s.text for s in plan.steps if not s.done]
    if not remaining:
        return "Plan complete. Write your answer now."
    return ("Plan noted. Next: " + remaining[0]
            + (f" ({len(remaining) - 1} more after that)" if len(remaining) > 1 else ""))


def unfinished(plan: Plan | None) -> list[str]:
    """Steps the agent wrote for itself and never ticked off."""
    if plan is None:
        return []
    return [s.text for s in plan.steps if not s.done]


#: Handed to the agent when the loop is about to end with its own plan
#: incomplete. Until this existed the plan was purely advisory — the agent
#: could write down four steps, do two, and answer as though it had done four.
#: Nothing checked, and the user had no way to know: the reply reads the same
#: either way, and the plan was only ever shown in the trace nobody opens.
#:
#: Deliberately offers BOTH ways out. "Finish them" alone turns a step that is
#: genuinely impossible — a connector that refused, a page that would not load
#: — into a loop. "Say what you skipped" alone gives up on work the agent could
#: still do in one more round. The unacceptable answer is the third one, which
#: is what it did before: neither, silently.
UNFINISHED_PROMPT = (
    "Before you answer: your own plan for this turn still has these steps "
    "unfinished:\n{steps}\n\n"
    "Either do them now, or write the answer and say plainly which parts you "
    "did not do and why. Do NOT present a partial answer as a complete one."
)


def unfinished_prompt(steps: list[str]) -> str:
    return UNFINISHED_PROMPT.format(
        steps="\n".join(f"  - {s}" for s in steps))


#: Whether a tool worked is now a field on its result (`results.ToolResult`),
#: not a guess from the start of its text. The prefix list that used to live
#: here read "web_search failed: …" as a success and "Tool budget …" as a
#: failure, which is why it is gone rather than fixed.
RETRY_NUDGE = (
    "The last tool call did not work. Do not repeat it unchanged — either fix "
    "the arguments, use a different tool, or tell the user plainly that this "
    "part could not be done."
)
