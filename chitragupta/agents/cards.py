"""What happened to a card the user already answered.

An action card is drawn from the `<action>` tag inside a stored message, so
reopening a conversation rebuilds it from scratch — and it came back asking to
confirm something that had already been done. "Confirm & create" on an
automation that exists is not a cosmetic fault: the obvious thing to do with it
is press it, and the result is a second automation.

The outcome only ever lived in the DOM. This is where it lives instead.

**The key is computed by whoever draws the card**, from the action and its
position in the conversation, so the same card gets the same key on every
render without the message needing an id it does not have. Two identical
proposals in one conversation are told apart by their position — which is the
only thing that distinguishes them for a person reading it, too.

Not localStorage, for a reason worth stating: what a card did is a fact about
the world, and clearing a browser cache must not make the app offer to send an
email again. It is also small — one row per card the user has answered.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from ..log import suppressed
from .agent import _conn

#: What a card settled as. `pending` is not stored — a card with no row is
#: pending, which is also what a card drawn before this existed will be.
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

#: **We tried and we do not know.** The request itself did not come back — no
#: network, a 500, a timeout — so the action may have run and may not.
#:
#: This is a fourth state rather than a shade of `failed`, because the two call
#: for opposite things. `failed` is a thing that certainly did not happen and
#: the agent can be asked to fix its call. An unknown may have happened, so
#: pressing the button again is exactly the duplicate this whole table exists to
#: prevent — and telling the user it failed would be a claim nobody checked.
#:
#: The vocabulary is `automation/`'s, for the same situation one layer down: a
#: handler that raised leaves its claim open, "we tried and do not know", and
#: the retry verifies instead of repeating. Here the verifier is the action log:
#: `settledState` prefers a logged run over an `unknown` record, so a card that
#: really did go through corrects itself on the next render.
UNKNOWN = "unknown"

SETTLED = (DONE, FAILED, CANCELLED, UNKNOWN)

#: The columns, in order, so the schema and the migration cannot disagree about
#: what a row is. `key` is not unique on its own — see `_SCHEMA`.
_COLUMNS = ("key", "agent_id", "state", "detail", "log_id", "verified_at",
            "reversible", "undo_label", "at")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_cards (
    key        TEXT NOT NULL,
    agent_id   TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT 'done',
    detail     TEXT NOT NULL DEFAULT '',
    log_id     TEXT NOT NULL DEFAULT '',
    verified_at TEXT NOT NULL DEFAULT '',
    reversible INTEGER NOT NULL DEFAULT 0,
    undo_label TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL,
    -- **A card is named by its conversation AND its key.** `key TEXT PRIMARY
    -- KEY` baked "one card per key" into storage, and the key is computed from
    -- the action, its parameters and its position — none of which mentions the
    -- agent. So two agents proposing the same thing at the same point in their
    -- conversations shared a row, and answering the second MOVED the first
    -- one's answer: `ON CONFLICT(key) DO UPDATE` overwrote `agent_id` too.
    --
    -- What that costs is worst for the one state that exists nowhere else. A
    -- card the user CANCELLED leaves no trace in the action log, so once its
    -- row had been taken by another agent there was nothing left to recognise
    -- it by — and it came back offering its button, over something the user had
    -- explicitly declined. The column and its index were already here; only the
    -- key was wrong.
    PRIMARY KEY (agent_id, key)
);
CREATE INDEX IF NOT EXISTS idx_cards_agent ON action_cards(agent_id, at);
"""

#: Columns added after the table shipped. `CREATE TABLE IF NOT EXISTS` does
#: nothing to a table that already exists, so a user upgrading in place keeps the
#: old shape and every read of a new column raises. Same reason and same shape as
#: `agents/approvals.py::_ADDED_COLUMNS`.
_ADDED_COLUMNS = {"undo_label": "TEXT NOT NULL DEFAULT ''"}

_READY = False


