"""Where one piece of external information came from, carried into the brain.

Six connectors passed a `metadata` dict before this and each invented its own
keys — `message_id`, `file_id`, `page_id`, `event_id`, `mcp_tool` — while
`source_id`, `uri` and `event_time` sat in the schema (`core/db.py`) unused by
every one of them. So the brain knew a memory came from `"gmail"` and could not
answer which account, which message, when it was fetched, when it last changed
at the source, or which sync produced it.

A `SourceRef` is those answers, in one shape, converted to `brain.ingest`
keywords by one function — so a connector cannot half-populate it and nothing
downstream has to know fifteen spellings.

    SourceRef(
        connector="gmail", provider="google",
        connection_id="gmail:a1b2", account="me@work.test",
        resource_type="email", external_id="18f2c…",
        url="https://mail.google.com/…",
        source_updated_at="2026-09-20T09:14:00Z",
        run_id="7c1f9e2a",
    )

## Why this is worth the seven fields

* **Attribution** — *"where did you get that"* is answerable at all.
* **Conflict resolution** — two sources disagreeing can be ranked by which is
  fresher at its own source, not by which we read last.
* **Freshness** — `retrieved_at` and `source_updated_at` are different facts
  and both matter: we read it an hour ago, it changed three weeks ago.
* **Deletion** — `external_id` is what a tombstone is keyed to.
* **Debugging** — `run_id` turns *"which sync put this here"* into a query.
* **Injection defence** — `trust` travels with the text, so the reader of a
  memory knows a stranger wrote it.

## Trust is named here and defined elsewhere

`trust` is a string on purpose. The trust *ladder* — what the levels are, which
of them get fenced before a model sees them, and how a fence is made
uncloseable from the inside — belongs to one module for the whole app, next to
`browser/page.py`'s quarantine fence which established the rule. This carries
the label; it does not define the vocabulary, and it must not grow a second
copy of one. See `docs/CONNECTOR-PLATFORM.md` §5.

What is safe to say here, because it is a fact about connectors rather than a
policy about models: **a connected source describes itself trustworthily and
its free text does not.** A calendar event's start time is structure the vendor
produced; the body of the invitation is something a person typed, and that
person is not always the user.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: The default label for anything a connector ingests: the user connected this
#: source, so its *shape* is trustworthy and the free text inside it is not.
CONNECTED_SOURCE = "connected_source"

#: For text a stranger wrote that arrived through a connected source — an email
#: body, an issue comment, a chat message. The connector says so; it does not
#: decide what is done about it.
UNTRUSTED_CONTENT = "untrusted_content"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SourceRef:
    """Where one record came from. Complete or not at all.

    Frozen, because provenance that can be edited after the fact is provenance
    nobody can rely on.
    """

    connector: str
    #: The account this came from. **Not** the connector: two Gmail accounts
    #: produce identical-looking memories and only this tells them apart.
    connection_id: str = ""
    provider: str = ""
    #: What the vendor calls the account. Display only — `connection_id` is the
    #: key, because an address can be renamed at the vendor.
    account: str = ""

    resource_type: str = "record"
    #: The vendor's own id. The one field a tombstone and a supersede are
    #: keyed to, and the one that must not be the title, the text or the URL.
    external_id: str = ""
    #: Where a person can go and look at it. Empty when there is nowhere —
    #: better than a link that 404s.
    url: str = ""

    #: When *we* fetched it.
    retrieved_at: str = field(default_factory=_now)
    #: When it last changed at the source. A different fact from the one above,
    #: and the one that decides which of two disagreeing sources is fresher.
    source_updated_at: str = ""

    #: The sync that produced it. Turns "which pass put this here" into a query.
    run_id: str = ""
    trust: str = CONNECTED_SOURCE

    #: Anything the connector knows that this shape does not name — a label, a
    #: repository, the MCP tool a record came from. Kept rather than dropped:
    #: *"normalized fields plus provider-specific metadata"*, never one at the
    #: cost of the other.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        """The stable identity, as one string.

        `connection` and not `connector`, because `1a2b3c` in the work Drive
        and `1a2b3c` in the personal one are different files.
        """
        return (f"{self.connection_id or self.connector}"
                f"/{self.resource_type}/{self.external_id}")

    @property
    def complete(self) -> bool:
        """Is there enough here to supersede or tombstone what it describes?

        An `external_id` is the whole answer: without one there is no identity,
        and a record with no identity can only ever be appended to the brain.
        """
        return bool(self.external_id and self.connection_id)

    def as_metadata(self) -> dict[str, Any]:
        """The provenance as it is stored on the memory.

        Namespaced under `source` rather than flattened, so a connector's own
        `extra` keys can never collide with a provenance field and quietly
        overwrite where something came from.
        """
        payload: dict[str, Any] = {
            "connector": self.connector,
            "connection": self.connection_id,
            "provider": self.provider or self.connector,
            "account": self.account,
            "resource_type": self.resource_type,
            "external_id": self.external_id,
            "url": self.url,
            "retrieved_at": self.retrieved_at,
            "source_updated_at": self.source_updated_at,
            "run_id": self.run_id,
            "trust": self.trust,
        }
        return {"source": {k: v for k, v in payload.items() if v},
                **dict(self.extra)}

    def ingest_kwargs(self) -> dict[str, Any]:
        """Everything `brain.ingest()` should be told, from one place.

        The fields already existed and no connector passed them. Building them
        here rather than at fifteen call sites is what stops the next connector
        populating four of the seven.

        `event_date` is deliberately **not** set from `retrieved_at`: the date
        of a memory is when the thing happened, not when we read it, and
        filling it with a fetch time makes every record from a first sync look
        like it happened today.
        """
        out: dict[str, Any] = {
            "source": self.connector,
            "metadata": self.as_metadata(),
        }
        if self.external_id:
            # The schema field that exists for exactly this and was never used.
            out["source_id"] = self.identity
        if self.url:
            out["uri"] = self.url
        if self.source_updated_at:
            out["event_time"] = self.source_updated_at
            out["event_date"] = self.source_updated_at[:10]
        return out


def of(memory_metadata: Any) -> dict[str, Any]:
    """The provenance back out of a stored memory, or {}.

    Tolerant on purpose: memories written before this existed have no `source`
    block, and a reader that raised on them would break recall for everything
    ingested up to now.
    """
    if not isinstance(memory_metadata, dict):
        return {}
    found = memory_metadata.get("source")
    return dict(found) if isinstance(found, dict) else {}


def describe(memory_metadata: Any) -> str:
    """One line a person can read: *"Gmail · me@work.test · 20 Sep"*.

    Empty when there is nothing worth saying, so a caller can put it on a row
    without first checking — an attribution line reading "unknown · unknown" is
    worse than no attribution line.
    """
    source = of(memory_metadata)
    if not source:
        return ""
    parts = [str(source.get("connector") or "")]
    if source.get("account"):
        parts.append(str(source["account"]))
    when = str(source.get("source_updated_at") or source.get("retrieved_at") or "")
    if when:
        parts.append(when[:10])
    return " · ".join(p for p in parts if p)
