"""Anthropic (Claude) provider.

Two credentials reach Claude and they are NOT interchangeable:

  • an **API key** -> the Messages API at api.anthropic.com, billed per token.
  • a **subscription** (Claude Pro / Max / Team, connected as an account)
    -> there is no subscription inference endpoint. The only way to run it is
    the local Claude CLI, so those calls are delegated to ClaudeCodeProvider.

Sending a subscription request to the Messages API without a key is a 401, so
the two paths are chosen explicitly rather than falling through.
"""
from __future__ import annotations

import json
import os
import uuid

import httpx

from ..log import get_logger, suppressed
from . import caching, reasoning
from .base import DEFAULT_MAX_OUTPUT, ChatResult, LLMProvider, Message, ToolCall, _saved_key
from .errors import ErrorKind, ProviderError, classify_exception, classify_http

log = get_logger(__name__)


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    key_env = "ANTHROPIC_API_KEY"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
        self.api_key = api_key if api_key is not None else (os.environ.get("ANTHROPIC_API_KEY", "") or _saved_key("ANTHROPIC_API_KEY"))
        self.base_url = os.environ.get(
            "ANTHROPIC_BASE_URL", "https://api.anthropic.com"
        )
        self._cli: LLMProvider | None = None
        # Images only work on the Messages API. Without a key this provider
        # delegates to the Claude CLI, which takes its prompt on argv — there
        # is nowhere for an image to go, whichever model is chosen. Set on the
        # instance rather than as a property: the base declares a plain
        # attribute, and overriding one with a read-only property is a
        # narrowing mypy is right to reject.
        self.supports_images = bool(self.api_key)

    def is_ready(self) -> tuple[bool, str]:
        if self.api_key:
            return True, ""
        with suppressed("from .connections import ConnectionStatus, get_connection …"):
            from .connections import ConnectionStatus, get_connection
            conn = get_connection("claude")
            if conn.account_status == ConnectionStatus.DISCONNECTED:
                return False, "Disconnected. Set ANTHROPIC_API_KEY or sign in with Claude"
            # An account the user actually connected — NOT merely a Claude CLI
            # that happens to be installed. Detection is not consent.
            if conn.account_connected:
                # ...but a subscription still needs the CLI to actually run.
                # Reporting ready without it is what produced a 401 on the first
                # message the user sent.
                from .claude_cli import find_claude
                if find_claude():
                    return True, ""
                return False, (
                    "Claude account connected, but running a Claude subscription "
                    "needs the Claude CLI. Install it (npm i -g "
                    "@anthropic-ai/claude-code) or add an ANTHROPIC_API_KEY."
                )
        return False, "set ANTHROPIC_API_KEY or connect Claude account"

    def _subscription_backend(self) -> LLMProvider | None:
        """The Claude CLI, which is how a subscription runs inference."""
        if self._cli is None:
            from .claude_cli import find_claude
            from .claude_code import ClaudeCodeProvider
            if not find_claude():
                return None
            # Pass the id through unchanged — the CLI accepts the same ids the
            # catalog uses (claude-opus-5, claude-sonnet-5, ...). Rewriting it
            # here produced names the CLI does not recognise.
            self._cli = ClaudeCodeProvider(model=self.model)
        return self._cli

    def _to_blocks(self, messages: list[Message]) -> tuple[list[dict], list[dict], int]:
        """`(system blocks, messages, stable_upto)` in wire form.

        System arrives as a *list* rather than one joined string so a cache
        marker can sit between the agent's own prompt and the per-turn blocks
        that follow it. Joined, the whole system prompt changed every turn and
        no marker on it could ever be hit twice.

        `stable_upto` is the index of the last system block the caller promised
        is identical next turn, or -1. It is returned beside the blocks rather
        than carried inside one, because anything inside a block is sent to the
        vendor and `_stable` is not a field they have. See `caching.py`.
        """
        system: list[dict] = []
        out: list[dict] = []
        stable_upto = -1
        for m in messages:
            if m.role == "system":
                if m.content:
                    system.append({"type": "text", "text": m.content})
                    if m.stable:
                        stable_upto = len(system) - 1
            elif m.role == "user":
                if m.images:
                    # Images come FIRST: Anthropic's own guidance is that a
                    # question placed after the image it refers to is answered
                    # against the image rather than in the abstract.
                    ublocks: list[dict] = [{
                        "type": "image",
                        "source": {"type": "base64",
                                   "media_type": img.media_type,
                                   "data": img.data},
                    } for img in m.images]
                    if m.content:
                        ublocks.append({"type": "text", "text": m.content})
                    out.append({"role": "user", "content": ublocks})
                else:
                    out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                # Thinking FIRST, and unaltered. Anthropic requires the blocks
                # back in their original order with their signatures intact for
                # every replayed assistant turn in a tool loop; reordering them
                # or dropping one is a 400 on the next round, not a worse answer.
                blocks: list[dict] = [dict(b) for b in m.reasoning
                                      if isinstance(b, dict)]
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append({
                        "type": "tool_use", "id": tc.id,
                        "name": tc.name, "input": tc.arguments,
                    })
                out.append({"role": "assistant", "content": blocks or m.content})
            elif m.role == "tool":
                out.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                }]})
        return system, out, stable_upto

    def _post(self, payload: dict):
        """One POST, so the retry path cannot drift from the first attempt."""
        return httpx.post(
            f"{self.base_url}/v1/messages",
            headers={"x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=payload, timeout=120,
        )

    def _payload(self, messages, tools, temperature, max_tokens) -> dict:
        """The request body, cache markers included.

        One builder for both `stream` and `chat`: they differ by a single
        `"stream"` key, and when they each built their own the caching change
        had to be made twice — which is the shape of a bug that ships half.
        """
        system, msgs, stable_upto = self._to_blocks(messages)
        schemas = [{"name": t.name, "description": t.description,
                    "input_schema": t.parameters} for t in tools] if tools else None
        system, msgs, schemas = caching.apply(
            system, msgs, schemas, stable_system_upto=stable_upto)
        payload: dict = {"model": self.model, "max_tokens": max_tokens,
                         "temperature": temperature, "messages": msgs}
        if system:
            payload["system"] = system
        if schemas:
            payload["tools"] = schemas
        budget = reasoning.anthropic_budget(self.model, reasoning.wanted(),
                                            max_tokens)
        if budget:
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # Not negotiable and not ours to choose: the vendor rejects any
            # other temperature while thinking is on. The loop's 0.15 buys
            # reliable tool use, and a model that reasons is steadier than a
            # cold one that does not — so this is the better half of the trade.
            payload["temperature"] = 1
        return payload

    def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        """Real streaming over the Messages API.

        With no API key this is a subscription, and the Claude CLI backend has
        its own streaming — delegating keeps that one code path rather than
        reimplementing it here.
        """
        from .streaming import anthropic_events, from_result, raise_for_status, sse_payloads

        if not self.api_key:
            backend = self._subscription_backend()
            if backend is None:
                yield from from_result(self.chat(
                    messages, tools=tools, temperature=temperature,
                    max_tokens=max_tokens))
                return
            yield from backend.stream(messages, tools=tools,
                                      temperature=temperature,
                                      max_tokens=max_tokens)
            return

        payload = {**self._payload(messages, tools, temperature, max_tokens),
                   "stream": True}
        try:
            with httpx.stream(
                "POST", f"{self.base_url}/v1/messages",
                headers={"x-api-key": self.api_key,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json=payload, timeout=300,
            ) as resp:
                raise_for_status(resp)
                yield from anthropic_events(sse_payloads(resp.iter_lines()))
            return
        except Exception as exc:
            # Falling back rather than surfacing a stream-specific failure: the
            # non-streaming path has the full error taxonomy, and a user whose
            # stream broke wants an answer, not a second kind of error message.
            log.debug("anthropic streaming failed, falling back: %s", exc)
        yield from from_result(self.chat(messages, tools=tools,
                                         temperature=temperature,
                                         max_tokens=max_tokens))

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        if not self.api_key:
            # Subscription, not an API key — the Messages API would 401.
            backend = self._subscription_backend()
            if backend is None:
                return ChatResult(text=(
                    "⚠️ No Anthropic API key, and the Claude CLI needed to run a "
                    "Claude subscription isn't installed. Add an API key in "
                    "Models & Accounts, or install the Claude CLI."))
            return backend.chat(messages, tools=tools, temperature=temperature,
                                max_tokens=max_tokens)

        payload = self._payload(messages, tools, temperature, max_tokens)

        try:
            resp = self._post(payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            err = classify_http("claude", exc.response.status_code,
                                exc.response.text, model=self.model,
                                key_env=self.key_env)
            # Classified first, unconditionally, so the only question left is
            # whether to SURFACE it — a handler that decides the answer before
            # it has one is how a branch ends up with no taxonomy behind it.
            # A model we wrongly believed could think must cost the user one
            # silent retry, never an error about a feature they never asked for
            # and cannot see. Narrow on purpose: any other 400 stays an error.
            if not reasoning.rejected_thinking(exc.response.status_code,
                                               exc.response.text):
                return ChatResult(text=err.as_reply())
            log.debug("%s refused a thinking budget; retrying without it",
                      self.model)
            try:
                resp = self._post({**reasoning.strip(payload),
                                   "temperature": temperature})
                resp.raise_for_status()
            except httpx.HTTPError as retry_exc:
                err = classify_exception("claude", retry_exc, model=self.model,
                                         base_url=self.base_url)
                return ChatResult(text=err.as_reply())
        except httpx.HTTPError as exc:
            err = classify_exception("claude", exc, model=self.model,
                                     base_url=self.base_url)
            return ChatResult(text=err.as_reply())

        try:
            data = resp.json()
            blocks = data.get("content", [])
        except (json.JSONDecodeError, TypeError, AttributeError):
            err = ProviderError(ErrorKind.BAD_REQUEST, "claude", model=self.model,
                                message="Unexpected response from Claude. "
                                        "Try again or switch models.")
            return ChatResult(text=err.as_reply())

        text_parts, calls = [], []
        # Kept whole and never read into: they go straight back on the next
        # round, and a thinking block is not part of the answer.
        thoughts = [b for b in blocks
                    if b.get("type") in ("thinking", "redacted_thinking")]
        for block in blocks:
            if block["type"] == "text":
                text_parts.append(block["text"])
            elif block["type"] == "tool_use":
                calls.append(ToolCall(
                    id=block.get("id", str(uuid.uuid4())),
                    name=block["name"],
                    arguments=block.get("input", {}),
                ))
        usage = data.get("usage") or {}
        # Anthropic reports cached prompt tokens SEPARATELY from `input_tokens`,
        # so once caching works a naive read of that field makes a turn look
        # ten times cheaper than it is — and the turn budget in `runtime.py`
        # would stop bounding anything. Everything the model processed is
        # counted; what it cost is the separate number beside it.
        read, written = caching.cache_stats(usage)
        return ChatResult(
            text="".join(text_parts),
            tool_calls=calls,
            raw=data,
            finish_reason=data.get("stop_reason", "stop"),
            input_tokens=int(usage.get("input_tokens") or 0) + read + written,
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_tokens=read,
            reasoning=thoughts,
        )
