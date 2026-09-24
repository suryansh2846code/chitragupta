"""An agent reaching the user's own connectors, mid-turn, read-only.

Three things have to hold at once, and each of them has already been a bug
somewhere in this repo's history:

- a tool that **writes** is never handed to a model (that path is propose →
  confirm, and the reason is recorded in `permissions.py`);
- a tool nobody has heard of still comes back as a sentence, not an exception;
- an agent that did not ask for connector tools gets exactly the built-ins it
  always got — thirteen of them, no more.

The supplying half (`chitragupta.connectors.mcp_tools`) is the Connectors lane's
and may not exist in this checkout, so these tests install a stand-in that
matches the agreed `MCPToolRef` shape exactly. That is the contract being
tested: if the real module drifts from it, this file is where it shows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from agent_harness import ScriptedProvider

from chitragupta.agents import mcp_tools, runtime
from chitragupta.agents.effort import HIGH
from chitragupta.agents.tools import (
    TOOL_DEFS,
    build_tools,
    describe_tools,
    run_tool,
    validate_tool_arguments,
)


@dataclass
class MCPToolRef:
    """The Connectors-side contract, mirrored so a drift is visible here."""

    qualified_name: str
    server_id: str
    server_label: str
    tool: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    writes: bool = False


SEARCH = MCPToolRef(
    qualified_name="notion:search",
    server_id="notion", server_label="Notion", tool="search",
    description="Search the user's Notion workspace.",
    parameters={"type": "object",
                "properties": {"query": {"type": "string"},
                               "limit": {"type": "integer"}},
                "required": ["query"]},
)
PAGES = MCPToolRef(
    qualified_name="notion:list_pages",
    server_id="notion", server_label="Notion", tool="list_pages",
    description="List recent pages.",
    parameters={"type": "object", "properties": {}},
)
CREATE = MCPToolRef(
    qualified_name="linear:create_issue",
    server_id="linear", server_label="Linear", tool="create_issue",
    description="File a new issue.",
    parameters={"type": "object", "properties": {"title": {"type": "string"}}},
    writes=True,
)


class FakeSupplier:
    """A stand-in for the connectors module: lists refs, answers calls."""

    def __init__(self, refs, answer: Any = "one result", boom: Exception | None = None):
        self.refs = list(refs)
        self.answer = answer
        self.boom = boom
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return list(self.refs)

    def call_tool(self, qualified_name: str, arguments: dict):
        self.calls.append((qualified_name, dict(arguments)))
        if self.boom:
            raise self.boom
        return self.answer

    def as_module(self):
        return SimpleNamespace(list_tools=self.list_tools, call_tool=self.call_tool)


@pytest.fixture(autouse=True)
def _connectors_allowed():
    """These tests are about the connector TOOL path, not the permission gate.

    An agent must be allowed before it reaches a connector, so without this
    they would measure the refusal instead of the thing they name. Granted the
    way `@` grants it — for the turn, never stored.
    """
    from chitragupta.agents import connector_grants
    from chitragupta.agents.mcp_tools import connector_ids

    token = connector_grants.allow_for_this_turn(
        ["notion", "linear", "demo", *connector_ids()])
    yield
    connector_grants.reset(token)


@pytest.fixture(autouse=True)
def _someone_to_ask():
    """`ask_agent` is not offered when there is nobody to ask — correct, and
    since nothing is pre-added any more these tests have to put someone there
    or they are measuring a different tool list than they mean to."""
    from chitragupta.agents.library import add_to_roster, remove_from_roster

    add_to_roster("inbox")
    add_to_roster("research")
    yield
    remove_from_roster("inbox")
    remove_from_roster("research")


@pytest.fixture(autouse=True)
def _no_stale_discovery():
    """Discovery is cached for seconds; a test must never inherit another's."""
    mcp_tools.clear_cache()
    yield
    mcp_tools.clear_cache()


@pytest.fixture
def connectors(monkeypatch):
    """Install a fake connector layer and hand the test its recorder."""
    def install(refs=(SEARCH, PAGES, CREATE), **kw):
        fake = FakeSupplier(refs, **kw)
        monkeypatch.setattr(mcp_tools, "_supplier", fake.as_module)
        mcp_tools.clear_cache()
        return fake
    return install


# ── what is offered ──────────────────────────────────────────────────────────

