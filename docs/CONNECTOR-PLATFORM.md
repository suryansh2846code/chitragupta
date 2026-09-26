# The connector platform — audit, contract, and what is actually built

> The connector layer as a **stable boundary** between Chitragupta and external
> systems. Companion to [`CONNECTORS.md`](CONNECTORS.md) (which sources exist
> and why), [`REACHING-AN-APP.md`](REACHING-AN-APP.md) (which route reaches
> one), [`ACTION-COVERAGE.md`](ACTION-COVERAGE.md) (what a write must do) and
> [`ARCHITECTURE.md`](ARCHITECTURE.md) §3 (which way imports may point).

This document is the plan **and** the honest scoreboard. Section 7 lists what is
not built. Nothing in section 7 is described anywhere else as though it were.

---

## 1. The audit (2026-09-26)

Sixteen connector classes, ~7,700 lines, one 304-line base class, one action
registry, one permission gate, one background scheduler.

### 1.1 Every connector, and what it actually does

| connector | reach | read | write | events | incremental | auth | notes |
|---|---|---|---|---|---|---|---|
| `files` | on-device | ✓ | — | — | ✓ (per-folder mtime) | none | `always_available`; manual sync only |
| `notes` | on-device | ✓ | — | — | — | none | written by hand; nothing to poll |
| `imessage` | on-device | ✓ | — | — | ✗ (days window) | none | needs Full Disk Access |
| `apple_mail` | on-device | ✓ | — | — | ✓ | none | needs Full Disk Access |
| `apple_calendar` | on-device | ✓ | — | — | ✓ | none | reads `.ics` |
| `apple_health` | on-device | ✓ | — | — | — | none | writes `metrics.py`, never the brain |
| `gmail` | account | ✓ | ✓ ×7 | — | ✓ | OAuth (Google) | the deepest connector |
| `gcal` | account | ✓ | ✓ ×6 | — | ✗ | OAuth (Google) | windowed ±180d |
| `gdrive` | account | ✓ | ✓ ×4 | — | ✓ | OAuth (Google) | |
| `google_fit` | account | ✓ | — | — | — | OAuth (Google) | measurement, not memory |
| `slack` | account | ✓ | ✓ `send` | — | ✓ | pasted token | messaging trio |
| `telegram` | account | ✓ | ✓ `send` | — | ✓ | MTProto session | messaging trio |
| `github` | account | ✓ | **stood down** | — | ✓ | pasted token | `prefer_mcp="github"` |
| `linear` | account | ✓ | **stood down** | — | ✓ | pasted token | `prefer_mcp="linear"` |
| `notion` | account | ✓ | **stood down** | — | ✗ | pasted token | `prefer_mcp="notion"` |
| `mcp:<id>` | account | ✓ | ✓ `perform` | — | ✗ | OAuth DCR / token / none | one per configured server |
| `custom:<id>` | account | ✓ | — | — | ✗ | token (bearer/header/query) | one per user definition |

**Polling is the only trigger.** `scheduler.py` sweeps every `auto_sync=True`
connector on `sync_interval_minutes`, serially. There is no webhook receiver
anywhere in the package — `grep -r webhook` matches one word in a stop-list.

### 1.2 What was already sound, and is reused unchanged

| thing | where | why it is not rebuilt |
|---|---|---|
| per-item crash isolation | `base.py::each_guarded` | decision H2, already contract-tested |
| cancellation into the connector | `base.py` + `scheduler` | already per-item, not per-connector |
| a watermark that only advances on a clean pass | `base.py::_finish` | the correct rule, already tested three ways |
| risk tiers on the action, not in the gate | `actions.py::ActionSpec.risk` | the permission system derives from it |
| fail-closed permission check | `agents/permissions.py::check` | unknown action → refused |
| per-`server:tool@scope` grants | `agents/grants.py` | the only readable key a connector write has |
| write-tool classification needing evidence | `mcp_source.py::_is_write` | `readOnlyHint` or a known read verb |
| secret redaction before extraction | `core/redact.py` | already on the ingest path |
| the capability manifest | `mcp_manifest.py` | *reads N, changes M, K need care* |
| bounded lanes for slow HTTP handlers | `api/concurrency.py` | the shape the sync lanes copy |
| the contract test suite | `tests/connectors/` | 6 files already walk `REGISTRY` |

