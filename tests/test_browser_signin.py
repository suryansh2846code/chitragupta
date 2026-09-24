"""Signing in to a site once, so an agent can read it as the user afterwards.

The alternatives were both worse and both tempting. Handing the credentials to a
broker means the password is typed into somebody else's page and the session
lives on their servers. Reading the user's own Chrome profile means decrypting a
cookie jar, which `/CLAUDE.md` forbids and which is what an infostealer does.

So the user signs in here, once, in a visible window, and the profile keeps it.
What is pinned below is mostly the things that would quietly make that untrue:
a grant recorded for a sign-in that never happened, a sign-out that leaves the
cookies behind, and an expired session that reads as the feature being broken.

Contract: docs/development/connected-sites.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chitragupta.browser import origins, signin


@pytest.fixture(autouse=True)
def clean():
    signin._clear(close_browser=False)
    yield
    signin._clear(close_browser=False)
    for grant in origins.list_grants():
        origins.revoke(grant.host)


class FakeDriver:
    """A browser that is wherever the test says it is."""

    def __init__(self, url="https://example.com/feed", title="Feed"):
        self.url, self.title = url, title
        self.went_to = []
        self.closed = False
        self.cleared = []
        #: Windows this browser has open besides the one it is driving —
        #: `[(url, title)]`. A real "Continue with Google" opens one.
        self.popups = []

    def windows(self):
        return [(self.url, self.title), *self.popups]

    def goto(self, url):
        self.went_to.append(url)
        return self.url, self.title, []

    def current(self):
        return self.url, self.title, getattr(self, "nodes", [])

    def clear_cookies(self, domain):
        self.cleared.append(domain)

    def close(self):
        self.closed = True


@pytest.fixture
def driver(monkeypatch):
    """The one shared browser, faked.

    `open_driver` is the factory and `shared_driver` caches what it returns, so
    the cache has to be cleared around every test — otherwise the first fake
    outlives its test and the rest assert against somebody else's browser. That
    caching is the point of the design, not an inconvenience: one profile
    permits one Chromium.
    """
    fake = FakeDriver()
    from chitragupta.browser import chromium

    monkeypatch.setattr(chromium, "open_driver", lambda: fake)
    chromium.reset_shared()
    yield fake
    chromium.reset_shared()


# ── the heuristic, and that it stays one ─────────────────────────────────
@pytest.mark.parametrize("url, title, expected", [
    ("https://www.linkedin.com/login", "Sign in", True),
    ("https://x.com/i/flow/login", "", True),
    ("https://accounts.google.com/v3/signin/challenge", "", True),
    ("https://www.linkedin.com/feed/", "Feed | LinkedIn", False),
    ("https://discord.com/channels/@me", "Discord", False),
    # A signed-in page carrying a redirect parameter is not a sign-in page —
    # reading the query string would make it one forever.
    ("https://x.com/home?redirect_after=/login", "Home", False),
])
def test_it_recognises_a_sign_in_page(url, title, expected):
    assert signin.looks_like_sign_in(url, title) is expected


# ── beginning ────────────────────────────────────────────────────────────
def test_opening_a_site_grants_nothing(driver):
    """A window being open is not consent."""
    out = signin.begin("https://www.linkedin.com")
    assert out["ok"]
    assert driver.went_to == ["https://www.linkedin.com"]
    assert origins.list_grants() == [], "it granted the site just for opening it"


def test_it_says_what_to_do_next(driver):
    out = signin.begin("https://www.linkedin.com")
    assert "Sign in there" in out["detail"]
    assert signin.status()["connecting"] is True
    assert signin.status()["host"] == "www.linkedin.com"


def test_two_sign_ins_at_once_are_refused(driver):
    signin.begin("https://www.linkedin.com")
    out = signin.begin("https://discord.com")
    assert not out["ok"]
    assert "Finish or cancel" in out["error"]


def test_a_nonsense_address_is_refused_before_a_browser_opens(driver):
    out = signin.begin("not a url")
    assert not out["ok"]
    assert driver.went_to == []


# ── finishing ────────────────────────────────────────────────────────────
def test_finishing_records_the_connection(driver):
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/feed/", "Feed"

    out = signin.finish()

    assert out["ok"]
    assert [g.host for g in origins.list_grants()] == ["www.linkedin.com"]
    assert origins.list_grants()[0].may_read is True
    assert signin.status()["connecting"] is False


def test_acting_is_not_granted_by_signing_in(driver):
    """Reading a site as the user is not permission to act as them."""
    signin.begin("https://www.linkedin.com")
    driver.url = "https://www.linkedin.com/feed/"
    signin.finish()

    assert origins.list_grants()[0].may_act is False


def test_still_on_the_sign_in_page_is_not_a_connection(driver):
    """Recording it would claim a connection that does not work."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/login", "Sign in"

    out = signin.finish()

    assert not out["ok"]
    assert out["still_signing_in"] is True
    assert origins.list_grants() == []
    assert signin.status()["connecting"] is True, "it gave up on the sign-in"


