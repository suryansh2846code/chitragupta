"""One agent asking another — and the three guards that stop it running away.

Until now "four agents, one brain" was half true: they shared a database and
never spoke, so the user did the routing by hand, reading an answer in one tab
and pasting it into another.

An agent that can call an agent can call itself, so the interesting tests here
are the refusals.
"""
import pytest
from agent_harness import ScriptedProvider

from chitragupta.agents import delegation, runtime
from chitragupta.agents.effort import get_effort
from chitragupta.agents.tools import build_tools, run_tool


@pytest.fixture(autouse=True)
def _clean_chain():
    token = delegation._CHAIN.set(None)
    yield
    delegation._CHAIN.reset(token)


@pytest.fixture
def scripted(monkeypatch):
    def make(script, **kw):
        provider = ScriptedProvider(script=list(script), **kw)
        monkeypatch.setattr("chitragupta.agents.runtime.get_provider",
                            lambda p, m: provider)
        monkeypatch.setattr("chitragupta.agents.runtime.resolve_usable_model",
                            lambda p, m: (m or "scripted-1", None))
        return provider
    return make


# ── it works ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _a_team_to_delegate_to():
    """Delegation needs somebody to delegate TO, and nothing is pre-added any
    more — an empty roster means `ask_agent` is correctly never offered, which
    would make these tests pass for the wrong reason."""
    from chitragupta.agents.library import add_to_roster, remove_from_roster

    team = ("inbox", "research", "personal", "writer")
    for tid in team:
        add_to_roster(tid)
    yield
    for tid in team:
        remove_from_roster(tid)


def test_an_agent_can_ask_another_and_gets_its_answer(scripted):
    scripted([[("ask_agent", {"agent_id": "research",
                              "question": "what is the harbour plan?"})],
              "Research says it's on track."],
             final_answer="The harbour plan is on track.")
    res = runtime.run_turn("inbox", "check with research", effort="high")
    assert "research answered" in " ".join(
        s.result for s in res.trace if s.kind == "tool_result")
    assert res.reply


def test_the_tool_names_the_other_agents_so_the_model_need_not_guess(monkeypatch):
    """A model that has to discover the roster spends a round doing it."""
    tools = build_tools(["ask_agent"], self_id="inbox")
    assert tools, "ask_agent was not offered"
    description = tools[0].description
    assert "research" in description and "inbox" not in description.split("Available agents:")[1]


def test_an_agent_is_never_offered_itself():
    ids = delegation.available_agents(exclude="inbox")
    assert "inbox" not in ids and ids


# ── the guards ───────────────────────────────────────────────────────────────

def test_low_effort_switches_delegation_off():
    """Delegation multiplies cost, so the cheapest gear does not offer it."""
    token = delegation.enter("inbox", get_effort("low"))
    try:
        assert "switched off" in (delegation.refusal("research") or "")
    finally:
        delegation.leave(token)


def test_an_agent_cannot_be_asked_twice_in_one_chain():
    """Inbox → Research → Inbox is a loop that ends only when the budget does."""
    t1 = delegation.enter("inbox", get_effort("high"))
    t2 = delegation.enter("research", get_effort("high"))
    try:
        assert "already working on this question" in (delegation.refusal("inbox") or "")
    finally:
        delegation.leave(t2)
        delegation.leave(t1)


def test_the_chain_cannot_grow_past_the_effort_limit():
    effort = get_effort("medium")            # one hop
    t1 = delegation.enter("inbox", effort)
    t2 = delegation.enter("research", effort)
    try:
        assert "which is the limit" in (delegation.refusal("personal") or "")
    finally:
        delegation.leave(t2)
        delegation.leave(t1)


def test_an_unknown_agent_is_refused_with_the_real_list():
    token = delegation.enter("inbox", get_effort("high"))
    try:
        msg = delegation.refusal("marketing") or ""
        assert "no agent called" in msg
        # The real roster, by the names a model reads in its prompt — so the
        # next attempt can name one rather than guessing again.
        assert "research" in msg.lower()
    finally:
        delegation.leave(token)


def test_a_refusal_is_prose_the_model_can_act_on():
    """Returned, not raised: the caller is a model, and a sentence produces a
    better next move than an exception the loop has to translate."""
    token = delegation.enter("inbox", get_effort("low"))
    try:
        out = run_tool("ask_agent", {"agent_id": "research", "question": "hi"})
    finally:
        delegation.leave(token)
    assert "switched off" in out
    assert "Traceback" not in out


# ── the guards survive the thread pool ───────────────────────────────────────

def test_the_chain_survives_parallel_tool_calls(scripted):
    """`ask_agent` reads the chain from a ContextVar, and tool calls run in a
    thread pool — which does not copy context. Without propagation every
    parallel call restarts at depth zero and both guards above are decoration.
    """
    seen = []

    def _spy(agent_id: str, question: str) -> str:
        seen.append(delegation.current_chain().agents)
        return "ok"

    from chitragupta.agents import tools as tools_mod
    original = tools_mod.TOOL_IMPLS["ask_agent"]
    tools_mod.TOOL_IMPLS["ask_agent"] = _spy
    try:
        scripted([[("ask_agent", {"agent_id": "research", "question": "a"}),
                   ("ask_agent", {"agent_id": "personal", "question": "b"}),
                   ("ask_agent", {"agent_id": "writer", "question": "c"})],
                  "done"])
        runtime.run_turn("inbox", "ask everyone", effort="high")
    finally:
        tools_mod.TOOL_IMPLS["ask_agent"] = original

    assert seen, "ask_agent never ran"
    assert all(chain and chain[-1] == "inbox" for chain in seen), (
        f"the chain was lost in the thread pool: {seen}")


def test_the_chain_is_left_even_when_the_model_fails(scripted):
    """A ContextVar set and never reset leaks this agent into whatever the
    caller does next."""
    class Broken(ScriptedProvider):
        def chat(self, *a, **kw):
            raise RuntimeError("model down")

    import chitragupta.agents.runtime as rt
    broken = Broken(script=[])
    rt_get = rt.get_provider
    rt.get_provider = lambda p, m: broken
    try:
        runtime.run_turn("inbox", "hello", effort="high")
    finally:
        rt.get_provider = rt_get
    assert delegation.current_chain().agents == (), "the chain leaked"


# ── budget ───────────────────────────────────────────────────────────────────

def test_a_sub_agent_gets_a_smaller_budget_than_its_parent():
    """Without this a chain of three at High could turn one question into
    dozens of model calls."""
    parent = get_effort("high")
    child = parent.child()
    assert child.max_steps < parent.max_steps
    assert child.max_delegation_depth == parent.max_delegation_depth - 1
    assert child.child().max_delegation_depth == 0, "the chain must bottom out"
