"""Turning what a connector produced into events, without the engine knowing how.

A connector's `sync()` answers "how many rows did I add", which is a count, not
an event — no identity, so no deduplication, and no payload, so the old engine
needed a branch per source. This module is the adapter between the two, and it
is the **only** place that knows a connector-shaped thing exists.

It sits deliberately outside `router`/`triggers`/`conditions`/`executor`: those
five modules must stay free of any connector name, which
`test_no_connector_is_named_in_the_automation_core` enforces. Here the mapping
is data — `EVENT_KINDS` — so a new source is a dictionary entry and a
`kind` string, not a code path.

Rows are read back out of the brain rather than intercepted mid-sync. That is
worth the round trip: the brain is where a synced item *lands*, it already has
a stable id and a timestamp, and reading from it means an event carries what
was actually stored rather than what the connector thought it was storing.
"""
from __future__ import annotations

from typing import Any

from ..core.events import Event
from ..core.provenance import Trust
from ..log import get_logger, suppressed

log = get_logger(__name__)

#: `connector -> (event kind, trust)`.
#:
#: Trust is per source and is a judgement about **who wrote the free text**. An
#: email body is written by a stranger; a calendar event the user created is
#: not; a file on their disk was put there by them. Getting this wrong in the
#: trusting direction is how an injection reaches a model unfenced, so an
#: unlisted source defaults to `UNTRUSTED_CONTENT`.
EVENT_KINDS: dict[str, tuple[str, Trust]] = {
    "gmail": ("email.received", Trust.UNTRUSTED_CONTENT),
    "apple_mail": ("email.received", Trust.UNTRUSTED_CONTENT),
    "imessage": ("message.received", Trust.UNTRUSTED_CONTENT),
    "telegram": ("message.received", Trust.UNTRUSTED_CONTENT),
    "slack": ("message.received", Trust.UNTRUSTED_CONTENT),
    "github": ("repository.changed", Trust.UNTRUSTED_CONTENT),
    "linear": ("issue.changed", Trust.UNTRUSTED_CONTENT),
    "notion": ("document.changed", Trust.CONNECTED_SOURCE),
    "gdrive": ("document.changed", Trust.CONNECTED_SOURCE),
    "files": ("file.changed", Trust.CONNECTED_SOURCE),
    "notes": ("note.changed", Trust.CONNECTED_SOURCE),
    "gcal": ("calendar.changed", Trust.CONNECTED_SOURCE),
    "apple_calendar": ("calendar.changed", Trust.CONNECTED_SOURCE),
    "google_fit": ("health.recorded", Trust.CONNECTED_SOURCE),
    "apple_health": ("health.recorded", Trust.CONNECTED_SOURCE),
}

#: Most a single sync may produce. A first-run Gmail import adds hundreds of
#: rows and must not start hundreds of runs; the automation for "when an email
#: arrives" means the ones arriving now.
MAX_EVENTS_PER_SYNC = 25


def kind_for(connector: str) -> tuple[str, Trust]:
    return EVENT_KINDS.get(connector, (f"{connector}.changed",
                                       Trust.UNTRUSTED_CONTENT))


def events_from_sync(connector: str, added: int, *,
                     rows: list[dict[str, Any]] | None = None) -> list[Event]:
    """The events one connector's sync produced.

    `rows` is injected so this is testable without a brain, and so the caller —
    which already knows whether the sync added anything — decides whether to
    pay for the lookup at all.
    """
    if added <= 0:
        return []
    kind, trust = kind_for(connector)
    out: list[Event] = []
    for row in (rows or [])[:MAX_EVENTS_PER_SYNC]:
        identifier = str(row.get("uri") or row.get("source_id") or row.get("id") or "")
        if not identifier:
            # Without an id the event ledger falls back to a payload hash,
            # which is weaker. Worth a debug line: it is the signal that a
            # connector should be supplying one.
            log.debug("%s produced a row with no stable id", connector)
        out.append(Event(
            kind=kind,
            source=connector,
            external_id=identifier,
            subject=str(row.get("title") or "")[:200],
            trust=trust,
            occurred_at=str(row.get("event_date") or row.get("created_at") or ""),
            data={
                "title": str(row.get("title") or "")[:200],
                "body": str(row.get("text") or "")[:4000],
                "uri": str(row.get("uri") or ""),
                "source": connector,
                **_extras(row),
            },
        ))
    return out


