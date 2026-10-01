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

# Re-exported, not redefined. `trouble` owns the vocabulary for a browser
# failure and is a leaf so that every layer above can read it; `BrowserError`
# moved down with it, so `from .driver import BrowserError` still means what it
# always did.
from .trouble import BrowserError

__all__ = ["ARRIVED", "BrowserError", "PlaywrightDriver", "parse_aria",
           "read_page", "settle"]

log = get_logger(__name__)

#: How long any single browser operation may take before we give up on it. A
#: page that never settles must not hold an agent's turn open indefinitely.
TIMEOUT_MS = 20_000

#: What counts as having arrived, for a navigation.
#:
#: **Not `load`.** `load` waits for every subresource — every avatar, font and
#: media thumbnail an app pulls in — and a messaging app on a cold cache spends
#: most of its first visit doing exactly that. Measured on WhatsApp Web with a
#: signed-in profile: `domcontentloaded` at 0.8s cold and 0.3s warm, `load` at
#: 5.7s cold on a fast link — and on a real user's connection it went past
#: `TIMEOUT_MS`, which is the bug this constant exists because of. The browser
#: was running fine the whole time; a navigation that could not finish within
#: twenty seconds was reported to them as *"the browser itself failed to
#: start"*.
#:
#: Nothing downstream needed `load`. `settle` is what guards against a page
#: moving itself after we arrive, `_land` re-checks the origin on every read,
#: and "the app has not drawn itself yet" is already owned by
#: `browse_tools._unready_hint` and `browse_wait` — which is the correct answer
#: to a loading screen and was unreachable while the navigation itself failed.
ARRIVED = "domcontentloaded"

#: How long past `TIMEOUT_MS` to wait for the browser thread to answer at all.
#:
#: The operation has its own budget; this is the margin on top of it, covering
#: the hand-off through the queue and the thread's own bookkeeping. Running out
#: of *this* means the thread is wedged rather than the page being slow — a
#: different failure, and `_call` says so rather than raising an empty
#: `queue.Empty` at the agent.
REPLY_GRACE_SECONDS = 10

#: Chromium switches we always launch with.
#:
#: The browser is shut down by killing it — that is what "anything we spawn, we
#: clean up" amounts to for a process with no other way out — and Chromium
#: records that as a crash. So the *next* launch greets the user with a bubble
#: reading "Chromium didn't shut down correctly. Restore pages?", which is our
#: own cleanup presented to them as a fault of theirs, offering to reopen the
#: tabs of somebody's last sign-in. Playwright does not pass this for a
#: persistent context, so we do.
#: `--disable-*-backgrounding` / `--disable-background-timer-throttling`: the
#: window lives minimised so it is not in the user's face, and a window Chromium
#: believes nobody is looking at is a window it stops painting. Without these the
#: in-app view would freeze the moment it was hidden — measured, not guessed.
LAUNCH_ARGS = (
    "--hide-crash-restore-bubble",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
)

#: What the browser calls itself.
#:
#: Playwright's Chromium reports `HeadlessChrome/<version>` even when it is not
#: headless, and it reports the Chromium build rather than a Chrome one. Sites
#: that gate on the browser read that and refuse: WhatsApp Web answers with
#: "WhatsApp works with Google Chrome 100+" and never renders the chat, so an
#: automation watching for a message sees a compatibility notice forever and
#: reports, correctly and uselessly, that it cannot tell.
#:
#: This is a **compatibility** string, not a disguise. The engine genuinely is
#: Chromium of that version, and this says so in the spelling the web has
#: standardised on — it does not hide that a browser is being driven, and
#: nothing else about the session is altered. A site that asks *"are you
#: automated"* still gets the true answer.
#:
#: Derived from the browser's own version rather than pinned, because a
#: hardcoded `Chrome/120` becomes "your browser is too old" a year later, which
#: is the same failure with a longer fuse.
def chrome_user_agent(reported: str) -> str:
    """`reported` with the marks that make a site refuse it removed, or "".

    Returns "" when there is nothing to change, so the caller can tell "already
    fine" from "fixed" and skip the override entirely on a browser that does
    not need one.
    """
    fixed = (str(reported or "")
             .replace("HeadlessChrome/", "Chrome/")
             .replace("Chromium/", "Chrome/"))
    return fixed if fixed != reported else ""


#: The size the page is rendered at, and therefore the coordinate space every
#: screenshot and every click shares. Fixed on purpose: the in-app view maps a
#: click on an image back to a point on the page, and a viewport that changed
#: with somebody's window would make that mapping a moving target.
VIEWPORT = {"width": 1280, "height": 800}

