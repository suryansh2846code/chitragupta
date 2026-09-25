# Chitragupta — operating manual for coding agents

Local-first AI **agent workspace**: a team of agents share one on-device "brain"
(memories + a knowledge graph) built from the user's connected sources. Bring
your own model. Everything runs and stays on the user's machine. macOS only.

**One session works this repo at a time.**

This file is what you need on almost every task. It states rules; it does not
explain them at length — the explanation is in the document each rule points to,
and that document is the one to update when the rule changes.

- **Architecture, ownership, dependency direction, contracts** →
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Every code directory has its own `CLAUDE.md`** — it narrows this file for
  that directory and never contradicts it. If they disagree, this file wins and
  the directory note is wrong.
- **No rule is written in two places.** A duplicated rule drifts, and the copy
  that drifts is always the one you read.

---

## Run it

- `chitragupta serve` → FastAPI on a browser tab.
- `chitragupta app` → native desktop window (**pywebview / WKWebView**), server on
  a dynamic loopback port. This is how users run it.
- **Dev / live-editing:** `chitragupta app --dev` (or `serve --dev`) runs the
  backend with **uvicorn --reload**, so Python edits hot-reload. Cmd+R in the
  window reloads only the FRONTEND — without `--dev` a new endpoint 404s until
  you relaunch. Frontend-only edits always show on Cmd+R.
- Tests `pytest` · lint `ruff check chitragupta tests` · types `mypy chitragupta`.
  Python venv at `.venv` — use `./.venv/bin/python`.
- Coverage: `pytest --cov --cov-report=term-missing`. No threshold, deliberately.
- **Shipping:** `scripts/build-dmg.sh` builds the real signed, notarised `.dmg`;
  `scripts/build-macos-app.sh` is a *development shim* that only works on this
  machine. Read [`docs/DISTRIBUTION.md`](docs/DISTRIBUTION.md) first.

## The map

| | |
|---|---|
| `api/` | the HTTP surface. `app.py` composes; routes live in `routes/` |
| `agents/` | the turn loop, tools, effort, delegation, approvals, permissions |
| `automation/` | the automation engine — triggers, conditions, durable runs |
| `models/` | providers, auth flows, entitlements, discovery, CLI manager, errors |
| `brain/` | memories, graph, enrichment; `canonical/` is the curated layer |
| `connectors/` | one class per source, registered in `__init__.py::REGISTRY` |
| `core/` | SQLite store, schema, embeddings, chunking, dates |
| `browser/` | the browser an agent drives, and the origin allow-list in front of it |
| `web/` | the whole frontend. Vanilla JS, **no build step** |
| `desktop.py` · `hud.py` | the native window and the floating sign-in card |
| `metrics.py` · `training.py` | numbers over time — measurements and sets/reps/load, kept as numbers not prose |
| `config.py` · `log.py` | settings and logging. Leaf utilities — keep them that way |

Plus one directory that is **not** part of the Python package:

