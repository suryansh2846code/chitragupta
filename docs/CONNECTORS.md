# Connectors — the plan

> Everything the user connects is a **connector**. That is the only word that
> appears in the UI. MCP is how some of them are implemented; the user never
> hears the acronym, the same way they never hear "vendor CLI" when they sign in
> to Claude. *(CLAUDE.md — "Never surface an internal.")*

**State today:** 11 connectors + custom-API, 1,758 lines, and **no direct test
coverage** — the only connector code the suite touches is five pure helpers in
`test_edge_cases.py` (`parse_ics`, `_dig`, `_decode`, `_extract_body`,
`_token_path`). Not one `sync()` or `is_configured()` is exercised.

That matters more than usual right now, because the next phase runs **other
people's code on the user's machine** and lets it write into the brain.

---

## The three tiers

Breadth does not come from one mechanism. It comes from three, and knowing which
tier a source belongs to is the first decision for every new integration.

| Tier | What | How | Cost per new source |
|---|---|---|---|
| **Deep** | Gmail, Drive, Calendar, Apple Mail, Notes, iMessage | Hand-written connector | High — but the list is ~6 and closed |
| **Wide** | Slack, Linear, GitHub, Jira, Stripe, Sentry… | **One MCP-backed connector** | Zero — a catalog entry |
| **Long tail** | Any REST API | `custom_api.py` ✅ already built (H8) | A form the user fills in |
| **Closed** | LinkedIn, WhatsApp, Instagram | Nobody can, Composio included | — |

Deep exists because MCP tools are shaped to answer *one question for an LLM*,
not to page 3,000 messages into a brain. Wide exists because hand-writing the
long tail is the thing that never finishes.

---

## What a connector says it can do

`mcp_manifest.py` answers the question a person asks when they add one, which
is never *how many tools is that*:

```
GitHub reads 26 things, changes 16, 2 need care.
  Can read            26   answers questions; agents use these without asking
  Can change          16   each one puts a card in front of you first
  Cannot be undone     2   delete_file, merge_pull_request — always ask
  Its own settings     1   lists about the connector, not about you
```

Eight facts, four from the server and four from us, and the split is stated
rather than blurred:

| from the server | from us |
|---|---|
| what it can read | which tools you left switched on |
| what it can change | how much we read per sync, and for how long |
| what cannot be undone | first-party? version-pinned? answered just now? |
| how it authenticates | its own endpoint, or a subprocess here |

**Nothing is invented.** MCP has no standard for OAuth scopes or rate limits,
so there is no field pretending to know them — a limit *we* impose is not a
promise the vendor made, and a manifest that blurs the two will eventually be
quoted back as theirs.

Served on `GET /api/connectors/mcp/{id}/tools` beside the existing `tools`
array, which the switches are still built from: the manifest is how a
connector is *read*, the list is how it is *changed*.

## Phase 0 — A safety net, before anything else

Nothing below is safe to build on a layer with no tests. Every meaningful
connector fix in `JOURNEY.md` (HTML noise, `.docx`, shared files, duplicates,
date parsing) was found by a **user**, not by the suite. That has to stop being
the discovery mechanism before third-party code joins the pipeline.

- [x] **0.1 — `tests/connectors/` with a fake-transport harness.** Each
      connector's `sync()` runs against recorded fixtures, no network. The
      pattern already exists for models (`tests/test_cursor_cli_backend.py`);
      copy its shape.
- [x] **0.2 — Freeze the `Connector` contract.** A test that walks `REGISTRY`
      and asserts every class declares `name`, `label`, `platforms`,
      `is_configured()` and a `sync()` returning `SyncResult`. Catches a new
      connector that forgets half the interface — which is exactly what a
      registry-plus-duck-typing design invites.
- [x] **0.3 — Per-item crash isolation, proven.** `gmail.py:100` already does
      this correctly ("one bad message never aborts the sync", decision H2).
      Assert it for **every** connector by feeding one poisoned item into a
      fixture batch and checking the rest still land.
