# Architecture

> **What this document is:** the architecture Chitragupta *has*, written down —
> who owns what, which direction dependencies are allowed to run, and where a
> responsibility currently sits in the wrong place.
>
> **What it is not:** a redesign. Everything described here is the code as it is
> today. Section 6 lists known deviations; each one is a note, not a plan.
>
> This is the **single normative** architecture document. `CLAUDE.md` states the
> rules an agent must follow; this file explains the structure those rules
> protect. Where they disagree, this file is describing and `CLAUDE.md` is
> instructing — fix whichever is stale.

---

## 1. What the system is

A local-first agent workspace. A team of agents share one on-device brain built
from the user's own connected sources, running on whatever model the user
already pays for. Everything stays on the machine: one FastAPI server bound to
loopback, one SQLite file for the raw index and another for curated facts, and
either a browser tab (`chitragupta serve`) or a native macOS window
(`chitragupta app`) in front of it.

Two consequences shape every boundary below:

- **The UI is served by the same process as the API.** Slow work in a handler
  does not just slow a request; it can freeze the window. Hence the lanes in §3.
- **Loopback is not a security boundary.** Every browser the user has open is
  also "on this machine". Hence the origin guard in §3.

---

## 2. Subsystem ownership

Each subsystem owns one responsibility. This table is the contract; §6 records
where today's code does not yet match it.

| subsystem | directory | owns | must not own |
|---|---|---|---|
| **storage** | `chitragupta/core/` | SQLite connections, schema and migrations, the memory table, vectors and embeddings, chunking, date parsing | interpreting what a memory *means*; anything provider- or UI-specific |
| **brain** | `chitragupta/brain/` | memories, semantic + lexical search, the entity/relation graph, enrichment, and the curated canonical claims in `brain/canonical/` | HTTP, model selection, connector scheduling |
| **agents** | `chitragupta/agents/` | agent execution, the tool loop, conversation memory, effort, delegation, planning, approvals and the unattended-action gate | which model to *offer*; how a provider authenticates |
| **models** | `chitragupta/models/` | providers, model discovery and entitlements, provider configuration, authentication flows, the vendor-CLI manager, the error taxonomy, streaming wire formats | brain content, agent policy, HTTP routing |
| **connectors** | `chitragupta/connectors/` | external integrations, ingestion, sync lifecycle and cancellation, the MCP client | how ingested text is scored or recalled |
| **api** | `chitragupta/api/` | the HTTP boundary: routing, request/response translation, the origin guard, the concurrency lanes, static assets | business logic of any kind; native window behaviour |
| **desktop** | `desktop.py`, `hud.py` | the native macOS window, port reservation, the sign-in HUD, vendor-login process reaping | HTTP routes, provider logic |
| **web** | `chitragupta/web/` | browser UI, presentation, browser-side state | anything the server can decide |
| *(workspace features)* | `actions.py`, `routines.py`, `tasks.py`, `reminders.py`, `scheduled.py`, `scheduler.py`, `usage.py`, `notify.py` | the user-facing productivity layer and the background sync loop | — see §6.3, these have no package of their own yet |
| *(leaf utilities)* | `config.py`, `log.py` | settings, paths, the secrets file, logging and `suppressed()` | everything else — these are imported by 45 modules each and must stay dependency-free |
| **character** | `/character` *(not in the Python package)* | the avatar renderer: the `character.scene` document format, its 3D-to-SVG projection, the follow-the-cursor loop, and the editor | anything about Chitragupta — it must stay liftable into its own repository |

**`/character` is outside the dependency graph entirely**, and that is the
point. It is a standalone, dependency-free JavaScript package with its own
README, tests and MIT licence, published into the app as a build artifact by
`scripts/sync-character.sh` and pinned by `tests/test_character_asset.py`.
Nothing in `chitragupta/` imports it; `web/` calls its global, and
`agents/avatars.py` stores its documents **without parsing them** — a second
copy of that schema in Python would be a second copy to keep current.

