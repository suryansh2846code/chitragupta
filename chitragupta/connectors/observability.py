"""What a connector did, recorded so it can be asked about afterwards.

The evidence a connector failure left before this was a `suppressed()` line in
a rotating log — good enough to read when you already know what you are looking
for, and no help at all for the questions a user or a developer actually has:

* Which sync put this memory here?
* How long does a Gmail pass take, and is it getting slower?
* How many times did we retry Slack yesterday?
* Is this connector making N+1 requests?

Each is a query, and none of them is answerable against prose.

## One `correlation_id` from the sync to the memory

A run id is minted once per pass (`sync_state.new_run_id`), rides on every
operation recorded here, and lands in each record's provenance
(`SourceRef.run_id`). So *"which pass produced this"* is a join rather than a
guess — and *"what else did that pass do"* is the same join backwards.

## Bounded, and never a second logging system

Rows are pruned to a ceiling, so a connector in a retry loop cannot fill the
disk. `detail` is scrubbed on the way in, for the same reason
`ConnectorError.detail` is: this is read by a diagnostics screen, and a screen
is a place a credential must never reach.

This is not a metrics library and deliberately not one: a dependency here is a
dependency to ship, sign and notarise, for two readers.
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..log import get_logger
from .db import connection as _db
from .db import row_to_dict

log = get_logger(__name__)

#: Operations kept. Roughly a fortnight of a busy install; older than that and
#: the sync state is the record that matters.
MAX_KEPT = 20000


class Outcome(StrEnum):
    OK = "ok"
    #: Finished, but not with everything — a truncated page walk, a partial
    #: harvest. Distinct from `OK` because "we got some of it" is a different
    #: thing to tell a user than "we got it".
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: Never started — paused, not configured, out of budget.
    SKIPPED = "skipped"


@dataclass
class Operation:
    """One thing a connector did."""

    connector: str
    operation: str
    connection_id: str = ""
    resource_type: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat())
    duration_ms: int = 0
    result: Outcome = Outcome.OK
    error_kind: str = ""
    detail: str = ""
    retries: int = 0
    records: int = 0
    correlation_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "connector": self.connector,
                "connection_id": self.connection_id,
                "operation": self.operation,
                "resource_type": self.resource_type,
                "started_at": self.started_at, "duration_ms": self.duration_ms,
                "result": self.result.value, "error_kind": self.error_kind,
                "detail": self.detail, "retries": self.retries,
                "records": self.records,
                "correlation_id": self.correlation_id}


def write(operation: Operation) -> None:
    from .errors import scrub

    conn = _db()
    conn.execute(
        """INSERT INTO connector_operations
             (id, connection_id, connector, operation, resource_type,
              started_at, duration_ms, result, error_kind, detail, retries,
              records, correlation_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (operation.id, operation.connection_id, operation.connector,
         operation.operation, operation.resource_type, operation.started_at,
         operation.duration_ms, operation.result.value, operation.error_kind,
         scrub(operation.detail)[:300], operation.retries, operation.records,
         operation.correlation_id))
    conn.commit()


@contextmanager
def recorded(connector: str, operation: str, *, connection_id: str = "",
             resource_type: str = "",
             correlation_id: str = "") -> Iterator[Operation]:
    """Time one operation and record how it ended, however it ends.

        with recorded("gmail", "sync", correlation_id=run) as op:
            op.records = len(messages)

    The `finally` is the point: an operation that raises is recorded as failed
    rather than not recorded at all, and *"nothing in the log"* is exactly what
    a crash looks like without it.
    """
    record = Operation(connector=connector, operation=operation,
                       connection_id=connection_id,
                       resource_type=resource_type,
                       correlation_id=correlation_id)
    started = time.monotonic()
    try:
        yield record
    except BaseException as exc:
        record.result = Outcome.FAILED
        record.error_kind = getattr(getattr(exc, "kind", None), "value", "") \
            or type(exc).__name__
        record.detail = str(exc)
        raise
    finally:
        record.duration_ms = int((time.monotonic() - started) * 1000)
        # Never let recording a failure become a second failure. A connector
        # that worked must not be reported as broken because the audit row
        # would not write.
        try:
            write(record)
        except Exception:
            log.debug("could not record %s/%s", connector, operation,
                      exc_info=True)


