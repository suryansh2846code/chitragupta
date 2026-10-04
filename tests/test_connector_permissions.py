"""An agent asks before it reaches a connector — except the one that does not.

Holding the connector category used to mean reading everything the user had
connected, on any turn, without saying so. Right when the category was the only
way in; wrong once agents are specialists somebody assembles, because adding a
Writer should not hand it the inbox.

Contract: docs/development/connector-permissions.md
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_harness import ScriptedProvider

from chitragupta.agents import connector_grants, mcp_tools, runtime
from chitragupta.agents.effort import get_effort
from chitragupta.agents.library import BY_ID
from chitragupta.agents.loop import NEEDS_PERMISSION, ToolRunner
from chitragupta.models.base import ToolCall

NOTION = SimpleNamespace(
    qualified_name="notion:search", server_id="notion", server_label="Notion",
    tool="search", description="Search Notion.", parameters={}, writes=False)


@pytest.fixture(autouse=True)
def connected(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_supplier",
                        lambda: SimpleNamespace(list_tools=lambda: [NOTION],
                                                call_tool=lambda *a, **k: "a page"))
    mcp_tools.clear_cache()
    yield
    mcp_tools.clear_cache()


@pytest.fixture(autouse=True)
def _no_leaked_grants():
    yield
    for agent_id in ("inbox", "writer", "research"):
        connector_grants.revoke(agent_id, "notion")


def _tool_name():
    return next(iter(mcp_tools.definitions()))


def _run(agent_id):
    runner = ToolRunner(effort=get_effort("medium"), agent_id=agent_id)
    return runner.run([ToolCall(id="c1", name=_tool_name(), arguments={})])[0]


# ── the gate ─────────────────────────────────────────────────────────────
def test_an_agent_must_ask_before_reaching_a_connector():
    out = _run("inbox")
    assert not out.ok
    assert "Needs the user's permission" in out.output
    assert "Notion" in out.output, "it does not say which connector"


def test_the_refusal_tells_the_model_what_to_do_with_it():
    """An agent that just sees "failed" tries another connector."""
    assert "Ask them for it in your reply" in NEEDS_PERMISSION
    assert "Do not try a different connector instead" in NEEDS_PERMISSION
    assert "do not answer as though" in NEEDS_PERMISSION


def test_the_refusal_says_where_the_user_turns_it_on():
    """"Please grant Google Calendar read access" is not an instruction anybody
    can follow. An error that names no control is a dead end, and the user is
    left believing the app is broken rather than that a switch is off.

    It used to name "Settings, then Agents & tools". That screen is the agent's
    own profile now — and, worse, naming it was what made the browser bug
    unfixable from the user's side: the switch there was already on, so five
    trips to it changed nothing. The route has to name the **card**, which is
    where the grant actually is.

    Asserted against `signposts.PERMISSIONS` rather than the words, because the
    literal is what let the old screen's name survive its own deletion in four
    strings at once."""
    from chitragupta.agents import signposts

    assert signposts.PERMISSIONS in NEEDS_PERMISSION
    assert "⋯" in NEEDS_PERMISSION, "it names no control the user can press"
    assert "card" in NEEDS_PERMISSION


def test_the_refusal_does_not_send_the_user_to_a_switch_that_is_already_on():
    """The grant and the read switch are two permissions, and the read switch
    is usually already on — so a refusal that says "turn it on" sends somebody
    to flip something that was never the problem. That is the loop this
    sentence exists to break, and it ran five times on one machine."""
    assert "second permission" in NEEDS_PERMISSION
    assert "already on" in NEEDS_PERMISSION


def test_the_refusal_forbids_naming_the_tool():
    """It shipped as "reading it (`calendar_lookup`) is still blocked". The
    user has never seen a tool name; one in a sentence about their calendar
    reads as a fault in the app, not as something they can fix."""
    assert "Never name a tool" in NEEDS_PERMISSION


def test_the_gate_is_at_execution_not_in_the_prompt():
    """A model told "ask first" will sometimes not."""
    ran = {"n": 0}
    import chitragupta.agents.loop as loop_mod

    saved = loop_mod.run_tool
    loop_mod.run_tool = lambda *a, **k: ran.__setitem__("n", ran["n"] + 1)
    try:
        _run("inbox")
    finally:
        loop_mod.run_tool = saved
    assert ran["n"] == 0, "the connector was reached despite no permission"


# ── always ───────────────────────────────────────────────────────────────
def test_always_allow_is_remembered():
    connector_grants.allow_always("inbox", "notion")
    assert _run("inbox").ok
    assert connector_grants.always_allowed("inbox") == ["notion"]


def test_a_grant_is_for_one_agent_only():
    connector_grants.allow_always("inbox", "notion")
    assert _run("inbox").ok
    assert not _run("writer").ok, "granting one agent granted another"


def test_revoking_takes_it_back():
    connector_grants.allow_always("inbox", "notion")
    assert connector_grants.revoke("inbox", "notion") is True
    assert not _run("inbox").ok


def test_a_refusal_is_not_remembered_across_the_grant():
    """A cached refusal would outlive the permission being granted."""
    runner = ToolRunner(effort=get_effort("medium"), agent_id="inbox")
    call = ToolCall(id="c1", name=_tool_name(), arguments={})
    assert not runner.run([call])[0].ok
    connector_grants.allow_always("inbox", "notion")
    assert runner.run([call])[0].ok, "the refusal was memoised"


# ── once ─────────────────────────────────────────────────────────────────
def test_a_one_message_grant_works_and_is_not_stored():
    token = connector_grants.allow_for_this_turn(["notion"])
    try:
        assert _run("inbox").ok
    finally:
        connector_grants.reset(token)
    assert connector_grants.always_allowed("inbox") == [], (
        '"just this time" became something the user must remember to undo')
    assert not _run("inbox").ok


def test_a_turns_grant_does_not_leak_into_the_next(monkeypatch):
    provider = ScriptedProvider(script=[], final_answer="ok")
    monkeypatch.setattr(runtime, "get_provider", lambda p, m: provider)
    monkeypatch.setattr(runtime, "resolve_usable_model", lambda p, m: (m or "x", None))

    runtime.run_turn("inbox", "look it up", connectors=["notion"], persist=False)
    assert connector_grants.granted_this_turn() == frozenset(), (
        "one message's grant survived the turn")


# ── the declared exception ───────────────────────────────────────────────
def test_the_generalist_never_stops_to_ask():
    assert BY_ID["chief-of-staff"].unrestricted_connectors is True
    assert _run("chief-of-staff").ok


def test_it_is_the_only_one():
    loose = [t.id for t in BY_ID.values() if t.unrestricted_connectors]
    assert loose == ["chief-of-staff"], f"unexpected agents skip the ask: {loose}"


def test_the_card_says_which_agent_that_is():
    from chitragupta.agents.library import describe

    cards = {c["id"]: c for c in describe(include_status=False)}
    assert cards["chief-of-staff"]["unrestricted_connectors"] is True
    assert cards["inbox"]["unrestricted_connectors"] is False


# ── housekeeping ─────────────────────────────────────────────────────────
def test_a_deleted_agent_leaves_no_permission_behind():
    """Ids are slugs of names, so they repeat — a grant left behind would apply
    to an agent nobody gave it to."""
    from chitragupta.agents.custom import get_custom_store

    store = get_custom_store()
    made = store.create("Grant Test Agent", tools=["search_brain"])
    connector_grants.allow_always(made.id, "notion")
    assert connector_grants.always_allowed(made.id) == ["notion"]
    store.delete(made.id)
    assert connector_grants.always_allowed(made.id) == []


def test_a_non_connector_tool_is_not_gated():
    runner = ToolRunner(effort=get_effort("medium"), agent_id="inbox")
    out = runner.run([ToolCall(id="c", name="list_entities", arguments={})])[0]
    assert "Needs the user's permission" not in out.output
