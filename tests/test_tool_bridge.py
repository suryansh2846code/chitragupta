"""The tools a vendor CLI runs on our behalf, and the fence around them.

Three backends are the vendor's own CLI run headless, and every one of them
accepted `tools=` and dropped it. What the user saw was an agent holding
fifty-four tools that called none of them — and a model which, given none of
ours, went looking with the CLI's own and reported back about `ToolSearch` and
`SendMessage`, tools this app has never had. `models/tool_bridge.py` is the way
in: an MCP server carrying that turn's tools, for that turn only.

Nothing here needs the `claude` binary. The bridge speaks HTTP, so the tests
speak HTTP to it — the same four methods a CLI sends, against the real server,
with a real socket. (The end-to-end run against the installed CLI is what
verified the flags in `_bridge_args`; that cannot run in CI, which is exactly
why every promise it proved is pinned here instead.)
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from chitragupta.models.base import ChatResult, Tool, ToolCall
from chitragupta.models.claude_code import ClaudeCodeProvider, _answered
from chitragupta.models.tool_bridge import SERVER_NAME, ToolBridge, qualified


def _tool(name="echo", handler=None):
    return Tool(name=name, description=f"{name} something",
                parameters={"type": "object",
                            "properties": {"text": {"type": "string"}}},
                handler=handler or (lambda text="": f"said {text}"))


@pytest.fixture
def bridge():
    """A running bridge, always shut down — a leaked one holds a port."""
    made = ToolBridge([_tool()])
    made.start()
    yield made
    made.close()


def _post(bridge, body, *, token=None, ident=1):
    """One JSON-RPC request, the way a client sends it."""
    payload = dict(body)
    if ident is not None:
        payload.setdefault("id", ident)
    payload.setdefault("jsonrpc", "2.0")
    request = urllib.request.Request(
        bridge.url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {bridge.token if token is None else token}"})
    with urllib.request.urlopen(request, timeout=10) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


# ── the protocol ────────────────────────────────────────────────────────────


def test_it_completes_a_handshake(bridge):
    """A client that cannot initialise never reaches a tool."""
    answer = _post(bridge, {"method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"}})

    assert answer["result"]["serverInfo"]["name"] == SERVER_NAME
    assert "tools" in answer["result"]["capabilities"]


def test_it_answers_with_the_protocol_version_it_was_asked_for():
    """A CLI that updates its revision must not stop working. Every version so
    far is wire-compatible for the four methods this serves, so echoing is
    honest where refusing would turn an upgrade into a broken connector."""
    with ToolBridge([_tool()]) as live:
        answer = _post(live, {"method": "initialize",
                              "params": {"protocolVersion": "2099-01-01"}})

    assert answer["result"]["protocolVersion"] == "2099-01-01"


def test_it_lists_the_tools_it_was_handed(bridge):
    answer = _post(bridge, {"method": "tools/list"})

    listed = answer["result"]["tools"]
    assert [t["name"] for t in listed] == ["echo"]
    # The schema goes through untouched: it is what the model fills in, and a
    # tool whose arguments we rewrote is a tool called with the wrong ones.
    assert listed[0]["inputSchema"]["properties"]["text"]["type"] == "string"


def test_it_runs_a_tool_and_returns_what_it_said(bridge):
    answer = _post(bridge, {"method": "tools/call",
                            "params": {"name": "echo",
                                       "arguments": {"text": "hello"}}})

    assert answer["result"]["content"][0]["text"] == "said hello"
    assert answer["result"]["isError"] is False


def test_a_tool_that_raises_is_reported_not_thrown(bridge):
    """The caller is a model on the far side of an HTTP boundary. A 500 is
    something it cannot read; a sentence is something it can act on."""
    def explode(**_):
        raise RuntimeError("the vendor said no")

    with ToolBridge([_tool("boom", explode)]) as live:
        answer = _post(live, {"method": "tools/call",
                              "params": {"name": "boom", "arguments": {}}})

    assert answer["result"]["isError"] is True
    assert "the vendor said no" in answer["result"]["content"][0]["text"]


def test_a_name_it_was_never_given_is_a_tool_error_not_a_protocol_error(bridge):
    """A JSON-RPC error ends the model's turn. A tool error lets it pick again."""
    answer = _post(bridge, {"method": "tools/call",
                            "params": {"name": "delete_everything"}})

    assert "error" not in answer
    assert answer["result"]["isError"] is True


def test_a_notification_gets_no_answer(bridge):
    """`notifications/initialized` carries no id. Answering one is a protocol
    violation, and the client is entitled to drop the connection over it."""
    assert _post(bridge, {"method": "notifications/initialized"}, ident=None) is None


