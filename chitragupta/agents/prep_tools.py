"""Walking into a meeting knowing what it is about.

*"Prep me for my next meeting"* is one question and five lookups: which meeting,
who is coming, what was said last time, what is still open with those people,
and what the user promised them. An agent can make all five calls — and the
one it forgets is always the same one, because the model stops as soon as it
has enough to write a paragraph that *sounds* prepared.

So the assembly is a tool rather than five instructions. One call, one brief.

Three things this is careful about, and each is a way the brief would be worse
than nothing:

* **The next meeting is the next one that has not started.** Today's calendar
  read at 4pm is mostly history, and a brief for a meeting that finished at
  eleven is not a mistake the user catches — it reads perfectly well.
* **An empty attendee list is not a meeting alone.** Plenty of synced events
  carry no attendees at all. Saying "nobody else is coming" would be inventing
  a fact; saying the calendar does not list anyone is the truth.
* **Silence about somebody is not the absence of history.** If the brain holds
  nothing on an attendee, the brief says so per person rather than leaving
  them off — a name missing from a list reads as "nothing to know", and the
  user cannot tell that apart from "we never synced their email".

Reads the brain, never the network. A prep brief that waits on an OAuth
refresh is one the user reads after the meeting.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..log import get_logger, suppressed
from .results import ToolResult

if TYPE_CHECKING:                                # pragma: no cover
    from ..core.store import Memory

log = get_logger(__name__)

#: How far ahead to look for "the next meeting". A fortnight: far enough that
#: a quiet week still answers, near enough that the answer is recognisably
#: "next" rather than a thing in October.
DEFAULT_HORIZON_DAYS = 14

#: Per attendee, and per topic. A brief nobody finishes reading is a brief
#: that did not help.
CONTEXT_PER_PERSON = 3

_ATTENDEE_LINE = re.compile(r"^With:\s*(.+)$", re.MULTILINE)
_WHEN_LINE = re.compile(r"^When:\s*(.+?)\s*(?:→|->)", re.MULTILINE)
_TITLE_LINE = re.compile(r"^Event:\s*(.+)$", re.MULTILINE)

CALENDAR_SOURCES = ("gcal", "apple_calendar")


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_start(raw: str) -> datetime | None:
    """An ISO start, or None. All-day events carry a date and no time."""
    raw = (raw or "").strip()
    if not raw:
        return None
    with suppressed("reading the start time of a calendar event"):
        stamp = datetime.fromisoformat(raw)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
    return None


def _upcoming(horizon_days: int) -> list[tuple[datetime, Memory]]:
    """Every synced calendar event that has not started yet, soonest first."""
    from ..brain import get_brain

    today = _now().date()
    hits = get_brain().store.search(
        "calendar event meeting appointment",
        limit=120,
        date_start=today.isoformat(),
        date_end=(today + timedelta(days=horizon_days)).isoformat(),
    )

    out: list[tuple[datetime, Memory]] = []
    for hit in hits:
        memory = hit.memory
        if (memory.source or "") not in CALENDAR_SOURCES:
            continue
        start = None
        with suppressed("reading a calendar event's metadata"):
            start = _parse_start(str((memory.metadata or {}).get("start") or ""))
        if start is None:
            # Fall back to the text, then give up rather than guessing: an
            # event we cannot time cannot be called "next".
            found = _WHEN_LINE.search(memory.text or "")
            start = _parse_start(found.group(1)) if found else None
        if start is None or start <= _now():
            continue
        out.append((start, memory))
    out.sort(key=lambda pair: pair[0])
    return out


def _attendees(text: str) -> list[str]:
    found = _ATTENDEE_LINE.search(text or "")
    if not found:
        return []
    return [part.strip() for part in found.group(1).split(",") if part.strip()]


def _title(memory: Memory) -> str:
    raw = str(getattr(memory, "title", "") or "").strip()
    if raw:
        return raw
    found = _TITLE_LINE.search(memory.text or "")
    return found.group(1).strip() if found else "the meeting"


def _handles(person: str) -> list[str]:
    """The ways this person might be written in a memory.

    `rahul@work.test` → `rahul@work.test`, `rahul`. A display name splits into
    its parts. Anything under three characters is dropped: a two-letter token
    matches half the brain.
    """
    raw = (person or "").strip().lower()
    if not raw:
        return []
    out = [raw]
    if "@" in raw:
        out.append(raw.split("@", 1)[0])
    for part in re.split(r"[^a-z0-9]+", raw.split("@", 1)[0]):
        if len(part) >= 3:
            out.append(part)
    return [h for h in dict.fromkeys(out) if len(h) >= 3]


def _history(query: str, *, must_mention: list[str] | None = None,
             exclude_sources=CALENDAR_SOURCES) -> list[str]:
    """What the brain holds about a person or a subject.

    Calendar entries are excluded: answering "what do we know about Rahul"
    with "you have a meeting with Rahul" is the tool reading its own input
    back to the user.

    **`must_mention` is a correctness guard, not a filter for tidiness.**
    Recall is semantic, so asking about `dana@work.test` cheerfully returns a
    memory about Rahul that happens to be about the same project — and a brief
    that prints it under *"what you know about Dana"* has invented a fact
    about a person the user is about to be in a room with. So for a named
    person, the memory has to actually name them; a loose match is dropped
    rather than re-labelled.

    Not applied to a topic search: a memory about "the budget review" may
    reasonably never use those words, and nobody is misattributed by it.
    """
    from ..brain import get_brain

    lines: list[str] = []
    seen: set[str] = set()
    with suppressed("recalling context for a meeting brief"):
        for hit in get_brain().store.search(query, limit=CONTEXT_PER_PERSON * 4):
            if (hit.memory.source or "") in exclude_sources:
                continue
            text = " ".join((hit.memory.text or "").split())
            if not text:
                continue
            if must_mention and not any(h in text.lower() for h in must_mention):
                continue
            trimmed = text[:200]
            # The same fact reached by two queries is one fact. Repeating it
            # makes a short brief look long and a long one unreadable.
            key = trimmed.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(trimmed)
            if len(lines) >= CONTEXT_PER_PERSON:
                break
    return lines


def _open_with(names: list[str]) -> list[str]:
    """Anything still open that names one of these people."""
    from ..brain import get_brain

    out: list[str] = []
    with suppressed("reading open loops for a meeting brief"):
        for loop in get_brain().get_open_loops(status="open") or []:
            blob = " ".join([
                str(loop.get("description") or ""),
                str((loop.get("metadata") or {}).get("who") or ""),
            ]).lower()
            if any(n.lower() in blob for n in names if n):
                due = loop.get("due_at")
                out.append(f"- {loop.get('description')}"
                           + (f" (due {str(due)[:10]})" if due else ""))
    return out


def _tasks_with(names: list[str]) -> list[str]:
    """What the USER owes that mentions one of them."""
    from ..tasks import get_tasks

    out: list[str] = []
    with suppressed("reading tasks for a meeting brief"):
        for task in get_tasks().list():
            title = str(task.get("title") or "")
            if any(n.lower() in title.lower() for n in names if n):
                out.append(f"- {title}"
                           + (f" (due {task['due']})" if task.get("due") else ""))
    return out


def meeting_prep(which: str = "next",
                 horizon_days: int = DEFAULT_HORIZON_DAYS) -> ToolResult:
    """A brief for the user's next meeting: who, what, and what is open.

    `which` is reserved for naming a specific meeting later; today any value
    means the next one that has not started.
    """
    horizon = max(1, min(60, int(horizon_days or DEFAULT_HORIZON_DAYS)))

    from ..connectors import get_connector

    connected = []
    for source in CALENDAR_SOURCES:
        with suppressed("checking whether a calendar is connected"):
            conn = get_connector(source)
            if conn is not None and conn.is_configured()[0]:
                connected.append(source)

    upcoming = _upcoming(horizon)
    if not upcoming:
        if not connected:
            # Never "you have no meetings" — we have not looked at a calendar.
            return ToolResult(
                "No calendar is connected, so I cannot see your meetings. The "
                "Connectors panel can link Google Calendar or Apple Calendar.")
        return ToolResult(
            f"Nothing is on your calendar in the next {horizon} days. If that "
            "looks wrong, the calendar may not have synced recently.")

    start, memory = upcoming[0]
    title = _title(memory)
    people = _attendees(memory.text or "")

    when = start.astimezone().strftime("%a %d %b, %-I:%M %p")
    parts = [f"NEXT: “{title}” — {when}"]

    if not people:
        # An event with no attendee list is common and is not a solo meeting.
        parts.append("The calendar does not list who is coming, so I could "
                     "not look anybody up. The invitation itself may say.")
    else:
        parts.append("WHO: " + ", ".join(people))
        lines = []
        for person in people:
            found = _history(person, must_mention=_handles(person))
            if found:
                lines.append(f"{person}:\n" + "\n".join(f"  - {f}" for f in found))
            else:
                # Said per person. A name simply left off reads as "nothing to
                # know", which is not the same as "we have nothing synced".
                lines.append(f"{person}: nothing in the brain about them yet.")
        parts.append("WHAT YOU KNOW:\n" + "\n\n".join(lines))

    about = _history(title)
    if about:
        parts.append(f"ON “{title}”:\n" + "\n".join(f"- {a}" for a in about))

    names = [*people, title]
    waiting = _open_with(names)
    if waiting:
        parts.append("STILL OPEN (you are waiting on these):\n"
                     + "\n".join(waiting))
    owed = _tasks_with(names)
    if owed:
        parts.append("YOU OWE:\n" + "\n".join(owed))

    if len(upcoming) > 1:
        after = upcoming[1][0].astimezone().strftime("%a %d %b, %-I:%M %p")
        parts.append(f"(After that: “{_title(upcoming[1][1])}” — {after}.)")

    return ToolResult("\n\n".join(parts))
