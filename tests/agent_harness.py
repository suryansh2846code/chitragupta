"""A provider you can script, so the agent loop can be tested without a model.

The loop's interesting behaviour — how deep it goes, what it does when a model
keeps asking the same question, whether independent calls overlap — is invisible
to a test that mocks `run_turn` and invisible to a test that calls a real model.
This lets a test say exactly what the model does on each round and then assert
what the loop did about it.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from chitragupta.models.base import (
    DEFAULT_MAX_OUTPUT,
    ChatResult,
    LLMProvider,
    ToolCall,
)


@dataclass
class ScriptedProvider(LLMProvider):
    """Returns a pre-written response per round.

    `script` is a list of rounds. Each round is either a string (a final answer,
    ending the turn) or a list of `(tool_name, arguments)` pairs to request. Once
    the script runs out, the provider answers — so a test cannot hang.
    """

    script: list = field(default_factory=list)
    name: str = "scripted"
    model: str = "scripted-1"
    final_answer: str = "done"

    calls: list = field(default_factory=list, repr=False)
    tools_offered: list = field(default_factory=list, repr=False)
    _round: int = 0

    def is_ready(self) -> tuple[bool, str]:
        return True, ""

    def chat(self, messages, *, tools=None, temperature=0.7,
             max_tokens=DEFAULT_MAX_OUTPUT):
        self.calls.append(messages)
        self.tools_offered.append([t.name for t in (tools or [])])
        if not tools:
            # A model handed no tools cannot call one. The loop relies on this
            # to turn a budget-exhausted turn into an answer, so the double has
            # to honour it or it tests nothing.
            return ChatResult(text=self.final_answer)
        if self._round >= len(self.script):
            return ChatResult(text=self.final_answer)
        step = self.script[self._round]
        self._round += 1
        if isinstance(step, str):
            return ChatResult(text=step)
        return ChatResult(
            text="",
            tool_calls=[ToolCall(id=f"c{self._round}-{i}", name=n, arguments=a)
                        for i, (n, a) in enumerate(step)],
        )

    @property
    def rounds_used(self) -> int:
        return len(self.calls)


class RecordingTool:
    """Counts executions and can be made slow, to observe overlap."""

    def __init__(self, delay: float = 0.0, output: str = "result"):
        self.delay = delay
        self.output = output
        self.lock = threading.Lock()
        self.executions = 0
        self.live = 0
        self.peak_live = 0

    def __call__(self, **kwargs) -> str:
        with self.lock:
            self.executions += 1
            self.live += 1
            self.peak_live = max(self.peak_live, self.live)
        try:
            if self.delay:
                time.sleep(self.delay)
            return f"{self.output}:{sorted(kwargs.items())}"
        finally:
            with self.lock:
                self.live -= 1
