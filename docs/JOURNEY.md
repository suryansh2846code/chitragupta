# Chitragupta — The Journey (how we built it, start to now)

> The story of how Chitragupta came to be, in order — the wrong turns, the pivots,
> the bugs found by testing with real data, and what each taught us. Written as a
> narrative; for the crisp "why" of each decision see [`DECISIONS.md`](DECISIONS.md).
> Timeline: **28–30 Aug 2026**. Repo: https://github.com/suryansh2846code/TURNOVER

---

## Chapter 0 — The spark
It started with a simple ask: *"learn about turnstone.ai."* That led to
**myturnstone.ai** (Turnstone, YC W26) — "the AI workspace where your agents
already know you." The idea gripped: separate **your context** (yours, private,
local) from **the model** (swappable), so any AI already knows you. The decision:
**build an open, faithful competitor.**

## Chapter 1 — The wrong build (v0.1)
First read of Turnstone was wrong. I built a **passive memory server** — a brain
that *other* tools (Claude, Cursor) query over MCP. It worked, it ran, tests
passed… and it was the wrong product. Turnstone isn't a plugin for other apps;
it's the place you *do the work*.

**Lesson:** research the product deeply before building. The user caught it:
*"I think you did not understand what Turnstone does."*

## Chapter 2 — The pivot (v0.2, agent-first)
Deeper research (their site, YC page, founders Aryan Vij & Jai Bhatia) corrected
it: Turnstone is a **Mac AI workspace** — named agents (Inbox, Launch, Research,
Personal) sharing a continuously-updated **second brain**, running on **any model
you bring**, that **take action**.

So we rebuilt from scratch, **agent-first**:
- a knowledge-graph brain (vector memories + entity/relation graph),
- a provider-agnostic model layer (Claude, OpenAI, OpenRouter, Ollama, mock),
- the four agents sharing one brain, with a tool-use loop,
- connectors, a FastAPI + web chat workspace, an MCP bridge.

Committed, pushed to **TURNOVER**. First real milestone.

## Chapter 3 — Making it think
It ran on the offline `mock` model (proving the loop), but to *reason* it needed
a real model. We installed **Ollama** (free, local) — agents genuinely reasoned,
grounded in the brain. Then two reliability truths surfaced with the small model:

- it wouldn't reliably call `search_brain` → we made the brain **auto-recall**
  every turn (inject context before the model runs).
- it invented facts and *claimed* actions it never did → we grounded it and made
  the **Tasks panel deterministic** (never depend on a flaky tool call for real
  actions).
- it hallucinated the date → we **inject the real date** every turn (LLMs have no
  clock). Also learned: possessive forms ("todays") must be handled.

We also added **`claude-code`** as a backend — running the user's own terminal
Claude via `claude -p`. Much smarter answers, at the cost of latency + usage.

## Chapter 4 — Auto-learn & the "how does it automate?" question
The user asked the right question: *"how is it going to automate if I have to tell
it everything manually?"* The answer became three layers:
1. **Connectors** bulk-import real data,
2. **Auto-learn** — just chatting saves durable facts,
3. manual notes only as a fallback.

## Chapter 5 — The polish pass (and a scary discovery)
The user set the bar: *"my portfolio should contain finished, polished products."*
So a full polish pass: markdown in chat, Stop button, typed knowledge-graph
entities, error handling, connector setup guides, memory management, a settings
panel, visual/responsive polish.

Mid-polish, a **critical discovery**: the brain had secretly bloated to **82,000+
memories / 433 MB** — whole project folders (38k files!) got synced by accident.
We hardened the files connector (cap + junk filters), purged the junk (**433 MB →
0.2 MB**), and restored the real knowledge clean.

**Lesson:** testing with real data surfaces the bugs that matter.

## Chapter 6 — Meaning, for real
A real Gmail query exposed weak recall: the small **MiniLM** embedder ranked
"Security alert" above "You're Selected for HackWithUP." We upgraded to **BGE**
(a proper retrieval model) with query instructions — the target email went from
*not in the top 15* to **#3**. Semantic search finally worked by meaning.

## Chapter 7 — Understanding the *real* vision
Reading the founder's launch post reframed everything: the heart of Turnstone is
**agents that take action** — *"my email agent… can you just handle that for me?"*
— learning your **writing style**, from a brain built off **all** your apps. We
wrote `docs/CONTEXT.md` to capture this and set the direction: connect daily apps →
action-taking → learn the user's voice.

