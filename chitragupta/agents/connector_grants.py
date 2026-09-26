"""Whether an agent may reach a connector right now.

An agent holding the connector category could read everything the user had
connected, on any turn, without saying so. That was the right shape when the
category was the only way to reach a connector at all. It is the wrong one once
agents are specialists somebody assembles: adding a Writer should not hand it
the inbox.

The line is not "trust the agent less". It is that reaching a third-party
account is a thing the user should be able to see happening and decide about,
per agent — while never being asked twice about something already settled.

Three ways in, and only three:

* **once** — this turn. From *Allow once*, or from `@gmail` in the message.
  Never stored: persisting "just this time" turns it into something the user
  has to remember to undo.
* **always** — stored per `(agent_id, connector)` until revoked.
* **unrestricted** — a template declares it. Only Chief of Staff, which is the
  agent the user adds knowing it is the one that can do anything.

Contract, including every field name: `docs/development/connector-permissions.md`.
"""
from __future__ import annotations

import contextvars
import sqlite3
from datetime import UTC, datetime

from ..config import get_settings
from ..log import get_logger, suppressed

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_connector_grants (
    agent_id   TEXT NOT NULL,
    connector  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, connector)
);
"""

#: Connectors granted for the current turn only.
#:
#: A ContextVar, not an attribute on anything: tool calls run in a thread pool
#: and a sub-agent runs a whole turn of its own, so this has to travel the same
#: way the delegation chain does. A plain global would leak one turn's grant
#: into whatever ran next.
_ONCE: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "chitragupta_connector_grants_once", default=frozenset())


#: One connection, held. Opening a fresh one per call looks harmless and is
#: not: `execute` on one and `commit` on another commits nothing, so a grant
#: was written and then rolled back when its connection was collected — while
#: the abandoned handles piled up until SQLite reported the database locked.
_DB: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    # The same file as the agents themselves: this is a fact about an agent,
    # and a second database would be a second thing to back up and keep
    # consistent.
    global _DB
    if _DB is None:
        path = get_settings().home / "agents.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _DB = sqlite3.connect(str(path), check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _DB.executescript(_SCHEMA)
    return _DB


#: Built-in tools that reach a connector, and which one.
#:
#: MCP tools carry their connector with them — `mcp_tools.connector_of` reads it
#: off the same pass that built the tool, so it cannot drift. A first-party tool
#: has no such registry: `list_mail` is a Python function, and nothing about it
#: says "Gmail" except this table.
#:
#: Without it the whole permission model had a hole in the shape of its own
#: motivating example — an agent had to ask before reading a Notion page and
#: could read the user's entire inbox without a word. `test_mail_tools.py` pins
#: every name here against the live tool table, because a renamed tool that
#: silently falls out of this map is a tool that silently stops asking.
FIRST_PARTY_TOOLS: dict[str, str] = {
    "gmail_search": "gmail",
    "list_mail": "gmail",
    "read_thread": "gmail",
    "calendar_lookup": "gcal",
    # Reads other people's free/busy as well as the user's own, so it is
    # at least as much a reach into Google as the lookup beside it.
    "find_time": "gcal",
    # The browser is a connector too. Which SITES it may reach is a separate
    # question, answered per origin in `browser/origins.py`; this answers
    # whether this agent may open one at all.
    "browse_sites": "browser",
    "browse_open": "browser",
    "browse_read": "browser",
    "browse_find": "browser",
}


def connector_of(tool_name: str) -> str:
    """Which connector a tool reaches, by id, or "" if it reaches none.

    The one answer both kinds of tool go through, so `loop.py` asks once.
    """
    from .mcp_tools import connector_of as mcp_connector_of

    found = mcp_connector_of(tool_name)
    return found or FIRST_PARTY_TOOLS.get(tool_name, "")


def first_party_labels() -> dict[str, str]:
    """Connector id → display name, for the first-party connectors in use here.

    Only the ones actually set up: offering to grant a connector the user has
    not connected is offering a control that cannot do anything.
    """
    labels: dict[str, str] = {}
    for connector_id in set(FIRST_PARTY_TOOLS.values()):
        if connector_id == "browser":
            # Not in the connector registry — it is the browser subsystem, and
            # "set up" means the one-time download has happened. Offering it
            # before that would be offering a control that cannot work.
            with suppressed("checking whether the browser is set up"):
                from ..browser import chromium

                if chromium.is_installed():
                    labels[connector_id] = "Browser"
            continue
        with suppressed("naming a built-in connector for the permission picker"):
            from ..connectors import get_connector

            connector = get_connector(connector_id)
            ready, _reason = connector.is_configured()
            if ready:
                labels[connector_id] = str(getattr(connector, "label", connector_id))
    return labels


#: The agent whose turn is currently running.
#:
#: `_blocked` maps a tool NAME to a connector, which is enough for every tool
#: that reaches exactly one. It is not enough for `list_chats`, which reaches
#: whichever app the model named in the arguments — the gate cannot see those.
#: So those tools ask the question themselves, and this is how they know who is
#: asking. A ContextVar for the same reason `_ONCE` is one: tool calls run in a
#: thread pool, under one `copy_context()` per call.
_ACTING: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chitragupta_acting_agent", default="")


def acting_as(agent_id: str):
    """Mark whose turn is running. Returns a token for `stop_acting`."""
    return _ACTING.set(str(agent_id or ""))


def stop_acting(token) -> None:
    with suppressed("clearing the acting agent"):
        _ACTING.reset(token)


def acting() -> str:
    """The agent currently running, for a tool that must gate itself."""
    return _ACTING.get()


# ── the one-turn ceiling ─────────────────────────────────────────────────
#
#: The opposite of `_ONCE`, and the reason both exist. A grant **widens** what
#: one turn may reach; this **narrows** it, and narrowing is the only direction
#: an automation is allowed to move in — one may never be able to do something
#: an interactive agent may not.
#:
#: `None` means unscoped, which is every turn a person is sitting in front of.
#: A frozenset means these and nothing else, and it beats a stored grant and an
#: unrestricted agent both: a mail-watching automation has no business in the
#: calendar even when the agent running it does.
_ONLY: contextvars.ContextVar[frozenset[str] | None] = contextvars.ContextVar(
    "chitragupta_only_connectors", default=None)


def only_these(connectors: list[str] | None):
    """Limit this turn to these connectors. Returns a token for `release_only`.

    An empty list is not a scope — it is "nothing was chosen", which means
    unscoped. An automation that may reach no app at all is not a thing anyone
    wants, and reading it that way would silently break every automation whose
    settings are empty because they predate this.
    """
    clean = frozenset(str(c).strip().lower() for c in (connectors or [])
                      if str(c).strip())
    return _ONLY.set(clean or None)


def release_only(token) -> None:
    with suppressed("releasing this turn's connector ceiling"):
        _ONLY.reset(token)


def scoped_to() -> frozenset[str] | None:
    """The ceiling on this turn, or None when there is not one."""
    return _ONLY.get()


# ── the one-turn grant ───────────────────────────────────────────────────
def allow_for_this_turn(connectors: list[str] | None):
    """Grant these connectors for the current turn. Returns a token for `reset`."""
    clean = frozenset(str(c).strip().lower() for c in (connectors or []) if str(c).strip())
    return _ONCE.set(clean)


def reset(token) -> None:
    with suppressed("releasing this turn's connector grants"):
        _ONCE.reset(token)


def granted_this_turn() -> frozenset[str]:
    return _ONCE.get()


# ── the stored grant ─────────────────────────────────────────────────────
def always_allowed(agent_id: str) -> list[str]:
    """Connectors this agent may use without asking, until revoked."""
    if not agent_id:
        return []
    rows = _conn().execute(
        "SELECT connector FROM agent_connector_grants WHERE agent_id=? "
        "ORDER BY connector", (agent_id,)).fetchall()
    return [r["connector"] for r in rows]


def allow_always(agent_id: str, connector: str) -> dict:
    name = str(connector or "").strip().lower()
    if not agent_id or not name:
        raise ValueError("a grant needs an agent and a connector")
    conn = _conn()
    conn.execute(
        "INSERT INTO agent_connector_grants (agent_id, connector, created_at) "
        "VALUES (?,?,?) ON CONFLICT(agent_id, connector) DO NOTHING",
        (agent_id, name, datetime.now(UTC).isoformat()))
    conn.commit()
    log.info("agent %s may now use %s without asking", agent_id, name)
    return {"agent_id": agent_id, "connector": name, "scope": "always"}


def revoke(agent_id: str, connector: str) -> bool:
    name = str(connector or "").strip().lower()
    conn = _conn()
    cur = conn.execute(
        "DELETE FROM agent_connector_grants WHERE agent_id=? AND connector=?",
        (agent_id, name))
    conn.commit()
    return cur.rowcount > 0


def forget_agent(agent_id: str) -> None:
    """Drop every grant for an agent that no longer exists.

    An agent id is a slug of its name, so it repeats: build "Writer", grant it
    Gmail, delete it, build another "Writer" — and without this the second one
    inherits a permission nobody gave it.
    """
    with suppressed("clearing an agent's connector grants"):
        conn = _conn()
        conn.execute("DELETE FROM agent_connector_grants WHERE agent_id=?", (agent_id,))
        conn.commit()


# ── the question everything else asks ────────────────────────────────────
def unrestricted(agent_id: str) -> bool:
    """Does this agent's template exempt it from asking at all?"""
    with suppressed("checking whether an agent may use connectors freely"):
        from .library import BY_ID

        template = BY_ID.get(agent_id)
        return bool(template and template.unrestricted_connectors)
    return False


