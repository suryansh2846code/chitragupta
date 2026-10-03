# Parked — frontend work that is known, scoped, and not done yet

Things found during a UI audit and deliberately left. Each one is a real
defect or a real duplication, not a preference. Pick them off in any order;
none blocks another.

**This file is the point.** The reason it exists: the audit that produced it
lived in a chat window, and a finding nobody wrote down is a finding that gets
re-discovered a month later by somebody paying for it in a bug. Add to it when
you find something you are not fixing today, and delete the line when you do.

---

## Navigation still has two systems

The sidebar and the settings-shell rail (`.ms-nav-item`) are two navigations
over one app. Four items now open the same shell — `Connectors`, `Agents &
tools`, `Model` and `Settings` — and `Settings` and `Model` land on the *same
panel*. The shell is `position: fixed; inset: 0`, so entering it hides the
sidebar, which is why `Agent Library` and `Replay onboarding` are unreachable
without going Back.

The consolidation: the sidebar keeps the places you *work* (Inbox, Brain, Agent
Library), and `Settings` is the one door to the shell, which already groups
Connectors and Model under a "Settings" heading of its own. That deletes two
sidebar items rather than adding a third.

Left undone because it changes where people click, which is a product decision
rather than a cleanup.

## The sidebar never shows where you are

`.snav` has hover styling and nothing else — no `.is-active`, no
`aria-current`. The rail inside the shell maintains both
(`models.js::showSettingsPanel`). Same app, two standards.

## Three full-screen surfaces have no focus management

`#modelScreen`, `#libraryScreen` and `#brainScreen` carry no `role="dialog"`,
no `aria-modal`, no `aria-labelledby`, and the focus manager in `app.js`
observes only `.modal-bg` — so none of them takes focus on open, traps it, or
returns it on close. The small modals do all three.

## Escape closes two things at once

`app.js` closes `#modelScreen` on Escape without `stopPropagation`, and the
modal handler is a separate window listener. Open *New automation* from Inbox,
press Escape, and the modal **and** the Inbox screen both close. `#brainModal`
over `#brainScreen` has the same shape (`usage.js`).

## `#agentModelModal` is dead markup

A complete 31-line modal in `index.html` — provider select, model select,
custom-model field, Reset/Cancel/Save — with **zero** JS references to any of
its eleven ids. `openAgentModelModal()` opens the composer popover instead and
ignores the `agentId` it is handed.

## Dead compat shims

About twenty elements exist only so scripts do not throw: the seven `ctx*`
spans for the removed Context panel, `#viewBrainBtn`, `#brainBuild` + five
`bb*`, `#onboard` + six `ob*`, and `#agentModelMatrix` / `#modelHint` /
`#privacyBadge`. `workspace.js` still carries `openOnboard`/`closeOnboard`/
`flashConnectors` and six `ob*` handlers that can never run — `app.js` rebinds
`#helpBtn` after they are set.

`#brainStats` is permanently `hidden` and `brain.js` writes its innerHTML on
every `loadBrain()`.

## `#ibApprovalsEmpty` never hides

`loadApprovals` toggles `#approvals` only, so when something *is* waiting on
you the Inbox shows the approval cards **and** "Nothing is waiting on you."
underneath them.

## "Default AI for new agents" is not a default

`#defProvider`/`#defModel` call `setActiveModel`, which writes the same
`chitragupta_provider` / `chitragupta_model` keys every turn reads — so it changes
the model for the conversation you are already in. `#defEffort` is likewise
global, not per-new-agent.

## `#provider` is an invisible `<select>` used as app state

Read in `chat.js`, `models.js` and `providers.js`, and kept in step with
localStorage by `setActiveModel` — whose own comment documents the shipped bug
where the two drifted and the pill disagreed with the turn.

## Three pollers on one endpoint

`workspace.js` polls `/api/sync/status` + `/api/brain/stats` every 5s forever,
even when nothing is syncing; `brain.js` and `brain-screen.js` poll the same
status independently.

