# Complexity Audit — Chitragupta

> **MODE: AUDIT.** Read-only. No source file was modified to produce this report.
> Measured against `4ba07fa` (main, clean tree) on 2026-09-13.
>
> **Baseline reproduced before writing anything:**
> `pytest` → **1425 passed, 18 skipped in 62.1s** · `ruff check chitragupta tests` → clean ·
> `mypy chitragupta` → clean over 113 files · `node --check chitragupta/web/app.js` → clean.
>
> Every number below came from a command run against this tree, not from the docs.

---

## Status — what has been done since (2026-09-14)

This report is a **dated snapshot**, kept as written so the evidence stays
readable. What it found has largely been acted on:

| § | finding | status |
|---|---|---|
| D.1 | CLI login state machine duplicated verbatim | **done** — one `models/cli_login.py`, 147 lines deleted |
| D.2 | `_augmented_path()` in three files | **done** — one implementation; the three `_EXTRA_BIN_DIRS` lists deliberately *not* merged, see below |
| F.1 | `app.js` is 3,581 lines | **done** — eleven scripts, `app.js` is the 237-line shell |
| F.2 | the harnesses can only load one file | **done** — `tests/js/_app_source.mjs`, order read from `index.html` |
| F.4 | the dead delete button | **done** — endpoint added, plus a contract test for all 67 frontend calls |
| G.1 | `CLAUDE.md` is 742 lines / 49.5 KB | **done** — 294 lines / 15 KB, nothing lost |
| G.2 | architecture described in four places | **done** — `docs/ARCHITECTURE.md` is normative |
| H.3 | no coverage measurement | **done** — runs in CI, 76%, no threshold |
| — | CI was red | **done** — two missing extras; also recovered 20 silently-skipping macOS tests |

**C.1 and C.2 closed 2026-09-25.** `models/` is now a **DAG — zero import
cycles**, down from one strongly-connected component of fourteen modules.
Pinned by `tests/test_models_layering.py`, which counts lazy imports as the real
edges they are; a lint rule cannot see them, which is exactly how the knot
survived this long.

Five moves did it, each establishing a rule rather than just cutting an edge:

| move | rule it establishes |
|---|---|
| `errors` took a supplier hook instead of importing `discovery` | **`errors` is the floor** — every provider imports it at module level, so it may only touch modules that touch nothing |
| `StreamEvent` moved from `streaming` to `base` | **a protocol type belongs with the protocol**; it made `base` import the wire parsers to implement its own default |
| `chatgpt_auth` calls `cache.credentials_changed()` | **an auth module reports to a leaf**; whoever holds derived state has already registered a clearer |
| `entitlements` split into `entitlement_rules` (pure) + `connection_state` (detection) + itself (choosing) | **policy is pure and sits at the bottom**, so a provider re-checking a model — which it must — does not drag the catalog up behind it |
| `find_claude` moved to `claude_cli` | **detection sits below the provider that uses it**, so `accounts` and `claude_code` stop importing each other |

Every existing import path still works: the moved names are re-exported and
declared in `__all__`, and `test_no_unused_imports` now honours `__all__` rather
than exempting `__init__.py` — a stricter check, not a looser one.

**Still open, and deliberately so:**

| § | finding | why it was left |
|---|---|---|
| C.3 | eight feature modules own no package | wide import churn for a naming win |
| C.4 | `api/` imports `desktop` | needs a bridge; two subsystems |
| C.5 | `core/` imports `brain/` | one import, low value alone |
| B.1–B.3 | `run_turn`, `store.search`, `brain.recall` | all well covered (92%, 86%, 69%); readability, not risk |

**One finding here was wrong.** D.2 implied the three `_EXTRA_BIN_DIRS` lists
should merge. They should not: each also drives the fallback binary scan in its
own `find_*` function, so unifying them would send Cursor's finder through
`~/.grok/bin`. Only the six lines that consume the lists were shared.

---

## Scale

