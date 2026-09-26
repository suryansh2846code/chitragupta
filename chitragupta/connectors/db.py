"""The connector layer's own durable state, and why it is its own file.

`core/db.py` owns the brain: memories, entities, relations, open loops. What a
connector needs to remember is none of those things — it is *operational* state:
which account this is, where the last pass got to, which records we have seen,
what arrived, what failed. Putting it in `chitragupta.db` would mean an export
of the brain carried sync cursors, and a reset of the brain lost them.

So: `connectors.db`, beside `agents.db`, which exists for the same reason and
was the precedent.

## Five tables, and the one that is not obvious

* `connector_connections` — **one row per account**, not per connector. This is
  the change that makes multi-account structurally possible: `connector_state`
  in `core/db.py` is `PRIMARY KEY (connector)`, which bakes `provider ==
  account` into storage and cannot be worked around above it.
* `connector_sync_state` — a checkpoint per `(connection, resource type)`. Per
  resource because a connector reading two kinds of thing has two positions,
  and one cursor for both moves each past the other's records.
* `connector_resources` — **stable external identity**, and the reason for it
  is the one that was quietly wrong: dedup is a content hash
  (`idx_memories_chash`), so an *edited* email is a new memory and the old one
  stays forever. An identity lets an update supersede rather than accumulate,
  and lets a deletion be recorded at all.
* `connector_events` — arrivals, deduplicated durably. Providers do not promise
  exactly-once and never have.
* `connector_operations` — what happened, bounded. Observability that survives
  a restart, which a counter in memory does not.

## Additive, idempotent, and never destructive

`CREATE TABLE IF NOT EXISTS` plus the `_ADDED_COLUMNS` idiom `core/db.py`
already uses. A schema change adds; it does not rewrite. The one thing that may
be dropped is nothing.
"""
from __future__ import annotations

import contextlib
import sqlite3
import threading
from typing import Any

from ..config import get_settings
from ..log import get_logger

log = get_logger(__name__)

SCHEMA = """
-- One row per ACCOUNT. `provider == account` is the assumption this table
-- exists to remove: a user with a personal and a work Gmail has two rows here,
-- two credentials, two sets of checkpoints and two sets of provenance.
CREATE TABLE IF NOT EXISTS connector_connections (
    id             TEXT PRIMARY KEY,   -- stable; never the account address
    connector      TEXT NOT NULL,      -- 'gmail'
    provider       TEXT NOT NULL,      -- 'google'
    account        TEXT NOT NULL DEFAULT '',   -- what the vendor calls them
    label          TEXT NOT NULL DEFAULT '',   -- what the USER calls it
    auth_state     TEXT NOT NULL DEFAULT 'disconnected',
    auth_detail    TEXT NOT NULL DEFAULT '',
    scopes         TEXT NOT NULL DEFAULT '[]',
    version        INTEGER NOT NULL DEFAULT 1, -- the connector version that made it
    paused         INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
-- Two connections to the same account on the same connector is a duplicate,
-- not a feature. An empty account is the single-account case and is allowed
-- exactly once per connector, which is what the existing connectors are.
CREATE UNIQUE INDEX IF NOT EXISTS idx_connection_account
    ON connector_connections(connector, account);

-- Where the last pass got to, per resource type.
CREATE TABLE IF NOT EXISTS connector_sync_state (
    connection_id   TEXT NOT NULL,
    resource_type   TEXT NOT NULL,
    cursor          TEXT NOT NULL DEFAULT '',
    last_success    TEXT,
    last_attempt    TEXT,
    items_processed INTEGER NOT NULL DEFAULT 0,
    error           TEXT NOT NULL DEFAULT '',
    -- The connector version that wrote this cursor. A bump invalidates it
    -- rather than resuming into data that now means something else.
    schema_version  INTEGER NOT NULL DEFAULT 1,
    -- The run that last touched it, so a half-finished pass is identifiable
    -- rather than merely old.
    run_id          TEXT NOT NULL DEFAULT '',
    started_at      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (connection_id, resource_type)
);

-- Stable identity for one thing in an external system.
CREATE TABLE IF NOT EXISTS connector_resources (
    connection_id     TEXT NOT NULL,
    resource_type     TEXT NOT NULL,
    external_id       TEXT NOT NULL,
    memory_id         TEXT NOT NULL DEFAULT '',
    state             TEXT NOT NULL DEFAULT 'active',
    -- What the content looked like last time, so an unchanged record can be
    -- skipped without re-reading it and an changed one can be recognised.
    fingerprint       TEXT NOT NULL DEFAULT '',
    source_updated_at TEXT NOT NULL DEFAULT '',
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    PRIMARY KEY (connection_id, resource_type, external_id)
);
CREATE INDEX IF NOT EXISTS idx_resource_state
    ON connector_resources(connection_id, state);
CREATE INDEX IF NOT EXISTS idx_resource_memory
    ON connector_resources(memory_id);

-- What arrived. Deduplicated durably, because no provider promises
-- exactly-once and several explicitly promise the opposite.
CREATE TABLE IF NOT EXISTS connector_events (
    id             TEXT PRIMARY KEY,
    connection_id  TEXT NOT NULL,
    -- Stored, not only folded into `id`. Without it a row read back could not
    -- reproduce its own primary key, so `mark()` updated nothing and every
    -- event stayed pending forever.
    provider_event_id TEXT NOT NULL DEFAULT '',
    event_type     TEXT NOT NULL,
    resource_type  TEXT NOT NULL,
    resource_id    TEXT NOT NULL DEFAULT '',
    occurred_at    TEXT NOT NULL DEFAULT '',
    received_at    TEXT NOT NULL,
    sequence       TEXT NOT NULL DEFAULT '',
    payload        TEXT NOT NULL DEFAULT '{}',
    correlation_id TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_event_pending
    ON connector_events(status, received_at);
CREATE INDEX IF NOT EXISTS idx_event_resource
    ON connector_events(connection_id, resource_type, resource_id);

-- What happened, bounded. Survives a restart, which a counter does not.
CREATE TABLE IF NOT EXISTS connector_operations (
    id             TEXT PRIMARY KEY,
    connection_id  TEXT NOT NULL,
    connector      TEXT NOT NULL,
    operation      TEXT NOT NULL,
    resource_type  TEXT NOT NULL DEFAULT '',
    started_at     TEXT NOT NULL,
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    result         TEXT NOT NULL DEFAULT '',
    error_kind     TEXT NOT NULL DEFAULT '',
    detail         TEXT NOT NULL DEFAULT '',
    retries        INTEGER NOT NULL DEFAULT 0,
    records        INTEGER NOT NULL DEFAULT 0,
    correlation_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_operation_recent
    ON connector_operations(connector, started_at);
"""

