"""Giving a vendor CLI our tools, over MCP, for the length of one turn.

Three of the provider backends are not APIs — they are the vendor's own CLI run
headless (`claude -p`, `cursor-agent`, `grok`). Those CLIs return **text**, and
until now that meant the `tools=` argument every one of them accepts was
silently dropped on the floor. An agent holding fifty-four tools got none of
them, and the model, having none, went looking with the CLI's *own* toolset and
reported back about tools the user has never heard of.

MCP is the way in. `claude -p --mcp-config` takes a server the CLI will connect
to and offer to its model, so the same tools the Messages API path passes as
`tools=[...]` are reachable on the subscription path as MCP tools. The CLI runs
its own loop over them and returns the finished answer.

**This server is the narrowest thing that can work, and it is torn down when the
turn ends.**

* **It exists for one turn.** Started when a call begins, shut down in a
  `finally`. Nothing is left listening between messages.
* **Loopback is not the boundary — the token is.** Every browser the user has
  open is also "on this machine" (`/CLAUDE.md`), so an unauthenticated port
  serving `run_python` and `write_file` would be a hole the size of the toolset.
  A fresh random bearer token is required on every request.
* **The token never reaches a command line.** `--mcp-config` takes a path as
  happily as a JSON string, and a string would put the token in `ps` output for
  every other user on the machine. It goes in a 0600 file that is deleted on the
  way out.
* **It serves what it was handed, and nothing else.** The handlers come from the
  caller. This module runs no tool of its own and knows nothing about what any
  of them do — which is what keeps it in `models/`, below the layer that owns
  permissions (`/docs/ARCHITECTURE.md` §3: `models/` may not import `agents/`).

Only four JSON-RPC methods are implemented, because only four are used:
`initialize`, `tools/list`, `tools/call` and `ping`. Reaching for the SDK's
server machinery would mean running an anyio event loop inside a worker thread
for something the protocol expresses as four request shapes.
"""
from __future__ import annotations

import contextvars
import json
import os
import secrets
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..log import get_logger, suppressed
from .base import Tool

log = get_logger(__name__)

#: The name the CLI knows this server by. It becomes part of every tool name the
#: model sees (`mcp__chitragupta__list_mail`), which is also what
#: `--allowedTools` has to be given — so it is one constant, not two strings
#: that have to agree.
SERVER_NAME = "chitragupta"

#: The protocol revision we answer with when a client does not name one.
#: A client that *does* name one gets its own back: every version so far has
#: been wire-compatible for these four methods, and refusing an unknown revision
#: would turn a working CLI into a broken connector on the day it updates.
DEFAULT_PROTOCOL = "2025-06-18"

#: How long a tool may run before the bridge answers with a failure instead.
#: The CLI has its own patience and the user has less; a tool that hangs must
#: not hold the turn open until the subprocess timeout at 180s.
CALL_TIMEOUT_SECONDS = 120.0


def qualified(tool_name: str) -> str:
    """What the model calls one of our tools once the CLI has namespaced it."""
    return f"mcp__{SERVER_NAME}__{tool_name}"


#: Called with (name, arguments, result) after every tool the CLI runs.
#: The trace the user watches is built in the agent loop, and on this path the
#: calls do not pass through it — they happen inside the subprocess. Without
#: this hook a bridged turn would show the user no steps at all, which reads as
#: "it answered without looking anything up".
ObserverFn = Callable[[str, dict, str], None]

#: Where the caller leaves that hook for the provider to find.
#:
#: A ContextVar rather than an attribute on the provider, because
#: `registry.get_provider` is `@lru_cache`d — **one instance is handed to every
#: concurrent turn**. An attribute there would be overwritten by whichever turn
#: started last, and one agent's tool calls would be drawn in another agent's
#: trace. Delegation already copies the context per call, so a sub-agent gets
#: its own without disturbing the turn that asked it.
#:
#: Read on the turn's own thread, when the bridge is built. The value is then
#: held on the bridge, so the socket threads never read this at all.
_OBSERVER: contextvars.ContextVar[ObserverFn | None] = contextvars.ContextVar(
    "chitragupta_tool_bridge_observer", default=None)


def observe(fn: ObserverFn | None):
    """Report this turn's bridged tool calls to `fn`. Returns a reset token."""
    return _OBSERVER.set(fn)


def stop_observing(token) -> None:
    with suppressed("clearing the bridged-tool observer"):
        _OBSERVER.reset(token)


def current_observer() -> ObserverFn | None:
    return _OBSERVER.get()


@dataclass
class ToolBridge:
    """An MCP server carrying one turn's tools. Use it as a context manager."""

    tools: list[Tool]
    observer: ObserverFn | None = None

    _server: ThreadingHTTPServer | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _config: Path | None = field(default=None, repr=False)
    token: str = field(default="", repr=False)
    port: int = 0

    # ── lifecycle ───────────────────────────────────────────────────────────

    def __enter__(self) -> ToolBridge:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start(self) -> None:
        """Bind an ephemeral loopback port and serve until `close()`.

        Port 0 rather than a fixed one: two agents can answer at the same time,
        and a fixed port would make the second one fail to bind — or, worse,
        reach the first one's tools.
        """
        self.token = secrets.token_urlsafe(32)
        by_name = {t.name: t for t in self.tools if t.name}
        handler = _make_handler(self, by_name)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="chitragupta-tool-bridge",
            daemon=True)
        self._thread.start()
        log.debug("tool bridge serving %d tools on 127.0.0.1:%d",
                  len(by_name), self.port)

    def close(self) -> None:
        """Stop listening and delete the config file. Safe to call twice."""
        if self._server is not None:
            with suppressed("shutting down the tool bridge"):
                self._server.shutdown()
                self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._config is not None:
            with suppressed("removing the tool bridge's config file"):
                self._config.unlink(missing_ok=True)
            self._config = None

    # ── what the CLI is told ────────────────────────────────────────────────

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def config_path(self) -> str:
        """A `--mcp-config` file naming this server. Written 0600, once.

        A file rather than the JSON string the flag also accepts: the string
        would carry the bearer token through `ps`, where every other account on
        the machine can read it.
        """
        if self._config is None:
            spec = {"mcpServers": {SERVER_NAME: {
                "type": "http",
                "url": self.url,
                "headers": {"Authorization": f"Bearer {self.token}"},
            }}}
            fd, raw = tempfile.mkstemp(prefix="chitragupta-mcp-", suffix=".json")
            os.close(fd)
            path = Path(raw)
            path.write_text(json.dumps(spec))
            path.chmod(0o600)
            self._config = path
        return str(self._config)

    def allowed_tools(self) -> list[str]:
        """Every tool's namespaced name, for `--allowedTools`.

        Enumerated rather than passed as the server name alone: a wildcard would
        keep granting whatever the server grows later, and the point of handing
        over a list is that it is the list.
        """
        return [qualified(t.name) for t in self.tools if t.name]