| | |
|---|---|
| Python | 23,793 lines · 113 modules · 1,085 functions |
| Frontend | 6,586 lines (`app.js` 3,581 · `styles.css` 1,654 · 3 HTML pages) |
| Tests | 15,453 lines · 843 test functions · 72 files + 10 executed JS harnesses |
| HTTP surface | 121 endpoints, all pinned in `tests/api_surface.json` |
| Docs | 16 files in `docs/` (3,981 lines) + root `CLAUDE.md` (742 lines) + 11 directory notes |
| History | 239 commits, one author, 2026-08-28 → 2026-09-13 |

**This codebase is not in bad shape.** Zero `TODO`/`FIXME` markers in 30k lines,
three genuinely dead symbols, a full green gate, and a test suite that executes
the frontend rather than parsing it. The complexity here is **structural, not
rot**: it is concentrated in six places, and it is the kind that makes the *next*
change expensive rather than the current code wrong.

---

# A. Current architecture

## A.1 The real dependency graph

```
                    ┌──────────── cli.py ─────────── desktop.py ── hud.py
                    │                   │                │
                    ▼                   ▼                ▼
              api/app.py ──────── api/routes/{6} ────────┘   (api → desktop: violation)
                    │                   │
      ┌─────────────┼───────────┬───────┴────────┬──────────────┐
      ▼             ▼           ▼                ▼              ▼
   agents/       models/     brain/         connectors/   scheduler.py
      │             │           │                │         actions.py
      │             │           ▼                │         routines.py
      │             │      brain/canonical/      │         tasks.py
      │             │           │                │         reminders.py
      │             │           ▼                │         scheduled.py
      └─────────────┴──────► core/ ◄─────────────┘         usage.py · notify.py
                         (store, db, embeddings)            ── the ownerless layer

                     config.py · log.py   ← leaf utilities (fan-in 45 each)
```

Measured cross-subsystem import edges, heaviest first:

```
api        -> models       42      agents     -> root        23
api        -> root         34      connectors -> root        13
models     -> root         29      root       -> connectors   9
api        -> agents       27      connectors -> brain        9
api        -> connectors   17      root       -> brain        9
```

## A.2 What each subsystem actually owns today

