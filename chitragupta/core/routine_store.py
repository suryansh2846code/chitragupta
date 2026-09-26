"""The routines table, and the one accessor for it.

A SQLite store, which is what `core/` owns. It is here rather than in
`routines.py` because **two layers above need the rows and neither may import
the other**: `routines.py` runs a schedule, and `agents/automation_tools.py`
lets an agent answer "what do I have running" and pause one.

Running a routine means driving an agent turn and passing its proposals through
the approval gate, so `routines` sits *above* `agents/` — which made
`automation_tools` importing `routines` an upward edge, and the last one holding
nineteen modules together as a single strongly-connected component.

Data below both, feature above. `routines` re-exports `RoutineStore` and
`get_routines`, so existing imports are unchanged.
"""
from __future__ import annotations

import builtins
import logging
import sqlite3
import uuid
from datetime import UTC, datetime

from ..config import get_settings
from .schedule import parse_days, parse_time

log = logging.getLogger("chitragupta.routines.store")

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
    # ── what makes a routine an automation ──────────────────────────────
    #
    # Added here rather than in a new table, because an automation *is* a
    # routine with more to say. A second table would have meant two things to
    # list, two things to enable, two screens, and a migration that moves a
    # user's routines — which is a migration that can lose them.
    #
    # Everything below defaults to empty, and an automation with all of them
    # empty behaves exactly as the routine it was. `model.Automation.from_row`
    # reads the legacy `trigger` / `at_time` / `days` / `interval_min` columns
    # whenever `trigger_json` is empty, so nothing is rewritten to keep working.
    "goal": "TEXT NOT NULL DEFAULT ''",
    "trigger_json": "TEXT NOT NULL DEFAULT ''",
    "conditions_json": "TEXT NOT NULL DEFAULT ''",
    "policy_json": "TEXT NOT NULL DEFAULT ''",
    "owner": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "next_run": "TEXT NOT NULL DEFAULT ''",
}

#: Columns `set_fields` will write. An allow-list rather than trusting the
#: caller: a column name cannot be parameterised in SQL, so it is interpolated
#: — and the one place that must never take a name from outside is the place
#: that builds the statement.
_WRITABLE = frozenset({
    "name", "agent_id", "instruction", "enabled", "at_time", "days",
    "interval_min", "trigger", "goal", "trigger_json", "conditions_json",
    "policy_json", "owner", "next_run", "last_result",
})


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
        row = self.get(rid)
        # It was inserted and committed one line ago. Asserting rather than
        # widening the return type: every caller treats this as a row and would
        # need a `None` branch that can never be taken. `mypy` only started
        # seeing it when this moved into `core/`, which is checked.
        assert row is not None, "the routine just written could not be read back"
        return row

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

    def set_fields(self, rid, **fields) -> bool:
        """Update named columns on one routine.

        Only names in `_WRITABLE` reach the statement — see the note there.
        Returns whether a row changed, so a caller can tell "no such routine"
        from "nothing to change".
        """
        clean = {k: v for k, v in fields.items() if k in _WRITABLE}
        if not clean:
            return False
        clean["updated_at"] = datetime.now(UTC).isoformat()
        assignments = ", ".join(f"{k}=?" for k in clean)
        cur = self._c.execute(
            f"UPDATE routines SET {assignments} WHERE id=?",
            (*clean.values(), rid))
        self._c.commit()
        return cur.rowcount > 0

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