def test_the_user_can_overrule_the_guess(driver):
    """The heuristic will be wrong somewhere, and being locked out of your own
    account by our guess is worse than a wrong guess."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/login", "Sign in"
    assert not signin.finish()["ok"]

    out = signin.finish(force=True)

    assert out["ok"]
    assert [g.host for g in origins.list_grants()] == ["www.linkedin.com"]


def test_finishing_nothing_says_so(driver):
    assert not signin.finish()["ok"]


# ── giving up ────────────────────────────────────────────────────────────
def test_cancelling_leaves_no_trace(driver):
    """Nothing granted, nothing remembered — and the browser is not left sitting
    on the half-filled login page they walked away from.

    It is *parked*, not closed. There is one browser for the whole app now, so
    closing it on a cancelled sign-in would end whatever page an agent had open
    — which is the failure this whole area kept producing.
    """
    signin.begin("https://www.linkedin.com")

    out = signin.cancel()

    assert out["ok"]
    assert origins.list_grants() == []
    assert driver.went_to[-1] == signin.PARKED, "left on the sign-in page"
    assert driver.closed is False, "cancelling took the shared browser down"
    assert signin.status()["connecting"] is False


# ── the browser is never asked to be the user ────────────────────────────
def test_nothing_here_can_type_a_password():
    """The user types it, into the real site. There is no code path that fills
    a form, and MFA is why the window has to be one a person can reach."""
    text = Path(signin.__file__).read_text()
    for forbidden in ("fill(", "type(", "password", "keyboard"):
        if forbidden == "password":
            # The word appears in prose; what must not appear is a value being
            # put into a field.
            continue
        assert f".{forbidden}" not in text, f"signin.py can {forbidden}"


# ── the session that quietly lapsed ──────────────────────────────────────
class _SignedOut:
    """A granted site that redirects wherever the test says."""

    def __init__(self, landing, title="Sign in"):
        self.landing, self.title = landing, title

    def goto(self, url):
        return self.landing, self.title, []

    def current(self):
        return self.landing, self.title, []

    def close(self):
        pass


def test_an_expired_session_says_so_instead_of_reading_the_login_form():
    """Handing an agent a login page means it reads one and reports on it.

    "Sign in to LinkedIn" is a perfectly coherent summary of a page nobody
    wanted summarised, and it reads to the user as the feature being broken
    rather than the login having ended.
    """
    from chitragupta.browser.session import Session

    origins.grant("https://www.linkedin.com", may_read=True)
    reading = Session(_SignedOut("https://www.linkedin.com/login")).open(
        "https://www.linkedin.com/feed/")

    assert reading.ok is False
    assert reading.needs_signin is True
    assert "signed out of www.linkedin.com" in reading.reason
    assert "Connectors" in reading.reason
    assert reading.text == "", "the login form reached the agent anyway"


def test_the_grant_survives_an_expired_session():
    """The permission is fine. Only the login ended."""
    from chitragupta.browser.session import Session

    origins.grant("https://www.linkedin.com", may_read=True)
    Session(_SignedOut("https://www.linkedin.com/login")).open(
        "https://www.linkedin.com/feed/")

    assert [g.host for g in origins.list_grants()] == ["www.linkedin.com"]


def test_a_working_page_is_not_mistaken_for_a_lapsed_session():
    from chitragupta.browser.session import Session

    origins.grant("https://www.linkedin.com", may_read=True)
    reading = Session(_SignedOut("https://www.linkedin.com/feed/", "Feed")).open(
        "https://www.linkedin.com/feed/")

    assert reading.needs_signin is False
    assert reading.ok is True


def test_a_page_whose_title_mentions_signing_up_is_still_readable():
    """"Sign up for our newsletter | BBC News" is an article.

    A title is allowed to *advise* the user during a sign-in and never to stop
    a read: blocking it would make a legitimate page unreadable, with nothing
    on screen to say why.
    """
    from chitragupta.browser.session import Session

    origins.grant("https://www.bbc.com", may_read=True)
    reading = Session(
        _SignedOut("https://www.bbc.com/news/article-1",
                   "Sign up for our newsletter | BBC News")).open(
        "https://www.bbc.com/news/article-1")

    assert reading.ok is True, "an article was refused for its title"
    assert reading.needs_signin is False


# ── disconnect means signed out, not just unlisted ───────────────────────
def test_disconnecting_clears_the_sign_in_too(monkeypatch, driver):
    """A grant dropped while the cookies stay is a lie about what it did."""
    from chitragupta.browser import chromium

    monkeypatch.setattr(chromium, "is_installed", lambda: True)
    assert chromium.forget_site("linkedin.com") is True
    assert driver.cleared == [".linkedin.com"], (
        "the subdomain form matters — a token left on www. is a live session")


def test_disconnecting_borrows_the_browser_instead_of_starting_one(monkeypatch, driver):
    """Disconnect used to start its own Chromium, which the profile refuses
    while another is running, and then never close it.

    It now uses the one shared browser — and must not close it either. This is a
    button somebody presses in passing; taking browsing down until they quit the
    app, or ending an agent's open page, are both the same old failure.
    """
    from chitragupta.browser import chromium

    monkeypatch.setattr(chromium, "is_installed", lambda: True)
    started = []
    monkeypatch.setattr(chromium, "open_driver",
                        lambda: started.append(1) or driver)
    chromium.reset_shared()
    chromium.shared_driver()                  # the app's browser, already up
    started.clear()

    chromium.forget_site("linkedin.com")

    assert started == [], "Disconnect started a second browser"
    assert driver.closed is False, "Disconnect closed the shared browser"


def test_a_failed_sign_out_leaves_the_browser_alone_too(monkeypatch, driver):
    """The path that matters more: something has already gone wrong, and that is
    the worst moment to also take the browser down."""
    from chitragupta.browser import chromium

    monkeypatch.setattr(chromium, "is_installed", lambda: True)

    def boom(_domain):
        raise RuntimeError("the cookie jar is locked")

    driver.clear_cookies = boom

    assert chromium.forget_site("linkedin.com") is False
    assert driver.closed is False


# ── which account, when the page says so unmistakably ────────────────────
def test_it_records_the_account_when_the_page_says_one():
    """The browser is on the signed-in page, so the answer is already there."""
    from chitragupta.browser.page import Node

    assert signin.account_in([
        Node("img", "Suryansh Singh"),
        Node("button", "suryansh2846@gmail.com"),
    ]) == "suryansh2846@gmail.com"


def test_a_handle_counts_too():
    from chitragupta.browser.page import Node

    assert signin.account_in([Node("link", "@suryansh_s")]) == "@suryansh_s"


def test_it_refuses_to_guess_a_name():
    """A heading can read as a person. Showing the wrong account is worse than
    showing none — it reads as confirmation."""
    from chitragupta.browser.page import Node

    assert signin.account_in([
        Node("heading", "Suryansh Singh"),
        Node("link", "Feed"),
        Node("heading", "Sign up for our newsletter"),
    ]) == ""


def test_the_site_cannot_use_the_note_as_a_billboard():
    """The snapshot is text the website wrote, and it lands on the user's
    Connectors screen."""
    from chitragupta.browser.page import Node

    shouty = "x" * 400 + "@example.com"
    assert len(signin.account_in([Node("link", shouty)])) <= signin.MAX_ACCOUNT_CHARS


def test_the_account_reaches_the_grant(driver):
    from chitragupta.browser.page import Node

    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/feed/", "Feed"
    driver.nodes = [Node("button", "suryansh2846@gmail.com")]

    signin.finish()

    assert origins.list_grants()[0].note == "signed in as suryansh2846@gmail.com"


def test_a_site_that_names_nobody_still_connects(driver):
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/feed/", "Feed"

    assert signin.finish()["ok"]
    assert origins.list_grants()[0].note == "signed in from Connectors"


# ── when the identity provider is the thing refusing ─────────────────────
#
# Reported from a real machine: "Continue with Google" on LinkedIn lands on
# Google's *"Couldn't sign you in — This browser or app may not be secure"*.
# Google refuses OAuth from any automated browser, and ours is one: it reports
# `navigator.webdriver = true`, carries a Chrome for Testing user agent, and has
# no `userAgentData.brands`. All three are checked, all three are true, and the
# refusal is Google's anti-abuse policy working exactly as designed.
#
# Nothing here tries to look like a different browser. The point of these tests
# is the opposite: that the app *recognises the wall* and says what gets past it,
# instead of leaving somebody pressing Done at a page that will never let them
# finish.
@pytest.mark.parametrize("url", [
    "https://accounts.google.com/v3/signin/rejected?continue=https%3A%2F%2Fwww.linkedin.com",
    "https://accounts.google.com/signin/rejected",
    "https://accounts.google.com/deniedsigninrejected",
    "https://ACCOUNTS.GOOGLE.COM/v3/signin/REJECTED?x=1",
])
def test_a_refused_sso_page_is_recognised(url):
    assert signin.sso_was_refused(url) is True


@pytest.mark.parametrize("url", [
    "https://accounts.google.com/v3/signin/identifier",
    "https://accounts.google.com/v3/signin/challenge",
    "https://www.linkedin.com/feed/",
    "https://example.com/signin/rejected-applications",
    "",
])
def test_an_ordinary_sign_in_page_is_not_mistaken_for_a_refusal(url):
    """The generic sign-in heuristic still owns those. Claiming Google refused
    us on a page where the user simply has not finished typing would send them
    away from a sign-in that was about to work."""
    assert signin.sso_was_refused(url) is False


def test_the_refusal_explains_itself_instead_of_saying_try_again(driver):
    """The bug as reported. Google's refusal page *is* a sign-in page, so the
    generic branch caught it and answered "finish signing in, then press Done" —
    advice that cannot be followed, about a page that will never let them
    finish."""
    signin.begin("https://www.linkedin.com")
    driver.url = ("https://accounts.google.com/v3/signin/rejected"
                  "?continue=https%3A%2F%2Fwww.linkedin.com")
    driver.title = "Couldn't sign you in"

    out = signin.finish()

    assert out["ok"] is False
    assert out["sso_refused"] is True
    assert "Google's rule" in out["error"]
    assert "not something wrong with your setup" in out["error"].lower()


def test_the_refusal_says_what_actually_works(driver):
    """A wall with no way round it is the "it just doesn't work" this app exists
    to avoid. Signing in to the site directly is unaffected — it is only the
    identity provider that refuses."""
    signin.begin("https://www.linkedin.com")
    driver.url = "https://accounts.google.com/v3/signin/rejected"

    error = signin.finish()["error"]

    assert "email and password" in error
    assert "Continue with" in error


def test_a_refused_sso_never_records_a_connection(driver):
    """The important half. Nobody is signed in, so claiming the site is
    connected would hand agents a grant that reads a logged-out page."""
    signin.begin("https://www.linkedin.com")
    driver.url = "https://accounts.google.com/v3/signin/rejected"

    signin.finish()

    assert origins.list_grants() == []
    assert signin.status()["connecting"] is True, "still connecting, not done"


def test_the_advice_survives_to_the_panel(driver):
    """`status()` is what the Connectors screen polls, so the explanation has to
    be there and not only in the response to the press."""
    signin.begin("https://www.linkedin.com")
    driver.url = "https://accounts.google.com/v3/signin/rejected"
    signin.finish()

    assert "Google" in (signin.status().get("note") or "")


def test_forcing_past_a_refused_sso_is_still_refused(driver):
    """`force` exists so a wrong heuristic cannot lock somebody out of their own
    account. This is not a heuristic being wrong: Google said no, nobody is
    signed in, and recording it anyway would create a connection that reads a
    logged-out page forever."""
    signin.begin("https://www.linkedin.com")
    driver.url = "https://accounts.google.com/v3/signin/rejected"

    out = signin.finish(force=True)

    assert out["ok"] is False
    assert origins.list_grants() == []


# ── the refusal arrives in a window nothing was looking at ───────────────
#
# The bug as photographed: LinkedIn's "Continue with Google" opens a **popup**,
# Google refuses it there, and the window we are driving is still sitting on
# `linkedin.com/login`. Every check above reads that one page, so the refusal
# landed somewhere nothing ever looked and the user was told to "finish signing
# in, then press Done" — about a sign-in that had already been refused and never
# could finish.
#
# Which is precisely the loop `SSO_REFUSED_ADVICE` was written to end. The
# advice was right all along; it just could not see the page it was about.
def test_a_refusal_in_a_popup_window_is_still_recognised(driver):
    signin.begin("https://www.linkedin.com")
    driver.url = "https://www.linkedin.com/login"
    driver.title = "LinkedIn Login, Sign in | LinkedIn"
    driver.popups = [
        ("https://accounts.google.com/v3/signin/rejected"
         "?continue=https%3A%2F%2Fwww.linkedin.com", "Couldn't sign you in"),
    ]

    out = signin.finish()

    assert out["sso_refused"] is True
    assert "Google's rule" in out["error"]
    assert origins.list_grants() == []


def test_a_refusal_is_not_offered_as_something_to_overrule(driver):
    """`still_signing_in` normally means "press Done again and I will believe
    you". A refusal is the one case where that is untrue — `force` is turned
    down — so the card is told which of the two it is looking at."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/login", "Sign in"
    driver.popups = [("https://accounts.google.com/v3/signin/rejected", "")]
    signin.finish()

    assert signin.status()["sso_refused"] is True


