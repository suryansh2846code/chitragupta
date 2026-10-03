"""User-defined custom agents, persisted alongside the built-in presets."""
from __future__ import annotations

import builtins
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime

from ..config import get_settings
from ..log import suppressed
from . import notes, profile_files
from .agent import Agent
from .prompt import KNOWN_ACTIONS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    tools         TEXT NOT NULL DEFAULT '[]',
    recall_sources TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL
);
"""


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or ("agent-" + uuid.uuid4().hex[:6])


class CustomAgentStore:
    def __init__(self) -> None:
        path = get_settings().home / "agents.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA)

    def _row_to_agent(self, r: sqlite3.Row) -> Agent:
        return Agent(
            id=r["id"], name=r["name"], role=r["role"],
            system_prompt=r["system_prompt"],
            tools=json.loads(r["tools"]),
            recall_sources=json.loads(r["recall_sources"]),
            # An agent the user built themselves keeps every proposal it could
            # make before the prompt was split by capability. Narrowing one
            # without asking would quietly take away something they had.
            actions=list(KNOWN_ACTIONS),
        )

    def list(self) -> builtins.list[Agent]:
        rows = self._c.execute(
            "SELECT * FROM custom_agents ORDER BY created_at").fetchall()
        return [self._row_to_agent(r) for r in rows]

    def get(self, agent_id: str) -> Agent | None:
        r = self._c.execute(
            "SELECT * FROM custom_agents WHERE id=?", (agent_id,)).fetchone()
        return self._row_to_agent(r) if r else None

    def create(self, name: str, role: str = "", system_prompt: str = "",
               tools: builtins.list[str] | None = None,
               recall_sources: builtins.list[str] | None = None) -> Agent:
        name = (name or "").strip() or "New Agent"
        base = _slug(name)
        aid, n = base, 2
        while self._c.execute("SELECT 1 FROM custom_agents WHERE id=?",
                              (aid,)).fetchone():
            aid = f"{base}-{n}"; n += 1
        # The same base every preset gets, from the same constant. It used to
        # be five hand-picked names with no connector access, so an agent the
        # user built themselves could not read a connector they had signed into
        # — and the sentinel is not offered at build time when nothing is
        # connected yet, so there was no moment at which they could have chosen
        # it. See docs/development/agent-tool-grants.md §1.
        from .library import BASE_TOOLS

        tools = tools if tools is not None else list(BASE_TOOLS)
        self._c.execute(
            "INSERT INTO custom_agents (id,name,role,system_prompt,tools,"
            "recall_sources,created_at) VALUES (?,?,?,?,?,?,?)",
            (aid, name, role, system_prompt, json.dumps(tools),
             json.dumps(recall_sources or []),
             datetime.now(UTC).isoformat()))
        self._c.commit()
        # Born file-backed. The column above is still written because an agent
        # is a row and always has been, but from here on `persona.md` is what
        # `presets._with_user_edits` reads — so there is one live copy of this
        # prose and the column only ever answers for agents made before it.
        # Nothing is written when there are no instructions: an empty file
        # means "the user cleared it", which is a different thing from "it
        # never had any".
        if system_prompt.strip():
            with suppressed("writing a new agent's persona.md"):
                profile_files.write(aid, profile_files.PERSONA, system_prompt)
        return self.get(aid)

    def delete(self, agent_id: str) -> bool:
        with suppressed("from .agent_models import clear_agent_model …"):
            from .agent_models import clear_agent_model
            clear_agent_model(agent_id)
        with suppressed("from .connector_grants import forget_agent …"):
            from .connector_grants import forget_agent
            forget_agent(agent_id)
        with suppressed("from .tool_overrides import get_tool_overrides …"):
            # An id is a slug of the name, so it is deterministic: delete
            # "Chotu", build another "Chotu", and it lands on the same id. An
            # override left behind would then apply to an agent that never had
            # it — a tool list from a deleted agent, silently.
            from .tool_overrides import get_tool_overrides
            get_tool_overrides().clear(agent_id)
        # Same reasoning as the override above, one step worse: a leftover
        # `memory.md` is not a stale setting, it is prose the next agent with
        # this name reads and acts on — standing instructions for a job it was
        # never given.
        profile_files.forget(agent_id)
        # And the ledger of what was written into those notes, or the next
        # agent with this name starts life unable to learn anything its
        # predecessor had already learned and lost.
        with suppressed("clearing the note ledger for a deleted agent"):
            notes.forget(agent_id)
        cur = self._c.execute("DELETE FROM custom_agents WHERE id=?", (agent_id,))
        self._c.commit()
        return cur.rowcount > 0


_store = None


def get_custom_store() -> CustomAgentStore:
    global _store
    if _store is None:
        _store = CustomAgentStore()
    return _store
