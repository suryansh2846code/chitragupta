"""A library you choose from, not four agents that were simply there.

Inbox, Launch, Research and Personal were all present and none of them chosen.
Somebody who had connected Gmail and nothing else still got a go-to-market
operator in their sidebar, and an agent nobody picked is one nobody opens.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import library
from chitragupta.agents.agent import AgentMemory
from chitragupta.agents.library import (
    BY_ID,
    CATEGORIES,
    DEFAULT_ROSTER,
    TEMPLATES,
    add_to_roster,
    describe,
    remove_from_roster,
    roster,
)
from chitragupta.agents.presets import get_agent, list_agents
from chitragupta.agents.prompt import KNOWN_ACTIONS
from chitragupta.agents.tools import TOOL_DEFS, build_tools


@pytest.fixture(autouse=True)
def clean_roster():
    from chitragupta.core.store import get_store
    before = get_store().get_meta(library.ROSTER_KEY)
    yield
    get_store().set_meta(library.ROSTER_KEY, before or "")
    if not before:
        get_store().set_meta(library.ROSTER_KEY, "")


# ── the shape of the library ─────────────────────────────────────────────
def test_every_template_is_coherent():
    for t in TEMPLATES:
        assert t.category in CATEGORIES, f"{t.id} is on no shelf"
        assert t.description.strip(), f"{t.id} has no card line"
        assert len(t.description) < 160, (
            f"{t.id}'s card line will not fit on a card")
        assert t.system_prompt.strip()
        assert set(t.needs) <= set(t.works_with), (
            f"{t.id} needs a source it does not claim to work with")
        for action in t.actions:
            assert action in KNOWN_ACTIONS, f"{t.id} declares unknown {action}"


def test_every_declared_tool_actually_exists():
    """A typo in a template would silently give an agent one fewer hand."""
    from chitragupta.agents.mcp_tools import SENTINEL

    for t in TEMPLATES:
        # Against what it GRANTS, not what it declares: the generalist holds a
        # marker, and validating the marker would either fail or — worse — pass
        # while never checking the thing it expands to.
        for name in t.resolved_tools():
            assert name == SENTINEL or name in TOOL_DEFS, (
                f"{t.id} asks for '{name}', which is not a tool")


def test_the_library_spans_the_shelves():
    used = {t.category for t in TEMPLATES}
    assert len(used) >= 5, f"the library only fills {len(used)} shelves"


def test_an_agent_with_nothing_to_send_is_not_taught_how():
    """Nothing outbound, rather than nothing at all — a reminder and a routine
    reach nobody, and an agent that cannot notice anything is half an agent."""
    from chitragupta.agents.permissions import OUTBOUND_ACTIONS

    researcher = BY_ID["research"]
    assert not set(researcher.actions) & OUTBOUND_ACTIONS
    assert "send_email" not in researcher.to_agent().system_message()


# ── the roster ───────────────────────────────────────────────────────────
def test_a_new_user_is_given_no_agents_at_all():
    """They are chosen, not issued. Onboarding still builds a lead agent, so
    the first run is one agent that knows them plus a library to pick from."""
    assert DEFAULT_ROSTER == []
    assert len(TEMPLATES) >= 8, "there is barely anything to choose from"


def test_adding_and_removing_changes_who_is_in_the_sidebar():
    assert "engineer" not in {a.id for a in list_agents()}

    add_to_roster("engineer")
    assert "engineer" in {a.id for a in list_agents()}

    remove_from_roster("engineer")
    assert "engineer" not in {a.id for a in list_agents()}


def test_adding_twice_does_not_duplicate():
    add_to_roster("engineer")
    add_to_roster("engineer")
    assert roster().count("engineer") == 1


def test_adding_something_that_is_not_in_the_library_is_refused():
    with pytest.raises(KeyError):
        add_to_roster("not-a-real-agent")


def test_removing_an_agent_keeps_its_conversation():
    """Removing is not deleting — adding it back returns you where you were."""
    add_to_roster("engineer")
    AgentMemory().append("engineer", "user", "a message worth keeping")

    remove_from_roster("engineer")
    kept = AgentMemory().history("engineer", limit=10)
    assert any("worth keeping" in (r["content"] or "") for r in kept)

    add_to_roster("engineer")
    assert "engineer" in {a.id for a in list_agents()}


def test_a_removed_agent_still_resolves():
    """A routine made while it was in the roster must not break."""
    remove_from_roster("inbox")
    assert get_agent("inbox").id == "inbox"


def test_a_roster_naming_a_retired_template_does_not_break_the_sidebar():
    from chitragupta.core.store import get_store

    get_store().set_meta(library.ROSTER_KEY, '["inbox", "a-template-we-retired"]')
    assert roster() == ["inbox"]


# ── the cards ────────────────────────────────────────────────────────────
def test_a_card_says_what_it_needs_before_it_is_added():
    """Never offer a control that cannot work."""
    rows = {r["id"]: r for r in describe()}
    inbox = rows["inbox"]
    assert "gmail" in inbox["needs"]
    assert isinstance(inbox["missing"], list)
    if not inbox["missing"]:
        return
    assert set(inbox["missing"]) <= set(inbox["needs"])


def test_a_card_says_when_an_agent_runs_code_or_touches_files():
    """Adding an agent IS the consent, so the consent has to be informed."""
    rows = {r["id"]: r for r in describe(include_status=False)}
    assert rows["engineer"]["runs_code"] is True
    assert rows["engineer"]["touches_files"] is True
    assert rows["research"]["runs_code"] is False


def test_the_cards_report_who_is_already_in_the_roster():
    add_to_roster("engineer")
    rows = {r["id"]: r for r in describe(include_status=False)}
    assert rows["engineer"]["in_roster"] is True
    remove_from_roster("engineer")
    rows = {r["id"]: r for r in describe(include_status=False)}
    assert rows["engineer"]["in_roster"] is False


# ── delegation follows the roster ────────────────────────────────────────
def test_agents_can_only_ask_the_agents_the_user_actually_has():
    """Otherwise ask_agent offers a catalogue rather than this person's team."""
    from chitragupta.agents.delegation import roster as agent_roster

    remove_from_roster("engineer")
    assert "engineer" not in agent_roster()
    add_to_roster("engineer")
    assert "engineer" in agent_roster()


