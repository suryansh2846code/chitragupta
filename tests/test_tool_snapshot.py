"""A stored tool list must not be frozen at the day it was stored.

From a real workspace: of six agents, exactly one could drive the browser —
Chief of Staff, the only one the user had never edited. It resolves its tools
from the library on every call, so it picked up every page tool as it shipped.
The other five held a stored list: a custom agent's own, or the override the
permission panel writes. Those lists were snapshots. `browse_wait`,
`browse_select`, `browse_press` and `what_i_looked_at` shipped on 2026-10-01
and could never reach an agent built on 2026-09-27, because the switch above
them grants *names* and the new names were not in the list.

So the user turned "Websites and the browser" on, the panel said on, and the
agent still answered that it had no browser and sent them back to the same
screen. Which is the dead end `tool_facts.Group.more_screen` exists to stop,
arriving from the other direction.

The rule under the fix: a save records the catalog it was chosen from, and a
tool that was not on that menu is **undecided**, not declined. Undecided takes
the agent's default, and the default is the bucket the panel draws as one
switch — `(group, access)`. Never a different group, never a different access
level, and never anything at all for an agent that holds nothing in it.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from chitragupta.agents.custom import get_custom_store
from chitragupta.agents.presets import PRESETS, get_agent
from chitragupta.agents.tool_facts import (
    GROUPS,
    builtin_names,
    tool_access,
    tool_group,
)
from chitragupta.agents.tool_overrides import get_tool_overrides
from chitragupta.agents.tool_snapshot import catalog, recorded_menu, resolve

#: The browser tools as `library._BROWSE` had them on 2026-09-27 — what a
#: custom agent built that day actually stored. The four that shipped on
#: 2026-10-01 are the ones that could never arrive.
SEPTEMBER_BROWSE = ["browse_sites", "browse_open", "browse_read", "browse_find",
                    "browse_back", "browse_reveal",
                    "browse_click", "browse_type", "browse_submit"]
SHIPPED_LATER = ["browse_wait", "browse_select", "browse_press",
                 "what_i_looked_at"]


def _bucket(name: str) -> tuple:
    return (tool_group(name), tool_access(name))


def _buckets() -> list[tuple]:
    """Every (group, access) pair that has a built-in in it."""
    return sorted({_bucket(n) for n in builtin_names()},
                  key=lambda b: (b[0], b[1].value))


# ── the rule, as a shape rather than as the browser ───────────────────────

@pytest.mark.parametrize("bucket", _buckets(), ids=lambda b: f"{b[0]}:{b[1].value}")
def test_a_tool_that_was_not_on_the_menu_joins_a_bucket_the_agent_holds(bucket):
    """Parametrised over every bucket, because the browser was only the one
    the user happened to notice. Any capability shipped after a save had the
    same problem in whichever group it landed in."""
    members = sorted(n for n in builtin_names() if _bucket(n) == bucket)
    if len(members) < 2:
        pytest.skip("a bucket of one cannot be half-stored")
    newcomer, *had = members
    menu = [n for n in catalog() if n != newcomer]   # it did not exist yet

    assert newcomer in resolve(had, menu)


@pytest.mark.parametrize("bucket", _buckets(), ids=lambda b: f"{b[0]}:{b[1].value}")
def test_a_tool_on_the_menu_and_left_out_stays_left_out(bucket):
    """The half that already worked, and must keep working: a capability the
    user turned off is a decision, and topping up must never undo one."""
    members = sorted(n for n in builtin_names() if _bucket(n) == bucket)
    if len(members) < 2:
        pytest.skip("a bucket of one cannot be half-stored")
    declined, *had = members

    assert declined not in resolve(had, catalog())


def test_nothing_is_granted_in_a_bucket_the_agent_has_nothing_in():
    """Undecided takes the *default*, and the default for a group an agent was
    never given anything in is still nothing."""
    out = resolve(["search_brain"], menu=[])
    assert not [t for t in out if tool_group(t) == "mac"]
    assert "browse_open" not in out


def test_an_agent_stripped_to_nothing_stays_stripped():
    """`[]` is a decision and `None` is an absence — the distinction
    `tool_overrides.get` keeps, and this must not blur it."""
    assert resolve([], menu=None) == []


def test_names_we_do_not_own_pass_through_untouched():
    """The connector sentinel and named connector tools are `mcp_tools`' to
    decide about, so they survive in place rather than being filtered."""
    out = resolve(["mcp", "notion:search", "search_brain"], menu=catalog())
    assert out[:3] == ["mcp", "notion:search", "search_brain"]


# ── the legacy repair: rows stored before a menu was recorded ─────────────

def test_a_list_stored_before_we_recorded_a_menu_fills_the_buckets_it_touches():
    """The agent from the report: built in September, holding the browse tools
    as they were that day. Without the fix the four from October never arrive."""
    out = resolve([*SEPTEMBER_BROWSE, "search_brain"], menu=None)
    for name in SHIPPED_LATER:
        assert name in out, f"{name} never reached an agent built before it"


def test_the_legacy_repair_never_crosses_a_group_or_an_access_level():
    """Filling a bucket is as far as it goes. A browser grant is not a file
    grant, and reading a page is not changing one."""
    out = set(resolve([*SEPTEMBER_BROWSE], menu=None)) - set(SEPTEMBER_BROWSE)
    assert out, "the repair did nothing at all"
    assert {_bucket(t)[0] for t in out} == {"websites"}


# ── the stores: the menu is recorded, and an old database gains the column ─

def test_an_override_records_the_catalog_it_was_chosen_from():
    store = get_tool_overrides()
    try:
        store.set("snapshot-test", ["search_brain"])
        assert store.menu("snapshot-test") == catalog()
    finally:
        store.clear("snapshot-test")


def test_an_override_written_before_this_release_reaches_the_new_tools(tmp_path):
    """End to end through `get_agent`, which is what a turn actually calls."""
    agent_id = next(iter(PRESETS))
    store = get_tool_overrides()
    try:
        # Written the old way: no catalog recorded, so every row on a machine
        # that upgrades looks like this.
        store.set(agent_id, [*SEPTEMBER_BROWSE, "search_brain"])
        store._c.execute(
            "UPDATE agent_tool_overrides SET known='' WHERE agent_id=?",
            (agent_id,))
        store._c.commit()
        tools = get_agent(agent_id).tools
        assert "browse_wait" in tools
    finally:
        store.clear(agent_id)


def test_a_database_without_the_column_gains_it(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` is a no-op against a table that is already
    there, so without the migration nobody who installed before this release
    would ever get the column — and they are exactly who the fix is for."""
    from chitragupta.agents.tool_snapshot import add_menu_column

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE agent_tool_overrides "
                 "(agent_id TEXT PRIMARY KEY, tools TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO agent_tool_overrides VALUES ('a','[]','now')")
    conn.commit()

    row = conn.execute("SELECT * FROM agent_tool_overrides").fetchone()
    assert recorded_menu(row) is None          # a row read before the column

    add_menu_column(conn, "agent_tool_overrides")
    row = conn.execute("SELECT * FROM agent_tool_overrides").fetchone()
    assert recorded_menu(row) is None          # present, empty: still legacy
    add_menu_column(conn, "agent_tool_overrides")   # and it is idempotent


