"""Claude Code provider — run agents on your terminal Claude (`claude -p`).

This uses the Claude Code CLI already installed on your machine as the model
backend, so Chitragupta's agents reason with Claude using your existing Claude
auth — no separate API key wired into Chitragupta.

Claude Code headless returns text, not our structured tool-calls — so for as
long as this file existed, the `tools=` argument it accepts was **dropped**, and
this docstring said that was fine because recall still made the agent "know
you". It was not fine. An agent holding fifty-four tools called none of them,
and a user who asked their Chief of Staff to check their mail was told that no
mail tools existed and that the search "only surfaces `SendMessage`" — a tool
belonging to the CLI, not to this app.

The tools now go over MCP (`tool_bridge.py`): a server carrying that turn's
tools, on a loopback port that exists for the length of one call, which the CLI
is pointed at with `--mcp-config`. It runs its own loop over them and returns
the finished answer, and `_bridge_args` turns the CLI's **own** tools off on the
way in — otherwise reading your mail would come with `Bash` over your whole
filesystem, granted by nobody.

Each call uses your Claude usage, so it is not free like the local Ollama
backend. See `docs/development/cli-tool-bridge.md`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..log import get_logger
from .base import ChatResult, LLMProvider, Message, parse_cli_json
from .cli_login import augmented_path
from .errors import (
    ErrorKind,
    ProviderError,
    classify_cli,
    is_limit_notice,
    parse_reset_at,
)

log = get_logger(__name__)

# Bin dirs GUI apps miss: apps launched from Finder/.app get a minimal PATH
# (/usr/bin:/bin:/usr/sbin:/sbin), so Homebrew, npm-global and the Claude Code
# local install are invisible to shutil.which. Search them explicitly.
_EXTRA_BIN_DIRS = [
    "/opt/homebrew/bin", "/usr/local/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".claude" / "local"),
    str(Path.home() / ".npm-global" / "bin"),
    str(Path.home() / "bin"),
    "/opt/homebrew/sbin",
]


def _augmented_path() -> str:
    return augmented_path(_EXTRA_BIN_DIRS)


def _answered(result: ChatResult, bridge) -> ChatResult:
    """The CLI's answer, with the tool calls it already made taken back out.

    A bridged run does its own agentic loop: it calls our tools over MCP,
    reads the results, and returns the finished text. Those calls still appear
    in the stream as `tool_use` blocks, and `streaming.anthropic_events` — which
    is right to collect them, because on the Messages API path they are work
    that has *not* happened yet — hands them back as `ChatResult.tool_calls`.

    Left there, `runtime.py` would see `wants_tools`, take the names the CLI
    gave them (`mcp__chitragupta__list_mail`) and run every one a second time.
    Reading the user's mail twice is the harmless version; the turn also never
    terminates on the answer the CLI had already written.
    """
    if bridge is None or not result.tool_calls:
        return result
    result.tool_calls = []
    return result


def find_claude() -> str | None:
    """Locate the `claude` binary even when PATH is the stripped GUI default."""
    found = shutil.which("claude", path=_augmented_path())
    if found:
        return found
    for d in _EXTRA_BIN_DIRS:
        cand = Path(d) / "claude"
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


class ClaudeCodeProvider(LLMProvider):
    name = "claude-code"
    model = "claude-code"

    def __init__(self, model: str | None = None, **_: object) -> None:
        self._bin = find_claude()
        # Only honor a model name that is actually a Claude model — the global
        # CHITRAGUPTA_MODEL_NAME may be set for another backend (e.g. an Ollama tag).
        if model and any(k in model.lower()
                         for k in ("claude", "haiku", "sonnet", "opus", "fable")):
            self.model = model
        # else keep default "claude-code" → let Claude Code pick its default model

    def is_ready(self) -> tuple[bool, str]:
        if not self._bin:
            return False, ("Claude Code CLI ('claude') not found. Install it "
                           "(npm i -g @anthropic-ai/claude-code or brew install "
                           "claude-code), or pick another model in the dropdown.")
        return True, ""

    def _split(self, messages: list[Message]) -> tuple[str, str]:
        """Return (system_prompt, user_prompt).

        Claude Code's `-p` expects a real instruction, not a fake User:/Assistant:
        transcript (that trips its stop-sequence handling and errors out). So we
        send the latest user message as the prompt, and everything else — our
        system instructions, injected brain/date/task context, and prior turns —
        via --append-system-prompt.
        """
        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        convo = [m for m in messages if m.role in ("user", "assistant", "tool")]
        last_user = next((m for m in reversed(convo) if m.role == "user"), None)
        prompt = last_user.content if last_user else ""
        prior = convo[: convo.index(last_user)] if last_user in convo else convo
        hist = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant' if m.role == 'assistant' else 'Tool'}: {m.content}"
            for m in prior if m.content
        )
        if hist:
            system_parts.append("Recent conversation so far:\n" + hist)
        return "\n\n".join(system_parts), prompt


    # ── handing the CLI our tools ───────────────────────────────────────

    def _bridge(self, tools):
        """An MCP server carrying this turn's tools, or None if there are none.

        This is the whole reason `tools=` stopped being a lie on this backend.
        `claude -p` returns text, so for three years of this file the argument
        was accepted and dropped: an agent holding fifty-four tools was given
        none, and the model — having none of ours — went looking with the CLI's
        own and answered about `ToolSearch` and `SendMessage`, which are not
        tools this app has ever had.

        Failing to start one is not fatal. The turn then behaves exactly as it
        did before this existed, which is worse than it should be but is still
        an answer; refusing to reply because a loopback port would not bind
        would be trading a degraded turn for no turn.
        """
        if not tools:
            return None
        try:
            from .tool_bridge import ToolBridge, current_observer

            bridge = ToolBridge(list(tools), observer=current_observer())
            bridge.start()
            return bridge
        except Exception as exc:
            log.debug("could not start the tool bridge, answering without "
                      "tools: %s", exc)
            return None

    @staticmethod
    def _bridge_args(bridge) -> list[str]:
        """The flags that point the CLI at our tools and at nothing else.

        Every one of these is load-bearing, and each was checked against the
        installed CLI rather than assumed:

        * `--strict-mcp-config` — the user's *own* MCP servers are configured
          for their terminal work. Without this they would silently join an
          agent's toolset, so the tools an agent has would depend on a file
          this app does not manage and the user was not thinking about.
        * `--tools ""` — turn the CLI's built-ins off. Left on, a Chief of
          Staff would hold `Bash`, `Write` and `Edit` over the user's whole
          filesystem because they asked it to read their mail. What an agent
          may reach is decided in the agent library, not by which backend
          happened to answer.
        * `--allowedTools` — headless has nobody to ask, so a tool that is not
          pre-allowed is a tool that is refused. Enumerated, never a wildcard:
          the list is the point.

        Comma-joined rather than passed as separate words: both flags are
        variadic, and a variadic flag followed by `--model` eats it.
        """
        return ["--mcp-config", bridge.config_path(),
                "--strict-mcp-config",
                "--tools", "",
                "--allowedTools", ",".join(bridge.allowed_tools())]

    def _stream_command(self, messages, tools, bridge=None):
        """`claude -p --output-format stream-json` — verified against the real
        CLI: it wraps Anthropic Messages events under `{"type":"stream_event"}`,
        and `--verbose` is required for the streaming format to be emitted."""
        system_prompt, prompt = self._split(messages)
        cmd = [self._bin, "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages"]
        if bridge is not None:
            cmd += self._bridge_args(bridge)
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]
        if self.model and self.model != "claude-code":
            cmd += ["--model", self.model]
        return cmd, prompt, {**os.environ, "PATH": _augmented_path()}

    def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=1500):
        """Stream by asking the CLI for incremental events.

        All three vendor CLIs emit Anthropic Messages events, one JSON object
        per line, so `streaming.anthropic_events` parses them all.

        If the stream produces no text at all — an older CLI that does not know
        the flag, a format that has moved — nothing has been yielded yet, so the
        plain call runs instead and the user gets an answer rather than silence.
        Falling back *after* emitting text would duplicate it, which is why the
        decision hangs on whether any arrived.
        """
        from .streaming import from_result, stream_cli

        bridge = self._bridge(tools)
        try:
            cmd, stdin, env = self._stream_command(messages, tools, bridge)
            if cmd is None:
                yield from from_result(self.chat(messages, tools=tools,
                                                 temperature=temperature,
                                                 max_tokens=max_tokens))
                return

            saw_text = False
            done = None
            try:
                for event in stream_cli(cmd, env=env, stdin=stdin,
                                        provider=self.name):
                    if event.kind == "text" and event.text:
                        saw_text = True
                        yield event
                    elif event.kind == "done":
                        done = event
            except Exception as exc:
                log.debug("%s streaming failed: %s", self.name, exc)
                saw_text = False

            if saw_text and done is not None and done.result is not None:
                _answered(done.result, bridge)
                yield done
                return
        finally:
            if bridge is not None:
                bridge.close()
        yield from from_result(self.chat(messages, tools=tools,
                                         temperature=temperature,
                                         max_tokens=max_tokens))

    @staticmethod
    def _invoke(cmd, prompt, env, bridge):
        """Run the CLI once, and stop serving tools the moment it exits.

        The bridge is closed here rather than at the end of `chat()` because
        this is when it stops being needed, and because `chat()` returns from
        six places — a `finally` around all of them would have to wrap the
        whole body, and a listening socket that outlives its turn because one
        early return missed it is the kind of leak nobody finds.
        """
        try:
            return subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, timeout=180,
                env=env,
            )
        finally:
            if bridge is not None:
                bridge.close()

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=1500):
        if not self._bin:
            return ChatResult(text="Claude Code CLI not available.")

        from .accounts import detect_claude_account
        from .entitlements import evaluate_model_entitlement

        acct = detect_claude_account()
        user_plan = acct.get("plan")
        context = {"disabled_models": acct.get("disabled_models", {})}
        if self.model and self.model != "claude-code":
            locked, plan_req = evaluate_model_entitlement(
                provider="claude-code",
                model_id=self.model,
                is_connected=True,
                user_plan=user_plan,
                context=context,
            )
            if locked:
                return ChatResult(
                    text=f"⚠️ Model `{self.model}` is currently locked ({plan_req}). "
                         "Please select an unlocked model (such as Claude Opus 5 or Claude Sonnet 5) in the model selector.",
                    finish_reason="stop",
                )
        system_prompt, prompt = self._split(messages)
        bridge = self._bridge(tools)
        cmd = [self._bin, "-p", "--output-format", "json"]
        if bridge is not None:
            cmd += self._bridge_args(bridge)
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]
        if self.model and self.model != "claude-code":
            cmd += ["--model", self.model]
        env = {**os.environ, "PATH": _augmented_path()}
        try:
            proc = self._invoke(cmd, prompt, env, bridge)
        except subprocess.TimeoutExpired:
            return ChatResult(text=ProviderError(
                ErrorKind.TIMEOUT, "claude-code", model=self.model, retryable=True,
                message="Claude Code timed out. Try again, or switch model.").as_reply())

        data = parse_cli_json(proc.stdout)
        text = (data.get("result") or "").strip()

        if proc.returncode != 0 or data.get("is_error"):
            # A plan limit is not an answer. The CLI reports one with `is_error`
            # AND a `result` string — "5-hour session limit · resets 12am
            # (Asia/Calcutta)" — and returning that as the reply put it in the
            # transcript looking exactly like something the model said, with no
            # retry and no idea how long to wait.
            if text and is_limit_notice(text):
                reset = parse_reset_at(text)
                return ChatResult(text=ProviderError(
                    kind=ErrorKind.RATE_LIMIT, provider="claude-code",
                    message=text.strip(), model=self.model, retryable=True,
                    retry_at=reset.isoformat() if reset else "").as_reply())
            if text:                       # Claude returned a usable message anyway
                return ChatResult(text=text, finish_reason="stop")
            err = classify_cli("claude-code", proc.returncode,
                               proc.stdout or str(data.get("subtype") or ""),
                               proc.stderr or "", model=self.model)
            return ChatResult(text=err.as_reply())
        return ChatResult(text=text or proc.stdout.strip(), finish_reason="stop")
