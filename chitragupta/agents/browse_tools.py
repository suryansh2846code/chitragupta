"""Looking at websites the user has allowed, and working in the ones they let an
agent change.

The set splits in two, and the split is the design. **Looking** — `browse_open`,
`browse_read`, `browse_find`, `browse_wait`, `browse_reveal`, `browse_back`,
`browse_sites` — is free on any allowed site. **Changing** — `browse_click`,
`browse_type`, `browse_submit`, `browse_select`, `browse_press` — is
off until the user turns it on for that host, and is refused outright when
nobody is watching (`permissions.NEVER_UNATTENDED_TOOLS`): a routine reads text a
stranger wrote, and that is the one caller that must never click inside somebody's
logged-in accounts. Which side a verb falls on is `driver.CHANGING_ACTS` and is
never restated here — `_gate_for` reads it, so a new act is gated the moment it
exists rather than when somebody remembers.

**Changing has one exception, and it is money.** A per-site decision can mean
"fill a basket here"; it cannot mean "and buy whatever you decide, at whatever
it comes to". So `_may_spend` refuses the control that commits a purchase —
recognised by `browser/purchase.py` from the name the *page* gave it — and the
order goes through the `place_order` action instead: one card, every item, the
shop's own total, and a tap that is asked for every single time. The boxes that
want a card number, a PIN, a password or a one-time code are refused in the same
place and for a simpler reason: we do not hold one and must never ask for one.

`browse_wait` is here because a page that is still loading is neither a failure
nor a refusal, and an agent with no way to wait did the only thing left — it
handed the waiting back to the user and asked to be told when to try again.

**Offered to every agent and gated at execution**, through `_BROWSE` in
`library.BASE_TOOLS` — the same shape as the connector tools beside them. An
agent that cannot *see* the tool tells the user it cannot read a website, which
is false: it can, as soon as they allow the site. The consent is the allow-list
in `browser/origins.py`, not the tool list.

The boundary, the bounding and the quarantine all live in `browser/`; this module
is the thin part on purpose. What it owns is the wording: what an agent is told
when it is refused, which is the difference between an agent that reports "you
need to sign in to payroll again" and one that retries the same URL until its
budget runs out.
"""
from __future__ import annotations

import time
from typing import Any

from ..browser import live, origins
from ..browser.session import Reading, Session
from ..browser.trouble import Trouble
from ..log import get_logger
from .results import ToolResult

log = get_logger(__name__)

#: The live session lives in `browser/live.py`, and these are the names every
#: caller and every test already reaches for.
#:
#: It moved **down**, not away. Placing an order runs from `actions.py` after
#: the user presses Confirm, and `actions` → `agents` → `actions` is a cycle —
#: `agents/permissions.py` reads the action registry at import time. A leaf both
#: halves can read is the first of the three ways out `/CLAUDE.md` names; this
#: delegation is what keeps the move invisible to anything already holding this
#: module.
get_session = live.get_session


def set_session(session: Session | None) -> None:
    """Replace the shared session. For tests, and for a reset after a crash.

    A function rather than a second alias, because it does one thing more than
    `live.set_session`: it clears the repeat counter below. A new browser is a
    fresh start, and whatever was failing before belongs to a session that no
    longer exists — carrying that count across would spend an agent's first
    real attempt on the new page telling it to stop repeating itself.
    """
    live.set_session(session)
    _worked()


# ── not letting an agent repeat itself ───────────────────────────────────
#
# From a real transcript, trying to send one WhatsApp message: *"Closing the
# calls dialog first"* → *"Trying to click directly on the chat row instead"*
# → *"the calls dialog is still blocking"* → *"Let me close that dialog
# first"* → *"I've tried multiple times… I don't want to keep retrying
# blindly."* Four round trips, a full page snapshot on each, and the user was
# asked to close a popup by hand at the end of it.
#
# Better wording on the failure is most of the answer and is why `FOR_AGENT`
# exists. This is the floor under it: a model that is going to repeat itself
# anyway should be told so by the second time, in the tool result, where it
# cannot be missed. Two is the threshold because the first repeat is often
# reasonable — a page really can settle between tries — and the third is
# where this stops being a retry and starts being a loop.
STOP_AFTER = 2

#: The last call that failed, and how many times running. One slot, not a
#: history: the question is only ever "again?", and a dict keyed by every
#: failed call would be a leak nobody ever reads.
_REPEATED: dict[str, Any] = {"key": "", "count": 0}


def _worked() -> None:
    """Anything succeeding clears the count. Progress is not a loop."""
    _REPEATED["key"], _REPEATED["count"] = "", 0


