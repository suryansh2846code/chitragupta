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
- Every new capability gets a case in `evaluation.py`, graded on **what reached
  the world**, not on what the agent said.

Rules for all of it: [`/CLAUDE.md`](../../CLAUDE.md).
