"""The only thing the HTTP layer may ask of the native window.

`docs/ARCHITECTURE.md` §3 rule 5: `desktop.py` and `hud.py` sit *beside* `api/`,
above everything else, and **`api/` must not import them**. Three routes did —
`providers.py` once and `workspace.py` twice — masked by being function-level
imports, which is why nothing caught it. `desktop.py` imports `api.app` to serve
the window, so the two were a cycle: recorded as deviation §6.1.

Inverted, the arrow points the right way. The desktop layer **attaches** itself
when it starts a window; `api/` asks this module and never learns what answered.

**Detached is not an error, it is the common case.** `chitragupta serve` runs in
a browser tab with no native window at all, and the honest answer to "is the
floating card available" is then *no*. The defaults below say exactly that, and
they say it in the same shape the attached surface uses — a status endpoint that
changes shape depending on how the app was launched is a status endpoint the
frontend has to branch on.

Deliberately three functions wide. This is a seam, not a second API: anything
that needs more of the window than "record what happened" and "say whether you
exist" belongs in a desktop-owned router, not here.
"""
from __future__ import annotations

from typing import Any, Protocol


class DesktopSurface(Protocol):
    """What `hud` provides. Named so the seam is checkable, not just documented."""

    SIGNIN_TIMEOUT_SECONDS: int

    def note(self, event: str, **fields: Any) -> None: ...

    def diagnostics(self) -> dict[str, Any]: ...


#: How long a sign-in may run before the card gives up, when no window layer has
#: attached. The attached surface's own value wins — this exists so a browser
#: tab still gets a number rather than a missing field.
DEFAULT_SIGNIN_TIMEOUT_SECONDS = 180

_surface: DesktopSurface | None = None


def attach(surface: DesktopSurface) -> None:
    """Called by the desktop layer once it owns a window."""
    global _surface
    _surface = surface


def detach() -> None:
    """Used by tests, and by a window that is going away."""
    global _surface
    _surface = None


def attached() -> bool:
    return _surface is not None


def note(event: str, **fields: Any) -> None:
    """Record something the page observed. A no-op with no window attached.

    Silence is correct here and nowhere else: these notes exist to explain why
    the floating card did or did not appear, and in a browser tab there is no
    card for them to be about.
    """
    if _surface is not None:
        _surface.note(event, **fields)


def diagnostics() -> dict[str, Any]:
    """Why the floating card is or is not available — for support, not flow.

    The detached shape is byte-identical to what `hud` reports before a window
    exists, because that is the same fact.
    """
    if _surface is not None:
        return _surface.diagnostics()
    return {
        "origin_set": False,
        "main_window": False,
        "hud_window_prepared": False,
        "available": False,
        "trail": [],
    }


def signin_timeout_seconds() -> int:
    return getattr(_surface, "SIGNIN_TIMEOUT_SECONDS",
                   DEFAULT_SIGNIN_TIMEOUT_SECONDS)