def _again(key: str) -> str:
    """Count one identical failure, and say so once it is a loop."""
    if key != _REPEATED["key"]:
        _REPEATED["key"], _REPEATED["count"] = key, 1
        return ""
    _REPEATED["count"] += 1
    if _REPEATED["count"] < STOP_AFTER:
        return ""
    return (
        f"\n\nYou have now tried this exact thing {_REPEATED['count']} times "
        "and it has failed the same way each time. Stop repeating it. Either "
        "do something different — read the page and work from what is "
        "actually on it — or tell the user what is blocking you, what you "
        "already tried, and what you need from them. Do not call this again.")


def _stuck(result: ToolResult, key: str) -> ToolResult:
    """Tag a failure with a nudge if it is the same one as last time.

    Wraps the *result*, not the call, so every way a page tool can fail — a
    browser error, a refusal, an element that is not there any more — is
    counted by the same thing. Counting only browser errors would have missed
    the transcript this exists for, where half the attempts failed on a refusal
    rather than on an exception.
    """
    if result.ok:
        _worked()
        return result
    note = _again(key)
    return ToolResult.failed(str(result) + note) if note else result


def _remember(reading: Reading) -> None:
    """Keep the fact of the visit. Never the page.

    Only on success, and only for a page the allow-list actually let through —
    a refusal is not work the agent did, it is work it was stopped from doing,
    and the log already has the failure.
    """
    from . import browse_record, connector_grants

    browse_record.record_visit(
        reading.url, reading.title, origins.host_of(reading.url),
        agent_id=connector_grants.acting())


def _answer(reading: Reading) -> ToolResult:
    """One place that turns a `Reading` into what the model sees.

    A refusal is a `ToolResult.failed`, so the loop knows to try something else
    rather than reissuing the same call — `results.py` exists because guessing
    that from the text got all three real failures wrong.

    **Including the loading hint, which only `browse_read` used to carry.** This
    function's first line claims to be the one place, and it was not: the hint
    was written on the read and never on the *open*, which is the more likely of
    the two to land on a loading screen — it is the first look at a page the
    browser has only just arrived at. Opening WhatsApp Web returns *"Your
    messages are downloading"* and nothing else for several seconds, and an
    agent handed that with no hint reports the site as empty. `browse_read`
    keeps its own call because it appends a page-version too; the rule itself
    lives in `_unready_hint` and only ever has.
    """
    if reading.ok:
        _remember(reading)
        return ToolResult(reading.text + _unready_hint(reading),
                          truncated=reading.truncated)

    if reading.grantable:
        # Named so the agent can report it and the user can act on it. The agent
        # cannot grant it — that is a decision only a person makes, and a tool
        # that could widen its own access would make the allow-list decorative.
        #
        # **Which permission, not just which site.** This said "if they want you
        # to read it" for every refusal, including the one where reading was
        # already allowed and only changing was not — so an agent asked to send
        # a message told the user to turn on a setting that was already on.
        site = reading.grantable.split("://")[-1]
        wanted = ("read it" if reading.needs == origins.READ
                  else "change things on it")
        # **Where the control actually is.** This said "on the Browser screen",
        # and the Browser screen is the live page an agent is driving — the
        # per-site switch is on Connectors, under Websites. So an agent refused
        # on a site sent the user to a screen that does not hold the control,
        # which is the same dead end the Agents & tools panel used to be: every
        # switch on, still refused, and nothing naming the list that decides it.
        return ToolResult.failed(
            f"{reading.reason} Tell the user they can turn that on for {site} "
            f"under Connectors → Websites if they want you to {wanted}. You "
            "cannot turn it on yourself. Do not try other addresses for the "
            "same thing.")
    return ToolResult.failed(reading.reason)


