"""The generalist: every tool, every connected source, and the whole team.

Chief of Staff is the one agent that is not a specialist. It holds everything
this app can do and reaches everything the user has connected — if they
connected it, this agent can use it, with no per-agent grant to set up.

Two things are load-bearing and easy to lose:

* its tool list is a MARKER, not an enumeration — a generalist written out by
  hand stops being one the first time a tool is added and one template is not
  updated;
* it knows who else is on the team while it is still deciding what the job is,
  not only once it has decided to delegate.
"""
from __future__ import annotations

import pytest

from chitragupta.agents.library import (
    BY_ID,
    EVERYTHING,
    add_to_roster,
    describe,
    expand_tools,
    remove_from_roster,
)
from chitragupta.agents.mcp_tools import SENTINEL
from chitragupta.agents.presets import get_agent
from chitragupta.agents.tools import TOOL_DEFS

CHIEF = "chief-of-staff"


@pytest.fixture
def alone():
    """A roster holding only the Chief, so "nobody else" is reachable."""
    from chitragupta.agents import library
    from chitragupta.agents.custom import get_custom_store
    from chitragupta.agents.library import roster
    from chitragupta.core.store import get_store

    before = get_store().get_meta(library.ROSTER_KEY)
    hidden = get_custom_store().list()
    for a in hidden:
        get_custom_store()._c.execute(
            "UPDATE custom_agents SET id = id || '__hidden' WHERE id=?", (a.id,))
    get_custom_store()._c.commit()
    get_store().set_meta(library.ROSTER_KEY, '["chief-of-staff"]')
    yield
    get_custom_store()._c.execute(
        "UPDATE custom_agents SET id = replace(id, '__hidden', '') "
        "WHERE id LIKE '%__hidden'")
    get_custom_store()._c.commit()
    get_store().set_meta(library.ROSTER_KEY, before or "")
    assert roster() is not None


@pytest.fixture
def team():
    for t in (CHIEF, "inbox", "engineer"):
        add_to_roster(t)
    yield
    for t in (CHIEF, "inbox", "engineer"):
        remove_from_roster(t)


# ── everything ───────────────────────────────────────────────────────────
def test_it_holds_every_tool_there_is():
    granted = set(get_agent(CHIEF).tools)
    missing = set(TOOL_DEFS) - granted
    assert not missing, f"the generalist is missing {sorted(missing)}"
    assert SENTINEL in granted, "it cannot reach the user's connectors"


def test_it_has_the_two_that_are_withheld_from_everyone_else():
    """Deliberate: this is the agent the user chose to be able to do anything."""
    granted = set(get_agent(CHIEF).tools)
    assert "run_python" in granted
    assert "forget_fact" in granted


def test_it_is_a_marker_not_a_list():
    """A generalist enumerated by hand stops being one the first time a tool is
    added and this template is not."""
    assert BY_ID[CHIEF].tools == [EVERYTHING]


def test_a_tool_added_tomorrow_reaches_it_without_an_edit():
    grown = dict(TOOL_DEFS)
    grown["a_brand_new_tool"] = object()
    import chitragupta.agents.tools as tools_mod

    saved = tools_mod.TOOL_DEFS
    tools_mod.TOOL_DEFS = grown
    try:
        assert "a_brand_new_tool" in expand_tools([EVERYTHING])
    finally:
        tools_mod.TOOL_DEFS = saved


def test_expanding_leaves_an_ordinary_template_alone():
    assert expand_tools(["search_brain", "web_search"]) == ["search_brain", "web_search"]


# ── the card tells the truth about it ────────────────────────────────────
def test_its_card_says_it_runs_code_and_touches_files():
    """Read from the RESOLVED list. A card reading the marker would tell the
    user it runs no code while handing it the interpreter."""
    row = next(r for r in describe(include_status=False) if r["id"] == CHIEF)
    assert row["runs_code"] is True
    assert row["touches_files"] is True


