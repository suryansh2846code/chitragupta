"""The way back into the turn loop, for the parts of this package that are
inside it.

`runtime.run_turn` builds an agent's tools at module import, and two things
under those tools need to start a turn of their own:

* **delegation** — `ask_agent` runs another agent's whole turn;
* **outcomes** — an action the user approved came back a failure, and the agent
  gets one chance to react to it.

Both are deliberate re-entry. The *recursion* is the design; the **import** was
not, and it was what held sixteen modules of `agents/` in a single
strongly-connected component — nothing here could be read or tested without the
other fifteen, and `ruff` saw none of it because every edge sat inside a
function body.

So the loop registers itself here at import, and its callers ask this module.
One line, no state, no policy: the guards that decide whether a delegated turn
may happen at all stay in `delegation.py`, where they were.

**Unregistered is not an error.** It means `runtime` has not been imported, and
there is then no loop to re-enter. `run()` returns `None`, which both callers
already handle — they run under `suppressed()` because a follow-up turn failing
must never take down the action that succeeded.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_runner: Callable[..., Any] | None = None


def set_runner(fn: Callable[..., Any]) -> None:
    """Called by `runtime` at import — the moment a loop exists at all."""
    global _runner
    _runner = fn


def available() -> bool:
    return _runner is not None


def run(agent_id: str, text: str, **kwargs: Any) -> Any:
    """Run one turn, or `None` if the loop has not been loaded."""
    if _runner is None:
        return None
    return _runner(agent_id, text, **kwargs)
