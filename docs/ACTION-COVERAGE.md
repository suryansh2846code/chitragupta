# Action coverage — the roadmap

> How many real things Chitragupta can *do*, not just read, understand or
> recommend. Written 2026-09-19 against `a32cf48`.
>
> The understanding half of this product is strong. This file is the plan for
> making the execution half match it — and the argument for why that is a
> change to **one primitive** rather than a list of a hundred new verbs.
>
> Companion to [`AGENTS.md`](AGENTS.md) (how a turn runs) and
> [`ROADMAP.md`](ROADMAP.md) (everything else that is wanted).

---

## The ladder

Every capability sits somewhere on six rungs. The rung is not a feature — it is
how far Chitragupta gets before it hands the problem back to the user.

| | | |
|---|---|---|
| **1** | Understand | *"Rahul is waiting for your proposal."* |
| **2** | Recommend | *"You should send Rahul the proposal."* |
| **3** | Prepare | *"I've drafted the proposal email."* |
| **4** | Act | *"Sent."* |
| **5** | Verify | *"Sent successfully at 3:42 PM."* |
| **6** | Remember | *"I recorded that the proposal was sent."* |

A Chief of Staff lives at 6. Anything below 4 is an assistant that makes work
rather than removing it — and the gap between 3 and 4 is smaller than it looks,
because **3 is where most of the value is and 3 is the rung we skipped.**

### Where each domain sits today

Measured against the code, not against intent.

| Domain | Rung | Why it stops there |
|---|---|---|
| **Health / training** | **6** | `log_measurement`, `log_workout` — proposed, editable, executed, stored. The most complete domain in the product, and the only one with no screen to show for it |
| **Email** | **4⁻** | `send_email` and `mail_triage` work. **No draft** — rung 3 is missing entirely. No reply-in-thread, no forward, no attachment (`gmail.py:181` is a bare `MIMEText`). Verify fires only on failure. Remember writes to the conversation, never the brain |
| **Calendar** | **4⁻** | `create_event` only. No reschedule, cancel, attendee change, location, notes — `gcal.py` has one write method |
| **Messaging** | **4 (blocked)** | `message_send` is registered and `telegram.py::send` is written — and **there is no UI to connect Telegram**, so the action cannot fire. An agent taught an action it cannot take |
| **Files** | **3 (local)** | `write_file` inside granted roots. No rename, move, convert, or attach-to-anything. A file Chitragupta writes cannot leave the machine |
| **GitHub · Linear · Notion · Drive** | **1** | Read-only sync. Zero actions. The tokens are stored and the SDKs installed; the write half was never built |

**Thirteen of fifteen connectors are read-only.** Only Gmail, GCal, Slack,
Telegram and MCP can push anything back out.

---

## The loop is the primitive

The seven steps below are not a workflow to remember. They are **seven slots on
an action**, and the reason this roadmap is tractable is that six of them
already exist in some form — just scattered, and never all in one place.

```
UNDERSTAND → PLAN → SHOW → ASK → ACT → VERIFY → REMEMBER
```

| Step | Today | Where |
|---|---|---|
| UNDERSTAND | ✅ strong | `store.search()`, canonical layer, graph |
| PLAN | ⚠️ advisory | `update_plan` — the agent can ignore its own plan, and the plan knows nothing about actions |
| SHOW | ⚠️ partial | The action card. **One** action per card; only `log_workout` is editable (`chat.js:288`) |
| ASK | ✅ strong | `permissions.check()` + `approvals` — the best-argued code in the repo |
| ACT | ✅ good | `actions.execute()` — 8 types, registry-driven, `at` auto-schedules |
| VERIFY | ⚠️ half | `outcomes.settle()` — deliberately asymmetric. A failure gets a retry; **a success is silent** |
| REMEMBER | ❌ missing | `outcomes.record()` appends to `AgentMemory` — the *conversation*. The brain never learns it. No open loop is ever closed by an action |

### The change

`actions.REGISTRY` entries are `{handler, label, fields}`. Grow them into the
loop:

```python
@dataclass(frozen=True)
class ActionSpec:
    handler:  Callable[[dict], dict]
    label:    str
    fields:   list[str]
    risk:     Risk                          # NEW — green | amber | red
    preview:  Callable[[dict], Preview]     # NEW — SHOW, before approval
    verify:   Callable[[dict, dict], dict]  # NEW — VERIFY, after execution
    remember: Callable[[dict, dict], None]  # NEW — REMEMBER, into the brain
    undo:     Callable[[dict, dict], dict] | None = None
```

Every new action then arrives with its own preview, its own verification and its
own memory write — or it does not ship. The registry stops being a dispatch
table and becomes the contract.

### And the second primitive: `ActionPlan`

