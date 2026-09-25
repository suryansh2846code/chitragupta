"""Asking a reasoning model to actually reason.

The catalog has known which models can think since `discovery.py` was written —
`reasoning=True` sits on the entry — and nothing in the app ever asked one to.
An Opus selected at High answered exactly like a model with no such capability,
because the request carried no reasoning parameter at all. The user paid the
reasoning-model price and got none of it.

The two vendors spell it differently and only one of them is easy.

**OpenAI** takes a word: `reasoning_effort` of `low` | `medium` | `high`. A
model that does not support it rejects the parameter, so it is sent only when
the catalog says the model reasons.

**Anthropic** takes a token budget, and enabling it changes three other things
about the request — each of which is a 400 if it is missed:

* `max_tokens` must exceed the budget. The thinking comes out of the same
  allowance as the answer, so a budget equal to the ceiling leaves no room to
  write anything.
* `temperature` must be 1. Our loop runs at 0.15 for reliable tool use, so
  thinking and that setting cannot both be honoured; thinking wins, because a
  model that reasons is already more reliable than a cold one that does not.
* **The thinking blocks must be handed back on the next round.** This is the
  one that bites. In a tool loop the assistant turn is replayed on every
  subsequent request, and Anthropic requires the thinking blocks to come back
  with it, signature intact and in their original order. Drop them and round
  two fails — so `Message.reasoning` carries them, opaque, and the provider
  puts them back at the head of the assistant turn where they belong.

Everything here is best-effort by design. `anthropic_budget` returning 0 or
`openai_effort` returning "" means the request goes out exactly as it did
before, which is why a model we have guessed wrong about degrades to the old
behaviour rather than to an error — and `rejected_thinking` catches the case
where the guess was wrong the other way.
"""
from __future__ import annotations

import contextvars
from typing import Any

#: Anthropic's own floor. A budget under this is rejected, and asking for 512
#: tokens of thought is not worth a round trip to find that out.
MIN_ANTHROPIC_BUDGET = 1024

#: Thinking is taken out of `max_tokens`, so the budget is capped at this
#: fraction of it and the answer always has somewhere to go.
MAX_SHARE_OF_OUTPUT = 0.6

#: Model-id fragments whose owners support a thinking budget. Matched on the
#: id because that is what the provider holds; the catalog's `reasoning` flag
#: is broader (it covers o-series and Gemini too) and would enable the
#: Anthropic-shaped parameter on models that have never heard of it.
_ANTHROPIC_THINKERS = ("claude-opus-4", "claude-opus-5", "claude-sonnet-4",
                       "claude-sonnet-5", "claude-3-7-sonnet", "claude-haiku-4-5")

#: OpenAI-compatible ids that take `reasoning_effort`.
_OPENAI_THINKERS = ("o1", "o3", "o4", "gpt-5", "gpt-6", "deepseek-reasoner")


def _matches(model: str, fragments: tuple[str, ...]) -> bool:
    mid = (model or "").lower()
    return any(f in mid for f in fragments)


def anthropic_budget(model: str, thinking_tokens: int, max_tokens: int) -> int:
    """Tokens of thought to ask for, or 0 for "do not enable this".

    Clamped rather than refused: a caller that asks for more than the answer can
    afford gets the largest budget that still leaves room, which is what they
    meant. Asking for something the vendor will reject is the only outcome worth
    preventing outright.
    """
    if thinking_tokens <= 0 or not _matches(model, _ANTHROPIC_THINKERS):
        return 0
    budget = min(thinking_tokens, int(max_tokens * MAX_SHARE_OF_OUTPUT))
    return budget if budget >= MIN_ANTHROPIC_BUDGET else 0


def openai_effort(model: str, thinking_tokens: int) -> str:
    """`reasoning_effort` for an OpenAI-compatible model, or "" for none.

    A budget in tokens does not map onto three words, so this is a banding
    rather than a conversion — and it is banded off the *effort profile's* own
    numbers so that moving a level's budget moves both vendors together.
    """
    if thinking_tokens <= 0 or not _matches(model, _OPENAI_THINKERS):
        return ""
    if thinking_tokens >= 6000:
        return "high"
    if thinking_tokens >= 1500:
        return "medium"
    return "low"


def rejected_thinking(status: int, body: str) -> bool:
    """Did the vendor refuse *because of* the reasoning parameter?

    The guard behind the retry. A model we wrongly believed could think must
    cost the user one silent extra round trip, never an error message about a
    feature they did not ask for and cannot see. Deliberately narrow: a 400
    about anything else is a real error and must stay one.
    """
    if status != 400:
        return False
    text = (body or "").lower()
    return any(word in text for word in
               ("thinking", "reasoning_effort", "budget_tokens"))


def strip(payload: dict[str, Any]) -> dict[str, Any]:
    """The same request with every reasoning parameter removed.

    `temperature` is deliberately NOT restored: the caller's original value was
    overwritten on the way in, and guessing it back here would put the number in
    two places. The retry path passes it explicitly.
    """
    return {k: v for k, v in payload.items()
            if k not in ("thinking", "reasoning_effort")}


#: How much thought the turn running *on this task* asked for.
#:
#: A `ContextVar`, not an attribute on the provider. `get_provider` is
#: `@lru_cache`d, so one instance answers every concurrent turn — the same
#: reason `tool_bridge` keeps its observer in the context rather than on the
#: provider, where "an attribute would draw one agent's tool calls into
#: another's trace". Set on the provider, a turn at Low would have silently
#: cancelled a turn at High that was already mid-loop, and the symptom would be
#: "the expensive setting sometimes does nothing".
_BUDGET: contextvars.ContextVar[int] = contextvars.ContextVar(
    "chitragupta_thinking_tokens", default=0)


def for_this_turn(tokens: int) -> object:
    """Ask for `tokens` of thought for the duration of this turn.

    Returns a token for `release`. The runtime resets it on every exit path,
    exactly as it does the delegation chain — a ContextVar set and never reset
    leaks this turn's setting into whatever the worker thread picks up next.
    """
    return _BUDGET.set(max(0, int(tokens or 0)))


def release(token: object) -> None:
    _BUDGET.reset(token)                                # type: ignore[arg-type]


def wanted() -> int:
    """The current turn's thinking budget, or 0 if nobody asked for one."""
    return _BUDGET.get()
