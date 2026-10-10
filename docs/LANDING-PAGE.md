# Chitragupta — landing page plan

> Status: **PLAN ONLY — the visual design is not decided here.** The user is
> supplying design inspiration separately. This document fixes the *story*, the
> *content*, the *asset list*, the *proof*, the *comparison* and the *waitlist
> mechanics*. Everything about look-and-feel defers to
> [`DESIGN-BRIEF.md`](DESIGN-BRIEF.md) and to the inspiration still to come.
>
> Nothing here restates a rule that lives elsewhere. Palette, mark, type and
> motion are the brief's; product truth is [`../CLAUDE.md`](../CLAUDE.md)'s.

---

## 1. What the page is for

One job: **get the right person onto the waitlist, having understood three
things they cannot un-know.**

1. The context is **theirs** — it is a folder on their Mac, not a row in our database.
2. The agents **already know them** — they explain themselves once, not per chat.
3. The agents **do the work and still ask** — action, with the final say kept.

Everything else on the page is in service of those three or it is cut.

### Who it is written for

| | who | what they already tried | the line that lands |
|---|---|---|---|
| **P1** | The heavy ChatGPT/Claude user who re-pastes the same context daily | custom instructions, project files, a "about me" doc they maintain by hand | *Your AI shouldn't start from zero.* |
| **P2** | Mac-native professional with data they will not upload — founders, devs, consultants, anyone under an NDA | nothing, on purpose | *Your context belongs to you.* |
| **P3** | Agent-curious, burnt by brittle cloud automation | Zapier / Lindy / a GPT with tools | *Let AI work. Keep the final say.* |

P1 is the volume. P2 is the conviction — they convert hardest and advocate
loudest. P3 is the expansion. The page is ordered for P1 and must never lose P2
by overclaiming.

### Proof obligations

Every claim on this page has to be *shown*, because all of them are claims the
visitor has heard before from something that was lying. A section without its
proof asset is not finished.

| claim | the proof that must be on the page |
|---|---|
| it is local | the real path on screen, a Finder window, and an **airplane-mode clip** where it still answers |
| it remembers | a recall trace — the actual memories and facts a turn used, with where each came from |
| it is a team | the real roster, real names, live avatars |
| it acts | the editable action card, and what runs being what is on the card |
| it asks | the approval that waited while the user was away, showing the whole thing |
| your model | the provider picker, swapped mid-conversation, same memory |
| it is real software | video of the actual app, the GitHub repo, the licence, the test count |

---

## 2. The story

A landing page is not a feature list with a hero on top. The arc below is the
page order, and each act ends by creating the question the next act answers.

> **Act I — The amnesia.** You have explained your company, your people and how
> you work, hundreds of times, to software that forgot by lunch. *So where should
> that knowledge actually live?*
>
> **Act II — The brain.** On your own disk: your mail, files, calendar and notes
> turned into memories and a graph of the people and projects inside them. Yours
> to read, yours to delete, no copy anywhere else. *So who works from it?*
>
> **Act III — The team.** Not a chatbot — a roster. Inbox, Researcher, Engineer,
> Money, Health. All reading the same brain, so you say it once and every one of
> them knows. *So what do they do while I'm not watching?*
>
> **Act IV — The work.** Triggers, conditions, runs that survive a crash. Work
> that happened overnight. *And what stops it doing something I didn't want?*
>
> **Act V — The final say.** Reading is free. Anything that leaves your machine
> stops at a card with the real content, every field editable, and what runs is
> what is on the card when you press Confirm. *And when a better model ships?*
>
> **Act VI — The swap.** Bring your own. Change it whenever. The memory does not
> move. *So why should I believe any of this?*
>
> **Act VII — The record.** Because it is built to remember what actually
> happened: claims are append-only, a change supersedes rather than overwrites,
> and every answer can say where it came from. The app is named after the scribe
> who keeps the account of what each person has actually done.

The seven taglines the user wrote are the act titles. They are not decoration —
each one is the H2 of its section:

| tagline | section |
|---|---|
| Your AI shouldn't start from zero. | **Hero** (H1) |
| Your context belongs to you. | Act II — the brain, local |
| One brain. A team of agents. | Act III — the roster |
| AI that remembers your world. | Act III-b — recall in action |
| Let AI work. Keep the final say. | Acts IV + V — automation and consent |
| Your models change. Your memory doesn't. | Act VI — bring your own model |
| Built to remember what actually happened. | Act VII — provenance |