# ── what an agent is told when the browser itself will not cooperate ─────
#
# **The wording is here; the verdict is not.** `browser/trouble.py` decides
# which failure a browser handed back, because that question had four answers
# living in four modules and three of them were wrong about at least one case.
# What stays here is the *phrasing for a model*, which is genuinely different
# from the phrasing for a person: an agent needs to be told whether to retry and
# what to say to the user, and a person reading a panel needs neither.
#
# Selecting both tables on the same `Trouble` is the point. A failure nobody has
# words for cannot reach only one of them.
#
# Each entry was bought. A `BrowserError` used to travel to the model as
# whatever Playwright wrote — *"Failed to create a ProcessSingleton for your
# profile directory"* — so the model paraphrased it into "the browser session
# closed unexpectedly" and offered to try again, which failed identically
# because nothing about retrying closes the other window.
FOR_AGENT: dict[Trouble, str] = {
    #: Every browser we open takes an *exclusive* lock on the profile, so a
    #: second cannot start while the first is alive. Retrying cannot close it.
    Trouble.BUSY: (
        "Another browser window opened by this app is still using the browser "
        "profile, so a new page cannot be opened. Tell the user to close the "
        "extra browser window — the one left over from connecting a site — and "
        "that reading will work again straight away. Do not retry this call: "
        "retrying cannot close that window, and it will fail the same way."),

    #: The **recoverable** one. "Target page, context or browser has been
    #: closed" is not a browser that will not start; it is one that was alive
    #: and is not any more, and the usual cause is us — connecting a site opens
    #: a browser and closes it on Done, which ends any page an agent had open.
    #: Reported as "could not be started, do not retry" it was the exact
    #: inversion of the truth: this is the one failure retrying *does* fix.
    Trouble.CLOSED: (
        "The browser window that had this page open was closed — connecting a "
        "site opens and closes one, which ends any page already open. Nothing "
        "is wrong with the sign-in, the site or the permission. Call this again "
        "once and a fresh browser will start; only tell the user if the second "
        "attempt fails too."),

    #: The one the fall-through used to lie about. A navigation that ran out of
    #: time is not a browser that would not start — it was running, and still
    #: was the next day — and the user was told *"the browser itself failed to
    #: start"* and the agent was told not to retry. **Open it again**, not
    #: `browse_wait`: a navigation that raised never reached `Session._land`, so
    #: there is no snapshot and `browse_read` would answer "No page is open yet."
    Trouble.SLOW: (
        "That page did not finish loading in time. The browser is running and "
        "nothing is wrong with the sign-in, the site or the permission — the "
        "page was just slow, which a big web app often is the first time it is "
        "opened. Open the same address again: it is usually quick the second "
        "time. Only tell the user if that attempt fails too, and say the site "
        "was slow rather than blaming the browser."),

    #: The loop this whole entry exists to end. A click that could not land
    #: used to arrive as a bare timeout and be handed `SLOW`'s advice — *"open
    #: the same address again"* — so the agent re-opened the page, met the same
    #: modal, and tried again. The transcript reads: *"I'll try opening
    #: WhatsApp Web again"*, then *"the calls dialog is still blocking"*, then
    #: *"I've tried multiple times… I don't want to keep retrying blindly."*
    #:
    #: So: say what is in the way, say the two ways through it, and say plainly
    #: that re-opening the page is the one thing that cannot work — because
    #: whatever is covering the page will be there again when it reloads.
    Trouble.BLOCKED: (
        "Something on the page is covering what you tried to use — nearly "
        "always a popup, dialog or notice that opened over it. Do NOT open the "
        "page again: it will come back exactly the same. Read the page, find "
        "the thing that is in the way, and get rid of it first — its own Close "
        "or dismiss button if it has one, otherwise browse_press Escape on the "
        "dialog itself. Then do what you were doing. If it will not go after "
        "one attempt at each, stop and tell the user what the popup says."),

    #: Not a slow *page*. The page arrived; this one control did not become
    #: usable. Re-opening the address is unrelated to it, and the thing that
    #: helps is looking again at what is actually on screen.
    Trouble.ELEMENT_UNUSABLE: (
        "That element never became usable — it may be hidden, switched off, or "
        "the page may still be drawing it. The browser and the page are both "
        "fine, so opening the address again will not help. Read the page "
        "again: either pick the control that is really there now, or use "
        "browse_wait if it looks half-drawn. Do not repeat this same call."),

    #: One browser means one window, and while a sign-in is live there is a
    #: person typing a password into it. A wait, not a refusal.
    Trouble.SIGNING_IN: (
        "The user is signing in to a site in the browser window right now, so "
        "it cannot be used for anything else until they finish. Do not retry "
        "in this turn — tell them you will read it once they are done."),

    #: Deliberately vague, because it is the case we have no name for. It must
    #: not claim to know which component failed — that claim is what produced
    #: the reported bug.
    Trouble.UNKNOWN: (
        "The browser could not do that, and the reason is not one this app "
        "recognises. Tell the user plainly, without blaming their sign-in or "
        "their settings. Try once more at most."),
}