| subsystem | owns | also owns (it shouldn't) |
|---|---|---|
| `api/` | HTTP surface, origin guard, concurrency lanes | page serving, the HUD bridge, `fs/browse`, and `/api/usage` |
| `models/` | providers, auth flows, entitlements, discovery, CLI manager, error taxonomy, streaming | — (but internally knotted, see C.1) |
| `brain/` | memories, graph, canonical claims, enrichment | **prompt-context string assembly** (see B.3) |
| `core/` | SQLite store, schema/migrations, embeddings, chunking, date parsing | **the entire 8-factor recall scoring model** (see B.2) |
| `connectors/` | one class per source, sync lifecycle, MCP client | — **cleanest subsystem in the repo** |
| `agents/` | turn loop, tools, effort, delegation, planning, approvals, permissions | model resolution, prompt assembly, canonical learning (see B.1) |
| `web/` | the whole UI | — in **one 3,581-line file** (see F) |
| *(package root)* | config, logging | **and 8 feature modules with no owning subsystem** (see C.3) |
| *(no `desktop/` package)* | `desktop.py` + `hud.py` sit loose at the root | |

## A.3 Contracts that are already good and must not be disturbed

These are load-bearing and the refactor should treat them as fixed points:

- `api/security.py::refusal()` — pure function, tested as a function.
- `api/concurrency.py` — two named lanes, `@calls_a_model` / `@probes_a_provider`.
- `models/auth_flows.py::AuthFlow` — `start`/`status`/`code`/`cancel`, one seam per provider.
- `connectors/base.py::Connector.each_guarded(items, result, ingest, …)` — the per-source contract.
- `agents/effort.py::Effort` — one gear selector deriving every budget.
- `tests/api_surface.json` — 121 paths pinned.
- `web/app.js::md()` — **escape-before-markdown**; `esc(src)` runs first, all inline
  markup is applied to already-escaped text. This invariant is why a model echoing
  a hostile email cannot inject HTML. It must survive every move, byte-for-byte.

---

# B. Complexity hotspots

Ranked by (size × branch count × how often a change lands there).

## B.1 — `agents/runtime.py::run_turn` — 237 lines, ~41 branches · **P1**

One function performing **five** sequential jobs, with no seam between them:

| lines (approx) | job |
|---|---|
| 246–270 | model resolution + repairing a stale agent binding |
| 271–345 | prompt assembly — identity, date grounding, recall injection, task grounding, history |
| 346–360 | runner/budget/context-var setup |
| 365–440 | **the actual tool loop** |
| 445–478 | persistence, auto-learn, canonical curation, result shaping |

The loop — the part the module is named for — is 25% of the function. Changing
the prompt means reading the loop; changing the loop means reading the prompt.

**Why it matters:** this is the single highest-traffic function in the codebase.
Every agent feature lands here.

## B.2 — `core/store.py::search` — 208 lines, ~51 branches · **P1**

One function doing query interpretation → date filtering → vector matmul → **eight
independent scoring factors inlined in a single loop** → explanation construction
→ sort → access recording.

The eight factors (semantic, lexical, importance, confidence, reinforcement,
source, recency, temporal, minus penalties) are pure arithmetic over one row.
They are the documented throughput ceiling (`docs/SCALING.md`: 50k memories ≈
2.5s per turn, 95% in this loop) and they are **not independently testable today**
— you can only observe them through a full `search()` call.

**Observation, not a proposed change:** line ~772 calls `self.get(mid)` *inside*
the candidate loop — an N+1 query for every row that passes `min_score` (default
`0.0`). That is very likely the real cost behind the documented linear ceiling.
Fixing it is a **performance** change, out of scope for this task; extracting the
scoring makes it measurable, which is the point.

## B.3 — `brain/brain.py::recall` — 149 lines, ~54 branches (highest in the repo) · **P1**

Mixes four responsibilities:
1. **query interpretation** — date range, "overview" intent, open-loop status intent;
2. **retrieval** — `store.search`, graph match, open loops, canonical block;
3. **budgeting** — compact vs full blocks, character budget, truncation;
4. **prompt text assembly** — the ~40-line string that is injected into every turn.

(4) is presentation. It lives in the brain subsystem because that is where it was
written, not because the brain owns it. It is also where a one-character change
silently alters what every model sees, with no test that reads the rendered block
in isolation.

## B.4 — `web/app.js::renderProviderConnectBox` — 520 lines, one function · **P1**

Lines 502–1021. Renders the entire provider card: account state, sign-in button,
CLI install, API-key toggle, plan badge, usage meter, error surface. It is the
function the two blindest bugs in this repo's history lived in (a TDZ
`ReferenceError` that blanked the drawer; a card written into a detached
container). It is covered by `render_provider_box.mjs` and `click_signin.mjs`
— which is the only reason it is survivable.

## B.5 — `models/chatgpt_auth.py` — 902 lines · **P2**

One module holding: PKCE OAuth, a **live HTTP callback server** (`_OAuthCallbackHandler.do_GET`,
149 lines), token refresh, plan-tier reading, subscription usage, *and* the Codex
responses inference path (`chat_with_chatgpt_subscription`, 152 lines / 38 branches).
Auth and inference are two subsystems' worth of work in one file.

**RED zone** — touching it is provider-authentication redesign. Listed for
completeness; I am not proposing to change it.

## B.6 — `models/accounts.py::connect_local_account` — 109 lines, 37 branches · **P2**

A per-provider `if/elif` ladder that duplicates what `auth_flows.py` was built to
unify. Every new provider adds a branch here *and* a flow there.

**Other functions over 100 lines** (context, not all proposed for change):
`evaluation.py::run` (200), `store.py::add` (137), `brain.py::enrich` (122),
`gdrive.py::sync` (100).

---

# C. Dependency / ownership problems

## C.1 — A 14-module cycle in `models/` · **the worst structural knot in the repo**

Counting function-level imports as the real edges they are, these 14 modules form
**one strongly-connected component** — none can be read, tested, or reasoned about
without the other thirteen:

```
accounts · anthropic · chatgpt_auth · claude_code · cursor · deepseek · discovery
entitlements · errors · gemini · grok_cli · openai_compat · registry · xai
```