## Dead controls

`#userChip` (the account chip in the sidebar foot) and `#waveformBtn` have no
handlers at all. `#micBtn` is a visible "coming soon".

## `~49 dead CSS class families`

`ctx-*` (the removed Context pane), `welcome-*`, fourteen `pc-*` from an older
provider card, `doc-card`, `cmp-toggle-*`, `ts-switch/slider/spinner`,
`agent-model-matrix`, `brain-stats`, `orb-lg`, `side-add`. Roughly 60 lines.

## Colour drift

Three reds (`#ff6b6b`, `#f87171`, `#e06c75`), two near-identical golds
(`#e0b45f`, `#e0a45e`), and `#8a94a3` used eleven times as a near-duplicate of
`--muted`. `:root` says "blue is deliberately gone" while `#6c8cff`, `#b79cf0`
and `#5fd0e0` are still in the file.

## Onboarding leftovers

- `v0.3` is typed into `onboarding.html` twice and will go stale.
- The four digest card icons use four hardcoded accents (`#b498f0`, `#5fcf8e`,
  `#5b9bff`, `#f5c877`); the brief commits to one gold.
- `FALLBACK` hardcodes eight connectors with labels and secret-field copy, shown
  when `/api/connectors` fails — the same duplication class as the
  `computeFallback` that was removed from the digest.
- The connector tiles use glyph monograms (`✎ ▤ ✉ ☏ ▲`) rather than real marks.
  Left alone when the emoji went: they are a twelve-icon design job, not a
  substitution.
- The digest never refreshes — if enrichment finishes seconds after handover,
  the cards keep the state they were built with.


## `GET /api/agents/connector-gaps` has no screen

Found while wiring the Agents & tools panel to every permission endpoint it
should have been using. Three of the four dangling endpoints are now on the
card they belong to — folders, per-agent connector grants, the unattended
allow-list. This one is deliberately still unused.

It answers a different question: *I just connected something — which of my
agents cannot see it?* That is a fact about the connector, not about one agent,
so putting it behind the panel's agent picker would be filing it under the one
axis it does not vary on. It belongs on the **Connectors** screen, beside the
source that was just added.

Nothing is broken today: `library.BASE_TOOLS` gives every new agent the
connector sentinel, so the gap it reports is rare and shrinking. Worth doing
when the Connectors screen is next opened up, not before.

**And it is now the cheap one of the two.** The measurement below puts
`/api/agents/{id}/connectors` at 4.5 seconds. The Agents & tools panel asks for
it once per open, after the switches are already drawn — but if that endpoint
is ever made to answer quickly, this note and the one under it are the same
piece of work.

## `selectAgent` fires a 4.5s connector probe per switch, and never cancels one

Measured on a real machine, 2026-10-03:

| endpoint | time |
|---|---|
| `/api/agents/{id}/history` | 0.00s |
| `/api/agents/{id}/cards` | 0.00s |
| `/api/agents/{id}/connectors` | **4.53s** |
| `/api/connectors` | **9.83s** |

`loadConnectorNames()` runs on every `selectAgent`, un-awaited and with nothing
dropping the previous one. Switching between two agents four times left **three**
`/api/agents/health/connectors` and two for the other agent still in flight, plus
`/api/connectors` — six requests, which is exactly Chrome's per-host connection
limit over HTTP/1.1. Everything behind them queues: the real-browser run had
`/api/agents/{id}/history` waiting on a socket for seconds.

Found while making agents run concurrently, and it is why the composer and the
draft are now painted *before* the first await rather than after the transcript
arrives — the symptom was an idle agent whose input stayed disabled by the agent
you had just left. That removed the user-visible half. The cause is still here:
two slow endpoints on a path a user now takes freely and often.

The fix is not a frontend one alone. `/api/connectors` is the shape to look at
first — it is nine seconds of provider probing on a page load, and the labels the
`@` picker wants out of it do not change between two switches a second apart.
