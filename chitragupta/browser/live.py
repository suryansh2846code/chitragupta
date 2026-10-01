"""The one browser the app is driving, and who is allowed to ask for it.

A module-level global, which it already was — this moves **where it lives**,
and the move is the point.

It used to sit in `agents/browse_tools.py`, which was right while an agent's
tools were the only thing that ever touched a page. Placing an order broke that
assumption in the one direction the layering cannot take: the order runs from
`actions.py`, after the user presses Confirm, and `actions` → `agents` →
`actions` is a cycle the moment `agents/permissions.py` reads the action
registry at import time — which it does, and must.

`/CLAUDE.md` names the three ways out of that and this is the first one: **move
the fact down to a leaf both sides read.** `browser/` imports nothing above
itself, so the tool half and the action half can each reach the same browser
without either reaching the other. `browse_tools.get_session` and `set_session`
stay exactly where every caller and every test already expects them, delegating
here — a fact that moved is not an interface that moved.
"""
from __future__ import annotations

from .session import Session

#: One browser per app, not per agent: two agents driving two Chromiums against
#: the same profile would fight over the cookie jar, and the profile is the
#: thing that makes a site "signed in".
_session: Session | None = None


def get_session() -> Session:
    """The shared browser session, started if it is not already running."""
    global _session
    if _session is None:
        from .chromium import open_session

        _session = open_session()
    return _session


def set_session(session: Session | None) -> None:
    """Replace the shared session. For tests, and for a reset after a crash."""
    global _session
    _session = session


def current() -> Session | None:
    """The session if one is already running, and **never one started here**.

    The difference from `get_session` is the whole reason this exists. Asking
    whether a page is open must not be the thing that launches a browser: a
    caller checking "is there anything to act on" would otherwise spend a
    150 MB download and a cold profile to be told no.
    """
    return _session