def test_a_write_tool_is_never_offered_to_a_model(connectors):
    """The standing decision in permissions.py, enforced one layer earlier.

    A model cannot propose what it was never handed, so the write filter is not
    a second opinion on `mcp_action` — it is what keeps the question from being
    asked mid-turn at all.
    """
    connectors()
    offered = {t.name for t in build_tools(["search_brain", mcp_tools.SENTINEL],
                                           self_id="inbox", effort=HIGH)}

    assert "notion_search" in offered
    assert "notion_list_pages" in offered
    assert not any("create_issue" in name for name in offered)

    # Not merely unlisted: unreachable. Naming it explicitly does not find it,
    # and running it answers the way an unknown tool does.
    assert mcp_tools.lookup("linear_create_issue") is None
    assert build_tools(["linear_create_issue"], self_id="inbox", effort=HIGH) == []
    assert run_tool("linear_create_issue", {"title": "x"}) == "Unknown tool: linear_create_issue"


def test_an_agent_without_the_sentinel_gets_exactly_the_built_ins(connectors):
    """Opting in is explicit. Adding a connector must not re-tool every agent."""
    fake = connectors()
    names = [t.name for t in build_tools(list(TOOL_DEFS), self_id="inbox", effort=HIGH)]

    assert names == list(TOOL_DEFS)
    # Every built-in and nothing else. Against len(TOOL_DEFS) rather than a
    # literal: the line above already pins the exact set, so a hardcoded count
    # only breaks whenever a tool is added without saying anything new.
    assert len(names) == len(TOOL_DEFS)
    assert fake.calls == []


def test_no_connector_layer_means_no_extra_tools_and_no_error(monkeypatch):
    """A checkout without the connectors module still runs a turn."""
    monkeypatch.setattr(mcp_tools, "_supplier", lambda: None)
    mcp_tools.clear_cache()

    assert mcp_tools.available() == []
    assert mcp_tools.describe() == []
    names = [t.name for t in build_tools([*TOOL_DEFS, mcp_tools.SENTINEL],
                                         self_id="inbox", effort=HIGH)]
    assert names == list(TOOL_DEFS)


def test_a_qualified_name_is_made_safe_for_every_provider(connectors):
    """`notion:search` is a valid MCP name and an invalid tool name upstream."""
    connectors()
    offered = {t.name for t in build_tools([mcp_tools.SENTINEL], effort=HIGH)}

    assert "notion_search" in offered
    assert all(name.replace("_", "").replace("-", "").isalnum() for name in offered)


def test_the_model_is_told_which_connector_a_tool_reaches(connectors):
    connectors()
    tool = mcp_tools.lookup("notion_search")

    assert tool is not None
    assert "Search the user's Notion workspace." in tool.description
    assert "Notion" in tool.description
    assert "read-only" in tool.description


# ── running one ──────────────────────────────────────────────────────────────

def test_arguments_are_cleaned_the_same_way_built_ins_are(connectors):
    connectors()

    ok, err, clean = validate_tool_arguments(
        "notion_search", {"query": "roadmap", "limit": "3", "nonsense": 1})
    assert ok, err
    assert clean == {"query": "roadmap", "limit": 3}      # coerced, and stripped

    ok, err, _ = validate_tool_arguments("notion_search", {"limit": 3})
    assert not ok
    assert "query" in err


def test_a_schema_that_names_no_properties_keeps_its_arguments(connectors):
    """Somebody else's server writes the schema, and it may say nothing.

    Filtering against an absent property list would send every search tool an
    empty query — worse than passing the model's arguments through, because it
    looks like an answer.
    """
    loose = MCPToolRef(qualified_name="slack:search", server_id="slack",
                       server_label="Slack", tool="search",
                       description="Search messages.", parameters={"type": "object"})
    connectors(refs=(loose,))

    ok, err, clean = validate_tool_arguments("slack_search", {"text": "standup"})
    assert ok, err
    assert clean == {"text": "standup"}


def test_a_connector_failure_is_a_sentence_not_an_exception(connectors):
    connectors(boom=RuntimeError("ECONNREFUSED talking to the server"))
    out = run_tool("notion_search", {"query": "roadmap"})

    assert isinstance(out, str)
    assert "ECONNREFUSED" not in out
    assert "Notion" in out


def test_a_structured_answer_is_rendered_for_the_model(connectors):
    connectors(answer={"results": [{"title": "Roadmap"}]})
    assert "Roadmap" in run_tool("notion_search", {"query": "roadmap"})


def test_a_huge_answer_is_capped(connectors):
    connectors(answer="x" * (mcp_tools.MAX_RESULT_CHARS * 3))
    out = run_tool("notion_list_pages", {})

    assert len(out) < mcp_tools.MAX_RESULT_CHARS + 200
    assert "truncated" in out


def test_an_unknown_tool_is_still_the_polite_string(connectors):
    connectors()
    assert run_tool("no_such_tool", {}) == "Unknown tool: no_such_tool"


# ── inside a real turn ───────────────────────────────────────────────────────