def test_walking_away_from_the_refusal_gives_the_override_back(driver):
    """They closed the Google window and went to the site's own form. That is a
    guess about a login page again, and leaving the flag standing would go on
    withholding the second press they now need."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/login", "Sign in"
    driver.popups = [("https://accounts.google.com/v3/signin/rejected", "")]
    signin.finish()

    driver.popups = []
    signin.finish()

    assert signin.status()["sso_refused"] is False
    assert signin.status()["still_signing_in"] is True


def test_a_popup_refusal_reaches_the_panel_too(driver):
    """`status()` is what the card polls, and the card is where the person is
    looking — not the response to the press they already made."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/login", "Sign in"
    driver.popups = [("https://accounts.google.com/v3/signin/rejected", "")]

    signin.finish()

    assert "Google" in (signin.status().get("note") or "")


def test_a_popup_left_open_does_not_block_someone_who_then_signed_in(driver):
    """They gave up on "Continue with Google", typed their password into the
    site itself, and got in — with the refused popup still open behind the
    window. Refusing on the strength of that window would lock somebody out of
    an account they are demonstrably signed into."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/feed/", "Feed"
    driver.popups = [("https://accounts.google.com/v3/signin/rejected",
                      "Couldn't sign you in")]

    out = signin.finish()

    assert out["ok"] is True
    assert [g.host for g in origins.list_grants()] == ["www.linkedin.com"]


def test_an_ordinary_popup_is_not_a_refusal(driver):
    """A help window, a consent frame, a blank tab a script opened. Only the
    identity provider's own refusal address counts, and the generic heuristic
    keeps the rest."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/login", "Sign in"
    driver.popups = [("https://www.linkedin.com/help/", "Help Center"),
                     ("about:blank", "")]

    out = signin.finish()

    assert out.get("sso_refused") is not True
    assert out["still_signing_in"] is True