---

## 3. Section-by-section content

Copy below is **draft at shipping quality** — written to be used, and to be
rewritten once the design arrives. Length targets are deliberate: this page is
long by design (a privacy claim needs evidence), but each section is one idea,
one visual, one scroll-height.

### S0 — Nav

Hairline, sticky after the hero, mono labels. `[mark] Chitragupta` · Story ·
Agents · Try it · Compare · FAQ · **[ Join the waitlist ]** (primary, always
visible). A `GitHub` link, because P2 will look for it before they read anything.

### S1 — Hero

- **H1:** Your AI shouldn't start from zero.
- **Sub:** A team of AI agents that share one brain — and it lives on your Mac.
  Connect your mail, files, calendar and notes once. Every agent knows you from
  then on.
- **Primary CTA:** Join the waitlist · **Secondary:** Watch it work (70s) →
- **Status line (HUD, per the brief):** `● LOCAL · OFFLINE-READY · macOS · APACHE-2.0 · NO ACCOUNT TO USE IT`
- **Visual:** the living-constellation particle field. `docs/hero-lab/brain-hero.html`
  already exists and is the starting point — do not rebuild it.
- **Below the fold tease:** one line of real app chrome peeking up, so the first
  scroll lands on software rather than on more marketing.

Three things the hero must *not* do: no "AI-powered", no centered-everything
(the brief says left-aligned column), no autoplaying sound.

### S2 — The cold open (the problem)

- **H2:** Every new chat, you start over.
- **Body:** You paste the same background. You re-explain who Priya is, which
  repo matters, how you write. Then the tab closes and it is gone — and tomorrow
  a better model ships and you do it again, from zero.
- **Visual:** a short loop of the same paragraph of context being pasted into
  three different assistants. This is the only section where the villain is
  shown, and it is shown for six seconds, not thirty.
- **Turn line (sets up Act II):** The problem was never the model. It is that
  your context has nowhere of its own to live.

### S3 — Your context belongs to you

- **H2:** Your context belongs to you.
- **Body:** Chitragupta builds the brain on your machine. Your sources become
  memories *and* a graph of the people, projects and facts inside them, in
  `~/Library/Chitragupta` — a folder you can open, copy, back up, or delete.
  There is no cloud copy, because there is no cloud.
- **Proof row (three hairline facts, each with its own small visual):**
  1. **It is a folder.** Finder, open, showing the home. The path is on screen.
  2. **It works with the wi-fi off.** A clip: menu bar wi-fi off, a question
     asked, answered from the brain on a local model.
  3. **Nothing is sent.** One request exists: a once-a-day check for a newer
     version — app version, macOS version, Apple silicon or Intel. No
     identifier, and it can be turned off.
- **Honesty note that must be respected in copy:** onboarding's *first* screen
  does ask for a Google sign-in, and a build with no OAuth client passes the user
  straight through. So the page may say **"no account to use it"** and **"nothing
  local consults a session"** — it may **not** say "no sign-in". The correct line
  is: *Signing in names your backup set. It never gates your data, and nothing
  local asks whether you did it.* Getting this wrong is the single worst thing
  this page could do, because P2 will check.

### S4 — One brain. A team of agents.

- **H2:** One brain. A team of agents.
- **Body:** Not one assistant with a long memory — a team, all reading the same
  brain. Tell the Inbox agent how you talk to a client and the Writer knows too.
- **Visual:** the architecture diagram, drawn once and reused everywhere:
  `sources → brain (memories + graph) → the roster`. Hairline, mono labels, one
  gold highlight travelling the path on scroll.
- **The roster**, with live `character.js` avatars and each agent's real
  one-liner (these are the shipped `role`/`description` strings — use them
  verbatim, they are already good):
  - **Chief of Staff** — your day, end to end
  - **Inbox** — email and messages; drafts in your voice, never claims to have sent
  - **Researcher** — investigates properly, cites where each answer came from, can act on nothing
  - **Engineer** — your repositories and issue tracker
  - **Writer** — drafts in your voice, learned from what you have actually written
  - **Money** — what you spend, from your own receipts and statements
  - **Health & Fitness** — measures before it judges
  - **Personal · Files · Shopping · Statements** — the rest of the library