# ── the four methods ────────────────────────────────────────────────────────


def _result(tool: Tool, arguments: dict) -> tuple[str, bool]:
    """Run one tool. Returns (text, is_error) — never raises.

    A handler that blows up is the vendor CLI's problem to report to its model,
    not an exception crossing an HTTP boundary as a 500 the model cannot read.
    """
    handler = tool.handler
    if handler is None:                       # pragma: no cover - defensive
        return f"{tool.name} is not runnable here.", True
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["text"] = str(handler(**(arguments or {})))
        except Exception as exc:
            box["error"] = f"{tool.name} failed: {exc}"

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(CALL_TIMEOUT_SECONDS)
    if worker.is_alive():
        return (f"{tool.name} did not finish in time and was left running.",
                True)
    if "error" in box:
        return box["error"], True
    return box.get("text", ""), False


def _dispatch(bridge: ToolBridge, by_name: dict[str, Tool],
              method: str, params: dict) -> tuple[dict | None, bool]:
    """Answer one JSON-RPC method. Returns (result, is_notification)."""
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if isinstance(asked, str) else DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
        }, False
    if method == "ping":
        return {}, False
    if method.startswith("notifications/"):
        return None, True
    if method == "tools/list":
        return {"tools": [{
            "name": t.name,
            "description": t.description,
            "inputSchema": t.parameters or {"type": "object", "properties": {}},
        } for t in by_name.values()]}, False
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        tool = by_name.get(name)
        if tool is None:
            # A name we were never given is answered as a tool error rather
            # than a protocol error: the model can read the sentence and pick a
            # different tool, where a JSON-RPC error ends its turn.
            text, failed = f"There is no tool called `{name}`.", True
        else:
            text, failed = _result(tool, arguments)
        if bridge.observer is not None:
            with suppressed("reporting a bridged tool call"):
                bridge.observer(name, arguments, text)
        return {"content": [{"type": "text", "text": text}],
                "isError": failed}, False
    raise KeyError(method)


def _make_handler(bridge: ToolBridge, by_name: dict[str, Tool]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            """Silence. The default writes every request to stderr, which in a
            packaged app is the user's console."""

        # A client that opens the GET stream before posting gets a clean
        # refusal rather than a hang: this server never pushes, so there is
        # nothing for an event stream to carry.
        def do_GET(self) -> None:
            self._refuse(405, "this server does not stream")

        def do_DELETE(self) -> None:
            self._blank(204)

        def do_POST(self) -> None:
            if not self._authorised():
                self._refuse(401, "unauthorised")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError):
                self._rpc_error(None, -32700, "could not parse that request")
                return
            # A batch is a list. Answering only the first would silently drop
            # work the client believes it sent.
            batch = body if isinstance(body, list) else [body]
            answers = [a for a in (self._one(m) for m in batch) if a is not None]
            if not answers:
                self._blank(202)
                return
            self._json(answers if isinstance(body, list) else answers[0])

        # ── one message ─────────────────────────────────────────────────

        def _one(self, message: Any) -> dict | None:
            if not isinstance(message, dict):
                return None
            ident = message.get("id")
            method = str(message.get("method") or "")
            params = message.get("params")
            params = params if isinstance(params, dict) else {}
            try:
                result, notification = _dispatch(bridge, by_name, method, params)
            except KeyError:
                if ident is None:
                    return None
                return {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32601,
                                  "message": f"unknown method {method}"}}
            except Exception as exc:          # pragma: no cover - defensive
                log.debug("tool bridge failed on %s: %s", method, exc)
                if ident is None:
                    return None
                return {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32603, "message": "internal error"}}
            if notification or ident is None:
                return None
            return {"jsonrpc": "2.0", "id": ident, "result": result}

        # ── plumbing ────────────────────────────────────────────────────

        def _authorised(self) -> bool:
            sent = (self.headers.get("Authorization") or "").strip()
            expected = f"Bearer {bridge.token}"
            # Constant time: the token is the whole boundary, and a loopback
            # attacker can time as many guesses as it likes.
            return bool(bridge.token) and secrets.compare_digest(sent, expected)

        def _json(self, payload: Any) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Mcp-Session-Id", bridge.token[:16])
            self.end_headers()
            self.wfile.write(raw)

        def _blank(self, code: int) -> None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _refuse(self, code: int, why: str) -> None:
            raw = json.dumps({"error": why}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _rpc_error(self, ident: Any, code: int, message: str) -> None:
            self._json({"jsonrpc": "2.0", "id": ident,
                        "error": {"code": code, "message": message}})

    return Handler