def test_a_connector_result_flows_back_into_the_turn(connectors, monkeypatch):
    """The whole point: the model asks, the server answers, the turn uses it."""
    fake = connectors(answer="Roadmap — ships next Tuesday")
    provider = ScriptedProvider(script=[[("notion_search", {"query": "roadmap"})]],
                                final_answer="It ships next Tuesday.")
    monkeypatch.setattr(runtime, "get_provider", lambda p, m: provider)
    monkeypatch.setattr(runtime, "resolve_usable_model", lambda p, m: (m or "scripted-1", None))

    # Attached to the message, the way `@notion` does it. `run_turn` sets the
    # turn's grants from this argument, so an outer grant would be overwritten
    # — which is correct: one message's permission is the message's to carry.
    result = runtime.run_turn("research", "when does the roadmap ship?",
                              effort="medium", connectors=["notion"])

    assert fake.calls == [("notion:search", {"query": "roadmap"})]
    assert "notion_search" in provider.tools_offered[0]
    assert not any("create_issue" in n for n in provider.tools_offered[0])
    results = [s.result for s in result.trace
               if s.kind == "tool_result" and s.name == "notion_search"]
    assert results == ["Roadmap — ships next Tuesday"]
    assert result.reply == "It ships next Tuesday."


# ── what the agent builder is shown ──────────────────────────────────────────

def test_the_tools_listing_keeps_its_old_shape_and_says_where_each_came_from(connectors):
    connectors()
    rows = describe_tools()
    by_name = {r["name"]: r for r in rows}

    # Additive: every built-in row is exactly what it always was, plus labels.
    for name, defn in TOOL_DEFS.items():
        assert by_name[name]["description"] == defn.description
        assert by_name[name]["source"] == "builtin"
        assert by_name[name]["connector"] == ""

    assert by_name["notion_search"]["source"] == "mcp"
    assert by_name["notion_search"]["connector"] == "Notion"
    assert mcp_tools.SENTINEL in by_name          # the category itself is offerable
    assert "linear_create_issue" not in by_name


def test_the_category_is_not_offered_when_there_is_nothing_behind_it(connectors):
    """Never show a control that cannot work."""
    connectors(refs=())
    names = {r["name"] for r in describe_tools()}

    assert names == set(TOOL_DEFS)


# ── the standing decision ────────────────────────────────────────────────────

def test_connector_writes_still_wait_for_a_tap():
    """Exposing a server's READ tools to the loop must not have made its write
    tools runnable — nothing here grants anything."""
    from chitragupta.agents import permissions

    verdict = permissions.check(
        "mcp_action", {"server_id": "linear", "tool": "create_issue"})
    assert not verdict.allowed
    assert "approval" in verdict.reason.lower()
    # **In words, not as the key.** This pinned the literal `linear:create_issue`
    # until a grant learned to name what the tool reaches — the key can now
    # carry a scope, and printing it raw is the internal this project refuses
    # to put in front of a user. Both halves still have to be named, and the
    # id form now has to be absent, which is stricter than what it replaces.
    assert "create_issue" in verdict.reason, (
        "the user is told which tool, not just that something was refused")
    assert "linear" in verdict.reason, "and which connector"
    assert "linear:create_issue" not in verdict.reason, (
        "the grant key is an id and must not reach the user verbatim")


# ── how many at once ─────────────────────────────────────────────────────────

def test_connector_calls_are_bounded_below_the_loops_parallel_width(monkeypatch):
    """A round's tool calls are subprocesses now, not SQLite reads.

    The loop's width (6 at High) is sized for cheap local reads, and lowering it
    would slow every built-in tool to protect a case that only some installs
    have. So the ceiling lives with the connector tools instead: the loop still
    fans out six ways, and no more than `MAX_CONCURRENT_CALLS` of them are
    talking to a server at any moment.
    """
    import threading
    import time

    from chitragupta.agents.loop import ToolRunner
    from chitragupta.models.base import ToolCall

    state = {"live": 0, "peak": 0}
    guard = threading.Lock()

    def slow_call(_name, _arguments):
        with guard:
            state["live"] += 1
            state["peak"] = max(state["peak"], state["live"])
        try:
            time.sleep(0.05)
            return "ok"
        finally:
            with guard:
                state["live"] -= 1

    monkeypatch.setattr(mcp_tools, "_supplier", lambda: SimpleNamespace(
        list_tools=lambda: [SEARCH], call_tool=slow_call))
    mcp_tools.clear_cache()

    runner = ToolRunner(effort=HIGH)
    outcomes = runner.run([ToolCall(id=f"c{i}", name="notion_search",
                                    arguments={"query": f"q{i}"})
                           for i in range(HIGH.max_parallel_tools)])

    assert [o.output for o in outcomes] == ["ok"] * HIGH.max_parallel_tools
    assert state["peak"] <= mcp_tools.MAX_CONCURRENT_CALLS
    assert state["peak"] > 1, "the gate serialised what should still overlap"
