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
from . import erase, profile_files
from .agent import Agent
from .prompt import KNOWN_ACTIONS
from .tool_snapshot import add_menu_column, catalog, recorded_menu, resolve

_SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    tools         TEXT NOT NULL DEFAULT '[]',
    recall_sources TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    known         TEXT NOT NULL DEFAULT '',  -- JSON array: the catalog it chose from
    -- When the user retired it. Retiring takes an agent out of the rail and
    -- keeps everything; deleting destroys it. A preset has had both since the
    -- roster existed — leaving the roster is retiring — and a custom agent had
    -- only the destructive one, so "I am not using this right now" and "erase
    -- what it learned" were the same button.
    retired_at    TEXT
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
        # Same migration as the override store, from the same function: both
        # hold a snapshot of a tool list and must record it the same way.
        add_menu_column(self._c, "custom_agents")
        # Same reason `add_menu_column` exists: `CREATE TABLE IF NOT EXISTS` is
        # a no-op against a table that is already there, so a column added to
        # the schema never reaches anybody who installed before it.
        cols = {r[1] for r in self._c.execute("PRAGMA table_info(custom_agents)")}
        if "retired_at" not in cols:
            self._c.execute("ALTER TABLE custom_agents ADD COLUMN retired_at TEXT")
            self._c.commit()

    def _row_to_agent(self, r: sqlite3.Row) -> Agent:
        return Agent(
            id=r["id"], name=r["name"], role=r["role"],
            system_prompt=r["system_prompt"],
            # The list the user chose, plus anything that did not exist to
            # choose from when they chose it. An agent built in September had
            # no `browse_wait` on its menu, and nothing would ever have given
            # it one — see `tool_snapshot`.
            tools=resolve(json.loads(r["tools"]), recorded_menu(r)),
            recall_sources=json.loads(r["recall_sources"]),
            # An agent the user built themselves keeps every proposal it could
            # make before the prompt was split by capability. Narrowing one
            # without asking would quietly take away something they had.
            actions=list(KNOWN_ACTIONS),
        )

    def list(self) -> builtins.list[Agent]:
        """The ones on the rail. A retired agent is not on the team."""
        rows = self._c.execute(
            "SELECT * FROM custom_agents WHERE retired_at IS NULL "
            "ORDER BY created_at").fetchall()
        return [self._row_to_agent(r) for r in rows]

    def retired(self) -> builtins.list[dict]:
        """The ones the user put away, newest first, for the Library to offer
        back. Not `Agent`s: nothing runs them, and the Library needs when they
        were retired so the shelf can say how long ago."""
        rows = self._c.execute(
            "SELECT id, name, role, retired_at FROM custom_agents "
            "WHERE retired_at IS NOT NULL ORDER BY retired_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get(self, agent_id: str) -> Agent | None:
        """One agent, retired or not.

        Deliberately not filtered: a retired agent's conversation is still
        readable and a routine that named it should not break because the user
        put it away — the same reason `presets.get_agent` resolves a template
        that has left the roster.
        """
        r = self._c.execute(
            "SELECT * FROM custom_agents WHERE id=?", (agent_id,)).fetchone()
        return self._row_to_agent(r) if r else None

    def retire(self, agent_id: str) -> bool:
        """Put it away. Everything it has is kept and nothing is erased."""
        cur = self._c.execute(
            "UPDATE custom_agents SET retired_at=? WHERE id=? AND retired_at IS NULL",
            (datetime.now(UTC).isoformat(), agent_id))
        self._c.commit()
        return cur.rowcount > 0

    def restore(self, agent_id: str) -> bool:
        """Bring it back, with its memory, its persona and its conversation."""
        cur = self._c.execute(
            "UPDATE custom_agents SET retired_at=NULL WHERE id=?", (agent_id,))
        self._c.commit()
        return cur.rowcount > 0

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
            "recall_sources,created_at,known) VALUES (?,?,?,?,?,?,?,?)",
            (aid, name, role, system_prompt, json.dumps(tools),
             json.dumps(recall_sources or []),
             datetime.now(UTC).isoformat(), json.dumps(catalog())))
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
        """Destroy it, and everything it accumulated.

        The trail is `erase.everything` — one list, shared with the reset an
        agent we ship gets, so a store added later reaches both rather than
        whichever cleanup somebody remembered.
        """
        erase.everything(agent_id)
        cur = self._c.execute("DELETE FROM custom_agents WHERE id=?", (agent_id,))
        self._c.commit()
        return cur.rowcount > 0


_store = None


def get_custom_store() -> CustomAgentStore:
    global _store
    if _store is None:
        _store = CustomAgentStore()
    return _store
