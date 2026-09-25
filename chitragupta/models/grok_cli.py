"""Grok provider via xAI's official CLI (`grok -p`).

xAI sells two separately-billed things: the developer API at `api.x.ai`, paid
for with credits from console.x.ai, and a SuperGrok subscription covering
grok.com and the apps. A subscription grants no API credits, so an OAuth
sign-in fails every `api.x.ai` request with 402
`personal-team-blocked:spending-limit`.

The sanctioned way to run a subscription is xAI's own agentic CLI, Grok Build:

    grok -p "<prompt>" --output-format json -m <model>

installed with `curl -fsSL https://x.ai/cli/install.sh | bash` (or
`npm i -g @xai-official/grok`) and signed in with `grok login`. The
`grok-cli:access` scope our OAuth token already carries is for exactly this.

Same shape as the Claude Code and Cursor backends: the CLI owns auth and
inference, and returns text rather than structured tool calls — so agents answer
from the context Chitragupta injects rather than calling tools themselves.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from ..log import get_logger
from .base import DEFAULT_MAX_OUTPUT, ChatResult, LLMProvider, Message, parse_cli_json
from .cache import ttl_cached
from .cli_login import CliLoginSession, augmented_path
from .errors import ErrorKind, ProviderError, classify_cli

log = get_logger(__name__)

# GUI-launched apps get a minimal PATH; the installer uses ~/.local/bin.
_EXTRA_BIN_DIRS = [
    str(Path.home() / ".local" / "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    str(Path.home() / ".grok" / "bin"),
    str(Path.home() / ".npm-global" / "bin"),
]

# We install and pin this CLI ourselves (`models/cli_manager.py`), so the way
# out of both of these is a button in the Models drawer, never a terminal.
INSTALL_HINT = (
    "Grok needs xAI's Grok CLI. Open Models & Accounts and choose Install "
    "under xAI — Chitragupta downloads it for you."
)

SIGNIN_HINT = (
    "Grok isn't signed in. Open Models & Accounts and choose Sign in with "
    "Grok — it opens xAI's own sign-in page in your browser."
)


def _augmented_path() -> str:
    return augmented_path(_EXTRA_BIN_DIRS)


def find_grok_cli() -> str | None:
    """Locate xAI's `grok` binary, verifying it is actually theirs."""
    # Our own managed copy wins: it is version-pinned and we installed it, so a
    # user never has to install anything by hand.
    from .cli_manager import managed_binary

    pinned = managed_binary("grok")
    if pinned:
        return str(pinned)

    candidates = []
    which = shutil.which("grok", path=_augmented_path())
    if which:
        candidates.append(which)
    for d in _EXTRA_BIN_DIRS:
        cand = Path(d) / "grok"
        if cand.exists() and os.access(cand, os.X_OK):
            candidates.append(str(cand))

    for path in candidates:
        try:
            res = subprocess.run([path, "--version"], capture_output=True, text=True,
                                 timeout=5.0,
                                 env={**os.environ, "PATH": _augmented_path()})
        except Exception:
            continue
        if "grok" in f"{res.stdout} {res.stderr}".lower():
            return path
    return None


@ttl_cached(120.0)
def grok_cli_models() -> list[str]:
    """Ask the CLI what this account can run — `grok models` prints e.g.

        Default model: grok-4.6

        Available models:
          * grok-4.6 (default)
          - grok-4.5
    """
    cli = find_grok_cli()
    if not cli:
        return []
    try:
        res = subprocess.run([cli, "models"], capture_output=True, text=True,
                             timeout=20.0, env={**os.environ, "PATH": _augmented_path()})
    except Exception:
        return []

    models: list[str] = []
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith(("*", "-")):
            continue
        name = line.lstrip("*- ").split()[0].strip()
        if name and name not in models:
            models.append(name)
    return models


_auth_cache: tuple[float, dict] | None = None
_AUTH_TTL = 3.0


def grok_cli_auth_status(*, fresh: bool = False) -> dict:
    """Whether the Grok CLI is signed in.

    `grok models` prints "You are not authenticated." when it is not, and the
    model list either way — so one call answers both questions.
    """
    global _auth_cache
    # Polled once a second during sign-in; without this every tick spawns a
    # subprocess and the request queue outruns the server.
    if not fresh and _auth_cache and (time.monotonic() - _auth_cache[0]) < _AUTH_TTL:
        return _auth_cache[1]

    cli = find_grok_cli()
    if not cli:
        result = {"installed": False, "authenticated": False}
        _auth_cache = (time.monotonic(), result)
        return result
    try:
        res = subprocess.run([cli, "models"], capture_output=True, text=True,
                             timeout=20.0, env={**os.environ, "PATH": _augmented_path()})
    except Exception:
        result = {"installed": True, "authenticated": False}
        _auth_cache = (time.monotonic(), result)
        return result
    blob = f"{res.stdout} {res.stderr}".lower()
    result = {
        "installed": True,
        "authenticated": "not authenticated" not in blob and res.returncode == 0,
    }
    _auth_cache = (time.monotonic(), result)
    return result


