# `chitragupta/browser/` — the browser, and the boundary around it

Read [`origins.py`](origins.py) before changing anything here. It is the only
thing between a page that says *"now go to attacker.example"* and an agent signed
in to the user's accounts, and every other module in this package assumes it
holds.

| module | owns |
|---|---|
| `origins.py` | **which sites an agent may reach, and for what** |
| `page.py` | a page as bounded, ref-bearing, quarantined text |
| `session.py` | navigating, and the landing check after every navigation |
| `chromium.py` | the profile, the one-time download, cleaning up what we spawn |
| `signin.py` | connecting a site once, and what a lapsed session looks like |
| `driver.py` | a real Chromium: ARIA snapshots, parsed, on a thread of its own |
| `trouble.py` | **what a browser failure is called, and what to say about it** |

- **The unit of consent is the origin**, granted per capability. A browser has no
  recipient to check the way an email does, so `agents/permissions.py`'s
  allow-list cannot be extended here — the reasoning is in `origins.py`.
- **The check happens at the tool, never in the model.** A page that names
  another site produces a refusal, not a decision by something that just read a
  stranger's text.
- **Check the landing, not the request.** A granted page can redirect anywhere,
  so the address that decides is the one the browser ended up on. A refused
  landing drops the page entirely — its text must not enter the context window
  to argue about being allowed.
- **Page content must never be able to close its own quarantine fence.** It
  cannot be made safe; it can be made unmistakably marked. See `page._defuse`.
- **Refs, never selectors.** A model that can emit JavaScript into a logged-in
  page controls that account.
- **Signing in drives the driver, never `Session`.** Connecting has to reach a
  site nobody has granted yet — that is what connecting *is* — so
  `chromium.open_driver()` exists and the boundary gained no exception.
  `Session` is what agents hold; a boundary with an exception in it is not one.
- **Only a path may stop a read; a title may only advise.** `is_sign_in_url`
  blocks, `looks_like_sign_in` suggests. "Sign up for our newsletter | BBC News"
  is an article, and refusing it would make a legitimate page unreadable with
  nothing on screen saying why.
- **A lapsed session is not a refusal.** A granted site landing on its own login
  page returns `needs_signin` and **drops the page** — an agent handed a login
  form reads one and reports on it, which the user sees as us being broken.
- **An identity provider refusing us is not our bug, and must not read like
  one.** Google blocks OAuth from any automated browser — ours reports
  `navigator.webdriver = true` and a Chrome for Testing user agent — so
  "Continue with Google" lands on *"This browser or app may not be secure"*.
  `signin.sso_was_refused` names that page and says to sign in to the site
  directly instead. **Do not fingerprint-spoof past it**: it is an arms race on
  Google's schedule, and losing it costs the user their real Google account.
- **The refusal lands in a popup, so the check reads every window.** "Continue
  with Google" opens a second window and Google answers in *that* one — the
  window we navigated is still on the site's own login page. A check that read
  one page therefore found nothing and fell through to "finish signing in, then
  press Done", which is the exact loop the refusal text exists to end. A popup
  counts only while the driven window is still on a sign-in page: somebody who
  gave up on the button and typed their password leaves that refused popup open
  behind them, and a refusal is not a heuristic `force` may overrule.
- **A refusal is the one waiting state with nothing to overrule**, so `status()`
  carries `sso_refused` and the card keeps its button reading "Done" instead of
  "Done anyway". Offering an override that is turned down anyway is a button
  that fails twice and explains itself neither time. The flag is *cleared* when
  the generic heuristic fires next, or somebody who walked away to the site's
  own form would be denied the second press they now need.
- **`driver.windows()` is not a fifth method on the `Driver` protocol.** The
  sign-in flow is its only caller, and it already drives the driver directly. An
  agent's `Session` reads the one page it navigated, deliberately: a page that
  can `window.open` anything must not get a lever on where the next read comes
  from.
- **Disconnect ends the session, not just the permission.** `forget_site()`
  clears that host's cookies; a grant dropped while the user stays signed in is
  a lie about what the button did.
- **A refusal a grant would fix names the grant — the site *and* the
  capability.** `Verdict.grantable` alone produced the worst dead end in the
  package: acting on a readable site refused with *"Changing anything on {host}
  needs your approval"*, `grantable=None`, and an approval card that had been
  removed a release earlier. So the agent had no route out, nothing above had a
  site to offer, and the sentence told the user to wait for a tap that was never
  coming. `Verdict.needs` is `READ` or `CHANGE`, it survives `may_act`
  delegating to `may_read` (or the offer is a one-press control that takes two
  presses), and `tests/test_browser_origins_invariant.py` runs the "granting
  what was offered actually unblocks it" check over **both** capabilities —
  which is what caught the delegation losing it.
