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
- **Acting is a second grant, and every act still waits for a tap.**
  `origins.may_act` is off until the user turns it on per site
  (`POST /api/browser/sites/{host}/acting`), and turning it on decides only
  whether an approval card may *appear* for that site — never whether one may
  be skipped. `browse_click` / `browse_type` / `browse_submit` are declared in
  `actions.py` as `Risk.RED`, which puts them in `permissions.NEVER_UNATTENDED`
  by derivation: a routine reads text a stranger wrote, and an injected
  instruction plus a click is an agent acting inside the user's accounts.
- **They are actions, not tools, and that is the safety design.** A browser has
  no `to` field — *Save draft* and *Transfer £4,000* are the same call — so
  there is no key an allow-list could hold and nothing to promote. What a card
  shows is the element's **own accessible name and the page's address**, not
  the agent's description of what it is about to press.
- **The ref proves the model saw it; the approved label is what executes.** A
  ref alone is too brittle — a live app re-renders while a person reads the
  card, which produced *"I didn't have a fresh, valid reference to the message
  box"* on pages where the box was plainly there. So `Session._resolve` tries
  the ref, then falls back to the element's **accessible name as printed on the
  card**, which is what the user actually said yes to. Both come out of the
  current snapshot, so a name invented by injected page text still finds
  nothing. What crosses into the driver is `role␟name` and never a selector.
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
- **One browser is one window, and a person may be in it.** While a sign-in is
  live, `browse_tools` refuses agent reads with `SIGN_IN_IN_PROGRESS` rather
  than navigating the window somebody is typing a password into. It is a wait,
  not a refusal, and it ends at Done or Cancel.
- **"Closed" and "would not start" are different failures with opposite
  advice.** A locked profile cannot be fixed by retrying; a browser that *was*
  alive and has been closed is fixed by nothing else. And we are the usual cause
  of the second — connecting a site opens a browser and closes it on Done, and
  Chromium hands a second launch on the same profile to the first process
  ("Opening in existing browser session"), so the two are one browser and
  closing either closes both. Reporting that as "could not be started, do not
  retry" told the agent the one thing that stopped it healing. `browse_tools`
  also **drops the cached session** there: a driver's thread outlives its
  browser, `_ensure_started` finds it alive and never relaunches, so without
  that the session stays broken for the life of the app.
- **A browser that will not start is explained, never dumped.** Playwright's
  *"Failed to create a ProcessSingleton for your profile directory"* used to
  travel through `tools.run_tool`'s catch-all straight to the model, which
  turned it into "the browser session closed unexpectedly" and offered to try
  again — twice, identically. `browse_tools.BROWSER_BUSY` names the extra window
  and says not to retry. A tool result is user-facing by the time a model has
  repeated it back.
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
