"""Who else is on the team — asked for, never assembled here.

Three places need the user's other agents and none of them owns the list:

* `prompt` tells an agent who it can hand work to (an agent told nothing about
  its team assumes there is one and claims to have asked it);
* `delegation` checks that a named agent exists before passing a question on;
* the `ask_agent` tool description names the roster so the model can choose.

The list is assembled by `presets`, which reads the shipped templates in
`library` and the user's own in `custom`. All three readers sit *below* that —
`presets` builds an `Agent`, whose prompt `prompt` renders — so importing it
from any of them pointed the arrow backwards. Together those edges held
thirteen modules of this package in one strongly-connected component.

**Unregistered means an empty roster, not an error.** That is the honest answer
before `presets` has been imported: there is no team yet. Every caller already
treats "nobody else" as a real state, because a first-run install genuinely has
one agent.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_supplier: Callable[[], list[Any]] | None = None
_lookup: Callable[[str], Any] | None = None


def set_supplier(all_agents: Callable[[], list[Any]],
                 get_one: Callable[[str], Any]) -> None:
    """Called by `presets` at import — it is what owns the roster."""
    global _supplier, _lookup
    _supplier, _lookup = all_agents, get_one


def all_agents() -> list[Any]:
    """Every agent on the user's roster, or [] if `presets` is not loaded."""
    return list(_supplier()) if _supplier is not None else []


def get_agent(agent_id: str) -> Any:
    """One agent by id. Raises `KeyError` for an unknown id, as `presets` does —
    including when nothing is registered, because an agent that does not exist
    and an agent nobody can look up are the same answer to the caller."""
    if _lookup is None:
        raise KeyError(agent_id)
    return _lookup(agent_id)
