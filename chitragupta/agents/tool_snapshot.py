"""What a stored tool list was choosing from — and what it never saw.

An agent's tools can come from a list stored on disk: a custom agent's own, or
the override a user makes on the permission panel. Both are **snapshots**, and
nothing ever added to one. A capability that shipped afterwards was simply
absent, and no release could reach the agent — the user's only route back was
to find the right screen and tick a box they had, from where they were sitting,
already ticked.

That is how a workspace ends up with one agent that can drive the browser and
five that cannot. Chief of Staff was never edited, so it resolves its tools from
the library every time and keeps up; every agent the user had built or adjusted
was frozen at the day it was touched. The user grants "Websites and the
browser", the panel says on, and the agent still reports it has no browser —
because the four page tools that shipped in between are not in the stored list
and the switch above them never covered a name.

**The fix is to record the menu, not just the order.** A list stored from now on
carries the catalog it was chosen from, so a tool that was not on that menu was
never declined — it is undecided, and it takes the agent's default. A tool that
*was* on the menu and is not in the list was turned down, and stays down: this
must never quietly undo a capability somebody removed on purpose.

**The default, for a tool nobody decided about, is the bucket it lands in.**
Not "everything": an agent with no page tools at all gains none. The unit is
`(group, access)` — the exact pair the panel draws as one switch — so a new
read tool joins an agent already allowed to read that group, and never crosses
into changing it, and never crosses into another group.

Legacy rows have no recorded menu. `assumed_menu` reconstructs the only
defensible one: every bucket the list does not touch was on the menu and
declined, and every bucket it does touch was offered in whatever shape it had
then. So the one-time repair fills the buckets the user already said yes to, and
grants nothing anywhere else.

A **leaf**: it reads `tool_facts` and nothing else, so both stores and
`presets` can use it without any of them importing each other.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..log import get_logger
from .tool_facts import builtin_names, tool_access, tool_group

log = get_logger(__name__)


def _bucket(name: str) -> tuple[str, object]:
    """The one switch a person actually sets this tool with.

    `(group, access)` — the pair `tool_facts` already groups the panel by, so
    "Websites and the browser · Read" is one bucket and "· Change" is another.
    Both halves fail closed for an unclassified tool, which is what keeps a
    tool nobody filed from joining a bucket somebody allowed.
    """
    return (tool_group(name), tool_access(name))


def catalog() -> list[str]:
    """Every built-in there is, right now. What a save is choosing from."""
    return sorted(builtin_names())


def assumed_menu(stored: list[str]) -> list[str]:
    """The menu a list stored before we recorded one must have been shown.

    Unknowable exactly, so this takes the reading that cannot widen a decision
    the user made: a bucket they have nothing from was offered and declined in
    full, and a bucket they have something from is credited only with what they
    actually hold. Everything else in a touched bucket is then "not on the
    menu", which is what lets `resolve` fill it.
    """
    known = builtin_names()
    held = {t for t in stored if t in known}
    touched = {_bucket(t) for t in held}
    return sorted(t for t in known if t in held or _bucket(t) not in touched)


def resolve(stored: list[str], menu: list[str] | None) -> list[str]:
    """A stored list, plus the tools that did not exist when it was stored.

    `menu` is the catalog recorded with the list; `None` for a row written
    before we recorded one. Order is preserved and nothing is ever removed —
    a name this module does not recognise (the connector sentinel, a named
    connector tool) passes straight through, because deciding about those is
    `mcp_tools`' job and not this one.
    """
    held = list(stored)
    seen = set(held)
    known = builtin_names()
    was_offered = set(menu if menu is not None else assumed_menu(held))
    # Only buckets this agent already has something in. An agent stripped to
    # nothing stays stripped, which is the distinction `tool_overrides.get`
    # keeps between "untouched" and "deliberately empty".
    touched = {_bucket(t) for t in held if t in known}
    fresh = [t for t in catalog()
             if t not in seen and t not in was_offered and _bucket(t) in touched]
    return held + fresh


# ── recording the menu, in the two tables that hold a list ────────────────
#
# Both helpers live here rather than in either store. Two stores hold the same
# kind of snapshot, and a column one of them reads differently from the other
# is the drift this module exists to end.

def add_menu_column(conn: sqlite3.Connection, table: str) -> None:
    """Give an existing database the catalog column, once.

    `CREATE TABLE IF NOT EXISTS` is a no-op against a table that is already
    there, so a column added to the schema never reaches anyone who installed
    before it. Every such user is exactly the person this fix is for.
    """
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "known" not in cols:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN known TEXT NOT NULL DEFAULT ''")
        conn.commit()


def recorded_menu(row: Any) -> list[str] | None:
    """The catalog recorded with a stored list, or None if it predates one.

    None is the legacy case and `resolve` is the only thing that may interpret
    it. Unreadable is treated as legacy rather than as empty: an empty menu
    would mean "nothing existed when they chose", which would hand the agent
    every tool in every bucket it touches.
    """
    if row is None:
        return None
    try:
        raw = (row["known"] or "").strip()
    except (IndexError, KeyError, TypeError):
        return None                      # a row read before the column existed
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("unreadable tool catalog on a stored list — treating it as old")
        return None
    return [str(t) for t in value] if isinstance(value, list) else None
