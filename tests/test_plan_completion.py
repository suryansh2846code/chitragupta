"""A plan the agent wrote for itself, and whether anyone checks it did it.

Planning has been in the loop since the deeper budget landed, and the plan was
*advisory*: echoed back each round so the shape of the task stays in front of
the model, and then never looked at again. `ROADMAP.md` has carried the gap the
whole time — "nothing checks at the end whether the steps the agent wrote for
itself were actually done."

The failure that gap allows is the expensive kind, because it is invisible. An
agent asked to compare three vendors, check which were already emailed, and
draft the reply writes those three steps down, does the first thoroughly, and
answers. The reply reads exactly like a complete one. The plan that would have
shown otherwise lives in a trace nobody opens.

So the turn no longer ends on an incomplete plan without the agent being told
once. **Once** is the design, not an oversight: a step that is genuinely
impossible — a connector that refused, a page that never loaded — would drive a
second nudge forever.
"""
from __future__ import annotations

import pytest
from agent_harness import RecordingTool, ScriptedProvider

from chitragupta.agents import planning, runtime


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


@pytest.fixture
def tool(monkeypatch):
    def install(name="list_entities", **kw):
        rec = RecordingTool(**kw)
        from chitragupta.agents import tools as tools_mod
        monkeypatch.setitem(tools_mod.TOOL_IMPLS, name, rec)
        return rec
    return install


PLAN_OF_THREE = ("update_plan", {"steps": ["compare vendors",
                                           "check who we emailed",
                                           "draft the reply"],
                                 "done_through": 1})


# ── the helpers ────────────────────────────────────────────────────────────

def test_unfinished_names_exactly_the_steps_left():
    plan = planning.Plan(steps=[planning.PlanStep("a", done=True),
                                planning.PlanStep("b"),
                                planning.PlanStep("c")])
    assert planning.unfinished(plan) == ["b", "c"]


def test_a_finished_plan_leaves_nothing():
    plan = planning.Plan(steps=[planning.PlanStep("a", done=True)])
    assert planning.unfinished(plan) == []


def test_no_plan_at_all_is_not_an_unfinished_one():
    """Low effort does not plan. It must not be nudged about a plan it was
    never asked to write."""
    assert planning.unfinished(None) == []


def test_the_prompt_offers_both_ways_out():
    """"Finish them" alone turns an impossible step into a loop. "Say what you
    skipped" alone gives up on work still doable in one round. The one answer
    that is not acceptable is the third — neither, silently."""
    said = planning.unfinished_prompt(["draft the reply"])
    assert "draft the reply" in said
    assert "do them now" in said
    assert "did not do" in said
    assert "partial answer as a complete one" in said


# ── the loop ───────────────────────────────────────────────────────────────

def test_an_incomplete_plan_gets_one_more_chance(scripted, tool):
    """The whole point: the turn does not end silently on undone work."""
    tool()
    provider = scripted([[PLAN_OF_THREE], "I compared the vendors."],
                        final_answer="I compared the vendors. I did not draft "
                                     "the reply.")
    result = runtime.run_turn("research", "compare and draft", effort="high")
    nudged = [m for call in provider.calls for m in call
              if m.role == "system" and "unfinished" in (m.content or "")]
    assert nudged, "the turn ended on two undone steps without saying so"
    assert "check who we emailed" in nudged[0].content
    assert "draft the reply" in nudged[0].content
    assert result.reply


def test_a_completed_plan_is_not_nudged(scripted, tool):
    """A turn that did what it said must not pay for an extra model call."""
    tool()
    provider = scripted([
        [("update_plan", {"steps": ["look it up"], "done_through": 1})],
        "Looked it up.",
    ])
    runtime.run_turn("research", "look it up", effort="high")
    assert not [m for call in provider.calls for m in call
                if m.role == "system" and "unfinished" in (m.content or "")]


def test_a_turn_with_no_plan_is_not_nudged(scripted, tool):
    tool()
    provider = scripted([[("list_entities", {})], "Here it is."])
    runtime.run_turn("research", "who is bob", effort="low")
    assert not [m for call in provider.calls for m in call
                if m.role == "system" and "unfinished" in (m.content or "")]


def test_the_nudge_happens_at_most_once(scripted, tool):
    """A step that cannot be finished would otherwise drive this forever."""
    tool()
    provider = scripted([[PLAN_OF_THREE], "still not done", "still not done"])
    runtime.run_turn("research", "compare and draft", effort="high")
    nudges = [m for call in provider.calls for m in call
              if m.role == "system" and "unfinished" in (m.content or "")]
    # Every later request replays the same conversation, so the message appears
    # in more than one call — it must only ever have been APPENDED once.
    assert max(sum(1 for m in call if m.role == "system"
                   and "unfinished" in (m.content or ""))
               for call in provider.calls) == 1


def test_a_stopped_turn_is_never_nudged(scripted, tool, monkeypatch):
    """The user asked for it to end, not to finish tidily. A stopped turn does
    not get one more model call — that rule predates this one and outranks it."""
    tool()
    provider = scripted([[PLAN_OF_THREE], "partial"])
    import threading
    stop = threading.Event()
    stop.set()
    runtime.run_turn("research", "compare and draft", effort="high", cancel=stop)
    assert not [m for call in provider.calls for m in call
                if m.role == "system" and "unfinished" in (m.content or "")]


def test_the_agent_may_go_back_to_work_rather_than_apologise(scripted, tool):
    """The useful outcome is that it finishes the job, so the closing round is
    offered tools — not just a chance to word the omission better."""
    rec = tool()
    scripted([[PLAN_OF_THREE], "partial", [("list_entities", {"limit": 3})]])
    runtime.run_turn("research", "compare and draft", effort="high")
    assert rec.executions >= 1
