"""Driving a real Chromium, behind the four-method seam in `session.py`.

Two decisions carry this module.

**The page arrives as an ARIA snapshot, not as HTML.** Playwright's
`aria_snapshot()` is the tree a screen reader would read — roles, accessible
names, values — which is both an order of magnitude smaller than markup and the
only view that does not carry `<script>`, hidden text and twenty thousand tokens
of class soup. Parsing it is a pure function (`parse_aria`), so the part most
likely to be wrong is tested without a browser.

**Playwright runs on a thread of its own, and nothing else touches it.** Its
sync API refuses to run inside an asyncio loop, and this is called from tool
code that may be anywhere — a plain handler thread, a worker, a turn loop. A
dedicated thread with a command queue sidesteps the question entirely, and gives
the other property this needs for free: **one browser, one page, one caller at a
time**. Two agents driving the same profile concurrently would fight over the
cookie jar that makes a site "signed in".

**A handle is a role and a name, never a selector.** That is what a future
`browse_click` will resolve through `get_by_role`, and it is deliberately not
something a model could compose into anything else — the ref it sees is `e3`.
"""
from __future__ import annotations

import queue
import re
import threading
from dataclasses import dataclass
from typing import Any

from ..log import get_logger, suppressed
from .page import Node

log = get_logger(__name__)

#: How long any single browser operation may take before we give up on it. A
#: page that never settles must not hold an agent's turn open indefinitely.
TIMEOUT_MS = 20_000

#: Chromium switches we always launch with.
#:
#: The browser is shut down by killing it — that is what "anything we spawn, we
#: clean up" amounts to for a process with no other way out — and Chromium
#: records that as a crash. So the *next* launch greets the user with a bubble
#: reading "Chromium didn't shut down correctly. Restore pages?", which is our
#: own cleanup presented to them as a fault of theirs, offering to reopen the
#: tabs of somebody's last sign-in. Playwright does not pass this for a
#: persistent context, so we do.
LAUNCH_ARGS = ("--hide-crash-restore-bubble",)

#: Lines in an ARIA snapshot that describe the *previous* node rather than a new
#: one — `/url:` under a link, for instance. They are metadata, not content.
_META = re.compile(r"^/")

#: `- role "name" [level=1]: value` in its various shapes.
_LINE = re.compile(
    r"^-\s+"
    r"(?P<role>[A-Za-z][\w-]*)"
    r"(?:\s+\"(?P<name>(?:[^\"\\]|\\.)*)\")?"
    r"(?P<attrs>(?:\s*\[[^\]]*\])*)"
    r"(?:\s*:\s*(?P<value>.*))?"
    r"\s*$"
)


def parse_aria(snapshot: str) -> list[Node]:
    """An ARIA snapshot as a flat list of nodes.

    Flattened on purpose: the model is given a readable page and refs, not a
    tree to navigate. Nesting in the snapshot is layout, and layout is the part
    of a page that changes every quarter.
    """
    found: list[Node] = []
    for raw in (snapshot or "").splitlines():
        line = raw.strip()
        if not line.startswith("-"):
            continue
        body = line[1:].strip()
        if not body or _META.match(body):
            continue                     # `/url: …` belongs to the node above
        match = _LINE.match(line)
        if not match:
            continue
        role = (match.group("role") or "").lower()
        name = match.group("name")
        value = (match.group("value") or "").strip()
        if name is None:
            # `- paragraph: Three available.` — the text *is* the name, and a
            # node whose only content sat in `value` would render as a bare role.
            name, value = value, ""
        found.append(Node(role=role, name=_unescape(name), value=value,
                          handle=_handle(role, _unescape(name))))
    return found


def _unescape(text: str) -> str:
    return (text or "").replace('\\"', '"').replace("\\\\", "\\")


def _handle(role: str, name: str) -> str:
    """How a later `browse_click` will find this element again.

    Role plus accessible name — Playwright's own locator, and the same pair a
    person would use to describe the control. Never a CSS selector: the point of
    refs is that nothing composable reaches the model.
    """
    return f"{role}␟{name}" if name else role


@dataclass
class _Command:
    name: str
    args: tuple = ()
    reply: queue.Queue = None            # type: ignore[assignment]


