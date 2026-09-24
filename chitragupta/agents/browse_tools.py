"""Looking at websites the user has allowed. Reading only.

Three tools, and the shape of the set is the design: **every one of them looks,
and none of them acts.** `docs/BROWSER.md` splits the capability that way because
reading and downloading covers a large part of the value with none of the
transactional risk — the most useful browser agent needs the least dangerous half
— and because the approval card that acting requires deserves to be designed
against real approval cards rather than guessed at.

So `browse_click`, `browse_type` and `browse_submit` do not exist here yet. When
they land they belong in `permissions.NEVER_UNATTENDED` **in the same commit**,
because a routine reading a stranger's email is the one caller that must never
reach them.

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

from ..browser import origins
from ..browser.session import Reading, Session
from ..log import get_logger
from .results import ToolResult

log = get_logger(__name__)

#: The live session, built on first use. One browser per app, not per agent: two
#: agents driving two Chromiums against the same profile would fight over the
#: cookie jar, and the profile is the thing that makes a site "signed in".
_session: Session | None = None


def get_session() -> Session:
    """The shared browser session, started if it is not already running."""
    global _session
    if _session is None:
        from ..browser.chromium import open_session

        _session = open_session()
    return _session


def set_session(session: Session | None) -> None:
    """Replace the shared session. For tests, and for a reset after a crash."""
    global _session
    _session = session


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
    """
    if reading.ok:
        _remember(reading)
        return ToolResult(reading.text, truncated=reading.truncated)

    if reading.grantable:
        # Named so the agent can report it and the user can act on it. The agent
        # cannot grant it — that is a decision only a person makes, and a tool
        # that could widen its own access would make the allow-list decorative.
        return ToolResult.failed(
            f"{reading.reason} Tell the user they can allow "
            f"{reading.grantable} in Settings if they want you to read it. Do "
            "not try other addresses for the same thing.")
    return ToolResult.failed(reading.reason)


#: What an agent is told when the browser itself will not start.
#:
#: The one failure this module had no words for, and the module docstring says
#: wording is the thing it owns. A `BrowserError` used to travel all the way to
#: the model as whatever Playwright wrote — *"Failed to create a ProcessSingleton
#: for your profile directory"* — which is an internal, so the model paraphrased
#: it into "the browser session closed unexpectedly" and offered to try again.
#: It then failed identically, because nothing about retrying closes the other
#: window that is holding the profile.
#:
#: So: say which of the two it is, and say the thing that actually clears it.
#: Every browser we open takes an *exclusive* lock on the profile, so a second
#: one cannot start while the first is alive.
BROWSER_BUSY = (
    "Another browser window opened by this app is still using the browser "
    "profile, so a new page cannot be opened. Tell the user to close the extra "
    "browser window — the one left over from connecting a site — and that "
    "reading will work again straight away. Do not retry this call: retrying "
    "cannot close that window, and it will fail the same way."
)
BROWSER_UNAVAILABLE = (
    "The browser could not be started, so no page can be read right now. Tell "
    "the user, and say it is the browser rather than their sign-in or their "
    "permission. Do not retry this call more than once."
)

#: The **recoverable** one, and the reason it needs its own words.
#:
#: "Target page, context or browser has been closed" is not a browser that will
#: not start. It is a browser that was alive and is not any more — and the usual
#: cause is us: connecting a site opens a browser and closes it again when the
#: user presses Done, and while the profile is shared that ends whatever page an
#: agent had open. Chromium hands a second launch on the same profile to the
#: first process ("Opening in existing browser session"), so the two are one
#: browser and closing either closes both.
#:
#: Reported as "the browser could not be started, do not retry", it was the
#: exact inversion of the truth: this is the one failure retrying *does* fix,
#: and the agent was told the one thing that stopped it fixing itself.
BROWSER_CLOSED = (
    "The browser window that had this page open was closed — connecting a site "
    "opens and closes one, which ends any page already open. Nothing is wrong "
    "with the sign-in, the site or the permission. Call this again once and a "
    "fresh browser will start; only tell the user if the second attempt fails "
    "too."
)

#: Playwright's name for "another Chromium already holds this profile". Matched
#: as a fragment because the rest of that message is a path and a paragraph of
#: advice aimed at whoever wrote the code, not at this user.
_PROFILE_LOCKED = "processsingleton"

#: How Playwright says "the thing you were driving is gone". Matched on the
#: phrase rather than an exception type because it arrives as a plain message
#: from the browser thread, already turned into a `BrowserError` by `_call`.
_CLOSED = ("has been closed", "target closed", "target page, context or browser")


