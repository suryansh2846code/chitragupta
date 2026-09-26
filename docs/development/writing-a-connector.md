# Writing a connector

> What you have to implement, what you get for free, and the six things the
> framework will refuse to let you get wrong. Contract:
> [`../CONNECTOR-PLATFORM.md`](../CONNECTOR-PLATFORM.md).

Before anything else: **is a connector the right answer at all?**
[`../REACHING-AN-APP.md`](../REACHING-AN-APP.md) says when it is not. Where the
vendor ships an MCP server, that is the route — a catalog entry, not a class.
A connector exists where nothing else can reach.

---

## 1. The whole of it

```python
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, PaginationStrategy, SyncStrategy
from . import engine
from .engine import Record
from .pagination import Page, cursor_of
from .provenance import SourceRef
from .resources import fingerprint


class AcmeConnector(Connector):
    name = "acme"                     # the registry key; must match
    label = "Acme"                    # what a PERSON reads. Never an id.
    provider = "acme"

    auth_method = AuthMethod.API_KEY
    required_scopes = ("tickets:read",)

    # What it can do. Verbs on resources — `capability.py`. Unknown fails
    # closed, so a typo here is an error at import rather than a write
    # classified as harmless.
    capabilities = caps("read:issue", "search:issue")

    auto_sync = True                  # the background loop re-runs it
    incremental = True                # `sync()` honours `since`
    resumable = True                  # a crash costs one page, not the pass
    sync_strategy = SyncStrategy.TIMESTAMP
    pagination = PaginationStrategy.CURSOR

    # Ours, and `Limits.as_dict()` says so. Not a promise the vendor made.
    limits = Limits(requests=60, per_seconds=60.0, concurrency=2,
                    page_size=100, records_per_sync=500)

    def is_configured(self) -> tuple[bool, str]:
        """(ready, why-not). The reason is the text a user reads, so an empty
        one is a silent dead end."""
        if not get_settings().get_secret("ACME_TOKEN"):
            return False, "click setup to paste your Acme token"
        return True, ""

    def _page(self, cursor: str) -> Page:
        """One request. The only method that leaves the machine."""
        payload = self._get("/tickets", cursor=cursor)
        return Page(
            records=[self._record(row) for row in payload["items"]],
            next_cursor=cursor_of(payload, "next_cursor"))

    def _record(self, row: dict) -> Record:
        return Record(
            external_id=str(row["id"]),          # the vendor's own id
            text=f"{row['title']}\n\n{row['body']}",
            title=row["title"],
            url=row.get("url", ""),
            source_updated_at=row.get("updated_at", ""),
            fingerprint=fingerprint(row.get("updated_at"), row.get("version")),
            extra={"status": row.get("status")},  # provider metadata, kept
            raw=row)

    def _ingest(self, record: Record, source: SourceRef) -> str:
        from ..brain import get_brain
        out = get_brain().ingest(record.text, kind="issue",
                                 title=record.title, fast=True,
                                 **source.ingest_kwargs())
        ids = out.get("memory_ids") or []
        return str(ids[0]) if ids else ""

    def sync(self, *, limit=None, full_history=False, cancel=None,
             progress=None, **_) -> SyncResult:
        ready, reason = self.is_configured()
        if not ready:
            result = SyncResult(connector=self.name)
            result.errors.append(reason)
            return self._finish(result)

        return self._finish(engine.run(
            engine.Plan(connector=self.name, manifest=self.manifest(),
                        resource_type="issue", fetch=self._page,
                        ingest=self._ingest,
                        connection_id=self.connection().id,
                        budget=limit or 0),
            cancel=cancel, progress=progress, full_history=full_history))
```

Then one line in `connectors/__init__.py::REGISTRY`, and one entry in
`tests/connectors/harness.py::FAKES`.

**The live reference is [`custom_api.py`](../../chitragupta/connectors/custom_api.py).**
It is the smallest complete connector in the tree and it does every one of the
things above, including the parts that are easy to skip.

---

## 2. What you get, without writing it

| you get | from | what it means |
|---|---|---|
| rate limiting | `limits.py` | per connector, shared across every caller |
| a concurrency lane | `limits.py` | one slow connector cannot hold the app |
| bounded retries | `retry.py` | only retryable kinds, jittered, `Retry-After` honoured |
| an error taxonomy | `errors.py` | one vocabulary, secrets scrubbed on construction |
| pagination + loop guards | `pagination.py` | repeated cursors, page ceilings, "it was truncated" |
| checkpoints per page | `sync_state.py` | a crash costs one page |
| stable identity | `resources.py` | an edit supersedes; a deletion is recorded |
| tombstones | `resources.py` | external deletion is representable |
| provenance | `provenance.py` | account, external id, url, freshness, run id |
| events | `events.py` | your sync's findings become `resource.created/updated` |
| health | `health.py` | a state and a sentence, without a probe |
| observability | `observability.py` | duration, retries, records, correlation id |
| crash isolation | `engine.py` | one bad record never aborts a pass |
| contract tests | `tests/connectors/` | ~25 suites run against your connector automatically |

