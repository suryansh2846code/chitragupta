"""The automation surface: what is running, what it did, and what it needs.

Deliberately **additive**. `/api/routines` is untouched and still works — an
automation is a routine with more to say, so the old endpoints keep creating and
listing the same rows. These add the parts a routine never had: a trigger spec,
conditions, policies, and the run history that makes behaviour inspectable.

Nothing here decides anything. Reads come from `core/automation_store`, writes
go through `automation.engine`, and the permission layer is never consulted from
here — an API that could authorise an action would be a second permission
system, which is the one thing this must not become.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ...core import automation_store as store
from ...log import get_logger, suppressed
from ..concurrency import calls_a_model

log = get_logger(__name__)
router = APIRouter()

#: Runs returned per listing. History is for reading, not for exporting.
MAX_RUNS = 50


def _summary(automation: Any, runs: list[dict]) -> dict[str, Any]:
    """One automation as a card: what it is, and how it is doing.

    `state` is the **worst live thing about it**, not its last run's state:
    a user scanning a list wants "this one needs you" to win over "and it also
    ran fine on Tuesday".
    """
    from ...automation import triggers

    live = [r for r in runs if r["state"] in store.LIVE_STATES]
    waiting = [r for r in live if r["state"] == store.RunState.WAITING_FOR_APPROVAL]
    recent = runs[0] if runs else None
    if not automation.enabled:
        state = "paused"
    elif waiting:
        state = "waiting_for_approval"
    elif any(r["state"] == store.RunState.ESCALATED for r in runs[:3]):
        state = "needs_attention"
    elif live:
        state = "running"
    else:
        state = "active"

    upcoming = ""
    with suppressed("computing an automation's next run for the API"):
        when = triggers.next_due(automation)
        upcoming = when.isoformat() if when else ""

    return {
        "id": automation.id,
        "name": automation.name,
        "goal": automation.stated_goal,
        "agent_id": automation.agent_id,
        "enabled": automation.enabled,
        "state": state,
        "trigger": automation.trigger,
        "conditions": automation.conditions,
        "policy": automation.policy.as_dict(),
        "execution": automation.execution.as_dict(),
        "next_run": upcoming or automation.next_run,
        "last_run": automation.last_run,
        "last_outcome": (recent or {}).get("outcome", ""),
        "last_state": (recent or {}).get("state", ""),
        "runs": len(runs),
        "waiting": len(waiting),
        # An automation whose agent is not in the roster can never run, and the
        # list is the only place a user would find out. It used to read "Not run
        # yet" forever, which reads as "nothing has happened" rather than "this
        # is broken".
        "agent_missing": _agent_is_gone(automation.agent_id),
    }


def _agent_is_gone(agent_id: str) -> bool:
    from ...agents import list_agents

    with suppressed("checking whether an automation's agent still exists"):
        return not any(a.id == agent_id for a in list_agents())
    return False


@router.get("/api/automations")
def list_automations():
    """Every automation, with enough to render the list screen."""
    from ...automation import engine

    out = []
    for automation in engine.list_automations():
        out.append(_summary(automation, store.runs_for(automation.id, limit=10)))
    return {"automations": out}


def _usable_providers() -> list[dict[str, Any]]:
    """Providers this user has connected, with the models each offers.

    Resolved per user, never hardcoded: a list typed in here is a list that
    offers a model the account cannot reach, and "the app is broken" is what
    that reads as when it 404s on the first run.
    """
    out: list[dict[str, Any]] = []
    with suppressed("listing providers an automation could run on"):
        from ...models import get_model_catalog

        for entry in get_model_catalog():
            if not entry.get("connected"):
                continue
            out.append({
                "id": entry.get("id", ""),
                "label": entry.get("name") or entry.get("id", ""),
                "models": [{"id": m.get("id", ""),
                            "label": m.get("name") or m.get("id", "")}
                           for m in (entry.get("models") or [])],
            })
    return out


def _effort_names() -> list[dict[str, str]]:
    """The effort levels, in the words the rest of the app uses for them."""
    with suppressed("listing the effort levels an automation could run at"):
        from ...agents.effort import LEVELS

        return [{"id": str(name), "label": str(name).replace("_", " ").title()}
                for name in LEVELS]
    return []


def _event_sources() -> list[dict[str, str]]:
    """Every app that produces events: its id, its name, and what it produces.

    `kind` is here so the builder can offer only the apps that can actually
    cause the event the user picked — "a calendar event changes" has no
    business listing Gmail, and a menu of fifteen apps where two are possible
    is a menu somebody picks wrongly from.
    """
    from ...automation import sources
    from ...connectors import REGISTRY

    out = []
    for connector, (kind, _trust) in sorted(sources.EVENT_KINDS.items()):
        cls = REGISTRY.get(connector)
        out.append({"id": connector,
                    "label": str(getattr(cls, "label", "") or connector),
                    "kind": kind})
    return out


@router.get("/api/automations/vocabulary")
def vocabulary():
    """What a trigger or condition may say, so the UI builds a form from the
    registry rather than keeping its own list that drifts.

    `triggers` and `conditions` carry the **sentence a person reads** and the
    keys each one needs answered, both declared next to the code that evaluates
    them. That is what lets the builder be a form over the existing schema
    rather than a second description of it: a condition added to the registry
    appears in the form, and one whose meaning changes cannot keep its old
    label.

    `names` is the flat list the older callers use, kept because a list of
    strings is what a test that pins the vocabulary wants to read.
    """
    from ...automation import conditions, sources, triggers
    from ...automation.model import Concurrency
    from ...config import get_settings

    return {
        "triggers": triggers.describe(),
        "conditions": conditions.describe(),
        "fields": sources.condition_fields(),
        "event_kinds": sorted({kind for kind, _ in sources.EVENT_KINDS.values()}),
        # The apps that produce events, each with the name it is called by and
        # the kind of event it makes. Joined here rather than in `sources.py`,
        # which may not import `connectors` — an event kind is the automation
        # layer's idea and "Google Calendar" is the connector's, and the route
        # is where the two are allowed to meet.
        "sources": _event_sources(),
        # How often a sync runs, which is what decides how soon an
        # event-triggered automation notices. The builder says it out loud
        # because the question gets asked — an agent was once asked "how often
        # should I check Gmail, every 15 minutes or every hour?", which is a
        # choice neither it nor the automation has. Reported, not offered:
        # there is no endpoint that changes it, and a control that cannot work
        # is worse than a sentence that is true.
        "sync_minutes": max(1, get_settings().sync_interval_minutes),
        # What an automation may be told to run as. The providers the user has
        # actually connected, and the effort levels that exist — a menu of
        # models this install cannot reach is a menu that 404s when picked.
        "providers": _usable_providers(),
        "efforts": _effort_names(),
        "concurrency": list(Concurrency.ALL),
        "names": {"triggers": triggers.known(),
                  "conditions": conditions.known()},
    }


@router.get("/api/automations/runs")
def recent_runs(limit: int = MAX_RUNS):
    """The newest runs across every automation — the activity feed."""
    return {"runs": store.recent_runs(limit=min(limit, MAX_RUNS))}


@router.get("/api/automations/{automation_id}")
def get_automation(automation_id: str):
    from ...automation import engine

    automation = engine.get_automation(automation_id)
    if automation is None:
        raise HTTPException(404, "no such automation")
    runs = store.runs_for(automation_id, limit=MAX_RUNS)
    return {**_summary(automation, runs), "history": runs}


@router.get("/api/automations/{automation_id}/runs/{run_id}")
def get_run(automation_id: str, run_id: str):
    """One run, whole: what triggered it, what it saw, what it did.

    This is the "why did it do that" screen, and it is why the context snapshot
    is stored rather than rebuilt — the answer has to be the same months later.
    """
    run = store.get_run(run_id)
    if run is None or run["automation_id"] != automation_id:
        raise HTTPException(404, "no such run")
    return {"run": run, "steps": store.steps_for(run_id)}


class AutomationIn(BaseModel):
    """What a user may change. Notably **not** the permission policy — there
    is no such field, because permissions live in one place and it is not here."""

    name: str | None = None
    goal: str | None = None
    agent_id: str | None = None
    instruction: str | None = None
    enabled: bool | None = None
    trigger: dict[str, Any] | None = None
    conditions: list[dict[str, Any]] | None = Field(default=None)
    policy: dict[str, Any] | None = None
    #: How it runs — model, apps, and the two switches. Re-parsed through
    #: `Execution.from_dict` before storing, for the same reason the policy is:
    #: a hand-edited or out-of-date one cannot put a value the engine will not
    #: understand into the database. Every field of it narrows or substitutes;
    #: none of them widen, so this is not the permission hole it looks like.
    execution: dict[str, Any] | None = None


@router.patch("/api/automations/{automation_id}")
def update_automation(automation_id: str, body: AutomationIn):
    """Change an automation. Unknown policy fields fall back to the safe value.

    A policy is re-parsed through `Policy.from_dict` before it is stored, so a
    hand-edited or out-of-date one cannot put a value the engine will not
    understand into the database.
    """
    from ...automation.model import Execution, Policy
    from ...core.routine_store import get_routines

    fields: dict[str, Any] = {}
    data = body.model_dump(exclude_none=True)
    for key in ("name", "goal", "agent_id", "instruction"):
        if key in data:
            fields[key] = data[key]
    if "enabled" in data:
        fields["enabled"] = 1 if data["enabled"] else 0
    if "trigger" in data:
        fields["trigger_json"] = json.dumps(data["trigger"])
    if "conditions" in data:
        fields["conditions_json"] = json.dumps(data["conditions"])
    if "policy" in data:
        fields["policy_json"] = json.dumps(
            Policy.from_dict(data["policy"]).as_dict())
    if "execution" in data:
        fields["execution_json"] = json.dumps(
            Execution.from_dict(data["execution"]).as_dict())

    if not get_routines().set_fields(automation_id, **fields):
        raise HTTPException(404, "no such automation")
    return get_automation(automation_id)


@router.post("/api/automations/{automation_id}/run")
@calls_a_model
def run_automation(automation_id: str):
    """Run it now because a person asked.

    Bypasses the **trigger** and nothing else: conditions are still evaluated,
    the permission gate is still asked, and limits still apply. "Run now" is not
    "run without the rules".
    """
    from ...automation import engine

    result = engine.run_now(automation_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error") or "no such automation")
    return result


@router.post("/api/automations/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """Stop a run. Anything the user starts, they can stop.

    Cancelling does **not** undo what already happened — a sent email is gone,
    and a button that implied otherwise would be a lie. It stops the run from
    doing anything more.
    """
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "no such run")
    if run["state"] in store.TERMINAL_STATES:
        return {"ok": True, "already": run["state"]}
    store.transition(run_id, store.RunState.CANCELLED,
                     reason="you stopped it", outcome="stopped")
    return {"ok": True, "state": str(store.RunState.CANCELLED)}


@router.post("/api/webhooks/{provider}")
async def receive_webhook(provider: str, request: Request):
    """A provider's callback, into the one event pipeline.

    Everything after `webhooks.receive` already existed: the event is
    deduplicated by `core/events`, routed by `automation/router`, and run by the
    same executor a scheduled automation uses. There is no webhook-specific
    path beyond authenticating the caller.

    **Unauthenticated callers are told nothing useful.** A 401 says "bad
    signature" and the reason goes to the log — telling someone which part of
    their forgery was wrong is helping them fix it.

    Returns 202 on a duplicate as well as on a new event: a provider retrying
    because our 200 was slow must be told "I have this" rather than handed an
    error it will retry again.
    """
    from ...automation import engine, webhooks

    body = await request.body()
    delivery = webhooks.Delivery(provider=provider, body=body,
                                 headers=dict(request.headers))
    try:
        event = webhooks.receive(delivery)
    except webhooks.WebhookError as refused:
        log.warning("webhook from %s refused: %s", provider, refused.reason)
        raise HTTPException(refused.status, refused.public) from None

    # The same front door a connector sync and the scheduler use. A webhook that
    # bypassed this would be a second pipeline with its own deduplication, its
    # own concurrency rules and its own bugs.
    outcome = await run_in_threadpool(engine.ingest, event)
    return {"accepted": True, "duplicate": bool(outcome.get("duplicate")),
            "started": len(outcome.get("started") or [])}


@router.post("/api/automations/resume")
@calls_a_model
def resume_runs():
    """Pick up every run left in flight — what the scheduler does on start.

    Exposed because a user who has just approved something should not have to
    wait for the next tick to see it continue.
    """
    from ...automation import engine

    return {"resumed": engine.recover()}