- [x] **0.4 — Re-sync is idempotent.** Sync the same fixture twice; memory count
      must not move. This currently works only because of the
      `idx_memories_chash` unique index — no connector checks anything. Freeze
      it before 1.1 changes how fetching works.
- [x] **0.5 — Redaction on the ingest path.** Brain v1.5 masks secrets on
      ingest; prove a connector carrying an API key in a message body cannot
      land it unmasked. Becomes load-bearing the moment MCP servers write here.

**Why first:** it is the only phase that makes the rest reversible.

---

## Phase 1 — Fix the contract before widening it

Four gaps in `connectors/base.py`, all of which get worse with more connectors.

- [x] **1.1 — Incremental sync.** `connector_state.cursor` exists
      (`core/db.py:50`) and **no connector uses it as a watermark**. The one
      writer is `files.py:101`, which stores the list of synced folder paths in
      it; `notion.py`'s `start_cursor` is Notion's own pagination *within* a
      single sync, not carried across runs. `_finish()` persists only
      `status`/`detail`/`last_sync`. So every 30-minute background pass
      re-downloads the full window — up to 600 Gmail messages — and discards
      nearly all of it on a content-hash collision. Correct, and expensive in
      time, battery and API quota. Give `SyncResult` a `cursor`, have
      `_finish()` persist it, and have `sync()` receive the previous one. (If
      `files.py` keeps the column for paths, it needs its own field first.)
- [x] **1.2 — A typed `sync()` signature.** Today it is `**kwargs: Any`, and six
      connectors improvise their own window/limit/cursor arguments. Define the
      shared ones (`since`, `limit`, `full_history`, `cancel`) on the base.
- [x] **1.3 — Cooperative cancel reaches the connector.** `sync_all` checks
      `self._cancel` **between** connectors (`scheduler.py:65`), so cancelling
      mid-Gmail still waits for all 600 messages. Pass the token down and check
      it in the item loop.
- [x] **1.4 — Per-connector progress.** Long work must report progress that
      survives a refresh (CLAUDE.md). A first sync reports nothing until the
      whole connector finishes. `SyncResult` needs a progress callback.
- [x] **1.5 — Decide `_AUTO` deliberately.** `scheduler.py:22` auto-syncs five of
      eleven: `gmail, gcal, gdrive, notion, imessage`. GitHub, Linear, Apple
      Mail, Apple Calendar and Notes never refresh on their own. Either that is
      a capability flag on the class, or it is a bug — right now it is a list a
      reader cannot explain.

**Why before Phase 2:** the MCP connector will be the twelfth implementation of
this interface. Fix the interface while there are eleven, not fifty.

---

## Phase 2 — One connector that speaks MCP

The whole point: **write this once, and every new source after it is config.**

- [x] **2.1 — `connectors/mcp_source.py`.** A `Connector` subclass that spawns an
      MCP server over stdio and maps its tools onto `sync()`. The SDK is already
      installed and `ClientSession` / `stdio_client` both import — Chitragupta
      currently uses MCP in one direction only (serving its brain out via
      `mcp_server/`). This is the same library pointed the other way.
- [x] **2.2 — Tool → memory mapping.** The hard part, and where this earns or
      loses its keep. A server exposing `list_messages` pages well; one exposing
      only `search_messages` cannot be bulk-synced and must degrade to
      on-demand recall instead of pretending to sync. Detect which, and say so
      in the UI rather than silently indexing nothing.
- [x] **2.3 — Sign-in belongs to the server.** A local stdio MCP server runs its
      own browser OAuth against its own vendor — GitHub's official server does
      exactly this. So **we register no OAuth client, need no CASA assessment,
      and the consent screen shows the vendor's name.** This is the same shape
      as `auth_flows.py` for models ("the OAuth client belongs to that CLI"), so
      reuse that seam rather than inventing a parallel one.
