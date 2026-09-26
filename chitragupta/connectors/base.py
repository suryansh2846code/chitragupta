"""The contract every source implements.

A connector is the one place in the app that reaches outside the machine, so
this interface carries four guarantees that are easy to get wrong once per
connector and impossible to get wrong once here:

* **A bad item is skipped, never fatal** (`each()`). Decision H2. Written by
  hand in some connectors and forgotten in others until the suite ran them.
* **A sync can be stopped** (`cancel`). The scheduler used to check only
  *between* connectors, so cancelling mid-Gmail still waited for 600 messages.
* **Long work reports progress** (`progress`), because "never make the user wait
  without telling them" applies to the first sync more than anywhere else.
* **A second pass is cheap** (`since()` / `SyncResult.cursor`). The background
  loop re-runs every ready connector on a timer, so the *second* sync is by far
  the most common one.

On that last point: the watermark is an optimisation, never a correctness
mechanism. Dedup is a content hash with a unique index, so re-fetching an item
costs a skip and nothing else — which is precisely what makes it safe to ask
for a deliberately generous overlap rather than trusting a remote clock.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..core.store import MemoryStore, get_store
from ..log import get_logger
from .capability import Capability
from .contract import (
    AuthMethod,
    ConnectorManifest,
    EventSource,
    Limits,
    PaginationStrategy,
    SyncStrategy,
    manifest_of,
)

log = get_logger(__name__)


class Cancellable(Protocol):
    """Anything that can say "stop" — `threading.Event` satisfies this."""

    def is_set(self) -> bool: ...


#: Called with (done, total, label). Total is 0 when it is not known upfront.
ProgressFn = Callable[[int, int, str], None]


@dataclass
class SyncResult:
    connector: str
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    detail: str = ""
    #: Watermark to resume from next time. Persisted by `_finish` only when the
    #: pass was not cancelled — a cursor saved from a half-finished sync would
    #: skip everything the cancelled half never fetched.
    cursor: str | None = None
    cancelled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "added": self.added,
            "skipped": self.skipped,
            "errors": self.errors,
            "detail": self.detail,
            "cancelled": self.cancelled,
        }


class Connector:
    """A source of memories. Subclasses implement `sync`."""

    name: str = "base"
    label: str = "Base"
    #: True when the connector can run with no extra credentials/config.
    always_available: bool = False

    #: Does this read something already on the machine, rather than an account?
    #:
    #: The difference a *person* can act on, and the one the Connectors screen
    #: groups by. On-device means no account is involved and nothing leaves the
    #: Mac — which is the whole product promise, and was previously visible
    #: nowhere. Everything else is a service the user signs in to, whether we
    #: wrote the client or the vendor ships the server.
    #:
    #: Declared on the class rather than mapped in the frontend, because the
    #: frontend had exactly such a map and four connectors were missing from it
    #: — so Slack, Telegram, Apple Health and Google Fit were filed under
    #: "Custom sources", which is a heading that was simply not true.
    runs_on_device: bool = False
    #: The MCP server id that does this better, if one exists.
    #:
    #: **A source the user can connect two ways is a source they will connect
    #: the wrong way.** Notion and Linear were offered here *and* as custom
    #: sources, and the Connectors screen showed both — so an agent proposed a
    #: write down the built-in path while the vendor's own server sat
    #: connected beside it, and the card failed with "Notion is not connected"
    #: in front of a green CONNECTED badge.
    #:
    #: Where the vendor ships a server, it wins: OAuth instead of a pasted
    #: secret, the vendor's own schema, and far more of it — forty-five Notion
    #: tools against the five that were hand-written here. So a connector that
    #: names one is not OFFERED. It is still shown to anybody who already had
    #: it configured, because hiding something somebody set up is how you lose
    #: their state to our change of mind.
    prefer_mcp: str = ""
    #: Connectors that authenticate with a single pasted token declare it here,
    #: so the UI renders an in-app field (no .env editing). Example:
    #:   secret_field = {"key": "NOTION_TOKEN", "label": "Integration secret",
    #:                   "placeholder": "ntn_…", "help_url": "https://…"}
    secret_field: dict | None = None
    #: OS platforms this connector can work on (sys.platform values, e.g.
    #: "darwin", "linux", "win32"). None = every platform. Lets the UI hide
    #: macOS-only connectors on Linux/Windows instead of showing them broken.
    platforms: tuple[str, ...] | None = None
    #: Does the background loop re-run this on a timer? A class flag rather than
    #: a list in the scheduler, because a list there is a decision no reader of
    #: the connector can see — and five of eleven were in it for no stated
    #: reason. False means "only when the user asks", which is the right answer
    #: for a source the user points at explicitly (a folder) or writes by hand.
    auto_sync: bool = False
    #: Does `sync()` honour `since`? Declared so the UI can say "checking for
    #: new items" rather than "syncing", and so a connector that has not been
    #: taught the watermark is visible as such instead of silently full-scanning.
    incremental: bool = False
    #: How far back to re-check on an incremental pass. Generous on purpose: an
    #: item that lands *during* a sync would otherwise sit just behind the new
    #: watermark and never be fetched again. Re-fetching is free (content-hash
    #: dedup), missing an email is not.
    overlap_minutes: int = 120

    #: Set by `is_configured()` when the refusal is something the user can clear
    #: themselves in one click — currently only `permissions.FULL_DISK_ACCESS`.
    #: `None` means the reason is informational and there is no button to offer.
    #:
    #: Valid **immediately after** `is_configured()` returns, which is how every
    #: caller uses it. It is an attribute rather than a third tuple member
    #: because that tuple is the contract eleven connectors already implement,
    #: and widening it would have meant touching all of them to express
    #: something three of them know.
    fix: str | None = None

    # ── the declaration ─────────────────────────────────────────────────────
    #
    # Everything below is read by `contract.manifest_of()` and by nothing else
    # directly. They are attributes on the class rather than a manifest written
    # beside it, because a description maintained in a second place is one that
    # will disagree with the connector — and the copy that drifts is always the
    # one being read. `docs/CONNECTOR-PLATFORM.md` §2.

    #: The vendor, where it differs from our id for it. `gcal` is Google.
    provider: str = ""
    #: Bumped when this connector's ingest shape changes incompatibly. Recorded
    #: on every checkpoint, so a bump invalidates watermarks written by the old
    #: shape instead of silently resuming into data that means something else.
    version: int = 1

    auth_method: AuthMethod = AuthMethod.NONE
    #: Scopes/permissions this connector cannot work without. Lets a health
    #: check say "signed in, but not allowed to write" rather than letting the
    #: first write fail at the vendor, in front of the user.
    required_scopes: tuple[str, ...] = ()

    #: **What this connector can do**, as verbs on resources. The security
    #: surface: `capability.parse()` refuses anything it does not recognise,
    #: and an action may not be registered at a weaker tier than its capability
    #: implies (`tests/connectors/test_capability_floor.py`).
    #:
    #: Empty means "has not been taught to describe itself", which
    #: `ConnectorManifest.declares_nothing` reports out loud — an empty set is
    #: otherwise indistinguishable from a read-only connector.
    capabilities: frozenset[Capability] = frozenset()

    sync_strategy: SyncStrategy = SyncStrategy.FULL
    #: Can a crashed pass continue from its last checkpoint rather than zero?
    #: False is the honest default: `_finish` stores a watermark only for a
    #: clean pass, which is safe and means a 2,000-item sync that fails at
    #: 1,900 starts again at 0. `engine.py` is what makes this True.
    resumable: bool = False
    pagination: PaginationStrategy = PaginationStrategy.NONE

    event_source: EventSource = EventSource.NONE
    #: Which field on a provider payload identifies an event, for dedup.
    event_id_field: str = ""
    #: Which field orders them. Empty means arrival order is all there is.
    event_ordering_field: str = ""

    #: How hard we will push this vendor. Ours, and `Limits.as_dict()` says so.
    limits: Limits = Limits()

    def connection(self, *, account: str = "") -> Any:
        """This connector's account row, with its auth state brought up to date.

        **The row mirrors the credential; it never holds one.** For every
        connector shipped today the credential lives somewhere else — the
        Keychain, `google_token.json`, a Telegram session — and
        `is_configured()` is the authoritative answer about whether it works.
        A second copy of that answer would be a second thing to keep current,
        and the copy that drifts is always the one being read.

        So this reconciles rather than decides, and it is careful in the two
        directions that matter:

        * **A user's pause is never undone.** `paused` is a separate column
          for exactly that reason: it is a decision about scheduling, not
          about the credential.
        * **`REVOKED` is never quietly re-authenticated.** The user (or an
          admin) took access away at the vendor; a local file still being
          present does not mean that was reversed.
        """
        from .connections import AuthState, ensure, transition

        found = ensure(self.name, provider=self.provider or self.name,
                       account=account, version=self.version)
        ready, reason = self.is_configured()

        if ready and found.auth_state not in (AuthState.AUTHENTICATED,
                                              AuthState.EXPIRED,
                                              AuthState.REVOKED):
            return transition(found.id, AuthState.AUTHENTICATED) or found
        if not ready and found.auth_state in (AuthState.AUTHENTICATED,
                                              AuthState.EXPIRED):
            # No credential at all is DISCONNECTED, not REAUTH_REQUIRED: the
            # second means *a sign-in expired and only you can renew it*, and
            # saying that about a connector nobody has set up yet sends the
            # user looking for a session that never existed.
            return transition(found.id, AuthState.DISCONNECTED,
                              detail=reason) or found
        return found

    def manifest(self) -> ConnectorManifest:
        """This connector, described. Derived — see the note above.

        An instance method rather than a classmethod because two connectors are
        one class per *configuration* rather than one class per source:
        `MCPConnector` and `CustomAPIConnector` are constructed from a spec the
        user wrote, and their id, label and limits are properties of that spec.
        `manifest_of()` reads attributes, so it works on either — and
        `tests/connectors/` calls it on the classes.
        """
        return manifest_of(self)

    def can(self, capability: Capability) -> bool:
        """May this connector do that at all?

        The floor under every gate, and it fails closed: a capability the
        connector never declared is refused here, before a risk tier or an
        allow-list is consulted.
        """
        return capability in self.capabilities

    @classmethod
    def supported_here(cls) -> bool:
        return cls.platforms is None or sys.platform in cls.platforms

    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or get_store()
        self.fix = None

    def is_configured(self) -> tuple[bool, str]:
        """Return (ready, human-readable reason-if-not)."""
        return True, ""

    def sync(
        self,
        *,
        since: str | None = None,
        limit: int | None = None,
        full_history: bool = False,
        cancel: Cancellable | None = None,
        progress: ProgressFn | None = None,
        interactive: bool = True,
        **kwargs: Any,
    ) -> SyncResult:  # pragma: no cover - interface
        """Ingest from this source.

        Every option is keyword-only and every connector accepts `**kwargs`,
        because the scheduler calls them all identically and passes options that
        not all of them understand.

        `since` is an ISO timestamp to resume from; `None` means "decide for
        yourself" (normally `self.since()`). `full_history` overrides it.
        """
        raise NotImplementedError

    # ── incremental sync ────────────────────────────────────────────────────

    def last_cursor(self) -> str | None:
        """The watermark the previous successful sync stored, if any."""
        state = self.store.get_connector_state(self.name) or {}
        return state.get("cursor") or None

    def since(self, *, full_history: bool = False) -> str | None:
        """Where an incremental pass should resume from, with overlap applied.

        Returns None when there is nothing to resume from — a first sync, a
        connector that does not keep a watermark, or an explicit full history —
        and callers must read that as "fetch the normal window", never as
        "fetch nothing".
        """
        if full_history or not self.incremental:
            return None
        raw = self.last_cursor()
        if not raw:
            return None
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            # A cursor we cannot read is a cursor we ignore. A connector whose
            # watermark format changed must re-scan, not silently sync nothing.
            log.debug("%s: unreadable cursor %r, falling back to a full window",
                      self.name, raw)
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return (stamp - timedelta(minutes=self.overlap_minutes)).isoformat()

    @staticmethod
    def now() -> str:
        """The watermark to store for this pass. Taken at the *start* of a sync,
        so anything that arrives while it runs is caught by the next one."""
        return datetime.now(UTC).isoformat()

    # ── the item loop ───────────────────────────────────────────────────────

    def each(
        self,
        items: Iterable[Any],
        result: SyncResult,
        *,
        cancel: Cancellable | None = None,
        progress: ProgressFn | None = None,
        total: int | None = None,
        label: str = "",
    ) -> Iterator[Any]:
        """Walk source records with cancellation, progress and crash isolation.

        The body a caller writes inside this loop may raise freely: the failure
        is counted as a skip and the walk continues. That is decision H2, and
        putting it here is what stops the next connector forgetting it.

        Cancellation is checked *per item*, not per connector, so stopping a
        first sync of a large mailbox takes one message rather than all of them.
        """
        seq = list(items)
        count = total if total is not None else len(seq)
        for index, item in enumerate(seq):
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                result.detail = result.detail or f"cancelled after {index} items"
                return
            try:
                yield item
            except Exception as exc:            # pragma: no cover - see note
                # Generators cannot actually catch an exception raised in the
                # caller's loop body; `each_guarded` is the form that can. This
                # arm exists for a `throw()` and is kept so the contract reads
                # the same from both directions.
                result.skipped += 1
                log.debug("%s: skipped an item: %s", self.name, exc)
            if progress is not None:
                progress(index + 1, count, label or self.label)

    def each_guarded(
        self,
        items: Iterable[Any],
        result: SyncResult,
        handler: Callable[[Any], int],
        *,
        cancel: Cancellable | None = None,
        progress: ProgressFn | None = None,
        label: str = "",
    ) -> None:
        """`each`, with the body passed in so its failures really are caught.

        `handler` returns how many memories it stored; returning 0 counts as a
        skip, which is how "already had it" and "nothing worth keeping" are
        reported without either being an error.
        """
        seq = list(items)
        for index, item in enumerate(seq):
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                result.detail = f"cancelled after {index} of {len(seq)}"
                return
            try:
                stored = handler(item)
            except Exception as exc:
                result.skipped += 1             # one bad item never aborts (H2)
                log.debug("%s: skipped an item: %s", self.name, exc)
            else:
                if stored:
                    result.added += stored
                else:
                    result.skipped += 1
            if progress is not None:
                progress(index + 1, len(seq), label or self.label)

    # ── finishing ───────────────────────────────────────────────────────────

    def _finish(self, result: SyncResult) -> SyncResult:
        state: dict[str, Any] = {
            "status": "ok" if not result.errors else "error",
            "detail": result.detail or f"+{result.added} added, {result.skipped} skipped",
            "last_sync": datetime.now(UTC).isoformat(),
        }
        # A cursor is only as good as the pass that produced it. Saving one from
        # a cancelled or failed sync would move the watermark past records that
        # were never fetched, and nothing would ever go back for them.
        if result.cursor and not result.cancelled and not result.errors:
            state["cursor"] = result.cursor
        self.store.set_connector_state(self.name, **state)
        return result
