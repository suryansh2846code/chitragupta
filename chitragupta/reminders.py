"""Reminders — agents schedule a desktop notification for a future time.

A background sweep (in the scheduler) fires due reminders as native OS
notifications. Times are parsed from natural language ("tomorrow 3pm",
"in 2 hours", "tonight", "at 17:30").
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timedelta

from .config import get_settings
from .log import suppressed

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT,
    message    TEXT NOT NULL,
    fire_at    TEXT NOT NULL,          -- ISO datetime (local tz)
    created_at TEXT NOT NULL,
    fired      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rem_fire ON reminders(fired, fire_at);
"""

_NAMED = {"noon": (12, 0), "midnight": (0, 0), "tonight": (20, 0),
          "morning": (9, 0), "afternoon": (15, 0), "evening": (18, 0)}


def parse_when(text: str, now: datetime | None = None) -> str | None:
    """Natural language → ISO local datetime, or None."""
    now = now or datetime.now().astimezone()
    t = (text or "").strip().lower()
    if not t:
        return None

    # full ISO
    if m := re.search(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}", t):
        with suppressed("return datetime.fromisoformat(m.group(0).replace(' ', 'T')).asti …"):
            return datetime.fromisoformat(m.group(0).replace(" ", "T")).astimezone().isoformat()
    # relative offsets
    if m := re.search(r"\bin\s+(\d+)\s*(min|minute|minutes|hour|hours|hr|hrs|day|days)\b", t):
        n = int(m.group(1)); unit = m.group(2)
        delta = (timedelta(minutes=n) if unit.startswith("min")
                 else timedelta(days=n) if unit.startswith("day")
                 else timedelta(hours=n))
        return (now + delta).isoformat()

    # time of day — an EXPLICIT time (12am, 3pm, 17:30) beats a vague word.
    # Invalid times (25pm, 9:99) are rejected, not crash-inducing or silently wrong.
    hour = minute = None
    if m := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t):
        h12, mi = int(m.group(1)), int(m.group(2) or 0)
        if 1 <= h12 <= 12 and 0 <= mi <= 59:
            hour = (h12 % 12) + (12 if m.group(3) == "pm" else 0)
            minute = mi
    elif m := re.search(r"\b(\d{1,2}):(\d{2})\b", t):
        h24, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h24 <= 23 and 0 <= mi <= 59:
            hour, minute = h24, mi
    if hour is None:
        for name, (h, mi) in _NAMED.items():
            if re.search(rf"\b{name}\b", t):
                hour, minute = h, mi
                break

    # date part
    from .core.dateparse import parse_date_range
    dr = parse_date_range(t)
    base = datetime.fromisoformat(dr[0]).date() if dr else now.date()

    if hour is None:
        if not dr:
            return None       # nothing time-like → can't schedule
        hour, minute = 9, 0   # a date with no time → 9am

    try:
        dt = now.replace(year=base.year, month=base.month, day=base.day,
                         hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None       # any out-of-range component → treat as unparseable
    # vague date ("today"/"tonight"/time-only) in the past → next day
    vague = dr is None or bool(re.search(r"\b(today|tonight|now)\b", t))
    if dt <= now and vague:
        dt += timedelta(days=1)
    return dt.isoformat()


class ReminderStore:
    def __init__(self) -> None:
        path = get_settings().home / "reminders.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL;")
        self._c.execute("PRAGMA busy_timeout=5000;")   # scheduler + API share this
        self._c.executescript(_SCHEMA)

    def add(self, message: str, fire_at: str, agent_id: str | None = None) -> dict:
        rid = str(uuid.uuid4())
        self._c.execute(
            "INSERT INTO reminders (id,agent_id,message,fire_at,created_at,fired) "
            "VALUES (?,?,?,?,?,0)",
            (rid, agent_id, message.strip(), fire_at,
             datetime.now().astimezone().isoformat()))
        self._c.commit()
        return dict(self._c.execute(
            "SELECT * FROM reminders WHERE id=?", (rid,)).fetchone())

    def due(self) -> list[dict]:
        now = datetime.now().astimezone().isoformat()
        rows = self._c.execute(
            "SELECT * FROM reminders WHERE fired=0 AND fire_at<=?", (now,)).fetchall()
        return [dict(r) for r in rows]

    def mark_fired(self, rid: str) -> None:
        self._c.execute("UPDATE reminders SET fired=1 WHERE id=?", (rid,))
        self._c.commit()

    def get(self, rid: str) -> dict | None:
        """One reminder by id, or None.

        Exists so `set_reminder` can be *verified* rather than assumed: the
        handler returning an id proves it built a row object, not that the row
        is in the database. Reading it back is the difference.
        """
        row = self._c.execute(
            "SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None

    def upcoming(self, limit: int = 20) -> list[dict]:
        rows = self._c.execute(
            "SELECT * FROM reminders WHERE fired=0 ORDER BY fire_at LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def update(self, rid: str, message: str | None = None,
               fire_at: str | None = None) -> dict | None:
        """Reword a reminder or move it. Returns None if there is no such one.

        A reminder that has already fired is left alone: editing it would
        resurrect a notification the user has already seen and dealt with.
        """
        sets, vals = [], []
        if message and message.strip():
            sets.append("message=?"); vals.append(message.strip())
        if fire_at:
            sets.append("fire_at=?"); vals.append(fire_at)
        if not sets:
            row = self._c.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
            return dict(row) if row else None
        vals.append(rid)
        cur = self._c.execute(
            f"UPDATE reminders SET {', '.join(sets)} WHERE id=? AND fired=0", vals)
        self._c.commit()
        if not cur.rowcount:
            return None
        return dict(self._c.execute(
            "SELECT * FROM reminders WHERE id=?", (rid,)).fetchone())

    def delete(self, rid: str) -> bool:
        cur = self._c.execute("DELETE FROM reminders WHERE id=?", (rid,))
        self._c.commit()
        return cur.rowcount > 0


_store = None


def get_reminders() -> ReminderStore:
    global _store
    if _store is None:
        _store = ReminderStore()
    return _store