- [x] **2.4 — Errors are translated, never dumped.** `models/errors.py` already
      has the taxonomy and `classify_cli()`. An MCP server that dies, hangs or
      returns a protocol error must surface as *what happened and what to do* —
      never raw JSON, never a stack trace.
- [x] **2.5 — Health, visibly.** A server that crashed on launch must not read
      as "Connected". Reuse the `is_configured() -> (ready, reason)` contract.

---

## Phase 3 — Breadth, safely

- [x] **3.1 — A vetted catalog, not a free-for-all.** ~20,000 MCP servers exist;
      only a few dozen are first-party (Google/Gmail, Slack, Notion, Linear,
      GitHub, Stripe, Sentry, Cloudflare). Ship a curated list with the tier
      marked, and let advanced users add their own.
- [x] **3.2 — Install the way we install vendor CLIs.** `models/cli_manager.py`
      already solves this: fetch the artifact **directly** (never pipe an
      install script into a shell), pin the version, verify before linking,
      background job with progress, never link a failed download. Same rules,
      same machinery — an MCP server is third-party code running as the user.
- [x] **3.3 — Least privilege at connect time.** Show which tools a server
      exposes before it is enabled, and let the user disable the write ones.
- [x] **3.4 — Honest platform limits in the UI.** LinkedIn cannot be read by
      anyone — LinkedIn's User Agreement §8.2 bans automated access, feed and
      member data sit behind partner approval, and every community server is a
      scraper (post-crackdown, on a Playwright fork built to evade detection).
      Composio's LinkedIn toolkit is write-only for the same reason. Discord
      needs a bot token and can never read personal DMs. **Say so in the place
      the user is looking** rather than shipping a control that cannot work
      (CLAUDE.md — "Never show a control that cannot work"), and never ship a
      path that risks the user's account.

---

## Phase 4 — Actions (only after 0–3)

*Built on the confirmation path that already existed for `send_email` rather
than a parallel one: `available_actions()` is what a card is built from,
`perform(..., confirmed=True)` is the only way anything runs, and `confirmed`
defaults to False so a caller that forgets it fails closed.*

Connectors are read-only today by design (decision C1). MCP tools write.

- [x] **4.1 — Write tools behind explicit confirmation.** "Agents that act with
      confirmation" is already the deferred item in `DECISIONS.md`; this is the
      mechanism arriving, not a new idea.
- [x] **4.2 — Nothing writes without the user seeing what and where.**

---

## Phase 5 — What a server can answer, offered to the agent

*`chitragupta/connectors/mcp_tools.py`. The connector half only — how an agent
loop presents these is Agents' call, and the contract between the two is
`MCPToolRef` / `list_tools()` / `call_tool()` / `invalidate()`.*

Phase 2 drew the line that makes this necessary: a server that can only
`search_records` cannot seed a brain, so it is never synced — and until now that
meant it was never used at all. It can still answer a question the user just
asked, which is the third thing a server is good for.

- [x] **5.1 — Asking what exists is cheap.** `list_tools()` is TTL-cached
      (`models/cache.py::ttl_cached`, 5 minutes) because the loop asks on every
      turn and each server costs a subprocess. Uncached this is the Models-drawer
      freeze moved inside chat. `invalidate()` is called from `upsert_server()`
      and `delete_server()`, so adding a connector or narrowing what it may read
      is visible on the next turn rather than five minutes later.
      Proof: `test_the_second_call_spawns_no_subprocess`.
- [x] **5.2 — Reads only, failing closed.** A write tool is listed (a
      confirmation card is built from something) and carries `writes=True`;
      `call_tool()` refuses it, then refuses it again against the server's own
      classification inside the session it opened, so a stale cache cannot let
      one through. Writes keep the Phase 4 propose/confirm path — `mcp_action`
      stays in `NEVER_UNATTENDED`.
