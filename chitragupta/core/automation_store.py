"""Automations, their runs, and the ledger that makes a run resumable.

A run used to be a function call. `run_routine` started a turn, parsed the
actions out of the reply, ran them, and returned a string — so a crash anywhere
in it lost the lot, and there was nothing on disk to resume from. Two things
follow from that and both are here: **state is written before it is acted on**,
and **every step is a row**.

The tables:

* `routines` — the automation itself. The same table routines already used,
  widened additively, so a user upgrading in place keeps theirs and they keep
  running. An automation *is* a routine with a trigger spec, conditions and
  policies instead of just an interval.
* `automation_runs` — one attempt at achieving the goal. Carries the triggering
  event, the context snapshot, the plan, the state, the limits it is spending
  against, and its lineage.
* `automation_steps` — what the run did, in order. A step is `pending` until it
  is `running`, and only ever `done` after its result is written.
* `automation_claims` — the idempotency ledger. A claim is taken **before** the
  side effect, so a crash mid-action leaves evidence the attempt happened.

This is a leaf: stdlib, `config` and `log`. The engine above it decides; this
only remembers. Two layers need these rows — the engine writes them and `api/`
reads them — and neither may import the other, which is why they are here.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..log import get_logger, suppressed

log = get_logger(__name__)


class RunState(StrEnum):
    """Where a run is. Stored as text so a database is readable by eye.

    The failure states are deliberately four and not one, because they call for
    different things from different people:

    * `RETRYING` — we will try again, by ourselves, at `next_attempt_at`.
    * `BLOCKED` — the system **correctly declined**: a condition was false, a
      permission said no, a limit was hit. Nobody needs to fix anything.
    * `FAILED` — we tried and could not, and have stopped trying.
    * `ESCALATED` — we stopped and **the user must do something**. The
      difference from FAILED is who is now holding it.

    Collapsing BLOCKED into FAILED is the tempting mistake: it turns "your
    automation correctly did nothing today" into a red badge, and a user who
    sees enough of those stops reading them.
    """

    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    FAILED = "failed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


#: States a run can still move out of on its own. Anything else is settled, and
#: the recovery sweep must not touch it.
LIVE_STATES = frozenset({
    RunState.PENDING, RunState.RUNNING, RunState.WAITING_FOR_APPROVAL,
    RunState.EXECUTING, RunState.VERIFYING, RunState.RETRYING,
})

#: Settled states. A run here is history.
TERMINAL_STATES = frozenset({
    RunState.COMPLETED, RunState.BLOCKED, RunState.FAILED,
    RunState.ESCALATED, RunState.CANCELLED,
})

#: What may follow what. Enforced on write, because an illegal transition is
#: always a bug in the driver and is much cheaper to find here than in a run
#: that quietly went from COMPLETED back to RUNNING.
_ALLOWED: dict[RunState, frozenset[RunState]] = {
    RunState.PENDING: frozenset({
        RunState.RUNNING, RunState.BLOCKED, RunState.CANCELLED}),
    RunState.RUNNING: frozenset({
        RunState.WAITING_FOR_APPROVAL, RunState.EXECUTING, RunState.VERIFYING,
        RunState.COMPLETED, RunState.BLOCKED, RunState.RETRYING,
        RunState.FAILED, RunState.ESCALATED, RunState.CANCELLED}),
    RunState.WAITING_FOR_APPROVAL: frozenset({
        RunState.EXECUTING, RunState.BLOCKED, RunState.CANCELLED,
        RunState.ESCALATED, RunState.FAILED}),
    RunState.EXECUTING: frozenset({
        RunState.VERIFYING, RunState.WAITING_FOR_APPROVAL, RunState.COMPLETED,
        RunState.RETRYING, RunState.FAILED, RunState.ESCALATED,
        RunState.BLOCKED, RunState.CANCELLED}),
    RunState.VERIFYING: frozenset({
        RunState.COMPLETED, RunState.RETRYING, RunState.FAILED,
        RunState.ESCALATED, RunState.EXECUTING, RunState.CANCELLED}),
    RunState.RETRYING: frozenset({
        RunState.RUNNING, RunState.EXECUTING, RunState.FAILED,
        RunState.ESCALATED, RunState.CANCELLED, RunState.BLOCKED}),
}


class IllegalTransitionError(RuntimeError):
    """A state change the machine does not allow. Always a driver bug."""


class StepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ClaimState(StrEnum):
    """An idempotency claim's life.

    `CLAIMED` is the interesting one: it means *we were about to cause this
    side effect and do not know whether we did*. A process that dies there
    leaves the claim behind, and recovery must verify rather than re-run.
    """

    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_runs (
    id                TEXT PRIMARY KEY,
    automation_id     TEXT NOT NULL,
    automation_name   TEXT NOT NULL DEFAULT '',
    state             TEXT NOT NULL DEFAULT 'pending',
    trigger_json      TEXT NOT NULL DEFAULT '{}',
    context_json      TEXT NOT NULL DEFAULT '{}',
    plan_json         TEXT NOT NULL DEFAULT '[]',
    outcome           TEXT NOT NULL DEFAULT '',
    error             TEXT NOT NULL DEFAULT '',
    reason            TEXT NOT NULL DEFAULT '',
    attempt           INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    next_attempt_at   TEXT NOT NULL DEFAULT '',
    approval_id       TEXT NOT NULL DEFAULT '',
    correlation_id    TEXT NOT NULL DEFAULT '',
    parent_run_id     TEXT NOT NULL DEFAULT '',
    depth             INTEGER NOT NULL DEFAULT 0,
    actions_used      INTEGER NOT NULL DEFAULT 0,
    model_calls_used  INTEGER NOT NULL DEFAULT 0,
    deadline_at       TEXT NOT NULL DEFAULT '',
    -- The user confirmed this exact action, with this content, at the moment
    -- they scheduled it. Set ONLY by `engine.run_once`, from a row in
    -- `scheduled_actions` that `actions.execute` wrote after a Confirm. See
    -- the note in `executor._run_action`.
    pre_approved      INTEGER NOT NULL DEFAULT 0,
    -- The automation, for a run whose automation was never written down: a
    -- reminder and a scheduled action are one occurrence, not a standing rule.
    -- Without this a restart or a retry cannot resume them — the goal, the
    -- policy and the limits only ever existed in the process that died. See
    -- `engine.run_once` and `engine.recover`.
    spec_json         TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    started_at        TEXT NOT NULL DEFAULT '',
    finished_at       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_state ON automation_runs(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_runs_auto ON automation_runs(automation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_corr ON automation_runs(correlation_id);

CREATE TABLE IF NOT EXISTS automation_steps (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL DEFAULT 'pending',
    params_json    TEXT NOT NULL DEFAULT '{}',
    result_json    TEXT NOT NULL DEFAULT '{}',
    idem_key       TEXT NOT NULL DEFAULT '',
    attempt        INTEGER NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON automation_steps(run_id, seq);

CREATE TABLE IF NOT EXISTS automation_claims (
    idem_key     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL DEFAULT '',
    step_id      TEXT NOT NULL DEFAULT '',
    action_type  TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'claimed',
    result_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_claims_run ON automation_claims(run_id);
"""