- **The detail that sells it:** the Researcher has *no* actions, deliberately —
  an agent with no way to send cannot claim it sent. Say that out loud. It is the
  kind of sentence that makes an engineer trust the whole page.

### S5 — AI that remembers your world

- **H2:** AI that remembers your world.
- **Body:** Every turn starts with recall: the memories, the facts and the
  excerpts that matter to what you just asked — fused, scored and cited. You do
  not prompt it. You do not paste anything.
- **Visual (the most important asset on the page):** a **recall trace**. The
  question, then the context it pulled — "Priya Raman · colleague · Aurora" from
  a calendar invite on 12 Sep; "we deploy on Fridays" from a Slack thread — then
  the answer that used them. Provenance chips on every item.
- **Supporting facts, as a hairline strip:** claims are append-only (a change
  supersedes, it never overwrites) · inferred never outranks what you confirmed ·
  recall over 25,000 memories costs 15 ms, so it runs on every single turn.

### S6 — Let AI work. Keep the final say.

Two halves of one section, because separating them lets a reader take away
"autonomous agents" without "and it asks" — which is exactly the misreading that
loses P2.

**Half A — the work happens.**
- Automations are triggers, conditions and durable runs. Mail arrives, a time
  comes round, another automation finishes. A run that dies mid-flight resumes
  rather than repeating, and the engine knows the difference between *failed* and
  *correctly declined to act*.
- **Visual:** a morning brief that ran at 07:00, with its run log.

**Half B — you keep the say.**
- **Body:** Reading is free. Anything that leaves your machine stops at a card
  with the actual content — every field editable — and **what runs is what is on
  the card when you press Confirm**, never what the model first wrote. An agent
  working while you were away cannot widen its own permissions; the request
  waits, showing the whole thing, because you cannot consent to text you were
  never shown.
- **Visual:** `docs/assets/action-card.png` and `docs/assets/approval.png`
  already exist and are good. Add one new clip: a field being *edited* on the
  card before Confirm.
- **The closer, and it is the best single proof we have:** an agent may fill a
  basket freely, but it may not place an order. The control that places an order
  is recognised, refused, and re-raised as one card carrying every item and the
  shop's own total — re-checked against the page at the moment you press Confirm,
  and refused outright if the basket moved. Card numbers, PINs and one-time codes
  are refused: we do not hold one and will never ask for one.

### S7 — Try it (interactive)

Full spec in §5. Copy frame:
- **H2:** Try it, right here.
- **Sub:** A miniature Chitragupta, running entirely in this tab, on a made-up
  person's brain. Ask one of the questions below, or your own. Nothing you type
  leaves your browser except the model call, and nothing is stored.
- **CTA under it:** That was a toy. The real one runs on your Mac, on your data
  → **Join the waitlist**.

### S8 — Your models change. Your memory doesn't.

- **H2:** Your models change. Your memory doesn't.
- **Body:** Claude, GPT, Gemini, Grok, DeepSeek, OpenRouter, a local Ollama — or
  the plan you already pay for. Swap mid-conversation. A stored model id is
  re-checked before it is used, so a model retired by its vendor is repaired, not
  fatal. Availability is resolved per *your* account, never from a list we
  hardcoded.
- **Visual:** the provider picker; one conversation, two models, same memory.
- **Why it is here and not in the hero:** it is a *relief* argument, and relief
  only lands after the reader has something to lose.

### S9 — Built to remember what actually happened

- **H2:** Built to remember what actually happened.
- **Body:** The app is named after Chitragupta — in Hindu tradition, the scribe
  who keeps the record of what each person has actually done. That is the whole
  job. Answers cite their source. History supersedes instead of overwriting. A
  number is a number, not prose about a number — one unit per measurement,
  converted on the way in, unknown units refused, because a silent assumption
  turns 170 lb into 170 kg and that is a different person.
- **Visual:** the brand mark, large, and a memory's history: three superseding
  versions of one fact with dates.

### S10 — Compare

Full spec in §6.

### S11 — What it is not

