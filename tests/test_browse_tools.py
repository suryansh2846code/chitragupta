"""The tools an agent actually calls, and what it is told when refused.

`agents/browse_tools.py` is thin on purpose — the boundary, the bounding and the
quarantine are all in `browser/`. What it owns is the wording and the verdict,
and both of those change behaviour:

* a refusal has to come back as a **failure**, or the loop reissues the same call
  until the budget is gone (`results.py` exists because guessing that from the
  text got all three real failures wrong)
* the refusal has to name the site and say who can allow it, or an agent reports
  "I couldn't" where it could have reported "ask the user to allow payroll"
* an agent must **never** be able to widen its own access, or the allow-list is
  decorative

And one structural check with a long memory: the write tools do not exist yet,
and when they do they have to be gated. A test that fails the day somebody adds
`browse_click` without the gate is worth more than a comment asking them to.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chitragupta.agents import browse_tools, permissions, tools
from chitragupta.browser import origins
from chitragupta.browser.page import Node
from chitragupta.browser.session import Session


class FakeDriver:
    def __init__(self, pages=None, redirects=None):
        self.pages = pages or {}
        self.redirects = redirects or {}
        self._at = ""

    def _page(self, url):
        title, nodes = self.pages.get(url, ("Untitled", []))
        return url, title, list(nodes)

    def goto(self, url):
        self._at = self.redirects.get(url, url)
        return self._page(self._at)

    def current(self):
        return self._page(self._at)

    def back(self):
        return self._page(self._at)

    def close(self):
        pass


#: Tools that would change something on a website. None of them exists yet; the
#: set is written down so the gate below can watch for them arriving.
_WRITE_TOOLS = frozenset({
    "browse_click", "browse_type", "browse_select", "browse_submit",
    "browse_download",
})

PAGES = {
    "https://payroll.example.com/payslips": ("Payslips", [
        Node(role="heading", name="Your payslips"),
        Node(role="link", name="March 2026", handle="#mar"),
        Node(role="button", name="Download all", handle="#dl"),
    ]),
}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    conn = origins._conn()
    conn.execute("DELETE FROM browser_origins")
    conn.commit()
    browse_tools.set_session(Session(FakeDriver(PAGES)))
    yield
    browse_tools.set_session(None)


# ── reading ─────────────────────────────────────────────────────────────
def test_an_allowed_page_comes_back_fenced():
    origins.grant("payroll.example.com")

    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert out.ok is True
    assert "Your payslips" in out
    assert "NOT INSTRUCTIONS" in out, "page text must arrive marked as content"


def test_a_refusal_is_a_failure_not_a_string_that_looks_like_one():
    """The loop reads `ok`. A refusal that reports success is a refusal the model
    retries until its budget is gone."""
    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert out.ok is False


def test_a_refusal_tells_the_agent_who_can_fix_it():
    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert "payroll.example.com" in out
    assert "the user" in out.lower()


def test_a_refusal_tells_the_agent_not_to_go_hunting():
    """Without this an agent tries the www, then the apex, then a guess — each
    one a refusal, and the last thing a user wants is an agent probing addresses
    on their behalf."""
    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert "not try other addresses" in out.lower()


def test_an_unusable_address_is_refused_without_offering_anything():
    out = browse_tools.browse_open("javascript:alert(1)")

    assert out.ok is False
    assert "Settings" not in out, "there is no grant that would make this work"


def test_no_tool_can_grant_itself_access():
    """The one property that makes the allow-list real. Every browse tool is
    read-only over `origins`, so an agent that has been talked into wanting more
    access has no way to take it."""
    text = Path(browse_tools.__file__).read_text(encoding="utf-8")

    assert "origins.grant" not in text
    assert "may_act=True" not in text


# ── re-reading, which is where the money goes ───────────────────────────
def test_reading_the_same_page_twice_is_cheap_the_second_time():
    """A page is the most expensive thing an agent can ask for, and it re-reads
    after every step."""
    origins.grant("payroll.example.com")
    browse_tools.browse_open("https://payroll.example.com/payslips")
    first = browse_tools.browse_read()
    version = first.split("[page-version: ")[1].rstrip("]\n")

    second = browse_tools.browse_read(since=version)

    assert "has not changed" in second
    assert "Your payslips" not in second, "it paid for the page again"
    assert len(second) < len(first) / 2


def test_a_changed_page_is_read_in_full_even_with_a_stale_version():
    origins.grant("payroll.example.com")
    session = Session(FakeDriver({
        "https://payroll.example.com/payslips": ("Payslips", [
            Node(role="heading", name="Your payslips")])}))
    browse_tools.set_session(session)
    browse_tools.browse_open("https://payroll.example.com/payslips")

    out = browse_tools.browse_read(since="notthecurrentdigest")

    assert "Your payslips" in out


def test_a_read_carries_a_version_the_agent_can_pass_back():
    origins.grant("payroll.example.com")
    browse_tools.browse_open("https://payroll.example.com/payslips")

    assert "[page-version: " in browse_tools.browse_read()


# ── finding ─────────────────────────────────────────────────────────────
def test_find_returns_refs_and_never_a_selector():
    origins.grant("payroll.example.com")
    browse_tools.browse_open("https://payroll.example.com/payslips")

    out = browse_tools.browse_find("download")

    assert "[e2] button: Download all" in out
    assert "#dl" not in out


def test_finding_nothing_says_the_page_may_have_changed():
    """The right failure for a site that redesigned overnight: "I could not find
    that" beats clicking something nearby."""
    origins.grant("payroll.example.com")
    browse_tools.browse_open("https://payroll.example.com/payslips")

    out = browse_tools.browse_find("the big red button")

    assert out.ok is False
    assert "may have changed" in out