#: How the browser's own window is kept out of the way.
#:
#: Minimised rather than off-screen, and rather than headless. macOS clamps a
#: window back onto the display, so `--window-position=-32000,-32000` leaves a
#: sliver showing; headless changes the fingerprint, and the fingerprint is the
#: one thing about this browser that currently works for signing in. Minimised
#: keeps a real headful Chromium, keeps painting (with the flags above), and
#: leaves the window one click from being brought back — which is what
#: `docs/BROWSER.md` means by MFA needing a window a person can reach.
HIDDEN = "minimized"
SHOWN = "normal"

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

    # ── the in-app view ─────────────────────────────────────────────────
    #
    # The browser's own window is minimised, so the page has to arrive
    # somewhere a person can see it. These three are what the Browser screen is
    # built from: a frame, a way to point at it, and a way to get the real
    # window back if the page needs something only a real window can do.
    #
    # **Nothing here is an agent's.** They are reached from `api/routes/browser`
    # by a user pressing something, the same way signing in is. An agent that
    # could click by coordinate would have walked straight around refs, the
    # origin check and everything else in this package.
    def frame(self, quality: int = 55) -> tuple[bytes, str, str]:
        """A JPEG of the page right now, with where it is and what it is called.

        JPEG rather than PNG: this is a photograph of a rendered page, several
        times a second, and a lossless one costs five times the bytes to show
        the same thing.
        """
        return self._call("frame", quality)

    def point(self, kind: str, x: float = 0, y: float = 0,
              text: str = "") -> None:
        """A click, a scroll, some typing, or a named key — from the user."""
        self._call("point", kind, x, y, text)

    def window(self, visible: bool) -> None:
        """Bring the real browser window back, or put it away again."""
        self._call("window", bool(visible))

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
        try:
            ok, payload = reply.get(
                timeout=TIMEOUT_MS / 1000 + REPLY_GRACE_SECONDS)
        except queue.Empty:
            # The browser thread is wedged on something that outlasted its own
            # budget. `queue.Empty` carries no message at all and is not a
            # `BrowserError`, so left alone it walks straight past
            # `browse_tools._browser_errors` to the catch-all in
            # `tools.run_tool` — and the model is handed "Tool browse_open
            # failed:" with nothing after the colon. Said as a timeout because
            # that is what it is, and because that is the word
            # `browse_tools._TIMED_OUT` reads to tell the agent to try again.
            raise BrowserError(
                f"Timeout: the browser did not answer the {name} it was "
                "given.") from None
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

    def _present_as_chrome(self, page: Any) -> None:  # pragma: no cover - needs a browser
        """Say `Chrome` where the engine says `Chromium` or `HeadlessChrome`.

        Over the CDP session that is already open, rather than by launching a
        second browser to read a string off it.

        See `chrome_user_agent` for why this is compatibility rather than
        disguise. Best-effort by design: a browser that refuses the override is
        a browser that works slightly worse on three sites, not one that fails
        to start.
        """
        if self._cdp is None:
            return
        with suppressed("presenting the browser as Chrome"):
            reported = page.evaluate("navigator.userAgent")
            wanted = chrome_user_agent(reported)
            if not wanted:
                return
            self._cdp.send("Network.setUserAgentOverride",
                           {"userAgent": wanted})
            log.info("browser presents as %s", wanted.split(") ")[-1])

    def _run(self) -> None:                       # pragma: no cover - needs a browser
        from playwright.sync_api import sync_playwright

        context = None
        try:
            with sync_playwright() as pw:
                launch: dict[str, Any] = {"headless": self._headless,
                                          "args": list(LAUNCH_ARGS),
                                          "viewport": dict(VIEWPORT)}
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
                # One CDP session for the life of the browser. It is how the
                # window is moved out of the way and brought back, which
                # Playwright has no API for.
                with suppressed("opening a CDP session for the window"):
                    self._cdp = context.new_cdp_session(page)
                self._present_as_chrome(page)
                self._set_window(HIDDEN)
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

    def _set_window(self, state: str) -> None:    # pragma: no cover - needs a browser
        """Put the OS window away, or bring it back.

        Best-effort on purpose. A browser whose window will not move is still a
        browser that reads pages perfectly well, and failing the whole start
        over a cosmetic bounds call would trade the feature for the decoration.
        """
        cdp = getattr(self, "_cdp", None)
        if cdp is None:
            return
        with suppressed("moving the browser's own window out of the way"):
            window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
            bounds: dict[str, Any] = {"windowState": state}
            if state == SHOWN:
                # Restoring needs a size as well: a window that comes back at
                # whatever it was minimised from can come back at nothing.
                bounds.update(left=60, top=60, width=VIEWPORT["width"],
                              height=VIEWPORT["height"] + 90)
            cdp.send("Browser.setWindowBounds",
                     {"windowId": window_id, "bounds": bounds})

    def _serve(self, page: Any) -> None:          # pragma: no cover - needs a browser
        while True:
            command = self._commands.get()
            if command is None:
                return
            try:
                command.reply.put((True, self._do(page, command)))
            except Exception as exc:
                # Summarised, not truncated — see `summarise`. Cutting this at
                # 300 characters removed the reason and kept the retry log.
                command.reply.put((False, summarise(exc)))

    def _do(self, page: Any, command: _Command) -> Any:  # pragma: no cover
        if command.name == "goto":
            page.goto(command.args[0], wait_until=ARRIVED)
            settle(page)
        elif command.name == "back":
            page.go_back(wait_until=ARRIVED)
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
        elif command.name == "frame":
            quality = max(20, min(int(command.args[0] or 55), 90))
            return (page.screenshot(type="jpeg", quality=quality),
                    page.url, page.title())
        elif command.name == "point":
            kind, x, y, text = command.args
            if kind == "click":
                page.mouse.click(float(x), float(y))
            elif kind == "move":
                page.mouse.move(float(x), float(y))
            elif kind == "wheel":
                page.mouse.wheel(float(x), float(y))
            elif kind == "text":
                page.keyboard.type(str(text))
            elif kind == "key":
                page.keyboard.press(str(text))
            else:
                raise BrowserError(f"unknown input {kind!r}")
            return None
        elif command.name == "window":
            self._set_window(SHOWN if command.args[0] else HIDDEN)
            return None
        elif command.name == "windows":
            context = getattr(self, "_context", None)
            if context is None:
                raise BrowserError("the browser is not holding a profile")
            return read_windows(context)
        elif command.name != "current":
            raise BrowserError(f"unknown command {command.name!r}")
        return read_page(page)




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

    It carries that alone now. Navigation returns at `ARRIVED` rather than at
    `load`, so this is also the gap between the document being parsed and the
    app having fetched anything — which is the same wait, measured from a
    slightly earlier point, and the reason that change cost nothing.

    Bounded and best-effort: a site that never goes idle is common, and the
    landing check runs again on every read, so the cost of giving up here is one
    stale read rather than a wrong decision.
    """
    with suppressed("letting a page finish redirecting"):
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)


#: What `act` may be asked to do. A closed set, checked before anything is
#: resolved: an unknown verb is a bug or an injection, and neither should reach
#: a page the user is signed in to.
#: Keys an agent may send by name, and nothing else.
#:
#: A closed list because `press` takes a *string that Chromium interprets*, and
#: the unbounded version of that is a keyboard-shaped hole in everything else
#: this package does: `Control+A` then `Control+V` is a paste, `F12` is devtools,
#: and a modifier chord is how you reach a browser menu. These are the keys that
#: navigate and dismiss — what a person uses to get around a page they are not
#: typing into — and nothing here composes with a modifier.
NAMED_KEYS = frozenset({
    "Enter", "Escape", "Tab", "Backspace", "Delete",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "Home", "End", "PageUp", "PageDown",
})


def _check_key(text: str) -> None:
    """Refuse a key that is not on the list, before anything is resolved.

    **Before**, not during. Checking inside the act would mean the element had
    already been located on a page the user is signed in to by the time we
    decided we were not going to touch it — harmless today and exactly the kind
    of ordering that stops being harmless when somebody adds a hover or a focus
    to the resolution step.
    """
    key = str(text or "").strip()
    if key not in NAMED_KEYS:
        raise BrowserError(
            f"{key!r} is not a key that may be sent. Allowed: "
            + ", ".join(sorted(NAMED_KEYS)))


#: What an act requires of its text, checked before the page is touched at all.
#: A dict rather than a branch for `ACTS`' reason: the verb and its rules stay
#: together, so a verb cannot acquire one and leave the other behind.
CHECKS: dict[str, Any] = {"press": _check_key}


#: Every verb that may be done to an element, and how each is performed.
#:
#: **A table rather than an `if` chain, because adding one used to mean editing
#: five places** — the tuple of names, the branch that ran it, the tool wrapper,
#: the unattended floor and the test that enumerates write tools. Four of those
#: are still real (a verb needs a tool and a tool needs a floor), but the two
#: that could silently disagree — the list of what is allowed and the code that
#: does it — are now one thing that cannot.
#:
#: Each takes the resolved locator and the approved text. Nothing composes, so
#: there is no verb an injected page can assemble out of two others.
ACTS: dict[str, Any] = {
    "click": lambda one, text: one.click(),
    # `fill` rather than `press_sequentially`: it clears first, so re-running a
    # correction does not append to what is already in the box.
    "type": lambda one, text: one.fill(text),
    "submit": lambda one, text: one.press("Enter"),
    # The form control a page tool could not touch at all. A dropdown is matched
    # by its visible label, for the same reason everything else here is matched
    # by its accessible name: that is the string the model was shown and the
    # user could check.
    "select": lambda one, text: one.select_option(label=str(text)),
    # Dismissing a dialog, moving through a list, accepting an autocomplete.
    # The reason `NAMED_KEYS` exists rather than a free string.
    "press": lambda one, text: one.press(str(text).strip()),
    # Reveal what a virtualised list has not drawn yet. A chat list, a mail list
    # and a feed all render roughly a screenful, so without this an agent can
    # see thirty rows and genuinely cannot reach the thirty-first.
    "reveal": lambda one, text: one.scroll_into_view_if_needed(),
}


#: The acts that change what a page or an account does, as opposed to what this
#: side of the glass can see.
#:
#: **`reveal` is deliberately not one.** Scrolling an element into view sends
#: nothing, presses nothing and alters no state the site can observe beyond a
#: list deciding to draw more of itself — which is reading. Gating it as a write
#: would mean a user had to allow *changes* on a site before an agent could read
#: past the first screenful of it, and the thing they were agreeing to would not
#: be the thing they were being asked about.
#:
#: Everything above derives from this rather than restating it: the tool names,
#: the origin check in `Session.act`, and the unattended floor in
#: `agents/permissions.py`. A verb added to `ACTS` is therefore gated by
#: default, which is the direction this has to fail in.
CHANGING_ACTS = frozenset(ACTS) - {"reveal"}


def locate(page: Any, handle: str, kind: str, text: str = "") -> None:
    """Resolve a handle to one element and do one thing to it.

    Split out for the same reason `parse_aria` is: it is the part that decides
    *what gets touched*, and it is worth being able to read on its own.

    `role␟name` goes to `get_by_role`, which is Playwright's own accessible-name
    lookup — the same pair a screen reader would use, and the pair the snapshot
    already showed the model. `exact=True` because "Send" and "Send later" are
    different buttons and a prefix match would pick whichever came first.
    """
    doing = ACTS.get(kind)
    if doing is None:                             # pragma: no cover - guarded above
        raise BrowserError(f"unknown act {kind!r}")
    checking = CHECKS.get(kind)
    if checking is not None:
        checking(text)
    role, _, name = str(handle or "").partition("␟")
    if not role:
        raise BrowserError("that element has no role to find it by")
    target = (page.get_by_role(role, name=name, exact=True) if name
              else page.get_by_role(role))
    # `.first` rather than a strict match: a real page has two "Send" buttons
    # more often than not — one visible, one in a hidden menu — and a strict
    # locator raises where a person would simply use the one on screen.
    doing(target.first, text)


#: Phrases Playwright writes into a call log to say *why* something failed.
#:
#: These are the only part of that log worth keeping. The rest is a retry
#: trace — "waiting for element to be visible, enabled and stable" four times
#: over — which is both useless to a model and expensive to send it.
_WHY = (
    # Something is on top of it. Overwhelmingly a modal, and the reason this
    # list exists: it is the single most actionable thing a page can tell us.
    "intercepts pointer events",
    "element is not visible",
    "element is not enabled",
    "element is not stable",
    "element is outside of the viewport",
    "not attached to the dom",
    "element is not editable",
)


def summarise(exc: BaseException, limit: int = 200) -> str:
    """A browser failure as one short line that still says why.

    **`str(exc)[:300]` threw the answer away and kept the noise.** Measured on a
    real blocked click: Playwright's message is 520 characters, the words
    `intercepts pointer events` begin at character 495, and the first 300 are
    its retry log. So the one fact that identifies the failure never reached
    `trouble.classify`, every blocked click looked like a generic timeout, and
    an agent told "open the same address again" did exactly that — which is the
    loop this function exists to end.

    Keeps the first line, which carries the API that failed and its budget, plus
    the reason if Playwright named one. Around eighty characters instead of
    three hundred, and the eighty are the useful ones.
    """
    text = str(exc)
    head = text.split("\n", 1)[0].strip() or text.strip()
    low = text.lower()
    for phrase in _WHY:
        if phrase in low:
            return f"{head} — {phrase}"[:limit]
    return head[:limit]


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