def _browser_failed(exc: Exception) -> ToolResult:
    """Turn a browser that will not start into something an agent can act on.

    Never the exception's own text. `/CLAUDE.md`: no stack traces and no
    internals in anything user-facing, and a tool result is user-facing by the
    time a model has repeated it back to somebody.

    `BrowserNotReadyError` is the exception, and it is one on purpose: its
    message is already written for a person — "choose Set up browsing" — so it
    is passed through rather than replaced by something vaguer.
    """
    from ..browser.chromium import BrowserNotReadyError

    if isinstance(exc, _SigningInError):
        # Not logged as a browser problem, because it is not one — somebody is
        # using the window for exactly what it is for.
        return ToolResult.failed(SIGN_IN_IN_PROGRESS)
    log.warning("browser unavailable for an agent: %s", str(exc)[:200])
    detail = str(exc).lower()
    if isinstance(exc, BrowserNotReadyError):
        return ToolResult.failed(f"{exc} Tell the user that, and do not retry.")
    if any(phrase in detail for phrase in _CLOSED):
        # **Dropped, not kept — and dropped in both places.** A driver's thread
        # outlives its browser, so `_ensure_started` sees it alive and returns
        # without relaunching: a stale handle is one that looks healthy and
        # answers nothing. Clearing the session alone is not enough, because the
        # next one would be handed the same dead driver from `shared_driver()`.
        # Clearing both is what makes "call this again once" true.
        from ..browser import chromium

        set_session(None)
        chromium.reset_shared()
        return ToolResult.failed(BROWSER_CLOSED)
    if _PROFILE_LOCKED in detail or "already in use" in detail:
        return ToolResult.failed(BROWSER_BUSY)
    return ToolResult.failed(BROWSER_UNAVAILABLE)


#: Said when the user is part-way through connecting a site.
#:
#: One browser for the whole app means one window, and while a sign-in is live
#: that window has a person typing a password into it. Navigating it out from
#: under them would lose the sign-in and look like the app fighting them. This
#: is a wait, not a refusal, and it ends when they press Done or Cancel.
SIGN_IN_IN_PROGRESS = (
    "The user is signing in to a site in the browser window right now, so it "
    "cannot be used for anything else until they finish. Do not retry in this "
    "turn — tell them you will read it once they are done."
)


class _SigningInError(RuntimeError):
    """Not a browser failure: the browser is busy being used by a person."""


def _refuse_while_signing_in() -> None:
    """Stop an agent taking the window out from under somebody's password."""
    from ..browser import signin

    if signin.status().get("connecting"):
        raise _SigningInError(SIGN_IN_IN_PROGRESS)


def browser_trouble(exc: Exception) -> str:
    """The same wording, for a caller that needs the sentence not a `ToolResult`.

    `actions._browse_act` runs behind an approval card rather than as a tool, so
    it has nowhere to put a `ToolResult` — but the rule does not change with the
    caller: Playwright's text never reaches a person, and this module is where
    that text is decided.
    """
    return str(_browser_failed(exc))


def _browser_errors() -> tuple:
    """The two ways asking for a browser can raise, as one `except` clause."""
    from ..browser.chromium import BrowserNotReadyError
    from ..browser.driver import BrowserError

    return (BrowserError, BrowserNotReadyError, _SigningInError)


def browse_open(url: str) -> ToolResult:
    """Open a page on an allowed site and read it."""
    try:
        _refuse_while_signing_in()
        return _answer(get_session().open(url))
    except _browser_errors() as exc:
        # Whether the session survives is decided in `_browser_failed`, by which
        # failure it was: a browser that would not *start* leaves the page an
        # agent holds refs into alone, a browser that has been *closed* has no
        # page left to protect and has to be replaced.
        return _browser_failed(exc)


def browse_read(since: str | None = None) -> ToolResult:
    """Re-read the current page, cheaply if it has not changed.

    `since` is the fingerprint from a previous read. A page is the most expensive
    thing an agent can ask for and it re-reads after every step, so answering
    "nothing has changed" for a few tokens instead of a few thousand is the
    difference between this capability feeling cheap and feeling reckless on
    somebody's own model plan.
    """
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
        return ToolResult(f"{reading.text}\n[page-version: {reading.digest}]",
                          truncated=reading.truncated)
    return _answer(reading)


def browse_find(what: str) -> ToolResult:
    """Find things on the current page by describing them.

    Returns refs. The model never receives a CSS selector and never gets to emit
    JavaScript, because a model that can do either has full control of whatever
    account the page belongs to.
    """
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


def browse_sites() -> ToolResult:
    """Which sites the user has allowed. Orientation, so an agent can say what it
    is able to do instead of discovering it by being refused."""
    grants = origins.list_grants()
    if not grants:
        return ToolResult(
            "The user has not allowed any websites yet. They can add one under "
            "Settings, and until they do you cannot open any page.")
    return ToolResult("You can read these sites:\n" + "\n".join(
        f"- {g.host}" + (" (and you may change things there)" if g.may_act else "")
        for g in grants))
