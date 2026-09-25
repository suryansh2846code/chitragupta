"""Finding a credential on the machine must never mean the user authorised it.

Chitragupta detects Claude / Cursor / Codex sessions already present on the
computer so it can *offer* them. Three paths used to treat that detection as
consent, so a provider the user never connected showed as connected and could
be used: `AnthropicProvider.is_ready()` returned True whenever the Claude CLI
binary existed, `get_model_catalog` promoted NOT_CONNECTED to CONNECTED in its
response whenever `ready` was set, and claude-code was connected purely because
the CLI was installed.
"""
from unittest.mock import patch

import pytest

from chitragupta.models.accounts import _claude_plan_label, connect_local_account
from chitragupta.models.anthropic import AnthropicProvider
from chitragupta.models.connections import (
    ConnectionStatus,
    ProviderConnection,
    get_connection,
    save_connection,
)
from chitragupta.models.entitlements import (
    evaluate_model_entitlement,
    is_provider_connected,
    normalize_plan_tier,
    provider_credentials,
)
from chitragupta.models.registry import clear_provider_cache


@pytest.fixture(autouse=True)
def _disconnected_claude():
    for pid in ("claude", "claude-code"):
        save_connection(ProviderConnection(provider=pid))
    clear_provider_cache()
    yield
    for pid in ("claude", "claude-code"):
        save_connection(ProviderConnection(provider=pid))
    clear_provider_cache()


CLI_FOUND = "/opt/homebrew/bin/claude"


# ── detection alone must not connect anything ────────────────────────────
def test_installed_claude_cli_does_not_make_anthropic_ready():
    with patch("chitragupta.models.claude_cli.find_claude", return_value=CLI_FOUND):
        ready, reason = AnthropicProvider(api_key="").is_ready()
    assert ready is False, "an installed CLI was treated as an authorised account"
    assert "ANTHROPIC_API_KEY" in reason or "sign in" in reason.lower()


def test_installed_claude_cli_does_not_connect_claude_code():
    with patch("chitragupta.models.claude_cli.find_claude", return_value=CLI_FOUND):
        connected, plan, _ = is_provider_connected("claude-code")
    assert connected is False
    assert plan is None


def test_detected_account_is_offered_not_used():
    """`found_on_computer` stays true — that is what renders 'Continue'."""
    from chitragupta.models.accounts import detect_claude_account

    with patch("chitragupta.models.claude_cli.find_claude", return_value=CLI_FOUND):
        detected = detect_claude_account()
        creds = provider_credentials("claude")

    assert detected.get("found_on_computer") is True, "the offer disappeared"
    assert detected.get("connected") is False
    assert creds["account"]["connected"] is False


def test_catalog_never_promotes_a_provider_to_connected():
    from chitragupta.models.registry import get_model_catalog

    with patch("chitragupta.models.claude_cli.find_claude", return_value=CLI_FOUND):
        catalog = {p["id"]: p for p in get_model_catalog()}

    for pid in ("claude", "claude-code"):
        entry = catalog[pid]
        assert entry["connected"] is False, f"{pid} auto-connected"
        assert entry["connection"]["connection_status"] != ConnectionStatus.ACCOUNT_CONNECTED
        assert all(m["locked"] for m in entry["models"]), \
            f"{pid} models unlocked without the user connecting it"


# ── explicit consent works ───────────────────────────────────────────────
def test_user_can_deliberately_connect_claude_code():
    with patch("chitragupta.models.claude_cli.find_claude", return_value=CLI_FOUND):
        ok, msg, _ = connect_local_account("claude-code")
        assert ok, msg
        clear_provider_cache()
        connected, _plan, _ = is_provider_connected("claude-code")
    assert connected is True
    assert get_connection("claude-code").account_connected is True


def test_connecting_claude_code_does_not_connect_the_api_provider():
    with patch("chitragupta.models.claude_cli.find_claude", return_value=CLI_FOUND):
        connect_local_account("claude-code")
        clear_provider_cache()
        assert is_provider_connected("claude-code")[0] is True
        assert is_provider_connected("claude")[0] is False


def test_connect_claude_code_fails_without_the_cli():
    with patch("chitragupta.models.claude_cli.find_claude", return_value=None):
        ok, msg, _ = connect_local_account("claude-code")
    assert ok is False
    assert "not found" in msg.lower()


# ── plan detection: Max must not read as Free ────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("claude_max", "Claude Max"),
    ("max", "Claude Max"),
    ("claude_pro", "Claude Pro"),
    ("team", "Claude Team"),
    ("enterprise", "Claude Enterprise"),
    ("", "Claude Free"),
    (None, "Claude Free"),
])
def test_claude_plan_labels(raw, expected):
    assert _claude_plan_label(raw) == expected


def test_max_outranks_pro_and_unlocks_its_models():
    assert normalize_plan_tier("claude", "Claude Max").level > \
           normalize_plan_tier("claude", "Claude Pro").level
    locked, _ = evaluate_model_entitlement("claude", "claude-opus-5",
                                           is_connected=True, user_plan="Claude Max")
    assert locked is False, "a Max subscriber was locked out of Opus 5"


def test_max_ranks_between_pro_and_team():
    pro = normalize_plan_tier("claude", "Claude Pro").level
    mx = normalize_plan_tier("claude", "Claude Max").level
    team = normalize_plan_tier("claude", "Claude Team").level
    ent = normalize_plan_tier("claude", "Claude Enterprise").level
    assert pro < mx < team < ent
