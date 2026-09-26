"""What the agents have told the user, and marking it read.

The list itself is `chitragupta/messages.py`. Nothing is decided here: an
agent writes, the user reads, and the only state this changes is whether
something has been seen.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...log import get_logger, suppressed

log = get_logger(__name__)
router = APIRouter()

#: Shown per request. The list is for the last little while, not an archive —
#: anything older is in the run history it came from.
MAX_MESSAGES = 50


@router.get("/api/messages")
def list_messages(limit: int = 30):
    """Newest first, read and unread together.

    Hiding what has been read would make it impossible to find something
    again, which is the one thing a list like this has to be good at.

    Each message carries the sending agent's **name**, resolved here: a list
    that printed `chief-of-staff` would be a list of ids, and the whole point
    is that a person can see who is talking to them.
    """
    from ... import messages

    rows = messages.get_messages().recent(limit=min(limit, MAX_MESSAGES))
    names = _agent_names()
    return {
        "messages": [{
            **row,
            "agent_name": names.get(row["agent_id"], row["agent_id"] or "Chitragupta"),
            "unread": not row["read_at"],
        } for row in rows],
        "unread": messages.get_messages().unread(),
    }


def _agent_names() -> dict[str, str]:
    """`id -> the name on screen`, for whoever still exists.

    An agent that has been deleted keeps its messages — what it said happened,
    and deleting the agent does not unsay it. Its id stands in for the name,
    which is honest about a sender who is no longer there.
    """
    with suppressed("naming the agents that have sent messages"):
        from ...agents import list_agents
        return {a.id: a.name for a in list_agents()}
    return {}


@router.post("/api/messages/{message_id}/read")
def mark_read(message_id: str):
    from ... import messages

    if not messages.get_messages().mark_read(message_id):
        # Already read, or gone. Neither is an error worth a red box: the user
        # asked for it to be read and it is read.
        return {"ok": True, "already": True}
    return {"ok": True}


@router.post("/api/messages/read-all")
def mark_all_read():
    from ... import messages

    return {"ok": True, "read": messages.get_messages().mark_all_read()}


@router.delete("/api/messages/{message_id}")
def delete_message(message_id: str):
    """Remove one. The run it came from is untouched — this is the notice,
    not the record."""
    from ... import messages

    if not messages.get_messages().delete(message_id):
        raise HTTPException(404, "no such message")
    return {"ok": True}