def test_finding_with_no_page_open_says_what_to_do_first():
    out = browse_tools.browse_find("anything")

    assert out.ok is False
    assert "browse_open" in out


# ── orientation ─────────────────────────────────────────────────────────
def test_an_agent_can_ask_which_sites_it_may_read():
    origins.grant("payroll.example.com")
    origins.grant("linkedin.com")

    out = browse_tools.browse_sites()

    assert "payroll.example.com" in out
    assert "linkedin.com" in out


def test_with_nothing_allowed_it_says_so_and_says_where_to_change_it():
    out = browse_tools.browse_sites()

    assert "not allowed any websites" in out
    assert "Settings" in out


# ── the registry, and the gate that must arrive with the write tools ────
@pytest.mark.parametrize("name", ["browse_open", "browse_read", "browse_find",
                                  "browse_sites"])
def test_every_browse_tool_is_registered_both_sides(name):
    """A spec with no implementation is a tool that 500s when a model picks it."""
    assert name in tools.TOOL_IMPLS
    assert name in [row["name"] for row in tools.describe_tools()]


def test_the_browse_tools_are_grouped_where_a_person_would_look():
    """Not folded into "Web": a search returns public results, these read pages
    the user is signed in to, and the person ticking the box should see the
    difference."""
    assert tools.tool_label("browse_open")[1] == "Websites you allow"


def test_the_browser_is_refused_until_the_user_grants_it():
    """What replaced "an agent holding the tool must declare it on its card".

    That rule was written when holding the tool meant having the capability, so
    an agent with it and a silent card was an agent with the most dangerous
    thing in the app and nothing saying so.

    The browser is a connector now. Every agent is offered the tools — hiding
    them makes an agent say it cannot read a website, which is false — and the
    tool is refused until the user grants it, for that agent, once or always.
    The origin list still decides which sites. So the tool grants nothing, and
    the gate is what has to be pinned.
    """
    from chitragupta.agents import connector_grants

    for tool in ("browse_sites", "browse_open", "browse_read", "browse_find"):
        assert connector_grants.connector_of(tool) == "browser", (
            f"{tool} reaches the browser without asking anybody")

    assert not connector_grants.may_use("research", "browser"), (
        "an agent could open a browser with no grant at all")


def test_an_agent_the_browser_is_part_of_says_so_on_its_card():
    """Informed consent, in the place the decision is actually made.

    Not every agent that *can* ask — that is all of them now — but every agent
    the browser is a stated part of. `needs=["browser"]` is the claim that it
    does not work without one, and a card that does not mention it is a card
    that lied about what the user was taking on.
    """
    from chitragupta.agents import library

    for template in library.TEMPLATES:
        if "browser" not in template.needs:
            continue
        assert "browser" in template.works_with, (
            f"{template.id} needs a browser and its card never mentions it")