def _repair_primary_key(conn: sqlite3.Connection) -> None:
    """Move an old `PRIMARY KEY (key)` table to `PRIMARY KEY (agent_id, key)`.

    SQLite cannot alter a primary key, so the table is rebuilt. The rows are
    carried across rather than dropped: a card's answer is a fact about the
    world, and the whole reason this table is not `localStorage` is that
    clearing a cache must not make the app offer to send an email again.

    `INSERT OR REPLACE` is the honest resolution for the rows the old key had
    already collapsed — they are indistinguishable by then, and the newest is
    the one the screen last agreed with.
    """
    pk = [r["name"] for r in conn.execute("PRAGMA table_info(action_cards)")
          if r["pk"]]
    if set(pk) == {"agent_id", "key"}:
        return
    columns = ", ".join(_COLUMNS)
    # The one schema, under a temporary name — every occurrence renamed, so the
    # index is created on the new table rather than on the one about to be
    # dropped. `_COLUMNS` is what makes the copy explicit: `SELECT *` would
    # depend on two tables happening to declare their columns in the same order.
    conn.executescript(_SCHEMA.replace("action_cards", "cards_rebuilt")
                       .replace("idx_cards_agent", "idx_cards_rebuilt"))
    conn.execute(f"INSERT OR REPLACE INTO cards_rebuilt ({columns}) "
                 f"SELECT {columns} FROM action_cards")
    conn.executescript(
        # Dropping the old table takes its own index with it.
        "DROP TABLE action_cards;"
        "ALTER TABLE cards_rebuilt RENAME TO action_cards;"
        # The rename carries the index across under its temporary name.
        "DROP INDEX IF EXISTS idx_cards_rebuilt;"
        "CREATE INDEX IF NOT EXISTS idx_cards_agent ON action_cards(agent_id, at);")
    conn.commit()


def _db() -> sqlite3.Connection:
    """The agents database, which is where a conversation already lives.

    A card belongs to a message; putting it in a file of its own would mean two
    things to back up and two things to keep in step.
    """
    global _READY
    conn = _conn()
    if not _READY:
        conn.executescript(_SCHEMA)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(action_cards)")}
        for column, decl in _ADDED_COLUMNS.items():
            if column not in have:
                conn.execute(
                    f"ALTER TABLE action_cards ADD COLUMN {column} {decl}")
        conn.commit()
        # After the columns, because the rebuild copies every one of them.
        _repair_primary_key(conn)
        _READY = True
    return conn


def reset_for_tests() -> None:
    global _READY
    _READY = False


def remember(agent_id: str, key: str, *, state: str, detail: str = "",
             log_id: str = "", verified_at: str = "",
             reversible: bool = False,
             undo_label: str = "") -> dict[str, Any]:
    """Record what a card settled as. Replaces any earlier answer for it.

    Replaces rather than refuses, because a card can legitimately be answered
    twice: an action that failed leaves its buttons, and the second attempt is
    the one that counts. An `unknown` is also written over once the action log
    settles the question.
    """
    if not key or state not in SETTLED:
        return {}
    conn = _db()
    conn.execute(
        "INSERT INTO action_cards (key,agent_id,state,detail,log_id,"
        "verified_at,reversible,undo_label,at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(agent_id,key) DO UPDATE SET state=excluded.state, "
        "detail=excluded.detail, log_id=excluded.log_id, "
        "verified_at=excluded.verified_at, reversible=excluded.reversible, "
        "undo_label=excluded.undo_label, at=excluded.at",
        (key, str(agent_id or ""), state, str(detail or "")[:400],
         str(log_id or ""), str(verified_at or ""), 1 if reversible else 0,
         str(undo_label or "")[:60], datetime.now(UTC).isoformat()))
    conn.commit()
    return get(agent_id, key)


def get(agent_id: str, key: str) -> dict[str, Any]:
    """One card's answer. **Addressed by conversation and key**, because the key
    alone was never unique — it names an action and a position, not an agent."""
    row = _db().execute(
        "SELECT * FROM action_cards WHERE agent_id=? AND key=?",
        (str(agent_id or ""), key)).fetchone()
    return _public(dict(row)) if row else {}


def _public(item: dict[str, Any]) -> dict[str, Any]:
    """A row as the screen reads it — `reversible` a bool, not sqlite's 1."""
    item["reversible"] = bool(item.get("reversible"))
    return item


def for_agent(agent_id: str) -> dict[str, dict[str, Any]]:
    """Every settled card in this conversation, keyed the way it was drawn."""
    out: dict[str, dict[str, Any]] = {}
    with suppressed("reading which action cards were already answered"):
        for row in _db().execute(
                "SELECT * FROM action_cards WHERE agent_id=?", (agent_id,)):
            item = _public(dict(row))
            out[item["key"]] = item
    return out


def forget_agent(agent_id: str) -> int:
    """Drop this conversation's cards. For "Clear chat", which removes the
    messages the cards belong to — leaving the answers behind would mean a new
    conversation inheriting them if a proposal ever keyed the same."""
    conn = _db()
    cur = conn.execute("DELETE FROM action_cards WHERE agent_id=?", (agent_id,))
    conn.commit()
    return cur.rowcount
