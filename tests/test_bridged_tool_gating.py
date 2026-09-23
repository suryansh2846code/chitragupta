"""A tool run inside a vendor CLI passes the same gates as one run here.

`ToolRunner.run()` is what stands between a model and the user's accounts: it
sets who is acting, refuses a connector this agent may not reach, and validates
arguments. A CLI backend given our tools over MCP never calls `run()` — it runs
its own loop and the calls arrive on a socket — so every one of those promises
had to be made again on that path, and `ToolRunner.invoke()` is where.

The thing that makes this more than a re-export is the **thread**. Both halves
of the permission state live in ContextVars, which is exactly right for the
loop's own thread pool (`copy_context()` carries them) and carries nothing at
all to a thread the CLI's HTTP client opened. A grant that silently stopped
applying is not a smaller permission system; it is a permission system that is
wrong in the direction of *refusing the user what they allowed*, and, for the
unrestricted agent, of allowing nothing at all.
"""
from __future__ import annotations

import threading

import pytest

from chitragupta.agents import connector_grants
from chitragupta.agents.effort import get_effort
from chitragupta.agents.loop import ToolRunner


@pytest.fixture
def mail_tool(monkeypatch):
    """`list_mail` wired to a stub, with Gmail as the connector it reaches.

    The real tool table is left alone: what is under test is the gate, and a
    test that also needed a mailbox would be testing two things and reporting
    one.
    """
    from chitragupta.agents import tools as tools_mod

    ran = []
    monkeypatch.setitem(tools_mod.TOOL_IMPLS, "list_mail",
                        lambda **kw: ran.append(kw) or "2 messages")
    monkeypatch.setitem(tools_mod.TOOL_DEFS, "list_mail", tools_mod.Tool(
        name="list_mail", description="the inbox",
        parameters={"type": "object", "properties": {}}))
    monkeypatch.setattr(connector_grants, "connector_of",
                        lambda name: "gmail" if name == "list_mail" else "")
    return ran


def _runner(agent_id="writer", **kw):
    return ToolRunner(effort=get_effort("medium"), agent_id=agent_id, **kw)


# ── the gate ────────────────────────────────────────────────────────────────


def test_a_connector_the_agent_may_not_reach_is_refused(mail_tool, monkeypatch):
    """The whole point. Without this, handing an agent's tools to a CLI would
    hand it every connector the user has, because the only check was in a
    function that path never calls."""
    monkeypatch.setattr(connector_grants, "may_use", lambda *_: False)

    answer = _runner().invoke("list_mail", {})

    assert "permission" in answer.lower() or "allow" in answer.lower()
    assert mail_tool == [], "the tool ran anyway"


def test_a_connector_the_agent_may_reach_runs(mail_tool, monkeypatch):
    """A gate that refuses everything is not a gate, it is an outage."""
    monkeypatch.setattr(connector_grants, "may_use", lambda *_: True)

    assert _runner().invoke("list_mail", {}) == "2 messages"
    assert mail_tool == [{}]


def test_it_knows_which_agent_is_asking(mail_tool, monkeypatch):
    """`may_use` is asked about an agent, and on this path nothing has set who
    that is — the ContextVar belongs to a thread that is not this one."""
    asked = []
    monkeypatch.setattr(connector_grants, "may_use",
                        lambda agent_id, connector: asked.append(
                            (agent_id, connector)) or True)

    _runner(agent_id="chief-of-staff").invoke("list_mail", {})

    assert asked == [("chief-of-staff", "gmail")]


def test_the_acting_agent_is_visible_to_a_tool_that_gates_itself(mail_tool,
                                                                monkeypatch):
    """A tool reaching whichever app the model named has to ask for itself, and
    `connector_grants.acting()` is how it knows who is asking. Set per call,
    because the call arrives on a thread this process did not start."""
    seen = []
    from chitragupta.agents import tools as tools_mod

    monkeypatch.setitem(tools_mod.TOOL_IMPLS, "list_mail",
                        lambda **_: seen.append(connector_grants.acting()) or "ok")
    monkeypatch.setattr(connector_grants, "may_use", lambda *_: True)

    _runner(agent_id="chief-of-staff").invoke("list_mail", {})

    assert seen == ["chief-of-staff"]