def _browser_failed(exc: Exception) -> ToolResult:
    """Turn a browser failure into something an agent can act on.

    Three steps, and only the middle one is this module's: `trouble.classify`
    names the failure, `chromium.recover_from` puts right whatever it left
    broken, and `FOR_AGENT` says it in words written for a model.

    Never the exception's own text. `/CLAUDE.md`: no stack traces and no
    internals in anything user-facing, and a tool result is user-facing by the
    time a model has repeated it back to somebody. `NOT_SET_UP` is the one
    exception and it is a deliberate one — its message is already written for a
    person ("choose Set up browsing"), so replacing it would lose the single
    instruction that works.
    """
    from ..browser import chromium, trouble

    if isinstance(exc, _SigningInError):
        # Not logged as a browser problem, because it is not one — somebody is
        # using the window for exactly what it is for.
        return ToolResult.failed(FOR_AGENT[Trouble.SIGNING_IN])

    found = trouble.classify(exc)
    log.warning("browser trouble for an agent (%s): %s",
                found.trouble.value, str(exc)[:200])
    # **Before the wording, not after.** A driver's thread outlives its browser,
    # so `_ensure_started` sees it alive and never relaunches: telling an agent
    # to "call this again" without dropping the dead handle first is advice that
    # cannot work. `chromium` owns the shared browser so it owns this; the
    # session is ours, so that half is here.
    if found.stale:
        set_session(None)
        chromium.recover_from(found)
    if found.trouble is Trouble.NOT_SET_UP:
        return ToolResult.failed(
            f"{found.for_person} Tell the user that, and do not retry.")
    return ToolResult.failed(FOR_AGENT[found.trouble])


class _SigningInError(RuntimeError):
    """Not a browser failure: the browser is busy being used by a person.

    One browser for the whole app means one window, and while a sign-in is live
    that window has somebody typing a password into it. Navigating it out from
    under them would lose the sign-in and look like the app fighting them. A
    wait, not a refusal, and it ends at Done or Cancel.
    """


def _refuse_while_signing_in() -> None:
    """Stop an agent taking the window out from under somebody's password."""
    from ..browser import signin

    if signin.status().get("connecting"):
        raise _SigningInError(FOR_AGENT[Trouble.SIGNING_IN])


def browser_trouble(exc: Exception) -> str:
    """The same wording, for a caller that needs the sentence not a `ToolResult`.

    `actions._browse_act` runs behind an approval card rather than as a tool, so
    it has nowhere to put a `ToolResult` — but the rule does not change with the
    caller: Playwright's text never reaches a person, and this module is where
    that text is decided.
    """
    return str(_browser_failed(exc))


def _browser_errors() -> tuple:
    """Every way asking for a browser can raise, as one `except` clause.

    Both exception types come from `browser.trouble` now — they moved down with
    the vocabulary, so that the module which has to *recognise* a browser
    failure does not have to import a driver to do it.
    """
    from ..browser.trouble import BrowserError, BrowserNotReadyError

    return (BrowserError, BrowserNotReadyError, _SigningInError)


def browse_open(url: str) -> ToolResult:
    """Open a page on an allowed site and read it."""
    refused = _may_look()
    if refused:
        return refused
    try:
        _refuse_while_signing_in()
        return _stuck(_answer(get_session().open(url)), f"open:{url}")
    except _browser_errors() as exc:
        # Whether the session survives is decided in `_browser_failed`, by which
        # failure it was: a browser that would not *start* leaves the page an
        # agent holds refs into alone, a browser that has been *closed* has no
        # page left to protect and has to be replaced.
        return _stuck(_browser_failed(exc), f"open:{url}")


def browse_read(since: str | None = None) -> ToolResult:
    """Re-read the current page, cheaply if it has not changed.

    `since` is the fingerprint from a previous read. A page is the most expensive
    thing an agent can ask for and it re-reads after every step, so answering
    "nothing has changed" for a few tokens instead of a few thousand is the
    difference between this capability feeling cheap and feeling reckless on
    somebody's own model plan.
    """
    refused = _may_look()
    if refused:
        return refused
    try:
        _refuse_while_signing_in()
        reading = get_session().read()
    except _browser_errors() as exc:
        return _browser_failed(exc)
    # Both success paths record, including the cheap one: re-reading a page to
    # check it is unchanged is still the agent having looked, and a record that
    # only appears when the page happened to change would be a record nobody
    # could reason about.
    if reading.ok:
        _remember(reading)
    if reading.ok and since and reading.digest and since.strip() == reading.digest:
        return ToolResult(
            f"The page has not changed since you last read it ({reading.url}). "
            "Nothing new to report from it.")
    if reading.ok:
        return ToolResult(
            f"{reading.text}\n[page-version: {reading.digest}]{_unready_hint(reading)}",
            truncated=reading.truncated)
    return _answer(reading)