#: Columns added to `routines` so an automation is a routine with more to say.
#: `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so
#: an in-place upgrade keeps the old shape and every read of a new column
#: raises — which is why this list exists rather than a second CREATE.
ROUTINE_COLUMNS = {
    "goal": "TEXT NOT NULL DEFAULT ''",
    "trigger_json": "TEXT NOT NULL DEFAULT '{}'",
    "conditions_json": "TEXT NOT NULL DEFAULT '[]'",
    "policy_json": "TEXT NOT NULL DEFAULT '{}'",
    "owner": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "next_run": "TEXT NOT NULL DEFAULT ''",
}

#: Columns added to `automation_runs` after it first shipped. Same reason as
#: `ROUTINE_COLUMNS`: a machine that already has the table keeps the old shape,
#: and the first read of a new column raises on a database full of real runs.
RUN_COLUMNS = {
    "plan_json": "TEXT NOT NULL DEFAULT '[]'",
    "pre_approved": "INTEGER NOT NULL DEFAULT 0",
    "spec_json": "TEXT NOT NULL DEFAULT '{}'",
}

_CONN: sqlite3.Connection | None = None
_PATH_OVERRIDE: Path | None = None
_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _PATH_OVERRIDE or (get_settings().home / "automation.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(automation_runs)")}
    for column, decl in RUN_COLUMNS.items():
        if column not in have:
            conn.execute(f"ALTER TABLE automation_runs ADD COLUMN {column} {decl}")
    conn.commit()
    _CONN = conn
    return conn