def test_an_agent_you_add_really_gets_its_tools():
    for tid in ("chief-of-staff", "inbox", "research"):
        add_to_roster(tid)
        agent = get_agent(tid)
        names = {t.name for t in build_tools(agent.tools, self_id=tid)}
        assert "search_brain" in names and "who_is" in names, f"{tid} is toolless"


def test_there_is_no_lead_agent_any_more():
    """It was a second definition of Chief of Staff, built from a hardcoded
    tool list with no connector access and a prompt naming three specialists a
    new user does not have. Two definitions of one role is how one goes stale."""
    from chitragupta.api.routes import agents as routes

    assert not hasattr(routes, "create_lead_agent")
    assert not hasattr(routes, "_fallback_welcome")
    assert "chief-of-staff" in BY_ID, "the role has to live somewhere"


def test_every_template_can_ask_for_permission():
    """`_PROACTIVE`'s own comment says every agent needs this — "one that
    cannot ask can only describe a settings screen, and the one that tried
    invented the path it described". Three templates spelled their actions out
    by hand and so never picked it up, Health & Fitness among them: the agent
    somebody spent five attempts trying to give the browser to. It could
    describe the problem exactly and had no way to ask for the fix."""
    from chitragupta.agents.library import BY_ID

    missing = sorted(t.id for t in BY_ID.values()
                     if "request_permission" not in (t.actions or []))
    assert not missing, (
        f"{missing} can only describe a settings screen — start from "
        "_PROACTIVE and add, never retype it")
