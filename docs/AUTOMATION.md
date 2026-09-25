# Automation — the engine, and what it was built on

> What an automation *is*, how a run survives the things that kill one, and
> which existing subsystem owns each part. Companion to
> [`AGENTS.md`](AGENTS.md) (the turn loop),
> [`ACTION-COVERAGE.md`](ACTION-COVERAGE.md) (what an action must do) and
> [`ARCHITECTURE.md`](ARCHITECTURE.md) §3 (which way imports may point).

---

## 1. The audit this was built from

Most of an automation engine was already here. The parts that were missing were
missing *entirely*, which is why they had to be built rather than extended.

### What already existed, and is reused unchanged

| subsystem | what it already does | used for |
|---|---|---|
| `actions.py` · `ActionSpec` | every action declares its `risk` tier, its `recipient_kind`, whether it is `schedulable`, and its own `verify` / `remember` / `undo` | **the action layer, whole.** No second registry. |
| `actions.run_now` | the single chokepoint: handler → verify → remember → log | every side effect an automation causes goes through it |
| `agents/permissions.py` · `check()` | fails closed on an unknown action, reads the tier off the spec, judges recipients against a persisted allow-list | **the permission layer, whole.** No second gate. |
| `agents/permissions.as_unattended()` | a `ContextVar` marking "nobody is watching" that the *tools* see too | wraps every automation turn |
| `agents/approvals.py` | `run_or_queue`, the `action_approvals` table, approve/reject | the approval store. The run now *resumes* from it. |
| `action_log.py` | per-action audit: params, result, verified, reversible, undone | per-action history, unchanged |
| `connectors/base.py` · cursor + `SyncResult` | incremental sync with a persisted watermark | how events get produced without re-reading everything |
| `browser/page.py` · `_defuse` | a quarantine fence untrusted text cannot close from the inside | generalised into `core/provenance.py` |
| `core/db.py` · `_ADDED_COLUMNS` idiom | additive `ALTER TABLE`, idempotent | every new table follows it |

### What existed but was not enough

**`routines.py` was a scheduled prompt runner, not an automation engine.**

```python
def sweep(new_email_count: int = 0) -> None:
    for r in store.enabled():
        if r["trigger"] == "daily":       ...
        elif r["trigger"] == "schedule":  ...
        elif r["trigger"] == "new_email" and new_email_count > 0: ...
```

Every gap follows from that shape:

* **Trigger matching was an `if/elif` chain in the engine.** Adding a connector
  event meant editing the engine — the thing requirement 16 exists to prevent.
* **`new_email_count` is a count, not an event.** There was no event identity,
  so no deduplication was possible even in principle; a re-sync re-delivered.
* **A run was one function call.** `run_routine` runs a turn, parses actions,
  calls `run_or_queue`, returns a string into `routines.last_result`. A crash
  anywhere in it loses everything, and there is nothing to resume.
* **Approval paused the *action*, never the *run*.** `run_or_queue` queues and
  returns; the routine has already finished by the time a human taps Approve.
  `outcomes.settle` then tells the agent what happened — a different thing from
  continuing the work.
* **No conditions.** "only if Z" had nowhere to live but the instruction text,
  where it is a suggestion to a model rather than a gate.
* **No idempotency, retries, escalation, concurrency control or loop
  protection.** An exception was logged and dropped.
* **The trigger context was pasted into the prompt unfenced** — `ctx = "NEW
  EMAIL(S) that just arrived:\n" + ...`. That is the injection surface, and the
  fence that closes it already existed one directory away, for the browser.

### What was duplicated

Three independent "is it due yet" implementations, all polled from
`scheduler._loop`: `routines.sweep` (daily/interval/email), `scheduled.py`
(one-shot `fire_at`), `reminders.py` (one-shot `fire_at`). And two "what
happened" records: `action_log` rows, and a JSON string in
`routines.last_result`.

---

## 2. The shape