### 1.3 The sixteen findings

1. **No capability model.** A connector declares `auto_sync`, `incremental`,
   `runs_on_device`, `platforms` — four booleans about *mechanics*. Nothing
   anywhere says `gmail` can `send_email` but not `delete_message`. The only
   capability vocabulary in the product is `mcp_manifest.py`, and it exists for
   exactly one connector family.
2. **Write surface is discovered by duck typing.** `actions.py` calls
   `get_connector("gmail").send_email(...)`. `messaging.py` finds a chat
   connector by "does it have `chats`, `history`, `send`". A connector that
   grows a method grows an action surface with nothing declaring it.
3. **One account per provider, structurally.** `connector_state` is
   `PRIMARY KEY (connector)`. `google_auth._token_path()` is one file. There is
   no connection identity anywhere; `provider == account` is baked into storage.
4. **Checkpointing is per-connector, not per-resource, and coarse.** One
   `cursor` string for a whole connector. `gdrive` syncs files and `gmail` syncs
   messages, but a connector reading two resource types has one watermark for
   both. A crash mid-pass loses the whole pass — `_finish` only stores a cursor
   when `not cancelled and not errors`, which is *safe* but means a 2,000-item
   sync that fails at item 1,900 starts from zero.
5. **No pagination utilities.** `notion.py` hand-rolls `start_cursor`,
   `gmail.py` hand-rolls `pageToken`, `custom_api.py` has none at all and reads
   whatever one request returns. No loop guard anywhere.
6. **Deduplication is a content hash.** `idx_memories_chash` is
   `hash(text, uri)`. An *edited* email is a new memory, not an update; the old
   one stays forever. There is no stable external identity —
   `brain.ingest(source_id=...)` exists in the schema and **no connector passes
   it**.
7. **External deletion is invisible.** Nothing models a tombstone. A deleted
   file stays in the brain indefinitely, and recall will cite it.
8. **No event abstraction.** `routines.py::sweep(new_email_count=...)` is the
   entire event system: one integer, derived from one connector's added-count,
   for one trigger type.
9. **No error taxonomy.** `models/errors.py` is mature and is a *sibling* —
   `connectors/` may not import it. Connectors instead do
   `result.errors.append(str(exc))`. `custom_api.py` maps 401/403 to one
   sentence and everything else to `f"API error {exc.code}"`.
10. **No retries, no backoff, no rate-limit handling.** `grep -rn "retry\|
    backoff\|429"` over `connectors/` returns two comment lines in `slack.py`.
    A 429 is a failed sync that retries in thirty minutes.
11. **No concurrency isolation between connectors.** The sweep is a serial
    `for` loop. One connector that blocks for its timeout blocks every
    connector behind it, and `MCPConnector.status()` starts a subprocess.
12. **No health model.** `connector_state.status` is `"ok"`/`"error"` written by
    `_finish`. `AUTH_REQUIRED`, `RATE_LIMITED`, `DEGRADED`, `SYNCING` are not
    representable, so the UI cannot show them.
13. **No observability.** No operation record, no counters, no correlation id.
    The evidence a failure leaves is a `suppressed()` log line.
14. **No backpressure.** `each_guarded` does `seq = list(items)` — the whole
    page in memory — then ingests synchronously. A connector returning 100k
    records is 100k records in a Python list.
