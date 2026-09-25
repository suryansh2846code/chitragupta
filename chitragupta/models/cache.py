"""Short-lived caches for the expensive probes behind the model catalog.

Building the catalog shells out to vendor CLIs and pokes local daemons. Done
naively that costs seconds, and `/api/providers` and `/api/models/catalog` are
both requested every time the Models drawer opens — so the UI stalled.

These caches are deliberately short: long enough that one drawer open does the
work once, short enough that connecting an account is reflected immediately.
Every cache registers itself so `clear_provider_cache()` can flush the lot when
a credential changes.
"""
from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from typing import Any

from ..log import suppressed

_registry: list[Callable[[], None]] = []


def ttl_cached(seconds: float) -> Callable:
    """Cache a zero-argument probe for `seconds`, thread-safely."""

    def decorate(fn: Callable) -> Callable:
        state: dict[str, Any] = {"at": 0.0, "value": None, "set": False}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if args or kwargs:          # never cache a parameterised call
                return fn(*args, **kwargs)
            now = time.monotonic()
            with lock:
                if state["set"] and (now - state["at"]) < seconds:
                    return state["value"]
            value = fn()
            with lock:
                state.update(at=time.monotonic(), value=value, set=True)
            return value

        def clear() -> None:
            with lock:
                state.update(at=0.0, value=None, set=False)

        wrapper.cache_clear = clear     # type: ignore[attr-defined]
        _registry.append(clear)
        return wrapper

    return decorate


def clear_all() -> None:
    """Flush every probe cache — called when a credential changes."""
    for clear in _registry:
        clear()
    with suppressed("from ..config import forget_cached_secrets …"):
        from ..config import forget_cached_secrets

        forget_cached_secrets()


#: Things that hold state derived from a credential, and how to drop it.
#:
#: `clear_all` above flushes the probe caches declared in this module. This list
#: is for the caches that live elsewhere and cannot be — the memoized provider
#: instances in `registry` (they capture the API key at construction) and the
#: model catalog in `discovery` (it would keep serving the disconnected
#: snapshot). Each registers itself; nothing here imports them.
#:
#: The direction is the point. `chatgpt_auth` finishing a sign-in has to
#: invalidate both, and it was doing so by importing `registry` — an auth module
#: reaching the top of the package, which is one of the edges that made all of
#: `models/` a single fourteen-module knot. Now it says "the credentials
#: changed" to a leaf, and whoever cares has already said so.
_invalidators: list[Callable[[str | None], None]] = []


def on_credentials_change(fn: Callable[[str | None], None]) -> Callable[[str | None], None]:
    """Register `fn` to run whenever a credential changes. Returns `fn`."""
    _invalidators.append(fn)
    return fn


def credentials_changed(provider: str | None = None) -> None:
    """A credential was added, removed or refreshed — drop what derives from it.

    Safe to call before anything has registered: an empty list means nothing
    has been built yet, which is the same outcome as clearing it.
    """
    for invalidate in list(_invalidators):
        with suppressed("dropping cached state after a credential change"):
            invalidate(provider)
    clear_all()
