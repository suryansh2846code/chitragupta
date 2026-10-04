"""The Agent primitive.

A named, persistent, domain-scoped worker. Each agent has its own system
prompt, its own tool set, and its own conversation memory — but every agent
shares the one Brain, so what one learns makes the others smarter.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import get_settings


@dataclass
class Agent:
    id: str
    name: str
    role: str                       # short domain label, e.g. "Inbox"
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    recall_sources: list[str] = field(default_factory=list)  # soft-preferred brain sources
    model_provider: str | None = None   # override global BYO model per agent
    model_name: str | None = None
    #: Does this agent skip asking before it reaches a connected app?
    #:
    #: A template declares it and only Chief of Staff does. It rides on the
    #: Agent so `connector_grants` can ask `roster` instead of `library`:
    #: that import was the edge that closed a ten-module cycle the moment
    #: `access` needed the gate, and `roster` is the seam this package already
    #: uses for "tell me about an agent" from underneath.
    unrestricted_connectors: bool = False

    #: Which proposals this agent may make. Empty means it has no way to
    #: propose an action at all — and is not told how to, which is the point:
    #: an agent that cannot send email should not be carrying the email
    #: protocol on every round, or be able to claim it sent one.
    actions: list[str] = field(default_factory=list)

    def system_message(self) -> str:
        """Assembled from what this agent can actually do. See `prompt.py`."""
        from .prompt import build

        return build(name=self.name, role=self.role,
                     system_prompt=self.system_prompt, actions=self.actions,
                     tools=self.tools, agent_id=self.id)


# ── per-agent conversation memory (persistent) ────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id        TEXT PRIMARY KEY,
    agent_id  TEXT NOT NULL,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    tool_json TEXT,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_msgs ON agent_messages(agent_id, ts);

-- What the agent remembers about turns that have fallen out of the verbatim
-- window. One row per agent, rewritten as the conversation grows; `through_ts`
-- marks how far it covers, so folding in newer turns never re-reads the lot.
CREATE TABLE IF NOT EXISTS agent_summaries (
    agent_id   TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    through_ts TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    path = get_settings().home / "agents.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


class AgentMemory:
    """Persistent per-agent chat history."""

    def __init__(self) -> None:
        self._c = _conn()

    def append(self, agent_id: str, role: str, content: str,
               tool_json: str | None = None) -> None:
        self._c.execute(
            "INSERT INTO agent_messages (id,agent_id,role,content,tool_json,ts) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), agent_id, role, content, tool_json,
             datetime.now(UTC).isoformat()),
        )
        self._c.commit()

    def history(self, agent_id: str, limit: int = 40) -> list[dict]:
        rows = self._c.execute(
            "SELECT role,content,tool_json,ts FROM agent_messages "
            "WHERE agent_id=? ORDER BY ts DESC LIMIT ?", (agent_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def messages_before(self, agent_id: str, ts: str | None,
                        after: str | None = None) -> list[dict]:
        """Turns older than `ts`, optionally newer than `after`.

        `after` is what makes compaction incremental: only the slice that has
        aged out since the summary was last written needs folding in.
        """
        sql = ("SELECT role,content,ts FROM agent_messages "
               "WHERE agent_id=? AND role IN ('user','assistant')")
        args: list = [agent_id]
        if ts:
            sql += " AND ts < ?"
            args.append(ts)
        if after:
            sql += " AND ts > ?"
            args.append(after)
        sql += " ORDER BY ts ASC"
        return [dict(r) for r in self._c.execute(sql, args).fetchall()]

    def get_summary(self, agent_id: str) -> dict | None:
        row = self._c.execute(
            "SELECT summary,through_ts FROM agent_summaries WHERE agent_id=?",
            (agent_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_summary(self, agent_id: str, summary: str, through_ts: str) -> None:
        self._c.execute(
            "INSERT INTO agent_summaries (agent_id,summary,through_ts,updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
            "summary=excluded.summary, through_ts=excluded.through_ts, "
            "updated_at=excluded.updated_at",
            (agent_id, summary, through_ts, datetime.now(UTC).isoformat()),
        )
        self._c.commit()

    def clear(self, agent_id: str) -> None:
        self._c.execute("DELETE FROM agent_messages WHERE agent_id=?", (agent_id,))
        self._c.execute("DELETE FROM agent_summaries WHERE agent_id=?", (agent_id,))
        self._c.commit()
