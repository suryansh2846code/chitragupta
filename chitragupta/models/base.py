"""Provider-agnostic LLM interface with tool calling.

Every backend (Claude, OpenAI, OpenRouter, Ollama, or a subscription-session
proxy) implements `LLMProvider.chat`, normalizing its wire format to the same
Message / ToolCall shapes so the agent runtime is model-agnostic — this is the
"bring your own model" layer.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: The ceiling on one reply when a caller does not choose. Every provider
#: signature reads it from here: it used to be the literal 1500 written out
#: fourteen times, and a number duplicated fourteen times is a number that gets
#: raised in thirteen places. Under a page of text — a drafted email fitted, an
#: eight-step plan with its reasoning did not, and the loop carried the severed
#: half into the next round as if it were a finished thought. The agent loop
#: does not use this: it passes `Effort.max_output_tokens`, which is the number
#: the user's chosen gear actually implies.
DEFAULT_MAX_OUTPUT = 4000


def _saved_key(env_key: str) -> str:
    """Fall back to a key saved from the UI (~/Library/Chitragupta/secrets.json)
    when the matching env var isn't set — so users can paste an API key in the
    onboarding / workspace instead of editing their shell profile."""
    try:
        from ..config import get_settings
        return get_settings().get_secret(env_key) or ""
    except Exception:
        return ""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    extra_content: dict[str, Any] | None = None


@dataclass
class Message:
    role: str                      # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None   # for role=="tool": which call this answers
    name: str | None = None
    #: Images attached to a user turn (`models.images.ImageInput`). Additive on
    #: purpose: `content` stays a plain string, so every provider that has not
    #: been taught about images keeps serialising exactly as it did, and the
    #: ones that have opt in by reading this. Turning `content` into a union
    #: would have been a rename landed on one side of eight call sites.
    images: list[Any] = field(default_factory=list)
    #: "The prefix ending at this message is the same on the next turn." Only
    #: meaningful on a system message, and only a hint: a provider with no
    #: prompt cache ignores it and nothing changes. The runtime sets it on the
    #: agent's own prompt and leaves recall and the task list unmarked, because
    #: those are rebuilt every turn and marking them would cache a prefix that
    #: can never be hit again. See `models/caching.py`.
    stable: bool = False
    #: Opaque reasoning blocks the model produced on THIS assistant turn, kept
    #: exactly as the vendor sent them, signature included. Anthropic requires
    #: them handed back with the assistant turn on every subsequent round of a
    #: tool loop — drop one and round two is a 400, not a degraded answer. We
    #: never read inside them, and nothing else in the app should either: a
    #: model's private reasoning is not the answer and must not be shown as one.
    reasoning: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": [tc.__dict__ for tc in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]     # JSON Schema
    handler: Callable[..., str] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] | None = None
    finish_reason: str = "stop"
    input_tokens: int = 0        # prompt tokens (0 if the provider doesn't report)
    output_tokens: int = 0       # completion tokens
    #: Of `input_tokens`, how many were served from the provider's prompt cache.
    #: Counted inside the input total on purpose — the model processed them and
    #: the turn's budget must see them — and reported separately because they
    #: cost a fraction, and a single number cannot answer "is caching working".
    cached_tokens: int = 0
    #: See `Message.reasoning` — carried out of the provider so the loop can
    #: put it back on the next request.
    reasoning: list[Any] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMProvider:
    name: str = "base"
    model: str = ""
    #: Can this BACKEND carry an image at all? Not "does the model see" — that
    #: is per model and lives in the catalog. This is about the transport: a
    #: vendor CLI takes a prompt on argv, so there is nowhere for an image to
    #: go no matter which model is selected. Default False so a provider that
    #: has not been taught about images refuses cleanly instead of silently
    #: dropping them, which is the failure mode worth designing against.
    supports_images: bool = False

    def is_ready(self) -> tuple[bool, str]:
        return True, ""

    def chat(
        self,
        messages: list[Message],
        *,
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = DEFAULT_MAX_OUTPUT,
    ) -> ChatResult:  # pragma: no cover - interface
        raise NotImplementedError

    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = DEFAULT_MAX_OUTPUT,
    ):
        """The same answer, delivered as it is written.

        The default is a complete, correct stream that happens to arrive in one
        piece, so every provider supports streaming from the day the seam
        exists and callers never branch on whether a backend can do it. A
        provider that really streams overrides this; the agent loop cannot tell
        the difference except in timing, which is the whole point.
        """
        from .streaming import from_result

        yield from from_result(self.chat(messages, tools=tools,
                                         temperature=temperature,
                                         max_tokens=max_tokens))


def parse_cli_json(stdout: str) -> dict:
    """Pull a JSON object out of a CLI's stdout.

    Agent CLIs prepend plain-text notices (deprecation warnings, update nags)
    before the payload, so the output does not necessarily start with '{'.
    Scan for the first brace that parses; otherwise every such call reads as a
    total failure.
    """
    import json as _json

    out = (stdout or "").strip()
    if not out:
        return {}
    start = out.find("{")
    while start != -1:
        try:
            return _json.loads(out[start:])
        except _json.JSONDecodeError:
            start = out.find("{", start + 1)
    return {}
