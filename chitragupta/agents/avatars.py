"""Per-agent avatars: the character document a user built for one agent.

The avatar itself is drawn in the browser by `web/character.js`, which owns the
whole format — the geometry, the palette, the schema and the normalisation. This
module stores one JSON document per agent and nothing more, deliberately:

**Nothing here understands what a character is.** No shape list, no palette
table, no field names. A second copy of the schema in Python would be a second
copy to keep current, and the copy that drifts is always the one that decides
whether a save is accepted. So the server checks the three things it can check
without knowing the format — that it is an object, that it claims the schema we
serve, and that it is not enormous — and leaves meaning to the renderer, which
normalises every document on the way in anyway and cannot be handed one it
refuses to draw.

**An agent without a row is not an agent without an avatar.** The frontend
generates one deterministically from the agent id, so every agent has a distinct
character from first launch with nothing stored. A row here is an override, and
deleting it returns the agent to its generated character rather than to nothing.

Shaped after `agent_models.py`, which solves the same problem for a different
per-agent setting, and stored in the same `agents.db`.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from ..config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_avatars (
    agent_id    TEXT PRIMARY KEY,
    scene       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

#: The schema id the bundled renderer writes. A document claiming anything else
#: came from a different tool or a different major version, and storing it would
#: mean an agent whose avatar silently fails to draw at some later launch.
SCHEMA_ID = "character.scene"

#: A generous ceiling on one stored document. A full character with a dozen
#: parts is around 3 kB; 256 kB is far past any real one and well short of
#: anything that would make listing every agent's avatar slow.
MAX_SCENE_BYTES = 256 * 1024


class AvatarRejectedError(ValueError):
    """The document was not something we are willing to store.

    Carries a sentence meant for a person, because it is shown to one.
    """


def _get_db() -> sqlite3.Connection:
    path = get_settings().home / "agents.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _validate(scene: Any) -> str:
    """Return the document as JSON text, or raise `AvatarRejectedError`."""
    if not isinstance(scene, dict):
        raise AvatarRejectedError("That avatar could not be read.")
    if scene.get("schema") != SCHEMA_ID:
        raise AvatarRejectedError("That avatar was made by a different tool.")
    try:
        text = json.dumps(scene, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AvatarRejectedError("That avatar could not be saved.") from exc
    if len(text.encode("utf-8")) > MAX_SCENE_BYTES:
        raise AvatarRejectedError("That avatar is too large to save.")
    return text


def get_agent_avatar(agent_id: str) -> dict[str, Any] | None:
    """The stored document for one agent, or None if it uses its generated one."""
    conn = _get_db()
    row = conn.execute(
        "SELECT scene FROM agent_avatars WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if not row:
        return None
    return _decode(row["scene"])


def set_agent_avatar(agent_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    """Store one agent's avatar, replacing any previous one."""
    text = _validate(scene)
    now = datetime.now(UTC).isoformat()
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO agent_avatars (agent_id, scene, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            scene = excluded.scene,
            updated_at = excluded.updated_at
        """,
        (agent_id, text, now),
    )
    conn.commit()
    return {"agent_id": agent_id, "scene": scene, "updated_at": now}


def clear_agent_avatar(agent_id: str) -> bool:
    """Drop the override so the agent goes back to its generated character."""
    conn = _get_db()
    cur = conn.execute("DELETE FROM agent_avatars WHERE agent_id = ?", (agent_id,))
    conn.commit()
    return cur.rowcount > 0


def list_agent_avatars() -> dict[str, dict[str, Any]]:
    """Every stored avatar, keyed by agent id.

    One call, because the workspace needs all of them before it paints the first
    frame of the agent rail — and an avatar that arrives after the rail has been
    drawn is a visible flicker of the wrong face.
    """
    conn = _get_db()
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT agent_id, scene, updated_at FROM agent_avatars"):
        scene = _decode(row["scene"])
        if scene is None:
            # A row we cannot parse is a row written by something else, or one
            # a disk error truncated. Skipping it costs that agent its custom
            # avatar for this launch; raising would cost every agent theirs.
            continue
        out[row["agent_id"]] = {"scene": scene, "updated_at": row["updated_at"]}
    return out


def _decode(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
