"""What you are waiting on, and whether it has arrived.

An open loop saying *"waiting on Rahul"* is true from the moment it is written
until somebody deletes it by hand. Nothing in this app ever went and looked to
see whether Rahul replied — so a week later the user gets chased about a thing
that was settled on Tuesday, which is worse than not being chased at all. A
follow-up list nobody can trust is a follow-up list nobody reads.

So this asks the thread. `create_followup` stores the `thread_id` beside the
commitment, and `awaiting_reply` reads each one back:

* somebody else wrote last → **answered**, and the loop is closed on the spot
* we wrote last → still waiting, with how many days
* the thread cannot be read → **unknown**, and it is reported as unknown

That last one is the case worth being careful about. Unknown is not "no reply":
a Gmail outage that reads as silence turns into a round of chasing emails to
people who already answered, sent in the user's name. Unknown says so and is
left alone.

Closing happens *here*, as a side effect of looking, rather than in a
background sweep. The check is the only thing that knows the answer, and a
loop that closes the moment anybody asks about it is one that cannot be stale
when it is read.
"""
from __future__ import annotations

from ..log import get_logger, suppressed
from .results import ToolResult

log = get_logger(__name__)

#: Past this, a thread with no answer is worth raising. Below it, chasing reads
#: as impatience — most people answer email within two working days and a
#: reminder that arrives sooner is the app being rude on the user's behalf.
DEFAULT_STALE_DAYS = 3


def _gmail():
    from ..connectors import get_connector

    with suppressed("reaching Gmail to check a thread"):
        connector = get_connector("gmail")
        ready, _ = connector.is_configured()
        if ready and callable(getattr(connector, "thread_reply_state", None)):
            return connector
    return None


def _due_verdict(due_at: object) -> bool | None:
    """Has a date the user named passed? `None` when they named none.

    Three answers rather than two, so the caller can tell "they said chase by
    now" from "they said nothing" — those are the same *action* today and
    different sentences, and the sentence is what makes the list readable.
    """
    if not due_at:
        return None
    from datetime import UTC, datetime

    with suppressed("reading the date a follow-up was due"):
        when = datetime.fromisoformat(str(due_at))
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return datetime.now(UTC) >= when
    return None


def _day(due_at: object) -> str:
    from datetime import datetime

    with suppressed("naming the day a follow-up is due"):
        return datetime.fromisoformat(str(due_at)).strftime("%a %b %d")
    return str(due_at)


def _tracked() -> list[dict]:
    """Open follow-ups, newest first, whichever way they were created."""
    from ..brain import get_brain

    loops = get_brain().get_open_loops(status="open") or []
    return [loop for loop in loops
            if (loop.get("metadata") or {}).get("thread_id")
            or loop.get("source") == "followup"]


def awaiting_reply(stale_days: int = DEFAULT_STALE_DAYS) -> ToolResult:
    """Who owes the user an answer, and for how long.

    Checks every tracked follow-up against its thread, closes the ones that
    have been answered, and reports what is left. `stale_days` only decides
    which are called out as worth chasing — everything still open is listed,
    because "nothing is overdue" and "you are waiting on nobody" are different
    answers and a person asking this wants to tell them apart.
    """
    from ..brain import get_brain

    tracked = _tracked()
    if not tracked:
        return ToolResult("You are not waiting on anybody that I know of.")

    gmail = _gmail()
    brain = get_brain()
    stale: list[str] = []
    waiting: list[str] = []
    closed: list[str] = []
    unknown: list[str] = []

    for loop in tracked:
        meta = loop.get("metadata") or {}
        who = meta.get("who") or ""
        about = loop.get("description") or "something"
        thread_id = meta.get("thread_id") or ""

        state = {"answered": None, "waiting_days": 0}
        if gmail is not None and thread_id:
            with suppressed("checking whether a thread was answered"):
                state = gmail.thread_reply_state(thread_id)

        if state.get("answered") is True:
            with suppressed("closing a follow-up that was answered"):
                brain.complete_open_loop(loop["id"])
            closed.append(f"- {about} — {who or 'they'} replied.")
            continue
        if state.get("answered") is None:
            # Not "no reply". See the module docstring: silence we could not
            # verify must never become a chase sent in the user's name.
            unknown.append(f"- {about} (id {loop['id'][:6]})"
                           + (" — no thread to check" if not thread_id
                              else " — could not read that thread"))
            continue

        days = int(state.get("waiting_days") or 0)
        line = (f"- {about}" + (f" — {who}" if who else "")
                + f", {days} day(s) with no reply"
                + f"  (thread {thread_id}, id {loop['id'][:6]})")

        # A date the user named beats the default. "Chase this in two days if
        # nothing happens" is a decision about THIS thread, and a global
        # `stale_days` that overrode it would mean the thing they asked for
        # either happened early or not at all.
        due = _due_verdict(loop.get("due_at"))
        if due is True:
            stale.append(line + "  · you asked to chase this by now")
        elif due is False:
            waiting.append(line + f"  · you said chase after {_day(loop['due_at'])}")
        else:
            (stale if days >= stale_days else waiting).append(line)

    parts = []
    if stale:
        # Both reasons in the header, because both put lines in this bucket and
        # a heading that said "3+ days" over a one-day-old item the user asked
        # to chase today would be describing the wrong rule.
        parts.append(f"WORTH CHASING (no reply after {stale_days} days, "
                     "or a date you set):\n" + "\n".join(stale))
    if waiting:
        parts.append("STILL RECENT, leave them be:\n" + "\n".join(waiting))
    if unknown:
        parts.append("COULD NOT CHECK — do not chase these:\n"
                     + "\n".join(unknown))
    if closed:
        parts.append("ANSWERED since you asked, now closed:\n"
                     + "\n".join(closed))
    if not parts:
        return ToolResult("Everything you were waiting on has been answered.")
    return ToolResult("\n\n".join(parts))