def may_use(agent_id: str, connector: str) -> bool:
    """May this agent reach this connector, right now?

    The single place that answers it. Called from the loop before a connector
    tool runs — never from a prompt, because a model told "ask first" will
    sometimes not, and the rule has to hold whatever the model does.
    """
    name = str(connector or "").strip().lower()
    if not name:
        return True                      # not a connector tool; not ours to gate
    # The ceiling is checked FIRST and beats everything under it, including an
    # unrestricted agent. Anywhere else in this function and it would be a
    # suggestion rather than a limit.
    ceiling = scoped_to()
    if ceiling is not None and name not in ceiling:
        return False
    if unrestricted(agent_id):
        return True
    if name in granted_this_turn():
        return True
    return name in set(always_allowed(agent_id))


def describe(agent_id: str) -> dict:
    """What this agent may use, and what it would have to ask for."""
    from .mcp_tools import connector_ids

    from_mcp = list(connector_ids())
    every = from_mcp + [c for c in first_party_labels() if c not in set(from_mcp)]
    ceiling = scoped_to()
    if ceiling is not None:
        # Under a ceiling the answer is the ceiling, whatever the agent holds.
        return {"agent_id": agent_id, "unrestricted": False,
                "allowed": sorted(c for c in every if c in ceiling),
                "must_ask": [], "this_turn": sorted(ceiling),
                "scoped": True}
    if unrestricted(agent_id):
        return {"agent_id": agent_id, "unrestricted": True,
                "allowed": list(every), "must_ask": [], "this_turn": []}
    allowed = set(always_allowed(agent_id))
    turn = granted_this_turn()
    return {
        "agent_id": agent_id,
        "unrestricted": False,
        "allowed": sorted(allowed),
        "must_ask": sorted(c for c in every if c not in allowed),
        "this_turn": sorted(turn),
    }