def reset_for_tests(path: Path | None = None) -> None:
    global _CONN, _PATH_OVERRIDE
    if _CONN is not None:
        with suppressed("closing the automation store between tests"):
            _CONN.close()
    _CONN = None
    _PATH_OVERRIDE = path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _run_public(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["trigger"] = _loads(out.pop("trigger_json", ""), {})
    out["context"] = _loads(out.pop("context_json", ""), {})
    out["plan"] = _loads(out.pop("plan_json", ""), [])
    out["spec"] = _loads(out.pop("spec_json", ""), {})
    return out


# ── runs ───────────────────────────────────────────────────────────────────

def create_run(automation_id: str, *, automation_name: str = "",
               trigger: dict[str, Any] | None = None,
               max_attempts: int = 3, deadline_at: str = "",
               correlation_id: str = "", parent_run_id: str = "",
               depth: int = 0, plan: list[dict[str, Any]] | None = None,
               pre_approved: bool = False,
               spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open a run in PENDING. Nothing has happened yet, and it is on disk."""
    run_id = str(uuid.uuid4())
    now = _now()
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO automation_runs (id, automation_id, automation_name, "
            "state, trigger_json, plan_json, max_attempts, deadline_at, "
            "correlation_id, parent_run_id, depth, pre_approved, spec_json, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, automation_id, automation_name, RunState.PENDING,
             json.dumps(trigger or {}), json.dumps(plan or []), max_attempts,
             deadline_at, correlation_id or run_id, parent_run_id, depth,
             1 if pre_approved else 0, json.dumps(spec or {}), now, now))
        conn.commit()
    return get_run(run_id) or {}


def get_run(run_id: str) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM automation_runs WHERE id=?", (run_id,)).fetchone()
    return _run_public(row) if row else None


def transition(run_id: str, to: RunState, **fields: Any) -> dict[str, Any]:
    """Move a run to `to`, writing it before anything acts on it.

    Refuses an illegal move rather than recording it. The alternative — trust
    the driver — means a bug shows up as a run in an impossible state hours
    later, with nothing to say how it got there.

    Idempotent for the same state: re-entering RETRYING after a restart is a
    normal thing for recovery to do, not an error.
    """
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM automation_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise IllegalTransitionError(f"no run {run_id}")
        current = RunState(row["state"])
        if current != to:
            if current in TERMINAL_STATES:
                raise IllegalTransitionError(
                    f"run {run_id} is settled in {current}; cannot move to {to}")
            if to not in _ALLOWED.get(current, frozenset()):
                raise IllegalTransitionError(f"{current} -> {to} is not allowed")

        sets = {"state": str(to), "updated_at": _now()}
        if to is RunState.RUNNING and not row["started_at"]:
            sets["started_at"] = _now()
        if to in TERMINAL_STATES:
            sets["finished_at"] = _now()
        for key, value in fields.items():
            if key in ("trigger", "context", "plan"):
                sets[f"{key}_json"] = json.dumps(value)
            else:
                sets[key] = value
        assignments = ", ".join(f"{k}=?" for k in sets)
        conn.execute(f"UPDATE automation_runs SET {assignments} WHERE id=?",
                     (*sets.values(), run_id))
        conn.commit()
    return get_run(run_id) or {}


def update_run(run_id: str, **fields: Any) -> None:
    """Change a run without moving it. For counters and snapshots."""
    if not fields:
        return
    sets: dict[str, Any] = {"updated_at": _now()}
    for key, value in fields.items():
        if key in ("trigger", "context", "plan"):
            sets[f"{key}_json"] = json.dumps(value)
        else:
            sets[key] = value
    with _LOCK:
        conn = _conn()
        assignments = ", ".join(f"{k}=?" for k in sets)
        conn.execute(f"UPDATE automation_runs SET {assignments} WHERE id=?",
                     (*sets.values(), run_id))
        conn.commit()


def bump(run_id: str, column: str, by: int = 1) -> int:
    """Increment a usage counter and return the new value.

    Read-modify-write in one statement under the lock: two threads spending
    the same run's action budget must not both see the old number.
    """
    if column not in ("actions_used", "model_calls_used"):
        raise ValueError(f"not a counter: {column}")
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            f"UPDATE automation_runs SET {column} = {column} + ?, updated_at=? "
            "WHERE id=? RETURNING " + column, (by, _now(), run_id))
        row = cur.fetchone()
        conn.commit()
    return int(row[0]) if row else 0


def live_runs(automation_id: str = "") -> list[dict[str, Any]]:
    """Runs that can still move. The recovery sweep's input."""
    marks = ",".join("?" * len(LIVE_STATES))
    args: list[Any] = [str(s) for s in LIVE_STATES]
    sql = f"SELECT * FROM automation_runs WHERE state IN ({marks})"
    if automation_id:
        sql += " AND automation_id=?"
        args.append(automation_id)
    sql += " ORDER BY created_at"
    return [_run_public(r) for r in _conn().execute(sql, args).fetchall()]


def runs_for(automation_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM automation_runs WHERE automation_id=? "
        "ORDER BY created_at DESC LIMIT ?", (automation_id, limit)).fetchall()
    return [_run_public(r) for r in rows]


def recent_runs(limit: int = 50) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM automation_runs ORDER BY created_at DESC LIMIT ?",
        (limit,)).fetchall()
    return [_run_public(r) for r in rows]


def run_waiting_on(approval_id: str) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM automation_runs WHERE approval_id=? AND state=? LIMIT 1",
        (approval_id, str(RunState.WAITING_FOR_APPROVAL))).fetchone()
    return _run_public(row) if row else None


