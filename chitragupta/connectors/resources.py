"""A stable identity for one thing in an external system, and what became of it.

Dedup in this app is a **content hash**: `idx_memories_chash` over
`hash(text, uri)`. That is exactly right for what it was built for — re-reading
the same email twice costs a skip — and it is wrong for the two cases that
matter once a connector syncs continuously:

* **An edited record is a new memory.** Change a document's second paragraph
  and the hash changes, so the brain now holds two versions and recall cites
  whichever scores higher. There is nothing to supersede *with*, because
  nothing ties the two rows together.
* **A deleted record is never deleted.** Nothing knows the memory came from
  file `1a2b3c`, so when that file is gone there is no way to say so. The
  brain keeps citing it, indefinitely.

Both need the same missing thing: identity that survives a content change.

    provider + connection + resource type + external id

Not the title (renamed), not the text (edited), not the URL (moved), not the
timestamp (touched). The vendor's own id for the thing, scoped to the account
it came from — because `1a2b3c` in the work Drive and `1a2b3c` in the personal
one are different files.

## Four states, and none of them erase

`ACTIVE` · `UPDATED` · `DELETED` · `UNAVAILABLE`

A tombstone marks; it does not remove. The user's data stays the user's data —
a file disappearing from Drive is not consent to forget a year of notes about
it, and *"this is no longer in Drive"* is a more useful thing for an agent to
know than silence. `UNAVAILABLE` is separate from `DELETED` for the reason the
whole module exists: *we lost access* and *it was destroyed* are different
facts, and a 403 that got filed as a deletion would quietly rewrite history
every time a token narrowed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..log import get_logger
from .db import connection as _db
from .db import row_to_dict

log = get_logger(__name__)


class ResourceState(StrEnum):
    """What became of one external record."""

    #: Present at the source, and what we hold matches.
    ACTIVE = "active"
    #: Present, and changed since we last read it.
    UPDATED = "updated"
    #: The source says it is gone. A tombstone, not an erasure.
    DELETED = "deleted"
    #: We can no longer see it. Access narrowed, a share was revoked, a
    #: workspace changed hands. **Not** the same as deleted, and conflating
    #: them rewrites history every time a token loses a scope.
    UNAVAILABLE = "unavailable"


#: States where what we hold should no longer be presented as current.
#: Recall may still surface it — the memory is the user's — but an agent
#: reading one of these is told it is not live.
STALE = frozenset({ResourceState.DELETED, ResourceState.UNAVAILABLE})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(*parts: Any) -> str:
    """A short digest of whatever the connector says identifies this version.

    A *change* detector, not an identity: the identity is `external_id`. Which
    fields go in is the connector's call, because only it knows what counts as
    a change — Drive's `modifiedTime` is authoritative, and a Slack message's
    `edited.ts` is, and neither is a general rule.
    """
    # A missing field and an empty one are different facts, so they get
    # different digests: collapsing them means a record that LOST a field
    # reads as unchanged and is never re-ingested.
    blob = "\x1f".join("\x00" if p is None else str(p) for p in parts)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:32]


@dataclass
class Resource:
    """One external record, as this app knows it."""

    connection_id: str
    resource_type: str
    external_id: str
    memory_id: str = ""
    state: ResourceState = ResourceState.ACTIVE
    fingerprint: str = ""
    source_updated_at: str = ""
    first_seen: str = ""
    last_seen: str = ""

    @property
    def stale(self) -> bool:
        return self.state in STALE

    @property
    def key(self) -> str:
        """The identity, as one string, for a log line or a provenance field."""
        return f"{self.connection_id}/{self.resource_type}/{self.external_id}"

    def as_dict(self) -> dict[str, Any]:
        return {"connection_id": self.connection_id,
                "resource_type": self.resource_type,
                "external_id": self.external_id, "memory_id": self.memory_id,
                "state": self.state.value, "stale": self.stale,
                "fingerprint": self.fingerprint,
                "source_updated_at": self.source_updated_at,
                "first_seen": self.first_seen, "last_seen": self.last_seen}


def _from_row(row: Any) -> Resource:
    data = row_to_dict(row)
    try:
        state = ResourceState(data.get("state") or "active")
    except ValueError:
        state = ResourceState.ACTIVE
    return Resource(
        connection_id=data["connection_id"],
        resource_type=data["resource_type"],
        external_id=data["external_id"],
        memory_id=data.get("memory_id") or "", state=state,
        fingerprint=data.get("fingerprint") or "",
        source_updated_at=data.get("source_updated_at") or "",
        first_seen=data.get("first_seen") or "",
        last_seen=data.get("last_seen") or "")


def get(connection_id: str, resource_type: str,
        external_id: str) -> Resource | None:
    row = _db().execute(
        "SELECT * FROM connector_resources WHERE connection_id=? "
        "AND resource_type=? AND external_id=?",
        (connection_id, resource_type, external_id)).fetchone()
    return _from_row(row) if row is not None else None


class Verdict(StrEnum):
    """What a connector should do with a record it just fetched."""

    #: Never seen. Ingest it.
    NEW = "new"
    #: Seen, and it has changed. Ingest it and supersede what we held.
    CHANGED = "changed"
    #: Seen, unchanged. Skip it without reading the body — which is the point:
    #: `gdrive` currently downloads a file to find out it already has it.
    UNCHANGED = "unchanged"
    #: Seen, and previously marked gone. It is back.
    RETURNED = "returned"


def classify(connection_id: str, resource_type: str, external_id: str, *,
             fingerprint_: str = "",
             source_updated_at: str = "") -> tuple[Verdict, Resource | None]:
    """Is this record new, changed, unchanged, or back from the dead?

    Answered **before** the body is fetched wherever the provider's listing
    carries a modified time, which is what turns a sync from "download
    everything and let the hash sort it out" into one that fetches what
    changed. `gdrive.py` already reaches for this idea by hand with a set of
    `(file_id, modifiedTime)` built from the memories table on every pass; this
    is the same question asked of a table that exists to answer it.
    """
    held = get(connection_id, resource_type, external_id)
    if held is None:
        return Verdict.NEW, None
    if held.stale:
        return Verdict.RETURNED, held
    same = (fingerprint_ and fingerprint_ == held.fingerprint) or (
        not fingerprint_ and source_updated_at
        and source_updated_at == held.source_updated_at)
    return (Verdict.UNCHANGED if same else Verdict.CHANGED), held


def seen(connection_id: str, resource_type: str, external_id: str, *,
         memory_id: str = "", memories: list[str] | None = None,
         fingerprint_: str = "", source_updated_at: str = "",
         state: ResourceState = ResourceState.ACTIVE) -> Resource:
    """Record that we have this version of this record.

    `first_seen` is preserved across updates — it is when this *thing* entered
    the brain, and resetting it on every edit would make every record look new
    on the day it was last touched.

    **One record can become several memories.** Drive chunks a long document, so
    `memories` takes the whole list while `memory_id` keeps the first for every
    reader written before this existed. Recording only the first is what would
    leave the rest orphaned when a user asks to delete what a source imported.
    """
    ids = [m for m in (memories or ([memory_id] if memory_id else [])) if m]
    now = _now()
    conn = _db()
    conn.execute(
        """INSERT INTO connector_resources
             (connection_id, resource_type, external_id, memory_id, memory_ids,
              state, fingerprint, source_updated_at, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(connection_id, resource_type, external_id) DO UPDATE SET
             memory_id=CASE WHEN excluded.memory_id != ''
                            THEN excluded.memory_id
                            ELSE connector_resources.memory_id END,
             memory_ids=CASE WHEN excluded.memory_ids != '[]'
                             THEN excluded.memory_ids
                             ELSE connector_resources.memory_ids END,
             state=excluded.state,
             fingerprint=excluded.fingerprint,
             source_updated_at=excluded.source_updated_at,
             last_seen=excluded.last_seen""",
        (connection_id, resource_type, external_id, ids[0] if ids else "",
         json.dumps(ids), state.value, fingerprint_, source_updated_at,
         now, now))
    conn.commit()
    found = get(connection_id, resource_type, external_id)
    assert found is not None       # just written
    return found


def mark(connection_id: str, resource_type: str, external_id: str,
         state: ResourceState, *, detail: str = "") -> Resource | None:
    """Record that a resource changed state — gone, or out of reach.

    **A tombstone marks; it does not remove.** The memory stays, because a file
    disappearing from Drive is not consent to forget a year of notes about it,
    and *"this is no longer in Drive"* is more useful to an agent than silence.
    """
    conn = _db()
    conn.execute(
        "UPDATE connector_resources SET state=?, last_seen=? "
        "WHERE connection_id=? AND resource_type=? AND external_id=?",
        (state.value, _now(), connection_id, resource_type, external_id))
    conn.commit()
    if detail:
        log.debug("%s/%s/%s → %s: %s", connection_id, resource_type,
                  external_id, state.value, detail)
    return get(connection_id, resource_type, external_id)


def sweep_missing(connection_id: str, resource_type: str,
                  present: set[str], *, complete: bool) -> list[Resource]:
    """Mark everything we hold that the source did not list this pass.

    **`complete` is required and is the entire safety of this function.** A
    sweep over a *truncated* listing marks every record the page limit cut off
    as deleted — the whole tail of a mailbox, tombstoned because a sync hit its
    budget. `pagination.Walk.complete` is the answer, and it is passed in
    rather than inferred so a caller has to have it.
    """
    if not complete:
        log.debug("%s/%s: not sweeping — the listing was incomplete",
                  connection_id, resource_type)
        return []

    rows = _db().execute(
        "SELECT * FROM connector_resources WHERE connection_id=? "
        "AND resource_type=? AND state IN ('active','updated')",
        (connection_id, resource_type)).fetchall()
    gone = [_from_row(r) for r in rows if _from_row(r).external_id not in present]
    for resource in gone:
        mark(connection_id, resource_type, resource.external_id,
             ResourceState.DELETED, detail="not in a complete listing")
    return gone


def for_connection(connection_id: str, *, resource_type: str | None = None,
                   state: ResourceState | None = None,
                   limit: int = 500) -> list[Resource]:
    """What this connection holds. Bounded — this is read by a screen."""
    where = ["connection_id=?"]
    params: list[Any] = [connection_id]
    if resource_type is not None:
        where.append("resource_type=?")
        params.append(resource_type)
    if state is not None:
        where.append("state=?")
        params.append(state.value)
    rows = _db().execute(
        f"SELECT * FROM connector_resources WHERE {' AND '.join(where)} "
        f"ORDER BY last_seen DESC LIMIT ?", (*params, max(1, limit))).fetchall()
    return [_from_row(r) for r in rows]


def counts(connection_id: str) -> dict[str, int]:
    """How much of what, per state. What the data-control screen shows when it
    answers *"what was imported, and is it still there"*."""
    rows = _db().execute(
        "SELECT state, COUNT(*) AS n FROM connector_resources "
        "WHERE connection_id=? GROUP BY state", (connection_id,)).fetchall()
    out = {state.value: 0 for state in ResourceState}
    for row in rows:
        out[str(row["state"])] = int(row["n"])
    out["total"] = sum(out[s.value] for s in ResourceState)
    return out


def memory_ids(connection_id: str) -> list[str]:
    """Every memory this connection put in the brain.

    What *delete the data this connector imported* needs, and the reason it can
    be offered honestly at all: without an identity table there is no list, and
    the only available implementation is "delete everything whose `source`
    string looks right".

    **Both columns are read**, because that is what makes the `memory_ids`
    migration additive: a row written before it exists has only `memory_id`, and
    a chunked document written after it has several. Missing either would mean a
    delete that quietly leaves part of the data behind — which is the one
    outcome this control must never have.
    """
    rows = _db().execute(
        "SELECT memory_id, memory_ids FROM connector_resources "
        "WHERE connection_id=?", (connection_id,)).fetchall()
    found: list[str] = []
    for row in rows:
        data = row_to_dict(row)
        try:
            parsed = json.loads(data.get("memory_ids") or "[]")
        except ValueError:
            parsed = []
        if isinstance(parsed, list):
            found.extend(str(m) for m in parsed if m)
        first = str(data.get("memory_id") or "")
        if first and first not in found:
            found.append(first)
    # Ordered and unique: a caller deletes these one by one, and asking the
    # brain twice for the same id is a wasted round trip per duplicate.
    return list(dict.fromkeys(found))
