"""Routines — automations that run an agent on a trigger.

Creating a routine pre-authorizes the *routine*. It does not pre-authorize
whoever wrote the text the routine happens to read: a `new_email` trigger hands
the agent content from a stranger, and an instruction hidden in that content
reaches an agent that can propose sending mail. So actions are filtered through
`agents/approvals.py::run_or_queue` — reading and note-taking run freely,
anything that leaves the machine needs a recipient the user has permitted, and
everything else waits for one tap.

Triggers:
  • schedule   — every N minutes.
  • daily      — at a wall-clock time, on chosen days ("weekdays at 8:00 AM").
  • new_email  — when new email(s) arrive during a sync.

`daily` exists because `schedule` could not say it. "Every morning" meant
`interval_min=1440`, which fires 24 hours after whenever you happened to create
it and then drifts by however long each run takes — so the morning brief
arrives at 8:04, then 8:11, then some time in the afternoon. A routine people
actually want is a wall-clock one, and the schema had no way to express it.

Guardrails: routines are user-created, disable-able, permission-gated for
outbound actions, and their runs are logged + notified.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from .core.routine_store import (  # noqa: F401  (re-exported)
    RoutineStore,
    get_routines,
)
from .core.schedule import (  # noqa: F401  (re-exported)
    WEEKDAYS,
    _clock,
    describe_schedule,
    parse_days,
    parse_time,
)
from .log import suppressed

log = logging.getLogger("chitragupta.routines")



# ── running routines ─────────────────────────────────────────────────────
def _new_emails_since(iso: str | None) -> list:
    """Gmail memories created after `iso` (the routine's last run)."""
    from .brain import get_brain
    store = get_brain().store
    return store._conn.execute(
        "SELECT title, text, created_at FROM memories WHERE source='gmail' "
        "AND created_at > ? ORDER BY created_at DESC LIMIT 10",
        (iso or "1970-01-01",)).fetchall()


def run_routine(r: dict, trigger_context: str = "") -> dict:
    """Run one routine: the agent acts on the instruction (+ any trigger
    context); actions it proposes are auto-executed."""
    from .actions import parse_actions
    from .agents import run_turn
    from .agents.approvals import run_or_queue
    from .agents.permissions import as_unattended
    from .notify import desktop_notify

    prompt = r["instruction"]
    if trigger_context:
        prompt += "\n\n" + trigger_context
    try:
        # The whole turn, not just the actions it proposes. Gating proposals was
        # enough while a routine's tools could only read and take notes — the
        # only thing that reached the world was the action. A browser that can
        # click is not like that: the tool *is* the thing that reaches the
        # world, and by the time an action would have been proposed the click
        # has already happened.
        with as_unattended():
            res = run_turn(r["agent_id"], prompt)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}

    outcomes = []
    for a in parse_actions(res.reply):
        a["params"]["agent_id"] = r["agent_id"]
        # Not `run_now`. A routine is pre-authorisation for the *routine*, and
        # that reasoning holds right up until the trigger is `new_email` — at
        # which point the text driving the agent was written by a stranger, and
        # an action that leaves the machine needs a recipient the user named.
        out = run_or_queue(a["type"], a["params"], routine_id=r["id"],
                           routine_name=r["name"], agent_id=r["agent_id"])
        outcomes.append(out.get("detail") or out.get("error") or "")
    summary = "; ".join(o for o in outcomes if o) or "ran (no action)"
    desktop_notify(f"◆ Chitragupta · {r['name']}", summary)
    return {"ok": True, "detail": summary}


def daily_due(routine: dict, now: datetime) -> bool:
    """Has this wall-clock routine's time come round today, unanswered?

    Three things it has to get right, and the third is the one that bites:

    * **The day.** `weekday()` is Monday-zero, which is why `WEEKDAYS` is
      written in that order — the name maps to the index by position.
    * **Local wall clock, not UTC.** "8am" means eight in the morning where the
      user is, so today's target is built from the local date and compared in
      UTC only at the end. Building it in UTC would move the morning brief by
      the timezone offset, and again twice a year.
    * **Late is better than never.** A laptop asleep at 08:00 and opened at
      11:00 still fires, because the user wanted the morning brief and did not
      get one. It fires *once*: `last_run` at or after today's target is what
      says today has been answered, so the next sweep five minutes later does
      not run it again.
    """
    at_time = routine.get("at_time") or ""
    if not at_time:
        return False
    try:
        hour, minute = (int(x) for x in at_time.split(":"))
    except ValueError:
        return False

    here = now.astimezone()
    days = routine.get("days") or ""
    if days and WEEKDAYS[here.weekday()] not in days.split(","):
        return False

    target = here.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if here < target:
        return False

    last = routine.get("last_run")
    if not last:
        return True
    try:
        ran = datetime.fromisoformat(last)
    except ValueError:                     # pragma: no cover - defensive
        return True
    if ran.tzinfo is None:
        ran = ran.replace(tzinfo=UTC)
    return ran < target.astimezone(UTC)


def sweep(new_email_count: int = 0) -> None:
    """Fire whatever is due. Kept as the name the scheduler already calls.

    **The `if/elif` chain that used to be here is gone.** It matched three
    hardcoded trigger names, which meant every new connector event was another
    branch in the thing that is supposed to be generic — and `new_email_count`
    is a count, so a redelivered message could not be recognised as one already
    handled.

    Now the clock is an event like any other: `automation.engine.tick()` emits
    `schedule.tick`, the router asks each automation's registered trigger
    whether it matches, and a run is created with durable state. The
    parameter survives because callers pass it; it is no longer read, because
    "how many emails arrived" is answered by the events themselves.

    See [`docs/AUTOMATION.md`](../docs/AUTOMATION.md).
    """
    from .automation import engine
    with suppressed("firing due automations"):
        engine.tick()