def lineage_depth(correlation_id: str) -> int:
    """How many runs already descend from one original cause.

    The second half of loop protection. `Event.depth` bounds a straight chain;
    this bounds a fan — twenty automations each triggering one more is not deep,
    and is still a runaway.
    """
    row = _conn().execute(
        "SELECT COUNT(*) FROM automation_runs WHERE correlation_id=?",
        (correlation_id,)).fetchone()
    return int(row[0]) if row else 0


# ── steps ──────────────────────────────────────────────────────────────────

def add_step(run_id: str, *, kind: str, name: str = "",
             params: dict[str, Any] | None = None, idem_key: str = "",
             state: StepState = StepState.PENDING) -> dict[str, Any]:
    step_id = str(uuid.uuid4())
    now = _now()
    with _LOCK:
        conn = _conn()
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM automation_steps WHERE run_id=?",
            (run_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO automation_steps (id, run_id, seq, kind, name, state, "
            "params_json, idem_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (step_id, run_id, seq, kind, name, str(state),
             json.dumps(params or {}), idem_key, now, now))
        conn.commit()
    return get_step(step_id) or {}


def finish_step(step_id: str, *, state: StepState,
                result: dict[str, Any] | None = None, error: str = "") -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE automation_steps SET state=?, result_json=?, error=?, "
            "attempt = attempt + 1, updated_at=? WHERE id=?",
            (str(state), json.dumps(result or {}), error[:500], _now(), step_id))
        conn.commit()


def get_step(step_id: str) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM automation_steps WHERE id=?", (step_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["params"] = _loads(out.pop("params_json", ""), {})
    out["result"] = _loads(out.pop("result_json", ""), {})
    return out


def steps_for(run_id: str) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM automation_steps WHERE run_id=? ORDER BY seq",
        (run_id,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["params"] = _loads(item.pop("params_json", ""), {})
        item["result"] = _loads(item.pop("result_json", ""), {})
        out.append(item)
    return out


# ── idempotency claims ─────────────────────────────────────────────────────

def claim(idem_key: str, *, run_id: str = "", step_id: str = "",
          action_type: str = "") -> tuple[bool, dict[str, Any] | None]:
    """Take the right to cause one side effect exactly once.

    Returns `(won, existing)`. `won` is True only for the caller that inserted
    the row; everyone else gets the claim that is already there and must not
    perform the effect.

    **Taken before the effect, not after.** A claim written afterwards proves
    nothing about a process that died in between — which is precisely the case
    this exists for. The cost is that a crash leaves a `CLAIMED` row whose
    outcome is unknown, and that is the honest state: recovery verifies it
    rather than assuming either way.
    """
    now = _now()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO automation_claims (idem_key, run_id, step_id, "
            "action_type, state, created_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(idem_key) DO NOTHING",
            (idem_key, run_id, step_id, action_type, str(ClaimState.CLAIMED), now))
        won = cur.rowcount == 1
        conn.commit()
    return (True, None) if won else (False, get_claim(idem_key))


def settle_claim(idem_key: str, *, state: ClaimState,
                 result: dict[str, Any] | None = None) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE automation_claims SET state=?, result_json=?, completed_at=? "
            "WHERE idem_key=?",
            (str(state), json.dumps(result or {}), _now(), idem_key))
        conn.commit()


def release_claim(idem_key: str) -> None:
    """Give back a claim whose side effect provably did not happen.

    Only for the case where we know nothing reached the world — a permission
    refusal, a validation error before the handler ran. Releasing a claim whose
    outcome is *unknown* is how a duplicate email gets sent.
    """
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM automation_claims WHERE idem_key=? AND state=?",
                     (idem_key, str(ClaimState.CLAIMED)))
        conn.commit()


def get_claim(idem_key: str) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM automation_claims WHERE idem_key=?", (idem_key,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["result"] = _loads(out.pop("result_json", ""), {})
    return out


def dangling_claims(run_id: str) -> list[dict[str, Any]]:
    """Claims this run took and never settled — the crash evidence."""
    rows = _conn().execute(
        "SELECT * FROM automation_claims WHERE run_id=? AND state=?",
        (run_id, str(ClaimState.CLAIMED))).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["result"] = _loads(item.pop("result_json", ""), {})
        out.append(item)
    return out