The load-bearing back-edges:

```
entitlements -> discovery     and  discovery -> entitlements
entitlements -> chatgpt_auth  and  chatgpt_auth -> entitlements
entitlements -> claude_code   and  claude_code -> entitlements
errors       -> discovery     and  discovery -> ... -> errors
registry     -> entitlements  and  entitlements -> ... -> registry
```

There are **no module-level cycles** — `ruff` is clean and imports resolve —
*because every one of these edges was pushed inside a function body.*

## C.2 — 199 function-level imports across the package

```
api/routes/agents.py     13 lazy imports     agents/runtime.py     4
api/routes/providers.py  12 lazy imports     brain/brain.py        6
api/routes/workspace.py  10 lazy imports     scheduler.py          9
agents/evaluation.py      8 lazy imports     models/entitlements.py 9
```

Some are legitimate (keeping an optional connector SDK optional, deferring a 10s
embedder load). Most are cycle-avoidance. The cost is concrete:

- the dependency graph is invisible to `ruff`, `mypy`, and to a reader;
- an agent editing `entitlements.py` cannot see from the file header what depends on it;
- import cost is paid per call, not once.

## C.3 — Eight feature modules own no subsystem

`actions.py` · `routines.py` · `tasks.py` · `reminders.py` · `scheduled.py` ·
`scheduler.py` · `usage.py` · `notify.py` — 1,072 lines at the package root,
next to `config.py` and `log.py`, which are leaf utilities with fan-in 45.

This is why the import graph shows `root <-> connectors`, `root <-> agents`,
`root <-> brain` as apparent cycles: "root" is two unrelated things wearing one name.
`actions ↔ routines ↔ agents.approvals` is a **genuine 3-module cycle** inside it.

## C.4 — `api/` imports `desktop`

`api/routes/workspace.py` (×2) and `api/routes/providers.py` (×1) do
`from ... import hud`. The HTTP layer reaching into the native window layer
inverts the intended direction. It is currently *masked* by being a lazy import.

`desktop.py` and `hud.py` also sit loose at the package root — there is no
`chitragupta/desktop/` package, despite that being the natural boundary.

## C.5 — `core/` imports `brain/`

`core/store.py` → `brain.canonical.redact`. The persistence layer depends on a
brain-domain module. The redaction is correct and must stay on the ingest path;
the *direction* is the problem.

## C.6 — `api/routes/workspace.py` is a five-concern grab-bag

18 routes covering: the desktop/HUD bridge (`/api/hud/note`, `/api/hud/diagnostics`,
`/signin-hud`, `/api/open-browser`), action execution, routines/reminders/tasks,
filesystem browsing, and page serving (`/`, `/onboarding`). Its 10 lazy imports
reach 10 different modules.

---

# D. Duplication

## D.1 — The CLI login state machine, copied verbatim · **P0, highest value/risk ratio**

`models/cursor.py:158–245` and `models/grok_cli.py:165–252` are the **same ~90
lines**. A literal `diff` of the two ranges differs only in: the status function
called, the binary finder called, the login argv (`["login"]` vs `["login","--oauth"]`),
and three brand strings.

Duplicated identically in both: the `_login_proc` module global, `reset_login_state()`,
`reset_auth_cache()`, `login_progress()`, `_still_running()`, `cancel_cli_login()`,
and the body of `start_*_cli_login()`.

**This is the file where "158 orphaned `claude auth login` processes" was fixed.**
A future fix to process reaping has to be made twice, and the second copy is the
one that gets missed.

## D.2 — `_augmented_path()` is byte-identical in three files

`models/cursor.py:54`, `models/grok_cli.py:59`, `models/claude_code.py:40` —
same 6-line body, three copies, three different `_EXTRA_BIN_DIRS` lists that
overlap but disagree (`claude_code` has `~/.npm-global/bin` and `/opt/homebrew/sbin`;
`cursor` has neither).

## D.3 — Provider-id aliasing: one Python implementation, four JS re-implementations that disagree

Python has the single source of truth:

