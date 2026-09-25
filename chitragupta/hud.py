"""The floating sign-in window.

A browser sign-in takes the user out of Chitragupta, so the status has to follow
them. This is a small always-on-top, frameless window that sits above the
browser while they authorise, then tells them it worked and offers a way back.

It only exists in the desktop app. `chitragupta serve` has no window to create,
so every call here is a no-op and the in-app card handles it instead — the
product must work in both modes.

**The window is opened from the page, not from the API.** Under
`chitragupta app --dev` the backend runs as a separate uvicorn process, which has
no handle on the webview at all — a backend-initiated window silently did
nothing there. The frontend always runs inside the webview, so it calls
`window.pywebview.api.open_signin_hud(...)` and this module does the rest.

**The window is created once, hidden, on the main thread** and then shown and
hidden as needed. Creating and destroying windows from the js_api worker thread
is the kind of cross-thread Cocoa work that is easy to get wrong; show/hide of a
pre-made window is a plain, thread-safe call.
"""
from __future__ import annotations

import logging
import threading
import urllib.parse

logger = logging.getLogger(__name__)

# How long we wait for a browser sign-in before giving up. Deliberately generous:
# switching accounts, typing a password and clearing 2FA regularly takes longer
# than two minutes, and a HUD that gives up while the user is mid-flow reads as
# the app being broken.
SIGNIN_TIMEOUT_SECONDS = 180

# Starting size. The card measures its own copy and calls `fit`, so the height
# here only has to be big enough not to clip before that lands.
_WINDOW_SIZE = (348, 250)
_MIN_HEIGHT = 150
_MAX_HEIGHT = 420
_SCREEN_MARGIN = 18          # gap from the screen edge, like a system notification

# Set by desktop.run_app; absent when running as a plain server.
_origin: str | None = None
_main_window = None
_hud_window = None
# What the last sign-in click actually did. Without this, "the card showed up in
# the wrong place" is unanswerable: the branch that never reached the floating
# card and the branch that reached it and failed look identical from outside.
_trail: list[dict] = []
_lock = threading.Lock()


def configure(origin: str, main_window) -> None:
    """Called by the desktop app once its window exists."""
    global _origin, _main_window
    _origin, _main_window = origin, main_window


def available() -> bool:
    return _origin is not None and _main_window is not None


def _corner_position(width: int, height: int) -> tuple[int | None, int | None]:
    """Top-right of the primary screen, where system notifications appear.

    Only used to place the window at creation, before AppKit is reachable;
    `_place_and_raise` re-positions it properly every time it is shown.
    """
    try:
        import webview

        screen = webview.screens[0]
        return (screen.x + screen.width - width - _SCREEN_MARGIN,
                screen.y + _SCREEN_MARGIN)
    except Exception:
        return None, None


def _native_window():
    """The NSWindow behind the pywebview window, or None when there isn't one.

    `chitragupta serve` and the test suite have no Cocoa window at all, so every
    caller must treat None as "do the portable thing instead".
    """
    if _hud_window is None:
        return None
    try:
        from webview.platforms.cocoa import BrowserView

        instance = BrowserView.instances.get(_hud_window.uid)
        return instance.window if instance is not None else None
    except Exception:
        return None


def _native_webview():
    """The WKWebView inside the card's window, or None when there isn't one."""
    if _hud_window is None:
        return None
    try:
        from webview.platforms.cocoa import BrowserView

        instance = BrowserView.instances.get(_hud_window.uid)
        return instance.webview if instance is not None else None
    except Exception:
        return None


def _clear_webview_backdrop(webview_) -> None:
    """Stop the web view painting an opaque sheet behind the page.

    A transparent window is not enough. WKWebView fills its bounds with
    `underPageBackgroundColor` — opaque white by default — which is what showed
    below the card as a white block. pywebview's transparency support predates
    that property, so it sets `drawsBackground` and stops there.
    """
    import AppKit

    try:
        webview_.setValue_forKey_(False, "drawsBackground")
    except Exception:
        logger.debug("web view keeps its background", exc_info=True)
    try:
        webview_.setUnderPageBackgroundColor_(AppKit.NSColor.clearColor())
    except Exception:
        # macOS 12+ only; older systems have nothing to clear.
        logger.debug("no underPageBackgroundColor on this system", exc_info=True)


