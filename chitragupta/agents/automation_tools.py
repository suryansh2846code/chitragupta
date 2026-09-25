"""What the agent has already set running, and being able to see it.

An agent could *create* a routine — "every morning, summarise my inbox" — and
then had no idea any existed. It could not list them, pause one, or see that an
action it proposed was sitting in a queue waiting for a tap. So "what automations
do I have running?" was unanswerable by the thing that set them up, and the only
route to a queued action was a desktop notification the user may have missed.

A routine nobody can see is a routine nobody can trust, and that matters most
for the routines that act on their own.

Reading is free. Pausing is free too, and that is a deliberate line: pausing
something the user already authorised takes authority away, which is the safe
direction. *Creating* a routine stays a propose-and-confirm action — it is in
`permissions.NEVER_UNATTENDED`, because a routine that creates routines can
widen its own authority without the user ever seeing it. Deleting is not offered
here at all; pausing is reversible and deleting is not.
"""
from __future__ import annotations

from .results import ToolResult

#: Enough to answer "what do I have running" without spending the turn on it.
MAX_ROWS = 20


def list_routines() -> ToolResult:
    """Every standing automation, and whether it is currently running."""
    # The store, not the feature: `routines` drives an agent turn, so it
    # sits above this package. See `core/routine_store.py`.
    from ..core.routine_store import get_routines

    rows = get_routines().list()
    if not rows:
        return ToolResult("No automations are set up.")
    lines = []
    for r in rows[:MAX_ROWS]:
        state = "running" if r.get("enabled") else "paused"
        trigger = r.get("trigger") or "?"
        when = (f"every {r['interval_min']} min"
                if trigger == "schedule" and r.get("interval_min")
                else "on each new email" if trigger == "new_email" else trigger)
        lines.append(f"- {r.get('name')} — {when}, {state}, runs the "
                     f"{r.get('agent_id')} agent  (id {str(r.get('id'))[:6]})")
    return ToolResult("Automations:\n" + "\n".join(lines))


def pause_routine(routine: str, resume: bool = False) -> ToolResult:
    """Stop an automation running, or start it again.

    Not gated: pausing takes authority away rather than granting it, and the
    user could not stop a routine through an agent at all before this.
    """
    # The store, not the feature: `routines` drives an agent turn, so it
    # sits above this package. See `core/routine_store.py`.
    from ..core.routine_store import get_routines

    needle = (routine or "").strip().lower()
    if not needle:
        return ToolResult.failed("Say which automation, by name or id.")

    store = get_routines()
    rows = store.list()
    match = next((r for r in rows
                  if str(r.get("id", "")).startswith(needle)
                  or needle in str(r.get("name", "")).lower()), None)
    if match is None:
        known = ", ".join(str(r.get("name")) for r in rows[:6]) or "none"
        return ToolResult.failed(
            f"No automation matched '{routine}'. There is: {known}.")

    store.toggle(match["id"], bool(resume))
    verb = "running again" if resume else "paused"
    return ToolResult(f"'{match.get('name')}' is {verb}.")


def list_pending_approvals() -> ToolResult:
    """Actions an unattended agent proposed that are waiting for one tap."""
    from .approvals import pending

    rows = pending()
    if not rows:
        return ToolResult("Nothing is waiting for approval.")
    lines = [f"- {r.get('summary')} — {r.get('reason') or 'waiting'}"
             f"  (id {str(r.get('id'))[:6]})" for r in rows[:MAX_ROWS]]
    return ToolResult(
        "Waiting for the user to approve:\n" + "\n".join(lines)
        + "\n\nThey can approve or dismiss these in Chitragupta. Do not claim any "
          "of them have happened.")


def list_scheduled() -> ToolResult:
    """Actions already confirmed that will fire at a time."""
    from ..scheduled import get_scheduled

    rows = get_scheduled().upcoming(limit=MAX_ROWS)
    if not rows:
        return ToolResult("Nothing is scheduled to fire later.")
    lines = []
    for r in rows:
        what = str(r.get("type", "")).replace("_", " ")
        lines.append(f"- {what} at {r.get('fire_at')}  (id {str(r.get('id'))[:6]})")
    return ToolResult("Scheduled:\n" + "\n".join(lines))
