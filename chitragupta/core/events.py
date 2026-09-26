"""One shape for "something happened", and a memory of what we have seen.

The automation engine used to ask `sweep(new_email_count=3)`. A count is not an
event: it has no identity, so it cannot be deduplicated; it has no payload, so
every trigger needed its own branch inside the engine; and it has no origin, so
nothing downstream could tell a calendar entry the user wrote from an email a
stranger sent.

This module is the fix, and it is deliberately a **leaf**. A connector emits an
event by importing this; it never imports the automation engine, and the engine
never imports a connector. That is the whole of "adding a connector event must
not require modifying the engine" — a new event is a new `Event`, not a new
branch.

**Nothing here decides anything.** No matching, no conditions, no execution.
This is the shape and the ledger; `automation/router.py` is the opinion.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..log import get_logger, suppressed
from .provenance import Trust

log = get_logger(__name__)

#: How long a seen event id is remembered. Long enough that a provider
#: redelivering after an outage is still recognised; short enough that the
#: table does not grow without bound. Providers that promise at-least-once
#: delivery generally retry for minutes, not days.
DEDUP_WINDOW_HOURS = 72

#: A run may cause an event, which may start a run. That is a feature — it is
#: how "automation A completes -> automation B starts" works — and it is also
#: how a system eats itself. Past this depth an event is refused at ingest,
#: before anything matches on it.
MAX_EVENT_DEPTH = 4


@dataclass(frozen=True)
class Event:
    """Something that happened, in the one shape the engine understands.

    `kind` is a dotted noun-verb (`email.received`, `schedule.tick`,
    `file.changed`, `automation.completed`). The engine matches on it as an
    opaque string and never parses it, so a connector may invent one.
    """

    kind: str
    source: str
    #: The provider's own id for this event, where one exists. Half of the
    #: deduplication key — see `dedup_key`. Empty is allowed and is handled by
    #: hashing the payload, which is weaker and is why it is not the default.
    external_id: str = ""
    #: A short human label. Shown in run history; never parsed.
    subject: str = ""
    #: The normalised payload. Flat, JSON-serialisable, and **data** — a
    #: condition reads it, a model may be shown it behind a fence, and nothing
    #: in it is ever an instruction.
    data: dict[str, Any] = field(default_factory=dict)
    #: How far the *free text* inside `data` may be believed. The engine fences
    #: anything at or below `provenance.FENCED_AT` before a model sees it.
    trust: Trust = Trust.CONNECTED_SOURCE
    occurred_at: str = ""
    #: Lineage. An event caused by an automation carries that run's id and one
    #: more than its depth, which is what makes A -> event -> A terminate.
    caused_by_run: str = ""
    caused_by_automation: str = ""
    depth: int = 0
    #: Ties every run and event descended from one original cause together, so
    #: a loop is visible in history rather than only prevented.
    correlation_id: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not self.occurred_at:
            object.__setattr__(self, "occurred_at", datetime.now(UTC).isoformat())
        if not self.correlation_id:
            object.__setattr__(self, "correlation_id", self.id)

    @property
    def dedup_key(self) -> str:
        """What makes two deliveries the same event.

        `source` is part of it because ids are only unique within a provider,
        and a Gmail message id colliding with a Linear issue id would otherwise
        silently suppress one of them.

        With no `external_id` the payload is hashed instead. That is genuinely
        weaker — two distinct events with identical payloads collapse — so the
        hash is bucketed by the hour, which bounds the damage to "a repeat
        within the same hour is treated as a repeat". A connector that can
        supply an id should.
        """
        if self.external_id:
            return f"{self.source}:{self.external_id}"
        payload = json.dumps(self.data, sort_keys=True, default=str)
        digest = hashlib.sha256(
            f"{self.kind}|{self.subject}|{payload}".encode()).hexdigest()[:32]
        bucket = (self.occurred_at or "")[:13]        # YYYY-MM-DDTHH
        return f"{self.source}:~{digest}:{bucket}"

    def child(self, **kw: Any) -> Event:
        """An event caused by this one, carrying the lineage forward."""
        kw.setdefault("depth", self.depth + 1)
        kw.setdefault("correlation_id", self.correlation_id)
        return Event(**kw)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["trust"] = int(self.trust)
        return out

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Event:
        data = dict(raw or {})
        data["trust"] = Trust(int(data.get("trust") or Trust.CONNECTED_SOURCE))
        known = set(Event.__dataclass_fields__)
        return Event(**{k: v for k, v in data.items() if k in known})


_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_events (
    dedup_key   TEXT PRIMARY KEY,
    event_id    TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_seen_first ON seen_events(first_seen);
"""

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
    conn.commit()
    _CONN = conn
    return conn


def reset_for_tests(path: Path | None = None) -> None:
    """Drop the cached handle, optionally pointing the next one elsewhere."""
    global _CONN, _PATH_OVERRIDE
    if _CONN is not None:
        with suppressed("closing the event ledger between tests"):
            _CONN.close()
    _CONN = None
    _PATH_OVERRIDE = path


def is_duplicate(event: Event) -> bool:
    """Have we already accepted this exact event?

    The check and the record are **one transaction**, because the whole point
    is that two deliveries racing must not both win. `INSERT ... ON CONFLICT`
    does it in one statement: the second caller's insert conflicts, updates the
    hit counter, and reports the duplicate.

    Providers do not promise exactly-once. Gmail redelivers after an outage,
    a webhook retries when our 200 was slow, and a sync that resumes from a
    cursor re-reads the boundary row. All three arrive here.
    """
    key = event.dedup_key
    now = datetime.now(UTC).isoformat()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO seen_events (dedup_key, event_id, kind, source, first_seen) "
            "VALUES (?,?,?,?,?) ON CONFLICT(dedup_key) DO UPDATE SET hits = hits + 1 "
            "RETURNING hits",
            (key, event.id, event.kind, event.source, now))
        hits = int(cur.fetchone()[0])
        conn.commit()
    if hits > 1:
        log.debug("duplicate event %s (%s), seen %d times", key, event.kind, hits)
    return hits > 1


def forget_old(hours: int = DEDUP_WINDOW_HOURS) -> int:
    """Drop dedup records older than the window. Returns how many went."""
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _LOCK:
        conn = _conn()
        cur = conn.execute("DELETE FROM seen_events WHERE first_seen < ?", (cutoff,))
        conn.commit()
    return cur.rowcount or 0


def seen_count(event: Event) -> int:
    """How many times this event's key has arrived. For tests and history."""
    row = _conn().execute(
        "SELECT hits FROM seen_events WHERE dedup_key=?",
        (event.dedup_key,)).fetchone()
    return int(row["hits"]) if row else 0
