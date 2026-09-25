"""Prompt caching — the prefix a twelve-round turn must not pay for twelve times.

A turn is a loop, and every round re-sends the agent's system prompt and the
full JSON Schema of every tool it owns. That is the bulk of the payload and it
is byte-identical each time. Billing it once per round is the largest avoidable
cost in the app, and it is silent — nothing in the UI says "you just re-sent
9,000 tokens".

These check the three things that can be wrong without anything looking wrong:

* that markers are actually placed, and in the positions that can be *hit*
  rather than merely written;
* that a per-turn block (recall, the task list) can never end up inside a
  marked prefix, because a cross-turn cache of something rebuilt every turn is
  a write nobody reads and is charged extra for;
* that the token accounting sees cached reads. Anthropic reports them OUTSIDE
  `input_tokens`, so the field the turn budget reads goes to near-zero exactly
  when caching starts working — and the budget silently stops bounding the loop.
"""
from __future__ import annotations

import pytest

from chitragupta.models import caching
from chitragupta.models.anthropic import AnthropicProvider
from chitragupta.models.base import Message, Tool

LONG = "x" * 6000   # past the vendor minimum, like a real agent prompt


def _tool(name: str) -> Tool:
    return Tool(name=name, description="d" * 200,
                parameters={"type": "object", "properties": {}})


def _markers(obj) -> int:
    """Every `cache_control` anywhere in a payload."""
    if isinstance(obj, dict):
        return ("cache_control" in obj) + sum(_markers(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_markers(v) for v in obj)
    return 0


def _payload(messages, tools=None):
    return AnthropicProvider(api_key="sk-test")._payload(
        messages, tools, 0.15, 1500)


# ── the markers land ───────────────────────────────────────────────────────

def test_the_agents_prompt_and_its_tools_are_both_marked():
    payload = _payload(
        [Message(role="system", content=LONG, stable=True),
         Message(role="user", content="hi")],
        [_tool("a"), _tool("b")])
    assert "cache_control" in payload["system"][0]
    assert "cache_control" in payload["tools"][-1]
    # Not on every tool: a marker is a breakpoint, not a flag, and there are
    # only four.
    assert "cache_control" not in payload["tools"][0]


def test_the_conversation_tail_is_marked_so_round_n_reads_round_n_minus_one():
    """The part that actually grows. Without this the accumulated tool results
    are re-billed on every remaining round of the loop."""
    payload = _payload([
        Message(role="system", content=LONG, stable=True),
        Message(role="user", content="find it"),
        Message(role="tool", content=LONG, tool_call_id="t1", name="search_brain"),
        Message(role="user", content="and then?"),
    ])
    marked = [m for m in payload["messages"] if _markers(m)]
    assert len(marked) == 2, "two rolling markers, so a hit survives a late write"
    assert marked[-1] is payload["messages"][-1]


def test_a_plain_string_body_is_widened_so_it_can_carry_a_marker():
    """`_to_blocks` emits a bare string for a user turn with no image, and a
    string has nowhere to put `cache_control`."""
    payload = _payload([Message(role="system", content=LONG, stable=True),
                        Message(role="user", content="hello " + LONG)])
    assert isinstance(payload["messages"][-1]["content"], list)


def test_never_more_than_the_four_the_vendor_allows():
    """A fifth is a 400, not a smaller saving."""
    messages = [Message(role="system", content=LONG, stable=True),
                Message(role="system", content=LONG)]
    messages += [Message(role="user", content=LONG) for _ in range(6)]
    payload = _payload(messages, [_tool(f"t{i}") for i in range(5)])
    assert _markers(payload) <= caching.MAX_BREAKPOINTS


# ── what must NOT be inside a marked prefix ────────────────────────────────

def test_a_per_turn_system_block_is_never_the_cached_boundary():
    """Recall and the task list are rebuilt every turn. A marker after them
    writes a cache entry that can never be hit and is billed at a premium for
    the privilege."""
    payload = _payload([
        Message(role="system", content=LONG, stable=True),
        Message(role="system", content="recalled: " + LONG),   # per-turn
        Message(role="system", content="open tasks: " + LONG),  # per-turn
        Message(role="user", content="hi"),
    ])
    assert "cache_control" in payload["system"][0]
    assert not any("cache_control" in b for b in payload["system"][1:])


def test_an_unmarked_system_prompt_gets_no_system_marker():
    """No promise from the caller, no cross-turn claim."""
    payload = _payload([Message(role="system", content=LONG),
                        Message(role="user", content="hi")])
    assert not any("cache_control" in b for b in payload["system"])


def test_a_small_request_is_left_exactly_as_it_was():
    """Below the vendor's minimum every marker is a no-op, and a payload that
    changes shape for no gain is a payload that is harder to read in a trace."""
    payload = _payload([Message(role="system", content="be brief", stable=True),
                        Message(role="user", content="hi")], [_tool("a")])
    assert _markers(payload) == 0


def test_applying_markers_does_not_mutate_what_it_was_given():
    """A provider that retries has to be able to rebuild the same request."""
    system = [{"type": "text", "text": LONG}]
    messages = [{"role": "user", "content": LONG}]
    tools = [{"name": "a", "description": LONG, "input_schema": {}}]
    caching.apply(system, messages, tools, stable_system_upto=0)
    assert system == [{"type": "text", "text": LONG}]
    assert messages == [{"role": "user", "content": LONG}]
    assert "cache_control" not in tools[0]


# ── the accounting ─────────────────────────────────────────────────────────

def test_cached_reads_are_counted_in_the_input_total():
    """Anthropic reports them OUTSIDE `input_tokens`. Read naively, a turn that
    caches well looks ten times cheaper than it is and the turn budget in
    `runtime.py` stops bounding the loop."""
    read, written = caching.cache_stats(
        {"input_tokens": 12, "cache_read_input_tokens": 9000,
         "cache_creation_input_tokens": 300})
    assert (read, written) == (9000, 300)


def test_the_streaming_path_counts_them_too():
    """Streaming is the path every real turn takes; the non-streaming one is a
    fallback. Accounting that is only right on the fallback is not accounting."""
    import json

    from chitragupta.models.streaming import anthropic_events
    events = [json.dumps(e) for e in [
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 12, "cache_read_input_tokens": 9000,
            "cache_creation_input_tokens": 300}}},
        {"type": "message_delta", "usage": {"output_tokens": 40}},
    ]]
    done = [e for e in anthropic_events(events) if e.kind == "done"][-1]
    assert done.result.input_tokens == 9312
    assert done.result.cached_tokens == 9000


