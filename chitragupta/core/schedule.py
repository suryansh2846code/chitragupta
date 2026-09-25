"""The vocabulary of a schedule: parsing one, and saying it back.

Pure functions over strings — no store, no settings, no clock beyond the one
the caller passes in. They live in `core/` because two layers need them and
neither should have to import the other:

* `routines.py` builds and runs schedules;
* `agents/approvals.py` has to put **when a routine will run** on the card,
  because that is the decision being approved. "New automation 'Morning brief'"
  asks the user to approve a schedule they were never shown, and a routine that
  fires at the wrong hour is discovered by the thing it did at that hour.

With these defined in `routines.py`, approvals imported it — and `routines`
imports `approvals` to queue a run. That pair was load-bearing in a nineteen-
module cycle spanning `agents/`, `actions.py` and `routines.py`.

`routines` re-exports all four names, so existing imports are unchanged.
"""
from __future__ import annotations

#: Weekdays as `datetime.weekday()` orders them, so a name maps to an index by
#: position and nothing has to keep a second table in step.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_DAY_ALIASES = {
    "weekday": "mon,tue,wed,thu,fri", "weekdays": "mon,tue,wed,thu,fri",
    "weekend": "sat,sun", "weekends": "sat,sun",
    "daily": "", "everyday": "", "every day": "", "all": "",
}


def parse_days(raw: object) -> str:
    """A day list the store can hold, from whatever the model or form sent.

    Accepts the words people use ("weekdays"), full names ("Monday"), and a
    list. Anything unrecognised is dropped rather than guessed at — a routine
    that runs on the wrong days is worse than one that runs on all of them,
    and "" (every day) is the honest fallback when nothing parsed.
    """
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        text = str(raw or "").strip().lower()
        if text in _DAY_ALIASES:
            return _DAY_ALIASES[text]
        parts = text.replace("/", ",").replace(" ", ",").split(",")

    found = []
    for part in parts:
        key = part.strip().lower()[:3]
        if key in WEEKDAYS and key not in found:
            found.append(key)
    # Stored in week order, never in the order they were typed, so two routines
    # on the same days compare equal and render the same.
    return ",".join(d for d in WEEKDAYS if d in found)


def parse_time(raw: object) -> str:
    """`"HH:MM"` from "8am", "08:00", "20:30", or "" if it is not a time.

    Deliberately small. `reminders.parse_when` understands "tomorrow at 3" and
    that is the wrong question here — a daily routine has no date, only a time
    of day, and handing it a parser that returns one is how "every morning"
    becomes a single reminder for tomorrow.
    """
    import re as _re

    text = str(raw or "").strip().lower().replace(".", ":")
    if not text:
        return ""
    found = _re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", text)
    if not found:
        return ""
    hour = int(found.group(1))
    minute = int(found.group(2) or 0)
    suffix = found.group(3)
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


def describe_schedule(routine: dict) -> str:
    """When this runs, in the words a person would use."""
    trigger = routine.get("trigger")
    if trigger == "new_email":
        return "on every new email"
    if trigger == "daily" and routine.get("at_time"):
        when = _clock(routine["at_time"])
        days = routine.get("days") or ""
        if not days:
            return f"every day at {when}"
        if days == "mon,tue,wed,thu,fri":
            return f"weekdays at {when}"
        if days == "sat,sun":
            return f"weekends at {when}"
        names = [d.capitalize() for d in days.split(",") if d]
        return f"{', '.join(names)} at {when}"
    minutes = int(routine.get("interval_min") or 60)
    return "every hour" if minutes == 60 else f"every {minutes} min"


def _clock(at_time: str) -> str:
    """"08:00" → "8:00 AM", without pulling in a date."""
    try:
        hour, minute = (int(x) for x in at_time.split(":"))
    except (ValueError, AttributeError):
        return at_time
    suffix = "AM" if hour < 12 else "PM"
    shown = hour % 12 or 12
    return f"{shown}:{minute:02d} {suffix}"