def test_a_batch_is_answered_in_full():
    """Answering only the first message silently drops work the client sent."""
    with ToolBridge([_tool()]) as live:
        request = urllib.request.Request(
            live.url,
            data=json.dumps([
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ]).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {live.token}"})
        with urllib.request.urlopen(request, timeout=10) as resp:
            answers = json.loads(resp.read())

    assert [a["id"] for a in answers] == [1, 2]


# ── the fence ───────────────────────────────────────────────────────────────


def test_it_refuses_a_request_with_no_token(bridge):
    """Loopback is not a boundary — every browser the user has open is also
    "on this machine". The token is the whole defence, so this is the test that
    matters most in the file."""
    with pytest.raises(urllib.error.HTTPError) as refused:
        _post(bridge, {"method": "tools/list"}, token="")

    assert refused.value.code == 401


def test_it_refuses_a_request_with_the_wrong_token(bridge):
    with pytest.raises(urllib.error.HTTPError) as refused:
        _post(bridge, {"method": "tools/call",
                       "params": {"name": "echo"}}, token="not-the-token")

    assert refused.value.code == 401


def test_a_refused_call_never_reaches_the_tool():
    """401 on the way out is not enough if the work already happened."""
    ran = []

    with ToolBridge([_tool("echo", lambda **_: ran.append(1) or "done")]) as live:
        with pytest.raises(urllib.error.HTTPError):
            _post(live, {"method": "tools/call",
                         "params": {"name": "echo"}}, token="wrong")

    assert ran == []


def test_two_bridges_do_not_share_a_token_or_a_port():
    """Two agents can answer at once. A shared port would make the second fail
    to bind; a shared token would let either reach the other's tools."""
    with ToolBridge([_tool()]) as one, ToolBridge([_tool()]) as two:
        assert one.port != two.port
        assert one.token != two.token

        with pytest.raises(urllib.error.HTTPError):
            _post(two, {"method": "tools/list"}, token=one.token)


# ── the config file the CLI is handed ───────────────────────────────────────


def test_the_config_names_this_server_and_carries_the_token(bridge):
    spec = json.loads(Path(bridge.config_path()).read_text())

    server = spec["mcpServers"][SERVER_NAME]
    assert server["type"] == "http"
    assert server["url"] == bridge.url
    assert server["headers"]["Authorization"] == f"Bearer {bridge.token}"


def test_the_config_is_not_readable_by_anyone_else(bridge):
    """It holds the bearer token for a server that runs `run_python`."""
    mode = Path(bridge.config_path()).stat().st_mode & 0o777

    assert mode == 0o600


def test_closing_deletes_the_config_and_stops_listening():
    """A file left in /tmp holds a token, and a socket left open holds tools."""
    live = ToolBridge([_tool()])
    live.start()
    path = Path(live.config_path())
    assert path.exists()

    live.close()

    assert not path.exists()
    with pytest.raises((urllib.error.URLError, ConnectionError)):
        _post(live, {"method": "tools/list"})


def test_closing_twice_is_harmless():
    """`chat()` closes it, and so does the `finally` above it."""
    live = ToolBridge([_tool()])
    live.start()
    live.close()

    live.close()


# ── what the CLI is told on the command line ────────────────────────────────


def test_the_token_never_reaches_the_command_line(bridge):
    """`--mcp-config` takes a JSON string too, and a string puts the token in
    `ps` output for every other account on this machine."""
    argv = ClaudeCodeProvider._bridge_args(bridge)

    assert bridge.token not in " ".join(argv)
    assert bridge.config_path() in argv


def test_the_cli_s_own_tools_are_turned_off(bridge):
    """Left on, asking an agent to read your mail hands it `Bash`, `Write` and
    `Edit` over your whole filesystem — because of which backend answered."""
    argv = ClaudeCodeProvider._bridge_args(bridge)

    assert argv[argv.index("--tools") + 1] == ""


def test_the_user_s_own_mcp_servers_are_kept_out(bridge):
    """Their terminal's servers are configured for their terminal work. An
    agent's toolset must not depend on a file this app does not manage."""
    assert "--strict-mcp-config" in ClaudeCodeProvider._bridge_args(bridge)


def test_every_tool_is_named_rather_than_wildcarded():
    """Headless has nobody to ask, so an un-allowed tool is a refused one. The
    list is enumerated because the list is the point — a wildcard would keep
    granting whatever the server grows later."""
    with ToolBridge([_tool("one"), _tool("two")]) as live:
        argv = ClaudeCodeProvider._bridge_args(live)

    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert allowed == [qualified("one"), qualified("two")]
    assert "*" not in argv[argv.index("--allowedTools") + 1]


def test_no_tools_means_no_bridge_and_no_flags():
    """An agent with no tools must not pay for a socket and a temp file."""
    assert ClaudeCodeProvider(model="claude-sonnet-5")._bridge([]) is None


# ── the calls the CLI already made ──────────────────────────────────────────


def test_a_bridged_answer_carries_no_tool_calls_back_to_the_loop():
    """The CLI ran them. Left on the result, `runtime.py` reads `wants_tools`
    and runs every one a second time — reading the user's mail twice, and never
    terminating on the answer the CLI had already written.

    Verified red: with the strip removed, a live `claude -p` run came back with
    `ToolCall(name='mcp__chitragupta__secret_number')` and `wants_tools=True`.
    """
    result = ChatResult(text="done", tool_calls=[
        ToolCall(id="1", name="mcp__chitragupta__list_mail", arguments={})])

    _answered(result, bridge=object())

    assert result.tool_calls == []
    assert not result.wants_tools


def test_an_unbridged_answer_keeps_its_tool_calls():
    """The strip belongs to the bridged path alone. A provider that genuinely
    wants a tool run must still be able to ask for one."""
    result = ChatResult(text="", tool_calls=[
        ToolCall(id="1", name="search_brain", arguments={})])

    _answered(result, bridge=None)

    assert result.wants_tools


def test_the_observer_sees_every_call_the_cli_made():
    """Those calls happen in another process, so they are absent from the trace
    the user watches — a turn that looked six things up would show no steps at
    all, which reads as an answer that was made up rather than found."""
    seen = []

    with ToolBridge([_tool()],
                    observer=lambda n, a, r: seen.append((n, a, r))) as live:
        _post(live, {"method": "tools/call",
                     "params": {"name": "echo", "arguments": {"text": "hi"}}})

    assert seen == [("echo", {"text": "hi"}, "said hi")]


def test_a_broken_observer_never_breaks_the_call():
    """Reporting a call is not worth failing one over."""
    def unusable(*_args):
        raise RuntimeError("the trace is gone")

    with ToolBridge([_tool()], observer=unusable) as live:
        answer = _post(live, {"method": "tools/call",
                              "params": {"name": "echo",
                                         "arguments": {"text": "hi"}}})

    assert answer["result"]["content"][0]["text"] == "said hi"


def test_calls_from_several_threads_are_all_served():
    """A CLI may run two tools at once, and a server that handles one at a time
    would turn that into a turn that waits."""
    started = threading.Barrier(3, timeout=10)

    def slow(**_):
        started.wait()
        return "ok"

    answers = []
    with ToolBridge([_tool("slow", slow)]) as live:
        def ask():
            answers.append(_post(live, {"method": "tools/call",
                                        "params": {"name": "slow"}}, ident=None))

        threads = [threading.Thread(target=ask) for _ in range(2)]
        for t in threads:
            t.start()
        started.wait()
        for t in threads:
            t.join(timeout=10)

    assert len(answers) == 2


# ── whose trace a call is drawn in ──────────────────────────────────────────


def test_the_observer_is_per_turn_not_per_provider():
    """`registry.get_provider` is `@lru_cache`d: ONE provider instance answers
    every concurrent turn. An observer stored on it would be overwritten by
    whichever turn started last, and Chief of Staff's tool calls would appear
    in the Health agent's trace.

    So it lives in a ContextVar, and each turn reads its own. Delegation
    already copies the context per call, which is what makes a sub-agent's
    calls land in the sub-agent's trace.
    """
    import contextvars

    from chitragupta.models import tool_bridge as tb

    seen = {}

    def turn(name):
        token = tb.observe(lambda *_a, name=name: seen.setdefault(name, name))
        try:
            return tb.current_observer()
        finally:
            tb.stop_observing(token)

    one = contextvars.copy_context().run(turn, "chief")
    two = contextvars.copy_context().run(turn, "health")

    assert one is not two
    assert tb.current_observer() is None, "an observer outlived its turn"


def test_a_bridge_holds_the_observer_it_was_built_with():
    """Read once, on the turn's own thread. The socket threads that serve the
    calls have never seen the ContextVar and must not need to."""
    from chitragupta.models import tool_bridge as tb

    calls = []
    token = tb.observe(lambda n, a, r: calls.append(n))
    try:
        live = ToolBridge([_tool()], observer=tb.current_observer())
        live.start()
    finally:
        tb.stop_observing(token)

    try:
        # The turn is over and the ContextVar is clear, but the call still
        # reports — which is the whole point of holding it on the bridge.
        _post(live, {"method": "tools/call",
                     "params": {"name": "echo", "arguments": {"text": "hi"}}})
    finally:
        live.close()

    assert calls == ["echo"]
