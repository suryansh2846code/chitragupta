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
| **Messaging** | **4** | ~~no UI to connect Telegram~~ — **shipped.** `message_send` can now actually fire: the four-screen sign-in lives in `connectors.js`, driven by `GET /api/telegram/status` rather than by its own step counter |
| **Files** | **4** | `find_file` searches the granted folders and reports each match's **modified date**, so "the latest" is a claim the user can check. `attach=` sends it. Still no rename, move or convert |
| **GitHub** | **5** | `github_comment` (reversible) and `github_create_issue` (not — GitHub has no delete-issue API). Allow-listed **by repository**, the first grant that is a place rather than a person |
| **Linear · Notion · Drive** | **1** | Read-only sync. Zero actions. The tokens are stored and the SDKs installed; the write half was never built |

~~Thirteen~~ **Twelve of fifteen connectors are read-only.** Only Gmail, GCal, Slack,
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
send_email · send_message · create_event · github_comment
linear_create_issue · notion_append · write_file · schedule_send
```

> **`update_event` and `cancel_event` were predicted here and shipped RED.**
> The test is not "does it reach somebody" but "can this tier's machinery
> *see* who". `create_event` is amber because its attendees are on the card.
> The people a *move* reaches are on the existing event, not in the params —
> so `recipients_of` finds none, and amber would have read that as "reaches
> nobody" and let an unattended agent rearrange a diary full of other
> people's mornings. `mail_triage` sits in red for the mirror-image reason.

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

> **Built.** `mcp_action` moved 🔴 → 🟡 with `TOOL_RECIPIENT` as its list and
> `connector_tool_key()` as its key. This is the one change in this whole
> document that *relaxes* a gate, so the argument and its limits are written
> out in [Per-tool grants](#per-tool-grants) rather than left implicit.

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
| 1 | "Clear the emails that don't need me" | 1→6 | ✅ done |
| 2 | "Draft replies to anything waiting on me" | 1→3 | 1 |
| 3 | "Reply to this thread saying X" | 1→6 | 1 |
| 4 | "Send the latest proposal to Rahul" | 1→6 | ✅ done |
| 5 | "Follow up with whoever hasn't replied" | 1→6 | ✅ done |
| 6 | "Move tomorrow's client meeting to Friday afternoon" | 1→6 | ✅ done |
| 7 | "Cancel Thursday and tell everyone why" | 1→6 | ✅ done |
| 8 | "Find a time with Rahul next week" | 1→6 | ✅ done |
| 9 | "Prep me for my next meeting" | 1→3 | 2 |
| 10 | "Tell Rahul I'll send it tonight" | 1→6 | 3 |
| 11 | "Chase this in two days if nothing happens" | 1→6 | ✅ done |
| 12 | "What am I waiting on, and who's waiting on me?" | 1→2 | ✅ done |
| 13 | "Turn this thread into a task" | 1→6 | 0 |
| 14 | "File an issue for this" | 1→6 | ✅ done |
| 15 | "Comment on that PR for me" | 1→6 | ✅ done |
| 16 | "Write this up as a document" | 1→3 | 3 |
| 17 | "Log: 5×5 squats, last one a grind" | 1→6 | ✅ done |
| 18 | "What did you do this week?" | 1→2 | ✅ done |

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

### Phase 1 — Email to rung 6 · **in progress**

The highest-frequency domain, and the one with a hole at rung 3.

| Action | Risk | State |
|---|---|---|
| `create_draft` | 🟢 | ✅ `gmail.drafts.create`. Reaches nobody, so no allow-list and **no approval** — the overnight-preparation unlock. Verifiable, and the only outbound-shaped action that is fully reversible (`Discard it`) |
| threading | 🟡 | ✅ `thread_id` on either mail action. Carries `In-Reply-To`/`References` read from the thread's **last** message — `threadId` alone convinces Gmail and nobody else |
| attachments | 🟡 | ✅ `attach="…"`, resolved through the **folder grants** in `agents/file_tools.py`, so `attach ../../.ssh/id_rsa` hits the boundary that already answers it. Filenames are stripped of paths and CR/LF before they become a header |
| `cc` | 🟡 | ✅ on both mail actions |
| `schedule_send` | 🟡 | ⚠️ works via `execute()`'s `at` path; not surfaced as its own control |
| `forward` | 🟡 | ❌ |
| `label` / `move` | 🟡 | ❌ — though `mail_triage` already covers both verbs |
| `create_followup` | 🟢 | ❌ An open loop with a `due_at` |

**Why `create_draft` is green, and what that does not mean.** A draft sits in
the user's own Drafts folder; they are the send button. An unattended agent
reading a stranger's email *can* be talked into drafting one back — nothing
sends it, and the card says **Draft**, never **Email**, because a person about
to press send in Gmail has to be able to tell the two apart. Attachments do not
rest on that argument: they go through the folder grants instead.

**`create_draft` travels with `send_email` and never without it.** Not for
permission reasons — it needs none — but an agent that can draft and cannot
send has no way to finish the job, and its card looks like a send that quietly
did not happen.

#### Job 1 — "clear the emails that don't need my attention" · ✅

```
Four emails. Two are noise, one is an FYI, and Rahul is waiting.
I left Rahul's in your inbox as well, so you can see the thread.

  4 emails — 2 newsletters, 1 to mark read, 1 needs you
  Archive 2 emails, Mark read 1 email
  Draft "Re: Proposal?" to rahul@work.test
  Changing your inbox always needs your approval.
                                           [ Approve & do all ]