```python
# models/discovery.py:25
_PROVIDER_ALIASES = {"anthropic": "claude", "google": "gemini", "grok": "xai"}
def normalize_provider_id(provider_id: str) -> str: ...
```

`app.js` re-derives it inline at four sites — **and they have already drifted**:

| site | handles |
|---|---|
| `app.js:1041` | `anthropic`→`claude`, `claude-code`→`claude`, `google`→`gemini`, `grok`→`xai` |
| `app.js:1181` | `anthropic`/`claude-code`→`claude` **only** |
| `app.js:1289` | `anthropic`/`claude-code`→`claude` **only** |
| `app.js:1564` | `anthropic`/`claude-code`→`claude` **only** |

## D.4 — `isProviderConnected` contains the same 5 lines twice

`app.js:1048–1054` and `app.js:1059–1065` differ only in which array is searched
(`MODEL_CATALOG` vs `PROVIDERS`). And this is the **third** independent reading of
`connection_status` in the file — `renderProviderConnectBox:511/523` and
`formatProviderPlanInfo:1315` each interpret it their own way.

## D.5 — A 7-way provider-id ternary for `key_env`

`app.js:525` is a single expression chaining `providerId === "openai" ? … : "anthropic" ? …`
through seven providers, behind a `caps.key_env` fallback. This is exactly the
`providerId === "x"` chain the project's own doctrine forbids, and the
capability field that should replace it already exists in the payload.

## D.6 — Root `CLAUDE.md` duplicates the directory notes it spawned

The 11 directory `CLAUDE.md` files are **better written than the root file's
versions of the same rules** — they are already the distilled form. Verbatim or
near-verbatim overlaps:

| rule | root CLAUDE.md | also in |
|---|---|---|
| `"key" in row` on `sqlite3.Row` | §Layout | `brain/CLAUDE.md` |
| `store.search()` cost table | §Layout | `core/CLAUDE.md` (same numbers) |
| Detection is not consent | §Model availability | `models/CLAUDE.md` |
| A test must never start a real sign-in | §Verification | `tests/CLAUDE.md` (same 158-process story) |
| `build-dmg.sh` vs `build-macos-app.sh` | §Run it | `packaging/CLAUDE.md` (same 189MB→74MB) |
| escape-before-markdown | §The Models drawer | `web/CLAUDE.md` |
| capability-derived UI | §Model availability | `web/CLAUDE.md` |
| desktop Spaces / window level | §This is a product | `docs/DESKTOP-SIGNIN.md` (full record) |

The root file's own rule is *"No rule is written in two places — a duplicated
rule drifts, and the copy that drifts is always the one you read."*

## D.7 — What is **not** duplicated (verified, do not "fix")

`connectors/` has nine sources that each call `get_brain()` and define a local
`ingest(item) -> int` closure passed to `each_guarded`. This reads like
duplication and is not: the shared part is already factored into
`base.py::each_guarded`, and what differs is the per-source item→text rendering,
which is irreducibly per-source. **Leave it alone.**

---

# E. Dead / unnecessary code

Scanned every module-level definition against the whole repo (source + tests +
scripts + JS harnesses). **89 apparent hits were all FastAPI route handlers and
Typer commands** referenced by decorator. Genuine dead code is:

| symbol | location | verdict |
|---|---|---|
| `MUTATING_METHODS` | `api/security.py:53` | **Dead, and actively misleading.** Its comment says GETs are Host-checked only and mutating methods *additionally* require an `Origin`. `refusal()` never reads it — the Origin check applies to **all** methods. The code is *stricter* than the constant claims, so this is safe, but a reader trusts the constant. |
| `CREDENTIAL_KINDS` | `models/connections.py:47` | Dead tuple. |
| `_PREF_PATTERNS` | `brain/contradiction.py:13` | Dead; residue of an earlier heuristic. |
| `_Bridge.close_hud` / `_Bridge.focus_main` | `hud.py:268,285` | **NOT dead** — called from `signin_hud.html:209,223` via `window.pywebview.api`. Invisible to Python tooling. Worth a comment. |
| `_OAuthCallbackHandler.do_GET` / `log_message` | `chatgpt_auth.py` | **NOT dead** — `BaseHTTPRequestHandler` overrides. |

