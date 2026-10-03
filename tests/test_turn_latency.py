"""The reply does not wait on the brain catching up.

Two model calls used to run after the last word arrived and before the turn
returned: one extracting durable facts, one curating them. Neither changes the
answer, so the user watched the text finish and then waited, with nothing on
screen saying why.
"""
from __future__ import annotations

import threading
import time

import pytest
from agent_harness import ScriptedProvider

from chitragupta.agents import background, runtime


@pytest.fixture
def scripted(monkeypatch):
    def make(script, **kw):
        provider = ScriptedProvider(script=list(script), **kw)
        monkeypatch.setattr(runtime, "get_provider", lambda p, m: provider)
        monkeypatch.setattr(runtime, "resolve_usable_model",
                            lambda p, m: (m or "scripted-1", None))
        return provider
    return make


def test_the_turn_returns_without_waiting_for_learning(scripted, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_learning(*a, **kw):
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(runtime, "_learn_from_turn", slow_learning)
    scripted([], final_answer="here is your answer")

    began = time.perf_counter()
    result = runtime.run_turn("research", "I work at Acme")
    elapsed = time.perf_counter() - began

    try:
        assert result.reply == "here is your answer"
        assert elapsed < 1.0, (
            f"the turn waited {elapsed:.1f}s on work that does not change the answer")
        assert started.wait(timeout=5), "the learning never ran at all"
    finally:
        release.set()
        background.wait_for_idle(timeout=5)


def test_the_learning_still_happens(scripted, monkeypatch):
    """Off the critical path, not dropped."""
    seen = {}
    monkeypatch.setattr(
        runtime, "_learn_from_turn",
        lambda agent_id, user, reply, provider: seen.update(agent=agent_id,
                                                            user=user))
    scripted([], final_answer="ok")

    runtime.run_turn("research", "I use Ollama locally")
    assert background.wait_for_idle(timeout=10)
    assert seen.get("user") == "I use Ollama locally"
    # Which agent said it, not just what was said: notes are per agent, so a
    # learner that does not know who it is learning for cannot file one.
    assert seen.get("agent") == "research"


def test_a_failure_in_the_background_never_reaches_the_user(scripted, monkeypatch):
    def explodes(*a, **kw):
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr(runtime, "_learn_from_turn", explodes)
    scripted([], final_answer="your answer")

    result = runtime.run_turn("research", "I like short answers")
    assert background.wait_for_idle(timeout=5)
    assert result.reply == "your answer"
    assert "blew up" not in result.reply


def test_a_stopped_turn_queues_nothing(scripted):
    """Already covered for spend; this pins that it did not move to the lane."""
    stop = threading.Event()
    stop.set()
    scripted([], final_answer="never")

    result = runtime.run_turn("research", "I work at Acme", cancel=stop)
    assert result.stopped is True
    assert not any(s.name == "auto_learn" for s in result.trace)