```

Every piece already existed — `list_mail` for the ids, one `mail_triage` for
the batch, `create_draft` for the replies, `<plan>` to put them under one
button. **Four capabilities is not the same as the one job people ask for**, so
the assembly is written down in `prompt._INBOX_RECIPE` and scored by three
checks in `evaluation.py`.

Derived from tools and actions, not from the Inbox template: somebody who
builds their own triage agent out of the same parts gets the same recipe, and
an agent missing any part gets none of it — a recipe for a capability an agent
half-has is worse than no recipe.

The rule that earns its place is *never archive or mark-read a message you are
also drafting a reply to*. That is how this job fails silently: an email
quietly archived under a draft is one the user will not know to look for. The
scorecard breaks on exactly that, not merely on "did it emit a plan".

The whole job is **reversible** — archive is a label pair, a draft is
discardable — which is what makes one tap over fourteen emails a reasonable
thing to ask somebody for.

#### Routines that run at a time of day · ✅

Shipped alongside, because it is what you want the moment you have seen job 1
work once: *do that every weekday at 8am.*

`trigger="daily"` with `at_time` and `days`. The schema could not say it
before — "every morning" meant `interval_min=1440`, which fires 24 hours after
whenever you created it and then drifts by however long each run takes, so the
morning brief arrives at 8:04, then 8:11, then some time in the afternoon.

Three properties carry it, all driven against a fake clock rather than waited
on: it fires **once a day** whatever the sweep cadence (288 sweeps, one run);
a laptop asleep at 08:00 and opened at 11:00 **still gets its brief**, because
the user wanted one and did not get one; and *weekdays* means weekdays.

The card promises what the handler builds. A model reaches for the trigger it
was shown first and then attaches `at="8am"` to it, so **a time wins over the
trigger** — in the handler and in the approval card identically, or the user
approves "weekdays at 8:00 AM" and gets "every 60 min".

> This is also the prerequisite for job 5 (*"follow up with whoever hasn't
> replied"*). A chase you have to trigger by hand is not a chase.

#### Job 5 — "follow up with whoever hasn't replied" · ✅

An open loop saying *"waiting on Rahul"* was true from the moment it was
written until somebody deleted it by hand. **Nothing ever went and looked to
see whether Rahul replied** — so a week later the user is chased about a thing
that was settled on Tuesday.

`create_followup` (🟢) stores the `thread_id` beside the commitment, which is
the difference between a note and a thing that can notice it has been
answered. `awaiting_reply` reads each one back and sorts it into **three**
states, not two:

```
WORTH CHASING (3+ days, no reply):
- Priya: the contract — priya@work.test, 9 day(s) with no reply  (thread t_stale)