class PlaywrightDriver:
    """A real browser, owned by one thread.

    Started lazily: constructing this must not launch anything, because
    `open_session()` is called to *ask* whether browsing works.
    """

    def __init__(self, executable: str | None, profile: str, *,
                 headless: bool = False) -> None:
        self._executable = executable
        self._profile = profile
        self._headless = headless
        self._commands: queue.Queue[_Command | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: str = ""

    # ── the public four ─────────────────────────────────────────────────
    def goto(self, url: str) -> tuple[str, str, list[Node]]:
        return self._call("goto", url)

    def current(self) -> tuple[str, str, list[Node]]:
        return self._call("current")

    def back(self) -> tuple[str, str, list[Node]]:
        return self._call("back")

    def clear_cookies(self, domain: str) -> None:
        """Forget the sign-in for one site, leaving every other one alone."""
        self._call("clear_cookies", domain)

    def act(self, kind: str, handle: str, text: str = "") -> tuple[str, str, list[Node]]:
        """Do one thing to one element, named the way a person would name it.

        `handle` is `role␟name` — the pair `parse_aria` built and the same pair
        `get_by_role` resolves. **Never a selector and never script.** A model
        that can emit either into a logged-in page owns that account, which is
        the whole reason the page arrives as refs in the first place.

        Returns the page afterwards, exactly as `goto` does, so the caller can
        check where the click actually took the browser.
        """
        return self._call("act", kind, handle, text)

    def windows(self) -> list[tuple[str, str]]:
        """`(url, title)` for every window this browser has open.

        Deliberately **not** a fifth method on the `Driver` protocol in
        `session.py`. An agent's `Session` reads the one page it navigated, and
        that is a property worth keeping: a page that can `window.open` anything
        would otherwise have a lever on which page the next read comes from.

        The sign-in flow is the one caller, it drives the driver directly
        already, and it has to see a popup — because "Continue with Google"
        *is* one, and so is the refusal that comes back from it.
        """
        return self._call("windows")

    def close(self) -> None:
        """Safe to call twice, and safe to call before anything started."""
        if self._thread is None:
            return
        with suppressed("shutting the browser down"):
            self._commands.put(None)
            self._thread.join(timeout=10)
        self._thread = None

    # ── the thread ──────────────────────────────────────────────────────
    def _call(self, name: str, *args: Any) -> Any:
        self._ensure_started()
        reply: queue.Queue = queue.Queue(maxsize=1)
        self._commands.put(_Command(name, args, reply))
        ok, payload = reply.get(timeout=TIMEOUT_MS / 1000 + 10)
        if not ok:
            raise BrowserError(payload)
        return payload

    def _ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        # Cleared before the attempt, not after it. A failure used to *latch*:
        # `_run` sets this and nothing ever unsets it, so the first call after
        # the cause was fixed — the other browser closed, the profile free —
        # started a browser perfectly well and then raised last time's message
        # at it. Recovery took two tries and reported a reason that was no
        # longer true, which is worse than either failing or working.
        self._start_error = ""
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="chitragupta-browser")
        self._thread.start()
        if not self._ready.wait(timeout=60):
            raise BrowserError("The browser did not start.")
        if self._start_error:
            raise BrowserError(self._start_error)

    def _run(self) -> None:                       # pragma: no cover - needs a browser
        from playwright.sync_api import sync_playwright

        context = None
        try:
            with sync_playwright() as pw:
                launch: dict[str, Any] = {"headless": self._headless,
                                          "args": list(LAUNCH_ARGS)}
                if self._executable:
                    launch["executable_path"] = self._executable
                # A persistent context is what makes a site stay signed in. The
                # profile is ours, never the user's own Chrome — see
                # `chromium.py`.
                context = pw.chromium.launch_persistent_context(
                    self._profile, **launch)
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(TIMEOUT_MS)
                # Cookies belong to the context, not the page, and signing a
                # site out is the one command that needs to reach them.
                self._context = context
                self._ready.set()
                self._serve(page)
        except Exception as exc:
            self._start_error = str(exc)[:300]
            self._ready.set()
            log.warning("browser thread stopped: %s", exc)
        finally:
            if context is not None:
                with suppressed("closing the browser context"):
                    context.close()

    def _serve(self, page: Any) -> None:          # pragma: no cover - needs a browser
        while True:
            command = self._commands.get()
            if command is None:
                return
            try:
                command.reply.put((True, self._do(page, command)))
            except Exception as exc:
                command.reply.put((False, str(exc)[:300]))

    def _do(self, page: Any, command: _Command) -> Any:  # pragma: no cover
        if command.name == "goto":
            page.goto(command.args[0], wait_until="load")
            settle(page)
        elif command.name == "back":
            page.go_back(wait_until="load")
            settle(page)
        elif command.name == "clear_cookies":
            # Playwright filters by domain including subdomains, which is what
            # signing out of a site means — a token left on `www.` or `m.` is a
            # session the user was told had ended.
            context = getattr(self, "_context", None)
            if context is None:
                raise BrowserError("the browser is not holding a profile")
            context.clear_cookies(domain=command.args[0])
            return None
        elif command.name == "act":
            kind, handle, text = command.args
            locate(page, handle, kind, text)
            settle(page)
        elif command.name == "windows":
            context = getattr(self, "_context", None)
            if context is None:
                raise BrowserError("the browser is not holding a profile")
            return read_windows(context)
        elif command.name != "current":
            raise BrowserError(f"unknown command {command.name!r}")
        return read_page(page)


