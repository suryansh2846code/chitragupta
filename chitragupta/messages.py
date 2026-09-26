"""What an agent wants to tell the user, in one place they can read later.

Before this there were three places an agent could say something and none of
them worked for the thing agents mostly need to say.

* **A desktop notification** vanishes. If the machine was asleep, or the user
  was in another space, or they glanced away — it is gone, and there is no
  record it ever existed.
* **The agent's own chat.** Tried and undone: an automation's result is not
  part of a conversation the user was having, and putting it there also put
  the automation's whole prompt in beside it, attributed to a user who typed
  none of it. A chat is a thing you are in the middle of; a notification is a
  thing that arrives.
* **The run history.** Correct, complete, and somewhere nobody looks — you
  have to already know something happened to go and read that it did.

So: one list, keyed by the agent who sent it, read in the Inbox where the user
already is. It outlives the moment it was sent, it says who is speaking, and
it can be marked read — which is the whole difference between a notification
and a record.

**Anything an agent wants to say goes through here**, not only automations. A
reminder firing, a watch finding something, a run that stopped and needs a
decision: same channel, same list, one place to look.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from .config import get_settings
from .log import suppressed

#: What kind of thing this is, which decides only how it is shown.
#:
#: Deliberately a small set. A "kind" per feature is how a list ends up with
#: fourteen badge colours and no meaning, and the only distinction the user
#: actually acts on is whether something is waiting for them.
RESULT = "result"           # an automation found something
NEEDS_YOU = "needs_you"     # it stopped, or it wants a decision
REMINDER = "reminder"       # a time the user asked to be told about
NOTE = "note"               # an agent saying something on its own account
KINDS = (RESULT, NEEDS_YOU, REMINDER, NOTE)

#: Kept so the list stays readable and the database stays small. A message
#: nobody has read in six weeks is not going to be read.
MAX_KEPT = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_notices (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'note',
    title      TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '',
    -- Where it came from, so "open it" can go somewhere. An automation run id,
    -- a reminder id; empty when there is nothing to open.
    source     TEXT NOT NULL DEFAULT '',
    source_id  TEXT NOT NULL DEFAULT '',
    read_at    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notices_time ON agent_notices(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notices_unread ON agent_notices(read_at, created_at);
"""


class MessageStore:
    """Every message an agent has sent the user."""

    def __init__(self) -> None:
        path = get_settings().home / "messages.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL;")
        # The scheduler writes these and the API reads them, from two threads.
        self._c.execute("PRAGMA busy_timeout=5000;")
        self._c.executescript(_SCHEMA)

    def send(self, agent_id: str, body: str, *, title: str = "",
             kind: str = NOTE, source: str = "", source_id: str = "") -> dict:
        """Say something to the user. Returns the row.

        An empty body is not sent. A message with nothing in it is a line in
        the list that costs the user a glance and tells them nothing, and the
        callers most likely to produce one are the ones reporting on something
        that did nothing.
        """
        text = str(body or "").strip()
        if not text:
            return {}
        row_id = str(uuid.uuid4())
        self._c.execute(
            "INSERT INTO agent_notices (id,agent_id,kind,title,body,source,"
            "source_id,read_at,created_at) VALUES (?,?,?,?,?,?,?,'',?)",
            (row_id, str(agent_id or ""),
             kind if kind in KINDS else NOTE,
             str(title or "").strip(), text, str(source or ""),
             str(source_id or ""), datetime.now(UTC).isoformat()))
        self._c.commit()
        self._prune()
        return self.get(row_id)

    def get(self, row_id: str) -> dict:
        row = self._c.execute("SELECT * FROM agent_notices WHERE id=?",
                              (row_id,)).fetchone()
        return dict(row) if row else {}

    def recent(self, limit: int = 30) -> list[dict]:
        """Newest first. Read and unread together — a list that hid what you
        had read would make it impossible to find something again."""
        rows = self._c.execute(
            "SELECT * FROM agent_notices ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit or 30), MAX_KEPT)),)).fetchall()
        return [dict(r) for r in rows]

    def unread(self) -> int:
        return int(self._c.execute(
            "SELECT COUNT(*) FROM agent_notices WHERE read_at=''").fetchone()[0])

    def mark_read(self, row_id: str) -> bool:
        cur = self._c.execute(
            "UPDATE agent_notices SET read_at=? WHERE id=? AND read_at=''",
            (datetime.now(UTC).isoformat(), row_id))
        self._c.commit()
        return cur.rowcount > 0

    def mark_all_read(self) -> int:
        cur = self._c.execute(
            "UPDATE agent_notices SET read_at=? WHERE read_at=''",
            (datetime.now(UTC).isoformat(),))
        self._c.commit()
        return cur.rowcount

    def delete(self, row_id: str) -> bool:
        cur = self._c.execute("DELETE FROM agent_notices WHERE id=?", (row_id,))
        self._c.commit()
        return cur.rowcount > 0

    def _prune(self) -> None:
        """Keep the newest `MAX_KEPT`. Unread ones are kept whatever their age —
        the point of a message is that somebody still has to see it."""
        with suppressed("pruning the message list"):
            self._c.execute(
                "DELETE FROM agent_notices WHERE read_at != '' AND id NOT IN ("
                "SELECT id FROM agent_notices ORDER BY created_at DESC LIMIT ?)",
                (MAX_KEPT,))
            self._c.commit()


_STORE: MessageStore | None = None


def get_messages() -> MessageStore:
    global _STORE
    if _STORE is None:
        _STORE = MessageStore()
    return _STORE


def reset_for_tests() -> None:
    global _STORE
    if _STORE is not None:
        with suppressed("closing the message store between tests"):
            _STORE._c.close()
    _STORE = None


def send(agent_id: str, body: str, **kw: Any) -> dict:
    """Say something to the user. The one function anything else should call."""
    with suppressed("sending a message to the user"):
        return get_messages().send(agent_id, body, **kw)
    return {}
