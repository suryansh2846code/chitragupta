"""The live browser, and the seam the tests replace.

Everything above this module — the tools an agent calls — talks to a `Session`.
Everything below it is a real browser driven over CDP. The seam is a `Driver`
protocol with four methods, which is what lets the whole capability be tested
without downloading 150 MB of Chromium or reaching the network: a fake driver
satisfies the same protocol, and the parts worth testing (the boundary, the
budget, the quarantine) are all above the line.

**Two checks per navigation, and the second one is the real boundary.** Asking
`origins.may_read` about the URL the agent requested is cheap and refuses early.
But a granted page can redirect anywhere — a session expires and `payroll.example`
bounces to an identity provider; a shortened link resolves somewhere else
entirely — so the address that matters is the one the browser **actually landed
on**, and that is only known afterwards. Checking only the requested URL is a
boundary that any redirect walks straight through, and an injected link is a
redirect away from being a granted one.

When a landing is refused, the page is left behind rather than returned: the
content is dropped, the agent is told where it was sent, and the user is offered
that origin. Nothing about the refused page reaches the model, because a page we
have decided not to allow is a page whose text must not be in the context window
arguing about it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..log import get_logger, suppressed
from . import origins, page
from .page import Node, Snapshot

log = get_logger(__name__)


class Driver(Protocol):
    """What a real browser has to provide. Deliberately four methods."""

    def goto(self, url: str) -> tuple[str, str, list[Node]]:
        """Navigate and return (final_url, title, accessibility nodes)."""

    def current(self) -> tuple[str, str, list[Node]]:
        """Re-read the page that is already open, without navigating."""

    def back(self) -> tuple[str, str, list[Node]]:
        """Go back one entry and return the page, like `goto`."""

    def act(self, kind: str, handle: str, text: str = "") -> tuple[str, str, list[Node]]:
        """Click / type / submit one element, named `role␟name`, and re-read.

        The fifth method, and the seam grew by exactly one because `Session` is
        what performs it and `Session` is where the origin check lives.
        `windows()` did *not* join it: only the sign-in flow needs that, and it
        drives the driver directly.
        """

    def close(self) -> None:
        """Shut the browser down. Must be safe to call twice."""


@dataclass(frozen=True)
class Reading:
    """The outcome of asking to look at a page."""

    ok: bool
    text: str = ""
    #: Where the browser ended up. Present even on a refusal — being told *where*
    #: a granted site sent you is most of the diagnostic value.
    url: str = ""
    title: str = ""
    truncated: bool = False
    #: Set when a grant would change the answer, so the UI can offer that site.
    grantable: str | None = None
    reason: str = ""
    #: The page's fingerprint, so a caller can skip re-reading an unchanged page.
    digest: str = ""
    #: The site is allowed and answered with a sign-in page. A session that has
    #: lapsed, in other words, which is a different thing from a refusal and
    #: needs a different answer: the grant is fine, the login is not.
    needs_signin: bool = False


class Session:
    """One browser, one current page, and the boundary in front of both."""

    def __init__(self, driver: Driver) -> None:
        self._driver = driver
        self._snapshot: Snapshot | None = None

    # ── looking ─────────────────────────────────────────────────────────
    def open(self, url: str) -> Reading:
        """Go to a URL and read it, if we are allowed to be there."""
        asked = origins.may_read(url)
        if not asked.allowed:
            # Refused before the browser is touched: an ungranted address is not
            # a place we request and then decline, it is a place we do not go.
            return Reading(False, grantable=asked.grantable, reason=asked.reason)

        final_url, title, nodes = self._driver.goto(url)
        return self._land(final_url, title, nodes, came_from=url)

    def read(self) -> Reading:
        """Re-read the page that is already open."""
        if self._snapshot is None:
            return Reading(False, reason="No page is open yet.")
        final_url, title, nodes = self._driver.current()
        return self._land(final_url, title, nodes, came_from=self._snapshot.url)

    def back(self) -> Reading:
        if self._snapshot is None:
            return Reading(False, reason="There is nothing to go back to.")
        final_url, title, nodes = self._driver.back()
        return self._land(final_url, title, nodes, came_from=self._snapshot.url)

    def find(self, description: str) -> list[tuple[str, Node]]:
        """Interactive elements on the current page matching a description.

        Substring matching on the accessible name, deliberately dumb: the model
        describes what it is looking for and gets refs back. Anything cleverer
        would be a query language, and a query language over a logged-in page is
        the selector this design exists to avoid handing out.
        """
        if self._snapshot is None:
            return []
        want = " ".join((description or "").lower().split())
        if not want:
            return []
        return [(ref, node) for ref, node in self._snapshot.refs.items()
                if want in node.name.lower() or want in node.role.lower()]

    # ── changing something ──────────────────────────────────────────────
    def act(self, kind: str, ref: str, text: str = "") -> Reading:
        """Click, type into, or submit one element of the page that is open.

        Four things have to be true, and each rules out a way this goes wrong:

        * **A page is open and the ref is on it.** Refs live and die with the
          snapshot, so an instruction injected into a page cannot name an
          element the model was not already shown. A stale ref is refused
          rather than re-resolved against whatever is on screen now.
        * **The site is granted for acting**, judged on the address the browser
          is actually on — not the one it was sent to, and never on anything the
          page says about itself.
        * **The element is named, not selected.** What crosses into the driver
          is `role␟name`; nothing composable, no script, no CSS.
        * **Where it lands is checked afterwards.** A click is the most likely
          thing on a page to navigate, which makes it the most likely way to end
          up somewhere nobody allowed. `_land` decides, exactly as it does for
          `open`, and drops the page if the answer is no.
        """
        if self._snapshot is None:
            return Reading(False, reason="No page is open yet.")
        node = self._snapshot.refs.get(ref)
        if node is None:
            return Reading(
                False, url=self._snapshot.url,
                reason=(f"There is no {ref} on this page. Refs belong to the "
                        "page you last read — read it again and use a ref from "
                        "that."))

        from .driver import ACTS

        if kind not in ACTS:
            return Reading(False, reason=f"{kind!r} is not something to do to a page.")

        allowed = origins.may_act(self._snapshot.url)
        if not allowed.allowed:
            return Reading(False, url=self._snapshot.url,
                           grantable=allowed.grantable, reason=allowed.reason)

        final_url, title, nodes = self._driver.act(kind, node.handle, text)
        return self._land(final_url, title, nodes, came_from=self._snapshot.url)

    # ── state ───────────────────────────────────────────────────────────
    @property
    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    def close(self) -> None:
        self._snapshot = None
        self._driver.close()

    # ── the landing check ───────────────────────────────────────────────
    def _land(self, final_url: str, title: str, nodes: list[Node],
              *, came_from: str) -> Reading:
        """Decide whether we are allowed to be where we ended up.

        This runs after every navigation, including `read()` — a page can move
        itself while nobody is looking, and re-reading is exactly when that shows
        up.
        """
        landed = origins.may_read(final_url)
        if not landed.allowed:
            # The page is dropped, not returned. Its text does not enter the
            # context window, so nothing it says can argue for being allowed.
            self._snapshot = None
            log.info("browser refused a landing at %s (from %s)",
                     final_url, came_from)
            return Reading(False, url=final_url,
                           grantable=landed.grantable,
                           reason=self._redirect_reason(came_from, final_url,
                                                        landed.reason))

        try:
            origin = origins.normalise(final_url)
        except origins.BadOriginError:            # pragma: no cover - may_read agreed
            origin = final_url
        from .signin import is_sign_in_url

        if is_sign_in_url(final_url):
            # The page is dropped rather than returned, for the same reason a
            # refused landing is: handing an agent a login form means it reads
            # one and reports on it, and "Sign in to LinkedIn" is a perfectly
            # coherent summary of a page nobody wanted summarised. Saying the
            # session lapsed is the answer; the form is not.
            self._snapshot = None
            host = final_url
            with suppressed("naming the site whose session lapsed"):
                host = origins.host_of(final_url)
            log.info("browser landed on a sign-in page at %s", final_url)
            return Reading(False, url=final_url, needs_signin=True,
                           reason=(f"You are signed out of {host}. The user "
                                   "needs to connect it again under Connectors "
                                   "— tell them, and do not try to sign in or "
                                   "read around it."))

        snap = page.build(final_url, title, origin, nodes)
        self._snapshot = snap
        text, cut = page.render(snap)
        return Reading(True, text=text, url=final_url, title=snap.title,
                       truncated=cut, digest=page.digest(snap))

    @staticmethod
    def _redirect_reason(came_from: str, final_url: str, plain: str) -> str:
        """Say what happened, in the terms it happened in.

        A bare "that site is not allowed" about an address the agent never asked
        for is baffling — the common cause is an expired session, and the useful
        sentence names both ends. Whoever reads this is either an agent deciding
        what to do next or a user deciding whether to sign in again.
        """
        try:
            from_host = origins.host_of(came_from)
        except origins.BadOriginError:
            return plain
        try:
            to_host = origins.host_of(final_url)
        except origins.BadOriginError:
            return (f"{from_host} sent us somewhere that is not a normal web "
                    "address, so nothing was read.")
        if from_host == to_host:
            return plain
        return (f"{from_host} redirected to {to_host}, which is not a site you "
                "have allowed. Nothing on it was read. If you were signed out, "
                "open the browser window and sign in again.")