- **What a verb is allowed to do is decided by `driver.ACTS`, and nothing
  restates it.** It is a dispatch table, not an `if` chain and not a tuple of
  names: adding a verb used to mean editing the list of what is allowed, the
  code that did it, the tool, the unattended floor and a test, and the first two
  could silently disagree. `CHANGING_ACTS` is the split that everything above
  derives from — `Session.act` picks which origin check to run from it, and
  `permissions.NEVER_UNATTENDED_TOOLS` is *generated* from it rather than typed
  out three files away. A new act is therefore gated the moment it exists, which
  is the direction this has to fail in.
- **`reveal` is a read, and that is deliberate.** Scrolling an element into view
  sends nothing and presses nothing; the only state it changes is a list
  deciding to draw more of itself, which is reading. Gating it as a write would
  mean a user had to allow *changes* before an agent could read past the first
  screenful — and the thing they were agreeing to would not be the thing they
  were being asked about. Without it an agent sees thirty rows of a hundred and
  reports the thirty-first as absent.
- **A modal is the one page state that makes every other element unusable, so
  it is said on the read and not discovered by failing.** `_hints` puts one
  sentence on any page carrying a `dialog`, and `dialog`/`alertdialog` are in
  `INTERACTIVE_ROLES` so there is something to send Escape *to*. Without both,
  an agent could see `dialog: View recent calls…`, correctly work out it was in
  the way, and have no means of addressing it — the reported transcript spent
  four round trips and a full snapshot each discovering by failure what one
  line could have told it, then asked the user to close the popup by hand.
- **A click that could not land is not a slow page, and the advice is the
  opposite.** `Trouble.BLOCKED` and `ELEMENT_UNUSABLE` exist because both used
  to arrive as a bare timeout and be handed `SLOW`'s *"open the same address
  again"* — the one action that guarantees the modal comes back. Playwright
  names the API at the front of its message (`Locator.click:` versus
  `Page.goto:`), which is how the two are told apart.
- **`driver.summarise`, never `str(exc)[:300]`.** Measured on a real blocked
  click: the message is 520 characters, `intercepts pointer events` begins at
  495, and the truncation cut at 300 — so the one fact identifying the failure
  never reached `classify`, and what did reach it was 300 characters of retry
  log. Keeping the first line plus the named reason is both the useful part and
  the cheap one: 67 characters instead of 300.
- **An agent that repeats itself is told so, by the second time.**
  `browse_tools._stuck` wraps every page-tool *result* — not just the browser
  errors, because half the attempts in the reported transcript failed on a
  refusal — and after `STOP_AFTER` identical failures the result says to stop
  and tell the user. Better wording is most of the answer; this is the floor
  under it, for the turn where the model was going to loop anyway.
- **`press` takes a key off `NAMED_KEYS` and never a free string.** A string
  Chromium interprets is a keyboard-shaped way around everything else here:
  `Control+V` is a paste and a modifier chord reaches the browser's own menus.
  The list is the keys that navigate and dismiss, and nothing on it composes.
- **Acting is one decision per site, not one per keystroke.** `origins.may_act`
  is off until the user turns it on for that host, and that press *is* the
  consent — after it, every changing verb (`browse_click`, `browse_type`,
  `browse_submit`, `browse_select`, `browse_press`) runs without asking again. They shipped as approval-card actions and it was
  unusable: one WhatsApp reply is find → click the chat → type → send, so a
  person said yes four times for one sentence, and `/CLAUDE.md` already knows
  what that costs — *"a tap nobody reads by the fourth time is not consent."*
  `docs/BROWSER.md` §5 had it right: an origin is promoted to "act freely",
  per site, having seen it work.
- **They are tools, and the floor under them is `NEVER_UNATTENDED_TOOLS`.**
  That list is the tool-side twin of `permissions.NEVER_UNATTENDED`, which is
  derived from the action registry and has nowhere to name a tool. A routine
  reads text a stranger wrote, so the write tools check
  `permissions.unattended()` themselves and refuse — no site setting lifts it,
  and the refusal says so rather than sending an agent hunting for a permission
  that does not exist. `routines.run_routine` wraps the **whole turn**, because
  with a browser the tool *is* what reaches the world: gating only the actions
  a turn proposes was enough when a routine's tools could only read.