Today one `<action>` tag is one card is one Confirm. *"Clear the emails that
don't need my attention"* is seventeen cards.

```python
@dataclass(frozen=True)
class ActionPlan:
    rationale: str            # what the agent understood
    steps:     list[Action]   # ordered; each carries its own risk
    def risk(self) -> Risk:   # the highest rung any step reaches
```

One card, N actions, one approval, results settled together:

```
I found 17 emails.

  9 newsletters      → archive          🟢
  5 informational    → mark read        🟢
  2 need replies     → draft responses  🟢
  1 important        → left for you

9 actions ready · nothing leaves your account

                              [ Approve ]
```

`mail_triage`'s `_items` (`actions.py:95`) already proves the multi-item shape
works end to end. `ActionPlan` is that generalised, and it is what makes the
whole roadmap feel like one feature instead of forty.

---

## Risk tiers

Today the gate is binary: an allow-listed recipient runs, everything else asks.
That is correct and too coarse — it cannot tell *drafting* an email from
*sending* one, so it treats both as outbound and asks twice.

Three tiers, declared per action in the registry:

### 🟢 Green — may run unattended

Reaches nobody. Reversible or inconsequential. **No approval, ever** — it appears
in the action log and that is the audit.

```
create_task · create_draft · remember · organize_memories · summarize
add_internal_note · rename_file (granted roots) · create_open_loop
log_measurement · set_reminder
```

**`create_draft` is the important one.** A draft reaches nobody, so it needs no
allow-list and no approval — which means an agent can do the *preparation* rung
overnight without a single tap. An agent that fills your drafts folder while you
sleep is worth more than one that queues nine approvals for Monday.

### 🟡 Amber — ask, and remember the answer

Reaches a person or changes somebody else's system. Approval required — but a
repeated approval of the *same shape* may be promoted to a standing grant.

```
send_email · send_message · create_event · update_event · cancel_event
mail_triage · github_comment · linear_create_issue · notion_append
write_file · schedule_send
```

Promotion is the rung the current design is missing. `permissions.py:73` argues
`mcp_action` can never be allow-listed because *"a Slack tool's `channel` and a
Jira tool's `assignee` are not the same field and never will be."* That is right
about **recipients** and wrong about **tools**: `(server_id, tool)` is a stable,
comparable, revocable key. "Always allow `linear:create_comment`" is coherent in
a way "always allow this argument blob" is not.

The UI pattern already exists — the **Always allow \<address\>** button that
appears on an approval card when `blocked` is non-empty. Extend it to
`(server_id, tool)` and the third identical approval offers the grant instead of
asking a fourth time.

### 🔴 Red — always explicit, never promotable

```
delete_data · send_money · make_purchase · publish_publicly
send_to_new_external_recipient · modify_account_settings
browser_act (Phase 5)
```

No grant promotes these. No routine may take them unattended — this is what
`permissions.NEVER_UNATTENDED` becomes, with the reasoning it already has.

> The whole point: **AI that acts without becoming scary.** Green is where the
> unattended value lives; red is where the fear lives; the design job is keeping
> them from touching.

---

## The 18 jobs

Breadth is not the goal. These are the things people repeatedly ask a Chief of
Staff for, and each one is an **acceptance test** — a phase ships when its jobs
run end to end through all seven steps, not when its endpoints exist.

| # | The user says | Rungs needed | Phase |
|---|---|---|---|
| 1 | "Clear the emails that don't need me" | 1→6 | 1 |
| 2 | "Draft replies to anything waiting on me" | 1→3 | 1 |
| 3 | "Reply to this thread saying X" | 1→6 | 1 |
| 4 | "Send the latest proposal to Rahul" | 1→6 | 3 |
| 5 | "Follow up with whoever hasn't replied" | 1→6 | 1 |
| 6 | "Move tomorrow's client meeting to Friday afternoon" | 1→6 | 2 |
| 7 | "Cancel Thursday and tell everyone why" | 1→6 | 2 |
| 8 | "Find a time with Rahul next week" | 1→6 | 2 |
| 9 | "Prep me for my next meeting" | 1→3 | 2 |
| 10 | "Tell Rahul I'll send it tonight" | 1→6 | 3 |
| 11 | "Chase this in two days if nothing happens" | 1→6 | 1 |
| 12 | "What am I waiting on, and who's waiting on me?" | 1→2 | 0 |
| 13 | "Turn this thread into a task" | 1→6 | 0 |
| 14 | "File an issue for this" | 1→6 | 4 |
| 15 | "Comment on that PR for me" | 1→6 | 4 |
| 16 | "Write this up as a document" | 1→3 | 3 |
| 17 | "Log: 5×5 squats, last one a grind" | 1→6 | ✅ done |
| 18 | "What did you do this week?" | 1→2 | 0 |

