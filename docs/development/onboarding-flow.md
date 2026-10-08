# The onboarding → workspace flow

> The path a brand-new user takes, and what each screen is waiting for.
> Order: **Account → Hero → Connect → Build → Digest → Workspace (Agent
> Library)**.

---

## Account

**The first screen, and the only one with no way around it.** Making an account
is step one — a product decision taken on 2026-10-06, recorded in
[`/CLAUDE.md`](../../CLAUDE.md) and
[`ACCOUNTS-DESIGN.md`](../ACCOUNTS-DESIGN.md) §0, which reverses the "skippable"
position that document was written with.

**No screen has a skip at all now.** The `Skip for now →` pill that used to sit
in the header — live on Hero and Connect, inert here — was removed for the
shipping build: setup is something a user finishes, not something they dismiss.
The rule that *a way out exists on every screen* was first narrowed to "every
screen after the account step" and is now reversed outright.

What remains is a different thing, and the distinction is the decision. A
**skip** abandons setup and enters the app anyway. An **escape hatch** fires
when a step cannot be completed at all. There are exactly two — `acoPass()`
below, and `Continue anyway →` on the build screen — and removing either turns
a finishable setup into a trap, which is the bug the skip was added to fix.
Pinned by `tests/test_onboarding_flow.py::test_no_screen_offers_a_skip` and
`::test_the_two_escape_hatches_survive_the_skip_being_removed`.

**It may never become a trap, and that is not a softening of the decision.**
Three ways a user can arrive with no key, and each passes them through or lets
them retry:

| | what happens |
|---|---|
| the build has no OAuth client | passes through, and records why |
| `/api/account/state` unreachable | passes through |
| a sign-in that never finished | the screen stays usable, with the reason |

A gate whose key does not exist is a bricked app rather than a strict one. The
accepted cost is the other one: **a first run with no network cannot get past
screen one.**

The providers come from `GET /api/account/state` and nothing on the page names
one — an unavailable provider is drawn `disabled` with its reason as the title,
because this page has no toast. A provider the page has never heard of is drawn
too, which is what makes adding Apple or Microsoft server data.

The block is bracketed `// >>> account-step >>>` and executed by
`tests/js/onboarding_account.mjs`.

---

## Connect

The source grid comes from `GET /api/connectors`; an **AI Model** card
(`openLLM`) picks a provider and an optional key.

Google sources are shown as **disconnected until real sign-in** —
`/api/google/status` overrides the bundled-client `ready`, because a shipped
OAuth client makes the connector look configured when the user has not consented
to anything.

Clicking a source really connects it: Google OAuth, an inline token, a folder
picker, or a custom form. A Google sign-in **can be cancelled** by tapping the
busy card; it used to hold the card for a 180-second poll with
`pointer-events:none` over it.

### What Continue actually requires

`canContinue()` is **one source — any source — and a model that answers.**
Both halves were wrong, and both are bracketed by `// >>> connect-gate >>>` and
executed by `tests/js/onboarding_gate.mjs`.

- It required **`selected["gmail"]`**, which nothing downstream does.
  `_base_personas` files an entity by what it *is*, never by where it came from,
  so a brain of Notion pages works exactly as well as one of email. What the
  check did was dead-end every user without Gmail — and the header's
  "Skip for now" used to disappear the moment the connect screen appeared, so
  there was no way past it at all. Fixing the gate is what made removing the
  skip safe: Continue now lights for *any* source plus a model that answers,
  so the screen is passable on its own terms rather than needing an exit.
- It read **`localStorage.getItem("chitragupta_provider")`** — the presence of a
  string. Choosing a cloud provider and leaving the key box empty wrote that
  string, lit the button, and three minutes later the finale rendered
  `reason: "no_model"` and told the user to connect an AI model. A stored model
  id is a **request**: `verifyLLM()` asks `/api/providers` for this account's own
  answer, on load and on every save, and `llmReady` is that answer.

## Start from zero — and when it asks first

"Build my brain" calls `POST /api/brain/reset` — wipes memories and graph and
connector state, clears connector secrets, disconnects Google — and clears local
prefs, so it genuinely feels like a new user rather than a cleared screen.

It is the most destructive call in the product and it used to fire with **no
confirmation**. Right for a genuine first run; wrong for every other way of
arriving here — and this page binds Cmd+R to reload onto the hero, where that
button is the only call to action. A user who had just pasted a Notion token and
a GitHub token could lose both to a refresh reflex.

So `somethingToLose()` runs first, and the dialog appears only when there is
something to lose. Two things it deliberately does **not** count:

- a connector that is merely `ready`. `always_available` sources (Manual Notes,
  Apple Mail) report ready before the user has done anything, and a reset does
  not touch them because they hold no credential. Counting them put "You already
  have a brain" in front of every genuine first run — *detection is not consent*,
  wearing a dialog.
- the decline. "Carry on from here" **continues to Connect** keeping everything;
  refusing to erase is not refusing to proceed, and parking the user on the hero
  would be a second dead end where the first one was.

