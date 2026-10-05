"""Whose turn is running, for the tools that must gate themselves.

This is one `ContextVar` and three functions, and it lives alone because two
modules need it and neither may import the other.

It started in `connector_grants`, which is where the first tool needed it —
`list_chats` reaches whichever app the model named, so the gate cannot see the
connector by tool name and the tool has to ask the question itself. Folder
scopes need the same fact for the same reason: `file_tools` has to know which
agent is reading before it can answer *which folders*. But `connector_grants`
reaches `roster`, `roster` reaches `library`, and `library` reaches
`file_tools` — so the import that looked like one line would have closed a
four-module cycle, and `tests/test_import_layering.py` counts a lazy import as
a real edge precisely so that moving it inside a function cannot hide it.

So the fact moves **down** to a leaf both sides read, which is the first of the
three shapes the root `CLAUDE.md` names. It imports `log` and nothing else, and
it must stay that way. `connector_grants` re-exports `acting_as`/`stop_acting`/
`acting` so every existing caller is unchanged.
"""
from __future__ import annotations

import contextvars

from ..log import suppressed

#: The agent whose turn is currently running.
#:
#: A ContextVar rather than an attribute on the runner: tool calls run in a
#: thread pool under one `copy_context()` per call, so anything hung off the
#: runner would be invisible to the worker that actually executes the tool.
_ACTING: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chitragupta_acting_agent", default="")


def acting_as(agent_id: str):
    """Mark whose turn is running. Returns a token for `stop_acting`."""
    return _ACTING.set(str(agent_id or ""))


def stop_acting(token) -> None:
    with suppressed("clearing the acting agent"):
        _ACTING.reset(token)


def acting() -> str:
    """The agent currently running, or "" when nobody is.

    Empty is a real answer, not a missing one: an approved action executed from
    the queue runs long after the turn that proposed it, and a caller that
    reads this has to say what it does with "we do not know".
    """
    return _ACTING.get()