def recent(connector: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
    """The last operations, newest first. Bounded — a screen reads this."""
    if connector:
        rows = _db().execute(
            "SELECT * FROM connector_operations WHERE connector=? "
            "ORDER BY started_at DESC LIMIT ?",
            (connector, max(1, limit))).fetchall()
    else:
        rows = _db().execute(
            "SELECT * FROM connector_operations ORDER BY started_at DESC "
            "LIMIT ?", (max(1, limit),)).fetchall()
    return [row_to_dict(r) for r in rows]


def for_run(correlation_id: str) -> list[dict[str, Any]]:
    """Everything one pass did. The join a run id exists for."""
    rows = _db().execute(
        "SELECT * FROM connector_operations WHERE correlation_id=? "
        "ORDER BY started_at", (correlation_id,)).fetchall()
    return [row_to_dict(r) for r in rows]


def metrics(connector: str = "", *, since: str = "") -> dict[str, Any]:
    """The numbers, aggregated in SQL rather than in Python.

    Aggregating in Python would mean loading every row to count them, which is
    the N+1 shape `docs/SCALING.md` records the brain having twice. A screen
    that costs a full table scan is a screen nobody opens twice.
    """
    where, params = ["1=1"], []
    if connector:
        where.append("connector=?")
        params.append(connector)
    if since:
        where.append("started_at >= ?")
        params.append(since)
    clause = " AND ".join(where)

    row = _db().execute(
        f"""SELECT COUNT(*) AS operations,
                   COALESCE(SUM(records), 0) AS records,
                   COALESCE(SUM(retries), 0) AS retries,
                   COALESCE(AVG(duration_ms), 0) AS mean_ms,
                   COALESCE(MAX(duration_ms), 0) AS slowest_ms,
                   COALESCE(SUM(result = 'failed'), 0) AS failures,
                   COALESCE(SUM(result = 'partial'), 0) AS partial,
                   COALESCE(SUM(error_kind = 'rate_limited'), 0) AS rate_limited,
                   COALESCE(SUM(error_kind = 'authentication'), 0) AS auth_failures
            FROM connector_operations WHERE {clause}""", params).fetchone()
    found = row_to_dict(row)
    operations = int(found.get("operations") or 0)
    return {
        "connector": connector or "all",
        "operations": operations,
        "records": int(found.get("records") or 0),
        "retries": int(found.get("retries") or 0),
        "mean_ms": round(float(found.get("mean_ms") or 0.0), 1),
        "slowest_ms": int(found.get("slowest_ms") or 0),
        "failures": int(found.get("failures") or 0),
        "partial": int(found.get("partial") or 0),
        "rate_limited": int(found.get("rate_limited") or 0),
        "auth_failures": int(found.get("auth_failures") or 0),
        # Reported rather than left to the reader to divide, because a rate is
        # the thing anybody actually compares and a count is not.
        "failure_rate": round(
            (int(found.get("failures") or 0) / operations) if operations else 0.0,
            4),
    }


def snapshot() -> dict[str, Any]:
    """Everything the diagnostics screen shows in one call.

    One call rather than four, because the page would otherwise make four
    requests to render one panel — the N+1 shape again, one level up.
    """
    from . import events, limits

    return {"metrics": metrics(), "queue_depth": events.depth(),
            "gates": limits.all_snapshots(), "recent": recent(limit=25)}


def prune(keep: int = MAX_KEPT) -> int:
    conn = _db()
    cursor = conn.execute(
        """DELETE FROM connector_operations WHERE id NOT IN (
               SELECT id FROM connector_operations
               ORDER BY started_at DESC LIMIT ?)""", (max(0, keep),))
    conn.commit()
    return cursor.rowcount
