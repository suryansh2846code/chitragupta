"""Can this automation actually run — asked before it is left alone to try.

An automation is set up once and then runs unattended for months. Everything it
needs is decided at that moment and checked at three in the morning, which is
the worst possible time to discover that the agent it names was deleted, the app
it reads was disconnected, or the address it writes to was never allowed.

So this asks the questions up front and answers them in the user's words. It is
a **report, never a decision**: nothing here grants anything, nothing here stops
a run, and the permission gate is asked exactly as it would have been. A user
who reads "it will pause and ask you" and is happy with that should be able to
save it and walk away.

Three levels, and the difference between them is what the user has to do:

* `ready` — nothing to do.
* `asks` — it will work, and it will stop and wait for a tap at some point.
  That is a setting, not a fault: "draft the reply and let me look" is a
  perfectly good automation.
* `stuck` — it cannot work as written, and running it would only produce a run
  that says so.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..log import suppressed

#: Worst wins. A single `stuck` makes the whole automation stuck, however many
#: other things are fine, because that is the bit that decides whether leaving
#: it alone is reasonable.
ORDER = {"ready": 0, "asks": 1, "stuck": 2}


@dataclass(frozen=True)
class Check:
    """One question, its answer, and what the user would do about it."""

    #: A short label for the thing being checked — "Gmail", "The agent".
    name: str
    state: str
    #: What is true, in the user's words. Never an exception, never an id.
    detail: str
    #: What they would do next, or "" when there is nothing to do.
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state,
                "detail": self.detail, "fix": self.fix}


#: `(connector id) -> (name on screen, is it set up)`. Injected, because this
#: package may not import `connectors/` — an event kind is the automation
#: layer's idea and "Google Calendar" is the connector layer's, and the place
#: the two are allowed to meet is the API route that has both.
AppState = Callable[[str], "tuple[str, bool]"]


def review(automation: Any, *, app_state: AppState | None = None,
           ) -> dict[str, Any]:
    """Every check, and the worst answer among them."""
    checks = [
        *_agent(automation),
        *_model(automation),
        *_trigger(automation),
        *_apps(automation, app_state),
        *_sending(automation),
    ]
    worst = max((ORDER.get(c.state, 0) for c in checks), default=0)
    state = next(k for k, v in ORDER.items() if v == worst)
    return {"state": state, "checks": [c.as_dict() for c in checks],
            "summary": _summary(state, checks)}


def _summary(state: str, checks: list[Check]) -> str:
    if state == "stuck":
        first = next((c for c in checks if c.state == "stuck"), None)
        return f"It cannot run yet — {first.detail}" if first else "It cannot run yet."
    if state == "asks":
        waiting = [c.name for c in checks if c.state == "asks"]
        return ("It will run, and will stop to ask you about "
                + ", ".join(waiting) + ".")
    return "Ready — it has everything it needs."


# ── the agent ─────────────────────────────────────────────────────────────

def _agent(automation: Any) -> list[Check]:
    from ..agents import list_agents

    roster = []
    with suppressed("listing agents to check an automation can run"):
        roster = list(list_agents())
    if not roster:
        return [Check("The agent", "stuck", "you have no agents yet",
                      "Make one from the Agent Library.")]
    match = next((a for a in roster if a.id == automation.agent_id), None)
    if match is None:
        return [Check("The agent", "stuck",
                      f"“{automation.agent_id}” is not one of your agents",
                      "Pick a real one in Edit: "
                      + ", ".join(a.name for a in roster))]
    return [Check("The agent", "ready", f"{match.name} will run it")]


# ── something to think with ───────────────────────────────────────────────

def _model(automation: Any) -> list[Check]:
    """A run with no model reaches the planning step and stops there.

    The automation's own choice is checked when it has one, and the user's
    default when it does not — those are two different questions and only one
    of them is being asked.
    """
    wanted = automation.execution.provider
    with suppressed("checking a provider is connected for an automation"):
        from ..models import get_model_catalog

        catalog = {e.get("id", ""): e for e in get_model_catalog()}
        if wanted:
            entry = catalog.get(wanted)
            if entry is None:
                return [Check("The model", "stuck",
                              f"“{wanted}” is not a provider this app has",
                              "Choose another under How it runs.")]
            if not entry.get("connected"):
                return [Check("The model", "stuck",
                              f"{entry.get('name') or wanted} is not connected",
                              "Sign in to it on the Model screen, or choose "
                              "another under How it runs.")]
            return [Check("The model", "ready",
                          f"{entry.get('name') or wanted} is connected")]
        if not any(e.get("connected") for e in catalog.values()):
            return [Check("The model", "stuck", "no model is connected",
                          "Connect one on the Model screen.")]
        return [Check("The model", "ready", "your usual model")]
    return []


# ── does the trigger say enough to match anything ─────────────────────────

def _trigger(automation: Any) -> list[Check]:
    from . import triggers

    spec = automation.trigger or {}
    kind = str(spec.get("type") or "")
    if kind not in triggers.known():
        return [Check("When it runs", "stuck",
                      f"“{kind or 'nothing'}” is not a trigger this app has",
                      "Set it again in Edit.")]
    if kind == triggers.EVENT and not str(spec.get("kind") or ""):
        return [Check("When it runs", "stuck",
                      "it waits for something to happen but does not say what",
                      "Pick what happens in Edit.")]
    if kind == "schedule" and not str(spec.get("at_time") or ""):
        return [Check("When it runs", "stuck",
                      "it runs at a time of day and has no time",
                      "Set the time in Edit.")]
    if kind == "manual":
        return [Check("When it runs", "ready",
                      "only when you press Run now")]
    return [Check("When it runs", "ready", _when(automation))]


def _when(automation: Any) -> str:
    with suppressed("describing when an automation next runs"):
        from . import triggers

        due = triggers.next_due(automation)
        if due:
            return f"next at {due.isoformat(timespec='minutes')}"
    return "on its trigger"


# ── the apps it reads ─────────────────────────────────────────────────────

def _apps(automation: Any, app_state: AppState | None) -> list[Check]:
    """Only the apps the automation *names*, because those are the only ones we
    can be sure about.

    An automation scoped to nothing might use any app its agent may use, and
    guessing which from the instruction text would be a check that is wrong
    often enough to be ignored — which is worse than not making it.

    With no `app_state` the question cannot be answered, and an unanswered
    question is reported as nothing rather than as "ready": a caller that did
    not wire this in should get silence, not a reassurance it did not earn.
    """
    from ..agents import connector_grants

    wanted = list(automation.execution.connectors)
    spec = automation.trigger or {}
    source = str(spec.get("source") or "").strip().lower()
    if source and source not in wanted:
        wanted.append(source)
    if not wanted or app_state is None:
        return []

    out: list[Check] = []
    for name in wanted:
        label, connected = name, False
        with suppressed("checking a connector is set up for an automation"):
            label, connected = app_state(name)
        if not connected:
            out.append(Check(label, "stuck", f"{label} is not connected",
                             "Connect it on the Connectors screen."))
            continue
        allowed = True
        with suppressed("checking an agent may use a connector"):
            allowed = connector_grants.may_use(automation.agent_id, name)
        if not allowed:
            out.append(Check(
                label, "asks", f"{label} is connected, but this agent has to "
                "ask before using it",
                "Allow it for this agent under Agents & tools."))
            continue
        out.append(Check(label, "ready", f"{label} is connected and allowed"))
    return out


# ── anything that leaves the machine ──────────────────────────────────────

def _sending(automation: Any) -> list[Check]:
    """Whether an outbound action would go through, or wait.

    Waiting is not a fault. It is the default and it is usually right — but a
    user who set an automation up to send a summary every morning should find
    out now that it will queue every morning instead, rather than in a week when
    they notice nothing arrived.
    """
    if not automation.execution.allow_email:
        return [Check("Sending", "ready",
                      "switched off for this automation — it will not send "
                      "anything")]
    allowed: list[str] = []
    with suppressed("reading the outbound allow-list for an automation"):
        from ..agents import permissions

        allowed = [str(row.get("label") or row.get("value") or "")
                   for row in permissions.list_permissions()]
    if not allowed:
        return [Check("Sending", "asks",
                      "nothing is on your allowed list, so anything it sends "
                      "will wait for a tap",
                      "Add the address you expect it to write to, or approve "
                      "each one as it comes.")]
    return [Check("Sending", "ready",
                  f"{len(allowed)} address(es) allowed — anything else waits "
                  "for a tap")]