def test_openais_cached_tokens_are_reported_but_not_added_twice():
    """OpenAI nests its hit INSIDE `prompt_tokens`. Adding it the way
    Anthropic's is added would double-count the whole prefix."""
    import json

    from chitragupta.models.streaming import openai_events
    events = [json.dumps({
        "usage": {"prompt_tokens": 9312, "completion_tokens": 40,
                  "prompt_tokens_details": {"cached_tokens": 9000}},
        "choices": [{"delta": {}, "finish_reason": "stop"}],
    })]
    done = [e for e in openai_events(events) if e.kind == "done"][-1]
    assert done.result.input_tokens == 9312
    assert done.result.cached_tokens == 9000


# ── the contract between the two layers ────────────────────────────────────

def test_the_runtime_marks_the_agents_prompt_and_nothing_after_it():
    """The half of the contract that lives in `agents/`. Both sides have to
    land together: markers with nothing marking them save nothing, and a
    `stable` flag no provider reads is a field that quietly rots."""
    import inspect

    from chitragupta.agents import runtime
    source = inspect.getsource(runtime.run_turn)
    assert "stable=True" in source, "the agent prompt must be marked stable"
    assert source.count("stable=True") == 1, (
        "exactly one boundary — a second marks a per-turn block as cacheable")


@pytest.mark.parametrize("field", ["cached_tokens"])
def test_chat_result_carries_the_number(field):
    from chitragupta.models.base import ChatResult
    assert hasattr(ChatResult(text=""), field)
