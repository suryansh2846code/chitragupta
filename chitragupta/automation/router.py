"""An event arrives. Which automations should start, and may they?

Four gates, cheapest first, and the order is the performance design:

1. **Duplicate?** One indexed lookup. A redelivered webhook stops here, before
   any automation has been considered.
2. **Too deep?** Lineage, from the event itself. An A → B → A chain stops here.
3. **Trigger match?** In-memory, no IO, per enabled automation.
4. **Concurrency?** One query per *matching* automation, not per automation.

Conditions are deliberately **not** here. They may need a model, and a model
call belongs after the run exists — so the user can see in history that their
condition was evaluated and what it decided, rather than the automation
silently not running.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..core import automation_store as store
from ..core import events as event_log
from ..core.events import MAX_EVENT_DEPTH, Event
from ..log import get_logger
from . import triggers
from .model import Automation, Concurrency

log = get_logger(__name__)


@dataclass
class Routed:
    """What one event caused. Everything it *did not* cause, and why."""

    event: Event
    started: list[str] = field(default_factory=list)
    duplicate: bool = False
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def note(self, automation: Automation, why: str) -> None:
        self.skipped.append({"automation_id": automation.id,
                             "name": automation.name, "why": why})

    def as_dict(self) -> dict[str, Any]:
        return {"event": self.event.kind, "source": self.event.source,
                "duplicate": self.duplicate, "started": list(self.started),
                "skipped": self.skipped}


def route(event: Event, automations: list[Automation], *,
          now: Callable[[], datetime] | None = None) -> Routed:
    """Decide what this event starts. Creates runs; does not execute them.

    Separated from execution on purpose: creating the run is the durable part
    and must happen in one quick pass over all automations, so a slow first run
    cannot delay the others from even being recorded.
    """
    clock = now or (lambda: datetime.now(UTC))
    out = Routed(event=event)

    if event.depth > MAX_EVENT_DEPTH:
        log.warning("event %s refused at depth %d (from run %s)",
                    event.kind, event.depth, event.caused_by_run)
        out.duplicate = False
        out.skipped.append({"automation_id": "", "name": "",
                            "why": f"event chain is {event.depth} deep"})
        return out

    if event_log.is_duplicate(event):
        out.duplicate = True
        return out

    for automation in automations:
        if not automation.enabled:
            continue
        match = triggers.matches(automation, event, now=clock())
        if not match.matched:
            if match.detail:
                out.note(automation, match.detail)
            continue

        # A run started by an automation's own event is the loop. Refusing the
        # *self* case explicitly rather than relying on depth alone, because
        # depth only bounds how long it takes to notice.
        if event.caused_by_automation and event.caused_by_automation == automation.id:
            out.note(automation, "it would have triggered itself")
            continue

        if store.lineage_depth(event.correlation_id) >= \
                automation.policy.limits.max_lineage_runs:
            out.note(automation, "too many runs descend from one cause")
            continue

        blocked = _concurrency_block(automation, event)
        if blocked:
            out.note(automation, blocked)
            continue

        limits = automation.policy.limits
        deadline = (clock() + timedelta(
            seconds=limits.max_duration_seconds)).isoformat()
        run = store.create_run(
            automation.id, automation_name=automation.name,
            trigger=event.as_dict(),
            max_attempts=automation.policy.retry.max_attempts,
            deadline_at=deadline,
            correlation_id=event.correlation_id,
            parent_run_id=event.caused_by_run,
            depth=event.depth)
        out.started.append(run["id"])
        log.info("automation %s started run %s from %s",
                 automation.name, run["id"][:8], event.kind)
    return out


def _concurrency_block(automation: Automation, event: Event) -> str:
    """Why this automation must not start another run right now, or "".

    `ONE_ACTIVE_RUN` is the default because it is the answer that cannot cause a
    duplicate side effect, and an automation that *can* safely run in parallel
    is a claim only the person who wrote it can make.

    Deliberately per-automation. A global lock would be simpler and would mean
    one slow Gmail automation delays an unrelated calendar one — the exact
    "global serialization that destroys unrelated automation performance" worth
    avoiding.
    """
    policy = automation.policy.concurrency
    if policy == Concurrency.ALLOW_PARALLEL:
        return ""
    live = store.live_runs(automation.id)
    if not live:
        return ""
    if policy == Concurrency.ONE_ACTIVE_RUN:
        return f"a run is already active ({live[0]['id'][:8]})"
    if policy == Concurrency.QUEUE:
        # Queued runs are simply created and picked up by the sweep when the
        # active one settles; the store is the queue and `live_runs` is ordered
        # by creation. Nothing to block.
        return ""
    if policy == Concurrency.COALESCE:
        # Fold this event into the run already going. It sees the burst rather
        # than the first of it, which is the point — but it is not the default,
        # because it changes what the automation observes.
        active = live[0]
        pending = list(active.get("trigger", {}).get("coalesced") or [])
        pending.append({"kind": event.kind, "subject": event.subject,
                        "data": event.data})
        merged = dict(active.get("trigger") or {})
        merged["coalesced"] = pending[:50]
        store.update_run(active["id"], trigger=merged)
        return f"folded into the active run ({active['id'][:8]})"
    return ""