def _apply_float_behaviour(nswin) -> None:
    """Make the card a floating panel rather than an ordinary window.

    Must run on the main thread. Called every time the card is shown: these are
    idempotent setters, and re-applying them is cheaper than reasoning about
    whether anything downstream reset them.
    """
    import AppKit

    # NSFloatingWindowLevel sits above every application's normal windows while
    # staying below the menu bar and system UI. pywebview's `on_top` gives us
    # NSStatusWindowLevel instead, which also covers the menu bar — more than a
    # sign-in card has any business claiming.
    nswin.setLevel_(AppKit.NSFloatingWindowLevel)

    # This is what was actually broken. With the default collection behaviour a
    # window belongs to the Space it was born on: switching to a browser on
    # another Space — and a full-screen browser gets a Space of its own — left
    # the card behind, still visible and still on top, just not where the user
    # was. CanJoinAllSpaces makes it follow them; FullScreenAuxiliary lets it
    # sit over a full-screen app rather than being hidden by it.
    #
    # Stationary is deliberately NOT set: it pins a window to screen coordinates
    # during Space transitions, which is for wallpaper-like overlays, and it is
    # redundant once the window joins every Space.
    nswin.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )

    # A sign-in card exists precisely for the moments this app is NOT active.
    nswin.setHidesOnDeactivate_(False)

    # `transparent=True` turns the native shadow off. Turn it back on: with a
    # transparent window macOS derives the shadow from the opaque content, so it
    # hugs the card's rounded corners — a real window shadow rather than a CSS
    # one squeezed into a gutter. It is cached, so it has to be invalidated
    # whenever the card's shape could have changed.
    nswin.setHasShadow_(True)
    nswin.invalidateShadow()


def _visible_corner(width: int, height: int):
    """Top-right of the screen the user is on, in Cocoa (bottom-left) coords.

    visibleFrame, not frame, so the menu bar and Dock are respected; the screen
    under the pointer, not screen zero, so the card appears where the user is
    working on a multi-monitor desk. Everything here is in points, so Retina and
    mixed-scale arrangements need no special handling.
    """
    import AppKit

    mouse = AppKit.NSEvent.mouseLocation()
    screen = None
    for candidate in AppKit.NSScreen.screens():
        if AppKit.NSPointInRect(mouse, candidate.frame()):
            screen = candidate
            break
    if screen is None:
        screen = AppKit.NSScreen.mainScreen()

    area = screen.visibleFrame()
    return AppKit.NSMakePoint(
        area.origin.x + area.size.width - width - _SCREEN_MARGIN,
        area.origin.y + area.size.height - height - _SCREEN_MARGIN,
    )


def _place_and_raise(nswin, width: int, height: int) -> None:
    """Show the card without activating Chitragupta.

    `show()` in the pywebview backend calls makeKeyAndOrderFront_ followed by
    activateIgnoringOtherApps_, which yanks keyboard focus out of the browser
    the user is signing in to — every time the card updates.
    orderFrontRegardless puts the window on screen and leaves the active
    application alone; clicking the card still brings it forward normally.
    """
    from PyObjCTools import AppHelper

    AppHelper.callAfter(_raise_now, nswin, width, height)


def _resize_now(nswin, height: int) -> None:
    """Main-thread half of `_Bridge.fit`."""
    import AppKit

    try:
        frame = nswin.frame()
        if abs(frame.size.height - height) < 1:
            return                        # already the right height
        top = frame.origin.y + frame.size.height      # Cocoa y grows upward
        nswin.setFrame_display_(
            AppKit.NSMakeRect(frame.origin.x, top - height,
                              frame.size.width, height), True)
        nswin.invalidateShadow()          # the shape changed
    except Exception:
        logger.warning("could not resize the sign-in window", exc_info=True)


def _raise_now(nswin, width: int, height: int) -> None:
    """The main-thread half of `_place_and_raise`, split out so it can be tested
    against a recording stub — the point of this code is which AppKit calls it
    does and does not make."""
    try:
        _apply_float_behaviour(nswin)
        webview_ = _native_webview()
        if webview_ is not None:
            _clear_webview_backdrop(webview_)
        nswin.setFrameOrigin_(_visible_corner(width, height))
        nswin.orderFrontRegardless()
    except Exception:
        logger.warning("could not raise the sign-in window", exc_info=True)


