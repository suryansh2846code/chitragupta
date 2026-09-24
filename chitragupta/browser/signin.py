"""Signing in to a site once, so an agent can read it as the user afterwards.

The browser already keeps a **persistent** profile and already opens
**visible** — `chromium.open_session()` says why: a user who can watch is a user
who can stop, and MFA needs a window a person can actually reach. So "sign in
once and stay signed in" is not a new mechanism. What was missing is the moment
that starts it and the grant that comes out of it.

Three rules shape this, and each rules out an easier design:

* **We never type the password.** The user types it, into the real site, in a
  real browser. Nothing here fills a form, and there is no code path that could.
  That is also why the window has to be visible rather than driven: two-factor,
  a CAPTCHA and a "check your email" step are all normal parts of signing in and
  all of them need a person.
* **We never read another browser's profile.** `/CLAUDE.md` forbids reading
  another product's app-support directory for credentials, and decrypting
  somebody's cookie jar is the signature behaviour of an infostealer whatever
  the intent. The user signs in here instead. Once.
* **Nothing about the page's text decides anything.** Whether a sign-in worked
  is judged from the address the browser ended up on, never from what the page
  says about itself — the same rule `session._land` already applies.

Which leaves one honest question: how do we know they are done? We ask. A
heuristic that watched for a cookie or a URL pattern would be wrong on some
site, silently, and "connected" is a claim the whole feature rests on. The user
presses Done. We check they are not still sitting on a sign-in page, and say so
if they are rather than recording a connection that does not work.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

from ..log import get_logger, suppressed
from . import origins

log = get_logger(__name__)

#: Path and title fragments that mean "you are being asked to sign in".
#:
#: A heuristic, and treated as one everywhere it is used: it advises the user
#: ("you look like you are still on the sign-in page") and reports an expired
#: session to an agent. It never blocks and never grants — a site we guessed
#: wrong about still connects if the person says it did.
SIGN_IN_MARKERS = (
    "/login", "/log-in", "/signin", "/sign-in", "/sign_in", "/auth/",
    "/authenticate", "/session/new", "/accounts/login", "/u/login",
    "/checkpoint", "/challenge",
)
SIGN_IN_TITLES = ("sign in", "log in", "login", "sign up", "verify",
                  "two-factor", "authenticate")

#: Where an identity provider lands you when it refuses to sign you in *because
#: of the browser*, rather than because of anything you typed.
#:
#: Google is the one that matters — "Continue with Google" is the first button on
#: a great many sign-in pages — and its refusal reads *"This browser or app may
#: not be secure"*, which sounds like our fault and is not. Any automated browser
#: is refused: ours reports `navigator.webdriver = true`, ships a Chrome for
#: Testing user agent, and has no `userAgentData.brands`, all of which are
#: checked. That is Google's anti-abuse policy working as intended.
#:
#: **We do not try to look like something else.** Defeating it means forging the
#: fingerprint of a browser we are not, in an arms race whose schedule Google
#: sets — and the cost of losing lands on the user's real Google account, which
#: is worth far more to them than this feature. So the honest move is to
#: recognise the wall and say what gets past it: sign in to the site directly.
SSO_REFUSED = (
    "accounts.google.com/v3/signin/rejected",
    "accounts.google.com/signin/rejected",
    "accounts.google.com/deniedsigninrejected",
)

#: What to tell someone staring at that page. Names the provider, says whose
#: decision it was, and gives the way through — a refusal with no route out is
#: the "it just doesn't work" this app is built to avoid.
SSO_REFUSED_ADVICE = (
    "Google would not sign you in through this browser — it only allows its "
    "own sign-in from an ordinary browser window. This is Google's rule, not "
    "something wrong with your setup. Go back and sign in to the site "
    "directly with your email and password instead of “Continue with "
    "Google”; that works normally here."
)


def sso_was_refused(url: str) -> bool:
    """Did an identity provider refuse us for being an automated browser?

    Matched on the address, never on the page text — the same rule the rest of
    this module follows, and the reason a translated version of that page is
    still recognised.
    """
    lowered = (url or "").lower()
    return any(marker in lowered for marker in SSO_REFUSED)


def is_sign_in_url(url: str) -> bool:
    """Is this address a sign-in page? Judged on the path alone.

    The strong signal, and the only one allowed to *stop* a read. A title is
    not: "Sign up for our newsletter | BBC News" is an article, and blocking it
    would make a legitimate page unreadable with no way for the user to tell
    why. Paths like `/login` are not ambiguous in the same way.
    """
    address = str(url or "").lower()
    # The query string is excluded: a `?redirect=/login` on a page you are
    # already signed into would otherwise read as a sign-in page forever.
    address = address.split("?", 1)[0].split("#", 1)[0]
    return any(marker in address for marker in SIGN_IN_MARKERS)


def looks_like_sign_in(url: str, title: str = "") -> bool:
    """The same question, allowed to be wrong, for advising the user.

    Used only where being wrong costs a sentence — "that still looks like a
    sign-in page" — and where the person can overrule it. The title is included
    here because a false positive is cheap and a missed one is not: recording a
    connection that does not work is the failure this whole check exists for.
    """
    if is_sign_in_url(url):
        return True
    heading = str(title or "").lower()
    return any(phrase in heading for phrase in SIGN_IN_TITLES)


@dataclass
class Connecting:
    """A sign-in the user is in the middle of."""

    host: str = ""
    url: str = ""
    #: Set once `finish` has been tried and the browser was still on a sign-in
    #: page, so the card can say so without pretending it failed outright.
    still_signing_in: bool = False
    #: …and set when the reason is an identity provider refusing us, which is
    #: the one waiting state a second press cannot overrule. The card reads it
    #: to go on offering "Done" rather than "Done anyway": a button promising an
    #: override that cannot happen is a button that fails twice and explains
    #: itself neither time.
    sso_refused: bool = False
    note: str = ""


@dataclass
class _State:
    current: Connecting | None = None
    session: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _State()


def status() -> dict[str, Any]:
    """What the Connectors screen needs to draw this."""
    with _state.lock:
        live = _state.current
        return {
            "connecting": live is not None,
            "host": live.host if live else "",
            "url": live.url if live else "",
            "still_signing_in": bool(live and live.still_signing_in),
            "sso_refused": bool(live and live.sso_refused),
            "note": live.note if live else "",
        }


def begin(url: str) -> dict[str, Any]:
    """Open the browser at a site so the user can sign in.

    Deliberately does **not** grant anything. A window being open is not
    consent, and a user who closes it having thought better of it should leave
    no trace behind.
    """
    from . import chromium

    try:
        origin = origins.normalise(url)
        host = origins.host_of(origin)
    except origins.BadOriginError as exc:
        return {"ok": False, "error": str(exc)}

    with _state.lock:
        if _state.current is not None:
            return {"ok": False,
                    "error": f"Already signing in to {_state.current.host}. "
                             "Finish or cancel that first."}
        try:
            # Borrowed, not started. One browser holds the profile, and a
            # second one is refused by Chromium — see `chromium.shared_driver`.
            driver = chromium.shared_driver()
        except chromium.BrowserNotReadyError as exc:
            return {"ok": False, "error": str(exc)}

        _state.session = driver
        _state.current = Connecting(host=host, url=origin)

    # Outside the lock: navigating can take a while, and holding the lock
    # across it would make `status()` hang for whoever is polling it.
    with suppressed("opening a site for the user to sign in to"):
        driver.goto(origin)

    log.info("browser sign-in started for %s", host)
    return {"ok": True, "host": host, "url": origin,
            "detail": f"A browser window is open at {host}. Sign in there, "
                      "then come back and press Done."}


def finish(force: bool = False) -> dict[str, Any]:
    """The user says they are signed in. Check, then record the connection.

    `force` is how "I really am signed in" wins over the heuristic. It exists
    because the heuristic will be wrong somewhere, and a user who cannot
    override it is a user we have locked out of their own account.
    """
    with _state.lock:
        live = _state.current
        session = _state.session
    if live is None:
        return {"ok": False, "error": "Nothing is being connected."}

    where, title, nodes = _where_is_the_browser(session)
    stuck = bool(where and looks_like_sign_in(where, title))

    # Checked before the sign-in heuristic, and separately from it. Google's
    # refusal page *is* a sign-in page, so the generic branch would catch it and
    # answer "finish signing in, then press Done" — advice that cannot be
    # followed, about a page that will never let them finish. Naming the real
    # cause is the difference between a user retrying forever and a user signing
    # in the way that works.
    #
    # **And the refusal usually is not on the page we are driving.** "Continue
    # with Google" opens a popup, Google refuses it *there*, and the window we
    # navigated is still sitting on the site's own login page — so a check that
    # reads one page finds nothing and falls through to exactly the advice this
    # branch exists to replace. Every open window is asked.
    #
    # A popup counts only while the driven window is still on a sign-in page.
    # Somebody who gave up on the button, typed their password and got in leaves
    # that refused popup open behind them, and treating it as current would
    # refuse a connection that demonstrably works — with no `force` to escape,
    # because a refusal is not a heuristic somebody may overrule.
    refused_here = bool(where and sso_was_refused(where))
    refused_beside = stuck and any(
        sso_was_refused(url) for url, _ in _open_windows(session))
    if refused_here or refused_beside:
        with _state.lock:
            if _state.current is not None:
                _state.current.still_signing_in = True
                _state.current.sso_refused = True
                _state.current.note = SSO_REFUSED_ADVICE
        return {"ok": False, "still_signing_in": True, "sso_refused": True,
                "host": live.host, "error": SSO_REFUSED_ADVICE}

    if not force and stuck:
        with _state.lock:
            if _state.current is not None:
                _state.current.still_signing_in = True
                # Cleared, not left standing. Somebody who walked away from the
                # refusal to the site's own form is back to a guess they may
                # overrule, and a stale flag would go on withholding exactly the
                # override they now need.
                _state.current.sso_refused = False
                _state.current.note = (
                    "The browser still looks like it is on a sign-in page. "
                    "Finish signing in, then press Done again — or press Done "
                    "again anyway if you know you are in.")
        return {"ok": False, "still_signing_in": True, "host": live.host,
                "error": "That still looks like a sign-in page. Finish signing "
                         "in, then press Done — or press Done again to record "
                         "it anyway."}

    account = account_in(nodes)
    grant = origins.grant(
        live.url, may_read=True,
        note=f"signed in as {account}" if account else "signed in from Connectors")
    # **Closed, now the sign-in is over.** Leaving it open used to look harmless
    # — the user can see they are in — but a browser holds an *exclusive* lock on
    # the profile directory, and the profile is the whole point of this flow. So
    # the window that proves the sign-in worked is also the window that stops
    # every agent from using it: the next `browse_open` cannot start a browser at
    # all, and no amount of retrying clears it because nothing is going to close
    # that window but the person who no longer has any reason to look at it.
    #
    # Closing is also what flushes the session to disk, so "it stays signed in"
    # is true on disk rather than only in the memory of a process we abandoned.
    _clear(close_browser=True)
    log.info("browser sign-in finished for %s", live.host)
    return {"ok": True, "host": live.host,
            "grant": grant.as_dict() if hasattr(grant, "as_dict") else None,
            "detail": f"{live.host} is connected. Agents you allow can read it "
                      "as you, and it stays signed in."}


def cancel() -> dict[str, Any]:
    """Give up on a sign-in. Nothing is granted and nothing is remembered."""
    with _state.lock:
        live = _state.current
    if live is None:
        return {"ok": True, "detail": "Nothing was being connected."}
    _clear(close_browser=True)
    log.info("browser sign-in cancelled for %s", live.host)
    return {"ok": True, "detail": f"Stopped connecting {live.host}."}


#: An identifier a page cannot accidentally look like. A heading can read as a
#: person's name; nothing reads as an email address or an @handle by accident.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HANDLE = re.compile(r"@[A-Za-z0-9_]{2,30}\b")

#: Long enough for an email, short enough that a page cannot use the note as a
#: billboard on the user's Connectors screen.
MAX_ACCOUNT_CHARS = 60


def account_in(nodes: Any) -> str:
    """The account this page is signed in as, if it says so unmistakably.

    Reads the accessibility tree, which is written by the site — so this looks
    only for shapes that cannot be anything else. "Suryansh Singh" next to an
    avatar is a guess; `suryansh@example.com` is not.

    Returns "" when nothing qualifies, and that is the common case. A generic
    note is right far more often than a confident wrong one.
    """
    for node in list(nodes or [])[:400]:
        text = f"{getattr(node, 'name', '')} {getattr(node, 'value', '')}"
        found = _EMAIL.search(text) or _HANDLE.search(text)
        if found:
            return found.group(0)[:MAX_ACCOUNT_CHARS]
    return ""


def _where_is_the_browser(driver: Any) -> tuple[str, str, list]:
    """Where the browser actually is, what it is called, and what is on it.

    Never the address we asked for — the whole question is where signing in
    took them, and the two differ on every site that redirects after login.
    """
    if driver is None:
        return "", "", []
    with suppressed("asking the browser where it ended up"):
        url, title, nodes = driver.current()
        return str(url), str(title), list(nodes or [])
    return "", "", []


def _open_windows(driver: Any) -> list[tuple[str, str]]:
    """Every window the browser has open, as `(url, title)`.

    An extra a real browser can answer, asked for with `getattr` rather than
    added to the `Driver` protocol in `session.py`: that seam is deliberately
    four methods, and an agent's `Session` reading exactly the page it navigated
    is a property worth keeping. Signing in already drives the driver directly,
    which is the whole reason `chromium.open_driver()` exists.

    Empty when the browser cannot say — a driver that does not know about
    windows behaves the way it did before this existed, and the generic sign-in
    heuristic still has the case.
    """
    if driver is None:
        return []
    listing = getattr(driver, "windows", None)
    if not callable(listing):
        return []
    with suppressed("asking the browser what windows are open"):
        return [(str(url), str(title)) for url, title in listing() or []]
    return []


#: Where the browser is left once a sign-in ends. Not closed — the browser is
#: shared, and closing it is how a finished sign-in used to kill the page an
#: agent had open. Navigated away instead, so it is not sitting on somebody's
#: feed, and so the next thing to borrow it starts from nowhere in particular.
PARKED = "about:blank"


def _clear(*, close_browser: bool) -> None:
    """End the sign-in. `close_browser` now means *park* it, not close it.

    The name is kept because it is what the two callers mean — "I am done with
    the browser" — and the answer to that changed, not the question. There is
    one browser for the whole app now (`chromium.shared_driver`), so closing it
    on behalf of one caller ends everyone else's page too: that is exactly how
    finishing a sign-in came to break an agent mid-read.
    """
    with _state.lock:
        session = _state.session
        _state.current = None
        _state.session = None
    if close_browser and session is not None:
        with suppressed("parking the browser after a sign-in"):
            session.goto(PARKED)
