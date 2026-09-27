"""Two things the model said do not become one word.

Anthropic returns prose in `text` **blocks**, and a response can carry several
— it writes a sentence, calls a tool, writes more. The Claude CLI backend,
which runs its own loop and streams the whole thing back through this parser,
produces many. Every delta was appended to one list and joined with `""`, so
the seam between two blocks disappeared and the user read:

    I'll check calendar access and existing automations first.Checked — write
    access to your calendar already works…

Two paragraphs run together into one word. The join is right *within* a block —
those are fragments of one sentence — and wrong *between* them.
"""
from __future__ import annotations

import json

from chitragupta.models.anthropic import AnthropicProvider
from chitragupta.models.base import Message
from chitragupta.models.streaming import anthropic_events


def run(events):
    return list(anthropic_events(json.dumps(e) for e in events))


def blocks(*texts, stop="end_turn"):
    """A response carrying one `text` block per argument."""
    out = [{"type": "message_start", "message": {"usage": {}}}]
    for index, text in enumerate(texts):
        out.append({"type": "content_block_start", "index": index,
                    "content_block": {"type": "text", "text": ""}})
        out.append({"type": "content_block_delta", "index": index,
                    "delta": {"type": "text_delta", "text": text}})
        out.append({"type": "content_block_stop", "index": index})
    out.append({"type": "message_delta", "delta": {"stop_reason": stop},
                "usage": {}})
    return out


# ── the bug ────────────────────────────────────────────────────────────────

def test_two_blocks_are_not_run_together():
    """The failure exactly as it was read on screen."""
    result = run(blocks("I'll check calendar access first.",
                        "Checked — write access already works."))[-1].result
    assert "first.Checked" not in result.text, result.text


def test_they_are_separated_as_paragraphs():
    result = run(blocks("One.", "Two."))[-1].result
    assert result.text == "One.\n\nTwo."


def test_the_stream_carries_the_separator_too():
    """The live preview is assembled from the `text` events, not from the
    result — so a separator only in the result would fix the stored message and
    leave the user watching the two run together as it arrived."""
    out = run(blocks("One.", "Two."))
    streamed = "".join(e.text for e in out if e.kind == "text")
    assert streamed == "One.\n\nTwo."


# ── and the fragments of one block still are ───────────────────────────────

def test_fragments_of_one_block_still_join_seamlessly():
    """The control. Deltas within a block are pieces of one sentence, and a
    separator between those would break every word the model streams."""
    events = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "Look"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "ing…"}},
    ]
    assert run(events)[-1].result.text == "Looking…"


def test_a_block_that_said_nothing_adds_no_gap():
    """An empty text block beside a tool call is ordinary. Padding the answer
    with a blank paragraph for one would put a hole in every tool-using turn."""
    result = run(blocks("", "Only this."))[-1].result
    assert result.text == "Only this."


def test_a_single_block_is_untouched():
    assert run(blocks("Just the one."))[-1].result.text == "Just the one."


# ── the non-streaming path has the same seam ───────────────────────────────

def test_the_non_streaming_path_separates_them_as_well(monkeypatch):
    """`chat()` reads the blocks straight off the response, and it is the
    fallback whenever a stream fails — so a fix in the parser alone would come
    back the first time one broke. Driven through the real method rather than
    around it: the join being fixed somewhere is not the claim."""
    import httpx

    from chitragupta.models import anthropic as module

    def answer(request):
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "I'll check first."},
                        {"type": "text", "text": "Checked."}],
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "stop_reason": "end_turn"})

    client = httpx.Client(transport=httpx.MockTransport(answer))
    monkeypatch.setattr(module.httpx, "post",
                        lambda url, **kw: client.post(url, **kw))

    result = AnthropicProvider(api_key="k").chat(
        [Message(role="user", content="hi")])
    assert result.text == "I'll check first.\n\nChecked."
