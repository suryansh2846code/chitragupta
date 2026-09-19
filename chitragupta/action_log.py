"""Everything Chitragupta actually did, and whether it worked.

Approval is consent *before*. This is the other half — the record the user
reads *after*, which is what makes letting an agent act at all a reasonable
thing to do. Until now the two stores that knew anything (`agents/approvals.py`
and `scheduled.py`) each held a slice, neither held the attended actions a
person confirmed in chat, and nothing read either of them back. So the honest
answer to "what have my agents been doing?" was that nobody could say.

Three things it has to get right:

* **Every action, one table.** Attended or unattended, approved or immediate,
  chat or routine — `actions.run_now()` is the single chokepoint they all pass
  through, so the log is written there rather than at four call sites that
  would each have to remember.
* **The result is kept, not just the sentence.** Undo needs the id the service
  returned, and a row storing only "Event created" has thrown away the one
  thing that could take it back.
* **Writing the log never breaks the action.** Every call here is wrapped by
  its caller in `suppressed()`. An audit trail that can fail a send is a worse
  bargain than no audit trail.

Its own database rather than `agents.db`, and set up properly: WAL and a busy
timeout, because the scheduler thread writes here while the window reads it.
Nine modules already share `agents.db` with neither, and joining them would
have been copying the bug rather than the pattern.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import get_settings
from .log import get_logger, suppressed

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_log (
    id           TEXT PRIMARY KEY,
    action_type  TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    risk         TEXT NOT NULL DEFAULT 'amber',
    params_json  TEXT NOT NULL DEFAULT '{}',
    result_json  TEXT NOT NULL DEFAULT '{}',   -- kept so undo has the id back
    ok           INTEGER NOT NULL DEFAULT 0,
    detail       TEXT NOT NULL DEFAULT '',
    verified     INTEGER NOT NULL DEFAULT 0,
    verified_at  TEXT NOT NULL DEFAULT '',
    reversible   INTEGER NOT NULL DEFAULT 0,
    undone       INTEGER NOT NULL DEFAULT 0,
    undone_at    TEXT NOT NULL DEFAULT '',
    agent_id     TEXT NOT NULL DEFAULT '',
    origin       TEXT NOT NULL DEFAULT 'chat', -- chat | routine | approval | scheduled
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_action_log_time ON action_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_log_type ON action_log(action_type);
"""

_CONN: sqlite3.Connection | None = None

#: Set only by `reset_for_tests`. A test that wants its own log should get one
#: without repointing `settings.home`, which every other cached store is also
#: holding — moving it mid-run leaves a singleton bound to a directory that is
#: about to be deleted, and the failure surfaces three tests later.
_PATH_OVERRIDE: Path | None = None


def _conn() -> sqlite3.Connection:
    """One connection for the process, WAL, and a timeout that is not zero.

    Cached rather than opened per call: the schema does not need re-running on
    every read, and a fresh connection per call is a fresh `busy_timeout` of
    nothing on a file the scheduler is also writing.
    """
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _PATH_OVERRIDE or (get_settings().home / "actions.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")   # scheduler + API share this file
    conn.executescript(_SCHEMA)
    conn.commit()
    _CONN = conn
    return conn


def reset_for_tests(path: Path | None = None) -> None:
    """Drop the cached handle, and optionally point the next one somewhere else.

    `path` is the whole database file. Passing one gives a test its own log
    without touching `settings.home`, which is shared with every other cached
    store in the process.
    """
    global _CONN, _PATH_OVERRIDE
    if _CONN is not None:
        with suppressed("closing the action log between tests"):
            _CONN.close()
    _CONN = None
    _PATH_OVERRIDE = path


def record(action_type: str, params: dict, result: dict, *,
           summary: str = "", risk: str = "amber", reversible: bool = False,
           agent_id: str = "", origin: str = "chat") -> str:
    """Write one row and return its id. Never raises."""
    row_id = str(uuid.uuid4())
    with suppressed("writing an action to the log"):
        conn = _conn()
        conn.execute(
            "INSERT INTO action_log (id,action_type,summary,risk,params_json,"
            "result_json,ok,detail,verified,verified_at,reversible,agent_id,"
            "origin,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row_id, action_type, summary[:300], risk,
             json.dumps(params or {}, default=str),
             json.dumps(result or {}, default=str),
             1 if result.get("ok") else 0,
             str(result.get("detail") or result.get("error") or "")[:500],
             1 if result.get("verified") else 0,
             str(result.get("verified_at") or ""),
             1 if reversible else 0, agent_id, origin,
             datetime.now(UTC).isoformat()))
        conn.commit()
    return row_id


def get(entry_id: str) -> dict | None:
    row = _conn().execute("SELECT * FROM action_log WHERE id=?",
                          (entry_id,)).fetchone()
    return _public(dict(row)) if row else None


def recent(limit: int = 50, *, action_type: str = "",
           since: str = "") -> list[dict]:
    """The log, newest first."""
    sql = "SELECT * FROM action_log"
    where: list[str] = []
    args: list[Any] = []
    if action_type:
        where.append("action_type=?")
        args.append(action_type)
    if since:
        where.append("created_at>=?")
        args.append(since)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(int(limit or 50), 500)))
    return [_public(dict(r)) for r in _conn().execute(sql, args).fetchall()]


def mark_undone(entry_id: str) -> None:
    with suppressed("marking an action as undone"):
        conn = _conn()
        conn.execute("UPDATE action_log SET undone=1, undone_at=? WHERE id=?",
                     (datetime.now(UTC).isoformat(), entry_id))
        conn.commit()


def summarise(days: int = 7) -> dict[str, Any]:
    """"What did you do this week?" — counts, not a wall of rows."""
    from datetime import timedelta

    since = (datetime.now(UTC) - timedelta(days=max(1, days))).isoformat()
    rows = _conn().execute(
        "SELECT action_type, ok, undone FROM action_log WHERE created_at>=?",
        (since,)).fetchall()
    by_type: dict[str, int] = {}
    done = failed = undone = 0
    for r in rows:
        by_type[r["action_type"]] = by_type.get(r["action_type"], 0) + 1
        if r["undone"]:
            undone += 1
        elif r["ok"]:
            done += 1
        else:
            failed += 1
    return {"days": days, "total": len(rows), "done": done,
            "failed": failed, "undone": undone, "by_type": by_type}


def _public(row: dict) -> dict:
    row["params"] = json.loads(row.pop("params_json", None) or "{}")
    row["result"] = json.loads(row.pop("result_json", None) or "{}")
    for flag in ("ok", "verified", "reversible", "undone"):
        row[flag] = bool(row.get(flag))
    return row