#: Columns added after the first release. Same idiom as `core/db.py`: additive,
#: idempotent, and `ALTER TABLE ADD COLUMN` on a table that already has it is
#: caught rather than guarded, because SQLite has no `IF NOT EXISTS` for it.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, definition)
    #
    # **One record can become several memories.** Drive chunks a long document,
    # so `memory_id` — one column, one value — could only ever record the first
    # piece. That matters for exactly one feature and it is the one a user
    # notices: *delete the data this source imported* would have left every
    # chunk after the first orphaned in the brain, with nothing pointing at them.
    #
    # Additive, so a row written before this keeps working: `memory_id` still
    # holds the first id and `memory_ids()` reads both columns.
    ("connector_resources", "memory_ids", "TEXT NOT NULL DEFAULT '[]'"),
]

#: One connection for the life of the process.
#:
#: Opening a fresh one per call looks harmless and is not — `agents/
#: connector_grants.py` records what happened when it did: `execute` on one
#: handle and `commit` on another commits nothing, so a write was rolled back
#: when its connection was collected, while the abandoned handles piled up
#: until SQLite reported the database locked.
_DB: sqlite3.Connection | None = None
_LOCK = threading.Lock()


def connection() -> sqlite3.Connection:
    global _DB
    with _LOCK:
        if _DB is None:
            path = get_settings().home / "connectors.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            # The scheduler thread and request handlers share this, exactly as
            # they share the brain's. Same five seconds, same reason.
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.executescript(SCHEMA)
            _migrate(conn)
            _DB = conn
        return _DB


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        # "already there" is the expected outcome on every launch after the
        # first, and SQLite has no `ADD COLUMN IF NOT EXISTS`. Suppressed
        # rather than guarded with a `PRAGMA table_info` round trip, which is
        # the idiom `core/db.py::_migrate` already uses.
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    """A row as a plain dict, or {}.

    **`"key" in row` tests the VALUES, not the keys** — `/CLAUDE.md` names this
    as bought by a shipped bug, and `SIM118`/`SIM401` are disabled in
    `pyproject.toml` so nothing re-suggests the broken form. Going through
    `.keys()` here means no caller has to remember.
    """
    return {key: row[key] for key in row.keys()} if row is not None else {}


def reset_for_tests() -> None:
    """Forget the handle so the next call reopens against the current
    `CHITRAGUPTA_HOME`. Tests only — a process doing this at runtime would
    strand every in-flight transaction."""
    global _DB
    with _LOCK:
        if _DB is not None:
            _DB.close()
        _DB = None