def test_holding_the_tool_grants_no_access_at_all():
    """Why the generalist having them is safe, stated as a test.

    The consent that matters is the **origin**, not the tool. An agent with
    `browse_open` and no allowed sites can reach nothing — so "every tool" can
    keep meaning every tool, and the user's list stays the only thing that opens
    a door. If this ever passes by reaching a page, the boundary has moved to the
    wrong place.
    """
    from chitragupta.agents import library

    chief = next(t for t in library.TEMPLATES
                 if library.EVERYTHING in t.tools)
    assert "browse_open" in chief.resolved_tools()

    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert out.ok is False
    assert origins.list_grants() == []


def test_a_write_tool_cannot_be_added_without_the_unattended_gate():
    """The long-memory check.

    `browse_click`, `browse_type`, `browse_submit` and `browse_download` do not
    exist yet. The day one does, it must be in `NEVER_UNATTENDED` in the same
    commit — a routine triggered by a stranger's email is precisely the caller
    that must not be able to click inside the user's logged-in accounts. This
    test fails the moment that is forgotten, which a comment asking nicely would
    not.
    """
    present = _WRITE_TOOLS & set(tools.TOOL_IMPLS)

    assert present <= permissions.NEVER_UNATTENDED, (
        f"{sorted(present - permissions.NEVER_UNATTENDED)} can act on a website "
        "and is not in permissions.NEVER_UNATTENDED")


def test_that_gate_would_actually_notice(monkeypatch):
    """The guard above passes today because no write *tool* exists — which is
    also how a guard quietly stops working.

    `conftest.py` covers its own vendor-login guard for this reason: a check
    nobody exercises is a check that has never been shown to fire. So one is
    added here, ungated, and the same condition is asserted to fail.

    It has to be a name that is not gated already: `browse_click` used to serve
    and cannot any more, because it is now a real RED action and therefore
    genuinely in the set. `browse_download` is the next one along that does not
    exist yet, and the day it does, this picks the one after.
    """
    ungated = next(n for n in sorted(_WRITE_TOOLS)
                   if n not in permissions.NEVER_UNATTENDED)
    monkeypatch.setitem(tools.TOOL_IMPLS, ungated, lambda ref: "did it")

    present = _WRITE_TOOLS & set(tools.TOOL_IMPLS)

    assert present, "the simulated write tool is not being seen at all"
    assert not present <= permissions.NEVER_UNATTENDED, (
        f"an ungated {ungated} did not trip the check that exists to catch it")


# ── acting on a page, and what it had to bring with it ───────────────────
def test_every_browser_write_waits_for_a_tap():
    """`docs/BROWSER.md` §5: a routine reads text a stranger wrote, and combined
    with a browser an injected instruction reaches an agent that can click
    inside the user's logged-in accounts.

    So these are RED, which puts them in `NEVER_UNATTENDED` by derivation — an
    unattended agent may look and may report, and may not act, whatever the
    site is set to.
    """
    from chitragupta.actions import REGISTRY, Risk

    for name in ("browse_click", "browse_type", "browse_submit"):
        assert name in REGISTRY, f"{name} is not a declared action"
        assert REGISTRY[name].risk is Risk.RED, f"{name} is not RED"
        assert name in permissions.NEVER_UNATTENDED


def test_a_browser_write_says_why_it_waits_in_the_users_terms():
    """A RED action carries its own sentence. "Creating automations always needs
    your approval" about a click would teach the user nothing except that the
    app is confused."""
    from chitragupta.actions import REGISTRY

    for name in ("browse_click", "browse_type", "browse_submit"):
        because = REGISTRY[name].always_ask_because
        assert because, f"{name} waits and does not say why"
        assert "approval" in because.lower()


def test_a_browser_write_is_never_offered_an_undo():
    """There is no inverse of a click. The page decided what it meant, and a
    button claiming to take it back would be a lie about somebody else's
    application."""
    from chitragupta.actions import REGISTRY

    for name in ("browse_click", "browse_type", "browse_submit"):
        assert REGISTRY[name].undo is None


