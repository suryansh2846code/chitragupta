# Signing in once — how a site stays connected

> The plan for "the user connects LinkedIn once and never logs in again", and
> why the obvious two ways to build it are both wrong for this product.
>
> Sits on [`../BROWSER.md`](../BROWSER.md), which settled the browser itself.

## The three ways this is built

### A. Hand the credentials to a broker — what Turnstone does

Turnstone routes LinkedIn through **Unipile** (`auth.unipile.com`): the user
types their LinkedIn email and password into Unipile's hosted page, Unipile
keeps the session on its servers, and the app calls Unipile's REST API for
messages.

It is a legitimate product choice and it is the fastest path to working. It is
also the one thing this product cannot do:

* **The password is typed into somebody else's page.** Not ours, not LinkedIn's.
* **The session lives on their servers**, so every message flows through a third
  party who can read it.
* **It is a per-account subscription**, forever, for something the user already
  has a browser for.
* It contradicts the first line of `/CLAUDE.md` — *everything runs and stays on
  the user's machine* — not at the edges but at the centre.

Worth stating plainly rather than implying: a broker is how you ship this in a
week. We are not shipping it in a week.

### B. Import the user's Chrome or Brave profile — **no**

The intuition is right: the user is already signed into everything, so why ask
again. Three reasons it is still no, in increasing order of importance.

1. **`/CLAUDE.md` already forbids it**: *"Never read another product's
   app-support directory for credentials or models."* That rule is not about
   browsers specifically, and it applies exactly.
2. **It is indistinguishable from what infostealer malware does.** Reading
   Chrome's cookie jar and decrypting it with the `Chrome Safe Storage` key from
   the Keychain is *the* signature behaviour of credential stealers. Antivirus
   and EDR flag it, macOS will prompt for Keychain access in a way that looks
   exactly like an attack, and Chrome has been adding App-Bound Encryption
   specifically to stop it. Being the good guy doing the bad-guy thing is not a
   defence a user can verify.
3. **It would not work reliably anyway.** Profiles lock while Chrome runs,
   cookie encryption is bound to the OS user, and the sites that matter most
   bind sessions to more than cookies. A feature that works for some sites, some
   of the time, silently, is worse than one that asks.

### C. Our own browser, our own profile — **this one**, and it is mostly built

`browser/chromium.py` already launches a **persistent** context against
`~/Library/Chitragupta/browser/profile`, and deliberately **not headless**:

> *a user who can watch is a user who can stop it*

Which means the thing the user asked for — sign in once, stay signed in — is
already the design. Cookies written during a visible sign-in are in our profile
and are there on the next launch. `chromium.profile_sites()` already counts them.

Nothing is imported, nothing is brokered, no password touches our code: the user
types it into the real site, in a real browser, in a window they can see.

---

## What is built

Reading already worked. Connecting is now built — `browser/signin.py`, four
endpoints, and a landing check that knows the difference between "not allowed"
and "signed out".

### 1. A "Connect a site" flow — **built**

One button per site. It opens our Chromium, visibly, at that site's sign-in
page, and waits. The user signs in — including any two-factor step, which is a
reason this must be a real window and not a scripted form fill. When they are
signed in, we record the origin grant and close the window.

What decides "signed in" is the same landing check `session.py` already runs
after every navigation: the address the browser ended up on. Nothing about the
page's text is trusted for this.

The one honest question is how we know the user is done, and the answer is that
we ask. A heuristic watching for a cookie would be wrong on some site, silently,
and *connected* is the claim the whole feature rests on. The user presses Done;
we check they are not still sitting on a sign-in page and say so if they are —
and `force` lets them overrule that, because a heuristic that cannot be
overruled is one that locks people out of their own accounts.

**The sign-in window drives the driver, not `Session`.** `Session` is what
agents hold, and its origin check is the only thing between a page saying *"now
go to attacker.example"* and an account. Signing in has to reach a site nobody
has granted yet — that is what signing in *is* — so `chromium.open_driver()` was
split out and the boundary gained no exception. A boundary with an exception in
it is not a boundary.

### 2. Sessions expire, and must say so — **built**

