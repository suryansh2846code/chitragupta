"""Unit tests for Dynamic Model Discovery."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from chitragupta.api.app import app
from chitragupta.models.discovery import (
    _detect_capabilities,
    discover_openai_models,
    discover_xai_models,
    get_discovered_models,
)


def test_capability_detection():
    """Verify reasoning, vision, and tool calling capability detection."""
    o1_caps = _detect_capabilities("o1-preview")
    assert o1_caps["reasoning"] is True
    assert o1_caps["tool_calling"] is True

    sonnet_caps = _detect_capabilities("claude-3-7-sonnet-latest")
    assert sonnet_caps["reasoning"] is True
    assert sonnet_caps["vision"] is True
    assert sonnet_caps["context_window"] == 200_000

    gemini_caps = _detect_capabilities("gemini-2.5-flash")
    assert gemini_caps["context_window"] == 1_000_000
    assert gemini_caps["vision"] is True


def test_mocked_openai_discovery():
    """Verify live OpenAI discovery parser and identity lookup."""
    mock_resp_me = MagicMock()
    mock_resp_me.status_code = 200
    mock_resp_me.json.return_value = {
        "id": "user_xyz",
        "email": "developer@turnover.ai",
        "name": "TURNOVER Developer",
        "orgs": {"data": [{"name": "Acme AI Corp", "id": "org_123"}]},
    }

    mock_resp_models = MagicMock()
    mock_resp_models.status_code = 200
    mock_resp_models.json.return_value = {
        "data": [
            {"id": "gpt-4o", "created": 1700000000},
            {"id": "gpt-4o-mini", "created": 1700000100},
            {"id": "o1", "created": 1700000200},
            {"id": "text-embedding-3-small", "created": 1690000000},  # should be filtered out
        ]
    }

    def mock_get(url, **kwargs):
        if "v1/me" in url:
            return mock_resp_me
        return mock_resp_models

    with patch("httpx.get", side_effect=mock_get):
        models, account_meta = discover_openai_models(api_key="sk-test-key")
        assert account_meta.get("email") == "developer@turnover.ai"
        assert account_meta.get("organization") == "Acme AI Corp"
        m_ids = [m.id for m in models]
        assert "gpt-4o" in m_ids
        assert "o1" in m_ids
        assert "text-embedding-3-small" not in m_ids


def test_mocked_xai_discovery_prevents_stale_models():
    """Verify xAI returns real discovered model list, eliminating grok-2-latest not found."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "grok-2-1212"},
            {"id": "grok-2-vision-1212"},
            {"id": "grok-beta"},
        ]
    }
    with patch("httpx.get", return_value=mock_resp):
        models = discover_xai_models(api_key="xai-test-key")
        m_ids = [m.id for m in models]
        assert "grok-2-1212" in m_ids
        assert "grok-2-vision-1212" in m_ids


def test_models_api_endpoint():
    """Verify GET /api/providers/{name}/models endpoint."""
    client = TestClient(app)
    resp = client.get("/api/providers/ollama/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) > 0


def test_chatgpt_subscription_locks_unsupported_models(tmp_path, monkeypatch):
    """Only the slugs the account's own Codex cache lists may be unlocked."""
    import json

    from chitragupta.models.discovery import _chatgpt_subscription_models

    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [
        {"slug": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra"},
        {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna"},
    ]}))
    monkeypatch.setattr("chitragupta.models.chatgpt_auth._codex_models_cache_path", lambda: cache)
    monkeypatch.setattr("chitragupta.models.chatgpt_auth.detect_chatgpt_local_session",
                        lambda fetch_usage=True: {"plan": "ChatGPT Free"})

    model_map = {m.id: m for m in _chatgpt_subscription_models()}

    # Present in the account's cache -> usable
    assert model_map["gpt-5.6-terra"].locked is False
    assert model_map["gpt-5.6-luna"].locked is False

    # Absent from it -> locked, with the tier that would be required
    assert model_map["gpt-6-astra"].locked is True
    assert model_map["gpt-6-astra"].plan_required == "Pro"
    assert model_map["gpt-5.6-sol"].locked is True
    assert model_map["gpt-5.6-sol"].plan_required == "Pro"


def test_chatgpt_subscription_falls_back_to_plan_when_no_cache(tmp_path, monkeypatch):
    """With no Codex cache, the plan tables decide — conservatively."""
    from chitragupta.models.discovery import _chatgpt_subscription_models

    monkeypatch.setattr("chitragupta.models.chatgpt_auth._codex_models_cache_path",
                        lambda: tmp_path / "absent.json")
    monkeypatch.setattr("chitragupta.models.chatgpt_auth.detect_chatgpt_local_session",
                        lambda fetch_usage=True: {"plan": "ChatGPT Free"})

    model_map = {m.id: m for m in _chatgpt_subscription_models()}
    assert model_map["gpt-5.6-terra"].locked is False   # free tier
    assert model_map["gpt-6-astra"].locked is True      # Pro only