**No dead code in `app.js`** — all 102 functions are reachable.

### Unnecessary abstraction: none found
There are no speculative ABCs, no one-implementation interfaces, no pattern
scaffolding. `LLMProvider`, `Connector`, and `AuthFlow` each have 5+ real
implementations. This is a genuine strength — do not add layers.

---

# F. Frontend complexity

## F.1 — `app.js` is 3,581 lines with **zero section banners** and 37 module-level globals

Twelve coherent clusters are already present in the file, in order, with no
markers separating them:

```
 lines    range        cluster
   115    1–115        core utils ($, api, esc, md, orbs, icons, toast)
    43    116–158      agent rail
   863    159–1021     providers + sign-in (HUD, CLI instructions, renderProviderConnectBox)
   792    1022–1813    model picker (composer flyouts, agent matrix, loadProviders)
   393    1814–2206    chat (send / stream / messages / actions / trace)
   207    2207–2413    brain panel + google card + sync
   286    2414–2699    connectors (help, catalog, custom apps, permissions)
   231    2700–2930    workspace (approvals, tasks, reminders, routines, file picker)
   132    2931–3062    onboarding / welcome / lead agent + brain status pill
   145    3063–3207    brain screen canvas visualisation
   127    3208–3334    usage + enrichment
   247    3335–3581    drawers + boot wiring
```

Two clusters are **46% of the file**: providers/sign-in (863) and model picker (792).

## F.2 — **The hard constraint on any frontend split** ⚠️

All nine `tests/js/*.mjs` harnesses evaluate `app.js` like this:

```js
const src = fs.readFileSync(APP_JS, "utf8");
new Function(src + "\nglobalThis.__render = renderProviderConnectBox;")();
```

They take **one file path as `argv[2]`**, read it whole, and pull named functions
out of its closure. This has three consequences that govern Phase 3:

1. **ES modules are impossible.** `new Function` cannot evaluate `import`/`export`.
   Converting `app.js` to `type="module"` breaks all 9 harnesses and the ~6 pytest
   files that drive them. The user's instruction not to introduce a framework and
   my constraint not to weaken tests point the same way.
2. **The only viable split is multiple classic `<script src>` tags** sharing one
   global scope, concatenated in load order — semantically identical to today.
3. **Every harness must learn to read N files in that order.** The clean way is a
   shared `tests/js/_app_source.mjs` that derives the order from `index.html`'s
   `<script>` tags, so adding a future module never touches a harness again.
   That is a 9-harness + 1-helper + 1-HTML change: **YELLOW, needs approval.**

This constraint is not documented anywhere in the repo. It is the single most
important thing to know before touching the frontend.

## F.3 — `index.html` has no inline `on*` handlers (0 found) and `app.js` assigns nothing to `window`

Good news: the split has no hidden coupling through the HTML. Every handler is
wired in JS. Ordering is the only contract.

## F.4 — Other frontend issues

- **A dead control that says nothing.** `app.js:2406` calls
  `DELETE /api/memories/${id}`. No such route exists (121 endpoints, 8 DELETEs,
  none matching). `api()` rejects on non-ok and the handler has no `catch`, so the
  click is an unhandled promise rejection: nothing deletes, nothing is said.
  *(This is the repo's own `docs/AUDIT.md` → A11, still open. A **bug fix**, not a
  refactor — flagged here, not bundled into any refactor step.)*
- **`onboarding.html` has zero `aria`/`role` attributes** (`index.html` has 41).
  Out of scope; noted so it is not lost.

---

# G. Documentation complexity

## G.1 — Root `CLAUDE.md`: 742 lines / 49.5 KB ≈ **~13k tokens loaded into every agent session before any work begins**

Two sections are 53% of it:

| section | lines | what it actually is |
|---|---|---|
| "This is a product, not a developer tool" | **179** | ~15 lines of doctrine + ~165 lines of *implementation war stories*: macOS window levels, cache measurements, the `/auth/status` freeze, 158 orphaned processes, port reservation |
| "Model availability is resolved per user" | **215** | a complete architecture spec for `models/` — authority order, per-provider subscription table, CLI invocations, capability rules |
| "Layout" | 99 | a file-tree walkthrough that drifts every time a file moves |
| everything else | 249 | |