- **The ref proves the model saw it; the approved label is what executes.** A
  ref alone is too brittle — a live app re-renders while a person reads the
  card, which produced *"I didn't have a fresh, valid reference to the message
  box"* on pages where the box was plainly there. So `Session._resolve` tries
  the ref, then falls back to the element's **accessible name as printed on the
  card**, which is what the user actually said yes to. Both come out of the
  current snapshot, so a name invented by injected page text still finds
  nothing. What crosses into the driver is `role␟name` and never a selector.
- **A page that is still loading is not a failure and not a refusal**, and an
  agent needs something to wait *with* or it hands the waiting back — *"give it
  a bit more time on your end, then tell me to try again"*, which turns five
  seconds into a conversation and asks the user to poll for us.
  `browse_tools.browse_wait` is that something: bounded, a failure on timeout so
  the loop cannot read it as success, and advertised by a line appended to any
  read that looks half-finished. `driver.settle` does **not** cover this — it
  waits for the network to go idle, which an app holding a WebSocket open never
  does, and it gives up after three seconds by design because it is a guard
  against a redirect rather than a wait for a slow site.
- **A row is a control.** `INTERACTIVE_ROLES` covers `listitem`, `row`,
  `gridcell` and friends, because a modern app's most important control is
  rarely a `<button>` — a chat list, a mail list and a search result are rows.
  Leaving them out is why an agent could see the conversation it wanted, name
  the person, and report that there was no ref to click.
- **No internal reaches the card.** It shows the element's own name and the
  site's address. `ref` used to be printed on it — an id the user cannot check,
  cannot act on and did not ask for.
- **Where a click lands is checked like any other navigation.** A click is the
  likeliest thing on a page to navigate, so `_land` decides afterwards and a
  refused landing drops the page.
- **Anything we spawn, we clean up — across runs**, through
  `models/login_processes.py`. A browser holds a profile lock; 158 orphaned
  login processes is the precedent.
- **There is one browser. `chromium.shared_driver()` owns it, callers borrow it,
  and nobody else starts or closes one.** The profile permits exactly one
  Chromium — which is not a quirk to route around, it is the shape of a cookie
  jar that must not have two writers. Three callers used to launch their own
  (`signin.begin`, `forget_site`, every agent via `open_session`) and three used
  to close one, and *every* browser failure this package produced came out of
  that: `ProcessSingleton` when two launched, "Opening in existing browser
  session" when Chromium merged them, "Target page, context or browser has been
  closed" when one closed another's, and browsing dead until the app quit when
  one was left open. Each was fixed on its own and the next one arrived.
- **A borrower parks the browser, it does not close it.** `signin._clear` sends
  it to `signin.PARKED` so it is not left on somebody's feed. Closing is the
  app's decision — teardown, or the user deleting the profile — never a caller's,
  because one caller finishing must not end everybody else's page.
- **A sign-in that could not open its window says so, and lets go.**
  `signin.begin` navigated under `suppressed`, so a failure left the card
  reading *"A browser window is open at {host}… press Done"* over nothing, and
  `_state.current` still set — which refused the retry as "already signing in".
  The user was told to do something impossible and then stopped from trying
  again.
- **One browser is one window, and a person may be in it.** While a sign-in is
  live, `browse_tools` refuses agent reads with `FOR_AGENT[Trouble.SIGNING_IN]`
  rather than navigating the window somebody is typing a password into. It is a wait,
  not a refusal, and it ends at Done or Cancel.
- **A browser failure is named in exactly one place: `trouble.py`.** That
  question used to have four answers — `browse_tools` had a string-matching
  table and wording for a model, `api/routes/browser` had three
  `raise HTTPException(..., str(exc))`, `signin.begin` swallowed it under
  `suppressed` and then reported success, and the Browser screen's stale-handle
  case had nothing at all. Three of the four were wrong about at least one
  failure, and the cost was measured: a navigation that timed out was reported
  to a user as *"the browser itself failed to start"* when the browser was
  running and stayed running for another day. Fixing that in `browse_tools` left
  the HTTP route still printing *"Page.goto: Timeout 20000ms exceeded. Call
  log…"* for the identical failure, because the rule lived in two places and
  only one of them got edited.
  `classify()` returns a `Diagnosis`: which `Trouble` it is, whether the cached
  handle is **stale**, and whether retrying **could** work. Those are facts
  about the browser and do not vary by audience. Only the wording does —
  `trouble.for_person` for a human, `browse_tools.FOR_AGENT` for a model — and
  both select on the same `Trouble`, so a failure nobody has words for cannot
  reach only one of them.
  **It is a leaf and must stay one.** `tests/test_import_layering.py` freezes
  the `browser` cycle at `{chromium, session, signin}`; `trouble` imports
  nothing from this package, which is why `BrowserError` and
  `BrowserNotReadyError` live there now and `driver`/`chromium` re-export them.
  Recovery is **not** there: dropping the shared browser is `chromium`'s,
  because `chromium` owns it — `chromium.recover_from(diagnosis)`.