# ── connectors, with no per-agent grant to set up ────────────────────────
def test_connector_access_needs_no_configuration(monkeypatch):
    """If the user connected it, this agent can use it."""
    from types import SimpleNamespace

    from chitragupta.agents import mcp_tools
    from chitragupta.agents.tools import build_tools

    ref = SimpleNamespace(qualified_name="notion:search", server_id="notion",
                          server_label="Notion", tool="search",
                          description="Search Notion.", parameters={}, writes=False)
    monkeypatch.setattr(mcp_tools, "_supplier",
                        lambda: SimpleNamespace(list_tools=lambda: [ref],
                                                call_tool=lambda *a, **k: "ok"))
    mcp_tools.clear_cache()
    names = {t.name for t in build_tools(get_agent(CHIEF).tools, self_id=CHIEF)}
    assert any("notion" in n for n in names), (
        "a connected source did not reach the generalist")
    mcp_tools.clear_cache()


# ── it knows the team ────────────────────────────────────────────────────
def test_it_is_told_who_else_is_on_the_team(team):
    prompt = get_agent(CHIEF).system_message()
    assert "YOUR TEAM" in prompt
    assert "Inbox" in prompt and "`inbox`" in prompt
    assert "Engineer" in prompt and "code, issues" in prompt
    assert "chief-of-staff" not in prompt.split("YOUR TEAM")[1].split("\n\n")[0], (
        "it lists itself as somebody to ask")


def test_with_nobody_else_it_is_told_so(alone):
    """An agent told nothing about the team assumes there is one, and claims to
    have asked it."""
    prompt = get_agent(CHIEF).system_message()
    assert "nobody else yet" in prompt
    assert "claim you asked" in prompt


def test_the_roster_is_read_per_turn_not_stored(team):
    first = get_agent(CHIEF).system_message()
    assert "Engineer" in first
    remove_from_roster("engineer")
    assert "Engineer" not in get_agent(CHIEF).system_message(), (
        "the team list was baked in, so it will name agents that are gone")


def test_an_agent_that_cannot_delegate_is_not_given_a_roster():
    from chitragupta.agents.prompt import build

    assert build(name="X", role="y", system_prompt="z",
                 tools=["search_brain"], agent_id="x").count("YOUR TEAM") == 0


# ── what it still will not do without a tap ──────────────────────────────
def test_acting_on_the_world_still_goes_through_the_user():
    """Having every tool is not the same as acting unattended."""
    from chitragupta.agents.permissions import NEVER_UNATTENDED, check

    assert "create_routine" in NEVER_UNATTENDED
    assert "mail_triage" in NEVER_UNATTENDED, (
        "the one agent that never asks to READ still asks before it CHANGES")
    # Connector writes are grantable per `server:tool` rather than forbidden
    # outright, so the claim here is the behavioural one: this agent reaches
    # every connector the user owns and still cannot write to one it was not
    # given.
    assert not check(
        "mcp_action", {"server_id": "notion", "tool": "create_page"}).allowed
    # Pinned as "what can this agent do WITHOUT a tap", not as an inventory of
    # its actions. The inventory form broke twice in a row on actions that
    # reach nobody — a draft, a follow-up — which is not the thing this test
    # exists to notice, and each break was fixed by editing the expectation.
    #
    # What must not change quietly is the other half: the set of things Chief
    # of Staff holds that DO touch somebody. An action joining that set is a
    # real widening of the one agent with every tool.
    from chitragupta.actions import REGISTRY, Risk

    held = set(get_agent(CHIEF).actions)
    assert held <= set(REGISTRY), f"unknown action: {sorted(held - set(REGISTRY))}"
    assert {a for a in held if REGISTRY[a].risk is not Risk.GREEN} == {
        "send_email", "create_event", "update_event", "cancel_event",
        "mail_triage", "message_send", "create_routine",
        "github_comment", "github_create_issue",
        # Phase 4. The generalist reaches the work surfaces because it is the
        # one agent with every tool, and an issue it cannot file is a job it
        # hands back. Each is gated on its own terms: Linear against the team,
        # Notion never promotable, Drive sharing against the email list.
        # `drive_create_doc` is absent from this set on purpose — it is GREEN,
        # because a document in the user's own Drive reaches nobody.
        "linear_create_issue", "linear_comment", "linear_update_issue",
        "notion_append", "notion_create_page", "drive_share"}
