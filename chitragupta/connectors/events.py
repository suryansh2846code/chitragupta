"""Something changed somewhere else, in one shape the automation layer can read.

The event system before this was one integer:

    def sweep(new_email_count: int = 0) -> None:
        ...
        elif r["trigger"] == "new_email" and new_email_count > 0: ...

One connector, one trigger, one number, derived from a sync's added-count. An
automation could not ask *which* email, could not fire on a calendar change at
all, and would fire twice if two syncs each added one.

An `ExternalEvent` is what a routine consumes instead, and the point of
normalising is that **the automation layer never learns Gmail's or Slack's or
GitHub's shapes**. `resource.updated` means the same thing in all three.

## Three things providers do that a naive consumer gets wrong

* **They send duplicates.** Nobody promises exactly-once; several explicitly
  promise at-least-once. Dedup is durable — a `PRIMARY KEY` — because an
  in-memory set forgets on restart, which is exactly when a provider redelivers.
* **They arrive out of order.** An `updated` from ten minutes ago can land
  after one from now. `supersedes` answers by comparing the provider's own
  ordering, not arrival — and when a provider offers no ordering at all, it
  says so rather than inventing one.
* **They go missing.** Which is why polling remains the floor and events are an
  accelerator: an event that never arrives is a change the next sync finds
  anyway. `docs/CONNECTOR-PLATFORM.md` §7 — no webhook receiver ships, so today
  every event here is minted from a poll.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..log import get_logger
from .db import connection as _db
from .db import row_to_dict

log = get_logger(__name__)

#: Kept so a provider that floods cannot fill the disk. Old events are history
#: the sync state already covers — the cursor is the durable position, and an
#: event is a hint that arrived once.
MAX_KEPT = 5000


class EventType(StrEnum):
    """What happened, in words that mean the same thing for every provider."""

    CREATED = "resource.created"
    UPDATED = "resource.updated"
    DELETED = "resource.deleted"
    #: We can no longer see it. Not the same as deleted — see `resources.py`.
    UNAVAILABLE = "resource.unavailable"
    #: The credential changed state. Not about a resource at all, and the one
    #: an automation should react to by pausing rather than by working harder.
    AUTH_CHANGED = "connection.auth_changed"


class EventStatus(StrEnum):
    NEW = "new"
    PROCESSED = "processed"
    #: A newer event about the same resource arrived and won.
    SUPERSEDED = "superseded"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ExternalEvent:
    """One change, normalised."""

    connection_id: str
    event_type: EventType
    resource_type: str
    resource_id: str = ""
    #: The provider's own id for the event, where it has one. **Preferred over
    #: anything we could mint**, because it is the only value that is stable
    #: across a redelivery: a digest of the payload changes if the provider
    #: re-serialises, and a timestamp changes if it retries.
    provider_event_id: str = ""
    #: When it happened, per the provider. Empty when it did not say.
    occurred_at: str = ""
    received_at: str = field(default_factory=_now)
    #: Whatever orders these for this provider — a sequence number, a history
    #: id, a monotonic timestamp. Compared as a string unless it is numeric;
    #: see `supersedes`.
    sequence: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    #: Ties an event to the run that produced it and to everything downstream.
    correlation_id: str = ""

    @property
    def id(self) -> str:
        """The dedup key.

        The provider's own id when there is one. Otherwise a digest of the
        identity **and the moment** — never of the payload, which a provider
        may re-serialise between deliveries, and never of the identity alone,
        which would collapse every update to one resource into a single event
        forever.
        """
        if self.provider_event_id:
            return f"{self.connection_id}:{self.provider_event_id}"
        material = "\x1f".join([
            self.connection_id, self.event_type.value, self.resource_type,
            self.resource_id, self.occurred_at or self.sequence or "",
        ])
        digest = hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()
        return f"{self.connection_id}:{digest[:24]}"

    @property
    def about_a_resource(self) -> bool:
        return self.event_type is not EventType.AUTH_CHANGED

    def supersedes(self, other: ExternalEvent | None) -> bool:
        """Is this newer than `other`, by the provider's own reckoning?

        **Never by arrival time.** An `updated` from ten minutes ago can land
        after one from now, and overwriting newer state with an older event is
        the corruption this method exists to prevent.

        When the provider offers no ordering — no sequence, no `occurred_at` —
        the honest answer is **False**: we do not know, so nothing is
        overwritten, and the next sync reads the resource and settles it. A
        `True` here would be a guess with a write behind it.
        """
        if other is None:
            return True
        mine, theirs = self.sequence, other.sequence
        if mine and theirs:
            try:
                return int(mine) > int(theirs)
            except ValueError:
                return mine > theirs
        if self.occurred_at and other.occurred_at:
            return self.occurred_at > other.occurred_at
        return False

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "connection_id": self.connection_id,
                "event_type": self.event_type.value,
                "resource_type": self.resource_type,
                "resource_id": self.resource_id,
                "occurred_at": self.occurred_at,
                "received_at": self.received_at, "sequence": self.sequence,
                "payload": dict(self.payload),
                "correlation_id": self.correlation_id}


def _from_row(row: Any) -> ExternalEvent:
    data = row_to_dict(row)
    try:
        payload = json.loads(data.get("payload") or "{}")
    except ValueError:
        payload = {}
    try:
        kind = EventType(data["event_type"])
    except ValueError:
        # An event written by a newer version. Kept as an update, which is the
        # conservative reading: something changed and we should look.
        kind = EventType.UPDATED
    return ExternalEvent(
        connection_id=data["connection_id"], event_type=kind,
        # Restored, so a row read back reproduces its own primary key. It did
        # not, once: `mark()` addressed a recomputed digest that no row had,
        # updated nothing, and every event stayed pending forever.
        provider_event_id=data.get("provider_event_id") or "",
        resource_type=data.get("resource_type") or "record",
        resource_id=data.get("resource_id") or "",
        occurred_at=data.get("occurred_at") or "",
        received_at=data.get("received_at") or "",
        sequence=data.get("sequence") or "",
        payload=payload if isinstance(payload, dict) else {},
        correlation_id=data.get("correlation_id") or "")


def record(event: ExternalEvent) -> bool:
    """Store an event. Returns False if it was already known.

    The duplicate case is **not** an error and not a log line at warning level:
    at-least-once is the contract providers actually offer, so a redelivery is
    the system working. What would be a bug is acting on it twice.
    """
    conn = _db()
    cursor = conn.execute(
        """INSERT INTO connector_events
             (id, connection_id, provider_event_id, event_type, resource_type,
              resource_id, occurred_at, received_at, sequence, payload,
              correlation_id, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO NOTHING""",
        (event.id, event.connection_id, event.provider_event_id,
         event.event_type.value,
         event.resource_type, event.resource_id, event.occurred_at,
         event.received_at, event.sequence,
         json.dumps(event.payload, default=str)[:20000],
         event.correlation_id, EventStatus.NEW.value))
    conn.commit()
    return cursor.rowcount > 0


def latest_for(connection_id: str, resource_type: str,
               resource_id: str) -> ExternalEvent | None:
    """The most recent event we hold about one resource.

    Ordered by the provider's `sequence` and `occurred_at` rather than by
    arrival — the same rule `supersedes` applies, because reading them back in
    arrival order would undo it.
    """
    row = _db().execute(
        "SELECT * FROM connector_events WHERE connection_id=? "
        "AND resource_type=? AND resource_id=? "
        "ORDER BY sequence DESC, occurred_at DESC, received_at DESC LIMIT 1",
        (connection_id, resource_type, resource_id)).fetchone()
    return _from_row(row) if row is not None else None


def accept(event: ExternalEvent) -> bool:
    """Record an event, and say whether it is worth acting on.

    False for a duplicate, and false for one the provider's own ordering says
    is stale. Both are stored either way — a stale event is evidence about what
    the provider did, and discarding it makes an out-of-order delivery
    invisible rather than harmless.
    """
    if event.about_a_resource:
        held = latest_for(event.connection_id, event.resource_type,
                          event.resource_id)
        fresh = record(event)
        if not fresh:
            return False
        if held is not None and not event.supersedes(held):
            mark(event.id, EventStatus.SUPERSEDED)
            log.debug("%s arrived out of order and was not acted on", event.id)
            return False
        return True
    return record(event)


def pending(limit: int = 200) -> list[ExternalEvent]:
    """Events nobody has acted on yet, oldest first.

    Bounded, because this is the queue an automation sweep drains and an
    unbounded read is how one noisy connector takes a sweep's whole budget.
    """
    rows = _db().execute(
        "SELECT * FROM connector_events WHERE status=? "
        "ORDER BY received_at LIMIT ?",
        (EventStatus.NEW.value, max(1, limit))).fetchall()
    return [_from_row(r) for r in rows]


def mark(event_id: str, status: EventStatus) -> None:
    conn = _db()
    conn.execute("UPDATE connector_events SET status=? WHERE id=?",
                 (status.value, event_id))
    conn.commit()


def depth() -> int:
    """How many events are waiting. The backpressure signal `engine.py` reads
    and the diagnostics screen shows."""
    row = _db().execute("SELECT COUNT(*) AS n FROM connector_events "
                        "WHERE status=?", (EventStatus.NEW.value,)).fetchone()
    return int(row["n"]) if row is not None else 0


def prune(keep: int = MAX_KEPT) -> int:
    """Drop the oldest processed events past `keep`.

    Only processed and superseded ones: a pending event is work nobody has done
    and deleting it loses it. The durable position is the sync cursor, so
    pruning history costs nothing a later pass cannot rediscover.
    """
    conn = _db()
    cursor = conn.execute(
        """DELETE FROM connector_events WHERE status != ? AND id NOT IN (
               SELECT id FROM connector_events WHERE status != ?
               ORDER BY received_at DESC LIMIT ?)""",
        (EventStatus.NEW.value, EventStatus.NEW.value, max(0, keep)))
    conn.commit()
    return cursor.rowcount


def from_resource_change(connection_id: str, resource_type: str,
                         external_id: str, verdict: Any, *,
                         occurred_at: str = "", sequence: str = "",
                         correlation_id: str = "",
                         payload: dict[str, Any] | None = None
                         ) -> ExternalEvent | None:
    """The event a sync's own findings imply, or None when nothing changed.

    This is what makes *polling* an event source rather than a separate world:
    the sync already decided new-versus-changed-versus-unchanged
    (`resources.classify`), and turning that into an `ExternalEvent` means an
    automation consumes one shape whether a webhook or a timer produced it.
    """
    from .resources import Verdict

    kind = {
        Verdict.NEW: EventType.CREATED,
        Verdict.CHANGED: EventType.UPDATED,
        Verdict.RETURNED: EventType.CREATED,
    }.get(verdict)
    if kind is None:            # UNCHANGED — nothing happened, say nothing
        return None
    return ExternalEvent(
        connection_id=connection_id, event_type=kind,
        resource_type=resource_type, resource_id=external_id,
        occurred_at=occurred_at, sequence=sequence,
        correlation_id=correlation_id, payload=dict(payload or {}))