Short, blunt, five bullets. macOS only. No mobile app. No sync between your own
Macs except by restoring a backup you hold the key to. You bring and pay for a
model. Local identity is recorded and deliberately **not** enforced, and the API
says `enforced: false` out loud rather than letting a screen imply otherwise.

This section converts. A page that concedes nothing reads as a page that is
hiding something, and P2 is reading for exactly that.

### S12 — FAQ

Eight, no more. Is my data really local (where, how do I verify). Does it need
the internet. Do I need an API key, what will it cost me. Which sources can it
read. Can it send email without asking. What happens if I delete it. Is it open
source, what licence. When do I get in, and what is the waitlist for.

### S13 — Waitlist

Full spec in §7. This block also appears once mid-page (after S6) — two
instances, one component, never three.

### S14 — Footer

Mark, one line of what it is, GitHub, licence, privacy note, contact. No
newsletter second-guess, no social wall.

---

## 4. Asset manifest

The page is 60% evidence by area. Treat these as deliverables with owners, not
as "screenshots we'll grab".

### Rules for every asset

- **Dark single-theme only.** The brief commits to it; a light screenshot would
  be of software that does not exist.
- **Never the user's real data.** Every capture runs against a seeded demo home
  (`CHITRAGUPTA_HOME=…/demo-home`) with a fictional persona. This needs a small
  script — `scripts/seed-demo-home.py` — and it pays for itself immediately: it
  also powers the interactive demo's brain (§5) and the OG image. Shipping one
  frame of the user's actual inbox is unrecoverable.
- **No GIFs.** A GIF of a dark UI is 8 MB and banded. Everything motion is
  `.mp4` (H.264) + `.webm` (VP9/AV1), `muted autoplay loop playsinline`,
  `preload="metadata"`, with a poster. Under 2.5 MB per inline loop; the one long
  film may be larger and must be click-to-play.
- **One idea per clip, 4–8 seconds**, no cursor wander, no typing typos, 60 fps
  capture, 2× retina, cropped to the chrome that matters.
- **Stills** are PNG captured, served AVIF + WebP with PNG fallback, width-capped
  at 2× the layout box.
- **Reduced motion:** every loop has a still that is shown instead when
  `prefers-reduced-motion` is set. Required by the brief, and it is also the
  low-bandwidth fallback.

### The list

| # | asset | kind | section | what must be in frame | source |
|---|---|---|---|---|---|
| A1 | constellation hero | canvas | S1 | particle field wiring into a graph, one gold pole-star | `docs/hero-lab/brain-hero.html` exists |
| A2 | the film | video 70s | S1 secondary | connect a source → brain builds → ask → agent answers with citations → proposes an email → edit → confirm | new, scripted |
| A3 | re-explaining loop | video 6s | S2 | the same context pasted into three assistants | new, trivial |
| A4 | the folder | still | S3 | Finder in `~/Library/Chitragupta`, path visible | new |
| A5 | offline answer | video 8s | S3 | wi-fi toggled off in the menu bar, then a real answer | new, shot in one take |
| A6 | architecture diagram | SVG | S4 | sources → brain → roster, hairline, mono | new, hand-drawn SVG |
| A7 | the roster | live DOM | S4 | real agent names, `character.js` avatars following the cursor | reuse `character/dist/character.global.js` |
| A8 | recall trace | video 10s | S5 | question, the memories and facts pulled, the provenance chips, the answer | new — **highest value, do first** |
| A9 | brain screen | still | S5 | the graph, entity list, counts | `docs/assets/brain.png` exists |
| A10 | automation run | video 8s | S6a | a 07:00 brief, trigger → condition → run log | new |
| A11 | action card edited | video 8s | S6b | a field changed on the card, then Confirm | new |
| A12 | approval waiting | still | S6b | the whole email, Approve / Always allow / Dismiss | `docs/assets/approval.png` exists |
| A13 | order refused | video 10s | S6b | basket filled freely, checkout refused, one card with the shop's own total | new — the single most persuasive clip available |
| A14 | model swap | video 8s | S8 | provider picker, two models, one conversation, same memory | new |
| A15 | memory history | still | S9 | three superseding versions of one fact, dated | new |
| A16 | workspace | still | nav/OG | the rail, a conversation, a card | `docs/assets/workspace.png` exists |
| A17 | OG image | still 1200×630 | meta | constellation + H1 + mark | new |