15. **Custom API is unbounded in one direction and under-specified in the
    other.** `base_url` is any `http(s)` URL including loopback and RFC-1918
    space (an SSRF surface aimed at the user's own machine), while the
    connector supports no pagination, no incremental sync, no writes, and no
    rate limit.
16. **Provenance is partial and inconsistent.** Six connectors pass a
    `metadata` dict; each invents its own keys (`message_id`, `file_id`,
    `page_id`, `event_id`, `mcp_tool`). `source_id`, `uri` and `event_time` are
    almost never set. No account, no run id, no `original_updated_at`, no trust
    classification survives ingestion.

### 1.4 Architecture constraints this plan must not break

- `connectors/` may **not** import `models/`, `brain/` (except
  `brain.ingest()`), `agents/` or `api/`. Sibling rule, `ARCHITECTURE.md` §3.2.
- `tests/test_import_layering.py` pins the `mcp` cycle at exactly three
  modules. New modules must not join it.
- `tests/api_surface.json` pins the HTTP surface. Every new endpoint is a line
  in the same commit.
- A lazy import is a real edge. Moving one into a function body hides a cycle,
  it does not break one.

---

## 2. The contract

A connector describes itself with one machine-readable record. It is **read off
the class**, never hand-maintained beside it, because that is how the four
booleans in §1.1 stayed true and the hand-maintained lists in
`agents/permissions.py` did not.

```
ConnectorManifest
├── connector_id · display_name · provider · version
├── auth        AuthMethod + the lifecycle state of each connection
├── capabilities frozenset[Capability]     ← the whole security surface
├── resources   what kinds of thing it holds
├── sync        strategy · incremental · resumable · cursor kind
├── events      webhook | polling | none, and the dedup key
├── health      supported? cost?
└── limits      requests/interval · concurrency · page size
```

### 2.1 Capabilities are verbs on resources, never app names

A capability is `Verb.READ` on `Resource.EMAIL`, spelled `read:email`. It says
what can be done, not what the app is called, so the permission layer can reason
about `write:email` across Gmail, Apple Mail and an MCP server without knowing
any of them.

Four access tiers, because they are four different questions the gate asks:

| tier | means | gate |
|---|---|---|
| `READ` | answers a question | no approval |
| `WRITE` | changes something at the vendor, reversibly | risk tier decides |
| `DESTRUCTIVE` | no inverse exists | always asks; never grantable |
| `OUTBOUND` | reaches a person who is not the user | needs a permitted recipient |

`OUTBOUND` is separate from `WRITE` on purpose: `create_draft` writes to the
user's own mailbox and reaches nobody, and the product deliberately treats those
differently (`actions.py` — a draft is GREEN, a send is AMBER).

**Unknown capabilities fail closed.** A capability string that does not parse is
not "probably a read"; it is refused.

### 2.2 The chain, and there is only one

```
connector declares capability
   → action registry names the capability it needs
   → agents/permissions.check() evaluates risk + recipient
   → approvals card (or a standing grant)
   → connector executes
```

No second permission system. `capability_of(action)` is a lookup on the
existing `ActionSpec`; the existing `Risk` tiers are unchanged and remain the
thing the gate reads.

---

## 3. What is built

Twelve modules, all inside `connectors/`, all below the API and agents layers,
none importing a sibling package.

| module | owns |
|---|---|
| `capability.py` | the verb/resource vocabulary and the four access tiers |
| `contract.py` | `ConnectorManifest`, and the small protocols a connector may implement |
| `errors.py` | the connector error taxonomy, classification, retryability |
| `retry.py` | bounded backoff with jitter that honours `Retry-After` |
| `limits.py` | a per-connector token bucket and concurrency gate |
| `pagination.py` | four pagination strategies with loop guards |
| `provenance.py` | `SourceRef` — where one record came from, carried into ingest |
| `connections.py` | connection identity, multi-account, the auth lifecycle |
| `sync_state.py` | durable per-`(connection, resource)` checkpoints |
| `events.py` | `ExternalEvent`, durable dedup, ordering metadata |
| `health.py` | the health state machine and a cheap check |
| `observability.py` | structured operation records and counters |

They compose in `engine.py`:

```
DISCOVER → FETCH → NORMALIZE → DEDUPLICATE → INGEST → CHECKPOINT → COMPLETE
```

with the checkpoint written **after** the page is committed, never before.

---

## 4. Migration, not rewrite

Existing connectors are not rewritten. Each gains a declaration and keeps its
`sync()`:

1. **Additive.** `ConnectorManifest` is derived from the class; a connector that
   declares nothing gets a manifest that says so, and a contract test names it.
2. **Adapters, not rewrites.** `engine.py` is opt-in per connector. A connector
   that has not moved still syncs exactly as it did.
3. **One old thing removed per landing**, never before the new one is carrying
   traffic.

---

## 5. Order

Shared infrastructure first — a connector migrated onto an abstraction that is
still moving has to be migrated twice.

```
capability → contract → errors → retry/limits → pagination
    → provenance → connections → sync_state → events → health → observability
    → engine → migrate connectors → MCP + custom-API hardening → UI → docs
```

After every landing: the focused test, `tests/connectors/`, `pytest`,
`ruff check chitragupta tests`, `mypy chitragupta`, and the layering test.

---

## 6. Where it landed

Measured 2026-09-26, on this branch:

| | before | after |
|---|---|---|
| tests | 3,741 passed, 31 skipped | **4,371 passed, 31 skipped** |
| ruff · mypy | clean · clean (181 files) | **clean · clean (196 files)** |
| coverage, whole tree | 80% | **81%** |
| coverage, `connectors/` | 63% | **76%** |
| architecture cycles | 3 known, bounded | **3 known, bounded — unchanged** |

Twelve new modules, each 74–100% covered. The connector layer's own numbers,
against fakes on this machine (2,000 records, 100 per page):

| | |
|---|---|
| initial sync | 314 ms · 6,375 records/s |
| resume from a checkpoint | under 1 ms — the cursor skips what landed |
| full re-read, nothing changed | 23 ms · **13.4× faster**, 2,000 skipped without reading a body |
| identity lookup | 6 µs per record |
| provenance carried per memory | 284 bytes |

And Gmail specifically, counted in **requests** rather than milliseconds —
because the requests are the cost that matters, each one a round trip against a
per-minute quota shared with everything else the account does:

| a 600-message window | list calls | body fetches | total |
|---|---|---|---|
| first pass | 2 | 600 | 602 |
| every pass after it | 2 | **0** | **2** |

600 avoided round trips per sweep, on a 30-minute timer — roughly 28,800 a day
that were previously spent finding out nothing had changed. `Plan.hydrate` is
what makes it possible: the keep-or-skip decision is answered from the message
id, before a body is paid for.

Drive is the same shape and the same result — 202 requests down to 2 over 200
files — plus two fixes that were not about cost at all. It used to scan **every
Drive memory in the table** on every pass to rebuild what it already knew, and it
compared modified *dates*, so a document edited twice in one day was read as
unchanged the second time and the edit never reached the brain.

The 13.4× is the N+1 the audit found in `gdrive`, removed for every connector
that moves onto the engine: an unchanged record is recognised from the listing
rather than downloaded to find out.

---

## 7. What this does NOT do

Written here so it is not claimed anywhere else.

- **No provider webhook receiver ships.** The event abstraction exists and
  polling produces normalized events through it; nothing registers a webhook
  with Google, Slack or GitHub. A local-first Mac app has no public URL, and
  building one means a tunnel or a relay — which is the hosted-broker trade
  `CONNECTORS.md` refuses. `EventSource.WEBHOOK` is representable and unused.
- **No connector process sandbox.** A stdio MCP server is a subprocess with the
  user's environment. Isolation today is the bounded session, the timeout and
  the tool allow-list, not a sandbox.
- **Multi-account is structural, not universal.** The storage, the identity and
  the lifecycle are per-connection, and the API refuses to guess when a
  connector has two. But `google_auth` still writes one token file and
  `mcp_servers.json` is one spec per server, so **Google and MCP remain
  single-account in practice** until each credential store is migrated. What
  changed is that nothing above them assumes it any more.

- **One connector at a time.** `scheduler.py::_sync_all` is still a serial
  loop. The *bound* exists — `limits.py` gives each connector a rate budget and
  a lane, and `engine.run` holds one per request — but running two connectors
  at once means concurrent writers on the one `MemoryStore` connection, which
  is a change to the brain's write path and needs its own landing with its own
  measurements. Recorded as `docs/ARCHITECTURE.md` §6.9.

- **Three connectors are migrated onto the engine, not fifteen.**
  `custom_api`, `gmail` and `gdrive` are on it; every connector declares its
  manifest, uses the capability floor and reports health. Calendar, Slack,
  Telegram and the on-device sources still run their own hand-written `sync()`,
  so they do not yet get per-page checkpoints or identity-based dedup. That is
  the migration order §4 sets out — shared infrastructure first — and it is
  deliberately unfinished rather than rushed.

  **Calendar is next.** Its window is `±180 days`, so the same "never sweep a
  window" rule applies, and it is the last of the three Google connectors.

- **A name that does not resolve is allowed through.** `outbound.py` refuses
  loopback and link-local by literal, by name and by resolution, and re-checks
  where a request landed. It does not refuse a host that fails to resolve:
  doing so makes every address check a network call — breaking offline use and
  making the suite depend on a resolver — and does not close the rebinding
  window anyway. The residual gap is a host that fails to resolve now and
  points at this machine a moment later.
