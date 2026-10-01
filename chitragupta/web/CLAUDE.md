# `chitragupta/web/` — the frontend

Vanilla JS, **no build step**. The workspace is `index.html` + `styles.css` +
sixteen plain scripts; `onboarding.html` and `signin_hud.html` are self-contained
pages.

`character.js` is the one exception and is **not edited here**. It is a build
artifact of the standalone package in [`/character`](../../character/README.md),
copied in by `scripts/sync-character.sh` and pinned by
`tests/test_character_asset.py`. Change the package, run the script.

**`index.html` declares the script order, and that is the only place it is
written down.** The browser and the test harnesses both read it from there.
Order is the dependency graph — `core.js` first, `app.js` last — and a `const`
read before its definition is a temporal dead-zone `ReferenceError` that
`node --check` passes.

| file | owns |
|---|---|
| `character.js` | the avatar renderer — **generated**, see above |
| `core.js` | `$` `api` `esc` `md` `toast`, the agent avatars, the icon set |
| `providers.js` | the catalog and its state, sign-in, the provider cards |
| `models.js` | the model picker, agent bindings, `loadProviders()` |
| `chat.js` | sending a turn, and everything that renders one |
| `brain.js` | the brain panel, sync, Google, entity and search modals |
| `connectors.js` | adding a source, and setting one up |
| `workspace.js` | approvals, tasks, reminders, routines, first-run |
| `brain-screen.js` | the full-screen canvas view |
| `usage.js` | the token meter and enrichment progress |
| `library.js` | the Agent Library screen — templates, shelves, the roster |
| `tools.js` | the Agents & tools panel — what one agent may use, with switches |
| `diagnostics.js` | *What just happened* — the log, read-only |
| `browser.js` | websites agents may read, on the Connectors screen |
| `webscreen.js` | the browser itself, shown and driven inside the app |
| `appearance.js` | the Appearance screen — what each agent looks like |
| `app.js` | the shell: state, chrome, agent rail, nav, keyboard, boot |

**Before moving code between them**, read the five checks in
[`docs/development/frontend-testing.md`](../../docs/development/frontend-testing.md)
— and grep for who reads the file you are moving out of. A test that greps one
script out of eleven does not fail; it passes.

- **Inbox is messages; Actions is the machinery.** What an agent *said* to you
  is news, and what it is running is furniture — one screen holding both made a
  user scroll past five sections of furniture to find the news. `messages.js`
  work lives in `automations.js` for now; the two panels are `data-sp="inbox"`
  and `data-sp="actions"`, and `test_frontend_model_screen.py` fails if a list
  ends up on both or on neither.
- **A card never phrases a schedule itself.** `proposedWhen` in `chat.js` goes
  through `routineWhen` (workspace.js), which mirrors
  `core.schedule.describe_schedule`. The card used to write the line inline and
  understood two triggers out of three — `schedule` said "every 60 min" and
  **everything else**, `daily` included, said "on every new email". So a
  Sunday-morning automation was presented as running on every new email, with
  the day and the time on the card's own fields underneath it. A card
  describing something other than what its button runs is the one thing a card
  may never do, and this is the third time this file has done it.
  `proposedWhen` also applies the server's rule that a time of day beats the
  trigger the model reached for — otherwise the card promises a schedule
  `actions._create_routine` will not create.
- **What a card settled as is server state, not DOM state.** The outcome of an
  action card only ever lived in the element that drew it, so reopening a chat
  rebuilt every card pending — an automation created an hour ago came back
  offering *Confirm & create*, and the obvious thing to do with that button is
  press it. `cardKey()` in `chat.js` names a card from the action and its
  position (a stored message has no id the frontend can see), `rememberCard()`
  records the answer, and `/api/agents/{id}/cards` is fetched **before**
  `renderHistory` so a card reads its own state as it is built. A failure is
  deliberately not settled — the retry is the attempt that counts, so it keeps
  its buttons. `CARD_SEEN` is reset per render, or the same card keys
  differently the second time the chat is opened.
  **`planCard` is keyed the same way and it is the one that matters most** —
  its button runs every step at once, so an unremembered plan card is not
  one duplicate but a second copy of the whole plan. It was the card that
  never remembered, because the state work landed on `actionCard` alone.
  **A recorded answer is not enough on its own, and that was the first version's
  mistake.** It only covers cards answered *since* it shipped; on a real machine
  `action_cards` held 0 rows while the action log held every approval the user
  was complaining about. So `/api/agents/{id}/cards` also answers with `ran` —
  what this agent actually did — and `settledState` falls back to matching a
  card against it by the parameters, which is the only thing a stored message
  and a log row share. Matching is **containment** (the confirm adds `agent_id`
  on the way through, so the logged params are always a superset) and each entry
  is **claimed once** (two identical proposals are two cards). The recorded
  answer still wins, because a cancellation never runs and so exists nowhere
  else. A plan settles only when every step is matched. Matched on the fields
  that **name** the action (`ActionSpec.identity`, published in the catalog),
  not on all of them: the boxes are editable on purpose, so a card whose agent
  box was corrected before Confirm logged a run it could never match and sat
  pending under the result of itself. An action declaring no identity keeps the
  strict all-fields rule, which can only fail to settle a card rather than
  settle the wrong one.