Five of seventeen already exist. A8 and A13 are the two that would carry the page
alone; if the asset budget collapses, shoot those two and the film.

---

## 5. The interactive demo

**Goal:** in under 30 seconds of a stranger's attention, make them feel *it
already knows this person* and *it asked before it acted*. Not a feature tour. A
feeling, twice.

### Shape

A single panel that borrows the real workspace's layout — agent rail, a
conversation, a card — on a **fictional persona's brain**, running in the tab.

1. **Pre-seeded brain, visible.** It opens showing what it knows: ~120 memories,
   ~40 entities, a small graph. The visitor did not type anything and it already
   has a world. That is the whole point of the product and it must be the first
   frame, not a reward for engagement.
2. **Suggested questions** (chips, instant, no model call needed):
   - *"What do I owe Priya?"* → open loop, cited to a thread
   - *"Who is Priya?"* → graph answer: colleague, Aurora, three shared threads
   - *"Draft the reply to the Aurora delay."* → **an action card**, editable
   - *"What did I spend on subscriptions last month?"* → a number, computed, with the receipts listed
   - *"Brief me on tomorrow."* → calendar + open loops + what to do first
   - *"Switch to a different model and ask again."* → same memory, different voice
3. **Recall is shown, always.** Above each answer, the trace: *3 memories · 2
   facts · 1 excerpt used*, expandable, each with a source chip. This is the
   differentiator and it is invisible unless drawn.
4. **One action card the visitor edits.** The draft reply appears with editable
   fields. They change a word. They press Confirm. It says: *Confirmed — and in
   the demo, nothing was sent. In the real app this is where the email leaves,
   exactly as you edited it.* Honest, and more memorable than a send would be.
5. **Agent switching.** Tapping another avatar re-asks the same question as that
   agent and the answer differs in scope — one brain, different jobs. Cheap to
   build, and it is the "team not chatbot" argument in one tap.

### Build

- **Mode 1 — scripted (ships first, always works).** The six chips are
  pre-composed answers with real-looking traces. Zero backend, zero cost, zero
  latency, no abuse surface, works offline and in a hotel wi-fi. This mode alone
  is a good demo and must be good enough to ship without mode 2.
- **Mode 2 — free text (ships second, optional).** An "ask anything" box that
  calls a Cloudflare Pages Function → one model, with: Turnstile, per-IP rate
  limit, hard output cap, a system prompt scoped to the demo persona, a daily
  spend ceiling, and a kill switch that degrades to mode 1 with a visible
  *"demo's taking a break — here's what it can do"*. Never a spinner with no end
  state.
- **Recall should be genuinely computed, not faked.** A ~120-row brain with
  lexical scoring plus a date filter is maybe 80 lines of JS, and it mirrors how
  the real thing unions a lexical net onto semantic top-K. Faking the trace is
  the one shortcut that would make the demo a lie about the mechanism.
- **Reuse exactly one thing from the app: `character.js`.** It is standalone,
  dependency-free, and already vendored. Do **not** import `web/app.js` or
  `web/chat.js` — they are evaluated by the test harnesses with `new Function`
  and coupling the site to them would put the marketing page inside the app's
  frontend test constraints. Copy the *styling* if useful; share no code.
- **Label it.** *"A miniature, running in your browser, on a made-up person's
  data."* A demo mistaken for the product sets an expectation the download then
  breaks.
- **Lazy.** Nothing in this section loads until it scrolls into view or is
  clicked. It must not be on the critical path of the hero.

---

## 6. Comparison

### The axes

Chosen so that the honest answer favours us *and* the dishonest answer is
checkable. No axis is on this table that we would not want a competitor to use.

1. Where your context is stored
2. Who else can read it
3. Works with no internet
4. Which model answers — and can you change it
5. One shared brain across many agents
6. Takes real action outside the app
7. Asks before it acts, and shows you the whole thing
8. Can it say where an answer came from
9. Automation that survives a crash
10. Backup and export you hold the key to
11. Account required to use it
12. Source available, licence
13. What it costs you
14. Platform

### The columns

