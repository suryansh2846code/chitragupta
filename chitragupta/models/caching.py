"""Prompt caching — the part of a turn the user must not pay for twelve times.

A turn is a loop. Round two sends everything round one sent, plus the tool
results; round twelve sends all of it again. The agent's system prompt and its
tool schemas are *identical on every one of those rounds* and they are the bulk
of the payload — `agents/prompt.py` composes a long one and `build_tools` hands
over the full JSON Schema for every tool the agent owns.

Uncached, a twelve-round turn at High bills that prefix twelve times. On the
user's own key. That is the single largest avoidable cost in the app, and it is
invisible: nothing in the UI says "you just re-sent 9,000 tokens".

Anthropic caches a *prefix*, in this fixed order:

    tools  →  system  →  messages

A `cache_control` marker on a content block means "everything up to and
including this block is cacheable". At most four markers, and a prefix shorter
than the vendor minimum is silently ignored rather than rejected — so the
failure mode of guessing wrong is a missed saving, never an error.

Where the four go, and what each one buys:

1. **The last tool.** Tool schemas change when the user changes an agent's
   tools, which is ~never. Survives *between* turns.
2. **The last system block the caller marked stable.** `Message.stable` says
   "the prefix ending here does not change turn to turn" — the runtime marks
   the agent's own prompt and leaves recall and the task list unmarked, because
   those are rebuilt per turn and would poison a cross-turn hit.
3. **and 4. The last two messages.** Rolling. Round N+1 reads what round N
   wrote, so the accumulated tool results — the part that actually grows — are
   billed once each instead of once per remaining round. Two rather than one is
   the vendor's own advice: it keeps a hit when the newest write has not landed.

There is deliberately no marker at the end of `system`. It would cost one of
the four to cache a few hundred tokens of recall, and markers 3 and 4 already
include the whole system block in their prefix.

OpenAI, DeepSeek and the OpenAI-compatible providers cache automatically and
have no parameter to set; what they need from us is a *stable prefix*, which is
the same discipline. Nothing here is sent to them.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

#: The vendor's hard limit. Exceeding it is a 400, so it is enforced here
#: rather than hoped for.
MAX_BREAKPOINTS = 4

#: Below roughly this many characters the prefix cannot reach the vendor's
#: minimum cacheable length (1024 tokens, 2048 on Haiku), and every marker is a
#: no-op. Skipping keeps small requests — a summary call, a title — exactly as
#: they were, which is also what keeps them easy to reason about in a trace.
MIN_CACHEABLE_CHARS = 4096

_EPHEMERAL = {"type": "ephemeral"}


def _marked(block: dict[str, Any]) -> dict[str, Any]:
    return {**block, "cache_control": dict(_EPHEMERAL)}


def _as_blocks(content: Any) -> list[dict[str, Any]]:
    """A message body in block form, whatever shape it arrived in.

    `_to_blocks` emits a bare string for a plain user turn. A string cannot
    carry `cache_control`, so a rolling marker has to widen it first.
    """
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content or "")}]


def _payload_size(system: list[dict], messages: list[dict],
                  tools: list[dict] | None) -> int:
    """A cheap stand-in for a token count.

    Counting properly means a tokeniser we do not ship and a dependency we do
    not want for a threshold whose only job is to skip obviously-small
    requests. Characters are wrong by a constant factor and that is enough.
    """
    size = sum(len(str(b.get("text", ""))) for b in system)
    size += sum(len(str(m.get("content", ""))) for m in messages)
    size += sum(len(str(t)) for t in (tools or []))
    return size


def apply(system: list[dict[str, Any]],
          messages: list[dict[str, Any]],
          tools: list[dict[str, Any]] | None,
          *, stable_system_upto: int = -1) -> tuple[
              list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Return the three payload pieces with cache markers placed.

    `stable_system_upto` is the index of the last system block whose prefix is
    the same on the next turn; -1 means the caller could not promise any.

    Pure: the inputs are not mutated, because a provider that retries a request
    must be able to rebuild it from the same messages.
    """
    if _payload_size(system, messages, tools) < MIN_CACHEABLE_CHARS:
        return system, messages, tools

    system = deepcopy(system)
    messages = deepcopy(messages)
    tools = deepcopy(tools) if tools else tools
    spent = 0

    # 1 — tool schemas. First in the vendor's prefix order, so this marker is
    # the only one that can survive a change anywhere else in the payload.
    if tools:
        tools[-1] = _marked(tools[-1])
        spent += 1

    # 2 — the stable head of the system prompt.
    if 0 <= stable_system_upto < len(system) and spent < MAX_BREAKPOINTS:
        system[stable_system_upto] = _marked(system[stable_system_upto])
        spent += 1

    # 3, 4 — rolling, over the tail of the conversation. Newest first so that
    # with only one marker left it goes where the next round will read it.
    for message in reversed(messages[-2:]):
        if spent >= MAX_BREAKPOINTS:
            break
        blocks = _as_blocks(message.get("content"))
        if not blocks:
            continue
        message["content"] = [*blocks[:-1], _marked(blocks[-1])]
        spent += 1

    return system, messages, tools


def cache_stats(usage: dict[str, Any] | None) -> tuple[int, int]:
    """`(read, written)` cached prompt tokens from a vendor usage block.

    Reported rather than folded into `input_tokens`: a cached read is still a
    token the model processed, so the turn's budget must see it, but the two
    cost very different amounts and a number that hides that cannot be used to
    tell whether any of this worked.
    """
    usage = usage or {}
    return (int(usage.get("cache_read_input_tokens") or 0),
            int(usage.get("cache_creation_input_tokens") or 0))
