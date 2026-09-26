"""Where a pass got to, per connection and per resource, durably.

`base.py` already keeps a watermark and the rule it keeps it by is right:

    if result.cursor and not result.cancelled and not result.errors:
        state["cursor"] = result.cursor

A cursor from a half-finished pass would move past records nobody fetched.
Safe — and it means a 2,000-item sync that fails at item 1,900 starts again at
zero, every time, forever. The fix is not to relax the rule; it is to make the
unit smaller. **Checkpoint after each page is committed, not after the pass**,
and a crash costs one page.

Three things this adds that `connector_state` could not express:

* **Per connection.** `connector_state` is `PRIMARY KEY (connector)`. Two Gmail
  accounts shared one cursor, so each pass moved the other account's watermark.
* **Per resource type.** A connector reading files *and* comments has two
  positions. One cursor for both moves each past the other's records.
* **Per version.** A cursor written by connector version 1 means something
  under version 2 only if nothing about the fetch changed. `schema_version`
  says which wrote it, so a bump re-scans instead of resuming into data that
  now means something different.

## Attempt and success are different fields, deliberately

`last_attempt` moves whenever a pass starts. `last_success` moves only when one
finishes cleanly. A single `last_sync` cannot tell *"it ran nine minutes ago"*
from *"it last worked nine days ago"*, and those are the two different things a
health row has to say.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..log import get_logger
from .db import connection as _db
from .db import row_to_dict

log = get_logger(__name__)

#: A resource type for a connector that only ever reads one kind of thing.
#: Named rather than `""`, so a row is legible and a second resource type can
#: be added later without the first one's rows meaning "all of them".
DEFAULT_RESOURCE = "default"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_run_id() -> str:
    """One id for one pass, carried onto every operation and every record it
    produces. What makes *"which sync did this come from"* answerable."""
    return uuid.uuid4().hex[:12]


@dataclass
class SyncState:
    """One checkpoint."""

    connection_id: str
    resource_type: str = DEFAULT_RESOURCE
    cursor: str = ""
    last_success: str = ""
    last_attempt: str = ""
    items_processed: int = 0
    error: str = ""
    schema_version: int = 1
    run_id: str = ""
    started_at: str = ""

    @property
    def in_flight(self) -> bool:
        """Did a pass start and never report an ending?

        True after a crash, and the only way to tell a resumable position from
        a finished one. A `last_attempt` newer than `last_success` is the
        signal; a `started_at` with no matching finish is the same fact stated
        so it survives a clock change.
        """
        return bool(self.started_at)

    def resumable_from(self, version: int) -> str:
        """The cursor to resume from, or "" to start the window again.

        Empty on a version bump, which callers must read as *"fetch the normal
        window"* and never as *"fetch nothing"* — the same contract
        `base.Connector.since()` documents, for the same reason.
        """
        if self.schema_version != version:
            log.debug("%s/%s: cursor was written by version %d, now %d — "
                      "re-scanning", self.connection_id, self.resource_type,
                      self.schema_version, version)
            return ""
        return self.cursor

    def as_dict(self) -> dict[str, Any]:
        return {"connection_id": self.connection_id,
                "resource_type": self.resource_type,
                "cursor": self.cursor, "last_success": self.last_success,
                "last_attempt": self.last_attempt,
                "items_processed": self.items_processed, "error": self.error,
                "schema_version": self.schema_version, "run_id": self.run_id,
                "in_flight": self.in_flight}


def _from_row(row: Any) -> SyncState:
    data = row_to_dict(row)
    return SyncState(
        connection_id=data["connection_id"],
        resource_type=data.get("resource_type") or DEFAULT_RESOURCE,
        cursor=data.get("cursor") or "",
        last_success=data.get("last_success") or "",
        last_attempt=data.get("last_attempt") or "",
        items_processed=int(data.get("items_processed") or 0),
        error=data.get("error") or "",
        schema_version=int(data.get("schema_version") or 1),
        run_id=data.get("run_id") or "",
        started_at=data.get("started_at") or "")


def get(connection_id: str,
        resource_type: str = DEFAULT_RESOURCE) -> SyncState:
    """The checkpoint, or a fresh one. Never None — a connector that has never
    synced has a position, and that position is the beginning."""
    row = _db().execute(
        "SELECT * FROM connector_sync_state WHERE connection_id=? "
        "AND resource_type=?", (connection_id, resource_type)).fetchone()
    return _from_row(row) if row is not None else SyncState(
        connection_id=connection_id, resource_type=resource_type)


def for_connection(connection_id: str) -> list[SyncState]:
    rows = _db().execute(
        "SELECT * FROM connector_sync_state WHERE connection_id=? "
        "ORDER BY resource_type", (connection_id,)).fetchall()
    return [_from_row(r) for r in rows]


def _upsert(connection_id: str, resource_type: str, **columns: Any) -> None:
    conn = _db()
    conn.execute(
        """INSERT INTO connector_sync_state (connection_id, resource_type)
           VALUES (?,?) ON CONFLICT(connection_id, resource_type) DO NOTHING""",
        (connection_id, resource_type))
    if columns:
        assignments = ", ".join(f"{name}=?" for name in columns)
        conn.execute(
            f"UPDATE connector_sync_state SET {assignments} "
            f"WHERE connection_id=? AND resource_type=?",
            (*columns.values(), connection_id, resource_type))
    conn.commit()


def begin(connection_id: str, resource_type: str = DEFAULT_RESOURCE, *,
          run_id: str = "") -> str:
    """Record that a pass has started. Returns its run id.

    `last_attempt` moves here and `last_success` does not, which is what lets a
    health row say *"tried nine minutes ago, last worked nine days ago"*.
    """
    run = run_id or new_run_id()
    now = _now()
    _upsert(connection_id, resource_type,
            last_attempt=now, started_at=now, run_id=run, error="")
    return run


def checkpoint(connection_id: str, resource_type: str = DEFAULT_RESOURCE, *,
               cursor: str, items: int = 0, version: int = 1) -> None:
    """Record a position that is **already committed**.

    Called after a page has been ingested, never before. The ordering is the
    whole mechanism: checkpoint-then-ingest loses a page on a crash and reports
    success for it, which is worse than losing the pass — the records are gone
    and nothing will go back for them.

    `items` accumulates within the pass, so a resumed sync reports what it has
    done in total rather than what this attempt did.
    """
    conn = _db()
    conn.execute(
        """INSERT INTO connector_sync_state
             (connection_id, resource_type, cursor, items_processed,
              schema_version)
           VALUES (?,?,?,?,?)
           ON CONFLICT(connection_id, resource_type) DO UPDATE SET
             cursor=excluded.cursor,
             items_processed=connector_sync_state.items_processed
                             + excluded.items_processed,
             schema_version=excluded.schema_version""",
        (connection_id, resource_type, cursor, max(0, items), version))
    conn.commit()


def complete(connection_id: str, resource_type: str = DEFAULT_RESOURCE, *,
             cursor: str | None = None, version: int = 1) -> None:
    """A pass finished cleanly.

    `cursor=None` keeps whatever the last checkpoint wrote, which is the normal
    case for a paged walk: the pages already moved it, and re-writing it here
    from a variable the caller has been carrying is how the two disagree.
    """
    columns: dict[str, Any] = {"last_success": _now(), "started_at": "",
                               "error": "", "schema_version": version}
    if cursor is not None:
        columns["cursor"] = cursor
    _upsert(connection_id, resource_type, **columns)


def fail(connection_id: str, resource_type: str = DEFAULT_RESOURCE, *,
         error: str) -> None:
    """A pass ended badly.

    **The cursor is left exactly where the last committed page put it.** That
    is the resumable half: the pass failed, the pages that landed still landed,
    and the next attempt starts from the last one that did — rather than from
    zero, which is what `base._finish` has to do because its unit is the pass.
    """
    from .errors import scrub

    _upsert(connection_id, resource_type,
            error=scrub(error)[:300], started_at="")


def interrupted(connection_id: str, resource_type: str = DEFAULT_RESOURCE, *,
                reason: str = "cancelled") -> None:
    """A pass was stopped on purpose. Same cursor rule as `fail`."""
    _upsert(connection_id, resource_type, error="", started_at="")
    log.debug("%s/%s: %s", connection_id, resource_type, reason)


def recover(connection_id: str) -> list[SyncState]:
    """Passes that started and never reported an ending — a crash, a kill, a
    power cut. Called on launch.

    They are **reported, not reset**: the cursor is the last committed page and
    is correct, and the only thing wrong is the in-flight marker. Clearing that
    is what lets the next pass resume rather than be refused as already
    running.
    """
    rows = _db().execute(
        "SELECT * FROM connector_sync_state WHERE connection_id=? "
        "AND started_at != ''", (connection_id,)).fetchall()
    found = [_from_row(r) for r in rows]
    for state in found:
        _upsert(state.connection_id, state.resource_type, started_at="")
        log.info("%s/%s: resuming from the last committed page (%s)",
                 state.connection_id, state.resource_type,
                 state.cursor or "the beginning")
    return found


def clear(connection_id: str, resource_type: str | None = None) -> None:
    """Forget a position, so the next pass re-reads the window.

    What *Sync everything again* does. Deliberately does not touch what was
    already ingested: dedup absorbs the re-read, and deleting the user's data
    to refresh it would be a far larger promise than the button makes.
    """
    conn = _db()
    if resource_type is None:
        conn.execute("DELETE FROM connector_sync_state WHERE connection_id=?",
                     (connection_id,))
    else:
        conn.execute("DELETE FROM connector_sync_state WHERE connection_id=? "
                     "AND resource_type=?", (connection_id, resource_type))
    conn.commit()