- **One settled renderer, every state, every card.** `settledCard` draws
  `done`, `cancelled` and `failed` alike — no buttons, no editable boxes,
  whatever happened. `done` and `cancelled` used to go through a settled path
  while `failed` was patched into the pending one, so a failure kept the amber
  Confirm, the whole form, and a tag reading *needs your confirmation* over the
  sentence explaining why it had already been attempted. A card that was acted
  on is never a proposal again — trying again means asking the agent, which
  produces a fresh card instead of replaying an old one. `markAnswered` is the
  same decision for the card already on screen, so live and reloaded agree.
- **Undo survives a reload, and a card is named by its conversation too.** The
  record has carried `log_id`, `reversible` and now `undo_label` since it was
  written and nothing read any of them, so the Undo a user could see at 3:42 was
  gone by the time they reloaded — which is when a person notices the date was
  wrong. `settledCard` offers it when the server said this run has an inverse and
  the log does not say it was already taken back, wearing the words the server
  chose (a connector action's inverse is decided per run, so the catalog cannot
  answer after the fact), and re-records the card as no longer reversible once it
  is used. A settled **plan** offers nothing: its record keeps one log id, the
  last step's, so the button would take back one of nine and look like all nine.
  `action_cards` is keyed `(agent_id, key)` now — the key names an action, its
  parameters and its position, and nothing in it mentions the agent, so two
  agents proposing the same thing at the same point shared a row and the second
  answer *moved* the first. The state that paid for it is the one that exists
  nowhere else: a **cancelled** card whose row had been taken came back offering
  its button over something the user had explicitly declined.
- **There are four settled states, and the fourth is "we do not know".** A
  confirm whose request never came back — no network, a 500, a timeout — settled
  nothing at all: `api()` rejects, and that `catch` was the one exit
  `markAnswered` did not cover, so the card kept the stale "Working…" where its
  buttons had been, a tag reading *needs your confirmation*, and a red error
  underneath, for the rest of the session. `cards.UNKNOWN` is that state.
  It is deliberately **not** a shade of `failed`: a 500 from
  `/api/actions/execute` can arrive after the email has gone, so claiming it
  failed is a claim nobody checked and leaving the button up offers to send it
  twice. It is not a dead end either — `settledState` prefers a *logged* run
  over an `unknown` record (the only state where the log outranks the record),
  so a send whose reply was lost comes back "done · confirmed 3:42 PM" on the
  next render. `settledCard`'s result line was a three-way choice with
  everything that was not `done` or `failed` falling through to **"Cancelled"**,
  which would have told the user nothing had happened; that arm is now named
  explicitly, and reverting it is how the missing test was found.
- **A card never offers a button that cannot work.** `ActionSpec.required` is
  the handler's own list of what it refuses to run without, published in the
  catalog; `missingFields` reads it, so a message with no chat and an
  automation with no agent are one rule rather than a check bolted onto one
  branch. It asks rather than blocks — the boxes are on the same card, and the
  button returns on the keystroke that fills the gap. And **nothing invents a
  value for an empty field**: the automation readback used to print
  "· personal" over a blank Agent box, naming an agent nobody has.
