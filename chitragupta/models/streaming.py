"""Turning a provider's stream into text as it is written.

Without this the user watches a spinner for the whole turn, and the frontend
has a list of invented "thinking" phrases and an estimated-seconds countdown to
fill the silence. That is a workaround for missing streaming, and it is the
biggest thing people *feel* about an assistant.

Only two wire formats are needed for every backend Chitragupta supports, which is
a lucky accident worth stating plainly:

* **Anthropic Messages** — the Claude API, and *also* the Claude CLI (whose
  `stream-json` wraps these events under `{"type": "stream_event", "event": …}`)
  and the Grok CLI (`--output-format streaming-messages-json` emits them
  directly). Three backends, one parser. Verified against the real Claude CLI.
* **OpenAI chat-completions** — the OpenAI API, OpenRouter, Ollama, DeepSeek,
  xAI and Gemini (which Chitragupta reaches through Google's OpenAI-compatible
  endpoint), and anything else speaking the same dialect.

Both parsers do the same job: yield text the instant it arrives, and quietly
accumulate tool calls so a complete `ChatResult` can be handed back at the end.
Streaming only the prose would be simpler and would break the agent loop, which
needs the tool calls from the same response.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..log import suppressed
from .base import ChatResult, StreamEvent, ToolCall

#: Re-exported: `StreamEvent` moved down to `base` with the other protocol
#: types, so `base` could implement its own default `stream()` without
#: importing the wire parsers. Every `from .streaming import StreamEvent` in
#: the tree still reads the same.
__all__ = ["StreamEvent", "anthropic_events", "from_result", "openai_events",
           "sse_payloads", "stream_cli"]


@dataclass
class _ToolAssembly:
    """A tool call arriving in pieces."""

    id: str = ""
    name: str = ""
    arguments: str = ""

    def finish(self) -> ToolCall | None:
        if not self.name:
            return None
        try:
            args = json.loads(self.arguments) if self.arguments.strip() else {}
        except json.JSONDecodeError:
            # A truncated or malformed argument blob must not lose the call —
            # `run_tool` validates arguments anyway and will say what is wrong,
            # which is more useful to the model than the call vanishing.
            args = {"_malformed": self.arguments[:500]}
        if not isinstance(args, dict):
            args = {"value": args}
        return ToolCall(id=self.id or f"call_{self.name}", name=self.name,
                        arguments=args)


@dataclass
class _Accumulator:
    text: list[str] = field(default_factory=list)
    tools: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    #: Thinking blocks under assembly, keyed by their stream index. Kept as the
    #: vendor's own shape because they are handed straight back on the next
    #: round and anything we normalise here we would have to un-normalise there.
    reasoning: dict = field(default_factory=dict)
    finish_reason: str = "stop"

    def result(self) -> ChatResult:
        calls = [a.finish() for _, a in sorted(self.tools.items())]
        return ChatResult(
            text="".join(self.text),
            tool_calls=[c for c in calls if c is not None],
            finish_reason=self.finish_reason,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_tokens=self.cached_tokens,
            reasoning=[b for _, b in sorted(self.reasoning.items())],
        )


def sse_payloads(lines: Iterable[str]) -> Iterator[str]:
    """The `data:` payloads of a Server-Sent Events stream.

    `[DONE]` is OpenAI's terminator and is not JSON; every other line is either
    an event name we do not need or blank.
    """
    for raw in lines:
        line = (raw or "").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload and payload != "[DONE]":
            yield payload


def anthropic_events(payloads: Iterable[str]) -> Iterator[StreamEvent]:
    """Parse Anthropic Messages stream events into text and tool calls."""
    acc = _Accumulator()
    for payload in payloads:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        # The Claude CLI nests the real event one level down.
        if event.get("type") == "stream_event" and isinstance(event.get("event"), dict):
            event = event["event"]

        etype = event.get("type")
        if etype == "message_start":
            usage = (event.get("message") or {}).get("usage") or {}
            # `input_tokens` here means "uncached". A turn that caches well
            # reports a handful there and thousands in the two cache fields, so
            # reading only the first makes a long turn look free. See
            # `caching.cache_stats`.
            from .caching import cache_stats
            read, written = cache_stats(usage)
            acc.input_tokens = int(usage.get("input_tokens") or 0) + read + written
            acc.cached_tokens = read
        elif etype == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                acc.tools[event.get("index", len(acc.tools))] = _ToolAssembly(
                    id=block.get("id", ""), name=block.get("name", ""))
            elif block.get("type") in ("thinking", "redacted_thinking"):
                # Collected but never yielded as text. Thinking is not the
                # answer, and streaming it into the reply would put the model's
                # working out in front of the user as if it were the result.
                acc.reasoning[event.get("index", len(acc.reasoning))] = dict(block)
        elif etype == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                chunk = delta.get("text") or ""
                if chunk:
                    acc.text.append(chunk)
                    yield StreamEvent("text", chunk)
            elif delta.get("type") == "input_json_delta":
                slot = acc.tools.get(event.get("index"))
                if slot is not None:
                    slot.arguments += delta.get("partial_json") or ""
            elif delta.get("type") == "thinking_delta":
                block = acc.reasoning.get(event.get("index"))
                if block is not None:
                    block["thinking"] = (block.get("thinking") or "") + (
                        delta.get("thinking") or "")
            elif delta.get("type") == "signature_delta":
                # The signature is what makes a thinking block replayable. A
                # block handed back without it is rejected, so losing this is
                # losing the whole round.
                block = acc.reasoning.get(event.get("index"))
                if block is not None:
                    block["signature"] = (block.get("signature") or "") + (
                        delta.get("signature") or "")
        elif etype == "message_delta":
            usage = event.get("usage") or {}
            acc.output_tokens = int(usage.get("output_tokens") or acc.output_tokens)
            reason = (event.get("delta") or {}).get("stop_reason")
            if reason:
                acc.finish_reason = reason
    yield StreamEvent("done", result=acc.result())


def openai_events(payloads: Iterable[str]) -> Iterator[StreamEvent]:
    """Parse OpenAI chat-completions stream chunks into text and tool calls."""
    acc = _Accumulator()
    for payload in payloads:
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = chunk.get("usage") or {}
        if usage:
            acc.input_tokens = int(usage.get("prompt_tokens") or acc.input_tokens)
            acc.output_tokens = int(usage.get("completion_tokens") or acc.output_tokens)
            # OpenAI caches automatically and reports the hit nested inside
            # `prompt_tokens` — already counted, so this is read for reporting
            # only and must NOT be added on the way Anthropic's is.
            details = usage.get("prompt_tokens_details") or {}
            acc.cached_tokens = int(details.get("cached_tokens") or acc.cached_tokens)

        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                acc.finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if text:
                acc.text.append(text)
                yield StreamEvent("text", text)
            for call in delta.get("tool_calls") or []:
                # `index` is what ties the fragments of one call together; a
                # response with two calls interleaves their argument chunks.
                slot = acc.tools.setdefault(call.get("index", 0), _ToolAssembly())
                if call.get("id"):
                    slot.id = call["id"]
                fn = call.get("function") or {}
                if fn.get("name"):
                    slot.name = fn["name"]
                if fn.get("arguments"):
                    slot.arguments += fn["arguments"]
    yield StreamEvent("done", result=acc.result())


def from_result(result: ChatResult) -> Iterator[StreamEvent]:
    """The fallback: a provider that cannot stream, shaped like one that can.

    Every provider gets streaming this way on day one; the ones below simply
    arrive in a single piece. Callers never branch on whether a backend supports
    it — which is what keeps the agent loop free of provider special cases.
    """
    if result.text:
        yield StreamEvent("text", result.text)
    yield StreamEvent("done", result=result)


def stream_cli(cmd: list[str], *, env: dict, stdin: str | None = None,
               timeout: int = 300, provider: str = "") -> Iterator[StreamEvent]:
    """Run a vendor CLI in streaming mode and parse its NDJSON output.

    The three CLI backends all emit Anthropic Messages events, one JSON object
    per line — Claude wraps them under `stream_event`, Grok emits them directly,
    and Cursor's shape is close enough that the same parser finds its text. That
    last one is the reason for the caller's fallback: a stream that yields no
    text at all is treated as unsupported rather than as an empty answer, and
    the caller re-runs the plain non-streaming call.
    """
    import subprocess

    from ..log import get_logger

    log = get_logger(__name__)
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        bufsize=1)
    try:
        if stdin is not None and proc.stdin is not None:
            proc.stdin.write(stdin)
            proc.stdin.close()
        assert proc.stdout is not None
        yield from anthropic_events(proc.stdout)
    finally:
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            log.debug("%s streaming CLI did not exit; killed", provider or cmd[0])


def raise_for_status(resp: Any) -> None:
    """`resp.raise_for_status()`, but for a response that is being streamed.

    httpx builds its error message *from the body*, and a streamed response has
    not read one — so calling `raise_for_status()` inside `httpx.stream(...)` on
    a 400 raises `ResponseNotRead: Attempted to access streaming response
    content, without having called read()`.

    That is not a description of anything the user did. It replaced every
    streaming failure in the app with an httpx internal: a wrong model id, an
    expired sign-in and a rate limit all arrived as the same sentence about
    `read()`, and the one thing none of them said was what had actually gone
    wrong. A user whose automation ran seven times saw it seven times.

    Reading first costs one buffered body on the error path only — the success
    path never reaches it, and an error body is small by construction.
    """
    if resp.status_code < 400:
        return
    with suppressed("reading an error body from a streamed response"):
        resp.read()
    resp.raise_for_status()