Job 17 already works. Job 12 and 18 need no new actions at all — only the
surfaces Phase 0 builds.

---

## Phases

### Phase 0 — The primitive · **mostly shipped**

**No new domain actions.** Every existing action gets better, and everything
after this becomes additive.

| Ship | Touches | State |
|---|---|---|
| `ActionSpec` with `risk` / `verify` / `remember` / `undo` | `actions.py` | ✅ The loop is a contract now, not a convention. An action that ships without the slots stops at rung 4, and the registry is the one place that is visible |
| Three-tier risk gate, **derived** from the registry | `permissions.py` | ✅ `NEVER_UNATTENDED` / `OUTBOUND_ACTIONS` / `RECIPIENT_KINDS` were three hand-kept literals; they are now read off `spec.risk`. Also **fails closed** on an action nobody declared, which used to fall through to "reaches nobody" and run |
| Editable cards driven by `REGISTRY[type].fields` | `chat.js`, `/api/actions/catalog` | ✅ `EDITABLE = { log_workout: true }` is gone. Every scalar field the registry declares is correctable — `send_email` included. Structured values keep their own editors |
| **VERIFY on success**, not only failure | `actions.py`, `outcomes.py`, `gmail.py`, `gcal.py` | ✅ `message_sent_at` / `event_exists` read the thing back. The card says *confirmed 3:42 PM* only when it was really checked; an unverifiable send reports **unverified, never failed** |
| **REMEMBER**: outcome → brain, and close the named open loop | `actions.py` | ✅ Episodic memory, `source="action"` — the store every agent reads, not one agent's conversation. A loop closes only on an explicit `loop_id`; fuzzy description matching was rejected |
| **Undo** where the inverse exists | `actions.py`, `gcal.py` | ✅ `mail_triage` (label pairs swapped), `create_event` (new `delete_event`), reminders, routines, scheduled sends. `send_email` declares **no** undo and the card offers no button |
| **The action log** | `action_log.py`, 4 routes, Inbox panel | ✅ Its own `actions.db` with WAL and a busy timeout. *What your agents did* sits under *Waiting on you* — the same subject the panel already promises, rather than a second place to forget about. Undo lives here after the card is gone, which is when a person actually notices the date was wrong |
| `ActionPlan` — batch, one approval, settled together | `actions.py`, `prompt.py`, `chat.js` | ✅ A model wraps proposals in `<plan rationale="…">`. One card, one button, run **in order, stopping at the first failure** — and every step that never started is named, because "6 of 9" does not say which three. Risk is the **worst** step's, so nine green archives beside one amber send is an amber card |

**Closes jobs 13 and 18.** Job 12 (*what am I waiting on*) still needs the
open-loops surface from `AUDIT.md` A9.

#### The line a plan does not cross

`parse_actions` still finds every `<action>` inside a `<plan>`, so an
*unattended* routine keeps judging each one on its own through
`approvals.run_or_queue`. The wrapper groups a decision a person is present to
make; it never widens one — and a `<plan>` is written by the **model**, so if it
could hide actions from the unattended gate it would be a way to smuggle one
past the check. `test_action_plans.py` breaks that on purpose.

> Undo and the audit trail are what make everything in Phases 1–5 safe to want.
> Both exist now.

#### What Phase 0 deliberately did *not* change

`mail_triage` stays **RED**. It has a real undo now, and that is a reason to
approve one more comfortably — not a reason to stop asking. A triage agent's
whole input is text strangers sent, and *archive everything from the bank* is a
sentence an email can contain. `test_action_loop.py` pins it so the promotion
cannot happen quietly; it needs its own argument and its own commit.

This matters because an earlier draft of this file claimed job 1 was "entirely
green". Archive and mark-read are green *in the tier model*; they are not green
while the list of what to archive is written by whoever sent the mail. The tier
describes the effect, and the gate has to describe the input too.

---

### Phase 1 — Email to rung 6 · ~2 weeks

The highest-frequency domain, and the one with a hole at rung 3.

| Action | Risk | Note |
|---|---|---|
| `create_draft` | 🟢 | **Start here.** `gmail.drafts.create`. Reaches nobody → unattended-safe → the overnight-preparation unlock |
| `reply_in_thread` | 🟡 | Needs `threadId` + `In-Reply-To`; `send_email` has neither today |
| `forward` | 🟡 | |
| `attach` | 🟡 | `gmail.py:181` is `MIMEText` — needs `MIMEMultipart`. Prerequisite for job 4 |
| `schedule_send` | 🟡 | `execute()`'s `at` path (`actions.py:377`) already does this — surface it on the card |
| `label` / `move` | 🟡 | `modify_messages` already takes add/remove; the verbs exist, the action type doesn't |
| `create_followup` | 🟢 | An open loop with a `due_at` — closes automatically when the reply lands |