class BrowserError(RuntimeError):
    """Something went wrong driving the browser. Carries a readable message."""


#: How long to let a page move itself after it has loaded, before deciding where
#: we are. Short: it is a guard against a redirect, not a wait for a slow site.
SETTLE_MS = 3_000


def settle(page: Any) -> None:
    """Let a page finish going wherever it is going.

    **`goto` returning is not the same as having arrived.** An HTTP redirect is
    followed before it returns, but a `<meta http-equiv="refresh">` or a script
    that sets `location` runs *after* load — and a session that expired redirects
    exactly that way. Snapshotting without this reads the page we were sent to
    while the browser is already somewhere else, and hands `session._land` an
    origin that is no longer true. That is a boundary check answering about the
    wrong page, which is the one failure this whole package exists to prevent.

    Bounded and best-effort: a site that never goes idle is common, and the
    landing check runs again on every read, so the cost of giving up here is one
    stale read rather than a wrong decision.
    """
    with suppressed("letting a page finish redirecting"):
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)


#: What `act` may be asked to do. A closed set, checked before anything is
#: resolved: an unknown verb is a bug or an injection, and neither should reach
#: a page the user is signed in to.
ACTS = ("click", "type", "submit")


def locate(page: Any, handle: str, kind: str, text: str = "") -> None:
    """Resolve a handle to one element and do one thing to it.

    Split out for the same reason `parse_aria` is: it is the part that decides
    *what gets touched*, and it is worth being able to read on its own.

    `role␟name` goes to `get_by_role`, which is Playwright's own accessible-name
    lookup — the same pair a screen reader would use, and the pair the snapshot
    already showed the model. `exact=True` because "Send" and "Send later" are
    different buttons and a prefix match would pick whichever came first.
    """
    if kind not in ACTS:                          # pragma: no cover - guarded above
        raise BrowserError(f"unknown act {kind!r}")
    role, _, name = str(handle or "").partition("␟")
    if not role:
        raise BrowserError("that element has no role to find it by")
    target = (page.get_by_role(role, name=name, exact=True) if name
              else page.get_by_role(role))
    # `.first` rather than a strict match: a real page has two "Send" buttons
    # more often than not — one visible, one in a hidden menu — and a strict
    # locator raises where a person would simply use the one on screen.
    one = target.first
    if kind == "click":
        one.click()
    elif kind == "type":
        # `fill` rather than `press_sequentially`: it clears first, so re-running
        # a correction does not append to what is already in the box.
        one.fill(text)
    else:
        one.press("Enter")


def read_windows(context: Any) -> list[tuple[str, str]]:
    """`(url, title)` for every page open in a browser context, popups included.

    A separate function from `read_page` for the same reason that one is
    separate from the driver: it can be handed a real context by a test without
    a thread in the way.

    Each window is read on its own, and a window that cannot be read is skipped
    rather than sinking the list — a popup can be mid-navigation or already
    closed by the time we ask, and one unreadable window must not hide the
    refusal sitting in the next one. The address is taken before the title
    because the address is the part anything decides on.
    """
    found: list[tuple[str, str]] = []
    for page in list(getattr(context, "pages", None) or []):
        url, title = "", ""
        with suppressed("reading an open browser window"):
            url = page.url
            title = page.title()
        if url:
            found.append((str(url), str(title)))
    return found


def read_page(page: Any) -> tuple[str, str, list[Node]]:
    """`(url, title, nodes)` for an open Playwright page.

    Separate from the driver so a test can hand it a real page without a thread,
    and so the snapshot call lives next to the parser it feeds.
    """
    return page.url, page.title(), parse_aria(page.aria_snapshot())