# ── waiting, which an agent could not do ─────────────────────────────────
#
# A page that is still loading is not a failure and it is not a refusal. It is
# the normal state of a web app for the first few seconds, and WhatsApp Web
# spends thirty of them saying *"messages are downloading"*.
#
# With nothing to wait *with*, an agent did the only thing left: it reported the
# page as unusable and handed the waiting back — *"give it a bit more time on
# your end, then tell me to try again"*. Which turns a five-second pause into a
# conversation, and asks the user to poll on the app's behalf.
#
# `driver.settle` cannot cover this. It waits for the network to go idle, and an
# app holding a WebSocket open never does — it gives up after three seconds by
# design, because it is a guard against a redirect, not a wait for a slow site.
#: The longest a single wait may run. Long enough for a real sync, short enough
#: that a turn cannot disappear into one — and a page that is not ready after
#: this is a page worth telling the user about rather than waiting on again.
MAX_WAIT_SECONDS = 30.0

#: How often to look while waiting. Each look is a full snapshot of the page, so
#: this is a cost as well as a delay; twice a second would buy nothing on a sync
#: measured in seconds.
WAIT_POLL_SECONDS = 2.0

#: Words a page uses about itself while it is not ready yet. Only ever used to
#: *advise* — never to refuse a read, and never to decide anything. A page that
#: says "loading" in an article about loading bays is still a page to read.
LOADING_WORDS = ("loading", "downloading", "syncing", "please wait",
                 "just a moment", "getting your", "connecting")


def _looks_unready(text: str) -> bool:
    """Does this page look like it has not finished yet?

    Two signals, and the first is the honest one: `progressbar` is what the
    accessibility standard has for exactly this, so an app doing its job
    announces its own loading state and `render` prints the role. The words are
    the fallback for the many that do not.

    Only ever used to **advise**. It never refuses a read and never decides
    anything — a news article about loading bays is still a page to read.
    """
    low = (text or "").lower()
    return "progressbar:" in low or any(w in low for w in LOADING_WORDS)


#: How a snapshot announces a modal. `render` prints the role, and a modal is
#: the one page state that makes every *other* element unusable.
_MODAL_ROLES = ("dialog:", "alertdialog:")


def _looks_blocked(text: str) -> bool:
    """Is something modal open over this page?"""
    low = (text or "").lower()
    return any(role in low for role in _MODAL_ROLES)


def _hints(reading: Reading) -> str:
    """Everything worth telling an agent about the *state* of a page it just read.

    **One place, because advice about page state is one kind of thing.** It was
    a single function about loading, and the second kind of advice — a modal
    sitting over everything — would otherwise have arrived as a second function
    called from the same two places, which is how the loading hint came to be
    on `browse_read` and not on `browse_open`.

    Appended outside the quarantine fence, the way the page-version is: this is
    ours, not the site's. Each line is one sentence and only appears when it is
    true, because the point is to save an agent a wasted round trip rather than
    to spend tokens describing pages that are fine.
    """
    lines = []
    if _looks_unready(reading.text):
        lines.append(
            "This page looks like it is still loading. Call browse_wait with "
            "what you are waiting to see, rather than reporting it as empty or "
            "asking the user to wait.")
    if _looks_blocked(reading.text):
        # **Said before anything fails, which is the whole value.** An agent
        # that learns this by having three clicks time out has spent three
        # round trips and sixty seconds finding out what one sentence could
        # have told it — and the transcript that prompted this shows it then
        # gave up and asked the user to close the popup by hand.
        lines.append(
            "A dialog is open on this page. While it is, clicks on anything "
            "behind it will not register — close it FIRST, using its own "
            "close or dismiss button, or browse_press Escape on the dialog "
            "itself.")
    return "".join(f"\n[{line}]" for line in lines)


def _unready_hint(reading: Reading) -> str:
    """Kept as the name the two call sites use. See `_hints`."""
    return _hints(reading)