A session that quietly lapses turns every agent answer into *"I could not find
anything"*, which reads as the feature being broken rather than the login having
ended. The landing check already catches a redirect to a login page; what is
missing is treating that as **a reconnect prompt**, not a read failure. The
grant stays; only the session needs renewing.

Landing on a sign-in page for a site that *is* granted now returns
`needs_signin` with the page **dropped**, not rendered. Handing an agent a login
form means it reads one and reports on it, and *"Sign in to LinkedIn"* is a
perfectly coherent summary of a page nobody wanted summarised — it reads to the
user as the feature being broken rather than the login having ended.

Only the **path** may stop a read (`is_sign_in_url`). A title may only advise
(`looks_like_sign_in`): *"Sign up for our newsletter | BBC News"* is an article,
and refusing it would make a legitimate page unreadable with nothing on screen
to say why. Writing the test is what turned that up.

### 3. The Connectors screen shows what is connected — **half built**

`DELETE /api/browser/sites/{host}` now clears the cookies as well as the grant —
`chromium.forget_site()`, matching `.host` so a token left on `www.` or `m.` does
not survive a disconnect the user was told had happened. It costs a browser
start, which is the price of not editing an encrypted cookie database on disk.

**Still to build: the screen itself.** `GET /api/browser/sites` and
`profile_sites()` have what it needs; what is missing is the frontend — a list
with Connect, Reconnect and Disconnect per site. That is the frontend lane's.

---

## Per app, because they are not the same

Routing each to its best path matters more than treating them uniformly. A
browser is the fallback for the ones with no other way in — not the default.

| app | best path | why |
|---|---|---|
| **Reddit** | **its own API** | free, documented, no browser needed. Using a browser here would be choosing the fragile option. |
| **Telegram** | **already built** — MTProto | a real client API. `connectors/telegram.py`. |
| **Slack** | **already built** — Web API | official, user token. |
| **WhatsApp** | browser, WhatsApp Web | the persistent profile *is* the linked-device model, and going through the official web client is materially safer than a reimplemented protocol. Ban risk is lower than `whatsmeow`, not zero. |
| **LinkedIn** | browser, read-mostly | no API for a normal developer. They detect automation aggressively; reading your own feed at human pace is a different thing from scraping, and the distinction is worth keeping. |
| **X** | browser | the API is priced out of reach. Aggressive bot detection. |
| **Discord** | browser for DMs | a bot token covers servers and cannot see DMs. User-token automation is bannable; a real browser session is less clearly so. |

**Say the risk in the UI, once, before the user connects one of the bottom four.**
Not buried in a doc. A connect button that does not mention it is a promise we
have not earned.

---

## Order of work

1. ~~**Connect a site**~~ — **done**. `browser/signin.py`.
2. ~~**Reconnect on expiry**~~ — **done**. `Reading.needs_signin`.
3. **The Connectors list** — the API half is done; the screen is not.
4. **Reddit via its API**, because it is the one that should not be a browser.
5. Revisit `may_act` only after all of the above have been used for a while.
   It is still ungranted, and `test_browse_tools.py` fails if a write tool lands
   without joining `permissions.NEVER_UNATTENDED` in the same commit.

## Decided, so it is not re-argued

* **No third-party broker.** Credentials do not leave the machine.
* **No reading another browser's profile.** Forbidden by `/CLAUDE.md`, and it is
  the shape of malware.
* **The sign-in window is visible, always** — and *visible* means raised, not
  merely un-minimised. The shared browser rests minimised so a second
  application does not sit on top of the user's work, and the page is shown
  inside the app instead; signing in is the one thing that cannot work that
  way, because the password goes into a window the *site* owns. `signin.begin`
  therefore brings the real window forward and `_clear` puts it back. Two
  halves: `Browser.setWindowBounds` takes it out of the Dock, `Page.bringToFront`
  raises it — bounds alone leaves it behind the app that asked for it, which is
  a window that did not appear as far as anybody looking at the screen is
  concerned. And a browser set to run with no window at all
  (`chromium.runs_hidden`) refuses the flow rather than promising one: there is
  nowhere to type a password, so the card would wait forever.
  It is where two-factor happens, and a user who can watch is a user who can
  stop it.
* **We never type the password.** The user types it, into the real site.
* **Disconnect clears cookies**, not just the grant.
