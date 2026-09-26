"""One sync lifecycle, so fifteen connectors do not each invent their own.

    DISCOVER → FETCH → NORMALIZE → DEDUPLICATE → INGEST → CHECKPOINT → COMPLETE

Every stage above already existed somewhere, written by hand, differently each
time: `gdrive` builds a `(file_id, modifiedTime)` set out of the memories table
to deduplicate; `gmail` threads a `pageToken`; `notion` threads a
`start_cursor`; `mcp_source` budgets records across tools; `custom_api` does
none of it. The behaviour that was *consistent* was consistent by luck.

## What the engine actually buys

**Checkpoint after ingest, per page.** `base._finish` writes a watermark only
for a clean pass, which is safe and means a 2,000-item sync failing at item
1,900 restarts at zero. Here the unit is a page: it is fetched, ingested,
committed, and only then is the cursor moved. A crash costs one page.

The ordering is not a detail. Checkpoint-then-ingest loses a page *and reports
success for it* — the records are gone and nothing will ever go back.

**Identity before content.** `resources.classify` answers new/changed/unchanged
from the listing, so an unchanged file is skipped without downloading it. That
is the N+1 the audit found in `gdrive`, removed for every connector at once.

**Backpressure.** `each_guarded` does `seq = list(items)` — the whole page in
memory before anything is processed. The engine takes an iterator of pages and
never holds more than one, and it stops when the brain's queue is backed up
rather than racing ahead of it.

## Opt-in, per connector

A connector that has not moved onto this still syncs exactly as it did. This is
additive — `docs/CONNECTOR-PLATFORM.md` §4 — and the migration is one connector
at a time, each with its own tests, never a rewrite of fifteen at once.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..log import get_logger, suppressed
from . import connections, events, observability, resources, sync_state
from .base import Cancellable, ProgressFn, SyncResult
from .contract import ConnectorManifest
from .errors import ConnectorError
from .limits import gate_for
from .pagination import Page, Walk, walk
from .provenance import SourceRef
from .resources import ResourceState, Verdict
from .retry import DEFAULT, CancelledError, RetryPolicy, with_retries

log = get_logger(__name__)


@dataclass(frozen=True)
class Record:
    """One thing fetched from a source, on its way into the brain.

    **Normalized fields plus whatever the provider said**, never one at the
    cost of the other. `text` and `title` are what the brain indexes; `raw` is
    the provider's own object, kept so a later connector version can extract
    something this one did not think to.
    """

    external_id: str
    #: Empty is legitimate **before** `hydrate` and never after it. A listing
    #: that carries ids and no bodies produces records in exactly this state,
    #: which is what lets the engine decide to skip one without paying for it.
    text: str = ""
    title: str = ""
    url: str = ""
    #: When it last changed at the source. Drives both freshness and the
    #: unchanged-skip, so a connector that can supply it should.
    source_updated_at: str = ""
    #: A digest of whatever counts as a change for this provider. Only the
    #: connector knows — Drive's `modifiedTime` is authoritative, a Slack
    #: message's `edited.ts` is, and there is no general rule.
    fingerprint: str = ""
    #: Provider-specific metadata, preserved rather than flattened away.
    extra: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    @property
    def identified(self) -> bool:
        """Enough to decide whether we already have this, and nothing more.

        **The half that a listing can answer.** Gmail's `messages.list` returns
        ids and nothing else; the body is a second request per message. So the
        keep-or-skip decision has to be answerable from this much alone, or the
        connector pays for every body just to discover it already had it — which
        is exactly what `gdrive` does today and what `gmail` did before the
        engine. See `Plan.hydrate`.
        """
        return bool(self.external_id)

    @property
    def usable(self) -> bool:
        """An identity *and* something to say. What must be true before ingest.

        One with no `external_id` can only ever be appended — never superseded,
        never tombstoned — and one with no text is nothing to remember.
        """
        return bool(self.external_id and self.text.strip())


@dataclass
class Plan:
    """What one connector wants the engine to do for one resource type."""

    connector: str
    manifest: ConnectorManifest
    resource_type: str
    #: `fetch(cursor) -> Page[Record]`. The one thing a connector must write.
    #:
    #: A listing may return records that are only **identified** — an id and
    #: little else. That is the normal shape for the providers where it matters
    #: most, and `hydrate` is how the rest arrives.
    fetch: Callable[[str], Page]
    #: Where to ingest. Injected so a test does not need a brain, and so the
    #: engine does not import `brain` at module scope — the one sanctioned
    #: connector → brain edge stays exactly where it already was.
    ingest: Callable[[Record, SourceRef], str]
    #: `hydrate(record) -> Record`, for a provider whose listing does not carry
    #: the content. Called **only** for records the engine has decided to keep,
    #: which is the entire point of it existing.
    #:
    #: Gmail is the case that forced it: `messages.list` answers with ids, and
    #: the body is one `messages.get` per message. Fetching every body and
    #: letting a content hash sort it out means 600 requests per pass to keep
    #: nothing — and `gdrive` does the same thing today, downloading a file to
    #: discover it already had it. With this, an unchanged record costs one
    #: index lookup (6 µs) instead of one HTTP round trip.
    #:
    #: None means the listing is already complete, which is the simpler and
    #: rarer case (`custom_api`, a local file walk).
    hydrate: Callable[[Record], Record] | None = None
    connection_id: str = ""
    #: Records this pass may take, across every page.
    budget: int = 0
    #: May this pass tombstone records the source did not list? Only safe when
    #: the listing is a **complete enumeration** of the resource — a windowed
    #: or filtered read is not, and sweeping one deletes everything outside the
    #: window. Off by default for exactly that reason.
    sweeps_deletions: bool = False
    retry: RetryPolicy = DEFAULT


def _budget_of(plan: Plan) -> int:
    return plan.budget or plan.manifest.limits.records_per_sync


def run(plan: Plan, *, cancel: Cancellable | None = None,
        progress: ProgressFn | None = None,
        full_history: bool = False) -> SyncResult:
    """One pass. Returns the same `SyncResult` every connector already returns.

    **Never raises.** A connector failure is a connector error, not an
    application crash (`docs/CONNECTOR-PLATFORM.md` §30) — and the scheduler
    already relies on that, catching exceptions and losing the reason in the
    process. Here the reason survives, classified.
    """
    result = SyncResult(connector=plan.connector)
    connection = connections.get(plan.connection_id) if plan.connection_id \
        else connections.ensure(plan.connector,
                                provider=plan.manifest.provider,
                                version=plan.manifest.version)

    if connection is None:
        # The connection was removed between this pass being planned and being
        # run — a disconnect the user made while a sweep was in flight, which
        # is a thing that will happen and is not a failure. Recreating it here
        # would quietly undo their disconnect.
        result.detail = "that account was disconnected"
        return result

    if not connection.runnable:
        # Not an error: a paused or signed-out connection is a decision, and
        # reporting it as a failure every thirty minutes turns a state the user
        # chose into noise they learn to ignore.
        result.detail = connections.SENTENCE[connection.auth_state]
        with suppressed("recording a skipped connector pass"):
            observability.write(observability.Operation(
                connector=plan.connector, operation="sync",
                connection_id=connection.id, resource_type=plan.resource_type,
                result=observability.Outcome.SKIPPED, detail=result.detail))
        return result

    run_id = sync_state.begin(connection.id, plan.resource_type)
    state = sync_state.get(connection.id, plan.resource_type)
    start = "" if full_history else state.resumable_from(plan.manifest.version)

    with observability.recorded(plan.connector, "sync",
                                connection_id=connection.id,
                                resource_type=plan.resource_type,
                                correlation_id=run_id) as operation:
        try:
            walked = _walk(plan, connection, run_id, result, start=start,
                           cancel=cancel, progress=progress)
        except CancelledError:
            result.cancelled = True
            result.detail = result.detail or "stopped"
            operation.result = observability.Outcome.CANCELLED
            sync_state.interrupted(connection.id, plan.resource_type)
            return result
        except ConnectorError as exc:
            # Classified, so the row says what happened and what to do, and the
            # connection's auth state moves only if the credential is the
            # problem — a 403 must not send the user round OAuth again.
            result.errors.append(exc.message)
            result.detail = exc.message
            operation.result = observability.Outcome.FAILED
            operation.error_kind = exc.kind.value
            operation.detail = exc.detail
            connections.on_error(connection.id, exc)
            _note_rate_limit(plan, exc)
            sync_state.fail(connection.id, plan.resource_type,
                            error=exc.message)
            return result
        except Exception as exc:
            # A connector that raises something nobody classified is still not
            # allowed to take the application down with it.
            log.exception("%s: sync raised", plan.connector)
            result.errors.append(f"{plan.manifest.display_name} could not sync.")
            result.detail = "sync failed"
            operation.result = observability.Outcome.FAILED
            operation.error_kind = type(exc).__name__
            sync_state.fail(connection.id, plan.resource_type, error=str(exc))
            return result

        operation.records = result.added
        operation.result = (observability.Outcome.OK if walked.complete
                            else observability.Outcome.PARTIAL)
        if not walked.complete:
            result.detail = walked.why(plan.manifest.display_name)
        sync_state.complete(connection.id, plan.resource_type,
                            version=plan.manifest.version)
        result.detail = result.detail or (
            f"{result.added} new, {result.skipped} already had")
    return result


def _note_rate_limit(plan: Plan, error: ConnectorError) -> None:
    """Spend the local budget when the vendor says slow down.

    Without this the 429 is absorbed by the retry and the *next* request goes
    out at the rate that caused it — and by every other caller too, since they
    share the gate.
    """
    from .errors import ConnectorErrorKind

    if error.kind is ConnectorErrorKind.RATE_LIMITED:
        gate_for(plan.connector, plan.manifest.limits).note_rate_limit()


def _walk(plan: Plan, connection: Any, run_id: str, result: SyncResult, *,
          start: str, cancel: Cancellable | None,
          progress: ProgressFn | None) -> Walk:
    """DISCOVER → FETCH → … → CHECKPOINT, one page at a time."""
    gate = gate_for(plan.connector, plan.manifest.limits)
    budget = _budget_of(plan)
    seen_ids: set[str] = set()
    label = plan.manifest.display_name

    def fetch(cursor: str) -> Page:
        """One page, rate-limited and retried. The only stage that leaves the
        machine, which is why it is the only one that holds the gate."""
        def once() -> Page:
            with gate(cancel=cancel, timeout=plan.manifest.limits.per_seconds * 2):
                return plan.fetch(cursor)

        return with_retries(once, connector=plan.connector, label=label,
                            policy=plan.retry, cancel=cancel,
                            on_retry=lambda e, n, pause: log.debug(
                                "%s: retrying after %s (%.1fs)",
                                plan.connector, e.kind.value, pause))

    def commit(records: list[Any], progress_so_far: Walk) -> None:
        """NORMALIZE → DEDUPLICATE → INGEST → CHECKPOINT, for one page.

        Called by `pagination.walk` after each page and **before** the next is
        requested, which is the whole of what makes this resumable.
        """
        for index, record in enumerate(records):
            # `identified`, not `usable`: a listing that carries ids and no
            # bodies is the normal shape, and demanding text here would throw
            # away every record before `hydrate` had a chance to fetch one.
            if not isinstance(record, Record) or not record.identified:
                result.skipped += 1
                continue
            seen_ids.add(record.external_id)
            _ingest_one(plan, connection, run_id, record, result)
            if progress is not None:
                progress(progress_so_far.records - len(records) + index + 1,
                         budget, label)

        # The cursor is moved only now — after every record on this page has
        # been ingested and committed. The other order loses a page on a crash
        # AND reports success for it.
        sync_state.checkpoint(connection.id, plan.resource_type,
                              cursor=progress_so_far.resume_cursor or "",
                              items=len(records),
                              version=plan.manifest.version)

    try:
        _, walked = walk(fetch, start=start, max_records=budget,
                         cancel=cancel, on_page=commit)
    except ConnectorError as exc:
        # **A page marker the provider will not take back is not a dead end.**
        # Resuming is the whole reason a cursor is stored, and a stored one can
        # go stale — Gmail's page tokens expire, and a provider that has
        # re-filtered underneath us rejects the old one outright. Left alone
        # that fails identically on every pass forever, which is worse than the
        # restart-from-zero it was built to avoid.
        #
        # Narrow on purpose: only on a *resume*, only for the kinds that mean
        # "this request is wrong" rather than "come back later", and only once.
        # A rate limit or an outage must still be left to the retry policy.
        from .errors import ConnectorErrorKind

        stale = (ConnectorErrorKind.VALIDATION, ConnectorErrorKind.NOT_FOUND)
        if not start or exc.kind not in stale:
            raise
        log.info("%s: %s would not take the stored page marker — reading the "
                 "window again", plan.connector, plan.manifest.display_name)
        sync_state.clear(connection.id, plan.resource_type)
        seen_ids.clear()
        result.skipped = 0
        result.added = 0
        _, walked = walk(fetch, start="", max_records=budget,
                         cancel=cancel, on_page=commit)

    if plan.sweeps_deletions:
        # `complete` is passed rather than inferred: sweeping a *truncated*
        # listing tombstones the whole tail of a mailbox because a sync hit its
        # budget.
        for gone in resources.sweep_missing(connection.id, plan.resource_type,
                                            seen_ids,
                                            complete=walked.complete):
            with suppressed("recording an external deletion"):
                events.accept(events.ExternalEvent(
                    connection_id=connection.id,
                    event_type=events.EventType.DELETED,
                    resource_type=plan.resource_type,
                    resource_id=gone.external_id, correlation_id=run_id))
    return walked


def _ingest_one(plan: Plan, connection: Any, run_id: str, record: Record,
                result: SyncResult) -> None:
    """One record: is it new, and if so what does the brain learn?

    Crash isolation is here rather than in the caller for the reason
    `base.each_guarded` exists — decision H2, one bad item never aborts a sync —
    and it is one `try` rather than one per connector.
    """
    verdict, held = resources.classify(
        connection.id, plan.resource_type, record.external_id,
        fingerprint_=record.fingerprint,
        source_updated_at=record.source_updated_at)

    if verdict is Verdict.UNCHANGED:
        # **Before `hydrate`, which is the whole point.** This is the branch
        # that turns a re-sync from 600 HTTP requests into 600 index lookups:
        # `gdrive` downloads a file to discover it already has it, and `gmail`
        # used to `messages.get` every message in its window every half hour.
        result.skipped += 1
        return

    if plan.hydrate is not None:
        # The expensive call, made only for what we are keeping. Its failure is
        # one record skipped, not a failed pass — decision H2, and a mailbox
        # with one unreadable message must still sync.
        try:
            record = plan.hydrate(record)
        except Exception as exc:
            result.skipped += 1
            log.debug("%s: could not read %s: %s", plan.connector,
                      record.external_id, exc)
            return

    if not record.usable:
        # Hydrated and still nothing to remember — an empty message, a file we
        # cannot extract text from. A skip, and deliberately **not** recorded
        # as seen: nothing was stored, so the next pass should look again
        # rather than skipping it forever on the strength of one bad read.
        result.skipped += 1
        return

    source = SourceRef(
        connector=plan.connector, connection_id=connection.id,
        provider=plan.manifest.provider, account=connection.account,
        resource_type=plan.resource_type, external_id=record.external_id,
        url=record.url, source_updated_at=record.source_updated_at,
        run_id=run_id, extra=dict(record.extra))

    try:
        memory_id = plan.ingest(record, source)
    except Exception as exc:
        result.skipped += 1
        log.debug("%s: skipped %s: %s", plan.connector, record.external_id, exc)
        return

    if not memory_id:
        # The brain already had identical content — dedup by content hash,
        # which still runs underneath this. Not an error and not an addition.
        result.skipped += 1
    else:
        result.added += 1

    resources.seen(connection.id, plan.resource_type, record.external_id,
                   memory_id=memory_id or (held.memory_id if held else ""),
                   fingerprint_=record.fingerprint,
                   source_updated_at=record.source_updated_at,
                   state=ResourceState.ACTIVE)

    with suppressed("recording what a sync found"):
        happened = events.from_resource_change(
            connection.id, plan.resource_type, record.external_id, verdict,
            occurred_at=record.source_updated_at, correlation_id=run_id)
        if happened is not None:
            events.accept(happened)


def paged(records: Iterable[Any], size: int) -> Iterator[Page]:
    """Turn a list a connector already has into pages.

    The adapter for a connector whose API is *"here is everything"* — a local
    file walk, an `.ics` export, an MCP tool's one answer. It gets the
    per-page commit and the checkpointing without pretending to a cursor the
    source does not have.
    """
    batch: list[Any] = []
    index = 0
    for item in records:
        batch.append(item)
        if len(batch) >= max(1, size):
            index += 1
            yield Page(records=batch, next_cursor=str(index))
            batch = []
    yield Page(records=batch, next_cursor="")
