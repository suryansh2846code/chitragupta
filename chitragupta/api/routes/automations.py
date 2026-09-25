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

from fastapi import APIRouter, HTTPException
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
        "next_run": upcoming or automation.next_run,
        "last_run": automation.last_run,
        "last_outcome": (recent or {}).get("outcome", ""),
        "last_state": (recent or {}).get("state", ""),
        "runs": len(runs),
        "waiting": len(waiting),
    }


@router.get("/api/automations")
def list_automations():
    """Every automation, with enough to render the list screen."""
    from ...automation import engine

    out = []
    for automation in engine.list_automations():
        out.append(_summary(automation, store.runs_for(automation.id, limit=10)))
    return {"automations": out}


@router.get("/api/automations/vocabulary")
def vocabulary():
    """What a trigger or condition may say, so the UI builds a form from the
    registry rather than keeping its own list that drifts."""
    from ...automation import conditions, sources, triggers

    return {
        "triggers": triggers.known(),
        "conditions": conditions.known(),
        "event_kinds": sorted({kind for kind, _ in sources.EVENT_KINDS.values()}),
        "concurrency": list(__import__(
            "chitragupta.automation.model", fromlist=["x"]).Concurrency.ALL),
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


@router.patch("/api/automations/{automation_id}")
def update_automation(automation_id: str, body: AutomationIn):
    """Change an automation. Unknown policy fields fall back to the safe value.

    A policy is re-parsed through `Policy.from_dict` before it is stored, so a
    hand-edited or out-of-date one cannot put a value the engine will not
    understand into the database.
    """
    from ...automation.model import Policy
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


@router.post("/api/automations/resume")
@calls_a_model
def resume_runs():
    """Pick up every run left in flight — what the scheduler does on start.

    Exposed because a user who has just approved something should not have to
    wait for the next tick to see it continue.
    """
    from ...automation import engine

    return {"resumed": engine.recover()}