A wiped or empty brain also redirects `/` → `/onboarding` once per session
(`sessionStorage.ls_saw_onboarding` guards it).

## Build

Kicks `POST /api/sync/now`, then the progress bar **loads until the profile is
extracted** — it waits for real data to land, then calls the digest. The bar
never shows 100%, because the brain keeps building in the background and a full
bar that is followed by more work is a lie.

Cancel → `POST /api/sync/cancel` (cooperative). It never blocks on a full first
sync.

## Build — and when it is allowed to end

The build screen used to hand over on a **timer**: 4.5s minimum, 45s cap. That
is before the first enrichment pass has typed a single entity, so onboarding
ended on four cards reading "still sorting" — true, and a terrible finale.

It now waits for real state, in three stages, each reporting its own numbers:

| stage | the screen says | driven by |
|---|---|---|
| sync | `Reading your sources…` · `N memories read so far…` | `/api/brain/stats` · `/api/sync/status` |
| enrichment | `Working out who's who — 600 of 1,200` | `/api/brain/enrich/status` |
| snapshot | `Assembling your snapshot…` | `/api/brain/digest` |

The first enrichment pass is **started by onboarding** (`/api/brain/enrich/start`,
once real memories have landed and settled), not left to the workspace. Handover
happens only when that pass is over **and** at least one persona is `grounded`.

Three things stop it becoming a hostage situation:

- enrichment that never shows life within `ENRICH_GRACE` (no model connected) is
  treated as unavailable and skipped, not waited on;
- while nothing is groundable the digest is re-asked every `DIGEST_RETRY` — the
  sync is still running, so the answer really does change;
- after `PATIENCE` a **Continue anyway →** appears. Both jobs keep running in
  the app. This is now one of only two escape hatches in the whole flow (the
  other is `acoPass()` on the account screen), so it is load-bearing in a way
  it was not when a header skip also existed: Cancel goes *backwards* to
  Connect, which makes Continue anyway the single way forward out of a build
  that never finishes. Do not remove it.

The block is bracketed by `// >>> build-progress >>>` and is **executed** by
`tests/js/onboarding_build.mjs` against a scripted backend and a scripted clock —
a 150-second patience window costs the suite no seconds.

## Digest — "Here's your brain"

Cards stay hidden until `POST /api/brain/digest` returns, then fade in.

**Every line on a card is measured or absent.** There is no written-in-advance
sentence anywhere — not in the markup, not as a server fallback, not as a client
one. "What you're building and working on." used to ship in all three, on a
screen a person reads as the app's first finding about them.

### The contract

```jsonc
{ "generated": true,      // a model wrote the summaries
  "total": 4200,          // memories in the brain
  "typed": true,          // the graph has worked out WHAT things are
  "reason": null,         // else: no_data | no_model | not_written | model_failed
  "personas": [ { "key": "work",        // work | learning | comm | personal
                  "title": "Work",      // what a person reads
                  "items": 1280,        // memories backing it — exact, needs facts
                  "mentions": 3100,     // times named — exists from the first sync
                  "themes": ["Atlas"],  // entities really typed into this area
                  "sources": ["gmail"], // connectors those memories came from
                  "summary": "…",       // null unless a model wrote it
                  "grounded": true } ]  // false ⇒ the card shows no claim
}
```

- An area is chosen **by entity type**, in `brain/graph.py::DIGEST_AREAS` — never
  by which connector a memory arrived from. `thing` is deliberately unmapped.
- `items` and `mentions` are different true numbers and the card labels each as
  what it is. `items` needs relations, which only enrichment produces.
- `sources` is derived from `relations.source_mem → memories.source`, so a
  connector added tomorrow appears without touching this code. It is **exact or
  absent** — a vector search stood in for it briefly and was pulled, because
  `embedding_provider` defaults to `hash`.
- `?written=0` returns the measured half without calling a model. The page asks
  for it when the written pass is slow, instead of computing personas of its own.

### `typed` is why the empty states differ

Before enrichment every entity is an untyped `thing`, so all four areas count
zero. That is **not** an empty brain, and the card says so:

| state | the card reads |
|---|---|
| `total == 0` | Nothing here yet. |
| `typed == false` | Still sorting your memories into this one. |
| `typed == true`, area empty | Nothing here yet. |
| grounded, no summary | Read from Gmail and Notion. + what to do |
| summary | the model's sentence |

A model summary stands even when `typed` is false — it read the real recall
context, which is grounding the type check cannot see.

The render block is bracketed by `// >>> digest-render >>>` markers and is
**executed** by `tests/js/onboarding_digest.mjs`; a grep over the page would
pass while the cards rendered nothing.

## Getting out, and being told what is happening

- **Cancel** returns to Connect at any point during the build.
- **Continue anyway** appears after `PATIENCE` and now *answers the tap*: it
  disables itself, says "Finishing…", and moves the detail line. It used to set
  a flag and change nothing, and the next tick is 700ms away with a digest
  behind it that can take 30 seconds.
