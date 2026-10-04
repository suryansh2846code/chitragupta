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
| `tools.js` | what one agent may use, with switches — drawn into the profile's Permissions tab and into the agent builder |
| `diagnostics.js` | *What just happened* — the log, read-only |
| `browser.js` | websites agents may read, on the Connectors screen |
| `webscreen.js` | the browser itself, shown and driven inside the app |
| `appearance.js` | what one agent looks like — the profile's Appearance tab |
| `autonomy.js` | the composer's mode pill — how much the open agent decides alone |
| `profile.js` | the agent profile popup — one agent's identity, files, model and permissions |
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
- **The tools panel is one card per app, with two switches on it.** The axis
  has moved twice. Sixty-four switches under thirteen headings became four
  cards by *reach* — what stays on this machine, what touches an account, a
  website, your files — which is the right question for the **gate** and the
  wrong one for a person: it put Gmail, the calendar, Telegram, Notion and
  Linear on one card called "Your connected accounts", so a user who wanted to
  say *read GitHub, leave my mail alone* had no control that said it. The card
  is the app now, and under its name is **Read** and the one word that app's
  changes actually are. Both axes still come down the wire — `app` and
  `access` on every row, derived server-side from the capability each tool
  declares. The screen renders them and decides nothing; a consumer that
  re-derived which tools are dangerous is the name chain this file carries
  three other warnings about. `group` is still sent and is still the **gate's**
  axis (`request_permission`, `prompt._withheld` and `tool_snapshot` all reason
  in it), so the panel reads it only as the fallback for a server that predates
  `app`.
  **`access` is the server's word and the switch label is the person's**, and
  they are not the same word. Comparing the UI labels straight against `access`
  matched neither `write` nor `destructive`, so the Change and Irreversible
  switches rendered as nothing while their tools sat in the disclosure below,
  switched on — a panel omitting a switch for something an agent can do is
  worse than the sixty-four it replaced. `bucketOf` is the mapping.
  **The second switch is never one generic word.** `/CLAUDE.md` forbids folding
  `outbound` or `destructive` into "change": a tap given for one must never
  silently cover the other. So the word comes from `tool_facts.App`
  (`write_label`, `run_label`) — Change on a web page, Write on a file — and
  running code is its own switch on the one card that has three tiers.
  **Between the two switches is the roll-up.** `coveredBy` prints what each one
  covers in the names a person reads. A switch labelled "Read" over a collapsed
  list says nothing about whether reading includes scrolling or going back, and
  the only way to find out was to open the disclosure — the wall this layout
  replaced.
  **A card switch is one PATCH.** Looping the per-tool toggle would send one
  request per tool, each carrying the whole list, and whichever replied last
  would win — so turning a tier on could land half on, depending on the
  network. The per-tool rows stay, folded into a disclosure: the complaint was
  that per-tool control was the only thing on offer, not that it should go. A
  tier somebody part-granted that way says "3 of 7 on" rather than showing a
  plain off over four live tools.
  **The always-on card draws no switch anywhere on it**, the disclosure
  included. Its heading says "not something to switch" and used to say it over
  a column of switches, which is the screen arguing with itself —
  `tool_facts.ALWAYS` says the switches were the half that was wrong.
- **Some changes are actions, and an app that only says "Read" is lying.**
  Sending mail, adding an event, running somebody else's verb in GitHub — none
  of them is a tool, so there is nothing here to toggle. `askRow` says what
  happens instead. Saying nothing is what this used to do, and an app showing a
  single Read switch reads as an app that cannot do anything else, which is
  what sends people hunting through settings for a control that does not exist.
  **And it answers "who may it reach" in the row, not behind a button.** The
  first version ended that row with *Who it may reach*, which opened the global
  allow-list on the Actions screen. From a connector card that was a dead end
  twice over: the list holds every app's grants mixed together, and the only
  thing it can **add** is an email address — a connector key is
  `server:tool@scope`, minted by the call it describes and never typed. So a
  user pressed a button about DeepWiki, landed on a screen about email, and had
  nothing to do there. Exactly the failure `more_screen` exists to stop, rebuilt
  by hand. `reachFor` slices the one list by the card it is drawn on — by
  **server id** for a connector, never by label, because a rename must not
  change which permissions look like its — and each grant gets the one control
  a standing permission needs: take it back.
  **Read-only, and it says what it governs.** Two more versions of this row
  were wrong before it settled. The second put a box on the card to add a
  grant; the third took it away again, because `approvals.run_or_queue` is
  "the seam every **unattended** action goes through — interactive chat does
  not come this way". So the list changes nothing a person will ever see in a
  conversation, and a row that read *"always comes to you as a card you
  confirm"* and then offered a control undoing it was two true sentences about
  two different situations, naming neither. It says *when an automation runs it
  with nobody watching* now, and the chips say *across every agent*, because
  the list is global and the card is not. A grant is **made** where the exact
  value is known and somebody has just learnt they want one: the blocked
  approval, which already offers it. An empty list draws nothing — there is
  nothing worth saying about it on a screen that cannot change it.
