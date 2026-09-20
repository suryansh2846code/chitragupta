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

### 6.1 `api/` imports `desktop`
`api/routes/workspace.py` (×2) and `api/routes/providers.py` (×1) do
`from ... import hud`. The HTTP layer reaching into the window layer inverts
rule 5 of §3. Currently masked by being a function-level import.
*Should become:* a small bridge the desktop layer registers with the API, or the
HUD routes moving to a desktop-owned router.

### 6.2 `core/` imports `brain/`
`core/store.py` → `brain.canonical.redact`. The redaction belongs on the ingest
path and must stay there; the *direction* is the deviation.
*Should become:* redaction moves down into `core/`, which is where a rule about
what may be written to the database belongs.

### 6.3 Eight feature modules have no package
`actions.py`, `routines.py`, `tasks.py`, `reminders.py`, `scheduled.py`,
`scheduler.py`, `usage.py`, `notify.py` — 1,072 lines at the package root beside
`config.py` and `log.py`, which are leaf utilities. This is why the import graph
shows `root ↔ connectors`, `root ↔ agents` and `root ↔ brain` as cycles: "root"
is two unrelated things under one name. `actions ↔ routines ↔ agents.approvals`
is a genuine cycle inside it.
*Should become:* a `workspace/` package for the productivity layer, leaving
`config`/`log` alone at the root.

### 6.4 `models/` is one 14-module cycle
`accounts · anthropic · chatgpt_auth · claude_code · cursor · deepseek ·
discovery · entitlements · errors · gemini · grok_cli · openai_compat ·
registry · xai` form a single strongly-connected component. There are no
module-level cycles only because those edges live inside function bodies.
*Should become:* a leaf module holding provider ids and plan tiers that
`entitlements`, `discovery` and `errors` can all import without a back-edge.
**This is the largest structural item in the codebase and the riskiest to
change.** Not scheduled.

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