def browse_wait(until: str = "", seconds: float = 15.0) -> ToolResult:
    """Wait for the open page to be ready, then read it.

    `until` is what you are waiting to see — a chat list, a heading, a button.
    With nothing named, this waits for the page to change at all, which is the
    right question after pressing something.
    """
    refused = _may_look()
    if refused:
        return refused
    from ..browser import page as pagemod

    try:
        session = get_session()
        # **What the agent last saw**, captured before this re-reads. Taking it
        # from `read()` instead would compare the page against itself: a page
        # that finished loading between the agent's last look and this call
        # would then be "unchanged", and the wait would run its full budget
        # over a page that was already ready.
        was = pagemod.digest(session.snapshot) if session.snapshot else ""
        reading = session.read()
    except _browser_errors() as exc:
        return _browser_failed(exc)
    if not reading.ok:
        return _answer(reading)

    want = " ".join(str(until or "").split()).lower()
    budget = max(WAIT_POLL_SECONDS, min(float(seconds or 0), MAX_WAIT_SECONDS))
    deadline = time.monotonic() + budget

    while True:
        if want and want in reading.text.lower():
            _remember(reading)
            return ToolResult(f"“{until}” is on the page now.\n{reading.text}",
                              truncated=reading.truncated)
        if not want and reading.digest and was and reading.digest != was:
            _remember(reading)
            return ToolResult(f"The page changed.\n{reading.text}",
                              truncated=reading.truncated)
        if time.monotonic() >= deadline:
            break
        time.sleep(WAIT_POLL_SECONDS)
        try:
            reading = get_session().read()
        except _browser_errors() as exc:
            return _browser_failed(exc)
        if not reading.ok:
            return _answer(reading)

    # A timeout is a **failure**, so the loop does not read it as "waited
    # successfully" and carry on as though the thing had appeared.
    waited = int(budget)
    if want:
        return ToolResult.failed(
            f"Waited {waited}s and “{until}” has still not appeared. The page "
            "may need longer, or it may not have that on it at all — read it "
            "and say what IS there rather than waiting again. Tell the user "
            "what the page is showing if it is still loading.")
    return ToolResult.failed(
        f"Waited {waited}s and the page did not change. Read it and say what "
        "it shows rather than waiting again.")


def browse_find(what: str) -> ToolResult:
    """Find things on the current page by describing them.

    Returns refs. The model never receives a CSS selector and never gets to emit
    JavaScript, because a model that can do either has full control of whatever
    account the page belongs to.
    """
    refused = _may_look()
    if refused:
        return refused
    try:
        session = get_session()
    except _browser_errors() as exc:
        return _browser_failed(exc)
    if session.snapshot is None:
        return ToolResult.failed("No page is open. Use browse_open first.")
    found = session.find(what)
    if not found:
        # The right failure for a site that has changed: say so, rather than
        # offering something nearby that happened to match.
        return ToolResult.failed(
            f"Nothing on this page matches “{what}”. It may have changed, or it "
            "may be behind something you have not opened yet.")
    lines = [f"[{ref}] {node.role}: {node.name}" for ref, node in found[:40]]
    more = f"\n[{len(found) - 40} more not shown.]" if len(found) > 40 else ""
    return ToolResult("\n".join(lines) + more)


# ── changing a page ──────────────────────────────────────────────────────
#
# **Consent is the site, granted once, not the keystroke.**
#
# These shipped as approval-card actions — one tap per click, per keystroke, per
# submit. That is unusable and, worse, it teaches the tap away: sending one
# WhatsApp message is find → click the chat → type → submit, so a person answering
# "yes" four times in a row for one sentence stops reading the fourth card, and a
# tap nobody reads is not consent. `/CLAUDE.md` already says this about batches:
# *"a tap nobody reads by the fourth time is not consent."*
#
# `docs/BROWSER.md` §5 anticipated the answer: *"The user may promote an origin
# to 'act freely', per site, having seen it work. That is a real choice a person
# can reason about, which 'let the agent use the web' is not."* Allowing changes
# on a site IS that promotion, and it is asked for once, on the Connectors
# screen, in front of a sentence saying what it means.
#
# Three things carry the safety that the per-press card used to:
#
#   * **The origin.** `origins.may_act` is off by default and is a deliberate,
#     revocable, per-site decision — the same unit the whole package uses.
#   * **Unattended never acts.** A routine reads text a stranger wrote, so these
#     check `permissions.unattended()` themselves. That check is the reason the
#     card could be dropped at all, and it must never be dropped with it.
#   * **The page decides nothing.** The origin check runs at the tool, refs come
#     from a snapshot the model was shown, and what crosses into the driver is a
#     role and a name.
#: Said to an agent running with nobody present. Not "you lack a permission":
#: there is no permission that would make this allowed, and an agent told
#: otherwise spends its turn hunting for one.
NOT_WHILE_UNWATCHED = (
    "Nobody is watching this run, so nothing on a website may be clicked or "
    "typed into — that is true however the site is set up, and there is no "
    "permission that changes it. Read and report instead, and leave anything "
    "that changes a page for a moment when the user is here."
)


NO_BROWSING = (
    "This automation is not allowed to look at web pages. Turn on “Let it read "
    "web pages” in the automation if it needs to."
)


