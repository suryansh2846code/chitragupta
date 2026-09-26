"""One machine-readable description of a connector, read off the connector.

    Connector
    ├── metadata      id · name · provider · version
    ├── auth          how it signs in
    ├── capabilities  what it can do            ← the security surface
    ├── resources     what kinds of thing it holds
    ├── sync          incremental? resumable? what the cursor is
    ├── events        webhook · polling · none
    ├── health        can it be checked cheaply?
    └── limits        how hard we will push the vendor

**Derived, never written beside the class.** The failure this avoids has
happened twice in this repo already: `agents/permissions.py` kept three
hand-maintained sets of action names and the one that got forgotten was
whichever was furthest from the code being written; the frontend kept its own
map of which connectors were on-device and four were missing from it. A
manifest maintained in a second place is a manifest that will disagree with the
connector, and the copy that drifts is always the one being read.

So every field here is an attribute on the class, and `manifest_of()` reads
them. A connector that declares nothing gets a manifest that says it declares
nothing — which a contract test can then name out loud, rather than a manifest
that quietly claims it does nothing.

## Not a god interface

The base class carries what **every** connector genuinely has: an identity, a
readiness answer, and a `sync()`. Everything optional is a small protocol a
connector either satisfies or does not — `SupportsWrites`, `SupportsMessaging`,
`SupportsEvents`, `SupportsHealthCheck`. `messaging.py` already worked this way
by duck typing and was right to; these give the same arrangement a name, so the
answer to "can this connector send a message" is a check rather than a
`hasattr` written out at each call site.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from .capability import Access, Capability, Resource, parse_all, strongest


class AuthMethod(StrEnum):
    """How a connection proves who it is.

    `LOCAL` is its own value rather than `NONE` because they are different
    facts about the world: Apple Mail needs no credential *and* needs Full Disk
    Access, and a UI that renders both as "nothing to do" is wrong about one of
    them.
    """

    NONE = "none"
    #: Reads something already on this Mac. No account, nothing leaves.
    LOCAL = "local"
    OAUTH2 = "oauth2"
    #: A key the user pastes. `Connector.secret_field` renders the input.
    API_KEY = "api_key"
    #: A session the vendor's own client established (Telegram's MTProto).
    SESSION = "session"


class SyncStrategy(StrEnum):
    """How a second pass avoids being the first pass again."""

    #: No watermark: the whole window is re-read and dedup absorbs it.
    FULL = "full"
    #: An ISO timestamp the connector filters on.
    TIMESTAMP = "timestamp"
    #: A token the provider hands back (Gmail history id, Drive page token).
    CURSOR = "cursor"
    #: The provider tells us what changed (delta / changes feed).
    DELTA = "delta"
    #: Nothing to poll — the user writes it, or points at it.
    MANUAL = "manual"


class EventSource(StrEnum):
    """Where a change becomes known.

    `WEBHOOK` is representable and unused: a local-first Mac app has no public
    URL, and giving it one means a tunnel or a relay — the hosted-broker trade
    `docs/CONNECTORS.md` refuses. It is spelled out here so that the day a
    provider offers a pull-based change feed, the shape is already right and
    nothing has to be renamed. See `docs/CONNECTOR-PLATFORM.md` §7.
    """

    NONE = "none"
    POLLING = "polling"
    WEBHOOK = "webhook"


class PaginationStrategy(StrEnum):
    NONE = "none"
    CURSOR = "cursor"
    PAGE_NUMBER = "page_number"
    NEXT_TOKEN = "next_token"
    LINK_HEADER = "link_header"


@dataclass(frozen=True)
class Limits:
    """How hard this app will push one vendor, and how much it will hold.

    **Ours, and labelled ours.** MCP publishes no rate limits and most REST
    vendors publish theirs only in documentation, so these are ceilings this
    app applies rather than promises anybody made. `mcp_manifest.py` learned
    the same lesson and says `imposed_by` for the same reason: a limit we chose
    that is read as the vendor's is a manifest that has started lying.
    """

    #: Requests this connector may start per `per_seconds`. 0 = unmetered.
    requests: int = 0
    per_seconds: float = 60.0
    #: Requests in flight at once. Bounds one connector's share of the app.
    concurrency: int = 2
    #: Records asked for in one page.
    page_size: int = 100
    #: Records one sync pass will ingest, across every page.
    records_per_sync: int = 1000
    #: Seconds one request may take before it is a timeout.
    seconds_per_request: float = 30.0

    def as_dict(self) -> dict[str, Any]:
        return {"requests": self.requests, "per_seconds": self.per_seconds,
                "concurrency": self.concurrency, "page_size": self.page_size,
                "records_per_sync": self.records_per_sync,
                "seconds_per_request": self.seconds_per_request,
                "imposed_by": "Chitragupta"}


@dataclass(frozen=True)
class SyncSupport:
    strategy: SyncStrategy = SyncStrategy.FULL
    #: Does `sync()` honour `since`?
    incremental: bool = False
    #: Can a crashed pass continue from its last checkpoint rather than zero?
    resumable: bool = False
    #: Does the background loop re-run it on a timer?
    scheduled: bool = False
    #: Minutes of deliberate re-read on an incremental pass.
    overlap_minutes: int = 0
    pagination: PaginationStrategy = PaginationStrategy.NONE

    def as_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy.value, "incremental": self.incremental,
                "resumable": self.resumable, "scheduled": self.scheduled,
                "overlap_minutes": self.overlap_minutes,
                "pagination": self.pagination.value}


@dataclass(frozen=True)
class EventSupport:
    source: EventSource = EventSource.NONE
    #: Which field on a provider payload identifies the event, for dedup.
    #: Empty means we mint one from the resource identity — see `events.py`.
    event_id_field: str = ""
    #: Which field orders them. Empty means arrival order is all we have, and
    #: `events.py` says so rather than pretending to a sequence.
    ordering_field: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source.value,
                "event_id_field": self.event_id_field,
                "ordering_field": self.ordering_field}


@dataclass(frozen=True)
class ConnectorManifest:
    """Everything the platform needs to know about one connector."""

    connector_id: str
    display_name: str
    provider: str
    version: int = 1

    auth: AuthMethod = AuthMethod.NONE
    #: OAuth scopes / token permissions this connector needs to work at all.
    #: Named so a health check can say *"signed in, but missing the scope that
    #: lets it write"* instead of letting the first write fail at the vendor.
    required_scopes: tuple[str, ...] = ()

    capabilities: frozenset[Capability] = frozenset()
    sync: SyncSupport = field(default_factory=SyncSupport)
    events: EventSupport = field(default_factory=EventSupport)
    limits: Limits = field(default_factory=Limits)

    health_check: bool = False
    #: Does the data stay on this Mac?
    on_device: bool = False
    platforms: tuple[str, ...] = ()
    #: An MCP server id that supersedes this connector, if one does.
    superseded_by: str = ""

    # ── derived ──────────────────────────────────────────────────────────

    @property
    def resources(self) -> frozenset[Resource]:
        """What this connector touches, from what it can do. Derived so a
        connector cannot claim a resource it has no capability on."""
        return frozenset(c.resource for c in self.capabilities)

    @property
    def reads(self) -> frozenset[Capability]:
        return frozenset(c for c in self.capabilities if c.reads_only)

    @property
    def writes(self) -> frozenset[Capability]:
        return frozenset(c for c in self.capabilities if not c.reads_only)

    @property
    def access(self) -> Access:
        """The strongest thing this connector can do."""
        return strongest(self.capabilities)

    @property
    def declares_nothing(self) -> bool:
        """A connector that has not been taught to describe itself.

        Visible rather than silent: an empty capability set is indistinguishable
        from a read-only connector unless something asks this question, and a
        contract test does.
        """
        return not self.capabilities

    def permits(self, capability: Capability) -> bool:
        """May this connector do that at all? The floor under every gate.

        Fails closed by construction: a capability the connector never declared
        is refused here, before any risk tier or allow-list is consulted.
        """
        return capability in self.capabilities

    def as_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "display_name": self.display_name,
            "provider": self.provider,
            "version": self.version,
            "auth": self.auth.value,
            "required_scopes": list(self.required_scopes),
            "capabilities": sorted(str(c) for c in self.capabilities),
            "reads": sorted(str(c) for c in self.reads),
            "writes": sorted(str(c) for c in self.writes),
            "resources": sorted(r.value for r in self.resources),
            "access": self.access.value,
            "sync": self.sync.as_dict(),
            "events": self.events.as_dict(),
            "limits": self.limits.as_dict(),
            "health_check": self.health_check,
            "on_device": self.on_device,
            "platforms": list(self.platforms),
            "superseded_by": self.superseded_by,
            "declares_nothing": self.declares_nothing,
        }


# ── the optional protocols ───────────────────────────────────────────────
#
# Each is one question with one answer, rather than a method on a base class
# that fifteen connectors have to inherit and thirteen have to not implement.


@runtime_checkable
class SupportsWrites(Protocol):
    """A connector that can change something at the vendor.

    `perform` is the single entry point, and `confirmed` is required rather
    than defaulted so a caller that forgets it fails closed — the rule
    `MCPConnector.perform` already follows, given a name so every connector
    that grows a write follows it too.
    """

    def perform(self, operation: str, arguments: dict[str, Any] | None = ...,
                *, confirmed: bool = ...) -> dict[str, Any]: ...


@runtime_checkable
class SupportsMessaging(Protocol):
    """Carries conversations. `../messaging.py` finds these by duck typing;
    this is the same shape with a name on it.

    `history` returns **oldest first** — every chat API returns the opposite,
    and a conversation read backwards is answered backwards.
    """

    def chats(self, limit: int = ...) -> list[Any]: ...
    def history(self, chat_id: str, limit: int = ...) -> list[Any]: ...
    def send(self, chat_id: str, text: str) -> dict[str, Any]: ...


@runtime_checkable
class SupportsEvents(Protocol):
    """Can say what changed since a point, rather than only what exists."""

    def changes_since(self, cursor: str | None) -> Iterable[Any]: ...


@runtime_checkable
class SupportsHealthCheck(Protocol):
    """Can answer *is this working* without running a sync.

    The distinction that makes it worth a protocol: `is_configured()` on an MCP
    connector starts a subprocess, and the Connectors page calls it for every
    row on load. A health check is the cheap question.
    """

    def check_health(self) -> Any: ...


def manifest_of(cls: Any) -> ConnectorManifest:
    """Read a connector class's own declaration.

    Takes `Any` rather than `type[Connector]` deliberately: `base.py` imports
    *this* module for its defaults, so importing it back would be a cycle —
    and a lazy import inside the function would be the same cycle, hidden
    (`/CLAUDE.md`, Everywhere).
    """
    declared: Any = getattr(cls, "capabilities", frozenset())
    capabilities = (declared if isinstance(declared, frozenset)
                    else parse_all(declared))

    name = getattr(cls, "name", "") or ""
    platforms = getattr(cls, "platforms", None) or ()

    return ConnectorManifest(
        connector_id=name,
        display_name=getattr(cls, "label", "") or name,
        provider=getattr(cls, "provider", "") or name,
        version=int(getattr(cls, "version", 1)),
        auth=AuthMethod(getattr(cls, "auth_method", AuthMethod.NONE)),
        required_scopes=tuple(getattr(cls, "required_scopes", ()) or ()),
        capabilities=capabilities,
        sync=SyncSupport(
            strategy=SyncStrategy(getattr(cls, "sync_strategy",
                                          SyncStrategy.FULL)),
            incremental=bool(getattr(cls, "incremental", False)),
            resumable=bool(getattr(cls, "resumable", False)),
            scheduled=bool(getattr(cls, "auto_sync", False)),
            # Only meaningful when a watermark is kept. Reporting 120 minutes
            # of overlap on a connector that re-reads everything anyway is a
            # number that describes nothing.
            overlap_minutes=(int(getattr(cls, "overlap_minutes", 0))
                             if getattr(cls, "incremental", False) else 0),
            pagination=PaginationStrategy(getattr(cls, "pagination",
                                                  PaginationStrategy.NONE)),
        ),
        events=EventSupport(
            source=EventSource(getattr(cls, "event_source", EventSource.NONE)),
            event_id_field=getattr(cls, "event_id_field", "") or "",
            ordering_field=getattr(cls, "event_ordering_field", "") or "",
        ),
        limits=getattr(cls, "limits", None) or Limits(),
        health_check=isinstance(cls, SupportsHealthCheck)
                     or hasattr(cls, "check_health"),
        on_device=bool(getattr(cls, "runs_on_device", False)),
        platforms=tuple(platforms),
        superseded_by=getattr(cls, "prefer_mcp", "") or "",
    )