```
 connector / scheduler / agent / api
            │  emits
            ▼
      core/events.py            Event(id, kind, source, subject, data, trust)
            │                   ── deduplicated on (source, external_id)
            ▼
   automation/router.py         which automations care?
            │
            ▼
  automation/triggers.py        a registry, not an if/elif
            │
            ▼
 automation/conditions.py       deterministic first, semantic only if needed
            │
            ▼
   core/automation_store.py     Run created: PENDING
            │
            ▼
  automation/executor.py        the driver, resumable from any state
            │
   ┌────────┴──────────────────────────────────────┐
   │ plan → approve → act → verify → retry → done  │
   └───────────────────────────────────────────────┘
            │ every side effect through
            ▼
   actions.run_now  ·  agents/permissions.check  ·  agents/approvals
```

**The engine is a new top-level package** (`chitragupta/automation/`), sitting
where `routines.py` sat: above `agents/`, below `api/`. It could not go in
`agents/` — an automation drives an agent turn, so `agents/` must not import it
— and it could not go in `core/`, which may not reach up to anything.

**Its durable state goes in `core/`**, because two layers need the rows and
neither may import the other: the engine writes them and the API reads them.
That is the same rule `core/routine_store.py` was moved down for.

**Connectors emit events into `core/events.py`, a leaf.** They never import the
automation engine, and the engine never imports a connector. That is the whole
of requirement 16: a new connector event is a new `Event`, not a new branch.

---

## 3. The state machine

```
            ┌──────────────────────────── CANCELLED (user)
            │
 PENDING ──► RUNNING ──► WAITING_FOR_APPROVAL ──► EXECUTING ──► VERIFYING ──► COMPLETED
               │  │                                   │             │
               │  └────────────► BLOCKED              │             │
               │     (condition false, permission denied)           │
               │                                      ▼             ▼
               └──────────────────────────────► RETRYING ──► ESCALATED / FAILED
```

Every transition is written to SQLite before it is acted on. A process that
dies between two transitions restarts in the earlier one, and the step ledger
says which steps already completed — so recovery is *resumption*, not replay.

`BLOCKED` and `FAILED` are different answers and are kept different:
**BLOCKED** is "the system correctly declined" (a condition was false, a
permission said no, a limit was hit). **FAILED** is "we tried and could not".
Only one of them is a bug.

---

## 4. The invariants

These are load-bearing. Each is enforced by a test named after it.

1. **There is one permission system.** Automation calls
   `agents/approvals.run_or_queue`, which calls `agents/permissions.check`.
   An automation may never widen what an interactive agent may do. A semantic
   condition returning "this looks safe" is *data*, never authorization.
2. **Unknown actions fail closed.** Inherited from `permissions.check`, and
   re-asserted at the automation boundary because an automation is the caller
   most likely to meet an action name a model invented.
3. **A side effect is claimed before it is caused.** The idempotency claim is
   written and committed *before* the handler runs, so a crash mid-action
   leaves evidence that the attempt happened. Recovery verifies rather than
   re-runs.
4. **External content is DATA.** Anything whose trust level is below
   `CONNECTED_SOURCE` is fenced with `core/provenance.py` before it reaches a
   model, and the fence cannot be closed from the inside. An email cannot
   redefine the goal, the conditions, or the permission policy.
5. **Every run is bounded.** Wall-clock, actions, retries and model iterations
   all have ceilings, and exceeding one is `BLOCKED`, not an exception.
6. **A run carries its lineage.** An event produced by automation A's action
   carries A's run id. A depth limit and a self-trigger check make the
   A → event → A loop terminate.
7. **Success means verified.** An action whose spec has a `verify` and whose
   verification did not pass is not `COMPLETED`.

---

## 5. What migrated

`routines` is the same table and the same `/api/routines` endpoints. An
automation *is* a routine with more columns, all added additively — a user
upgrading in place keeps their routines, which keep running.

`routines.sweep()` is gone as a trigger matcher: the scheduler now emits
`schedule.tick` and `connector.synced` events, and the router decides. The
function remains as a thin shim so anything calling it still works.

`scheduled.py` (one-shot actions) and `reminders.py` are **not** migrated. They
are one-shot notifications with no goal, no conditions and no verification, and
folding them in would have meant building a run around a `desktop_notify`. They
are listed under *Remaining limitations*.
