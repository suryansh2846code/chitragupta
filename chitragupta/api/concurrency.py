"""Keeping slow work from starving the window.

Every route handler in this app is a plain `def`, so FastAPI runs it in a
shared worker-thread pool. That is the right default — the handlers block, and
blocking the event loop would be worse — but it makes the pool a single shared
resource with no priority in it, and the app's own UI is served by the same
server. When enough slow handlers are in flight, the cheap ones the interface
polls cannot get a thread, and the whole window stops responding.

This is not hypothetical here. `/auth/status` is polled every two seconds during
sign-in; when that poll called `/refresh` (1.6–6.4s per provider), requests
queued faster than they could finish, filled the pool, and froze the app. The
fix at the time was to poll something cheaper — correct, and it left the cause
standing: any future handler that blocks for seconds can do it again.

So slow work is given its own bounded lane. A capacity limiter caps how many of
those may run at once, and the endpoint becomes `async` — which means it holds
*no* worker thread while it waits, instead of holding one and asking for
another. Whatever else is happening, the threads the UI needs stay available.

Two lanes, because their costs are not alike:

* `MODEL_CALLS` — a turn against a model provider, which can run for a minute.
* `PROBES` — shelling out to a vendor CLI, reading the Keychain, asking a
  provider what models an account can run. Seconds, and far more frequent.

One lane would mean a single chat turn could hold up every sign-in probe behind
it, which is the failure this module exists to prevent, one level down.
"""
from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import TypeVar

import anyio
import anyio.to_thread

T = TypeVar("T")

#: Concurrent LLM turns. The window uses this: an agent is its own identity with
#: its own turn, so a user can set several of them working and keep moving. The
#: scheduler and routines chat alongside them.
MODEL_CALLS = anyio.CapacityLimiter(8)

#: Concurrent provider probes. Deliberately small — these spawn processes, and
#: the point is that they cannot crowd out anything else.
PROBES = anyio.CapacityLimiter(6)

#: Threads FastAPI may use for ordinary handlers. The framework default is 40,
#: shared with everything; raising it costs little and buys headroom for the
#: cheap routes that are polled.
DEFAULT_THREAD_LIMIT = 64


async def _apply_thread_limit() -> None:
    """Widen the shared pool. Must run inside the loop that owns the limiter."""
    anyio.to_thread.current_default_thread_limiter().total_tokens = DEFAULT_THREAD_LIMIT


def offloaded(limiter: anyio.CapacityLimiter) -> Callable[[Callable[..., T]],
                                                          Callable[..., Awaitable[T]]]:
    """Run this route's body in `limiter`'s lane instead of the shared pool.

    The wrapper is `async`, so FastAPI awaits it on the event loop and the
    handler occupies a worker thread only while it is actually running — never
    while it is queued. `functools.wraps` keeps `__wrapped__` pointing at the
    original, which is how FastAPI still sees the real signature and builds the
    same path/query/body parameters it would have.
    """
    def decorate(fn: Callable[..., T]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def endpoint(*args: object, **kwargs: object) -> T:
            return await anyio.to_thread.run_sync(
                functools.partial(fn, *args, **kwargs), limiter=limiter)
        return endpoint
    return decorate


#: Sugar, so a route reads as what it is rather than as which limiter it names.
calls_a_model = offloaded(MODEL_CALLS)
probes_a_provider = offloaded(PROBES)