#: Metadata keys lifted onto `event.data`, and therefore the field names a
#: condition can address. Published through `/api/automations/vocabulary` so the
#: builder offers `event.from` because the event carries it, not because
#: somebody typed it into a form.
EXTRA_FIELDS = ("from", "sender", "to", "repository", "repo", "author", "path",
                "channel", "chat_id", "app", "labels", "state", "url")

#: Always on an event, whatever the source produced it.
BASE_FIELDS = ("title", "body", "uri", "source")

#: What each field is called on screen.
#:
#: Here rather than in the frontend because the *name* and the *path* have to
#: stay attached: `event.from` is what the engine reads and "Who it's from" is
#: what a person picks, and a form that kept its own half of that pairing would
#: drift into offering a field no event carries.
#:
#: A field with no entry falls back to its path. That is deliberately not an
#: error — a new key lifted onto an event should appear in the builder straight
#: away, reading a bit technical, rather than not appear at all.
FIELD_NAMES: dict[str, str] = {
    "event.title": "Subject or title",
    "event.body": "The text of it",
    "event.uri": "Link to it",
    "event.source": "Which app it came from",
    "event.from": "Who it is from",
    "event.sender": "Who sent it",
    "event.to": "Who it was sent to",
    "event.repository": "Repository",
    "event.repo": "Repository",
    "event.author": "Who wrote it",
    "event.path": "File path",
    "event.channel": "Channel",
    "event.chat_id": "Chat",
    "event.app": "App",
    "event.labels": "Labels on it",
    "event.state": "Its status",
    "event.url": "Link to it",
    "event_kind": "What kind of thing happened",
    "event_source": "Which app it came from",
}


def condition_fields() -> list[dict[str, str]]:
    """The things a condition can ask about — the path, and its name on screen.

    The facts a condition sees are assembled in `executor._check_conditions`;
    these are the leaves of it that mean something to a person. Not exhaustive
    by design — a condition may address any path — but a user should never have
    to guess, or type, the common ones.

    Duplicates by name are dropped: `event.repo` and `event.repository` are the
    same question asked of two connectors, and offering "Repository" twice is a
    menu that makes the user wonder what the difference is.
    """
    paths = [f"event.{name}" for name in (*BASE_FIELDS, *EXTRA_FIELDS)] + [
        "event_kind", "event_source"]
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in paths:
        label = FIELD_NAMES.get(path, path)
        if label in seen:
            continue
        seen.add(label)
        out.append({"path": path, "label": label})
    return out


def _extras(row: dict[str, Any]) -> dict[str, Any]:
    """Fields a condition is likely to want, pulled out of the row's metadata.

    Flat keys, because a condition addresses `event.from` and not
    `event.metadata.headers.from`. Everything here is still **data** — the
    values were written by whoever sent the thing.
    """
    meta = row.get("metadata")
    if not isinstance(meta, dict):
        return {}
    return {k: meta[k] for k in EXTRA_FIELDS
            if k in meta and meta[k] not in (None, "")}


def recent_rows(connector: str, since: str, limit: int = MAX_EVENTS_PER_SYNC
                ) -> list[dict[str, Any]]:
    """What this connector stored since `since`, newest first.

    The one function here that touches the brain, kept separate so
    `events_from_sync` stays pure and testable.
    """
    rows: list[dict[str, Any]] = []
    with suppressed("reading newly synced rows for automation events"):
        from ..brain import get_brain
        store = get_brain().store
        query = ("SELECT id, title, text, uri, source_id, created_at, event_date, "
                 "metadata FROM memories WHERE source=? ")
        args: list[Any] = [connector]
        if since:
            query += "AND created_at > ? "
            args.append(since)
        query += "ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        import json as _json
        for row in store._conn.execute(query, args).fetchall():
            item = dict(row)
            raw = item.get("metadata")
            if isinstance(raw, str) and raw:
                try:
                    item["metadata"] = _json.loads(raw)
                except ValueError:
                    item["metadata"] = {}
            rows.append(item)
    return rows