| | |
|---|---|
| `character/` | the avatar renderer — a standalone, dependency-free JS package with its own README, tests and MIT licence. Nothing in `chitragupta/` imports it; `scripts/sync-character.sh` copies its bundle to `chitragupta/web/character.js` and `tests/test_character_asset.py` fails if the copy drifts. Published standalone at **[github.com/suryansh2846code/character](https://github.com/suryansh2846code/character)** — that repo is the upstream; this copy is the vendored one. Fix bugs here, then mirror them there (or the reverse), and run the sync script. |

Full ownership table and the allowed dependency direction:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §2–3.

---

## This is a product, not a developer tool

Chitragupta ships to people who did not build it. **Every decision is a product
decision** — where "correct for an engineer" and "works for the person who
installed this" disagree, choose the second and make it correct underneath.

- **Never ask the user to open a terminal.** We download, pin and manage the
  vendor CLIs ourselves. One button, with progress. A copyable command is the
  fallback, never the plan.
- **Never show a control that cannot work.** A sign-in button that teaches us
  nothing, a model that 404s when selected, a "Connected" badge with no
  credential — each shipped here, and each read as "the app is broken".
- **Never surface an internal.** No raw provider JSON, no stack traces, no
  internal ids in user-facing text. Errors say what happened and what to do.
- **Anything the user starts, they can stop.** Sign-in, sync, enrichment. A
  close button that silently leaves work running is a lie.
- **Never make the user wait without telling them.** Long work is a background
  job with progress that survives a refresh. A spinner with no end state is a bug.
- **Never lose the user's state to our mistakes.** A retired model id is
  repaired, not fatal. An unusable connection is reported, not dropped.
- **Assume nothing is installed and nothing is configured.** First launch, no
  keys, no CLIs, no accounts — it must still open and explain itself.

When in doubt: would a non-technical user understand what just happened, and
what to do next? If not, it is not finished.

---

## Invariants

Load-bearing. Each was bought by a shipped bug. Breaking one is a regression
even when every test is green. Reasoning and measurements:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §7.

**Security — `api/`**
- The origin guard compares `Origin` to the `Host` it arrived on. **Never** "is
  `Origin` loopback" — a Vite server on `localhost:5173` passes that and could
  `POST /api/brain/reset`. Deliberately not a token.
- `/api/open-browser` takes `http(s)` only, or it hands the system opener any
  scheme an installed app registered.
- Loopback is not a security boundary: every browser the user has open is also
  "on this machine".

**Concurrency — `api/`**
- Slow handlers run in a bounded lane (`@calls_a_model`, `@probes_a_provider`)
  and are `async`, so a queued one holds **no** worker thread. Without this, 20
  blocking probes made the UI wait 2.47s.

**Desktop — `desktop.py`, `hud.py`** · [`docs/DESKTOP-SIGNIN.md`](docs/DESKTOP-SIGNIN.md)
- **The webview origin is user state.** localStorage is keyed to it, so the saved
  port is bound with `SO_REUSEADDR` and handed to the server. When this broke,
  every *other* launch opened empty.
- A native window is raised **by the page**, never by the API — under `--dev` the
  backend is a separate process with no handle on the webview.
- Showing the card must not activate the app (`orderFrontRegardless`, not
  `activateIgnoringOtherApps_`). Every Cocoa mutation goes through
  `AppHelper.callAfter`, or the process hangs.
- **Anything we spawn, we clean up — across runs.** Every vendor login is
  recorded to disk and reaped on launch *and* quit; only recorded PIDs are
  signalled, each re-checked against the command line recorded with it. 158 live
  `claude auth login` processes were once found on one machine.

**Models — `models/`** · [`docs/development/models.md`](docs/development/models.md)
- **Model availability is resolved per user, never hardcoded.** Connection → the
  provider's own answer for this account → static tables. Shipped lists are
  fallbacks flagged `is_fallback=True`.
- **A stored model id is a request**, re-checked before use. Never send one
  straight from storage to a provider.
- **Detection is not consent.** A binary on the machine lets us *offer* it and
  never marks it connected.
- **Every identifier we send a vendor is the vendor's or ours** — never a third
  party's. Sending `originator=opencode` made every user's sign-in breakable by
  someone else's decision.
- A `chat()` returns a `ChatResult`, never raises. Errors are classified and
  translated, never dumped.
- Credentials are independent: removing a key must not sign the user out.
- **A turn's stable prefix is cached, not re-billed twelve times.** A round
  re-sends the agent's prompt and every tool schema unchanged; uncached, a
  twelve-round turn pays for all of it twelve times, on the user's own key.
  `caching.py` places the four markers the vendor allows. **Never mark a block
  that is rebuilt per turn** — recall and the task list sit deliberately outside
  the boundary `Message.stable` draws, because caching something that can never
  be hit again costs more than not caching it.
- **A cached read is still a token the budget must see.** Anthropic reports them
  *outside* `input_tokens`, so reading that field alone makes a long turn look
  ten times cheaper than it is and the turn ledger stops bounding the loop.
- **A model that can think is asked to.** The catalog has flagged `reasoning`
  since it was written and nothing ever sent the parameter, so an Opus at High
  answered like a model with no such capability. `reasoning.py` owns both vendor
  spellings — and the thinking blocks come back on the next round with their
  signatures, or round two of a tool loop is a 400. A model we guessed wrong
  about costs one silent retry, never an error about a feature nobody asked for.

**Measurements — `metrics.py`, `training.py`**
- **A number over time is not a memory.** Recall is linear in memory count, so
  an Apple Health export filed as memories costs every agent a second per turn,
  forever. Claims supersede; readings accumulate.
- **One unit per metric, converted on the way in, unknown units refused.** A
  silent assumption turns 170 lb into 170 kg — a different person, not a
  rounding error.
- **A trend is smoothed and says what it is based on.** Body weight moves a kilo
  a day on water; under ~10 days no trend is reported at all.
- **Data entry the model interpreted lands on a card the user can correct**, and
  what executes is what is on the card at the moment Confirm is pressed — never
  what was proposed. Opt-in per action type (`EDITABLE` in `web/chat.js`), for
  exactly the cases where a misread is easy and surfaces weeks later.

**Brain and storage — `brain/`, `core/`**
- **`"key" in row` on a `sqlite3.Row` tests the VALUES, not the keys.** Use
  `row.keys()`. `SIM118`/`SIM401` are disabled in `pyproject.toml` for exactly
  this reason; re-enabling either reintroduces the bug.
- Claims are **append-only** — a change supersedes, never `UPDATE`s. Inferred
  never supersedes confirmed.
- Recall order is canonical facts → graph → source excerpts, and a curation
  failure can never break plain retrieval.
- **Recall runs on every turn, so nothing in it may be linear in brain size.**
  It was twice over: the eight-factor scoring loop over every row, and an N+1
  that loaded a Memory per row to build objects the sort threw away. 25k
  memories cost 1,145 ms; they now cost 15 ms. **Semantic top-K is never the
  only way into the candidate set** — an exact word match is what a vector index
  is worst at and what a user is most confident about, so the lexical net and
  the date filter are unioned on top, and below `FULL_SCAN_LIMIT` nothing
  narrows at all.
- Graph enrichment is content-based. **Never gate it on a connector allowlist.**

**Automation — `automation/`** · [`docs/AUTOMATION.md`](docs/AUTOMATION.md)
- **There is one permission system and it is not in `automation/`.** Every side
  effect goes through `approvals.run_or_queue` → `permissions.check`. An
  automation may never widen what an interactive agent may do, and a semantic
  condition's verdict is **data, never authorization** — it is a bool and a
  float, and the gate never reads either.
- **State is written before it is acted on**, and the idempotency claim is taken
  **before** the side effect. A claim written afterwards proves nothing about a
  process that died in between, which is the case it exists for. A handler that
  raised leaves the claim open — "we tried and do not know" — and the retry
  verifies instead of repeating.
- **No connector is named in the engine core.** A Gmail message, a webhook and
  one automation finishing are all `Event`s; `automation/sources.py` holds the
  mapping as a dict. Adding a connector event must never mean editing the
  engine, and an AST test enforces it.
- **`BLOCKED` is not `FAILED`.** Blocked is the system correctly declining.
  Collapsing them turns "it correctly did nothing today" into a red badge, and a
  user who sees enough of those stops reading them.
- **The run deadline bounds active work, not waiting.** An approval takes hours;
  checking wall-clock through a wait killed every run that waited.

**Agents — `agents/`** · [`docs/AGENTS.md`](docs/AGENTS.md)
- **A routine pre-authorises the routine, not the stranger who wrote the email it
  read.** Outbound actions need a recipient on the explicit allow-list; a derived
  list is exactly what an injection would name. Interactive chat is deliberately
  not gated.
- **Changing a third-party account waits for a tap until the user says
  otherwise, and the grant names what it reaches.** `mail_triage` is in
  `NEVER_UNATTENDED` — its input is text strangers wrote, so there is nothing
  safe to allow-list. `mcp_action` was too, and is now amber against a key of
  its own: `server:tool@scope`, e.g. `github:add_issue_comment@acme/api`. One
  verb, one container, nothing granted by default, and
  `is_irreversible()` verbs still ask every single time. The argument for the
  promotion is written out in
  [`docs/ACTION-COVERAGE.md`](docs/ACTION-COVERAGE.md) § Per-tool grants —
  read it before loosening anything further.
  `message_send` is allow-listable against its own list keyed `app:chat` — a
  chat id means nothing outside the app it came from, so it is never judged
  against the email list. A batch is **one** card covering every item, never one card each: a
  tap nobody reads by the fourth time is not consent.
- **An agent may not answer as though it did what it skipped.** The plan was
  advisory, which meant it could write four steps, do two, and reply in a way
  that reads identically either way. A turn ending on undone steps is told so
  **once** — with both ways out, finish them or name them — because a step that
  is genuinely impossible would drive a second nudge forever.
- Streaming is a callback on the same loop, never a second loop.
- Delegation guards live in a `ContextVar`: one `copy_context()` **per call**,
  and the chain is left on every exit path.

**Browser — `browser/`** · [`docs/BROWSER.md`](docs/BROWSER.md)
- **The unit of consent is the origin**, and the check runs at the tool, never in
  the model — a page naming another site is an injection, not a decision.
- **Check where the browser landed, not where it was sent.** A granted page can
  redirect anywhere; a refused landing drops the page rather than returning it.
- **Page content must never be able to close its own quarantine fence**, or it
  can make its next paragraph look like ours.
- **Acting is one decision per site, not one per keystroke.** Turning changes
  on for a host IS the consent; `browse_click` / `browse_type` / `browse_submit`
  then run without asking again. The floor is `NEVER_UNATTENDED_TOOLS` — no
  site setting lets an unwatched run touch a page — and a target must be on the
  snapshot the model was shown.

**Frontend — `web/`**
- **Every agent has a face, and it is never blank.** `character.js` composes one
  from the agent's id, so a first launch with an empty database still shows a
  full roster. A stored avatar is an *override*; deleting it returns the agent to
  its generated character, never to nothing.
- **A live avatar follows the cursor and costs frames; a static one does not.**
  `live: true` is for the rail and the chat header. A list of thirty gets
  strings — a roster of live instances is thirty springs integrating on every
  pointer move.
- **Escape before applying inline markdown**, the way `md()` does. The link
  regex is safe only because quotes are already `&quot;`.
- Relative API paths only. Never a host or port.
- Derive UI from capabilities, never from a `providerId === "x"` chain.
- **`app.js` cannot become an ES module** — the test harnesses evaluate it with
  `new Function`. Read
  [`docs/development/frontend-testing.md`](docs/development/frontend-testing.md)
  before splitting anything.

**Everywhere**
- `except Exception: pass` is invisible afterwards. Use
  `with suppressed("what you were attempting"):` from `chitragupta/log.py`.
- **A lazy import is a real dependency, and moving one inside a function does
  not break a cycle — it hides it.** `models/` reached a fourteen-module
  strongly-connected component with `ruff` clean throughout, because every edge
  had been pushed into a function body. 27 modules were in cycles on
  2026-09-25; 11 are, and `tests/test_import_layering.py` pins the remaining
  three as a **closed list that may not grow**. When you need a fact that lives
  above you, the answer is one of three shapes, never an upward import:
  move the fact **down** to a leaf both sides read (`entitlement_rules`,
  `core/schedule`, `core/routine_store`, `action_phrasing`, `home`); **invert**
  it so the owner registers with you (`errors.set_alternatives_supplier`,
  `cache.on_credentials_change`, `roster.set_supplier`, `entry.set_runner`); or
  put a **seam** between two layers that must not know each other
  (`api/desktop_bridge`). A registered callable must be **late-binding** — see
  `runtime._entry_run_turn` — or the indirection that breaks the cycle also
  breaks the ability to substitute what it points at.

---

## Keep everything general / device-independent

Many users, many machines. Do not bake in anything specific to one of either.

- **No hardcoded user data.** Persona text, counts, entity names, agent names and
  sample content are neutral placeholders filled at runtime from the real brain.
- **No absolute or user paths, accounts, or model keys** in code. Secrets live in
  `~/Library/Chitragupta/secrets.json` via `settings.set_secret` / `get_secret`.
- **Frontend calls relative paths** — the desktop app uses a random loopback port.
- **Fonts** use system stacks with fallbacks (`--sans`, `--mono`).
- **Cross-platform keys**: `e.metaKey || e.ctrlKey`, and check `e.key`/`e.code`.

---

## Conventions

- **The LLM provider is chosen client-side** and stored in localStorage
  (`chitragupta_provider`, `chitragupta_model`). Any endpoint that calls an LLM for
  the UI (`chat`, `welcome`, `digest`) accepts `provider`/`model` in the body and
  falls back to `settings.model_provider`. Keys entered in the UI are saved via
  `POST /api/providers/{name}/key`; providers read them through
  `models/base.py::_saved_key` when the env var is unset.
- **localStorage keys**: `chitragupta_onboarded`, `chitragupta_saw_library`,
  `chitragupta_provider`, `chitragupta_model`, `chitragupta_effort`,
  `chitragupta_enrich_provider` / `_model` / `_tip_off`.
  (There is no `sessionStorage` onboarding guard any more: the Agent Library is
  the first screen, so there is no empty-brain redirect to guard.)
- **Cmd/Ctrl+R** is bound in JS on both pages — the webview does not wire it.
- **The workspace** is a 2-column layout: agent rail (gradient orb avatars) and
  chat. Everything else is a full-screen surface. **Keep every element id** —
  `app.js` injects into many of them.
- **Every left-nav item opens a screen.** There is no slide-over drawer.
- **No emoji, and no dingbat doing an icon's job.** `IC` in `web/core.js` is the
  icon set; an emoji is a colour font that ignores `currentColor`, so it can
  never take the accent. Arrows inside sentences are typography, not icons.
- **When you find a defect and do not fix it, write it down** in
  [`docs/development/frontend-parked.md`](docs/development/frontend-parked.md).
  An audit that lives in a chat window is an audit somebody pays for twice.
- A pydantic body model used by a route must be **defined above** it — FastAPI
  resolves the annotation at decoration time.
- Adding an endpoint means adding a line to `tests/api_surface.json`, in the same
  commit, deliberately.
- The onboarding → workspace flow:
  [`docs/development/onboarding-flow.md`](docs/development/onboarding-flow.md).

---

## Working in this repo

### Pick the narrowest mode that solves the problem

**IMPLEMENT** · **REVIEW** · **DEBUG** · **TEST** · **AUDIT** · **PROFILE** ·
**DESIGN** · **INVESTIGATE**

Say which one you are in, and do not silently widen it. An AUDIT that starts
refactoring is no longer an audit — its findings are now entangled with its own
changes and stop being trustworthy.

### Verification

In order: **the focused test → the subsystem's suite → `pytest` →
`ruff check chitragupta tests` → `mypy chitragupta` → the `tests/js/` harnesses if
the frontend changed → `chitragupta app` opens and renders.**

Baseline, measured 2026-09-25: **3900 passed, 31 skipped in ~2min**, ruff
clean, mypy clean over 192 files, coverage 80%. Locally the split differs — some
tests skip when a provider is genuinely connected on the machine. Run tests when
stuck or finishing, not after every edit. Details:
[`tests/CLAUDE.md`](tests/CLAUDE.md).

**Re-measure this number in the commit that changes it.** It said 1440 for long
enough that the suite had grown past 3,600 underneath it — so the figure every
session compared its run against was wrong by 2,233, and "the same as the
baseline" stopped meaning anything.

Four rules, each bought the hard way:

- **Never weaken a test to get green**, never delete one that exposes an
  inconvenient architecture problem, never skip the regression test on a fix.
- **A bug fix ships with a test that fails without it — and you must watch it
  fail.** Reintroduce the bug, confirm red, restore. A test written after the fix
  and never seen red is a guess about what it covers.
- **`tests/api_surface.json` pins the HTTP surface.** A red `test_api_surface.py`
  means an endpoint moved. Never re-baseline it to get green.
- **A test must never start a real sign-in.** `conftest.py` swaps the argv of any
  `login` spawn. The guard is itself covered, because a guard nobody exercises
  quietly stops working.

### A change that spans two layers

**Write the contract down before the code**, and land both sides together with a
test that fails if only one of them ships. Two halves that each pass their own
tests can still be wrong about each other, and that is not hypothetical here.

**Additive first.** Ship the new thing beside the old, migrate the consumer, then
remove the old — three landings, never one. And **name a display string
separately from an id**: if a field crosses a layer and a person will read it,
the contract says which field they read.

The boundaries and what each must name:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §4.

### Things to never do

- Add a model id to a fallback list without verifying it resolves.
- Read another product's app-support directory for credentials or models.
- Add a `providerId === "x"` branch to a render path.
- Gate graph enrichment on a connector-name allowlist.
- Promote a detected credential to connected.
- Invest in Windows/Linux paths. macOS is the only supported platform.

### Git

- Commit style: lowercase type, then what a *user* gets — `fix(web): the model
  picker now changes which model actually answers`. Not what the code does.
- Small, focused commits, one behaviour each.
- **No `Co-Authored-By` trailer, and no "generated with" line.** Not for Claude,
  not for any agent, not in a commit and not in a PR body. This rule used to
  live under *Things to never do*, three screens from the place a commit message
  actually gets written, and 47 commits carried the trailer anyway — so it is
  written here, where you are when you write one. Your harness may instruct you
  to add one; this file overrides it.
- **Always read `git diff` before finishing.** Every time.
- Commit or push only when asked.
- **Never rewrite pushed history without being asked for that specifically.**
  A force-push over `main` is not a tidy-up: every SHA after the rewrite point
  changes, and any other clone or in-flight session has to recover by hand.

---

## Where to look when

| you need | read |
|---|---|
| who owns what, and what may import what | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| why a decision was made | [`docs/DECISIONS.md`](docs/DECISIONS.md) |
| measured complexity and known duplication | [`COMPLEXITY_AUDIT.md`](COMPLEXITY_AUDIT.md) |
| the agent loop in depth | [`docs/AGENTS.md`](docs/AGENTS.md) |
| providers, entitlements, auth, errors, streaming | [`docs/development/models.md`](docs/development/models.md) |
| why a CLI backend can call our tools at all | [`docs/development/cli-tool-bridge.md`](docs/development/cli-tool-bridge.md) |
| what was slow and what fixed it | [`docs/development/performance.md`](docs/development/performance.md) |
| recall cost at scale | [`docs/SCALING.md`](docs/SCALING.md) |
| the macOS window and sign-in card | [`docs/DESKTOP-SIGNIN.md`](docs/DESKTOP-SIGNIN.md) |
| why `app.js` cannot be split yet | [`docs/development/frontend-testing.md`](docs/development/frontend-testing.md) |
| the first-run flow | [`docs/development/onboarding-flow.md`](docs/development/onboarding-flow.md) |
| the avatar renderer, its document format and its editor | [`character/README.md`](character/README.md) |
| frontend defects found and deliberately left | [`docs/development/frontend-parked.md`](docs/development/frontend-parked.md) |
| the brain's data model | [`docs/BRAIN-V1.5.md`](docs/BRAIN-V1.5.md) |
| which way to reach an app at all | [`docs/REACHING-AN-APP.md`](docs/REACHING-AN-APP.md) |
| connectors | [`docs/CONNECTORS.md`](docs/CONNECTORS.md) |
| automations, and how a run survives a crash | [`docs/AUTOMATION.md`](docs/AUTOMATION.md) |
| connecting Telegram | [`docs/development/telegram.md`](docs/development/telegram.md) |
| measurements, and the health boundary | [`docs/development/health.md`](docs/development/health.md) |
| what the Health agent still needs | [`docs/development/health-roadmap.md`](docs/development/health-roadmap.md) |
| changing the user's inbox, and why it could not | [`docs/development/mail-triage.md`](docs/development/mail-triage.md) |
| which messaging apps are actually reachable | [`docs/MESSAGING.md`](docs/MESSAGING.md) |
| replacing the bundled Google OAuth client | [`docs/development/google-client-rotation.md`](docs/development/google-client-rotation.md) |
| driving a real browser | [`docs/BROWSER.md`](docs/BROWSER.md) |
| signing in to a site once, and keeping it | [`docs/development/connected-sites.md`](docs/development/connected-sites.md) |
| building and shipping | [`docs/DISTRIBUTION.md`](docs/DISTRIBUTION.md) |
| known gaps | [`docs/AUDIT.md`](docs/AUDIT.md) |