def test_a_custom_agent_built_before_a_tool_shipped_still_gets_it():
    """The other store with the same snapshot, and the one five of the six
    agents on the reported machine were using."""
    store = get_custom_store()
    agent = store.create("Snapshot Probe", tools=[*SEPTEMBER_BROWSE, "search_brain"])
    try:
        store._c.execute("UPDATE custom_agents SET known='' WHERE id=?",
                         (agent.id,))
        store._c.commit()
        assert "browse_wait" in store.get(agent.id).tools
    finally:
        store.delete(agent.id)


def test_a_custom_agent_records_its_menu_at_creation():
    store = get_custom_store()
    agent = store.create("Snapshot Probe 2", tools=["search_brain"])
    try:
        row = store._c.execute("SELECT * FROM custom_agents WHERE id=?",
                               (agent.id,)).fetchone()
        assert recorded_menu(row) == catalog()
    finally:
        store.delete(agent.id)


def test_every_group_the_panel_draws_can_be_filled():
    """A guard on the vocabulary rather than on the fix: a group with no
    built-in in it is a heading the user can switch and nothing can satisfy."""
    covered = {tool_group(n) for n in builtin_names()}
    assert {g.key for g in GROUPS} <= covered


def test_the_stored_json_is_a_list_of_names():
    store = get_tool_overrides()
    try:
        store.set("snapshot-test-2", ["search_brain"])
        row = store._c.execute(
            "SELECT known FROM agent_tool_overrides WHERE agent_id=?",
            ("snapshot-test-2",)).fetchone()
        assert isinstance(json.loads(row["known"]), list)
    finally:
        store.clear("snapshot-test-2")
