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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_cards (
    key        TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT 'done',
    detail     TEXT NOT NULL DEFAULT '',
    log_id     TEXT NOT NULL DEFAULT '',
    verified_at TEXT NOT NULL DEFAULT '',
    reversible INTEGER NOT NULL DEFAULT 0,
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_agent ON action_cards(agent_id, at);
"""

_READY = False


def _db() -> sqlite3.Connection:
    """The agents database, which is where a conversation already lives.

    A card belongs to a message; putting it in a file of its own would mean two
    things to back up and two things to keep in step.
    """
    global _READY
    conn = _conn()
    if not _READY:
        conn.executescript(_SCHEMA)
        conn.commit()
        _READY = True
    return conn


def reset_for_tests() -> None:
    global _READY
    _READY = False


def remember(agent_id: str, key: str, *, state: str, detail: str = "",
             log_id: str = "", verified_at: str = "",
             reversible: bool = False) -> dict[str, Any]:
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
        "verified_at,reversible,at) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET state=excluded.state, "
        "detail=excluded.detail, log_id=excluded.log_id, "
        "verified_at=excluded.verified_at, reversible=excluded.reversible, "
        "at=excluded.at",
        (key, str(agent_id or ""), state, str(detail or "")[:400],
         str(log_id or ""), str(verified_at or ""), 1 if reversible else 0,
         datetime.now(UTC).isoformat()))
    conn.commit()
    return get(key)


def get(key: str) -> dict[str, Any]:
    row = _db().execute("SELECT * FROM action_cards WHERE key=?",
                        (key,)).fetchone()
    return dict(row) if row else {}


def for_agent(agent_id: str) -> dict[str, dict[str, Any]]:
    """Every settled card in this conversation, keyed the way it was drawn."""
    out: dict[str, dict[str, Any]] = {}
    with suppressed("reading which action cards were already answered"):
        for row in _db().execute(
                "SELECT * FROM action_cards WHERE agent_id=?", (agent_id,)):
            item = dict(row)
            item["reversible"] = bool(item.get("reversible"))
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