The war stories are **valuable and must not be lost** — they are the evidence
behind the rules. They are simply not operating instructions, which is what a
file loaded into every session should be.

## G.2 — Architecture is currently described in four places

`CLAUDE.md §Layout` · `docs/PROJECT.md §4 Architecture` · `docs/DECISIONS.md §Architecture` ·
the 11 directory `CLAUDE.md` files.

**Risk for Phase 2:** adding `docs/ARCHITECTURE.md` naively creates a *fifth*.
It must be the single normative one, with the others reduced to pointers.

## G.3 — The directory notes are the model to copy

`core/CLAUDE.md` is 11 lines and carries the entire recall-cost story with numbers
and a pointer to `docs/SCALING.md`. That is the target shape for the root file.

---

# H. Risk assessment

## H.1 — Where a refactor could silently change behavior

| # | area | the trap |
|---|---|---|
| R1 | **`app.js` load order** | Script order *is* the dependency graph. `md()`/`esc()` are used ~119 times; `_lessMotion` is a hoisted `function` **on purpose** because `_bsDraw` calls it from far above. Moving a `const` arrow across a boundary creates a TDZ `ReferenceError` that `node --check` passes and that blanks a screen. |
| R2 | **escape-before-markdown** | `md()` does `esc(src)` first, then applies inline markup to escaped text. Any reordering, or applying `mdInline` to raw input, is an XSS hole. The link regex is safe *only because* quotes are already `&quot;`. |
| R3 | **FastAPI route registration order** | A literal path registered after a parameterised one that matches is unreachable. `/api/brain/canonical/review` sits before `/api/brain/canonical/{section}` **in the same module for that reason**. Never move a route across modules. |
| R4 | **`resolve_usable_model` position in `run_turn`** | It runs *before* `get_provider` and repairs a stale agent binding as a side effect. Extracting it must preserve both the order and the side effect. |
| R5 | **`delegation.enter/leave` around the loop** | The `ContextVar` is released in a `finally`. Any extraction that moves code across that boundary can leak an agent id into the next turn. |
| R6 | **Scoring arithmetic in `store.search`** | Factor *order* does not matter (all summed) but **short-circuit order does**: `min_score` filtering, `date_ids` filtering and `source` filtering happen *after* scoring and before `self.get()`. Reordering changes which memories are returned. |
| R7 | **`suppressed()` swallowing a refactor's own error** | 71 call sites. A moved import inside one becomes a silent no-op instead of a crash — a broken refactor that still passes. |
| R8 | **`tests/api_surface.json`** | 121 paths. Any route move shows up here immediately — which is protection, not a risk, as long as it is never re-baselined. |
| R9 | **`models/` 14-module cycle** | Any attempt to promote a lazy import to module level in that cluster produces an `ImportError` at startup. Breaking the cycle is a real design change, not a mechanical move. |

## H.2 — What protects the refactor

Unusually strong. 843 tests, the agent loop *scored* by `evaluation.py`, the
frontend *executed* by 10 harnesses, the HTTP surface pinned, and
`test_singletons_build_once.py` / `test_startup_is_not_blocked.py` guarding
import-time behavior. The gate is a genuine safety net.

## H.3 — What is NOT protected

- **No coverage measurement anywhere.** No `.coveragerc`, no CI gate. I cannot
  tell you which branches of `renderProviderConnectBox` or `store.search` are
  exercised. For B.2 and B.4 I would want a coverage read *before* extracting.
- **`mypy` covers less than "clean over 113 files" suggests**: `check_untyped_defs=false`
  globally, only 10 modules strict, and **26% of all lines** (`core/store.py`,
  `brain/brain.py`, `agents/tools.py`, `api/routes/agents.py`, `api/routes/providers.py`, …)
  are `ignore_errors`. Three of the hotspots in section B sit inside that 26%.

---

# I. Recommended refactoring order

Sequenced so each step is independently verifiable and each one makes the next cheaper.

