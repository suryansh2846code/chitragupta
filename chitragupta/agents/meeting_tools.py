"""Finding a time that actually works — for the user and for somebody else.

Rung 3 for the calendar is proposing a *specific* slot. An agent that answers
*"what time next week suits you?"* has handed the job back: the user asked to
have a meeting arranged, not to be consulted about arranging one.

Two things make that honest rather than merely confident.

**Whose calendar we could actually read.** Google answers an unreadable
calendar with an empty busy list and an `errors` entry beside it, so anything
reading only `busy` sees a person with a completely clear week. Calendar
sharing is normal inside one Workspace domain and rare outside it — which
means the unreadable case is the *common* one for exactly the people a user
needs to arrange something with. So a slot is never described as working for
somebody we could not see; it is described as working for the user, and the
others are named as unchecked.

**Working hours.** A gap at 03:00 is free and is not a time to suggest to
anybody. The window is deliberately a constant rather than a setting: a
setting nobody has filled in is a worse default than a stated one, and the
agent says which hours it used so the user can disagree in words.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from ..core.dateparse import parse_date_range
from ..log import get_logger, suppressed
from .results import ToolResult

log = get_logger(__name__)

#: When it is reasonable to put something in somebody's day, local time.
WORK_START, WORK_END = time(9, 0), time(18, 0)

#: Monday-zero, matching `datetime.weekday()`.
WORK_DAYS = (0, 1, 2, 3, 4)

#: How many candidates to offer. One reads as a demand and five as a survey;
#: three is enough that "none of those" is a short conversation.
MAX_SLOTS = 3

#: Nothing shorter is a meeting and nothing longer fits this tool's promise.
MIN_MINUTES, MAX_MINUTES = 5, 8 * 60


def _gcal():
    from ..connectors import get_connector

    with suppressed("reaching Google Calendar"):
        connector = get_connector("gcal")
        ready, _ = connector.is_configured()
        if ready and callable(getattr(connector, "free_busy", None)):
            return connector
    return None


def _as_local(stamp: str) -> datetime | None:
    with suppressed("reading a busy block"):
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone()
    return None


def _work_windows(start_day: str, end_day: str) -> list[tuple[datetime, datetime]]:
    """Each working day in the range, as a local (from, to) pair."""
    windows = []
    with suppressed("laying out the working days in a range"):
        first = datetime.fromisoformat(start_day).date()
        last = datetime.fromisoformat(end_day).date()
        here = datetime.now().astimezone().tzinfo
        day = first
        while day <= last:
            if day.weekday() in WORK_DAYS:
                windows.append((
                    datetime.combine(day, WORK_START, tzinfo=here),
                    datetime.combine(day, WORK_END, tzinfo=here)))
            day += timedelta(days=1)
    return windows


def _free_slots(windows: list[tuple[datetime, datetime]],
                busy: list[tuple[datetime, datetime]],
                minutes: int) -> list[tuple[datetime, datetime]]:
    """Gaps long enough to hold the meeting, earliest first.

    Busy blocks are merged before subtracting. Two overlapping meetings left
    separate would leave a phantom gap between them — the sliver where one
    ended after the next began — and offering that as free is offering a time
    the user is already in something.
    """
    merged: list[list[datetime]] = []
    for block_start, block_end in sorted(busy):
        if merged and block_start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], block_end)
        else:
            merged.append([block_start, block_end])

    need = timedelta(minutes=minutes)
    now = datetime.now().astimezone()
    found = []
    for window_start, window_end in windows:
        cursor = max(window_start, now)      # never offer a slot in the past
        for block_start, block_end in merged:
            if block_end <= cursor or block_start >= window_end:
                continue
            if block_start - cursor >= need:
                found.append((cursor, cursor + need))
            cursor = max(cursor, block_end)
        if window_end - cursor >= need:
            found.append((cursor, cursor + need))
    return found


def _clock(when: datetime) -> str:
    return when.strftime("%a %d %b, %-I:%M %p")


def find_time(with_people: str = "", when: str = "next week",
              minutes: int = 30) -> ToolResult:
    """Concrete slots for a meeting, and who they were actually checked against.

    `with_people` is a comma-separated list of email addresses; leave it empty
    to find time in the user's own day. `when` is any period `calendar_lookup`
    understands.
    """
    window = parse_date_range((when or "next week").strip())
    if not window:
        return ToolResult.failed(
            f"I could not read '{when}' as a period. Try 'next week', "
            "'tomorrow', or a date like 2026-09-28.")
    start_day, end_day = window

    # `0` and `None` both mean "they did not say", which is half an hour — not
    # a five-minute meeting. A *negative* number is a model getting it wrong
    # and does get clamped.
    length = max(MIN_MINUTES, min(int(minutes or 30), MAX_MINUTES))
    others = [e.strip() for e in (with_people or "").split(",") if e.strip()]

    gcal = _gcal()
    if gcal is None:
        return ToolResult.failed(
            "Google Calendar is not connected, so I cannot see when anybody "
            "is free.")

    windows = _work_windows(start_day, end_day)
    if not windows:
        return ToolResult(f"There are no working days in {when}.")

    answer = gcal.free_busy(
        ["primary", *others],
        windows[0][0].isoformat(), windows[-1][1].isoformat())
    busy_by: dict = answer.get("busy") or {}
    unreadable = [e for e in (answer.get("unreadable") or []) if e != "primary"]

    if "primary" not in busy_by:
        return ToolResult.failed(
            "I could not read your own calendar, so anything I suggested "
            "would be a guess. Try reconnecting Google under Connectors.")

    blocks: list[tuple[datetime, datetime]] = []
    checked = []
    for email, spans in busy_by.items():
        if email != "primary":
            checked.append(email)
        for raw_start, raw_end in spans:
            first, second = _as_local(raw_start), _as_local(raw_end)
            if first and second:
                blocks.append((first, second))

    slots = _free_slots(windows, blocks, length)
    if not slots:
        return ToolResult(
            f"Nothing {length} minutes long is free in {when} "
            f"({WORK_START:%H:%M}–{WORK_END:%H:%M}, weekdays)"
            + (f", counting {', '.join(checked)}." if checked else "."))

    lines = [f"{length}-minute slots in {when} "
             f"({WORK_START:%H:%M}–{WORK_END:%H:%M} weekdays):"]
    lines += [f"- {_clock(a)}  →  {b.isoformat()}" for a, b in slots[:MAX_SLOTS]]
    lines.append("")
    lines.append("Checked against: your calendar"
                 + (", " + ", ".join(checked) if checked else ""))
    if unreadable:
        # Named, and named as a limit rather than as a result. "Rahul is free"
        # when Rahul's calendar is not shared is the sentence this tool exists
        # to stop an agent from saying.
        lines.append(
            "NOT checked (their calendar is not shared with you): "
            + ", ".join(unreadable)
            + ". Offer these times, do not assert they are free for them.")
    return ToolResult("\n".join(lines))
