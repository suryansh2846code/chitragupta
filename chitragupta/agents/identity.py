"""What the user calls an agent, when it is not what we called it.

The fourth per-agent override, and the same shape as the other three
(`agent_models`, `agent_avatars`, `agent_tool_overrides`): a row means the user
changed something, no row means they have not, and clearing one is
reset-to-shipped rather than a value anybody has to remember.

Structured rather than prose, which is why it is a table and not a third file
beside `persona.md`. A name is a field on a form; putting it in markdown would
mean a parser, and a parser is a thing a user can break by typing.

**The id never moves.** Everything an agent owns is keyed by it — its chat
history, its notes, its tool overrides, its connector grants, the folder its
files live in. Renaming is a label change and nothing else; a rename that
re-slugged the id would be a delete and a create wearing the same face.

**Only what was given is overridden.** `name=None` means "leave the role alone"
and vice versa, because the profile saves one field at a time and a blanked
role is what the rail shows under the name.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from ..config import get_settings

#: The rail truncates past this, and a name nobody can read is a name that
#: cannot be picked out of a list to fix.
MAX_NAME = 60

#: Shown under the name in the rail and the chat header.
MAX_ROLE = 140

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_identity (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT,
    role        TEXT,
    updated_at  TEXT NOT NULL
);
"""


class IdentityRejectedError(ValueError):
    """Carries a sentence meant for the person who pressed Save."""


def _get_db() -> sqlite3.Connection:
    path = get_settings().home / "agents.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def get(agent_id: str) -> dict[str, Any] | None:
    """The user's name and role for this agent, or None if untouched.

    Either field may be None on its own: they are set independently, so "they
    renamed it but kept the role" is a real and common state.
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT name, role FROM agent_identity WHERE agent_id = ?",
        (agent_id,)).fetchone()
    if not row or (row["name"] is None and row["role"] is None):
        return None
    return {"name": row["name"], "role": row["role"]}


def set_identity(agent_id: str, name: str | None = None,
                 role: str | None = None) -> dict[str, Any]:
    """Rename an agent, change its role, or both. Omitted fields are untouched."""
    current = get(agent_id) or {}
    if name is not None:
        name = name.strip()
        if not name:
            raise IdentityRejectedError("An agent needs a name.")
        if len(name) > MAX_NAME:
            raise IdentityRejectedError(
                f"That name is too long — keep it under {MAX_NAME} characters.")
    if role is not None:
        role = role.strip()
        if len(role) > MAX_ROLE:
            raise IdentityRejectedError(
                f"That is too long — keep it under {MAX_ROLE} characters.")

    name = current.get("name") if name is None else name
    role = current.get("role") if role is None else role
    now = datetime.now(UTC).isoformat()
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO agent_identity (agent_id, name, role, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            name = excluded.name,
            role = excluded.role,
            updated_at = excluded.updated_at
        """,
        (agent_id, name, role, now))
    conn.commit()
    return {"agent_id": agent_id, "name": name, "role": role, "updated_at": now}


def clear(agent_id: str) -> bool:
    """Back to the name we ship."""
    conn = _get_db()
    cur = conn.execute("DELETE FROM agent_identity WHERE agent_id = ?",
                       (agent_id,))
    conn.commit()
    return cur.rowcount > 0