- **"Closed" and "would not start" and "slow" are three failures with three
  different answers**, and collapsing any two inverts the advice. A locked
  profile cannot be fixed by retrying. A browser that *was* alive is fixed by
  nothing else — and its dead handle must be dropped first, because a driver's
  thread outlives its browser and `_ensure_started` finds it alive and never
  relaunches. A slow page is neither: the browser is fine, the session is fine,
  and throwing either away would lose an agent its refs for nothing.
  We are the usual cause of the middle one: connecting a site opens a browser
  and closes it on Done, and Chromium hands a second launch on the same profile
  to the first process ("Opening in existing browser session"), so the two are
  one browser and closing either closes both.
- **A navigation arrives at `driver.ARRIVED`, never at `load`.** `load` waits
  for every avatar, font and thumbnail an app pulls in. Measured on WhatsApp Web
  with a signed-in profile: `domcontentloaded` 0.8s cold, `load` 5.7s cold on a
  fast link and past `TIMEOUT_MS` on a real user's. Nothing downstream wanted
  `load` — `settle` guards the redirect, `_land` re-checks the origin on every
  read, and "the app has not drawn itself yet" belongs to
  `browse_tools._unready_hint` and `browse_wait`, which were unreachable while
  the navigation itself was failing.
- **The browser's own window is minimised, and the app shows the page.** The
  picture is polled as JPEG frames by `web/webscreen.js` through
  `/api/browser/view`, and clicks and keys go back through `/view/input` —
  which is why `driver.VIEWPORT` is fixed: image pixels and page pixels then
  differ by one scale factor rather than two.
  **"Visible, not headless" is kept rather than dropped.** That rule is about a
  person being able to watch and to stop, and about MFA needing a window
  somebody can reach; both are more true on a screen inside the app than in a
  window behind it. Headless was the wrong way to get there — it changes the
  fingerprint, and the fingerprint is the one thing about this browser that
  currently works for signing in. Off-screen was too: macOS clamps a window
  back onto the display. `/view/window` brings the real one back for anything
  that needs it.
  **No agent reaches any of it.** A tool that could click at a coordinate would
  walk straight past refs, the origin check, and everything else here.
- **A minimised window is still an application, so it is still in the Dock.**
  `chromium.runs_hidden()` is the user choosing to give that up: hidden means
  *headless*, and the difference is one token — measured, not assumed. The
  user-agent says `HeadlessChrome` instead of `Chrome`; `navigator.webdriver`
  is already true either way, and brands, plugins and the WebGL renderer are
  identical. What it really costs is the window, so there is nothing to bring
  back for a file picker or a system prompt — which is why it is off by
  default and per machine. Patching the bundle's `Info.plist` with
  `LSUIElement` is **not** an option: Chromium validates its own bundle and
  dies with SIGTRAP.
- **Our cleanup must not surface as the user's problem.** Killing the browser is
  a crash as far as Chromium is concerned, so the next launch would otherwise
  open with *"Chromium didn't shut down correctly. Restore pages?"* — offering
  to reopen the tabs of somebody's last sign-in. `driver.LAUNCH_ARGS` carries
  `--hide-crash-restore-bubble`; Playwright does not pass it for a persistent
  context.
- The `Driver` protocol in `session.py` is the seam. The fake behind it is why
  the boundary, the budget and the quarantine are testable with no browser and
  no network — keep it that way.
- **Parsing a snapshot is a pure function** (`driver.parse_aria`). It is the part
  most likely to be wrong, and it is tested without a browser precisely because
  it does not live inside the driver.
- **`goto` returning is not the same as having arrived.** A `<meta refresh>` or a
  script setting `location` runs after load — which is how an expired session
  redirects — so `driver.settle` runs before any snapshot. Without it the
  boundary decides about a page the browser has already left.
- **Playwright owns one thread and nothing else touches it.** Its sync API
  refuses to run inside an asyncio loop, and this keeps one browser, one page,
  one caller at a time.

Decisions and what the building changed: [`docs/BROWSER.md`](../../docs/BROWSER.md).
Rules for all of it: [`/CLAUDE.md`](../../CLAUDE.md).
