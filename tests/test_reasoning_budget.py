"""Asking a reasoning model to reason — and surviving having asked.

The catalog has flagged `reasoning=True` on models since `discovery.py` was
written, and nothing in the app ever sent a reasoning parameter. An Opus
selected at High cost the user reasoning-model money and answered like a model
that cannot reason at all.

Turning it on is three separate things that each fail silently or loudly:

* **the parameter itself**, in two different vendor spellings;
* **the replay** — Anthropic requires the thinking blocks handed back with the
  assistant turn on every later round of a tool loop, signature intact. This is
  the one that bites, because round one succeeds and round two is a 400;
* **the retreat** — a model we guessed wrong about must cost one silent retry,
  never an error message about a feature the user cannot see.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chitragupta.models import reasoning
from chitragupta.models.anthropic import AnthropicProvider
from chitragupta.models.base import Message, ToolCall
from chitragupta.models.openai_compat import OpenAICompatProvider


@pytest.fixture(autouse=True)
def _no_leaked_budget():
    """Every test starts with no thinking budget asked for.

    Not tidiness — this is the bug the ContextVar exists to prevent, one layer
    down. The first version of these tests set the budget and never released
    it, and `test_the_default_sends_nothing_at_all` then failed because it
    inherited the previous test's setting. That is exactly what one turn does
    to another when the budget lives on the `@lru_cache`d provider instead.
    """
    token = reasoning.for_this_turn(0)
    yield
    reasoning.release(token)


def _thinking(tokens: int):
    """Ask for a budget for the rest of this test."""
    reasoning.for_this_turn(tokens)


# ── the parameter ──────────────────────────────────────────────────────────

def test_a_thinking_model_is_asked_to_think():
    p = AnthropicProvider(model="claude-opus-5", api_key="sk-test")
    _thinking(4000)
    payload = p._payload([Message(role="user", content="hard")], None, 0.15, 16000)
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 4000}


def test_the_loops_cold_temperature_gives_way_to_thinking():
    """The vendor rejects any other temperature while thinking is on, so the
    two settings cannot both be honoured. Silently sending 0.15 anyway is a 400
    on every single turn."""
    p = AnthropicProvider(model="claude-opus-5", api_key="sk-test")
    _thinking(4000)
    assert p._payload([Message(role="user", content="x")], None, 0.15, 16000
                      )["temperature"] == 1


def test_a_model_that_cannot_think_is_not_asked_to():
    p = AnthropicProvider(model="claude-3-haiku-20240307", api_key="sk-test")
    _thinking(4000)
    assert "thinking" not in p._payload(
        [Message(role="user", content="x")], None, 0.15, 16000)
    assert p._payload([Message(role="user", content="x")], None, 0.15, 16000
                      )["temperature"] == 0.15


def test_the_default_sends_nothing_at_all():
    """A provider nobody configured behaves exactly as it did before."""
    p = AnthropicProvider(model="claude-opus-5", api_key="sk-test")
    payload = p._payload([Message(role="user", content="x")], None, 0.7, 4000)
    assert "thinking" not in payload and payload["temperature"] == 0.7


def test_the_budget_never_eats_the_whole_answer():
    """Thinking comes out of `max_tokens`. A budget equal to the ceiling leaves
    the model no room to write anything, which reads to the user as an empty
    reply rather than as a setting."""
    assert reasoning.anthropic_budget("claude-opus-5", 8000, 4000) < 4000


def test_a_budget_under_the_vendor_floor_is_not_sent():
    assert reasoning.anthropic_budget("claude-opus-5", 500, 16000) == 0


@pytest.mark.parametrize("model,tokens,expected", [
    ("gpt-5", 8000, "high"),
    ("o3-mini", 2000, "medium"),
    ("deepseek-reasoner", 1000, "low"),
    ("gpt-4o", 8000, ""),          # no reasoning — the parameter is a 400
    ("gpt-5", 0, ""),
])
def test_openai_gets_a_word_where_anthropic_gets_a_number(model, tokens, expected):
    assert reasoning.openai_effort(model, tokens) == expected


def test_reasoning_effort_reaches_both_openai_bodies():
    """`stream` and `chat` build their own payloads, so a parameter added to
    one of them is a feature that stops working the moment the stream falls
    back to the other."""
    p = OpenAICompatProvider()
    p.model = "gpt-5"
    _thinking(8000)
    assert p._reasoning_effort() == {"reasoning_effort": "high"}


# ── the replay ─────────────────────────────────────────────────────────────

THOUGHT = {"type": "thinking", "thinking": "let me check", "signature": "sig-abc"}


def test_thinking_blocks_come_back_at_the_head_of_the_assistant_turn():
    """Anthropic requires them first, in order, signature intact. Round one
    succeeds without this and round two is a 400."""
    _sys, msgs, _stable = AnthropicProvider(api_key="sk-test")._to_blocks([
        Message(role="assistant", content="checking",
                tool_calls=[ToolCall(id="t1", name="search_brain", arguments={})],
                reasoning=[THOUGHT]),
    ])
    blocks = msgs[0]["content"]
    assert blocks[0] == THOUGHT
    assert blocks[1]["type"] == "text"
    assert blocks[2]["type"] == "tool_use"


def test_the_signature_survives_streaming():
    """A thinking block handed back without its signature is rejected, so
    losing the signature loses the round just as completely as losing the block."""
    from chitragupta.models.streaming import anthropic_events
    events = [json.dumps(e) for e in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "let me "}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "check"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "sig-abc"}},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "text_delta", "text": "done"}},
    ]]
    out = list(anthropic_events(events))
    result = out[-1].result
    assert result.reasoning == [
        {"type": "thinking", "thinking": "let me check", "signature": "sig-abc"}]
    assert result.text == "done"


def test_thinking_is_never_streamed_to_the_user_as_text():
    """It is the model's working out, not the answer. Streamed into the reply
    it arrives in front of the user looking exactly like one."""
    from chitragupta.models.streaming import anthropic_events
    events = [json.dumps(e) for e in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "second-guessing myself"}},
    ]]
    assert not [e for e in anthropic_events(events) if e.kind == "text"]


def test_the_runtime_hands_the_blocks_back():
    """The half of the contract that lives in `agents/`. Both sides must land
    together: capturing thinking blocks nobody replays is a 400 on round two."""
    import inspect

    from chitragupta.agents import runtime
    source = inspect.getsource(runtime.run_turn)
    assert "reasoning=result.reasoning" in source
    assert "reasoning.for_this_turn(profile.thinking_tokens)" in source
    assert "reasoning.release(think_token)" in source, (
        "a ContextVar set and never reset leaks this turn's setting into "
        "whatever the worker thread picks up next")


# ── the retreat ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,body,expected", [
    (400, "thinking.budget_tokens: unsupported", True),
    (400, "`reasoning_effort` is not supported", True),
    (400, "messages.0: invalid role", False),   # a real error, and must stay one
    (429, "thinking rate limited", False),      # retrying this would hide a limit
])
def test_only_a_refusal_about_reasoning_counts_as_one(status, body, expected):
    assert reasoning.rejected_thinking(status, body) is expected


def test_a_refused_budget_costs_one_silent_retry_not_an_error(monkeypatch):
    """A model we guessed wrong about must not surface a message about a
    feature the user never asked for and cannot see."""
    p = AnthropicProvider(model="claude-opus-5", api_key="sk-test")
    _thinking(4000)
    seen: list[dict] = []

    def fake_post(payload):
        seen.append(payload)
        if "thinking" in payload:
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            return httpx.Response(400, text="thinking: unsupported", request=req)
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "the answer"}],
            "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 2},
        }, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    monkeypatch.setattr(p, "_post", fake_post)
    result = p.chat([Message(role="user", content="hi")], temperature=0.15)
    assert result.text == "the answer"
    assert len(seen) == 2
    assert "thinking" not in seen[1]
    # The caller's own temperature is restored, not left at the 1 that thinking
    # forced — otherwise the retry quietly answers at a setting nobody chose.
    assert seen[1]["temperature"] == 0.15


def test_a_real_four_hundred_is_still_an_error(monkeypatch):
    """Retrying everything would turn a genuine bad request into a silent
    double spend and a confusing answer."""
    p = AnthropicProvider(model="claude-opus-5", api_key="sk-test")
    _thinking(4000)
    calls = []

    def fake_post(payload):
        calls.append(payload)
        return httpx.Response(
            400, text="messages: at least one message is required",
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    monkeypatch.setattr(p, "_post", fake_post)
    result = p.chat([Message(role="user", content="hi")])
    assert len(calls) == 1
    assert "⚠️" in result.text or "error" in result.text.lower()


def test_strip_leaves_every_other_parameter_alone():
    payload = {"model": "m", "messages": [], "thinking": {"x": 1},
               "reasoning_effort": "high", "tools": [1], "max_tokens": 9}
    assert reasoning.strip(payload) == {
        "model": "m", "messages": [], "tools": [1], "max_tokens": 9}


# ── the ceiling on a reply ─────────────────────────────────────────────────

def test_a_reply_is_no_longer_capped_at_a_single_page():
    """1500 tokens is under a page. A drafted email survived it; an eight-step
    plan with its reasoning was cut mid-sentence, and the loop then carried the
    severed half into the next round as though it were finished."""
    from chitragupta.agents.effort import HIGH, LOW, MEDIUM
    assert LOW.max_output_tokens >= 2000
    assert MEDIUM.max_output_tokens >= 4000
    assert HIGH.max_output_tokens > MEDIUM.max_output_tokens


def test_the_turn_actually_asks_for_that_ceiling():
    """The profile can say 16,000 and the loop still send the default."""
    import inspect

    from chitragupta.agents import runtime
    assert "max_tokens=profile.max_output_tokens" in inspect.getsource(runtime.run_turn)


def test_a_sub_agent_is_not_cut_in_half():
    """A truncated finding propagates upward as a confident wrong answer."""
    from chitragupta.agents.effort import HIGH
    assert HIGH.child().max_output_tokens == HIGH.max_output_tokens


# ── one turn must not change another turn's setting ────────────────────────

def test_two_concurrent_turns_keep_their_own_budgets():
    """The bug this nearly shipped with.

    The budget was first written as an attribute on the provider — and
    `get_provider` is `@lru_cache`d, so **one instance answers every concurrent
    turn**. A turn at Low would have set 0 on the same object a turn at High
    was already looping on, and the symptom is the worst kind: "the expensive
    setting sometimes does nothing", intermittently, under load only.

    `runtime.py` already carried the warning, about the tool-call observer:
    "an attribute there would draw one agent's tool calls into another's trace".
    """
    import concurrent.futures
    import contextvars

    seen: dict[str, int] = {}

    def turn(name: str, tokens: int, wait) -> None:
        token = reasoning.for_this_turn(tokens)
        try:
            wait.wait(timeout=5)          # both turns are now mid-loop
            seen[name] = reasoning.wanted()
        finally:
            reasoning.release(token)

    import threading
    both_started = threading.Barrier(2, timeout=5)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        futures = []
        for name, tokens in (("high", 8000), ("low", 0)):
            ctx = contextvars.copy_context()
            futures.append(pool.submit(ctx.run, turn, name, tokens, both_started))
        for f in futures:
            f.result()

    assert seen == {"high": 8000, "low": 0}, (
        "one turn's thinking budget reached the other")


def test_the_budget_does_not_outlive_the_turn():
    """A ContextVar set and never reset leaks into whatever the worker thread
    picks up next — the same rule the delegation chain is held to."""
    token = reasoning.for_this_turn(8000)
    assert reasoning.wanted() == 8000
    reasoning.release(token)
    assert reasoning.wanted() == 0


def test_no_provider_carries_the_budget_as_an_attribute():
    """Where it must NOT live. An attribute on an `@lru_cache`d provider is
    shared by every concurrent turn."""
    import pathlib
    models = pathlib.Path(reasoning.__file__).parent
    offenders = [p.name for p in models.glob("*.py")
                 if "self.thinking_tokens" in p.read_text()]
    assert not offenders, offenders
