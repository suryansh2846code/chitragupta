"""A simple, local task/reminder store for the assistant.

Kept deliberately small: add / list / complete / delete tasks, each with an
optional natural due date. Lives in its own SQLite file alongside the brain so
agents (especially Personal) can answer "what's on for today?".
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    due        TEXT,                       -- ISO date (YYYY-MM-DD) or NULL
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    done_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due, done);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive only, so an existing tasks.db opens unchanged.

    `source` is where the task came from — an email thread, usually. A task
    that says *"send Rahul the revised figures"* and cannot say which
    conversation asked for it makes the user go and find it again, which is
    the work they asked to have taken off them.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN source TEXT")
    if "source_ref" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN source_ref TEXT")
    conn.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat()


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]


def parse_due(text: str | None) -> str | None:
    """Turn a loose due phrase (or a title containing one) into an ISO date.
    Understands today/tonight, tomorrow, weekdays, 'in N days', and YYYY-MM-DD.
    Matches as substrings so it works on full task titles too."""
    if not text:
        return None
    t = text.strip().lower()
    today = date.today()
    if re.search(r"\b(today|tonight)\b", t):
        return today.isoformat()
    if re.search(r"\b(tomorrow|tmrw)\b", t):
        return (today + timedelta(days=1)).isoformat()
    if m := re.search(r"\bin (\d+) days?\b", t):
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    if m := re.search(r"\d{4}-\d{2}-\d{2}", t):
        return m.group(0)
    for i, wd in enumerate(_WEEKDAYS):
        if re.search(rf"\b{wd}\b", t):
            delta = (i - today.weekday()) % 7 or 7   # next occurrence
            return (today + timedelta(days=delta)).isoformat()
    return None


def _strip_due_phrase(title: str) -> str:
    """Remove a trailing date phrase from a title, so 'call Sam tomorrow' stores
    as title 'call Sam' with the due date on the side."""
    pat = (r"\s*\b(on|by|before|due(\s+on)?)?\s*"
           r"(today|tonight|tomorrow|tmrw|in \d+ days?|"
           + "|".join(_WEEKDAYS) + r"|\d{4}-\d{2}-\d{2})\b\.?\s*$")
    cleaned = re.sub(pat, "", title, flags=re.I).strip(" ,.-")
    return cleaned or title


class TaskStore:
    def __init__(self, db_path=None) -> None:
        path = db_path or (get_settings().home / "tasks.db")
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA)
        _migrate(self._c)

    def add(self, title: str, due: str | None = None, *,
            source: str = "", source_ref: str = "") -> dict:
        """`source` is what kind of thing this came from ("email"), and
        `source_ref` is which one (a thread id). Both optional: a task typed
        into the box has no origin, and that is not a missing value."""
        tid = str(uuid.uuid4())
        # prefer an explicit due; otherwise recover one from the title itself
        iso_due = parse_due(due) or parse_due(title)
        clean_title = _strip_due_phrase(title.strip()) if iso_due else title.strip()
        self._c.execute(
            "INSERT INTO tasks (id,title,due,done,created_at,source,source_ref) "
            "VALUES (?,?,?,0,?,?,?)",
            (tid, clean_title, iso_due, _now(),
             (source or "").strip(), (source_ref or "").strip()),
        )
        self._c.commit()
        return self.get(tid)

    def get(self, tid: str) -> dict | None:
        row = self._c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

    def list(self, *, when: str | None = None, include_done: bool = False) -> list[dict]:
        """when: 'today' | 'overdue' | 'upcoming' | None (all open)."""
        q = "SELECT * FROM tasks"
        cond, args = [], []
        if not include_done:
            cond.append("done=0")
        today = date.today().isoformat()
        if when == "today":
            cond.append("due=?"); args.append(today)
        elif when == "overdue":
            cond.append("due IS NOT NULL AND due<? AND done=0"); args.append(today)
        elif when == "upcoming":
            cond.append("due IS NOT NULL AND due>=?"); args.append(today)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY (due IS NULL), due ASC, created_at ASC"
        return [dict(r) for r in self._c.execute(q, args).fetchall()]

    def complete(self, tid_or_prefix: str) -> dict | None:
        row = self._match(tid_or_prefix)
        if not row:
            return None
        self._c.execute("UPDATE tasks SET done=1, done_at=? WHERE id=?",
                        (_now(), row["id"]))
        self._c.commit()
        return self.get(row["id"])

    def delete(self, tid_or_prefix: str) -> bool:
        row = self._match(tid_or_prefix)
        if not row:
            return False
        self._c.execute("DELETE FROM tasks WHERE id=?", (row["id"],))
        self._c.commit()
        return True

    def _match(self, tid_or_prefix: str):
        row = self._c.execute("SELECT * FROM tasks WHERE id=?",
                              (tid_or_prefix,)).fetchone()
        if row:
            return row
        return self._c.execute(
            "SELECT * FROM tasks WHERE id LIKE ? OR title LIKE ? LIMIT 1",
            (f"{tid_or_prefix}%", f"%{tid_or_prefix}%")).fetchone()

    def stats(self) -> dict:
        today = date.today().isoformat()
        c = self._c.execute
        return {
            "open": c("SELECT COUNT(*) n FROM tasks WHERE done=0").fetchone()["n"],
            "today": c("SELECT COUNT(*) n FROM tasks WHERE done=0 AND due=?",
                       (today,)).fetchone()["n"],
            "overdue": c("SELECT COUNT(*) n FROM tasks WHERE done=0 AND due<?",
                         (today,)).fetchone()["n"],
        }


@lru_cache
def get_tasks() -> TaskStore:
    return TaskStore()
