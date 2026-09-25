"""Resolving an agent by id — from the library, or from the user's own.

This file used to *be* the roster: four hardcoded agents, all present, none of
them chosen. That moved to `library.py`, which offers templates and remembers
which ones this person took on. What is left here is the lookup every other
module already used, so nothing had to learn a new way to ask.

`PRESETS` is kept as the whole library keyed by id. It was the name for "the
agents that ship", and it still means that — it is simply no longer the same as
"the agents this user has".
"""
from __future__ import annotations

import dataclasses

from . import roster
from .agent import Agent
from .agent_models import get_agent_model
from .library import BY_ID, rostered_agents

#: Every agent Chitragupta ships, by id. Not the same as the user's roster.
PRESETS: dict[str, Agent] = {tid: t.to_agent() for tid, t in BY_ID.items()}


def _with_user_edits(agent: Agent) -> Agent:
    """The agent as the user has it: the shipped definition plus their changes.

    One seam, because both `list_agents()` and `get_agent()` come through here
    — an override applied in only one of them is an agent whose tools differ
    depending on which call site asked, which is exactly the kind of bug that
    shows up as "it works in the sidebar but not in the turn".
    """
    from .tool_overrides import get_tool_overrides

    p, m = get_agent_model(agent.id)
    if p:
        agent = dataclasses.replace(agent, model_provider=p, model_name=m)
    else:
        agent = dataclasses.replace(agent)

    # `None` is untouched; `[]` is an agent the user deliberately stripped, and
    # falling back to the default there would silently undo that.
    tools = get_tool_overrides().get(agent.id)
    if tools is not None:
        agent = dataclasses.replace(agent, tools=tools)
    return agent


def list_agents() -> list[Agent]:
    """The user's team: the templates they took on, plus the ones they built.

    Only rostered templates, not the whole library — otherwise `ask_agent` would
    offer every agent that exists rather than the ones this person uses, and the
    sidebar would be a catalogue again.
    """
    from .custom import get_custom_store

    raw = rostered_agents() + get_custom_store().list()
    return [_with_user_edits(a) for a in raw]


def get_agent(agent_id: str) -> Agent:
    """One agent by id, whether or not it is currently in the roster.

    A template that has been removed still resolves, because its conversation
    is still there to read and a routine created while it was in the roster
    should not break when the sidebar changes.
    """
    if agent_id in PRESETS:
        return _with_user_edits(PRESETS[agent_id])
    from .custom import get_custom_store
    custom = get_custom_store().get(agent_id)
    if custom:
        return _with_user_edits(custom)
    raise KeyError(f"unknown agent '{agent_id}'")


# `prompt` (to tell an agent who it can hand work to) and `delegation` (to check
# a named agent exists) both need the roster, and neither may import this module
# to get it — those edges were load-bearing in a thirteen-module cycle. This is
# where the roster is assembled, so this is where it is published.
roster.set_supplier(list_agents, get_agent)