def test_it_is_put_back_afterwards(mail_tool, monkeypatch):
    """Set and never reset, the acting agent leaks into whatever runs next on
    that thread — and the threads here are reused for the next turn's calls."""
    monkeypatch.setattr(connector_grants, "may_use", lambda *_: True)

    _runner(agent_id="chief-of-staff").invoke("list_mail", {})

    assert connector_grants.acting() == ""


# ── the grant the user made, reaching another thread ────────────────────────


def test_an_allow_once_grant_reaches_a_call_on_another_thread(mail_tool):
    """The user clicked *Allow once*, or typed `@gmail`. That grant lives in a
    ContextVar set on the turn's thread; the CLI's call arrives on a socket
    thread that has never seen it. Captured at construction, applied per call.

    Verified red: with `once` dropped from `invoke`, this refuses.
    """
    token = connector_grants.allow_for_this_turn(["gmail"])
    try:
        runner = _runner()                 # captures the grant, on this thread
    finally:
        connector_grants.reset(token)

    answer: list[str] = []
    worker = threading.Thread(
        target=lambda: answer.append(runner.invoke("list_mail", {})))
    worker.start()
    worker.join(timeout=10)

    assert answer == ["2 messages"]


def test_without_the_grant_the_same_call_is_refused(mail_tool):
    """The other half — otherwise the test above would pass on a gate that
    never refuses anything."""
    runner = _runner()

    answer: list[str] = []
    worker = threading.Thread(
        target=lambda: answer.append(runner.invoke("list_mail", {})))
    worker.start()
    worker.join(timeout=10)

    assert mail_tool == []
    assert "2 messages" not in answer[0]


def test_the_turn_grant_is_put_back_after_the_call(mail_tool):
    """`invoke` sets it to run the tool. Leaving it set would grant the next
    turn on that thread whatever this one was allowed."""
    runner = _runner()

    runner.invoke("list_mail", {})

    assert connector_grants.granted_this_turn() == frozenset()


# ── the rest of what the loop does ──────────────────────────────────────────


def test_a_repeated_call_gets_the_answer_and_a_nudge(mail_tool, monkeypatch):
    """A CLI's own loop can circle exactly as ours can, and it is cheaper to
    hand back what it already has than to read the mailbox twice."""
    monkeypatch.setattr(connector_grants, "may_use", lambda *_: True)
    runner = _runner()

    first = runner.invoke("list_mail", {})
    second = runner.invoke("list_mail", {})

    assert first == "2 messages"
    assert "already ran this" in second
    assert len(mail_tool) == 1, "the tool ran a second time"
    assert runner.repeats_seen == 1


def test_a_stopped_turn_runs_nothing_more(mail_tool, monkeypatch):
    """Anything the user starts they can stop — including the half of a turn
    that is happening inside somebody else's process."""
    monkeypatch.setattr(connector_grants, "may_use", lambda *_: True)
    stop = threading.Event()
    stop.set()

    answer = _runner(cancel=stop).invoke("list_mail", {})

    assert mail_tool == []
    assert "stopped" in answer.lower()


def test_bad_arguments_are_a_sentence_not_an_exception(mail_tool, monkeypatch):
    """The caller is a model on the far side of an HTTP boundary. An exception
    there is a turn that ends; a sentence is one it can recover from."""
    monkeypatch.setattr(connector_grants, "may_use", lambda *_: True)

    answer = _runner().invoke("no_such_tool_at_all", {})

    assert "unknown tool" in answer.lower()


def test_an_unknown_tool_is_not_remembered_as_an_answer(mail_tool, monkeypatch):
    """Counted, so `calls_made` reflects what the CLI actually did with the
    turn — it is what the budget and the stall check are read from."""
    monkeypatch.setattr(connector_grants, "may_use", lambda *_: True)
    runner = _runner()

    runner.invoke("list_mail", {})

    assert runner.calls_made == 1