**Acceptance — job 1, end to end:**

```
"Clear the emails that don't need my attention"
  → 17 found, classified, one card
  → 9 archive 🟢 · 5 mark read 🟢 · 2 drafts 🟢 · 1 left alone
  → one Approve · verified · recorded in the brain · undo for 10s
```

Note that in the tier model **this entire job is green** — nothing leaves the
account. It could run unattended on a routine, and the user reads the action log
in the morning. That is the product.

---

### Phase 2 — Calendar to rung 6 · ~1.5 weeks

`gcal.py` has exactly one write method. Everything below is the same SDK shape.

`update_event` · `cancel_event` · `add_attendee` · `remove_attendee` ·
`change_location` · `add_notes` — all 🟡.

**Acceptance — job 6:** *"Move tomorrow's client meeting to Friday afternoon"* →
find it → read attendees' free/busy → propose a concrete slot → show the change
as a diff → approve → update → verify → record → notify attendees.

The hard part is not the API, it's **candidate selection**: rung 3 for calendar
means proposing a *specific* time, not asking the user to pick one. Free/busy is
already reachable through the Google auth that's in place.

---

### Phase 3 — Messaging and Files · ~2 weeks

**Messaging.** Start with the one-hour fix: **a connect-Telegram UI.** Six
endpoints and a complete Telethon flow are already mounted (`telegram_auth.py`)
with no door. Shipping the door activates `message_send`, which is already
written, already registered, and currently unreachable.

Then: `draft_message` 🟢 · `schedule_message` 🟡 · `create_followup` 🟢.

**Files.** Today `write_file` writes inside granted roots and the result can
never leave the machine.

`create_file` 🟢 · `rename` 🟢 · `move` 🟡 · `convert` 🟢 · `generate_document`
🟢 · **`attach_to_email`** 🟡.

**Acceptance — job 4:** *"Take the latest proposal and send it to Rahul"* → find
candidates → **show which version and why** (rung 3 is the version check, not the
attachment) → draft with it attached → approve → send → verify → record.

---

### Phase 4 — Work surfaces · ~2 weeks

Four connectors that read today and cannot write.

| Connector | Actions | Risk |
|---|---|---|
| GitHub | `comment`, `create_issue`, `assign`, `label`, `close_issue` | 🟡 |
| Linear | `create_issue`, `comment`, `assign`, `move_state` | 🟡 |
| Notion | `append_block`, `create_page` | 🟡 |
| Drive | `create_doc`, `share` | 🟡 / 🔴 (share) |

Ship alongside **per-tool grants** (the amber promotion above) — this is the
phase where the number of approvals gets annoying enough to justify the rung,
and where `(server_id, tool)` proves itself as a key.

`create_branch` / `create_pr` / `review_pr` are deliberately **out of scope**.
They are a different product, and the 18 jobs do not need them.

---

### Phase 5 — The long tail · later

Browser acting. `origins.py:226`: *"`may_act` defaults to False and no code path
in this package flips it."* `browse_tools.py:10`: *"`browse_click`, `browse_type`
and `browse_submit` do not exist here yet."*

The largest capability unlock available — an agent that can operate a site you
are signed into is no longer bounded by which vendors ship an API. It is also
permanently 🔴: per-origin `may_act`, never unattended, a visible window so the
user watches it happen, and per-action confirmation rather than per-session.

**Do not start this until Phase 0 exists.** Undo and an audit log are what make
it survivable.

---

## Where this lands

| | today | P0 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|---|
| Email | 4⁻ | 4 | **6** | 6 | 6 | 6 |
| Calendar | 4⁻ | 4 | 4 | **6** | 6 | 6 |
| Messaging | 4 (blocked) | 4 | 4 | 4 | **6** | 6 |
| Files | 3 | 3 | 3 | 3 | **5** | 5 |
| Work surfaces | 1 | 1 | 1 | 1 | 1 | **6** |
| Health | 6 | 6 | 6 | 6 | 6 | 6 |
| | | | | | | |
| **Action layer** | **7.0** | **8.2** | **8.6** | **8.9** | **9.2** | **9.5** |

Phase 0 is three weeks and buys the largest single jump — because what holds the
layer back today is not what the agents *can* do. It is that a user cannot
correct, reverse, or review what they did.

---

## The rule this file exists to protect

> Ship the 18 jobs until they are boring. Then consider the nineteenth.

A hundred actions that each work most of the time is a worse product than
eighteen that always do. Every phase above is scoped to its jobs, and a phase is
not done when its endpoints respond — it is done when its jobs run from
UNDERSTAND to REMEMBER without a person filling a gap in the middle.
