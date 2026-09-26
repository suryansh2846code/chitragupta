# `chitragupta/automation/` — the engine

GOAL → TRIGGER → CONTEXT → CONDITIONS → PLAN → APPROVAL → ACTIONS →
VERIFICATION → RECOVERY → COMPLETION / ESCALATION, with every state on disk
before it is acted on. Full design: [`docs/AUTOMATION.md`](../../docs/AUTOMATION.md).

**It sits above `agents/` and below `api/`.** An automation drives an agent
turn, so `agents/` must not import this package. Its durable state lives in
`core/automation_store.py` and `core/events.py` — two layers need those rows
and neither may import the other.

- **There is one permission system and it is not here.** Every side effect goes
  through `agents/approvals.run_or_queue` → `agents/permissions.check`. An
  automation may never widen what an interactive agent may do. A semantic
  condition returning "this looks safe" is **data**, never authorization: it is
  a bool and a float, and the gate never reads either.
- **No connector is named in `triggers`, `router`, `conditions`, `context` or
  `executor`.** A Gmail message, a GitHub push, a webhook and one automation
  finishing are all `Event`s. The mapping lives in `sources.py`, as a dict.
  `test_no_connector_is_named_in_the_automation_core` walks the AST — docstrings
  may use examples; code may not.
- **The claim is taken before the effect, not after.** A claim written
  afterwards proves nothing about a process that died in between, which is the
  case it exists for. A handler that *raised* leaves the claim `CLAIMED` — the
  honest "we tried and do not know" — and the retry **verifies instead of
  repeating**. A handler that returned `ok: False` releases it, because that is
  a statement that nothing happened.
- **`BLOCKED` is not `FAILED`.** Blocked is the system correctly declining: a
  condition was false, a permission said no, a limit was hit. Collapsing them
  turns "your automation correctly did nothing today" into a red badge, and a
  user who sees enough of those stops reading them.
- **The deadline bounds active work, not waiting.** An approval takes hours.
  Checking wall-clock through `WAITING_FOR_APPROVAL` or `RETRYING` killed every
  run that waited; each active stretch is bounded instead, and the retry count
  bounds how many stretches there can be.
- **Untrusted content is fenced and the fence cannot be closed from inside**
  (`core/provenance.py`). The goal is stated first and in our voice. Neither
  stops a model being persuaded — the gate afterwards is why that is survivable.
- **`executor.py` imports nothing it acts through.** Every dependency is a
  callable on `Deps`, wired in `engine.py`. That is what keeps the dependency
  direction honest and what makes a crash, an approval answered four minutes
  later and a verification that fails twice all testable without a model.
- **A webhook is authenticated and normalised, then stops being special.**
  `webhooks.py` owns the door: a signature compared in constant time, a bounded
  replay window, a size limit, and a 503 rather than an open door when a
  provider's secret is missing. What comes through it is an `Event` and goes to
  the same `engine.ingest` a sync uses. Provider knowledge lives in a
  `Verifier`; `receive` may not name one, and `test_automation_architecture.py`
  reads the AST to check.
- **A reminder and a scheduled action are runs.** `engine.run_once` fills the
  plan in from what the user already decided, so no model is asked. The spec of
  that ephemeral automation is stored **on the run** — it is never a routines
  row, so recovery has nowhere else to read its policy and limits from.
- **`pre_approved` is the one flag that lets a refused action run**, it is set
  in exactly one place, and it means "the user confirmed this exact content when
  they scheduled it" — not "skip the gate". The refusal is still recorded on the
  run, so history says which approval it is standing on.
- **A retry that has no work to retry goes back to the plan.** A provider blip
  during the agent turn leaves a run with no action steps; sending that to
  `EXECUTING` found nothing pending and **completed**, reporting success for an
  automation that did nothing.
- **What an automation says about itself only ever narrows.** `Execution` holds
  the model it runs on, the apps it may reach, whether it may read pages or send
  anything, and how often to check. Every field substitutes or takes capability
  away — none of them widen, because an automation may never be able to do
  something an interactive agent may not. The connector ceiling
  (`connector_grants.only_these`) is checked **first** in `may_use` and beats a
  stored grant and an unrestricted agent both; the page ban sits on top of
  `NEVER_UNATTENDED_TOOLS`, which it cannot lift; the sending switch refuses
  before the gate is asked, and the gate is still asked after.
- **A watch may ask for its source to be checked sooner**, and the scheduler
  honours the tightest request across every enabled automation —
  `engine.wanted_sooner` collects it, `Scheduler.fast_check` spends it. Only
  faster, never slower: an automation cannot slow a sync the rest of the app
  depends on. It costs the user API calls, so it is off unless asked.
- **Readiness is a report, never a decision.** `readiness.py` answers whether an
  automation can run *before* it is left alone for a month — the agent exists, a
  model is connected, the trigger says enough to match, the apps are set up. It
  grants nothing and stops nothing, and `asks` is not a fault: "draft it and let
  me look" is a good automation, and a screen that called it broken would teach
  the user to ignore the screen. It may not import `connectors/`, so the app
  state is injected by the route that has both.
- **A quiet run is not news.** `worth_delivering` decides what reaches the
  Inbox: by default a run that acted or stopped, never one that looked and found
  nothing — a watch polling every two minutes would otherwise bury the one
  result that mattered under its own reports. `never` means never, including
  the bad ones, because a setting that is quietly overridden stops being
  believed.
- Every new capability gets a case in `evaluation.py`, graded on **what reached
  the world**, not on what the agent said.

Rules for all of it: [`/CLAUDE.md`](../../CLAUDE.md).