- **A row and a box for the same field are the same value twice.** `actionFace`
  returns rows as data, each carrying the registry field it `owns`; a field with
  an editable box has no row. They were HTML strings, so neither half knew what
  the other had said and an email card printed its recipient, subject and whole
  body twice — and after a correction the printed copy was the *wrong* one, which
  is a card describing something other than what its button runs, reachable by
  typing. Two lines are **derived** rather than owned (an automation's "Runs …",
  a session's total): dropping them because their inputs have boxes would delete
  the most important sentence on the card, so `liveRows` redraws them on every
  keystroke instead. Dropping an exercise is not a keystroke — `workoutFields`
  calls `notify` for that.
- **The form goes above the buttons, and the tier line goes last.** The boxes
  were appended after `.ac-result`, so the thing to correct sat under the thing
  to press and tabbing off Confirm moved *forwards* into the fields it had
  already run with. `insertBefore(.ac-actions)`, not a CSS `order`: visual order
  and tab order have to be the same thing. `.ac-risk` and `.ac-missing` are
  inserted too, so "this leaves your machine" is immediately above the button
  that does it. Three `tests/js/` harnesses needed `insertBefore` for this, and
  the one that did not get it *threw* — which the anti-vacuity check in
  `test_frontend_tool_result_injection.py` caught, exactly as designed.
- **A boolean is not a text box, and a flag is not a card.** `drive_share` fell
  through to the registry fallback, which draws a field per value — so a link
  share drew a box labelled **Anyone** containing the word **true**, and the
  difference between sending a document to Rahul and publishing it to everyone
  was an internal nobody explained. It has its own branch now: the card is
  titled *Publish this document* and says "anyone with the link will be able to
  open it". Found by rendering the card in a real browser and looking at it —
  every `tests/js/` harness had been green the whole time, because a fake DOM
  can say what a card *says* and never what it *looks like*.
- **The card has one vertical rhythm, `--ac-gap` and `--ac-tight`.** It was
  eleven hand-picked numbers and a padding a pixel shorter at the bottom than the
  top. Nothing was individually wrong and it was never square; a block added
  later now spaces itself by reading one line. **`--border` is `transparent`**
  by design — "separation comes from panel fills, not lines" — so a
  `border-top: 1px solid var(--border)` separator draws nothing, and the
  `margin-top` + `padding-top` around it were both spending space to separate
  with a line that was never there: a 24px void under the last field on every
  card. And two chips on one line have to be the same box, or the line under
  them is ragged in a way nobody can name and everybody sees.
- **One face, two surfaces.** `actionFace` in `chat.js` turns an action into its
  title, rows, kind and tier; `actionCard` draws it and so does `loadApprovals`
  in `workspace.js`. It was the inside of `actionCard`, so the approvals queue —
  the **only** place an *unattended* agent asks permission — had nothing to draw
  with and showed one summary line and three buttons. The user approved an email
  without being shown what it said, on the path where the text was composed
  after reading a stranger's message, while the card in their own conversation
  showed the whole body and let them fix a typo in it. The parameters were on the
  wire the entire time (`approvals._public`). Undo stays off that row on purpose:
  the row disappears on the next poll, and *What just happened* is where Undo
  lives afterwards.
- **A card's title is text; its rows are markup.** The head escapes `title` and
  `verb` and interpolates `rows` as it stands, so no branch writes its own
  `esc()` for a title. It used to interpolate `title` raw, which was safe only
  while every branch remembered: nine wrote constants, one pre-escaped, and the
  two that built a title out of what the *model* wrote did not — so a
  `mail_triage` label from a JSON body put a live `<svg onload=…>` into a card
  head, three lines above the row that escaped the same string correctly. An
  action tag's attributes cannot carry `"` or `>`, so most fields were safe by
  accident; a JSON body is not, and neither is any other path that builds
  params. `tests/test_frontend_injection.py` now walks the whole registry twice
  — once through `parseActions`, once straight at the renderer — and asserts a
  card was actually drawn, because the first version of that walk silently
  dropped every attribute payload and passed with the bug live.
- **Every card says what kind it is.** `CARD_KIND` in `chat.js` — "Automation",
  "Email", "Calendar" — beside the status. A conversation fills with cards that
  look alike, and a settled one has lost its buttons, so there is *less* left to
  recognise it by. A word, never the action id.
- **The tools panel shows what a group IS, not what every tool is.** It was
  sixty-four switches under thirteen headings, all of them asked before the
  user had sent the agent a message. Now: four groups (what a tool touches)
  with a switch per tier inside each (what it does to it), both derived
  server-side from the capability each tool declares and sent on the row —
  `group` and `access`. The screen renders them and decides nothing; a consumer
  that re-derived which tools are dangerous is the name chain this file carries
  three other warnings about.
  **`access` is the server's word and the switch label is the person's**, and
  they are not the same word. Comparing the UI labels straight against `access`
  matched neither `write` nor `destructive`, so the Change and Irreversible
  switches rendered as nothing while their tools sat in the disclosure below,
  switched on — a panel omitting a switch for something an agent can do is
  worse than the sixty-four it replaced. `bucketOf` is the mapping.
  **A group switch is one PATCH.** Looping the per-tool toggle would send one
  request per tool, each carrying the whole list, and whichever replied last
  would win — so turning a group on could land half on, depending on the
  network. The per-tool rows stay, folded into a disclosure: the complaint was
  that per-tool control was the only thing on offer, not that it should go. A
  group somebody part-granted that way says "3 of 7 on" rather than showing a
  plain off over four live tools.
- **Every left-nav item opens a screen.** The slide-over drawer is gone:
  `tasks` moved into Inbox and `tools` became the Agents & tools panel, and
  those were its only two occupants. `openDrawer()` kept its name — four call
  sites use it — and is now pure routing. `tests/js/open_model_screen.mjs`
  reads the nav list out of `index.html` and clicks every item.
- **Icons are drawn, never typed.** No emoji, and no dingbat standing in for a
  control: `IC` in `core.js` is the set. An emoji is a colour font the OS
  picks, so it ignores `currentColor` — it cannot take the gold accent, it sits
  at its own weight beside every drawn icon, and it changes shape between macOS
  versions. A typographic arrow *inside a sentence* ("Add agent →", "System
  Settings → Privacy") is not an icon and stays; the design brief asks for it.
- **A connector row shows what the server says it is doing, not what a
  timestamp implies.** `/api/connectors/health` answers with a state and a
  sentence naming what to do; the row used to derive staleness from `last_sync`
  and could therefore say exactly three things. The two it could never say are
  the two that matter: a sign-in that has run out (the user must act) and a
  service rate-limiting us (the user must *not*). The timestamp path stays as
  the fallback for a source the server has no row for, which is most of the
  list on a first run.
- **A selector in a click handler is a claim about markup, and it goes stale.**
  `syncConn` looked for `.conn` / `.conn-sub` / `.dot` long after the row became
  `.cn-row` / `.cn-sub` / `.cn-logo[data-state]`; `.conn` survived only as a CSS
  rule, so every line guarded by `?.` silently did nothing — no "Syncing…", no
  disabled button, no error on the row. `node --check` passes on all of it.
  `tests/js/connector_sync_feedback.mjs` finds its elements *through the markup
  the renderer produced*, which is the only shape of harness that can catch it.
- **A disabled attribute is not a guard against a double click.** The row is
  re-rendered wholesale by `loadBrain()`, and the fresh button arrives enabled.
  `SYNCING` in `brain.js` is the set that survives a repaint; a first Gmail pass
  runs for minutes, which is a long time to be able to start twice.
- **Long work is started, not awaited.** `syncConn` calls
  `/api/connectors/{name}/sync/start` and follows the job; the blocking route
  still exists and a screen must not use it, because it holds one of six shared
  lane slots for the whole pass — the lane this page loads through. The watch is
  also started from `renderConnectors`, which is what makes progress survive a
  refresh: the job lives on the server, so a reloaded page finds it instead of
  drawing a finished-looking row over a sync that is still going.
- **A progress bar with no total is worse than a count.** A paged read never
  knows how much there is, so `percent` is `null` rather than 0 and the row says
  "Syncing… 40" instead of freezing at 0%.
- Relative API paths only (`/api/…`). Never a host or port — the desktop app
  binds a different loopback port per install.
- `Cmd+R` reloads the frontend only. It cannot reload Python; without `--dev` a
  new endpoint 404s until you relaunch.
- **If you add a render path or a click handler, execute it in a test**
  (`tests/js/`). `node --check` passes on the temporal-dead-zone `ReferenceError`
  that blanked the whole drawer, and a source-order assertion passed while a card
  was being written into a detached container.
- **An agent always has a face.** `paintAvatar` (core.js) draws the saved
  character if there is one and a character generated from the agent's id if
  there is not, so no render path has an empty state to handle. `live: true`
  mounts a following instance and is for the few avatars a person is looking at
  — the rail and the chat header. A list gets the static string; thirty live
  instances is thirty springs integrating on every pointer move.
- Derive UI from capabilities, never from a `providerId === "x"` chain.
- Any new `innerHTML` path must escape *before* applying inline markdown, the way
  `md()` does.

Rules for all of it: [`/CLAUDE.md`](../../CLAUDE.md).