def test_a_driver_that_cannot_list_windows_still_works(driver):
    """The window list is an extra a real browser has, not a fifth method on the
    `Driver` seam in `session.py`. Anything that cannot answer must behave the
    way it did before this existed."""
    driver.windows = None                     # not callable

    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/feed/", "Feed"

    assert signin.finish()["ok"] is True


# ── what the sign-in window does once the sign-in is over ────────────────
def test_finishing_parks_the_window_rather_than_closing_it(driver):
    """Both wrong answers here were shipped, a day apart.

    Leaving the window on the signed-in site meant it went on holding the
    profile, and every later `browse_open` was refused. Closing it meant the
    finished sign-in tore down the one browser the agents were using, and an
    agent mid-read got "Target page, context or browser has been closed".

    Neither is a browser lifecycle a caller gets to decide. There is one browser,
    `chromium` owns it, and a sign-in that is over simply stops looking at the
    site — so it is not left on somebody's feed, and nothing else loses its page.
    """
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/feed/", "Feed"

    assert signin.finish()["ok"] is True

    assert driver.closed is False, "finishing took the shared browser down"
    assert driver.went_to[-1] == signin.PARKED, "left sitting on the user's feed"


def test_the_grant_is_recorded_before_the_window_goes(driver):
    """Closing must not cost the connection it was proving."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/feed/", "Feed"

    signin.finish()

    assert [g.host for g in origins.list_grants()] == ["www.linkedin.com"]


def test_a_sign_in_that_did_not_finish_keeps_its_window(driver):
    """The other half. Somebody still on the login page needs the window they
    are typing into — closing it on a failed check would throw away the sign-in
    in progress."""
    signin.begin("https://www.linkedin.com")
    driver.url, driver.title = "https://www.linkedin.com/login", "Sign in"

    assert signin.finish()["ok"] is False

    assert driver.closed is False


# ── one browser, and only one ────────────────────────────────────────────
#
# The root cause behind every browser failure this package produced. The profile
# permits exactly one Chromium, and three callers each used to start their own
# and close their own. What came out of it, in order of discovery:
#
#   two launching at once  -> "Failed to create a ProcessSingleton"
#   Chromium merging them  -> "Opening in existing browser session"
#   one closing another's  -> "Target page, context or browser has been closed"
#   one left open          -> every later launch refused until the app quits
#
# Each was fixed on its own and the next one arrived. These pin the property
# that makes the whole family impossible rather than the messages they produced.
def test_everything_shares_one_browser(monkeypatch, driver):
    """Sign-in, sign-out and agents all borrow the same one."""
    from chitragupta.browser import chromium

    monkeypatch.setattr(chromium, "is_installed", lambda: True)
    started = []
    monkeypatch.setattr(chromium, "open_driver",
                        lambda: started.append(1) or driver)
    chromium.reset_shared()

    signin.begin("https://www.linkedin.com")
    driver.url = "https://www.linkedin.com/feed/"
    signin.finish(force=True)
    chromium.forget_site("linkedin.com")
    chromium.shared_driver()

    assert started == [1], f"{len(started)} browsers were started, not one"


def test_nothing_that_borrows_the_browser_closes_it(monkeypatch, driver):
    """A caller ending its own work must never end everybody else's.

    Closing is the app's decision, not a borrower's — which is why the two
    borrowers that used to close are pinned here together.
    """
    from chitragupta.browser import chromium

    monkeypatch.setattr(chromium, "is_installed", lambda: True)
    chromium.reset_shared()

    signin.begin("https://www.linkedin.com")
    signin.cancel()
    driver.url = "https://www.linkedin.com/feed/"
    signin.begin("https://www.linkedin.com")
    signin.finish(force=True)
    chromium.forget_site("linkedin.com")

    assert driver.closed is False


def test_the_shared_browser_can_be_replaced_when_it_dies(monkeypatch):
    """A driver's thread outlives its browser, so a dead handle looks healthy
    and answers nothing. Dropping it is what lets the next caller get a live
    browser instead of the corpse of the last one."""
    from chitragupta.browser import chromium

    made = [FakeDriver(), FakeDriver()]
    monkeypatch.setattr(chromium, "open_driver", lambda: made.pop(0))
    chromium.reset_shared()

    first = chromium.shared_driver()
    assert chromium.shared_driver() is first, "it started a second browser"

    chromium.reset_shared()

    assert chromium.shared_driver() is not first