STILL RECENT, leave them be:
- Sam: the deck — Sam, 1 day(s) with no reply

COULD NOT CHECK — do not chase these:
- Lee: the invoice — could not read that thread

ANSWERED since you asked, now closed:
- Rahul: the proposal — Rahul replied.
```

**The third state is the point.** A Gmail outage that read as silence would
become a round of chasing emails to people who already answered, sent in the
user's name and impossible to take back. Unknown says so and is left alone —
and the recipe forbids touching it. Five tests break if that guard goes.

Answered loops close **as a side effect of looking**, not in a background
sweep: the check is the only thing that knows, and a loop that closes the
moment anybody asks cannot be stale when it is read.

This also answers **job 12** — *"what am I waiting on?"* — because that is the
same question asked without the chasing.

**Job 11** — *"chase this in two days if nothing happens"* — is the same
machinery with the stored `due` actually honoured. It was written down and then
ignored: the global three-day default governed regardless, so a date the user
named either fired early or not at all. It now beats the default **in both
directions** — chase when they said, stay quiet until then — and beats it in
neither of the two that matter: somebody replying still closes the loop, and
unverified silence is still not a chase.

#### Job 18 — "what did you do this week?" · ✅

`action_log` recorded every action from the day the loop landed, and only the
Inbox screen ever read it back. But this is a question asked **in chat**, of
whichever agent is open, and an agent that answers it with *"check the Inbox
panel"* cannot answer a question about itself.

`what_i_did` is in `BASE_TOOLS` for that reason, and for a second one worth
more: an agent about to claim it sent something can now **check the record**
rather than trust its own memory of the conversation — which is exactly what
`_HONESTY` tells it not to trust.

```
In the last 7 day(s): 1 done, 1 failed, 1 taken back.
2× set reminder, 1× create followup

↩ Follow up: Priya: the contract
✕ Reminder:  — reminder message required
✓ Reminder: call Rahul
```

Three outcomes, not two — an undone action is neither a success the user should
still see as done nor a failure needing attention. A clean week mentions
neither failures nor undos, because saying "0 failed" to somebody whose week
went fine is noise.

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

### Phase 2 — Calendar to rung 6 · ✅ **jobs 6, 7 and 8**

`gcal.py` had exactly one write method, so an agent could put a meeting in the
diary and not move it — which is how you end up with two of everything.

| | |
|---|---|
| `update_event` | ✅ 🔴 · move, rename, relocate, change who is coming. Verified, and **reversible**: the handler keeps the event as it found it, so *Put it back* has the other side of the diff |
| `cancel_event` | ✅ 🔴 · calls it off and tells the attendees. **No undo** — recreating it is a new invitation to people already told it was off, which is not the same event |
| `calendar_lookup` | ✅ now surfaces the `event_id` the sync has stored since it was written. Without it an agent asked to move a meeting could describe it and not address it — the same gap `list_mail` had |
| `find_time` | ✅ free/busy for the user **and** the other people, where their calendar is shared. Proposes concrete slots in working hours, and names who it could **not** check rather than calling them free |

**Three things that would each have done real damage:**

* **A patch, never a replace.** An event carries a Meet link, recurrence and
  reminders nobody named on the card. A body built from the four things the
  user mentioned drops the rest.
* **Attendees are only touched when named.** `attendees=[]` because nobody
  mentioned them uninvites the meeting — delivered to everyone on it as a
  cancellation.
* **Moving keeps the length.** *"Move it to Friday afternoon"* is about when it
  begins. Keeping the old finish time makes a one-hour call end the previous
  day.

The last one is why this phase has tests against the **real connector** and a
fake Google, not only against a fake connector. Breaking the duration rule
passed all 36 action-level tests: the fake had reimplemented the logic rather
than exercising it.

#### Job 8 — "find a time with Rahul next week" · ✅

```
30-minute slots in next week (09:00–18:00 weekdays):
- Mon 21 Sep, 2:00 PM  →  2026-09-21T14:30:00+05:30
- Tue 22 Sep, 9:00 AM  →  2026-09-22T09:30:00+05:30