### P0 — very safe, high value

| # | change | files | subsystems | budget |
|---|---|---|---|---|
| **1** | `docs/ARCHITECTURE.md` + slim root `CLAUDE.md` to an operating manual; move war stories into `docs/` (see G) | ~6 docs, 0 source | docs only | GREEN |
| **2** | Extract the CLI login state machine from `cursor.py`/`grok_cli.py` into one parameterised `models/cli_login.py`; fold the three `_augmented_path()` copies into it (D.1, D.2) | 4 | 1 (`models`) | GREEN |
| **3** | Add section banners + a header map to `app.js` — **no code movement at all** (F.1) | 1 | 1 (`web`) | GREEN |
| **4** | Delete the 3 dead constants; fix the `MUTATING_METHODS` comment/code mismatch in `security.py`; comment the two JS-called `_Bridge` methods (E) | 3 | 2 | GREEN |
| **5** | `web`: single `readConnected()` + single `normalizeProviderId()` + use `caps.key_env`, replacing D.3/D.4/D.5 | 1 (+harness check) | 1 (`web`) | GREEN |

### P1 — useful, requires care

| # | change | files | subsystems | budget |
|---|---|---|---|---|
| **6** | Split `run_turn` into `_resolve_model()` / `_build_messages()` / the loop / `_record_turn()`, **same module** (B.1) | 1 | 1 (`agents`) | GREEN |
| **7** | Extract the 8-factor scorer from `store.search` into `core/recall_scoring.py`, pure functions, unchanged arithmetic and short-circuit order (B.2, R6) | 2 | 1 (`core`) | GREEN |
| **8** | Extract `brain.recall`'s context-string assembly into `brain/recall_context.py` (B.3) | 2 | 1 (`brain`) | GREEN |
| **9** | **`app.js` split, step 1**: teach the harnesses to read N ordered sources (`tests/js/_app_source.mjs`, order derived from `index.html`), then carve out `web/core.js` (~115 lines: `$`, `api`, `esc`, `md`, `toast`, orbs, icons) | ~12 | 2 (`web`, `tests`) | **YELLOW — ask first** |
| **10** | **`app.js` split, step 2+**: `web/providers.js` (863) then `web/models.js` (792) — one module per commit, harness green between each | 3–4 each | 1 | YELLOW |
| **11** | Create `chitragupta/desktop/` (`desktop.py` + `hud.py`); invert `api → hud` behind a small bridge so the HTTP layer stops importing the window layer (C.4) | ~8 | 2 (`api`, `desktop`) | **YELLOW — ask first** |

### P2 — architectural, high risk

| # | change | why it is P2 |
|---|---|---|
| **12** | Break the 14-module `models/` cycle — most likely by extracting a leaf `models/provider_ids.py` + `models/plan_tiers.py` that `entitlements`/`discovery`/`errors` can all import without back-edges (C.1) | Touches the provider/auth architecture. Every promoted import can fail at startup. |
| **13** | Give the 8 ownerless root modules a home — e.g. `chitragupta/workspace/` for actions/routines/tasks/reminders/scheduled (C.3) | Wide import churn; breaks the `actions ↔ routines ↔ approvals` cycle, which is a design change |
| **14** | Split `api/routes/workspace.py` by concern (C.6) | Route registration order (R3); `api_surface.json` must stay byte-identical |
| **15** | Separate auth from inference in `models/chatgpt_auth.py` (B.5) | **RED** — provider authentication redesign. Would require explicit approval. |
| **16** | `core/store.py` N+1 `self.get()` in the search loop (B.2) | A **performance** change, not complexity. Needs a benchmark before/after. Out of this task's scope. |

### Explicitly NOT recommended

- Any ES-module conversion of `app.js` (F.2).
- Any UI redesign, framework, or build step.
- Refactoring `connectors/` — it is the cleanest subsystem and the apparent duplication is irreducible (D.7).
- Adding dependencies, changing schemas, changing API contracts, or touching auth flows.
- Fixing the dead delete button (F.4) *as part of a refactor* — it is a behavior bug and belongs in its own commit.

