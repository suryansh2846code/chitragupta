"""What the user changed about an agent's tools.

An agent's tool list has two sources. A preset ships one in code; a custom
agent stores one when it is built. Neither could be changed afterwards, which
is how an agent ended up permanently unable to see a connector the user had
just added — with no screen anywhere that would have shown them why.

This is the layer that records the change, and it is deliberately an
**override** rather than an edit of the original:

* A preset stays a code constant. Editing `PRESETS` in place would mean the
  shipped defaults are whatever the user last did, and a later release that
  improves a preset could never reach anyone who had touched it.
* Reset-to-default is then just deleting a row, rather than needing to know
  what the default used to be.
* One table covers both kinds of agent, so `get_agent()` applies it in one
  place instead of the caller having to know which kind it is holding.

The stored list is **not validated against the live tool catalog**, on purpose.
A connector that is offline drops its tools out of the catalog, so validating
would silently strip every Notion tool from an agent the moment Notion was
signed out — turning a temporary outage into a permanent edit. Resolving names
to real tools is `build_tools()`'s job, and it already ignores what it cannot
find.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from ..config import get_settings
from ..log import get_logger
from .tool_snapshot import add_menu_column, catalog, recorded_menu

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_tool_overrides (
    agent_id   TEXT PRIMARY KEY,
    tools      TEXT NOT NULL,          -- JSON array of tool names
    updated_at TEXT NOT NULL,
    known      TEXT NOT NULL DEFAULT ''  -- JSON array: the catalog it chose from
);
"""


class ToolOverrideStore:
    def __init__(self) -> None:
        # Same file as the custom agents: this is a fact about an agent, and
        # splitting it into its own database would mean two things to back up
        # and two to keep consistent.
        path = get_settings().home / "agents.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA)
        add_menu_column(self._c, "agent_tool_overrides")

    def get(self, agent_id: str) -> list[str] | None:
        """The user's tool list for this agent, or None if untouched.

        None and `[]` mean different things and must stay distinguishable:
        untouched falls back to the default, empty is an agent the user has
        deliberately stripped of every tool.
        """
        row = self._c.execute(
            "SELECT tools FROM agent_tool_overrides WHERE agent_id=?",
            (agent_id,)).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["tools"])
        except (TypeError, ValueError):
            log.warning("unreadable tool override for %r — ignoring it", agent_id)
            return None
        return [str(t) for t in value] if isinstance(value, list) else None

    def menu(self, agent_id: str) -> list[str] | None:
        """The tool catalog this agent's list was chosen from, or None.

        None is a row written before the catalog was recorded — see
        `tool_snapshot`, which is the only thing that knows what to do with
        that. Empty is impossible: a save always has a catalog.
        """
        row = self._c.execute(
            "SELECT known FROM agent_tool_overrides WHERE agent_id=?",
            (agent_id,)).fetchone()
        return recorded_menu(row)

    def set(self, agent_id: str, tools: list[str]) -> list[str]:
        """Record a tool list. Order is preserved; duplicates are not."""
        seen: dict[str, None] = {}
        for t in tools:
            name = str(t).strip()
            if name:
                seen.setdefault(name, None)
        clean = list(seen)
        # The catalog goes in with the choice. Without it, a tool that ships
        # tomorrow is indistinguishable from one the user turned down today,
        # and the list is a snapshot nothing can ever reach again.
        self._c.execute(
            "INSERT INTO agent_tool_overrides (agent_id, tools, updated_at, known) "
            "VALUES (?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
            "tools=excluded.tools, updated_at=excluded.updated_at, "
            "known=excluded.known",
            (agent_id, json.dumps(clean), datetime.now(UTC).isoformat(),
             json.dumps(catalog())))
        self._c.commit()
        return clean

    def clear(self, agent_id: str) -> bool:
        """Forget the user's changes, so the agent goes back to its default."""
        cur = self._c.execute(
            "DELETE FROM agent_tool_overrides WHERE agent_id=?", (agent_id,))
        self._c.commit()
        return cur.rowcount > 0


_store: ToolOverrideStore | None = None


def get_tool_overrides() -> ToolOverrideStore:
    global _store
    if _store is None:
        _store = ToolOverrideStore()
    return _store