Checked against: your calendar
NOT checked (their calendar is not shared with you): rahul@work.test.
Offer these times, do not assert they are free for them.
```

**Google answers an unreadable calendar with an empty `busy` list**, so
anything reading only `busy` sees a person with a completely clear week.
Sharing is normal inside one Workspace domain and rare outside it — which makes
the unreadable case the *common* one for exactly the people you need to arrange
something with. "Rahul is free Tuesday" when nobody can see Rahul's diary is a
sentence you get embarrassed by, so `free_busy` returns **busy** and
**unreadable** as separate things and the tool never merges them.

`calendar.readonly` was already granted, so this needed no reconnect.

Two arithmetic traps, both pinned: overlapping meetings are merged before
subtracting (the sliver where one ends after the next began is not free time),
and a gap at 03:00 is free and is not a time to offer anybody.

**`parse_date_range("next week")` returned nothing.** The module grew up
answering *"what did I get"*, so every relative phrase in it pointed backwards
— which means `calendar_lookup("next week")`, the most ordinary question
anybody asks a diary, has been answering *"I could not read that as a period"*
this whole time. Fixed at the source, with `next month` and `next N days`.

**Rung 3 is the part that is not the API.** *"Friday afternoon"* has to become
a specific time — an agent that replies *"what time on Friday works for you?"*
has handed the job back, because the user asked to have it moved. The recipe
makes it look at that day and pick a clear slot, and say why it chose it so the
reasoning can be argued with rather than only the answer.

---

### Phase 3 — Messaging and Files · **jobs 4 and the Telegram door**

#### The Telegram door · ✅

Six endpoints and the whole Telethon sign-in shipped with nothing calling
them, so `message_send` was an action the Inbox agent is *taught* and
structurally could not take. The Connectors row even had a **Connect** button:
it opened *"add the value to your `.env` and restart"*, which is worse than no
button.

Four screens, and **the server owns which one you are on** — `status` is asked
first and again after anything that might have moved, rather than the modal
counting its own steps. Two answers that are not failures had to be read as
steps: `already` means you are done, and `needs_password` is two-factor, not a
refusal. Reading the second as an error strands every account with two-step
verification on.

#### Job 4 — "send the latest proposal to Rahul" · ✅

Two questions, and the dangerous one is the first. Attaching is easy;
**picking the wrong draft is discovered by the recipient, not by you** — and by
then it is a document somebody else has read.

So rung 3 here is not *find a file*, it is **say which one and when it was
modified, before it goes**:

```
3 file(s) matching "proposal" — newest first:
- Acme proposal.pdf
  /Work/2026/Q3/Acme proposal.pdf
  modified 18 Sep 2026, 09:12 · 84,210 bytes
- proposal-draft.md
  …