| column | why it is there |
|---|---|
| **Chitragupta** | ours |
| **Turnstone** | the direct competitor; same promise, cloud-shaped. We have a first-hand teardown in [`TURNSTONE-TEARDOWN.md`](TURNSTONE-TEARDOWN.md) — use it, and keep the comparison to behaviour we actually observed and can date |
| **Cloud assistant memory** (ChatGPT / Claude / Gemini) | what P1 uses today |
| **Local note-AI** (Obsidian-and-a-plugin, Reor, Khoj, Mem) | what P2 settled for |
| **Automation platforms** (Zapier agents, Lindy, Make) | what P3 tried |

### Rules — these matter more than the table

- **Grouped categories, not a wall of logos.** Four columns, not eleven. A table
  that names eleven products will be wrong about three of them within a month.
- **Date-stamp it** ("verified 8 Oct 2026") and say what was tested — first-hand
  for Turnstone, published docs for the rest, linked.
- **Every competitor cell is verified before publish, by a human, with the
  source linked.** I will not assert a competitor's current behaviour from
  memory in shipped copy; an out-of-date "no" in a comparison table is the kind
  of error that gets screenshot and quoted back.
- **Concede, visibly.** Cells where we lose stay in, and S11 exists for the same
  reason.
- **No trash talk.** Describe shapes, not sins: *"your context lives on their
  servers"* is a fact; *"they read your email"* is a lawsuit.

### Our actual advantages, in order of how much they are worth on this page

1. **The data is on the user's disk, and they can verify it in Finder.** Nobody
   in the cloud column can answer this, at all.
2. **It works offline.** Not a privacy promise — a demonstrable behaviour.
3. **Many agents, one brain.** The cloud assistants have memory *per product*;
   the automation platforms have no memory of you at all.
4. **The model is yours and swappable, and memory survives the swap.** Every
   cloud column is a bet on one vendor's roadmap.
5. **Consent is a card you edit, not a toggle you set.** What runs is what is on
   the card. Cloud agents ask with a summary of what they intend.
6. **Provenance by construction** — append-only, supersede-never-overwrite, and
   answers that cite.
7. **Open source, Apache-2.0, auditable.** For P2 this is not a feature, it is
   the precondition.
8. **Backup you hold the key to**, encrypted, and it appends generations rather
   than mirroring — so an accidental reset cannot propagate into the only copy.

---

## 7. Waitlist

### Fields

| field | required | notes |
|---|---|---|
| Name | yes | one field, not first/last |
| Email | yes | label it **Email**, accept any domain. (Asked for as "Gmail" — ~70% will be Gmail anyway, and a Gmail-only field turns away the work addresses that are the best signal.) |
| LinkedIn | **recommend optional** | hint: *helps us pick who to let in first*. Requiring it costs conversion from exactly the privacy-minded segment the product is for — P2 is the person least likely to hand over a profile to a form. Their call; if required, say why in one line next to it. |
| "What would you point it at first?" | optional, one line | the single most valuable thing on the form. It is a free sentence, it reads as interest rather than interrogation, and it is how the beta cohort gets chosen |

Four fields, one column, no multi-step. Mac chip, role, company size — all
tempting, all cut; ask them in the invite email instead.

### Behaviour

- Inline section, **never a modal**. A modal on a privacy-first page reads as a
  growth hack.
- Submit → optimistic state → **"You're in. #417 on the list."** A position
  number is worth more than a thank-you.
- **Confirmation email**, immediately, from a real address that accepts replies.
  Two sentences, no graphics, states exactly what we will do with the address:
  *one email when the beta opens, nothing else.*
- **Idempotent.** The same email twice says *"You're already on the list — #417"*,
  not an error, and never a duplicate row.
- Specific errors. Honeypot field plus Cloudflare Turnstile. Per-IP rate limit.
- **Works without JavaScript**: a real `<form method="post">` that the function
  handles, enhanced by JS when present. Cheap here, and consistent with a page
  whose whole argument is that software should not need a cloud to function.
- Privacy line under the button, not in a footer: *Stored on our server, used to
  send you one email when the beta opens, deleted on request. No tracking
  pixels, no third-party form.*

### Backend — recommendation

**Cloudflare Pages Functions + D1 + a transactional email provider.** There is
already a Cloudflare account and a Pages project here, so this adds one vendor
(email) and no servers.

