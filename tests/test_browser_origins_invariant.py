"""The boundary, checked as a property rather than case by case.

`test_browser_origins.py` attacks specific spellings — userinfo, homoglyphs,
suffix matches, IP literals. That catches the tricks somebody thought of. This
file checks the thing that has to be true *whatever* was thought of:

    If `may_read(url)` allows, then the host the browser would actually contact
    is covered by a host the user granted.

Written deliberately against a different seam. The other suite asks "is this
spelling refused"; this one takes the answer the boundary gave and re-derives
the destination independently, the way a driver would when it finally navigates.
A disagreement between those two is the shape of every real bypass: the checker
and the navigator did not mean the same host.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from chitragupta.browser import origins

#: Granted in every test below. Two hosts, one nested, so both the "covered" and
#: "more specific" paths are live while the fuzz runs.
GRANTED = ("linkedin.com", "mail.example.com")

#: Everything a page, a redirect or an injected link might hand a tool. Not
#: exhaustive — the point is that the assertion holds for anything at all.
CANDIDATES = [
    "https://linkedin.com",
    "https://linkedin.com/feed?next=https://evil.test",
    "https://www.linkedin.com/in/someone",
    "https://LINKEDIN.COM",
    "https://linkedin.com.",
    "https://linkedin.com:443/x",
    "https://linkedin.com@evil.test/",
    "https://evil.test/?u=linkedin.com",
    "https://evil.test#linkedin.com",
    "https://notlinkedin.com",
    "https://linkedin.com.evil.test",
    "https://linkedin.evil.test",
    "https://xn--linkedin-.com",
    "https://аmazon.com",
    "https://mail.example.com/inbox",
    "https://example.com",
    "https://shop.example.com",
    "https://mail.example.com.evil.test",
    "http://linkedin.com",
    "//linkedin.com",
    "https://127.0.0.1",
    "https://192.168.0.1",
    "https://[::1]",
    "javascript:fetch('https://linkedin.com')",
    "data:text/html,<a href=https://linkedin.com>",
    "file:///Users/someone/Library",
    "",
    "   ",
    "https://",
    "https://linkedin.com:8443",
    "https://user:pass@linkedin.com.evil.test/",
    "https://linkedin.com%2Eevil.test",
    "https://linkedin%2ecom.evil.test",
    "https://‮linkedin.com",
]


@pytest.fixture(autouse=True)
def granted():
    conn = origins._conn()
    conn.execute("DELETE FROM browser_origins")
    conn.commit()
    for host in GRANTED:
        origins.grant(host)
    yield


def _destination(url: str) -> str | None:
    """Where a browser would actually go, derived without using `origins`.

    Independent on purpose: reusing `normalise` here would make the assertion
    compare the boundary against itself, which is exactly the tautology that
    lets a bypass pass its own tests.
    """
    try:
        parts = urlsplit(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    host = (parts.hostname or "").rstrip(".").lower()
    return host or None


def _covered_by(granted: str, host: str) -> bool:
    """Is `host` inside `granted`, decided by splitting labels?

    **Not** `origins.covers`. The first version of this file called it, and when
    the label-boundary check was replaced with a bare `endswith` — the classic
    bypass, where `evil-linkedin.com` counts as `linkedin.com` — every test here
    still passed, because the assertion and the code under test shared the bug.
    An invariant checked with the implementation it is checking is not an
    invariant; it is a restatement.
    """
    want = [label for label in granted.split(".") if label]
    have = [label for label in host.split(".") if label]
    return len(have) >= len(want) and have[-len(want):] == want


@pytest.mark.parametrize("url", CANDIDATES)
def test_anything_allowed_lands_on_a_host_the_user_granted(url):
    """The invariant. One failure here is a bypass, whatever the reason."""
    verdict = origins.may_read(url)
    if not verdict.allowed:
        return

    landing = _destination(url)
    assert landing is not None, f"allowed {url!r} with no derivable host"
    assert any(_covered_by(host, landing) for host in GRANTED), (
        f"allowed {url!r}, which would contact {landing!r} — "
        "not covered by any grant")


@pytest.mark.parametrize("url", CANDIDATES)
def test_anything_allowed_is_https(url):
    """A permitted read must never be downgradeable in flight."""
    if origins.may_read(url).allowed:
        assert origins.normalise(url).startswith("https://")


#: Both ways of asking, because the invariant below is about the *shape* of a
#: refusal and not about reading. It was written for `may_read` alone, and
#: `may_act` spent a release returning `grantable=None` with a sentence naming
#: an approval card that had been removed — a dead end this parametrisation
#: would have caught on the day it shipped.
ASKERS = {"read": origins.may_read, "change": origins.may_act}


@pytest.mark.parametrize("url", CANDIDATES)
@pytest.mark.parametrize("capability", sorted(ASKERS))
def test_a_refusal_never_offers_a_site_it_would_then_refuse(capability, url):
    """`grantable` drives a one-click "Allow this site" button.

    If granting the offered origin would not actually unblock the URL the agent
    was trying to use, that button is a control that cannot work — the failure
    `/CLAUDE.md` names first, and here it would also teach the user that the
    allow-list is unreliable.

    The grant is made with exactly the capability the refusal asked for, which
    is the other half: an offer that names the site but not the permission sends
    the user to turn on something that is already on.
    """
    ask = ASKERS[capability]
    verdict = ask(url)
    if verdict.allowed or not verdict.grantable:
        return

    origins.grant(verdict.grantable,
                  may_act=verdict.needs == origins.CHANGE)
    try:
        assert ask(url).allowed, (
            f"offered to allow {verdict.grantable!r} ({verdict.needs}) for "
            f"{url!r}, and it was still refused afterwards")
    finally:
        origins.revoke(verdict.grantable)


@pytest.mark.parametrize("url", CANDIDATES)
@pytest.mark.parametrize("capability", sorted(ASKERS))
def test_a_refusal_a_grant_would_fix_always_names_the_grant(capability, url):
    """The dead end, stated as a rule rather than as one case.

    A refusal carrying no `grantable` is saying "nothing you can do changes
    this", and that has to be true: a bad address is genuinely ungrantable, a
    site merely not on the list is not. Anything that a grant *would* fix has to
    name both the site and the capability, or the layers above have nothing to
    offer and word it by guessing.
    """
    verdict = ASKERS[capability](url)
    if verdict.allowed:
        return
    if verdict.grantable is None:
        # Then granting must genuinely be powerless here. The only such case is
        # an address we would never accept in the first place.
        with pytest.raises(origins.BadOriginError):
            origins.normalise(url)
        return
    assert verdict.needs in (origins.READ, origins.CHANGE), verdict


@pytest.mark.parametrize("url", CANDIDATES)
def test_no_input_makes_the_boundary_raise(url):
    """It is called from a tool, with whatever a page contained. An exception
    escaping here is a crashed turn rather than a refused navigation."""
    verdict = origins.may_read(url)

    assert isinstance(verdict.allowed, bool)
    assert verdict.allowed or verdict.reason, "a refusal must say something"


@pytest.mark.parametrize("url", CANDIDATES)
def test_acting_is_never_allowed_by_a_read_only_grant(url):
    """Every grant in this file is read-only, so no input may unlock acting."""
    assert origins.may_act(url).allowed is False