def test_the_card_shows_the_page_s_own_name_for_the_thing():
    """`label` and `url` are on every card, and they are what the *page*
    reported — not the agent's description of what it is about to press. That
    is the difference between approving "Click “Send” on web.whatsapp.com" and
    taking an agent's word for what a button does."""
    from chitragupta.actions import REGISTRY

    for name in ("browse_click", "browse_type", "browse_submit"):
        assert "label" in REGISTRY[name].fields
        assert "url" in REGISTRY[name].fields
    assert REGISTRY["browse_type"].fields[0] == "text", (
        "the text being typed is the first thing the user should see")


# ── when the browser itself will not start ───────────────────────────────
#
# The reported failure, and the most expensive kind: the tool raised, the
# generic catch in `tools.run_tool` wrapped Playwright's own words — *"Failed to
# create a ProcessSingleton for your profile directory"* — and handed them to
# the model, which reasonably turned that into "the browser session closed
# unexpectedly" and offered to try again. It then failed identically, twice,
# because retrying cannot close the other window holding the profile.
#
# Two things are wrong there and both are this module's to own: an internal
# reached the model, and the advice it produced could not work.
class _WontStart:
    """A driver whose browser refuses to launch, the way a locked profile does."""

    def __init__(self, message):
        self.message = message

    def _boom(self, *_args):
        from chitragupta.browser.driver import BrowserError
        raise BrowserError(self.message)

    goto = current = back = _boom

    def close(self):
        pass


LOCKED = ("BrowserType.launch_persistent_context: Failed to create a "
          "ProcessSingleton for your profile directory. This usually means "
          "that the profile is already in use by another instance of Chromium.")


def _wont_start(message=LOCKED):
    browse_tools.set_session(Session(_WontStart(message)))


def test_a_browser_that_will_not_start_is_a_failure_not_an_exception():
    """It has to reach the loop as a verdict. An exception becomes "Tool
    browse_open failed: <whatever Playwright said>", which is the path that
    produced the wrong advice."""
    origins.grant("payroll.example.com")
    _wont_start()

    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert out.ok is False


def test_playwrights_own_words_never_reach_the_model():
    """`/CLAUDE.md`: never surface an internal. A tool result is user-facing by
    the time a model has repeated it back to somebody."""
    origins.grant("payroll.example.com")
    _wont_start()

    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert "ProcessSingleton" not in out
    assert "launch_persistent_context" not in out
    assert "Playwright" not in out


def test_a_locked_profile_says_what_actually_clears_it():
    """The whole point. "Try again" cannot work — nothing about retrying closes
    the window that is holding the profile — so the answer has to name the
    window and tell the agent not to retry."""
    origins.grant("payroll.example.com")
    _wont_start()

    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert "close" in out.lower() and "window" in out.lower()
    assert "not retry" in out.lower() or "do not retry" in out.lower()


def test_a_browser_that_is_not_set_up_keeps_its_own_sentence():
    """That message is already written for a person — "choose Set up browsing".
    Replacing it with something vaguer would lose the one instruction that
    works."""
    from chitragupta.browser.chromium import BrowserNotReadyError

    origins.grant("payroll.example.com")

    def refuses():
        raise BrowserNotReadyError(
            "The browser has not been set up yet. Open Connectors and choose "
            "“Set up browsing”.")

    browse_tools.set_session(None)
    import chitragupta.browser.chromium as chromium_mod
    original = chromium_mod.open_session
    chromium_mod.open_session = lambda: refuses()
    try:
        out = browse_tools.browse_open("https://payroll.example.com/payslips")
    finally:
        chromium_mod.open_session = original

    assert out.ok is False
    assert "Set up browsing" in out


def test_re_reading_and_finding_are_covered_too():
    """All three entry points ask for the browser, so all three can raise."""
    origins.grant("payroll.example.com")
    _wont_start()

    browse_tools.browse_open("https://payroll.example.com/payslips")
    assert browse_tools.browse_read().ok is False
    assert "ProcessSingleton" not in browse_tools.browse_read()


