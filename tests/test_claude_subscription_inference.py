"""A Claude subscription must actually be able to answer.

`AnthropicProvider.is_ready()` reported True for an account-connected Claude
with no API key, then `chat()` posted to api.anthropic.com with `x-api-key:
None`. The 401 escaped as an uncaught HTTPStatusError, so the first message a
subscriber sent blew up. Anthropic has no subscription inference endpoint, so
those calls belong to the local Claude CLI.
"""
from unittest.mock import patch

import httpx
import pytest

from chitragupta.models.anthropic import AnthropicProvider
from chitragupta.models.base import ChatResult, Message, parse_cli_json
from chitragupta.models.connections import (
    ACCOUNT,
    ConnectionStatus,
    ProviderConnection,
    get_connection,
    save_connection,
)
from chitragupta.models.registry import clear_provider_cache

CLI = "/opt/homebrew/bin/claude"
HELLO = [Message(role="user", content="hello")]


@pytest.fixture
def connected_account():
    conn = get_connection("claude")
    conn.set_credential(ACCOUNT, ConnectionStatus.ACCOUNT_CONNECTED)
    conn.email = "user@example.com"
    save_connection(conn)
    clear_provider_cache()
    yield conn
    save_connection(ProviderConnection(provider="claude"))
    clear_provider_cache()


# ── the bug ───────────────────────────────────────────────────────────────
def test_subscription_never_calls_the_messages_api(connected_account):
    with patch("chitragupta.models.claude_cli.find_claude", return_value=CLI), \
         patch("chitragupta.models.claude_code.ClaudeCodeProvider.chat",
               return_value=ChatResult(text="from the CLI")) as cli_chat, \
         patch("httpx.post", side_effect=AssertionError("posted to api.anthropic.com")) as post:
        result = AnthropicProvider(api_key="").chat(HELLO)

    assert result.text == "from the CLI"
    assert cli_chat.called
    assert not post.called, "a subscription request was sent to the Messages API"


def test_subscription_is_not_ready_without_the_cli(connected_account):
    with patch("chitragupta.models.claude_cli.find_claude", return_value=None):
        ready, reason = AnthropicProvider(api_key="").is_ready()
    assert ready is False
    assert "Claude CLI" in reason


def test_subscription_without_cli_explains_itself_instead_of_crashing(connected_account):
    with patch("chitragupta.models.claude_cli.find_claude", return_value=None), \
         patch("httpx.post", side_effect=AssertionError("should not be called")):
        result = AnthropicProvider(api_key="").chat(HELLO)
    assert "⚠️" in result.text
    assert "Claude CLI" in result.text


def test_model_id_reaches_the_cli_unchanged(connected_account):
    """'-latest' used to be stripped, producing ids the CLI rejects."""
    seen = {}

    class FakeCLI:
        def __init__(self, model=None, **_):
            seen["model"] = model

        def chat(self, *a, **kw):
            return ChatResult(text="ok")

    with patch("chitragupta.models.claude_cli.find_claude", return_value=CLI), \
         patch("chitragupta.models.claude_code.ClaudeCodeProvider", FakeCLI):
        AnthropicProvider(model="claude-3-7-sonnet-latest", api_key="").chat(HELLO)
    assert seen["model"] == "claude-3-7-sonnet-latest"


# ── API-key path must not raise either ───────────────────────────────────
@pytest.mark.parametrize("code,expect", [
    (401, "rejected the credential"), (403, "rejected the credential"),
    (429, "rate-limited"), (404, "isn't available"), (500, "server error"),
])
def test_api_errors_become_messages_not_exceptions(code, expect):
    resp = httpx.Response(code, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                          text="boom")
    with patch("httpx.post", return_value=resp):
        result = AnthropicProvider(api_key="sk-ant-test").chat(HELLO)
    assert isinstance(result, ChatResult)
    assert expect in result.text


def test_api_timeout_becomes_a_message():
    with patch("httpx.post", side_effect=httpx.TimeoutException("slow")):
        result = AnthropicProvider(api_key="sk-ant-test").chat(HELLO)
    assert "timed out" in result.text


# ── CLI output parsing ───────────────────────────────────────────────────
def test_cli_json_survives_a_leading_deprecation_notice():
    """A deprecated model prints an end-of-life banner before the payload."""
    stdout = (
        "The model 'claude-3-7-sonnet-latest' is deprecated and will reach "
        "end-of-life on February 19th, 2026\n"
        "Please migrate to a newer model.\n"
        '{"is_error": false, "result": "hello there"}'
    )
    assert parse_cli_json(stdout).get("result") == "hello there"


@pytest.mark.parametrize("stdout", ["", "   ", "not json at all", "{broken"])
def test_cli_json_parser_is_safe_on_junk(stdout):
    assert parse_cli_json(stdout) == {}


# ── catalog must not advertise models that cannot run ────────────────────
RETIRED = ["claude-fable-5-1", "claude-3-7-sonnet-latest",
           "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"]


@pytest.mark.parametrize("provider", ["claude", "claude-code", "subscription"])
def test_catalog_offers_no_retired_claude_models(provider):
    """Verified against the live Claude CLI: these ids no longer resolve."""
    from chitragupta.models.registry import MODEL_CATALOG

    ids = [m["id"] for m in MODEL_CATALOG[provider]["models"]]
    assert not (set(ids) & set(RETIRED)), f"{provider} still lists retired ids"


def test_default_claude_model_is_current():
    from chitragupta.models.registry import MODEL_CATALOG

    assert MODEL_CATALOG["claude"]["default_model"] not in RETIRED
    assert AnthropicProvider(api_key="x").model not in RETIRED


def test_fable_is_not_gated_behind_team():
    """The Team/Enterprise gate came from misreading the CLI's
    `cc-update-required-1` entry, which is a CLI *version* requirement."""
    from chitragupta.models.entitlements import evaluate_model_entitlement

    for plan in ("Claude Pro", "Claude Max"):
        locked, _ = evaluate_model_entitlement("claude", "claude-fable-5",
                                               is_connected=True, user_plan=plan)
        assert locked is False, f"Fable 5 locked for {plan}"
