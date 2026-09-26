"""OpenAI-compatible provider.

One implementation covers OpenAI, OpenRouter, Ollama, vLLM, llama.cpp, LM
Studio, and any other server that speaks the /chat/completions API with tool
calling — differing only in base URL, key and default model.
"""
from __future__ import annotations

import json
import os
import uuid

import httpx

from ..log import get_logger, suppressed
from . import reasoning
from .base import DEFAULT_MAX_OUTPUT, ChatResult, LLMProvider, Message, ToolCall, _saved_key
from .errors import ErrorKind, ProviderError, classify_exception, classify_http

log = get_logger(__name__)


class OpenAICompatProvider(LLMProvider):
    name = "openai"
    default_base = "https://api.openai.com/v1"
    default_model = "gpt-5.6-terra"
    key_env = "OPENAI_API_KEY"
    key_required = True
    #: /v1/chat/completions takes an image as an image_url part, so every
    #: backend on this wire format can carry one. Whether the chosen MODEL can
    #: see it is a separate question, answered by the catalog.
    supports_images = True

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        self.model = model or os.environ.get(
            f"{self.name.upper()}_MODEL", self.default_model
        )
        self.api_key = api_key if api_key is not None else (os.environ.get(self.key_env, "") or _saved_key(self.key_env))
        self.base_url = (base_url or os.environ.get(
            f"{self.name.upper()}_BASE_URL", self.default_base
        )).rstrip("/")

    def is_ready(self) -> tuple[bool, str]:
        if self.key_required and not self.api_key:
            if self.name == "openai":
                with suppressed("from .connections import ConnectionStatus, get_connection …"):
                    from .chatgpt_auth import get_chatgpt_access_token
                    from .connections import ConnectionStatus, get_connection
                    conn = get_connection("openai")
                    if conn.connection_status == ConnectionStatus.DISCONNECTED:
                        return False, f"Disconnected. Set {self.key_env} or Sign in with ChatGPT"
                    if conn.connection_status == ConnectionStatus.ACCOUNT_CONNECTED:
                        if get_chatgpt_access_token():
                            return True, ""
                        return False, "ChatGPT session expired or missing token. Please reconnect in Models & Accounts."
                    if get_chatgpt_access_token():
                        return True, ""
                return False, f"set {self.key_env} or Sign in with ChatGPT"
            return False, f"set {self.key_env}"
        return True, ""

    def _to_openai(self, messages: list[Message]) -> list[dict]:
        out = []
        for m in messages:
            if m.role == "tool":
                out.append({
                    "role": "tool", "tool_call_id": m.tool_call_id,
                    "content": m.content,
                })
            elif m.role == "assistant" and m.tool_calls:
                tc_list = []
                for tc in m.tool_calls:
                    item = {
                        "id": tc.id, "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else str(tc.arguments),
                        },
                    }
                    if getattr(tc, "extra_content", None):
                        item["extra_content"] = tc.extra_content
                    tc_list.append(item)
                out.append({
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": tc_list,
                })
            elif m.role == "user" and m.images:
                # The multimodal form is a content ARRAY; sending it for a
                # text-only turn is a needless difference, so only user turns
                # that actually carry an image take this shape.
                parts: list[dict] = [{"type": "image_url",
                                      "image_url": {"url": img.as_data_url()}}
                                     for img in m.images]
                if m.content:
                    parts.append({"type": "text", "text": m.content})
                out.append({"role": "user", "content": parts})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def _refine_error(self, err):
        """Hook for a provider to sharpen a classified error. Default: as-is."""
        return err

    def _reasoning_effort(self) -> dict:
        """`{"reasoning_effort": ...}` when the model takes it, else `{}`.

        Spread into the payload rather than set conditionally at two call
        sites: `stream` and `chat` build their own bodies, and a parameter
        added to one of them is a feature that works until the stream falls
        back. See `models/reasoning.py`.
        """
        effort = reasoning.openai_effort(self.model, reasoning.wanted())
        return {"reasoning_effort": effort} if effort else {}

    def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        """Real streaming for anything speaking the chat-completions dialect.

        The ChatGPT-subscription path (no API key) has its own transport, so it
        falls through to the single-piece default rather than being reimplemented.
        """
        from .streaming import (
            from_result,
            openai_events,
            raise_for_status,
            sse_payloads,
        )

        if self.name == "openai" and not self.api_key:
            yield from from_result(self.chat(messages, tools=tools,
                                             temperature=temperature,
                                             max_tokens=max_tokens))
            return

        payload = {"model": self.model, "messages": self._to_openai(messages),
                   "temperature": temperature, "max_tokens": max_tokens,
                   "stream": True, **self._reasoning_effort()}
        if tools:
            payload["tools"] = [{"type": "function",
                                 "function": {"name": t.name,
                                              "description": t.description,
                                              "parameters": t.parameters}}
                                for t in tools]
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.stream("POST", f"{self.base_url}/chat/completions",
                              headers=headers, json=payload, timeout=300) as resp:
                raise_for_status(resp)
                yield from openai_events(sse_payloads(resp.iter_lines()))
            return
        except Exception as exc:
            log.debug("%s streaming failed, falling back: %s", self.name, exc)
        yield from from_result(self.chat(messages, tools=tools,
                                         temperature=temperature,
                                         max_tokens=max_tokens))

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        if self.name == "openai" and not self.api_key:
            from .chatgpt_auth import chat_with_chatgpt_subscription, get_chatgpt_access_token
            tok = get_chatgpt_access_token()
            if tok:
                return chat_with_chatgpt_subscription(
                    messages, model=self.model, tools=tools,
                )
            return ChatResult(text="⚠️ No OpenAI API key or ChatGPT subscription connected. Please sign in with ChatGPT or set OPENAI_API_KEY in Models & Accounts.")

        payload = {
            "model": self.model,
            "messages": self._to_openai(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._reasoning_effort(),
        }
        if tools:
            payload["tools"] = [{
                "type": "function",
                "function": {
                    "name": t.name, "description": t.description,
                    "parameters": t.parameters,
                },
            } for t in tools]

        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=payload, timeout=120,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            err = classify_http(self.name, exc.response.status_code, exc.response.text,
                                model=self.model, key_env=self.key_env)
            return ChatResult(text=self._refine_error(err).as_reply())
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            err = classify_exception(self.name, exc, model=self.model, base_url=self.base_url)
            if self.name == "ollama" and err.kind is ErrorKind.NETWORK:
                err.message = "Ollama isn't running. Start it with `ollama serve`."
            return ChatResult(text=self._refine_error(err).as_reply())
        try:
            data = resp.json()
            choice = data["choices"][0]["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            err = ProviderError(ErrorKind.BAD_REQUEST, self.name, model=self.model,
                                message=f"Unexpected response from {self.name}. "
                                        "Try again or switch models.")
            return ChatResult(text=err.as_reply())
        calls = []
        for tc in choice.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(
                id=tc.get("id", str(uuid.uuid4())),
                name=tc["function"]["name"], arguments=args,
            ))
        usage = data.get("usage") or {}
        return ChatResult(
            text=choice.get("content") or "",
            tool_calls=calls,
            raw=data,
            finish_reason=data["choices"][0].get("finish_reason", "stop"),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )


class OpenRouterProvider(OpenAICompatProvider):
    name = "openrouter"
    default_base = "https://openrouter.ai/api/v1"
    default_model = "anthropic/claude-sonnet-5"
    key_env = "OPENROUTER_API_KEY"
    key_required = True


class OllamaProvider(OpenAICompatProvider):
    """Local models via Ollama — fully offline, no key."""
    name = "ollama"
    default_base = "http://localhost:11434/v1"
    default_model = "llama3.2"
    key_env = "OLLAMA_API_KEY"
    key_required = False

    def is_ready(self) -> tuple[bool, str]:
        try:
            httpx.get(self.base_url.replace("/v1", "") + "/api/tags", timeout=2)
            return True, ""
        except Exception:
            return False, "Ollama not running on localhost:11434"