- [x] **5.3 — A reply a model can hold.** Truncated to 8 000 characters with the
      truncation stated in the text, so a tool answering with megabytes of vendor
      JSON cannot evict the conversation and the recall block before the model
      reads it.
- [x] **5.4 — One broken server costs one server's tools.** Every server is asked
      at the same time under its own ceiling, so N connectors cost one timeout
      rather than N; a crash, a missing binary or a stalled handshake contributes
      nothing and takes nothing away from the others. Failures reach the model as
      a sentence from `mcp_errors.explain()`, never as a raise.

---

## Order, and why

```
0 (safety net)  →  1 (contract)  →  2 (MCP connector)  →  3 (breadth)  →  4 (actions)
```

Phase 0 is not optional and not a formality: it is the only phase that makes
Phases 2–4 recoverable. Phase 1 is cheaper now than after the twelfth
implementation exists. Phases 2 and 3 are where the 250-app gap against
Turnstone actually closes — **locally**, which is the entire point.

## What this deliberately does not do

- **No Composio, and no hosted broker.** Turnstone routes connectors through
  Composio's cloud (`TURNSTONE-TEARDOWN.md`: *"your data + tokens leave your
  machine… their local story is really cloud-brain"*), and that trade is exactly
  the ground Chitragupta owns. Adopting it deletes the winning rows from our own gap
  analysis.
- **No scraping, ever** — of LinkedIn or anything else. A ban lands on the
  *user's* account, not ours.
- **No server.** Nango, Activepieces and Open Connector are all real open-source
  options and all of them are services with a database. We are a Mac app; an MCP
  server is a subprocess.


---

## Phase 6 — Making it reachable

Phases 0–5 built the layer correctly and nothing could get to it. An audit in
September 2026 found **zero of four catalog entries able to start**: two npm
packages deprecated upstream, one that had never existed on npm at all, and one
needing a positional argument the catalog had no way to express. Underneath
that, connector writes were classified by a name denylist that let
`merge_pull_request` through as a read, and no agent could propose a write at
all — `parse_actions()` had no `mcp_action` arm and the prompt never mentioned
the tag.

Contract: [`development/mcp-contract.md`](development/mcp-contract.md).

- [x] **6.1 — Remote servers, as a first-class transport.** `transport="http"`
      reaches the vendor's own endpoint over HTTPS. Nothing is downloaded, no
      third-party code runs as the user, and there is no package to rot — the
      failure that emptied the catalog. OAuth 2.1 with Dynamic Client
      Registration means we still register no OAuth client and the consent
      screen still carries the vendor's name. Seven of the nine catalog entries
      are now remote; each endpoint was checked to exist and to answer an
      unauthenticated call with an OAuth challenge before being listed.
- [x] **6.2 — A catalog that resolves.** Plus `needs_args`, so a server that
      takes a folder can be given one.
- [x] **6.3 — Writes fail closed.** Evidence is required to call a tool a read.
- [x] **6.4 — An agent can propose a connector write.** The tag, the prompt, the
      server-side parser, the client-side parser and the card. `mcp_action`
      stays in `NEVER_UNATTENDED`.
- [x] **6.5 — Least privilege the user can set.** `allowed_tools` had no writer
      outside tests; it now has a screen and a `PATCH`.
- [x] **6.6 — Credentials in the Keychain.** `mcp_servers.json` is served to the
      page, so it holds names only. Legacy plaintext migrates on read.
- [x] **6.7 — Reachable at all.** The launch PATH a Dock-launched `.app` does
      not have, one bounded session per operation instead of two unbounded
      ones, and a tool list that survives a server the SDK will not model.
- [x] **6.8 — Add your own.** The catalog is what we vetted; it will never be
      all of it.

**Still open, deliberately:** `MCPConnector.sync()` ingests one page. MCP is an
ask/act surface — the line this phase draws — and bulk ingest belongs to the
deep connectors and `custom_api.py`, so paging is tracked rather than rushed.