---

## 3. The six the framework refuses to let you get wrong

1. **A write may not be gentler than what it does.** Declare `delete:file` and
   register the action at GREEN, and `test_capability_floor.py` fails. Fail
   closed, always: an unrecognised capability is refused, never assumed to be
   a read.
2. **A checkpoint never precedes an ingest.** `engine._walk` commits the page,
   then moves the cursor. The other order loses records *and reports success
   for them* — there is no going back from that.
3. **A truncated listing never tombstones.** `sweeps_deletions` requires
   `Walk.complete`, so a sync that hit its budget cannot delete the tail of a
   mailbox.
4. **A secret cannot reach a screen.** `ConnectorError.detail`,
   `Connection.auth_detail` and `Operation.detail` are scrubbed on
   construction, not at the call sites.
5. **A user's stop actually stops.** `cancel` is checked per item, per page,
   and inside every backoff — `retry._wait` sleeps in 0.25s slices for exactly
   this reason.
6. **A connector failure is a connector error.** `engine.run` never raises;
   the worst case is a `SyncResult` with a sentence in `errors`.

---

## 4. The decisions only you can make

**`external_id`.** The vendor's own id. Not the title (renamed), not the text
(edited), not the URL (moved), not the position in a list (reorders). Get this
wrong and every update becomes one tombstone plus one new memory.

**`fingerprint`.** What counts as a change *for this provider*. Drive's
`modifiedTime` is authoritative; a Slack message's `edited.ts` is; a generic
REST endpoint has nothing, so `custom_api` digests the whole record. There is
no general rule, which is why the framework asks rather than guesses.

**`sweeps_deletions`.** Only true when your listing is a **complete
enumeration** of the resource. A search, a filter, a window or a feed is not,
and sweeping one deletes everything outside it.

**`resource_type`.** One per kind of thing you read. Two kinds means two
checkpoints; one cursor for both moves each past the other's records.

**The capability set.** What you can do, not what you might one day. A
capability you declare and do not implement is a control that cannot work.

---

## 5. Authentication

The credential lives where credentials live — the Keychain via
`settings.set_secret`, an OAuth token file, a vendor session. **`connections.py`
holds the state of one, never one.**

`Connector.connection()` reconciles: `is_configured()` is the authoritative
answer, and the row mirrors it. You do not normally call `transition()`
yourself. The two you might:

* `AuthState.EXPIRED` — an access token ran out and a refresh will fix it.
  Ours, silently.
* `AuthState.REAUTH_REQUIRED` — the refresh failed. **Only the user can fix
  this**, and only `errors.ConnectorError.needs_reauth` (a 401, never a 403)
  should put a connection here. A 403 is a scope problem and survives signing
  in again; sending the user round OAuth for one teaches them the app is
  broken.

---

## 6. Testing

Add your fake to `tests/connectors/harness.py::FAKES` and you inherit the
generic suites — contract, manifest, crash isolation, idempotency, redaction,
sync contract, fixtures. Then write what is specific to your provider.

**Never a live API.** Every fake replaces the *transport* only: your parsing,
your dedup and your writes all run for real. A connector that cannot be faked
is a connector nobody can test, and that is worth finding out while you are
writing it.

Failure injection is already generic —
[`tests/connectors/test_engine.py`](../../tests/connectors/test_engine.py)
covers timeout, reset, 401, 403, 404, 409, 429, 500, malformed responses,
repeating cursors, partial syncs and mid-pass crashes. Add a provider-specific
case only where your provider fails in a way none of those cover.

And the standing rule: **a bug fix ships with a test you have watched fail.**
Reintroduce the bug, confirm red, restore.

---

## 7. Where to look

| you need | read |
|---|---|
| the contract, and what is deliberately not built | [`../CONNECTOR-PLATFORM.md`](../CONNECTOR-PLATFORM.md) |
| which route reaches a source at all | [`../REACHING-AN-APP.md`](../REACHING-AN-APP.md) |
| what a write must do beyond doing it | [`../ACTION-COVERAGE.md`](../ACTION-COVERAGE.md) |
| the MCP client's own rules | [`mcp-contract.md`](mcp-contract.md) |
| which way imports may point | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §3 |