- **Leaving this panel means leaving the profile it is a tab inside.** Every
  button that opens another screen used to navigate **behind** the dialog: the
  page changed, the modal stayed up, and the user was looking at a profile over
  a screen they had just been sent to. One of the five closed first — the one
  written last — and the other four did not, which is a fix applied per case
  rather than to the shape. `goElsewhere` is the seam now, and it returns
  whether it actually left: `closeAgentProfile` asks before discarding unsaved
  changes, so "close then go" navigated anyway when the user said no.
- **Nothing on this screen may be a control that does nothing — in either
  direction.** Three permissions had a working endpoint and nothing in the app
  that called it: `/api/agents/folders`, `/api/agents/{id}/connectors`, and the
  unattended allow-list sitting three screens away under *Model*. So "Your Mac
  → Read" granted a tool whose only possible answer was *"No folder has been
  opened to agents yet"*, and no screen anywhere opened one. Each is now a
  strip on the card it belongs to — `cardStrip` — and where it is a control
  rather than a fact, the control is **in the card**: a button that sends
  somebody to another screen is the thing that lost them last time.
  `_PANEL` holds the three answers and `loadPanelPermissions` fetches them
  **after** the first render, never into it: `/api/agents/{id}/connectors`
  probes every configured server, and blocking the panel on it is the 2.47s
  stall `api/concurrency.py` exists to stop. A key that is not a list means
  "not back yet" and draws nothing — `[]` is a fact worth stating and an
  unanswered fetch is not, and a panel that claimed "no site is allowed" before
  asking would be wrong for the first few hundred milliseconds of every open.
- **There are no presets, and the per-card "Allow all" is gone with them.**
  Three buttons above the list — *Allow everything · Read only · Nothing yet* —
  and a bulk button per card made sense when a card was four switches over a
  wall of sixty-four. A card is two switches now, so the preset row was a
  second way to do a thing that takes one press, and "Allow everything"
  silently included running code. `PRESETS`, `preset_tools`, `_preset_merge`
  and the `preset` field on both bodies went with it.
- **The agent BUILDER is this panel, not a second drawing of it.**
  `openAgentModal` calls `renderAgentTools` with a **draft** agent — `draft:
  true`, its tools in the object on screen, nothing sent until Create — so the
  cards, the roll-ups and the disclosures cannot land on one screen and not the
  other. They had not: the builder was still arranging itself by the thirteen
  category headings two releases after the panel had moved on. A draft is
  offered no control it cannot use — the folder box, the grant switch and every
  "open that screen" button are absent, because the modal is a modal and the
  screen behind it would come up behind it.
- **The screen must not argue with itself in either direction.** A tier switch
  patches the rows under it, and `syncGroupSwitches` brings the switches *above*
  a row with it — turning one browser tool on inside the disclosure used to
  leave the switch over it reading a plain "off" over a live tool. The
  `.at-part` count is always in the markup and empty when there is nothing to
  say, because an element rendered only when it has content cannot be filled in
  the one case it exists for.
- **A repaint must not undo what the user opened.** Every permission that lands
  repaints the whole panel; `_openGroups` is what stops that closing every
  disclosure somebody had opened to decide with. `loadAgentTools` only paints
  "Loading…" over an empty panel or a switch to a *different* agent — otherwise
  a repaint is a flash of nothing in the middle of reading.