#: Said when the control about to be pressed is the one that charges the card.
#:
#: **Not a permission failure, and it must not read as one.** Allowing changes
#: on a site is a real decision the user made and it still holds — this is the
#: one thing inside it that is decided per basket rather than per site, because
#: "yes, act on amazon.in" is not something anybody can mean about a total they
#: have not seen. An agent told "you lack permission" here goes looking for a
#: setting to ask for, and there is none; an agent told to read the basket and
#: propose it does the thing that actually gets the order placed.
#:
#: Written to be true for every agent, including one with no `place_order` of
#: its own: both ways out are named, so the agent that cannot propose says so
#: instead of silently emitting a tag nothing will run.
BUYING_WAITS_FOR_A_TAP = (
    "That control places an order, so it is not something to press directly — "
    "spending the user's money always waits for them, however the site is set "
    "up. Nothing was clicked and the basket is untouched. Read the page, then "
    "propose it as a `place_order` action carrying the items, the exact total "
    "the page shows, and this control's name, so they get one card and one "
    "tap. If you cannot propose that action, say the basket is ready and that "
    "they press the button themselves — do not press it for them."
)

#: Said when the box about to be typed into wants a secret.
#:
#: Absolute, and not tied to acting on a site: the gate above decides whether
#: an agent may type HERE, and this decides whether it may type THIS. We do not
#: hold a card number or a one-time code, we must never ask for one, and an
#: agent that filled a CVV box with a guess would be an agent failing a payment
#: in a way the user finds out about from their bank.
SECRETS_ARE_THE_USERS = (
    "That box wants a card number, a PIN, a password or a one-time code. Those "
    "are the user's to type and never yours — nothing was typed. If the "
    "checkout needs one, say which box is waiting and let them fill it in; a "
    "saved payment method is the only way you can get through one of these. "
    "Never ask the user to tell you the number so you can type it."
)


def _may_look() -> ToolResult | None:
    """The one gate every page tool passes through — reading included.

    Separate from `_may_change`, because the two refuse for different reasons
    and a user reading the message needs the right one. This one is an
    automation's own ceiling; the other is the floor under everything
    unattended, and nothing can lift that.
    """
    from . import permissions

    if permissions.browsing_banned():
        return ToolResult.failed(NO_BROWSING)
    return None


def _may_change() -> ToolResult | None:
    """The one gate every write tool passes through. None means go ahead."""
    from . import permissions

    if permissions.browsing_banned():
        return ToolResult.failed(NO_BROWSING)
    if permissions.unattended():
        return ToolResult.failed(NOT_WHILE_UNWATCHED)
    return None


def browse_click(ref: str = "", label: str = "") -> ToolResult:
    """Click one element of the page that is open."""
    return _change("click", ref=ref, label=label)


def browse_type(text: str, ref: str = "", label: str = "") -> ToolResult:
    """Type into one element of the page that is open."""
    return _change("type", ref=ref, label=label, text=text)


def browse_submit(ref: str = "", label: str = "") -> ToolResult:
    """Press Enter in one element — how most message boxes send."""
    return _change("submit", ref=ref, label=label)


def browse_select(option: str, ref: str = "", label: str = "") -> ToolResult:
    """Choose an option in a dropdown on the page that is open.

    The form control an agent could not touch at all, which made every page
    built around one — a date, a country, a category — a page it could read and
    then stall on.
    """
    if not str(option or "").strip():
        return ToolResult.failed("There is no option named to choose.")
    return _change("select", ref=ref, label=label, text=option)


def browse_press(key: str, ref: str = "", label: str = "") -> ToolResult:
    """Send one named key to an element — Escape, Tab, an arrow.

    Bounded by `driver.NAMED_KEYS` rather than taking whatever string arrives: a
    free-text key is a keyboard-shaped way around everything else here, because
    a modifier chord reaches the browser's own menus.
    """
    return _change("press", ref=ref, label=label, text=key)


def browse_reveal(ref: str = "", label: str = "") -> ToolResult:
    """Scroll one element into view, so a long list draws more of itself.

    **A read, not a change**, and gated as one — see `driver.CHANGING_ACTS`. A
    chat list, a mail list and a feed each render roughly a screenful, so
    without this an agent could see thirty rows and genuinely not reach the
    thirty-first: it would report the thing as absent, which to a user whose
    chat is plainly there reads as the app being broken.
    """
    return _change("reveal", ref=ref, label=label)


def browse_back() -> ToolResult:
    """Go back one page, and read where that landed.

    `Session.back` has existed since the package did and nothing could call it,
    so an agent that followed a link into a dead end had to re-navigate from the
    top — and on a site where the way back is a link rather than an address,
    could not get back at all. The landing is checked exactly as it is for any
    other navigation: going back is still arriving somewhere.
    """
    refused = _may_look()
    if refused:
        return refused
    try:
        _refuse_while_signing_in()
        return _answer(get_session().back())
    except _browser_errors() as exc:
        return _browser_failed(exc)