- A **stalled sync is reported**. `/api/sync/now` failing was swallowed whole,
  so the bar sat at 30% saying "Waiting for your first source to answer…" for
  150 seconds. `kickSync()` surfaces the refusal with a retry, and `STALL`
  catches "nothing has arrived and nothing is syncing".
- A **backend that stops answering** is reported too: `LOST_AFTER` unanswered
  polls in a row is a fact worth saying out loud, and it is the one state the
  detail line cannot express by standing still.
- The progress bar is a `role="progressbar"` carrying `aria-valuenow`, and the
  detail line is an `aria-live` region — for anyone who cannot see the bar, that
  line *is* the progress.

## First entry — the Agent Library

There used to be a fifth step here: *name your lead agent*, which built a custom
agent from the brain and gave it a **Lead** badge. It is gone, and so is the
one-time LLM intro it delivered.

It was a second answer to a question the library already answers. "Chief of
Staff" is that agent, written once, in `library.py`, with the tools for the job
— where the lead agent was built from a hardcoded list that had no connector
access and a prompt naming three specialists a new user does not have. Two
definitions of the same role is how one of them ends up stale, and that one did.

So first entry opens the **Agent Library** instead. Nothing is pre-added and
there is no lead agent, so a new install genuinely has no agents: the library is
not a nicety here, it is the only way to get one. It opens once
(`chitragupta_saw_library`), and the agent rail says so and points there whenever
it is empty.

## The panes are switched, not just faded

The four screens are each `position:absolute; inset:0`, layered on one stage and
hidden with `opacity:0; pointer-events:none`. That hides a screen from the eyes
and from the mouse **and from nothing else** — `Tab` still walked every control
on every screen. On the finale, five of seven tab stops were invisible, and one
of them was `#buildBtn`, which erases the brain and every credential.

`setStage()` is the only way the stage's classes change, and it calls
`syncPanes()`, which puts `inert` (plus `aria-hidden`) on everything that is not
the live screen — out of the tab order *and* out of the accessibility tree.
Where `inert` is unsupported the fallback is `visibility:hidden`, which also
removes a subtree from the tab order. An open dialog inerts the whole stage
behind it, so the backdrop cannot be tabbed through either.

Focus follows: `focusPane()` moves it to the new screen's `<h1>`. Moving the eye
is not moving the focus, and a keyboard user who pressed Continue used to stay
parked on a button that had just gone inert.

Bracketed by `// >>> pane-switching >>>` and executed by
`tests/js/onboarding_panes.mjs`, which reports the tab order the way a keyboard
sees it — a question no grep and no screenshot can answer.

## Motion

This is the most motion-heavy surface in the product — a canvas rAF loop
redrawing a rotating point cloud every 32ms, a scanline overlay, a ripple and a
horizontal glitch offset — and it is the first thing a new user sees. It had
**zero** occurrences of `prefers-reduced-motion`; the rule was enforced for
`styles.css` and for the app's JS, in a test that never read this file.

Under `reduce` the field is a **still image**: the CSS kills transitions and
hides `.scan`, and because a rAF loop is something CSS cannot reach, `step()`
asks as well — the camera snaps to each state instead of easing to it, nothing
rotates, and ripple and glitch never start. It is redrawn on demand (state
change, resize) rather than thirty times a second.

## Brain status

The header pill polls `/api/sync/status` + `/api/brain/stats`. Clicking it opens
the live "Your brain" panel (`#brainBuild`).

---

## The state this flow writes

| key | where | what it means |
|---|---|---|
| `chitragupta_onboarded` | localStorage | the user finished onboarding |
| `chitragupta_saw_library` | localStorage | the Agent Library has opened itself once |
| `chitragupta_provider` / `chitragupta_model` | localStorage | the chosen backend |
| `ls_saw_onboarding` | sessionStorage | guards the empty-brain redirect |
| onboarded flag | `GET`/`POST /api/onboarded` | **server-side**, because localStorage is per-origin and the desktop app binds a different port per launch |

That last row is the one to remember: the webview origin is user state. A launch
that lands on a different port loses everything keyed to localStorage, and the
app opens looking empty. See [`../DESKTOP-SIGNIN.md`](../DESKTOP-SIGNIN.md) → 5.

## Endpoints this flow added

`POST /api/brain/reset` · `POST /api/brain/digest` ·
`POST /api/providers/{name}/key` · `POST /api/sync/cancel`

(`POST /api/agents/lead` and `POST /api/agents/{id}/welcome` were removed with
the lead agent.)

There is exactly **one** onboarding. `workspace.js` used to carry a second — a
`#onboard` modal with six buttons, kept alive by six empty `hidden` stubs in
`index.html` so the bindings would not throw — and `openOnboard()` had no
caller. Two answers to one question is how one of them goes stale, and that one
had: it still offered a lead agent that no longer exists.

The authoritative list of every endpoint is `tests/api_surface.json`, not this
file.
