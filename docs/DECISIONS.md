# Chitragupta — Decision Log (ADRs)

> Every important architectural/product decision, with the reasoning. Newest at
> the bottom of each section. This is the "why we did it this way" record.
> Companions: [`CONTEXT.md`](CONTEXT.md) (direction), [`PROJECT.md`](PROJECT.md)
> (how it works), [`CONCEPTS.md`](CONCEPTS.md) (the ideas from scratch).

---

## Product / strategy

### D1 — Build a faithful, open Turnstone (myturnstone.ai, YC W26)
Clone the real product: a local-first AI **workspace** where named agents share a
**second brain** built from your apps and act using **any model you bring**.
NOT the passive MCP memory-server we first (wrongly) built.
**Why:** it's what Turnstone actually is; agents that *do work* are the point.

### D2 — North Star: private "knows-your-work" assistant, depth-first (NOT Jarvis)
Match Turnstone's shape; grow "does things" abilities over time. Do not chase a
broad voice/life-OS ("Jarvis").
**Why:** Jarvis competes with Apple/Google/OpenAI and is never "finishable" — the
opposite of the goal (a polished, finished product). Depth is finishable and
defensible (big clouds won't do local/private well).

### D3 — Polished, finished quality bar (not demo-grade)
Hold a high bar on every change; fix rough edges properly.
**Why:** the portfolio should contain finished products, not half-built demos.

### D4 — Every fix must be SYSTEMIC (automatic for all users)
No manual data surgery as "the fix" — dedup, migrations, syncs, cleanups must be
code that self-heals for every user, on startup / on sync / on schedule.
**Why:** it's a product for many users; they can't run scripts or CLI. If a bug
needs a manual fix, that fix belongs in the system.

### D5 — Likely wedge: Indian small businesses (later specialization)
Same engine, pointed at a market Turnstone won't serve. Not the current focus.

---

## Architecture

### A1 — Python + FastAPI + web UI (web-first, desktop-later)
**Why:** fastest to a working demo; a Tauri/Electron shell can wrap the same
engine later with zero rewrite. Web now, app later — not either/or.

### A2 — Agent-first: agents are the core primitive
Brain, models, tools, connectors all exist to serve agents.
**Why:** matches what the product is — you talk to agents that act.

### A3 — Four preset agents sharing one brain (Inbox, Launch, Research, Personal)
Turnstone's exact set. Each domain-scoped; all share one brain.
**Why:** faithful parity; shared brain = "what one learns, all know."

### A4 — Bring-your-own-model via a provider abstraction
One interface; backends: claude-code, anthropic, openai, openrouter, ollama,
subscription-gateway, mock. Switchable in the UI, per message.
**Why:** BYO model is Turnstone's core promise; abstraction means agent code
never changes when you swap models. `mock` keeps everything testable offline.

### A5 — Default to a free LOCAL model (Ollama), later added claude-code
**Why:** most on-brand for local-first/private; zero keys/cost. `claude-code`
(runs the user's terminal Claude via `claude -p`) added for much smarter answers
using the user's own Claude — at the cost of latency + usage.

### A6 — Local SQLite for everything; vectors as BLOBs searched in NumPy
No external DB. `~/Library/Chitragupta/*.db`.
**Why:** local-first, zero-config, portable, ships inside a desktop app. Fine to
tens of thousands of memories. Deferred: sqlite-vec + FTS5 (Tier 2) at scale.

### A7 — Knowledge-graph brain (GraphRAG), built from CURATED prose only
Vector memories + an entity/relation graph; graph is built from notes/manual/
agent/notion + prose files — NOT bulk email or Drive.
**Why:** vectors give coverage, the graph gives precise structured recall; but
email/Drive HTML pollutes the graph (DOCTYPE/Arial/Subject), so they stay
searchable memories without polluting entities.

### A8 — Semantic embeddings: upgraded MiniLM → BGE (bge-small-en-v1.5)
With query-instruction prefixes (embed_query).
**Why:** MiniLM ranked "Security alert" above "You're Selected for HackWithUP";
a real Gmail lookup failed. BGE fixed it (target went from not-in-top-15 → #3).

### A9 — Context grounding: inject what the model can't know, every turn
Auto-recall (brain), current date/time, current tasks, and (for domain agents) a
source preference are injected before the model runs.
**Why:** LLMs are stateless and have no clock; small models won't reliably call
tools. Injecting facts deterministically = "already knows you" on any backend.

### A10 — Date-aware retrieval
Parse a date/range from the query ("emails on July 14", "todays mails", "last
week") and filter memories by their real `event_date` (Gmail/Calendar/Drive
store it). Compact overview rendering so a whole day's items fit.
**Why:** semantic search can't retrieve by date — a date is a filter, not a
meaning. Possessive/plural forms (todays/yesterdays) are handled.

### A11 — Source overviews ("what's in my drive/inbox/calendar")
Detect listing intent + a source; inject a DISTINCT file/email/event listing
(grouped by uri so multi-chunk PDFs count once).
**Why:** "what's in my drive" is a listing request semantic search can't answer
(docs don't contain the word "drive").

### A12 — On-demand Drive fetch (lazy loading)  ← key recent decision
Keep a **bounded** brain (capped sync). When the user asks for a document NOT in
the brain, do a **live targeted Drive search** (name + full-text, incl.
Shared-with-me) and ingest just that file, then answer. Done in the RECALL layer
(not a model tool) so it works on any backend, incl. claude-code.
**Why:** "Shared with me" has hundreds of unrelated files — syncing all is bloaty
AND unreliable for finding one. Lazy loading = bounded index + long-tail on
demand. The user proposed this; it's the right design.

### A13 — Continuous background sync + self-heal
A scheduler re-syncs ready connectors on a timer (default 30m) and on startup;
re-syncs are cheap (store skips content it already has). It runs `dedupe()` on
startup and after every sweep.
**Why:** Turnstone "continuously updates"; and per D4, dedup/heal must be
automatic — no user runs a dedup script.

---

## Connectors

### C1 — Read-only, lazy-imported SDKs, graceful when unconfigured
All connectors read-only; each shows readiness + a setup guide in the UI.

### C2 — Files connector guardrails (cap + junk filters)
Refuse >CHITRAGUPTA_MAX_FILES (default 2000); skip build/vendor/lock/minified/
node_modules. **Why:** a folder sync once bloated the brain to 82k memories/433MB
(unrelated project code). Guardrails + a configurable cap prevent recurrence.

### C3 — Google connectors share one OAuth (Gmail/Calendar/Drive)
One Desktop OAuth client, read-only scopes, token cached. `client_secret.json`
lives outside the repo and is gitignored. **Why:** one consent, three sources;
never commit secrets.

### C4 — Gmail: all mail (paginated) + HTML stripping + real dates
Default query is all mail up to CHITRAGUPTA_GMAIL_MAX (1500), bodies converted to
clean text (drop CSS/tags), each email tagged with its date.
**Why:** 30-day/50-msg default was too small; CSS leaked into summaries; dates
enable date queries.

### C5 — Drive: Office formats + Shared-with-me/Shared-Drives + file dates + skip
Parses .docx/.pptx + Google Slides; lists Shared-with-me and Shared Drives;
stores modifiedTime as date; skips re-downloading unchanged files (file_id).
**Why:** the resume (.docx) and shared course notes were invisible; re-downloads
made syncs slow.

### C6 — Added Google Calendar + iMessage connectors
Calendar reuses Google auth (fixed a Brain.ingest metadata bug that crashed it);
iMessage reads the local macOS chat.db (Full Disk Access, no cloud).

---

## Notable bugs fixed (root-caused in code, not patched by hand)

- **claude-code blank error** — fake User:/Assistant: transcript tripped its
  stop-sequence; now uses --append-system-prompt + parses is_error.
- **"claude.ai connector isn't authorized" hallucination** — claude-code (real
  Claude) confused its own connectors with Chitragupta; prompt now forbids it and
  states data is already in the brain.
- **Recall missed real emails** — MiniLM weakness → BGE (A8) + source preference.
- **Email/Drive HTML in the graph** — graph now curated-prose only (A7).
- **Calendar crash** — Brain.ingest didn't accept `metadata`.
- **1,451 duplicate emails** — earlier manual in-place cleanup + re-sync; fixed by
  systemic auto-`dedupe()` on startup + after sync (D4).
- **Resume/shared notes invisible** — .docx support + Shared-with-me + on-demand
  fetch (C5, A12).
- **parse_when crashed on invalid times** — "9:99"/"3:70" raised an uncaught
  ValueError that could kill the reminder/action flow; now range-validated → None.
- **Gmail base64 crash aborted the whole sync** — Gmail returns unpadded base64url;
  `_decode` raised "Incorrect padding", and since the message loop shared one try,
  ONE such email lost all remaining mail. Now re-pads + isolates each message (H4).
- **Google auth stuck on expired/corrupt token** — a revoked refresh token (7-day
  Testing-mode expiry) or corrupt token file failed every future sync forever with
  the stale token never cleared. Now self-heals: drop + re-consent (H5).
- **Custom-API field-less records collapsed to one "None"** — str(None) is truthy so
  the `or` fallback never fired; field-less rows deduped to a single record (H8).
- **"database is locked" under scheduler+API concurrency** — no busy_timeout; added
  5s busy_timeout (+ WAL) on every DB (H6).
- **claude-code "not found" as a GUI app** — Finder gives a stripped PATH; now searches
  Homebrew/npm-global/Claude-local and passes an augmented PATH to the subprocess (H1).

---

## Production hardening & trust (H-series — the "make it a finished product" pass)

### H1 — GUI-safe binary + platform-aware connectors
Finder-launched apps get a minimal PATH; `claude` is found across Homebrew (Apple
Silicon + Intel), npm-global and `~/.claude/local`. Connectors declare `platforms`;
Apple Mail/Calendar/iMessage are darwin-only and hidden off macOS instead of shown broken.

### H2 — Crash-isolation everywhere (one bad item never aborts a sync)
Every list-syncing connector (Gmail, Calendar, Notion, Linear, GitHub, custom API)
isolates each record: a malformed item is counted as skipped and the sync continues.

### H3 — Providers fail with clear messages, never raw tracebacks
OpenAI-compatible `chat()` maps unreachable host / 401·403 / 429 / 5xx / timeout /
malformed response to short, actionable text. An engaging reply indicator (pulsing
dot + cycling status + countdown + progress bar) replaces the static "thinking…".

### H4 — Gmail base64url decode tolerates missing padding *(see bugs)*
### H5 — Self-healing Google auth (corrupt/expired token → re-consent) *(see bugs)*
### H6 — SQLite busy_timeout=5000 + WAL on all DBs *(see bugs)*

### H7 — Input validation & safety at the API edge
Empty/whitespace chat rejected (422, no wasted model call); chat/ingest payloads
size-capped; custom-app URLs must be http(s) (urllib would otherwise read file://);
email recipients validated against a real address pattern before send.

### H8 — Custom API connector (connect any REST app, no code)
Declarative: base URL + endpoint + auth (bearer/header/query) + dot-path field
mapping; fetched and ingested like a built-in. No arbitrary code (no RCE). Field-less
records stay distinct (see bugs).

### H9 — In-app secret fields + macOS Keychain (encrypted at rest)
Token connectors (Notion/Linear/GitHub/custom) declare a `secret_field` → the UI
renders an in-app input, no `.env`. Secrets are stored in the **macOS Keychain**
(encrypted at rest by the OS); env var → Keychain → legacy file resolution, with
plaintext-file secrets migrated out on next save.

### H10 — "What leaves my device" transparency badge
Each model backend is classified local (Ollama/mock — stays on-device) or cloud
(claude-code/anthropic/openai/openrouter/subscription — context sent off-device),
shown as a 🔒/☁️ badge with a tooltip naming the destination. Serves the North Star (D2).

### H11 — Brain export / import (you own your data)
Portable JSON backup of every memory (no vectors); import re-embeds under the current
embedder and rebuilds the graph, so a backup survives an embedder change or a new Mac.
Dedup makes re-import idempotent.

### H12 — Bundled Google Desktop OAuth client is committed
Installed-app client secrets are non-confidential (PKCE + localhost redirect), so the
Desktop client ships in-repo → a stranger who installs the `.dmg` presses *Sign in with
Google* and it works, with no Cloud Console setup. That is the decision and it stands.

**What it costs, which the original entry did not say.** The `client_id` is public, so
anyone can stand up a consent screen branded "Chitragupta" with it. Abuse attributed to the
project lands on our quota: a rate-limit or suspension takes Gmail, Calendar **and** Drive
down for *every* user at once, and nothing shippable from our side fixes it except a new
client. A secret scanner revoking it has the same effect, unilaterally, on a day we did
not choose.

**Why that is survivable.** `config.py::google_client_secrets` resolves in precedence
order — `$GOOGLE_CLIENT_SECRETS`, then `~/Library/Chitragupta/google_client_secret.json`,
then the bundled file. So a replacement is verified against a real account *before* it is
committed, and one affected user is unblocked by dropping a file rather than by a release.
That order is pinned by `tests/test_google_client_rotation.py`; if it breaks, the rotation
procedure stops being executable.

Procedure, and what users experience during it:
[`development/google-client-rotation.md`](development/google-client-rotation.md).

**If the repo goes public**, treat the id as burned rather than waiting for evidence, and
per-user clients become the escape hatch — a product decision, deliberately not taken here.

### H13 — First-run onboarding flow
A "connect your data" screen with four real choices (Sign in with Google · local Mac
sources · connect an app · tell it a fact), shown on a fresh/empty brain and reopenable
via "? Getting started".

### H14 — Per-connector sync feedback + stale-at-a-glance
Live "syncing…" state per row; overdue connectors show an amber "stale" dot.

### H15 — Test suite expanded 6 → 59; scheduler/routines log failures
Edge-case regression tests (parse_when, actions, custom API, Gmail decode, auth
self-heal, migrations, reminders, export/import, API validation, MCP). Background
loops `log.exception` instead of silent `pass`.

### H16 — MCP brain-as-a-service (the private memory layer for any AI)
Chitragupta exposes its brain to any MCP client (Claude Desktop/Code, Cursor) over
stdio via `chitragupta/mcp_server/server.py` — 8 tools: `search_brain`, `about`
(knowledge-graph lookup), `remember`, `web_search`, `brain_stats`, `list_tasks`,
`add_task`, `complete_task`. Correct for **mcp 2.x** (FastMCP→MCPServer). Local-only
(stdio, spawned by the client — no network/auth needed). `chitragupta mcp-install`
prints the one-line registration. This makes the brain usable by *every* AI, not
just Chitragupta's own agents — the biggest differentiator (D2).
- `about()` guard: BGE gives unrelated short phrases a high baseline cosine, so it
  requires the entity name to share a word with the query (or score ≥0.8), else a
  query returns near-random entities.

### Observation (to investigate) — LLM-extractor graph noise
When the extractor uses **claude-code**, it inherits the user's Claude memory /
project context, so extracting an unrelated sentence can add personal entities that
aren't in the text (e.g. "Dev", "macOS Keychain"). Only affects the LLM-extractor
path (manual ingest / `remember`); connector bulk syncs use the clean heuristic
(`fast=True`). Candidate fix: constrain the extractor prompt to the given text only,
or prefer the heuristic for single short ingests.

---

## Desktop sign-in & window behaviour (W-series)

> Full record, with measurements and the wrong turns:
> [`DESKTOP-SIGNIN.md`](DESKTOP-SIGNIN.md).

### W1 — The floating card is raised by the page, never by the API
`window.pywebview.api.open_signin_hud(...)` from the frontend; the backend never
creates a window.
**Why:** under `chitragupta app --dev` the backend is a separate uvicorn process
with no handle on the webview, so a backend-initiated window silently did
nothing. The frontend always runs inside the webview.

### W2 — `NSFloatingWindowLevel`, not `NSStatusWindowLevel`
pywebview's `on_top` gives status level (25). We set floating (3).
**Why:** status level also covers the menu bar and system UI, which a sign-in
card has no business claiming. Level was never the bug — see W3 — so the higher
level bought nothing and cost correctness.

### W3 — `CanJoinAllSpaces | FullScreenAuxiliary`, and NOT `Stationary`
**Why:** the card "going behind other apps" was never z-order. A window with the
default collection behaviour belongs to the Space it was created on, so switching
Spaces left it behind — measured, `isOnActiveSpace` went `False`. `Stationary`
pins a window to screen coordinates during Space transitions (for wallpaper-like
overlays) and is redundant once a window joins every Space; tested, changed
nothing. Applying every flag that sounds relevant is how a floating panel starts
behaving like a screen-saver overlay.

### W4 — Accept that the card cannot cover another app's full-screen Space
**Why:** measured across levels 3/25/101, with and without `Stationary`, and with
an accessory activation policy — none reach it. Apps that manage it are
`LSUIElement` accessory apps using a non-activating `NSPanel`; pywebview creates
a plain `NSWindow`, and making Chitragupta dockless is not worth this. The in-app
row in the Models panel is the fallback, which is why it stays on screen even
when the floating card is up.

### W5 — `orderFrontRegardless()` instead of the backend's `show()`
**Why:** pywebview's `show()` is `makeKeyAndOrderFront_` +
`activateIgnoringOtherApps_`, which pulls keyboard focus out of the browser the
user is signing in to — on every card update. A click on the card still brings
the app forward normally.

### W6 — The window sizes itself to the card
`_Bridge.fit` → `_resize_now`, driven by a `ResizeObserver` and re-run on
`document.fonts.ready`, anchored at the **top** edge.
**Why:** the copy differs per provider and per state, so a fixed height either
leaves a hole under the text or clips it. Cocoa's origin is the bottom-left, so
resizing naively walks the card up the screen. `overflow: hidden` on `html, body`
is load-bearing: a window a pixel short grows a scrollbar, which rewraps the text
and makes the card taller, so the window chases a height that keeps moving.

### W7 — The webview's backdrop is cleared, not just the window
**Why:** a transparent window is not enough. WKWebView fills its bounds with
`underPageBackgroundColor` (opaque white, measured at alpha 1.0), which showed as
a white block below the card. pywebview's transparency support predates that
property and only clears the older `drawsBackground`.

### W8 — Anything we spawn, we clean up — across runs
`models/login_processes.py` records every vendor-CLI login to disk; `run_app`
reaps on launch and on quit; only recorded PIDs are signalled, and only after
re-checking the command line.
**Why:** a CLI `login` waits for a browser callback that may never come, nothing
reaped them, and the handle lived in module state so each launch forgot the last
one's. **158 were found alive on one machine**, each holding the vendor's OAuth
callback port until sign-in stopped working. A reused PID killed is worse than
the leak, hence the command-line re-check.

### W9 — The webview origin is user state, so the port is fixed
`_reserve_port` binds the saved port the way uvicorn does (**`SO_REUSEADDR`**) and
hands that socket to the server.
**Why:** `localStorage` is keyed to the origin. The old probe bound without
`SO_REUSEADDR`, so a port left in `TIME_WAIT` by the server that had just exited
read as taken; the app moved to a random port and the onboarding flag, chosen
model and lead agent all silently vanished. Symptom: **every other launch opened
empty.**

### W10 — Tests may never start a real sign-in
`conftest.py` swaps the argv of any `login` spawn for a command that exits at once.
**Why:** `flow.start()` and `POST /signin` reach `claude auth login` for real, and
**every pytest run left one alive** — the dominant source of W8's 158. The flows
still run their own code; only the vendor binary is kept out. Third test-hygiene
incident here, after tests writing to the real Keychain and binding port 1455.

---

## The engineering fleet, and why it was retired (F-series)

### F1 — One session builds this repo, not eight

**Retired 2026-09-13.** For one day this repo was built by eight specialised
Claude sessions in parallel — an Architect on `main` plus Frontend, API, Agents,
Brain, Models, Connectors and QA, each in its own git worktree on its own
`agent/*` branch, each with a rules file under `.claude/fleet/<role>/`, governed
by an ownership map, six named seams, ten rules of parallel work, an append-only
handoff log and a courtesy lock protocol.

It shipped one real feature that way: connector tools inside the agent loop,
built by four sessions at once (`docs/ROADMAP.md` → shipped). That feature is
kept. The machinery that produced it is not.

**What it cost, measured on its only run.** The parallelism was real — four
subsystems moved at once and each arrived with its own tests, and QA found a
live vulnerability (A8) precisely because it had nothing to do but look. But
integration surfaced three defects that existed *only because* the work was
split, and none of which any single session could see:

- Each side tested against its own idea of the other, so the row shape at the
  Agents↔Frontend seam was wrong in both directions at once and both suites were
  green. It put a skill named after the protocol on screen inside an invented
  group, beside skills named `mcp__github__list_issues`.
- Two layers independently bounded the same result, and the outer cap sat below
  the inner one, silently deleting the message the inner one existed to write.
- Four sessions appended to one append-only log, which conflicted on every
  merge.

The contract was written down in advance, as the process required, and was still
underspecified — it named `{name, description, source, connector}` and neither a
display name nor what a category row is. **A contract is only as good as the
test that fails when one side ships without the other**, and writing that test
is exactly the work that has no owner when ownership is the organising idea.

**Why retire it rather than fix it.** The failures above are fixable — a
contract test at each seam, a per-session handoff file instead of one shared
log. But they are the *visible* tax, and the invisible one is larger: eight role
files (1,423 lines) and a third of `/CLAUDE.md` described how the sessions
relate to each other rather than how the product works, and every one of those
lines is read by every session on every task, forever. A codebase this size does
not need a parliament. The rules that made the code good — the product doctrine,
the model-availability rules, the agent-loop invariants, the test hygiene — were
never about parallelism, and they are what is kept.

**What was removed:**

| | |
|---|---|
| worktrees | `../chitragupta-{agents,api,brain,connectors,frontend,models,qa}` |
| branches | `agent/*` (all seven, all fully merged into `main` first) |
| rules | `.claude/fleet/**` — 10 role files, the README, the template, `locks/` |
| log | `.claude/fleet/HANDOFF.md` (558 lines; its load-bearing content is here) |
| doctrine | `/CLAUDE.md`'s role table, ownership map, six seams, ten rules of parallel work, and the whole "Fleet operations" section |
| pointers | the role-pointer framing in each directory's `CLAUDE.md` |

**What was kept, and where it lives now:**

- Every product rule, the model-availability doctrine, the agent-loop
  invariants, the layout and the conventions — unchanged, in `/CLAUDE.md`.
- The per-directory `CLAUDE.md` files survive as genuine notes about that
  directory, with the role and handoff framing stripped. They are still worth
  having: they load automatically when you open a file there.
- `tests/CLAUDE.md`'s four standing rules, which were the fleet's best
  contribution and have nothing to do with it: no test may start a real sign-in,
  never weaken a test to get green, a bug fix ships with a regression test, and
  frontend render paths must be executed rather than parsed.
- The verification sequence — focused tests → `pytest` → `ruff` → `mypy` — which
  was the per-role table collapsed to the one column that always applied.

**Before deleting, every worktree was verified to have no uncommitted changes,
no untracked files, no stashes, and zero commits unreachable from `main`.**
Nothing was lost. The eight-session history stays in the log: `git log` still
shows the four feature branches and their merges.

**Reopen if** the repo grows subsystems that genuinely cannot be held in one
context at once. If it does, the thing to bring back first is not the ownership
map — it is a contract test per seam, which is the part that actually caught
something.

## Complexity reduction (X-series)

*2026-09-14. A pass whose brief was "make the next change easier, not make the
codebase different". Two real bugs were found on the way; the rest is
structure.*

### X1 — `docs/ARCHITECTURE.md` is the one normative architecture document

Architecture was described in four places — `CLAUDE.md` §Layout, `PROJECT.md`
§4, this file, and the eleven directory notes. Four descriptions of one system
is three chances to read the stale one.

`ARCHITECTURE.md` now owns subsystem ownership, the allowed dependency
direction, the contracts between layers and the invariants. The others point at
it. **Adding a fifth description is the failure mode to avoid**, including in a
future audit report.

### X2 — `CLAUDE.md` is an operating manual, not an archive

742 lines loaded into every session before any work began, of which 53% was two
sections of implementation history. Now 294 lines: purpose, commands, ownership,
invariants, how to work here, and a table of where to look for everything else.

Nothing was deleted — the war stories moved to `DESKTOP-SIGNIN.md` (which
already held all ten incidents) and to new `docs/development/` files. Verified by
grepping 28 load-bearing identifiers across the result, not by reading it over.

### X3 — Coverage prints, and never gates

No threshold, and there should not be one: a percentage invites tests written to
raise it. The per-module table is the output that matters — it is how
`grok_cli.cancel_cli_login` was found to have no test at all while its
byte-identical twin in `cursor.py` was covered.

Nothing is omitted from the report either. `desktop.py` sits at 31% because
`run_app` needs a real window, and a file excluded to keep the number tidy is
the number lying.

### X4 — One vendor-CLI sign-in, and vendor lists stay with vendors

`cursor.py` and `grok_cli.py` each carried the same ninety-line login state
machine; normalising four names left two differences across it. Identical was
never the problem — **separately maintained** was, and the coverage asymmetry
above is what that looks like.

The counterpart decision is where the sharing stops. The three `_EXTRA_BIN_DIRS`
lists were **not** merged: each also drives the fallback binary scan in its own
`find_*`, so unifying them would send Cursor's finder through `~/.grok/bin`.
Deduplication that changes behaviour is not deduplication.

### X5 — The frontend splits into plain scripts, never ES modules

`app.js` went 3,581 → 237 lines across eleven files. Not modules: the nine
`tests/js` harnesses evaluate the frontend with `new Function`, which compiles a
script and cannot process `import`, and several of them reach into that scope to
install a fixture. Several `<script src>` tags in one shared scope is
semantically what one file already was.

**`index.html` declares the order and is the only place it is written down.** A
hardcoded list in the loader would be a second source of truth whose drift is
silent — the browser loading one order while the tests load another is exactly
the class of bug these harnesses exist to catch.

### X6 — A test that reads the frontend reads all of it

Twice, moving code out of `app.js` broke tests that greped it by path. The first
time was eleven at once, loudly. The dangerous version is the one that keeps
passing while checking a file the code has moved out of — and the next
extraction would have produced exactly that.

`tests/web_sources.py` reads `index.html` the same way `_app_source.mjs` does,
and a guard now fails if any test reads `app.js` directly. Passing the path to a
harness is still fine; only reading it here is the mistake.

### X7 — Each file must parse on its own, not just the concatenation

The `tools.js` cut ended one line early, leaving a closing brace behind. The two
halves **cancelled out** when joined, so `node --check` on the whole was happy
while every top-level declaration after the seam sat nested inside a truncated
function and nothing was global.

Parsing the concatenation is a different question from parsing each file. It is
now the fifth check before any cut, and it refuses the bad one.

---

## The rename (R-series)

### R1 — Lodestone became Chitragupta (2026-09-16)

*Lodestone* named a compass stone: something that points you somewhere.
*Chitragupta* is, in Hindu tradition, the scribe who keeps the record of what
each person has actually done — which is what this product is. It remembers your
work and can always say where a fact came from. The second name describes the
thing; the first described an aspiration about it.

The package, the CLI, the env prefix, the bundle id `ai.chitragupta.app`, the
Keychain service, the data directory and the localStorage keys all moved with
it, along with ~1,550 strings across 237 files. A `migration.py` carried
pre-rename installs across — the data directory, the Keychain items and the
browser prefs — and was **deleted on 2026-09-17** once nothing predated the
rename. The one-time move is done; there is no compatibility path left, and
`LODESTONE_*` environment variables no longer do anything.

**The exception that remains.** `data/google_client.json` still reads
`project_id: lodestone-507013`. That is a real Google Cloud project, not a
string — rewriting it breaks Google sign-in for every user. Renaming it means
renaming the project (or issuing a new client) in the Cloud console first; see
[`development/google-client-rotation.md`](development/google-client-rotation.md)
and A1 in [`AUDIT.md`](AUDIT.md), which already wants that client rotated.

The visual identity was untouched: "Living Constellation" was designed for the
*brain* — night sky, memories as particles, the graph as lines between them —
not for the wordmark, so it survived intact.

---

## Packaging (P-series)

### P1 — Playwright ships inside the `.dmg` (2026-09-17)

**Decided: keep it.** The browser feature costs **130 MB** in the bundle
(116 MB arm64 node binary + 14 MB driver JS), taking the app from 189 MB to
321 MB and the download from 75 MB to 122 MB. The alternative was excluding it
and making the browser a source-checkout-only feature.

Kept because a feature that only exists for people with a git clone is not a
feature of the product. `can_drive()` would have answered `False` cleanly, so
nothing would have offered a dead button — the browser would simply have been
absent from every installed copy, which is a strange thing to build and then
not ship.

The user does pay twice: 122 MB for the app, then ~150 MB of Chromium on first
use. That is the accepted cost.

**How it nearly broke silently.** Playwright reached the bundle only because
`browser/driver.py` spells `from playwright.sync_api import ...` statically
inside a function, which PyInstaller's bytecode analysis happens to follow.
Rewriting that one line as `importlib.import_module(...)` would have dropped
130 MB of driver with a green build, a running app, `can_drive()` still
answering `True` (it only asks `find_spec`), and the browser failing at the
moment a user opened a page — *after* downloading 150 MB of Chromium. It is now
named in the spec's `HIDDEN` list so that cannot happen quietly.

**Still unfaced:** a notarised app spawning a downloaded binary. Nothing here
has been signed or notarised, and Chromium arriving at runtime under the
Hardened Runtime is the case `packaging/entitlements.plist` was not written for.

---

## Deferred (tracked, do later)

- **Tier 2 scaling**: sqlite-vec (ANN) + FTS5 + incremental indexing, for when the
  brain passes ~30–50k memories.
- **Auto-migration versioning**: stamp a data version; on startup, auto re-embed /
  rebuild-graph when the extractor/embedder changes (so these stop being manual).
- **UI-based OAuth**: authorize connectors from the UI, no CLI.
- **Action-taking**: agents that *do* ("draft a reply") with confirmation.
- **On-demand fetch for Gmail** (like Drive): "find the email where X sent Y".
- **Tauri desktop shell**; **custom user-defined agents**.