#: Which gate each page verb passes. Derived from `driver.CHANGING_ACTS` so that
#: adding a verb cannot quietly add an ungated tool — the failure this has to be
#: safe against is a new act arriving with nobody remembering the floor under
#: it, and a frozen list repeated here is exactly how that happens.
def _gate_for(kind: str):
    from ..browser.driver import CHANGING_ACTS

    return _may_change if kind in CHANGING_ACTS else _may_look


def _may_spend(session: Session, kind: str, ref: str,
               label: str) -> ToolResult | None:
    """The gate that is about the BASKET rather than about the site.

    Every other check in `_may_change` answers *"may this agent act here"*, and
    they all still run first. This one answers *"is this particular press the
    one that spends money"*, which a per-site decision cannot contain: the user
    allowed changes on a shop so an agent could fill a basket, and nobody means
    "and buy whatever you decide" by that.

    **The name comes off the snapshot, never off the call.** `session.control_name`
    resolves the same element `act` would and returns the name the page gave it,
    so an agent (or something that wrote the page the agent just read) cannot
    talk its way past this by describing the order button as something else.

    **Every changing verb, derived — never a list of the ones that looked
    dangerous.** This started as `click` and `submit`, which was already wrong
    by the time it landed beside `press`: *Enter* on a focused "Place your
    order" is the same event as clicking it, and a gate naming verbs one at a
    time is a gate with a hole in it the next verb wide. `CHANGING_ACTS` is what
    `_gate_for` already derives from, so a new act is covered here the moment it
    exists rather than when somebody remembers. `reveal` is outside it and stays
    free: scrolling the order button into view is reading.

    Returns None to go ahead, which is the common answer — adding to a basket,
    opening a product, typing in a search box are all untouched.
    """
    from ..browser import page as pagemod
    from ..browser import purchase
    from ..browser.driver import CHANGING_ACTS

    snap = session.snapshot
    if snap is None:
        return None                      # `act` will say there is no page open
    name = session.control_name(ref, label)
    if not name:
        return None                      # not on the page; `act` says so better

    if kind == "type" and purchase.asks_for_a_secret(name):
        return ToolResult.failed(SECRETS_ARE_THE_USERS)
    if kind in CHANGING_ACTS:
        text, _ = pagemod.render(snap)
        if purchase.commits_a_purchase(
                name, at_checkout=purchase.is_payment_page(snap.url, text)):
            log.info("refused a purchase control %r at %s — it needs a card",
                     name[:80], snap.url)
            return ToolResult.failed(BUYING_WAITS_FOR_A_TAP)
    return None


def _change(kind: str, *, ref: str = "", label: str = "",
            text: str = "") -> ToolResult:
    refused = _gate_for(kind)()
    if refused is not None:
        return refused
    if kind == "type" and not str(text or "").strip():
        return ToolResult.failed("There is nothing to type.")
    from ..browser import page as pagemod

    key = f"{kind}:{label or ref}:{text}"
    try:
        session = get_session()
        refused = _may_spend(session, kind, ref, label)
        if refused is not None:
            return refused
        was = pagemod.digest(session.snapshot) if session.snapshot else ""
        reading = session.act(kind, ref, text, label=label)
    except _browser_errors() as exc:
        return _stuck(_browser_failed(exc), key)

    if not reading.ok:
        return _stuck(_answer(reading), key)
    _worked()
    _remember(reading)
    # **Say whether anything happened.** "Done" on a page that had not moved is
    # how an agent came to report that a chat had opened when it had not, and
    # then spent three more turns reasoning from it. The digest is already
    # computed for the cheap-re-read path, so comparing costs nothing.
    moved = bool(was) and bool(reading.digest) and reading.digest != was
    return ToolResult(
        f"Did it — {kind} on “{label or ref}” at {reading.url}.\n" + (
            "The page changed. Read it again to see what it says now."
            if moved else
            "The page looks unchanged. If you expected it to change, what you "
            "acted on may not have been the control you wanted — read the page "
            "and look again rather than repeating this."))


def browse_sites() -> ToolResult:
    """Which sites the user has allowed. Orientation, so an agent can say what it
    is able to do instead of discovering it by being refused."""
    refused = _may_look()
    if refused:
        return refused
    grants = origins.list_grants()
    if not grants:
        return ToolResult(
            "The user has not allowed any websites yet. They can add one under "
            "Settings, and until they do you cannot open any page.")
    return ToolResult("You can read these sites:\n" + "\n".join(
        f"- {g.host}" + (" (and you may change things there)" if g.may_act else "")
        for g in grants))