# ── the browser that was alive and is not any more ───────────────────────
#
# Reported from a real log:
#
#   12:24:29  browser sign-in finished for web.whatsapp.com   ← we close it
#   12:25:28  browse_open -> Page.goto: Target page, context or browser has been closed
#
# Connecting a site opens a browser and closes it on Done, and Chromium hands a
# second launch on the same profile to the first process — so the two are one
# browser and closing either closes both. The agent's page died with it.
#
# Both halves of the answer were wrong. It was reported as "the browser could
# not be started", which it was not, **and** the advice was "do not retry" —
# the exact inversion, because this is the one failure a retry fixes. The agent
# said so in as many words: "I've been told not to keep retrying this same call".
CLOSED = "Page.goto: Target page, context or browser has been closed"


def test_a_closed_browser_is_not_reported_as_one_that_would_not_start():
    origins.grant("payroll.example.com")
    _wont_start(CLOSED)

    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert out.ok is False
    assert "could not be started" not in out


def test_a_closed_browser_invites_exactly_one_retry():
    """The advice has to be the opposite of the locked-profile case. Retrying a
    lock cannot help; retrying a closed browser is the whole fix."""
    origins.grant("payroll.example.com")
    _wont_start(CLOSED)

    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert "again" in out.lower()
    assert "do not retry" not in out.lower()


def test_a_closed_browser_is_thrown_away_so_the_next_call_gets_a_new_one():
    """The half that makes the advice true.

    A driver's thread outlives its browser, so `_ensure_started` finds it alive
    and returns without relaunching — the cached session stays broken for the
    life of the app and "call this again" would be a lie. Retrying only works if
    the dead session is gone.
    """
    origins.grant("payroll.example.com")
    _wont_start(CLOSED)

    browse_tools.browse_open("https://payroll.example.com/payslips")

    assert browse_tools._session is None, "the dead session was kept"


def test_a_browser_that_would_not_start_keeps_its_session():
    """The other side of that decision. Nothing died, and an agent may still
    hold refs into the page that is open."""
    origins.grant("payroll.example.com")
    _wont_start(LOCKED)

    browse_tools.browse_open("https://payroll.example.com/payslips")

    assert browse_tools._session is not None


# ── one browser means one window, and a person may be using it ───────────
def test_an_agent_does_not_take_the_window_out_from_under_a_password():
    """Sharing one browser is what removed the whole family of launch failures,
    but it means the window an agent drives is the window somebody types their
    password into. Navigating it mid-sign-in would lose the sign-in and read as
    the app fighting them."""
    from chitragupta.browser import chromium, signin

    origins.grant("payroll.example.com")

    class _Fake:
        url, title = "https://payroll.example.com/login", "Sign in"
        def goto(self, url): return self.url, self.title, []
        def current(self): return self.url, self.title, []
        def close(self): pass

    original = chromium.open_driver
    chromium.open_driver = _Fake
    chromium.reset_shared()
    try:
        signin.begin("https://payroll.example.com")

        out = browse_tools.browse_open("https://payroll.example.com/payslips")

        assert out.ok is False
        assert "signing in" in out.lower()
        assert "do not retry in this turn" in out.lower()
    finally:
        signin._clear(close_browser=False)
        chromium.open_driver = original
        chromium.reset_shared()


def test_once_the_sign_in_is_over_the_browser_is_available_again():
    """The guard is a wait, not a refusal — it has to end."""
    from chitragupta.browser import signin

    origins.grant("payroll.example.com")
    signin._clear(close_browser=False)

    out = browse_tools.browse_open("https://payroll.example.com/payslips")

    assert out.ok is True


def test_a_dead_browser_is_dropped_from_the_shared_slot_too():
    """Clearing the agent's session alone would not help: the next one is handed
    the same dead driver by `shared_driver()`."""
    from chitragupta.browser import chromium

    origins.grant("payroll.example.com")
    chromium.reset_shared()
    kept = chromium.shared_driver()
    _wont_start(CLOSED)

    browse_tools.browse_open("https://payroll.example.com/payslips")

    assert chromium.shared_driver() is not kept, "the corpse was handed on"
    chromium.reset_shared()