def test_chatgpt_subscription_rejects_unsupported_model():
    """Verify chat_with_chatgpt_subscription informs user of unsupported model instead of silent fallback."""
    from chitragupta.models.base import Message
    from chitragupta.models.chatgpt_auth import chat_with_chatgpt_subscription

    with patch("chitragupta.models.chatgpt_auth.get_chatgpt_access_token", return_value="mock_token"):
        res = chat_with_chatgpt_subscription(
            [Message(role="user", content="hello")],
            model="gpt-6-astra",
        )
        # Must return warning explaining the plan requirement, not silent success
        assert "not supported on your" in res.text
        assert "Pro" in res.text


def test_set_agent_model_api_rejects_locked_model():
    """Verify API prevents binding an agent to a locked model."""
    client = TestClient(app)
    resp = client.post("/api/agents/inbox/model", json={"provider": "openai", "model": "gpt-6-astra"})
    assert resp.status_code == 400
    assert "locked" in resp.json()["detail"].lower() or "requires" in resp.json()["detail"].lower()


def test_claude_opus_and_fable_discovery():
    """Verify Claude Opus 5, Sonnet 5 and Fable 5 are discovered for a paid plan."""
    from chitragupta.models.connections import ConnectionStatus, get_connection, save_connection
    conn = get_connection("claude")
    prev_status = conn.connection_status
    prev_email = conn.email
    try:
        conn.connection_status = ConnectionStatus.ACCOUNT_CONNECTED
        conn.email = "pro@anthropic.com"
        save_connection(conn)
        models, _ = get_discovered_models("claude", force_refresh=True)
        model_map = {m["id"]: m for m in models}

        assert "claude-opus-5" in model_map
        assert "claude-sonnet-5" in model_map
        assert "claude-fable-5" in model_map

        # Opus 5 capabilities
        opus = model_map["claude-opus-5"]
        assert opus.get("reasoning") is True
        assert opus.get("context_window") == 200_000

        # Fable 5 is a subscription model — available on any paid Claude plan.
        # (Fable 5.1 is a separate model gated on Claude CLI >= 2.1.255, which
        # is a client-version requirement, not a plan tier.)
        fable = model_map["claude-fable-5"]
        assert fable.get("locked") is False
        assert fable.get("plan_required") is None
    finally:
        conn.connection_status = prev_status
        conn.email = prev_email
        save_connection(conn)


def test_claude_code_reports_a_locked_model_cleanly():
    """A model the plan cannot run is refused before shelling out to the CLI."""
    from unittest.mock import patch

    from chitragupta.models.base import Message
    from chitragupta.models.claude_code import ClaudeCodeProvider

    with patch("chitragupta.models.claude_cli.find_claude", return_value="/usr/bin/claude"), \
         patch("chitragupta.models.accounts.detect_claude_account",
               return_value={"plan": "Claude Free", "disabled_models": {}}), \
         patch("subprocess.run", side_effect=AssertionError("must not invoke the CLI")):
        res = ClaudeCodeProvider(model="claude-opus-5").chat(
            [Message(role="user", content="hello")])
    assert "currently locked" in res.text


def test_claude_code_respects_a_cli_reported_disablement():
    """~/.claude.json can mark a model unavailable for this CLI version."""
    from unittest.mock import patch

    from chitragupta.models.base import Message
    from chitragupta.models.claude_code import ClaudeCodeProvider

    with patch("chitragupta.models.claude_cli.find_claude", return_value="/usr/bin/claude"), \
         patch("chitragupta.models.accounts.detect_claude_account",
               return_value={"plan": "Claude Max",
                             "disabled_models": {"opus": "Update to 2.1.255+"}}), \
         patch("subprocess.run", side_effect=AssertionError("must not invoke the CLI")):
        res = ClaudeCodeProvider(model="claude-opus-5").chat(
            [Message(role="user", content="hello")])
    assert "Update to 2.1.255+" in res.text


def test_cursor_locking_matches_plan(monkeypatch):
    """A free Cursor plan can run only `auto` — the CLI refuses named models."""
    from chitragupta.models import discovery

    monkeypatch.setattr("chitragupta.models.cursor.cursor_cli_models",
                        lambda: [("auto", "Auto"), ("claude-opus-5-high", "Claude Opus 5")])
    monkeypatch.setattr("chitragupta.models.accounts.detect_cursor_account",
                        lambda: {"plan": "Cursor Free"})
    models = {m.id: m for m in discovery._discover_raw("cursor", None)[0]}
    assert models["auto"].locked is False
    assert models["claude-opus-5-high"].locked is True
    assert models["claude-opus-5-high"].plan_required == "Cursor Pro"

    monkeypatch.setattr("chitragupta.models.accounts.detect_cursor_account",
                        lambda: {"plan": "Cursor Pro"})
    models = {m.id: m for m in discovery._discover_raw("cursor", None)[0]}
    assert models["claude-opus-5-high"].locked is False