It now lives publicly at
**[github.com/suryansh2846code/character](https://github.com/suryansh2846code/character)**
(MIT, CI on Node 18/20/22). That repo is upstream; the directory here is a
vendored copy of the same files. There is deliberately no submodule and no npm
dependency: the app must build with no network and no toolchain beyond Python,
and a copy plus a test that proves it current is the cheapest honest way to get
that. When you change one, mirror it to the other in the same commit.

---

## 3. Dependency direction

Allowed direction, top to bottom. **An arrow may never be reversed.**

```
                 cli.py  ·  desktop.py → hud.py          entry points
                    │            │
                    ▼            ▼
                api/app.py  ──►  api/routes/{6}          HTTP boundary
                                   │
          ┌────────────┬───────────┼───────────┬──────────────┐
          ▼            ▼           ▼           ▼              ▼
       agents/      models/      brain/    connectors/   workspace features
          │            │           │           │          (actions, routines,
          │            │           │           │           tasks, scheduler…)
          └────────────┴─────┬─────┴───────────┘
                             ▼
                          core/                            storage
                             │
                             ▼
                   config.py · log.py                      leaf utilities
```

**The rules that follow from it:**

1. `core/` depends on nothing above it. It is the bottom.
2. `brain/`, `models/`, `connectors/` are **siblings and do not import each
   other**, with one sanctioned exception: a connector writes what it ingests
   through `brain.ingest()`.
3. `agents/` may depend on `brain/` and `models/` — it composes them. Neither
   may depend on `agents/`.
4. `api/` may depend on anything below it and **nothing may depend on `api/`**
   except the entry points.
5. `desktop/` sits beside `api/`, above everything else. **`api/` must not
   import it** (§6.1).
6. `web/` depends on the HTTP surface only, by relative path. It never learns a
   host or a port — the desktop app binds a different loopback port per install.

### Cross-cutting concerns that live at the boundary, deliberately

| concern | where | why there |
|---|---|---|
| origin guard | `api/security.py` | Compares `Origin` against the `Host` the request arrived on — *not* "is `Origin` loopback", which would let any local dev server call us. Pure function, so it is tested as one. |
| concurrency lanes | `api/concurrency.py` | Every handler is a plain `def` sharing one worker pool, and the UI is served by the same server. `@calls_a_model` and `@probes_a_provider` give slow work bounded lanes so a queued probe holds no thread. |
| failure visibility | `log.py::suppressed()` | `except Exception: pass` is correct at runtime and invisible afterwards. Every swallowed failure keeps its evidence in a rotating log. |
| secret redaction | `brain/canonical/redact.py` | Runs on the ingest path, *before* extraction — so a credential never reaches a model or the index. |

---

## 4. The contracts between subsystems

A change that spans two of these must name the contract first and land both
sides together, with a test that fails if only one ships.

| boundary | the contract names |
|---|---|
| web ↔ api | endpoint, request schema, response schema, error states, loading states, streaming behaviour. Pinned from the server side by `tests/api_surface.json` and from the client side by `tests/test_frontend_calls_real_endpoints.py`. |
| api ↔ agents | agent id, turn input, effort, event stream, `TurnResult`, errors |
| agents ↔ brain | recall context, memory writes, canonical learning, enrichment |
| agents ↔ models | model resolution, provider capabilities, streaming format, token accounting, auth state |
| models ↔ auth | `AuthFlow`: `start` / `status` / `code` / `cancel`, and the connection state each implies |
| connectors ↔ brain | source id, memory ingestion, metadata, sync lifecycle |
| brain ↔ storage | the `Memory` row, `RecallHit`, `RecallExplanation`, the append-only claim history |
| desktop ↔ web | `window.pywebview.api` — a native window is raised **by the page**, never by the backend (§5.4) |

**Additive first.** Ship the new thing beside the old, migrate the consumer,
then remove the old — three landings, never one. A rename landed on one side is
an outage on the other.

**Name a display string separately from an id.** If a field crosses a layer and
a person will read it, the contract says which field they read. The one contract
defect that reached the screen here was a row carrying an internal name and
nothing human, so the consumer rendered the id.

---

## 5. The five extension points

Adding a capability should mean registering it, never editing a chain of `if`s.

| to add a… | register | never |
|---|---|---|
| **data source** | a `Connector` subclass in `connectors/__init__.py::REGISTRY` | add a branch to the sync loop |
| **model provider** | a provider class + `ProviderCapabilities` + an `AuthFlow` in `models/auth_flows.py` | add a route, or a `providerId === "x"` branch in the UI |
| **agent tool** | a tool in `agents/tools.py`, and a case in `agents/evaluation.py` | widen the loop |
| **HTTP route** | a handler in the right `api/routes/` module, and a line in `api_surface.json` | put it in `api/app.py` |
| **native window behaviour** | `hud.py`, driven from the page | call it from a route (§5.4) |

### 5.1 Model availability is resolved per user, never hardcoded
First answer wins: **connection** → **the provider's own answer for this
account** → **static tier tables**. Shipped model lists are fallbacks flagged
`is_fallback=True`, which is exactly what tells the resolver it has no provider
answer. A stored model id is a *request*, re-checked against the live catalog on
every turn. Detail: the "Model availability" section of [`/CLAUDE.md`](../CLAUDE.md) and [`chitragupta/models/CLAUDE.md`](../chitragupta/models/CLAUDE.md).

### 5.2 Detection is not consent
Finding a CLI, a config file or a session on the machine means we may *offer*
it. No code path may promote a credential to connected, or a provider to
`ready`, because a binary happens to be installed.

### 5.3 Every subscription path is a vendor CLI
None of these providers exposes a subscription inference endpoint; each ships an
official headless CLI, and that is how a paid plan is reached. "Support
provider X's subscription" almost always means "shell out to X's CLI".

### 5.4 A native window is raised by the page, never by the API
Under `chitragupta app --dev` the backend is a separate uvicorn process with no
handle on the webview, so a backend-initiated window silently does nothing. The
frontend always runs inside the webview. Detail:
[`docs/DESKTOP-SIGNIN.md`](DESKTOP-SIGNIN.md).

---

## 6. Known deviations

Each of these is real today. None is urgent on its own; they are written down so
the next change does not deepen them. Evidence and cost:
[`COMPLEXITY_AUDIT.md`](../COMPLEXITY_AUDIT.md).

### 6.9 The connector sweep is serial · **OPEN**

`scheduler.py::_sync_all` walks connectors in one `for` loop, so a connector
that blocks for its timeout blocks every connector behind it. The *bound* now
exists — `connectors/limits.py` gives each connector a rate budget and a
concurrency lane, and `engine.run` holds one per request — but nothing yet runs
two connectors at once.

**Deliberately not changed in the landing that built the lanes.** Making the
sweep parallel means concurrent writers on the one `MemoryStore` connection
(`check_same_thread=False`, one handle, WAL). That is a change to the brain's
write path, not to the connector layer, and it needs its own landing with its
own measurements — shipping it inside a connector change would be exactly the
kind of entangled edit `/CLAUDE.md` tells an AUDIT not to make.

What holds in the meantime: every connector is bounded per request, a wedged
one is bounded by `CALL_TIMEOUT_SECONDS`, and a credential that is dead is
skipped rather than retried every thirty minutes.

**The fix**: give the sweep a bounded worker pool sized from
`Limits.concurrency`, after `core/store.py` can take concurrent writers — a
connection per thread, or a write queue.

### 6.1 ~~`api/` imports `desktop`~~ · **CLOSED 2026-09-25**

> Closed as prescribed: `api/desktop_bridge.py` is a three-function seam the
> desktop layer **attaches** itself to (`hud._attach_to_api`, called from
> `desktop.py`). `api/` now asks the bridge and never learns what answered.
>
> It was a genuine cycle, not just a reversed arrow — `desktop.py` imports
> `api.app` to serve the window — and it was invisible because all three
> imports sat inside function bodies.
>
> **Detached is the common case, not an error.** `chitragupta serve` is a
> browser tab with no native window, and the bridge's defaults say exactly that
> in the same shape the attached surface uses. A status endpoint that changes
> shape with how the app was launched is one the frontend has to branch on.
>
> `tests/test_desktop_bridge.py` walks the AST of `api/` rather than grepping —
> `from .. import desktop_bridge` contains the string `import desktop`, and a
> substring check calls the fix a violation. It also exercises the **attached**
> path, which nothing else could: every fallback is indistinguishable from the
> real value, so a bridge nobody wired up passes every existing sign-in test.

### 6.2 ~~`core/` imports `brain/`~~ · **CLOSED 2026-09-25**

> Closed as prescribed: `redact.py` moved to `core/`, where a rule about what
> may be written to the database belongs. `brain/canonical/redact.py` re-exports
> both names, so every existing import reads the same, and the redaction still
> runs on the ingest path before a credential can reach a model or the index —
> only the direction changed.
>
> This was the edge that made `brain · connectors · core` a cycle at package
> level. `PROSE_EXT` moved down to `core/chunk.py` at the same time, for the
> same reason: `brain` was importing a *sibling* to read it (§3 rule 2).

### 6.8 Three import cycles remain · **OPEN, and bounded**

Measured 2026-09-25, counting lazy imports as the real edges they are. **27
modules were in cycles; 11 are.** `tests/test_import_layering.py` pins the
remaining three as a closed list — a new cycle fails, and a listed one that
*grows* fails, which is the direction this rots in: `models/` reached fourteen
by absorbing one more module each time somebody needed a fact from it.

| cycle | modules | why it is still here |
|---|---|---|
| `agents.tools` · `library` · `connector_grants` · `browse_tools` · `message_tools` | 5 | `connector_grants` reads one **security-relevant** flag off a library template — `unrestricted_connectors`, which only Chief of Staff has — and `library` resolves the "everything" tool marker against `tools`. Moving where a template declares that flag changes how consent is expressed, which deserves more care than a cycle costs. |
| `connectors.mcp_auth` · `mcp_source` · `mcp_tools` | 3 | One subsystem split three ways: auth needs the server spec, the spec needs its tools, the tools need auth to call them. |
| `browser.chromium` · `session` · `signin` | 3 | A driver, the session it owns, and the sign-in flow that drives both. |

*Should become:* for the first, the flag published from a leaf both sides read,
so it is declared once and neither imports the other. The other two are single
subsystems whose internal split is the accident; they read as cycles because the
file boundaries do not match the concept boundaries.

### 6.3 Eight feature modules have no package
`actions.py`, `routines.py`, `tasks.py`, `reminders.py`, `scheduled.py`,
`scheduler.py`, `usage.py`, `notify.py` — 1,072 lines at the package root beside
`config.py` and `log.py`, which are leaf utilities. This is why the import graph
shows `root ↔ connectors`, `root ↔ agents` and `root ↔ brain` as cycles: "root"
is two unrelated things under one name.

**The `actions ↔ routines ↔ agents.approvals` cycle this entry named is closed**
(2026-09-25) — without the package. Three moves did it, and each is a rule:
the schedule vocabulary and the routine store went down to `core/` (**data below
every layer that reads it**); the action wording went to `action_phrasing.py`
(**one wording, below both the card that asks and the log that records**); and
`delegation`/`outcomes` re-enter the loop through `agents/entry.py` (**the
recursion is the design, the import was not**). The naming problem this entry
is really about is untouched.
*Should become:* a `workspace/` package for the productivity layer, leaving
`config`/`log` alone at the root.

### 6.4 ~~`models/` is one 14-module cycle~~ · **CLOSED 2026-09-25**

> **Zero import cycles**, pinned by `tests/test_models_layering.py`. The
> prescribed fix — "a leaf module holding provider ids and plan tiers" — was
> most of it; two more edges needed the same treatment. The layering is now:
>
> ```
> errors · base · streaming · capabilities · cache   nothing above them
> entitlement_rules                                  pure policy, stdlib only
> claude_cli                                         where the CLI is
> accounts · connection_state                        what the user has
> discovery                                          the catalog, decorated
> entitlements                                       choosing a model to run
> registry                                           everything
> ```
>
> Five moves, each a rule rather than a cut: `errors` took a supplier hook
> instead of importing `discovery` (**the floor may only touch modules that
> touch nothing**); `StreamEvent` moved to `base` (**a protocol type belongs
> with the protocol**); `chatgpt_auth` calls `cache.credentials_changed()`
> (**an auth module reports to a leaf**); `entitlements` split into pure policy,
> detection and choosing (**a provider re-checking a model must not drag the
> catalog up behind it**); `find_claude` moved to `claude_cli` (**detection sits
> below the provider that uses it**).
>
> Every moved name is re-exported and declared in `__all__`, and
> `test_no_unused_imports` now honours `__all__` rather than exempting
> `__init__.py` — a stricter check, not a looser one.

### 6.7 `brain/` imports `connectors/` · **OPEN**
`brain/brain.py` reaches sideways into `connectors` twice: `_maybe_fetch` live-
searches Gmail or Drive when recall misses, and `_escape_hatch` starts a full
archive sync when the user asks for one. §3 rule 2 sanctions the *opposite*
direction only — a connector writing through `brain.ingest()` — so this is a
violation that was never written down, because it lives inside function bodies
like the others did.

Both are real features on the recall path, and both hardcode `"gmail"` /
`"gdrive"` routing inside `brain`. **Not fixed here deliberately:** moving them
is a redesign of what recall is allowed to do, which is a product decision, not
a defect fix — and the recall path carries the invariant that a curation failure
can never break plain retrieval.
*Should become:* on-demand fetch owned by the layer above both, with `brain`
reporting a miss rather than resolving it.

### 6.5 Responsibilities sitting in the wrong subsystem
| what | where it is | where it belongs |
|---|---|---|
| the 8-factor recall scoring model | inlined in `core/store.py::search` | its own module beside the store — it is the brain's ranking policy, not persistence |
| recall prompt-text assembly | inlined in `brain/brain.py::recall` | a presentation seam in `brain/` — a one-character change there silently alters what every model sees |
| model resolution + prompt assembly | inlined in `agents/runtime.py::run_turn` | named functions in the same module; the tool loop is only 25% of the function named for it |
| `/api/usage` | `api/routes/providers.py` | token accounting is its own subject |
| the HUD bridge, `fs/browse`, page serving | `api/routes/workspace.py` | a five-concern grab-bag; see §6.1 |

### 6.6 ~~`web/app.js` is one 3,581-line file~~ · **CLOSED**

> **Closed 2026-09-14.** `app.js` is 208 lines; the frontend is eleven plain
> scripts, each with one responsibility, listed in
> [`chitragupta/web/CLAUDE.md`](../chitragupta/web/CLAUDE.md). `index.html` declares
> the load order and is the only place it is written down — the browser and the
> harnesses both read it from there.
>
> The blocker was never the code. The harnesses evaluate the frontend with
> `new Function`, which compiles a script and cannot process `import`, so the
> loader had to change first and land as a proven no-op before a line moved.
> Method, and the five checks to run before any further move:
> [`docs/development/frontend-testing.md`](development/frontend-testing.md).

---

## 7. Invariants

Load-bearing. Each was bought by a shipped bug. Violating one is a regression
even when every test is green.

| # | invariant | subsystem |
|---|---|---|
| I1 | The origin guard compares `Origin` to the `Host` it arrived on. Never "is `Origin` loopback". | api |
| I2 | `/api/open-browser` accepts `http(s)` only — otherwise it hands the system opener any scheme an installed app registered. | api |
| I3 | Slow handlers run in a bounded lane and are `async`, so a queued one holds no worker thread. | api |
| I4 | The webview origin is user state. A launch on a different port loses localStorage — the saved port is bound with `SO_REUSEADDR`. | desktop |
| I5 | Showing the sign-in HUD must not activate the app; every Cocoa mutation goes through `AppHelper.callAfter`. | desktop |
| I6 | Every login process we spawn is recorded **to disk** and reaped on launch and quit. Only recorded PIDs are signalled, each re-checked against the command line recorded with it. | desktop / models |
| I7 | Anything the user starts, they can cancel — sign-in, sync, enrichment. A close button that leaves work running is a lie. | all |
| I8 | Every identifier sent to a vendor is that vendor's or ours, never a third party's. | models |
| I9 | A `chat()` returns a `ChatResult`, never raises. Provider errors are classified and translated, never dumped. | models |
| I10 | A stored model id is re-checked before use; a discovery failure never blocks a turn. | models |
| I11 | `"key" in row` on a `sqlite3.Row` tests the **values**. Use `row.keys()`. `SIM118`/`SIM401` are disabled for this reason. | storage |
| I12 | Claims are append-only: a change supersedes, never `UPDATE`s. Inferred never supersedes confirmed. | brain |
| I13 | Recall order is canonical facts → graph → source excerpts, and a curation failure can never break plain retrieval. | brain |
| I14 | Graph enrichment is content-based. Never gated on a connector-name allowlist. | brain |
| I15 | A routine pre-authorises the routine, not the stranger who wrote the email it read. Outbound actions need an explicitly permitted recipient. | agents |
| I16 | Streaming is a callback on the same loop, never a second loop. | agents |
| I17 | Delegation guards live in a `ContextVar`; one `copy_context()` **per call**, and the chain is left on every exit path. | agents |
| I18 | Any `innerHTML` path escapes *before* applying inline markdown, the way `md()` does. | web |
| I19 | The frontend uses relative paths only. Never a host or port. | web |
| I20 | UI is derived from capabilities, never from a `providerId === "x"` chain. | web |
| I21 | Connectors are read-only; the one path that changes something at a vendor requires an explicit `confirmed`, so a caller that forgets it fails closed. | connectors |

---

## 8. Where to read more

| subject | document |
|---|---|
| rules an agent must follow | [`/CLAUDE.md`](../CLAUDE.md) + the note in each code directory |
| measured complexity, with evidence | [`COMPLEXITY_AUDIT.md`](../COMPLEXITY_AUDIT.md) |
| why a decision was made | [`DECISIONS.md`](DECISIONS.md) |
| the agent loop in depth | [`AGENTS.md`](AGENTS.md) |
| the brain's data model | [`BRAIN-V1.5.md`](BRAIN-V1.5.md) |
| the macOS window and sign-in HUD | [`DESKTOP-SIGNIN.md`](DESKTOP-SIGNIN.md) |
| recall cost at scale | [`SCALING.md`](SCALING.md) |
| connectors | [`CONNECTORS.md`](CONNECTORS.md) |
| building and shipping the app | [`DISTRIBUTION.md`](DISTRIBUTION.md) |
| known gaps | [`AUDIT.md`](AUDIT.md) |
