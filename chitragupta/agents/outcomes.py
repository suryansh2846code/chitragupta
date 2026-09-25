"""What happened when the user approved an action — told back to the agent.

An agent proposes an action, the user taps Confirm, the frontend runs it, and
the result is rendered in the card. The agent's turn ended long before any of
that. So it never learns whether the thing it proposed worked.

The cost of that showed up the first time a connector refused one. The agent had
no way to see the refusal, so it could not fix its own call — the best it could
manage was to apologise, guess at the argument shape, and ask the user to paste
the error back in. Three taps later it still did not know.

Nothing here weakens the gate. The user still approves every outbound action;
`permissions.NEVER_UNATTENDED` is untouched. What changes is that the outcome
comes back **into the conversation** rather than only onto a card, and that a
failure gets the agent one chance to react to it.

Deliberately asymmetric:

* **Success is recorded and nothing else happens.** A model call to say "yes,
  that worked" spends the user's money to tell them what the card already says.
* **A failure runs one follow-up turn.** That is the case where the agent can
  do something useful — correct the call, or say plainly that the connector
  cannot do this. One, never a loop: a corrected proposal is a new card, so a
  person is back in the middle before anything else runs.
"""
from __future__ import annotations

from typing import Any

from ..log import get_logger, suppressed

log = get_logger(__name__)

#: Marks the agent's own record of what its proposal did, so the next turn can
#: tell an outcome from something it said.
PREFIX = "[Action result]"

#: Keeps a connector's complaint from filling the conversation window.
MAX_DETAIL_CHARS = 400

#: What the agent is asked after one of its proposals fails. Phrased so that
#: "this cannot be done" is an acceptable answer — an agent told only to fix it
#: will keep inventing arguments, which is the behaviour this replaces.
RETRY_BRIEF = (
    "An action you proposed has now run and it FAILED. Here is exactly what "
    "happened:\n\n{detail}\n\n"
    "Do one of these two things, and nothing else:\n"
    "1. If you can tell from that message what was wrong with YOUR call, "
    "propose a corrected action. Use only the arguments the tool actually "
    "lists.\n"
    "2. If the connector simply cannot do what the user asked — the tool does "
    "not exist, or it has no argument for this — say so plainly, say what it "
    "CAN do instead, and stop. Do not propose the same call again.\n"
    "Be brief. The user is waiting."
)


def _what_was_attempted(action_type: str, params: dict) -> str:
    """The action in the user's terms, reusing the approvals wording.

    One phrasing for an action, whether it is read off an approval card or
    read back to the agent — two would drift, and the drift would be the agent
    describing the thing differently from the button the user pressed.
    """
    with suppressed("describing an approved action for the agent"):
        # `action_phrasing`, not the re-export in `approvals` — which calls
        # back into this module to settle an action, and importing it here
        # closed that loop. The wording lives below both.
        from ..action_phrasing import describe as summarise
        return summarise(action_type, params or {})
    return action_type.replace("_", " ")


def _clock(stamp: str) -> str:
    """" at 3:42 PM" from an ISO timestamp, or "" if it is not one.

    Never raises on a malformed stamp: the verification is a nicety and a
    broken clock string must not take the outcome line down with it.
    """
    if not stamp:
        return ""
    with suppressed("formatting the time an action was confirmed"):
        from datetime import datetime
        return " at " + datetime.fromisoformat(stamp).strftime("%-I:%M %p")
    return ""


def describe(action_type: str, params: dict, result: dict) -> str:
    """One line: what was attempted, and what came back."""
    what = _what_was_attempted(action_type, params)

    if result.get("ok"):
        detail = str(result.get("detail") or "").strip()
        # Rung 5, in the sentence the agent reads. "done" is what we asked for;
        # "confirmed 3:42 PM" is what the service says happened, and an agent
        # that can tell the two apart can answer "did it definitely go?".
        verb = "confirmed" if result.get("verified") else "done"
        when = _clock(str(result.get("verified_at") or ""))
        head = f"{PREFIX} {what} — {verb}{when}."
        return head + (f" {detail[:MAX_DETAIL_CHARS]}" if detail else "")
    why = str(result.get("error") or "it did not work").strip()
    return f"{PREFIX} {what} — FAILED: {why[:MAX_DETAIL_CHARS]}"