- `POST /api/waitlist` → validate → Turnstile check → `INSERT OR IGNORE` into D1
  → send two emails: the signer's confirmation, and a notification to the user so
  signups arrive as messages rather than as a dashboard they have to remember to
  open. That notification is what was asked for and it is one line of code.
- Secrets as Pages environment variables. Never in the repo.
- Export: `wrangler d1 execute --json`, or a tiny `/admin` page behind
  Cloudflare Access. Do not build an admin UI before there are 200 rows.

**Fallback if that is a day's work too many:** a Google Apps Script web app
appending to a Sheet and emailing on each row. Zero infrastructure, data lands
in the user's own Drive, same morning. Migrating to D1 later is an import.

**Not recommended:** an embedded third-party form (Tally, Typeform, Formspree,
Mailchimp). It puts every signer's email and a tracking script on the page of the
product whose entire pitch is that your data stays yours. The inconsistency is
the kind a commenter finds in ten seconds.

---

## 8. Build and hosting

### Where it lives

**A new `site/` directory in this repo, deployed as its own Cloudflare Pages
project.** Not in the existing one: `chitragupta-bf7.pages.dev/appcast.json` is
the default update feed baked into shipped builds, and
[`../appcast/README.md`](../appcast/README.md) is explicit that the project
publishes a folder's whole contents and therefore holds nothing but the feed.
That URL must stay exactly as stable as it is. A custom domain for the site
later; the feed URL never moves.

### Stack

Hand-written HTML, CSS and vanilla JS. **No framework and no build step** — the
app's own frontend has that rule for its own reasons, and here it is simply the
right answer for one long page: no hydration, no bundle, nothing to upgrade in
six months. If the demo later grows past what is comfortable in plain JS, Astro
is the one to reach for (zero JS by default); React and Tailwind would both be
weight this page cannot justify.

### Budgets and the non-negotiables

- **LCP under 1.5 s on a 4G profile.** The hero's LCP is the poster image, never
  a video. First-party JS under 60 kB gzipped excluding the demo; the demo is
  lazy and carries its own budget.
- **System font stacks only** — the brief already specifies system mono, so there
  is no webfont cost to pay.
- Fully keyboard operable, real buttons, visible focus rings. The film gets
  captions and a transcript. `prefers-reduced-motion` turns the particle field
  static — required by the brief, and the demo must honour it too.
- Body text at 4.5:1 or better: the `--faint` token is ~2.1:1 and is for hairline
  HUD decoration only, never for a sentence.
- **Analytics: cookieless or none.** Cloudflare Web Analytics or a self-hosted
  Plausible. No Google Analytics, no Meta pixel, no session recorder. The app
  makes one identifier-free request a day; a landing page with six trackers in
  front of it would be the loudest thing on the site.
- Meta title/description, OG + Twitter cards (A17), JSON-LD `SoftwareApplication`,
  sitemap, `robots.txt`. No cookie banner needed, because there are no cookies.

### Phases

| phase | ships | why this order |
|---|---|---|
| **P0** | skeleton, hero, S3 proof row, S13 waitlist, footer — live, collecting | the waitlist earns from day one; everything after this is conversion rate |
| **P1** | S2, S4, S5, S6, S8, S9 with assets A4–A16 | the story, once the design inspiration has landed |
| **P2** | S10 comparison, S11, S12 FAQ | needs human verification per cell |
| **P3** | S7 interactive — scripted mode, then free text | the most build for the most payoff; last because P0–P2 must not wait on it |
| **P4** | perf, a11y, OG, the 70s film, launch | |

---

## 9. Decisions still needed

1. **Design inspiration** — the user is supplying it. Nothing in §3 assumes a
   look beyond the brief.
2. **Domain.** Needed before P0 ships, because the confirmation email's From
   address depends on it.
3. **Is LinkedIn required or optional?** Recommendation in §7: optional.
4. **Where do signup notifications go** — which inbox, and immediately per signup
   or a daily digest.
5. **Do we name Turnstone in the comparison?** Recommendation: yes, factually,
   first-hand, dated. We have the teardown and the honesty is worth more than the
   caution.
6. **Beta framing** — free beta, paid later, price? The FAQ needs an answer and
   "we don't know yet" is an acceptable one if it is said plainly.
7. **Does the free-text demo mode ship at all?** It is the only part of this plan
   with a running cost and an abuse surface.