## Chapter 8 — Connecting real life
We wired the user's **actual** Google account: Gmail, Calendar, Drive (one OAuth).
The "it knows you" moment landed — the Inbox agent answered from **real email**
(hackathon acceptances, internship threads). Then:
- **Continuous background sync** (Turnstone's "continuously updates"), with cheap
  re-syncs (skip content already stored).
- **Date-aware retrieval** — "summarize todays mails", "emails from July 14",
  "last week" — because a date is a *filter*, not a meaning.
- **Source overviews** — "what's in my drive/inbox/calendar" (a listing request
  semantic search can't answer).

## Chapter 9 — The long tail (real data breaks things)
Testing kept finding real gaps:
- The **resume** was invisible — it's a **.docx**; the connector only read Docs/
  PDF/text. Added .docx/.pptx parsing.
- **Shared-with-me** files (DBMS notes) weren't reachable — added Shared-with-me +
  Shared Drives.
- **Duplicate emails** (1,451) appeared — an earlier manual in-place cleanup +
  background sync clashed.

## Chapter 10 — The mindset correction (systemic, not manual)
The user made the most important point of the whole journey:
> *"we can't have users do this manually every time — make the fix for every user,
> not just me. Keep that for all changes."*

Right. I'd been *manually* fixing the user's data (dedup scripts, CLI syncs). That
doesn't scale. So we made fixes **systemic**: `dedupe()` now self-heals on startup
and after every sync — automatic for everyone. This became a standing principle.

## Chapter 11 — Lazy loading (the user's own design)
For the shared-notes problem, the user proposed the *right* architecture:
> *"build the brain with capped files; if the user asks for a file not in the
> brain, search Drive for just that file and sync it on demand."*

We built exactly that: a **bounded brain + on-demand Drive fetch**. Ask for a
document not in the brain → a live, targeted Drive search (name + full-text,
including Shared-with-me) fetches just that file and answers. Done in the recall
layer, so it works on any model backend. *"find the dbms notes in my drive"* now
pulls `DatabaseManagementSystem (2).pdf` (shared, never bulk-synced) in ~1s.

---

## Where we are now
A private, local-first AI workspace where four agents share a knowledge-graph
brain built from the user's **real** Gmail, Calendar, and Drive; recall works by
**meaning and by date**; overviews and **on-demand fetch** cover the long tail;
background sync + **self-healing dedup** keep it current automatically; and it runs
on **any model you bring** (local Ollama or the user's own Claude). Built to a
polished bar, with fixes made **systemic for all users**.

## The production-hardening pass (making it a finished product)
After the feature set was complete, a deliberate hardening sweep turned it from
"works" into "shippable" — see the **H-series in DECISIONS.md**. Highlights:
actions/agents that *do* things (send · schedule · remind · automate) with
confirmation; **custom agents**; three new connectors (Linear · GitHub) plus a
**custom-API connector** to connect any REST app with no code; **in-app token
fields** backed by the **macOS Keychain** (encrypted at rest); a **"what leaves
my device" badge** per model; **brain export/import**; a **first-run onboarding**
flow; and a big reliability pass — crash-isolation on every sync, a Gmail base64
bug that was silently killing whole syncs, self-healing Google auth (7-day expiry),
SQLite `busy_timeout`, clean provider error messages, API input validation, and
the test suite grown **6 → 58**. A machine-portability audit removed the last
"works on my machine" gap (a gitignored OAuth client). Every fix stayed **systemic**.

## Chapter 12 — The model layer, and the sign-in that followed the user
The next demand was blunt: *"make the provider appear unlocked only when it is
connected"* — and then the one that shaped everything after it, **"make a system
that is dynamic, not only for my computer; everytime any user does that he have
same experience like me."**

That became the rule now at the top of `CLAUDE.md`: **model availability is
resolved per user, never hardcoded.** Connection first, then the provider's own
answer for *this account*, and only then a conservative static table. Hardcoded
catalogs are fallbacks, flagged `is_fallback`, replaced the moment a credential
exists. An audit found **13 dead model ids** being offered — 4 of 6 Claude, all 5
xAI, the OpenRouter and Cursor defaults, and all 5 Cursor ids were fictional.
Retired ids are worse than a short list: they render as selectable and fail at
send time.

Two things fell out of researching how competitors reach paid plans. First,
**every subscription path is a vendor CLI** — `claude -p`, `agent -p`, `grok -p`,
codex — because the OAuth client belongs to that CLI; there is no private API to
find. Second, **a subscription is not an API key**: a SuperGrok plan grants no
credits on `api.x.ai`, so an xAI OAuth token authenticates and then 402s on every
request. It is reported as not-ready with an explanation rather than sold as
working. Then: *"bundle them, because not every user had them installed"* — so we
download and pin the CLIs ourselves, reading a vendor's install script as a
*manifest* rather than piping it into a shell.

### The sign-in card, and a week of wrong guesses
The last stretch was one small window. A browser sign-in leaves the app, so the
status had to follow — a floating card, like a system notification. It kept
vanishing the moment the user switched to Chrome.

I guessed "window level" twice and was wrong both times: pywebview already had it
**above** every normal window. The real answer was **Spaces** — a window with the
default collection behaviour belongs to the Space it was born on. We ended up
*lowering* the level and fixing the collection behaviour instead. It still cannot
cover another app's full-screen Space; that was measured across five
level/behaviour/policy combinations before being written down as a limit rather
than guessed at.

Underneath it were four bugs that had nothing to do with windows. A
**temporal-dead-zone `ReferenceError`** in `app.js` tore the card down the instant
the browser opened — `node --check` passes on TDZ, and the click harness that
existed had every fixture short-circuiting into a branch that never reached it. A
port probe binding without `SO_REUSEADDR` made **every other launch open empty**,
because `localStorage` is keyed to the origin and the app kept falling back to a
random port. **158 live `claude auth login` processes** were slowly strangling the
machine — and the biggest source was *the test suite*, spawning a real one on
every run. And the Models drawer froze the app for ten seconds until it was
**profiled** (after two more wrong guesses) and the subprocess probes cached:
5.64s → 0.29s.

The throughline of the chapter: **measure before explaining.** Reading the
NSWindow directly, asking the window server who was actually on top, and
measuring the page's own geometry settled in minutes what argument had got wrong
for days. And every regression test here was validated by putting the old bug
back and watching it fail. Full record in
**[`DESKTOP-SIGNIN.md`](DESKTOP-SIGNIN.md)**, decisions as **W1–W10** in
DECISIONS.md.

## What's next (tracked)
MCP (brain-as-a-service — server already scaffolded) · encrypt-at-rest done, next
transparency panel polish · on-demand fetch for Gmail · Tier-2 scaling (sqlite-vec +
FTS5) · signed/notarized DMG · landing page. Full map in **SCOPE.md**.

## The throughline
Two things drove the whole journey: **research the real product before building**,
and **test with real data** — almost every meaningful fix (bloat, weak recall,
HTML noise, .docx, shared files, duplicates, date parsing) came from the user
trying something real and it breaking. That's the loop that made it good.