_session = CliLoginSession(
    brand="Grok",
    # Looked up at call time, not bound here — see the note in cursor.py.
    find_cli=lambda: find_grok_cli(),
    auth_status=lambda **kw: grok_cli_auth_status(**kw),
    invalidate_cache=lambda: reset_auth_cache(),
    # The OAuth client belongs to Grok Build, so `grok login --oauth` is the
    # only way to reach the accounts.x.ai consent screen.
    login_args=["login", "--oauth"],
    install_hint=INSTALL_HINT,
    env_path=lambda: _augmented_path(),
)


def reset_login_state() -> None:
    """Forget any in-flight login (used on disconnect, and by tests)."""
    _session.reset()


def reset_auth_cache() -> None:
    """Forget the cached sign-in state (used on login/cancel, and by tests)."""
    global _auth_cache
    _auth_cache = None


def login_progress() -> dict:
    return _session.progress()


def cancel_cli_login() -> bool:
    return _session.cancel()


def start_grok_cli_login() -> tuple[bool, str]:
    return _session.start()


class GrokCliProvider(LLMProvider):
    name = "grok-cli"
    model = "grok-4.6"

    def __init__(self, model: str | None = None, **_: object) -> None:
        self._bin = find_grok_cli()
        if model and model.lower().startswith("grok"):
            self.model = model

    def is_ready(self) -> tuple[bool, str]:
        """Installed is not signed in.

        This used to return ready as soon as the binary existed, which made
        every surface downstream lie: `/refresh` marked xAI connected with no
        credential at all, the drawer showed a green "Connected" badge, and the
        first message reached `grok -p`, which answered "Not signed in. …run
        `grok login --device-code`" — a vendor error rendered as the agent's
        reply. A provider that cannot answer must say so here, once, where the
        user can act on it.
        """
        if not self._bin:
            return False, INSTALL_HINT
        if not grok_cli_auth_status().get("authenticated"):
            return False, SIGNIN_HINT
        return True, ""

    def _prompt(self, messages: list[Message]) -> str:
        """`grok -p` takes one instruction, not a User:/Assistant: transcript, so
        system context and prior turns are folded into the prompt."""
        system = "\n\n".join(m.content for m in messages
                             if m.role == "system" and m.content)
        convo = [m for m in messages if m.role in ("user", "assistant", "tool")]
        last_user = next((m for m in reversed(convo) if m.role == "user"), None)
        prompt = last_user.content if last_user else ""
        prior = convo[: convo.index(last_user)] if last_user in convo else convo
        hist = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant' if m.role == 'assistant' else 'Tool'}: {m.content}"
            for m in prior if m.content
        )
        if hist:
            system = (system + "\n\nRecent conversation so far:\n" + hist).strip()
        return f"{system}\n\n---\n\n{prompt}" if system else prompt


    def _stream_command(self, messages, tools):
        """`grok --output-format streaming-messages-json` emits the Anthropic
        Messages wire format directly; `--include-partial-messages` adds the
        text deltas that make it a stream rather than one lump."""
        import tempfile

        workspace = tempfile.gettempdir()
        cmd = [self._bin, "-p", self._prompt(messages),
               "--output-format", "streaming-messages-json",
               "--include-partial-messages", "--cwd", str(workspace)]
        if self.model:
            cmd += ["-m", self.model]
        return cmd, None, {**os.environ, "PATH": _augmented_path()}

    def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
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

        cmd, stdin, env = self._stream_command(messages, tools)
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
            yield done
            return
        yield from from_result(self.chat(messages, tools=tools,
                                         temperature=temperature,
                                         max_tokens=max_tokens))

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        if not self._bin:
            return ChatResult(text=f"⚠️ {INSTALL_HINT}")

        from .cli_manager import agent_workspace

        workspace = agent_workspace()
        # Run in our own empty workspace: this is a coding agent and we only
        # want text back, so nothing of the user's is in reach.
        cmd = [self._bin, "-p", self._prompt(messages), "--output-format", "json",
               "--cwd", str(workspace)]
        if self.model:
            cmd += ["-m", self.model]
        env = {**os.environ, "PATH": _augmented_path()}
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                                  env=env, cwd=str(workspace))
        except subprocess.TimeoutExpired:
            return ChatResult(text=ProviderError(
                ErrorKind.TIMEOUT, "xai", model=self.model, retryable=True,
                message="Grok timed out. Try again, or switch model.").as_reply())

        data = parse_cli_json(proc.stdout)
        text = ""
        for key in ("result", "text", "response", "content", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        if not text and not data:
            text = (proc.stdout or "").strip()

        failed = (proc.returncode != 0 or data.get("is_error")
                  or data.get("type") == "error")
        if failed:
            # `text` here is the CLI's own error prose — it was being returned
            # as the agent's answer, which is how "Not signed in. To
            # authenticate without a browser, run: grok login --device-code"
            # arrived in the chat as if Grok had said it. Classify it instead,
            # so the user gets our message and a way out inside the app.
            err = classify_cli("xai", proc.returncode,
                               f"{proc.stdout or ''}\n{text}",
                               proc.stderr or "", model=self.model)
            if err.kind is ErrorKind.AUTH:
                err.message = SIGNIN_HINT
                err.detail = ""
            return ChatResult(text=err.as_reply())
        return ChatResult(text=text, finish_reason="stop")