- **A card says where the rest of its permission is set.** Turning *Change* on
  for the browser grants nothing by itself: which sites is a list on the
  Connectors screen, and a user who turned everything on here and was still
  refused had no way to learn that from this panel. The card names a screen id
  (`more`); `screenOpener` resolves it, and an id the frontend does not have
  draws no button — never a control that goes nowhere. `allowlist` is the one
  that is not a screen any more: it is a section at the bottom of this panel,
  so the opener scrolls instead of navigating.
- **The agent profile is a popup, and its markup is in `index.html`.** The
  focus observer and the Escape handler in `app.js` bind to every `.modal-bg`
  **once, at load** — an overlay `profile.js` injected later would open without
  taking focus and would never close on Escape, and neither failure is visible
  until somebody uses the keyboard. Escape closes the topmost modal by clicking
  the button in its `.modal-head`, which is why the unsaved-work guard lives on
  that button rather than in a keydown handler of its own: one rule, both ways
  out. A dialog rather than a screen because you open it *about* the agent you
  are talking to, and getting back to the conversation should not be a
  navigation.
- **`profile.js` composes; it does not redraw.** Permissions come from
  `loadAgentTools`/`renderAgentTools` with the popup's own container passed in —
  the entry above about the agent builder is the same rule, and this is the
  third surface. Its globals are `prof*`, never `ap*`: `appearance.js` already
  declares `apAgent`, `apDoc`, `apDirty` and `apEditor` at the top level of this
  shared scope, and redeclaring one with `let` is a SyntaxError that kills the
  whole file — the symptom being a ⋯ that does nothing.
- **Agents & tools and Appearance are tabs, not screens.** Both were settings
  pages with "which agent?" on them — a dropdown on one, a roster of every agent
  on the other — and both questions are already answered by the time a profile
  is open. The panels, the rail items, the dropdown and the roster are all gone;
  `loadAgentTools(id, box)` and `mountAppearanceFor(box, id)` are what is
  left, and the allow-list — the one permission that is global rather than per
  agent — followed the approvals queue onto the Actions screen.
  `openToolsScreen` and `openAppearanceScreen` survive as redirects into the
  profile, because four call sites and two old nav names still reach for them
  and a link that lands nowhere is worse than one that lands near.
- **Creating an agent hands over to its profile.** Building one does not end
  at a name — it has a face, a way of working, a model and a memory, and every
  one of those already has a screen. `#amCreate` opens that screen, so creating
  and changing an agent are the SAME screen rather than two that drift. A
  persona picker or an avatar editor inside the builder would be the second
  drawing this file's agent-builder entry already warns about, which is why the
  tool list there is `renderAgentTools` and not a copy of it.
- **The mode pill and the Persona tab are one setting.** Both read the levels
  from `GET /api/agents/{id}/persona` and write with `PUT` — a mode set in the
  composer and a mode set in the profile that disagreed would be two settings
  wearing one name. It is **per agent** and re-read on every `selectAgent`,
  because showing the level of the agent you just left is a label about
  somebody else; it stays **hidden until the level has arrived**, because a
  pill reading "Mode" is a control reporting something nobody told it; and the
  label moves before the server answers and **moves back if the save fails**,
  or the pill reports a mode the agent is not in.
- **The persona picker's options come down the wire.** `agents/persona.py` owns
  the traits, the styles, the autonomy levels and the caps; a copy of those
  lists here would be a second copy to keep current, and the one that drifts is
  the one somebody is choosing from. The tab sends every field on save, because
  one document is rendered from all of them server-side — sending only what
  changed would render the rest away.
- **Hold the element, do not re-query it.** The profile's Save button is kept in
  `profSaveEl` by whichever pane built it, and `appearance.js` keeps its five
  parts in `apBox` / `apSaveBtn` / `apResetBtn` / `apNameEl` / `apNoteEl` for
  the same reason: those ids were safe only while the markup was in
  `index.html` and there was exactly one of each. They are built per pane now — `$("#profSave")` from a handler is the
  selector-as-a-claim-about-markup failure this file already records for
  `syncConn`, and a fake DOM is where it showed up first: the lookup returned a
  different element and Save stayed dead after an edit.
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