class _Bridge:
    """Exposed to the HUD page as `window.pywebview.api`."""

    def fit(self, height: float) -> bool:
        """Trim the window to the height the card actually needs.

        The copy differs per provider and per state, so a fixed height either
        leaves a hole under the text or clips it. The card measures itself and
        asks for that height; the window keeps its **top edge** so it stays
        pinned to the corner and grows downward instead of drifting.
        """
        nswin = _native_window()
        if nswin is None:
            return False
        try:
            wanted = max(_MIN_HEIGHT, min(_MAX_HEIGHT, round(float(height))))
        except (TypeError, ValueError):
            return False
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_resize_now, nswin, wanted)
        return True

    def close_hud(self) -> None:
        close()

    def cancel_signin(self, provider: str) -> bool:
        """Abandon an in-progress sign-in and put the card away."""
        close()
        try:
            from .models.auth_flows import get_flow

            cancel = getattr(get_flow(provider), "cancel", None)
            if cancel is not None:
                cancel()
            return True
        except Exception:
            logger.debug("could not cancel %s sign-in", provider, exc_info=True)
            return False

    def focus_main(self) -> None:
        close()
        try:
            if _main_window is not None:
                _main_window.show()
                _main_window.on_top = True      # bring it forward…
                _main_window.on_top = False     # …without pinning it there
        except Exception:
            logger.debug("could not focus the main window", exc_info=True)


def prepare(create_window) -> None:
    """Build the (hidden) window up front, on the main thread."""
    global _hud_window
    width, height = _WINDOW_SIZE
    x, y = _corner_position(width, height)
    try:
        _hud_window = create_window(
            "Connect", "about:blank",
            width=width, height=height, x=x, y=y,
            hidden=True, frameless=True, easy_drag=True, on_top=True,
            # Without this the window is an opaque white rectangle behind the
            # rounded card, which reads as a pale border around it. Transparent
            # also turns off the native shadow, so the card's own box-shadow is
            # what the body padding leaves room for.
            transparent=True,
            resizable=False, shadow=True, focus=True, js_api=_Bridge(),
        )
    except Exception:
        logger.warning("could not prepare the sign-in window", exc_info=True)
        _hud_window = None


def note(event: str, **fields) -> None:
    """Record a step of the sign-in path. Bounded; support signal, not a log."""
    _trail.append({"event": event, **fields})
    del _trail[:-12]


def open_signin(provider: str, brand: str, auth_url: str = "") -> bool:
    """Show the floating card for an in-progress sign-in. False if unavailable."""
    if not available() or _hud_window is None:
        note("open_signin", provider=provider, ok=False,
             reason="no native window in this process")
        return False

    query = urllib.parse.urlencode({
        "provider": provider, "brand": brand,
        "auth_url": auth_url, "limit": SIGNIN_TIMEOUT_SECONDS,
    })
    with _lock:
        try:
            # The same window every time — reloading it is what "update the
            # existing card" means here, and it is why a second card cannot
            # appear no matter how often a sign-in is started.
            _hud_window.load_url(f"{_origin}/signin-hud?{query}")
            width, height = _WINDOW_SIZE
            nswin = _native_window()
            if nswin is not None:
                _place_and_raise(nswin, width, height)
            else:
                # No Cocoa window (plain server, tests). Best effort.
                x, y = _corner_position(width, height)
                if x is not None:
                    _hud_window.move(x, y)
                _hud_window.show()
            note("open_signin", provider=provider, ok=True)
            return True
        except Exception:
            logger.warning("could not show the sign-in window", exc_info=True)
            note("open_signin", provider=provider, ok=False, reason="show failed")
            return False


def diagnostics() -> dict:
    """Why the floating card is or isn't available — for support, not for flow
    control. Silence here is what made this hard to diagnose."""
    return {
        "origin_set": _origin is not None,
        "main_window": _main_window is not None,
        "hud_window_prepared": _hud_window is not None,
        "available": available(),
        "trail": list(_trail),
    }


def close() -> None:
    """Hide the card. The window is reused, never destroyed — tearing one down
    from a worker thread is exactly the cross-thread work we are avoiding."""
    if _hud_window is None:
        return
    try:
        _hud_window.hide()
        _hud_window.load_url("about:blank")     # stop the page polling
    except Exception:
        logger.debug("sign-in window already hidden", exc_info=True)


def _attach_to_api() -> None:
    """Hand this module to the HTTP layer as its desktop surface.

    Here rather than in `desktop.py` so the wiring sits beside the thing being
    wired. `api/` must not import this module (`docs/ARCHITECTURE.md` §3 rule 5)
    — importing in the other direction is fine, and is what makes the seam a
    seam. Detached, the bridge answers "no native window", which is the truth
    under `chitragupta serve`.
    """
    import sys

    from .api import desktop_bridge
    desktop_bridge.attach(sys.modules[__name__])