def record(agent_id: str, action_type: str, params: dict, result: dict) -> str:
    """Write the outcome into the agent's conversation. Returns what was written.

    Stored as the agent's own note rather than as something the user said,
    because the user did not say it — they tapped a button.
    """
    line = describe(action_type, params, result)
    if not agent_id:
        return line
    with suppressed("recording an action outcome for the agent"):
        from .agent import AgentMemory
        AgentMemory().append(agent_id, "assistant", line)
    return line


def react(agent_id: str, action_type: str, params: dict, result: dict,
          **turn_kwargs: Any) -> str:
    """Let the agent answer for a failed action. Returns its reply, or "".

    Runs isolated (`persist=False`) and the reply is appended afterwards, so the
    transcript reads as the agent reacting to its own action rather than as a
    turn the user started.
    """
    if not agent_id or result.get("ok"):
        return ""

    line = describe(action_type, params, result)
    reply = ""
    with suppressed("asking the agent to react to a failed action"):
        from . import entry
        from .agent import AgentMemory

        # Through `entry`, so this module does not import the loop it runs
        # inside. See `agents/entry.py`.
        turn = entry.run(agent_id, RETRY_BRIEF.format(detail=line),
                         persist=False, **turn_kwargs)
        reply = (turn.reply or "").strip() if turn is not None else ""
        if reply:
            AgentMemory().append(agent_id, "assistant", reply)
    return reply


def _step_line(step: dict) -> str:
    """One plan step, in the words the card used for it.

    Built from the step's own `summary` rather than run back through
    `describe()`: `run_plan` already worked out what to call each action, and
    asking a second time with the params thrown away produces "send email"
    where the card said "Email “Revised proposal” to rahul@work.test".
    """
    result = step.get("result") or {}
    what = step.get("summary") or str(step.get("type") or "an action")
    if result.get("ok"):
        verb = "confirmed" if result.get("verified") else "done"
        when = _clock(str(result.get("verified_at") or ""))
        detail = str(result.get("detail") or "").strip()
        return (f"{PREFIX} {what} — {verb}{when}."
                + (f" {detail[:MAX_DETAIL_CHARS]}" if detail else ""))
    why = str(result.get("error") or "it did not work").strip()
    return f"{PREFIX} {what} — FAILED: {why[:MAX_DETAIL_CHARS]}"


def settle_plan(agent_id: str, plan_result: dict, **turn_kwargs: Any) -> dict:
    """Record what a whole plan did, and answer for it at most once.

    A plan of nine steps that failed on the seventh must not become seven
    follow-up turns. The asymmetry that governs a single action governs this
    too, one level up: every step is written into the conversation so the agent
    knows what happened, and **one** turn is spent only if something failed.

    The note the agent is given is the plan's own summary line, not the failed
    step's error alone — an agent told "Gmail refused" without being told that
    six things before it worked will apologise for all seven.
    """
    from .agent import AgentMemory

    steps = plan_result.get("steps") or []
    lines = [_step_line(s) for s in steps]
    for s in plan_result.get("skipped") or []:
        # Named, never left to be inferred from a count. "Six of nine" does not
        # tell anybody which three did not happen.
        lines.append(f"{PREFIX} {s.get('summary') or s.get('type', '')} "
                     "— not started, the step before it failed.")
    summary = f"{PREFIX} Plan: {plan_result.get('detail') or ''}".strip()

    if agent_id:
        with suppressed("recording a plan's outcome for the agent"):
            memory = AgentMemory()
            for line in [*lines, summary]:
                memory.append(agent_id, "assistant", line)

    out = dict(plan_result)
    if plan_result.get("ok"):
        return out

    note = ""
    with suppressed("asking the agent to react to a failed plan"):
        from . import entry

        detail = "\n".join([*lines, summary])
        turn = entry.run(agent_id, RETRY_BRIEF.format(detail=detail),
                        persist=False, **turn_kwargs)
        note = (turn.reply or "").strip()
        if note:
            AgentMemory().append(agent_id, "assistant", note)
    if note:
        out["agent_note"] = note
    return out


def settle(agent_id: str, action_type: str, params: dict,
           result: dict, **turn_kwargs: Any) -> dict:
    """Record the outcome, and on a failure give the agent one chance to answer.

    Returns the result unchanged plus `agent_note` — what the agent said about
    it, when it had anything to say. The caller is the approval path, so this
    never runs without the user having pressed something.
    """
    record(agent_id, action_type, params, result)
    note = react(agent_id, action_type, params, result, **turn_kwargs)
    out = dict(result)
    if note:
        out["agent_note"] = note
    return out
