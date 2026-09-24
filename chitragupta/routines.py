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

import builtins
import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from .config import get_settings

log = logging.getLogger("chitragupta.routines")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    agent_id     TEXT NOT NULL DEFAULT 'personal',
    trigger      TEXT NOT NULL,            -- schedule | daily | new_email
    interval_min INTEGER NOT NULL DEFAULT 60,
    instruction  TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    last_run     TEXT,
    last_result  TEXT,
    at_time      TEXT NOT NULL DEFAULT '',   -- "HH:MM", local wall clock
    days         TEXT NOT NULL DEFAULT ''    -- "mon,tue,…"; "" is every day
);
"""

#: Columns added after the table shipped. `CREATE TABLE IF NOT EXISTS` does
#: nothing to a table that already exists, so a user upgrading in place keeps
#: the old shape and every read of a new column raises — the same lesson
#: `agents/approvals.py` records, and the same fix.
_ADDED_COLUMNS = {
    "at_time": "TEXT NOT NULL DEFAULT ''",
    "days": "TEXT NOT NULL DEFAULT ''",
}

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


class RoutineStore:
    def __init__(self) -> None:
        path = get_settings().home / "routines.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL;")
        self._c.execute("PRAGMA busy_timeout=5000;")   # scheduler + API share this
        self._c.executescript(_SCHEMA)
        have = {r["name"] for r in self._c.execute("PRAGMA table_info(routines)")}
        for column, decl in _ADDED_COLUMNS.items():
            if column not in have:
                self._c.execute(f"ALTER TABLE routines ADD COLUMN {column} {decl}")
        self._c.commit()

    def create(self, name, agent_id, trigger, instruction,
               interval_min=60, at_time="", days="") -> dict:
        rid = str(uuid.uuid4())
        at_time = parse_time(at_time)
        # A `daily` routine with no readable time has no way to fire, and a
        # routine that never fires looks exactly like one that is broken. It
        # falls back to an interval, which at least does something and which
        # `describe_schedule` will then say out loud on the card.
        if trigger == "daily" and not at_time:
            trigger = "schedule"
        self._c.execute(
            "INSERT INTO routines (id,name,agent_id,trigger,interval_min,"
            "instruction,enabled,created_at,at_time,days) "
            "VALUES (?,?,?,?,?,?,1,?,?,?)",
            (rid, name.strip() or "Routine", agent_id, trigger,
             int(interval_min or 60), instruction.strip(),
             datetime.now(UTC).isoformat(), at_time, parse_days(days)))
        self._c.commit()
        return self.get(rid)

    def get(self, rid) -> dict | None:
        r = self._c.execute("SELECT * FROM routines WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None

    def list(self) -> builtins.list[dict]:
        return [dict(r) for r in self._c.execute(
            "SELECT * FROM routines ORDER BY created_at").fetchall()]

    def enabled(self) -> builtins.list[dict]:
        return [dict(r) for r in self._c.execute(
            "SELECT * FROM routines WHERE enabled=1").fetchall()]

    #: What an edit is allowed to touch. `enabled` has its own toggle and the
    #: run history is the routine's record of itself — neither is the user's to
    #: retype, and allowing them here would let a typo erase what it did.
    EDITABLE = ("name", "agent_id", "trigger", "interval_min", "instruction",
                "at_time", "days")

    def update(self, rid, **fields) -> dict | None:
        """Change a routine in place. Unknown or absent fields are ignored.

        Returns the updated routine, or None if there was none to update — the
        caller turns that into a 404 rather than reporting a silent success.
        """
        sets, vals = [], []
        for k in self.EDITABLE:
            if fields.get(k) is None:
                continue
            v = fields[k]
            if k == "interval_min":
                v = max(1, int(v))        # a zero-minute routine is a busy loop
            elif k == "at_time":
                v = parse_time(v)
                if not v:
                    continue              # an unreadable time is not an edit
            elif k == "days":
                # "" is meaningful here — it is "every day" — so unlike a name,
                # an empty day list is a real edit and must not be skipped.
                sets.append("days=?")
                vals.append(parse_days(v))
                continue
            elif isinstance(v, str):
                v = v.strip()
                if not v:
                    continue              # blanking a name is not an edit
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return self.get(rid)
        vals.append(rid)
        cur = self._c.execute(f"UPDATE routines SET {', '.join(sets)} WHERE id=?", vals)
        self._c.commit()
        return self.get(rid) if cur.rowcount else None

    def toggle(self, rid, on: bool) -> None:
        self._c.execute("UPDATE routines SET enabled=? WHERE id=?",
                        (1 if on else 0, rid)); self._c.commit()

    def mark_run(self, rid, result: str) -> None:
        self._c.execute("UPDATE routines SET last_run=?, last_result=? WHERE id=?",
                        (datetime.now(UTC).isoformat(), result[:400], rid))
        self._c.commit()

    def delete(self, rid) -> bool:
        cur = self._c.execute("DELETE FROM routines WHERE id=?", (rid,))
        self._c.commit()
        return cur.rowcount > 0


_store = None


def get_routines() -> RoutineStore:
    global _store
    if _store is None:
        _store = RoutineStore()
    return _store


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
    """Called by the scheduler each cycle: fire due routines."""
    store = get_routines()
    now = datetime.now(UTC)
    for r in store.enabled():
        try:
            if r["trigger"] == "daily":
                if not daily_due(r, now):
                    continue
                res = run_routine(r)
                store.mark_run(r["id"], json.dumps(res)[:400])
            elif r["trigger"] == "schedule":
                last = r["last_run"]
                due = (last is None or
                       datetime.fromisoformat(last) +
                       timedelta(minutes=r["interval_min"]) <= now)
                if not due:
                    continue
                res = run_routine(r)
                store.mark_run(r["id"], json.dumps(res)[:400])
            elif r["trigger"] == "new_email" and new_email_count > 0:
                rows = _new_emails_since(r["last_run"])
                if not rows:
                    continue
                ctx = "NEW EMAIL(S) that just arrived:\n" + "\n".join(
                    f"- {row['title']}: {' '.join(row['text'].split())[:200]}"
                    for row in rows)
                res = run_routine(r, ctx)
                store.mark_run(r["id"], json.dumps(res)[:400])
        except Exception:
            log.exception("routine %s failed", r.get("id"))