Say WHICH one you picked and when it was modified before attaching it.
Two drafts a week apart look identical in a sentence.
```

The ranking is a suggestion; the **evidence is what makes disagreeing
possible**. And the recipe says to *ask* rather than choose when two are close
in time — "the latest" is only obvious when it is.

`find_file` searches `granted_roots()` and nothing else, and the attachment is
re-resolved through `_resolve` on the way out. Two checks rather than one on
purpose: the search could be widened one day and the send must not widen with
it.

### Phase 3 — the rest · ~1 week

`create_followup` 🟢 landed early, with job 5. What is left:

| | |
|---|---|
| `draft_message` 🟢 | the messaging twin of `create_draft` — prepare a reply without sending it |
| `schedule_message` 🟡 | `execute()`'s `at` path already schedules mail; messaging does not use it |
| `rename` 🟢 · `move` 🟡 | inside granted roots |
| `convert` 🟢 · `generate_document` 🟢 | job 16, *"write this up as a document"* |

None of these is a new mechanism. `draft_message` is `create_draft` pointed at
a different connector; `rename` and `move` are `_resolve` twice and a call to
`Path.rename`.

---

### Phase 4 — Work surfaces · **GitHub done**

Four connectors that read and cannot write. One of them can now.

| Connector | Actions | Risk |
|---|---|---|
| GitHub | ✅ `github_comment` · `github_create_issue`. `assign`, `label`, `close_issue` not yet | 🟡 |
| Linear | `create_issue`, `comment`, `assign`, `move_state` | 🟡 |
| Notion | `append_block`, `create_page` | 🟡 |
| Drive | `create_doc`, `share` | 🟡 / 🔴 (share) |

**The allow-list is a place, not a person — and that is the first time.**
The tier test has never been "does this reach somebody", it is *can the gate
see what it reaches*. `update_event` went RED because the people a move touches
are on the event and not in the params. Nobody can enumerate who watches
`acme/api` either — but `acme/api` **is** in the URL, so it is a key an
allow-list can hold, and *"always allow comments on acme/api"* is a coherent
offer. `REPO_RECIPIENT` is that list, case-folded because GitHub is.

That is the same argument as **per-tool grants** for `mcp_action`
(`(server_id, tool)` as the key). GitHub was its proof of concept; the grant
itself is built — see *Per-tool grants* below.

Starting this phase turned up a live bug first: the two allow-lists that
already existed were plumbed as one. The *"Always allow"* button on a Telegram
card wrote onto the **email** list, which `message_send` never reads — so the
tap did nothing and said it had worked. Fixed in `3c7c1c3` before any of the
above.

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

## Per-tool grants

The **Promote** rung, and the only place this project has made a gate *looser*.
It deserves the argument written down, because "we relaxed a security control
and the tests went green" is a sentence that should never stand on its own.

**What changed.** `mcp_action` was 🔴 — refused unattended, unpromotable,
forever. It is now 🟡 against a list of `server:tool` keys
(`actions.connector_tool_key`, `permissions.TOOL_RECIPIENT`).

**Why the old tier was wrong.** Red was never a judgement that connector writes
are the most dangerous thing here — `send_email` is amber and mail is
irreversible. It was a judgement that *the gate could not see what it reached*,
which was true while the only candidate key was somebody else's argument blob.
`linear:create_comment` is a key: stable, comparable, revocable, and readable
by the person granting it. That is the same move `REPO_RECIPIENT` made for
GitHub, and the tier test is unchanged — the key got better, so the tier
followed.

**What now carries the weight red used to.** Four things, and the second is the
one that makes this safe rather than merely defensible:

1. **Nothing is granted by default.** An unconfigured install refuses every
   connector write, exactly as before. A grant exists only where a person
   tapped for it.
2. **A grant cannot cover a verb nobody can take back.**
   `ActionSpec.always_ask_when` is consulted *before* the tier, so
   `demo:delete_project` is refused even to a user who granted precisely that
   tool — and told why. `mcp_source.is_irreversible` is the classifier;
   it reads the tool's own verb and errs toward asking.
3. **The grant is one tool, not one server.** Allowing
   `linear:create_comment` does nothing for `linear:create_issue`, and an
   action arriving without both halves of its key fails closed against a
   placeholder no grant can match.
4. **It is visible and revocable, and every use is logged.** The allow-list
   screen shows it tagged *Connector tool*; `action_log` records each run.

**What got worse.** Honestly: a user who grants a write tool has made a
standing decision, and a prompt-injected agent that reaches that exact tool can
now use it without a tap. Before, it could not. That is the trade — it is the
same trade already made for `send_email`, bounded harder here by (2), and it is
the trade that makes a repeated write stop asking a fourth time.

**What this cost in tests.** Six tests asserted `"mcp_action" in
NEVER_UNATTENDED`. That membership was a *proxy* for "a connector write cannot
run unattended", and the proxy stopped being the mechanism. Each was rewritten
to assert the behaviour instead — refused with nothing granted — and each was
watched failing against a gate with its recipient lookup removed. One got
strictly stronger: `test_a_connector_write_still_needs_a_tap_when_nobody_is_
watching` now grants `x:delete_everything` and asserts it is *still* refused.

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
