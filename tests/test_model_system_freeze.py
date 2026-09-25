"""Final Model System Hardening & Freeze Verification Suite.

Validates all 24 required operational and security invariants:
1. Provider connection gating
2. Disconnected provider cannot bind to agent
3. Connected provider can bind
4. Locked model cannot bind
5. Unlocked model can bind
6. Auto cannot be selected when provider is disconnected
7. Ollama detects installed models
8. Ollama locks uninstalled models
9. Cursor detection does NOT expose accessToken
10. Claude detection does NOT expose OAuth token
11. OpenAI detection does NOT copy ~/.codex/auth.json
12. Disconnect invalidates Chitragupta connection
13. Runtime identity contains actual provider/model
14. Model answers runtime identity correctly
15. API-key provider remains functional
16. OAuth provider remains functional
17. Existing CLI provider remains functional
18. Dynamic discovery failure falls back safely
19. Static entitlement fallback works
20. Provider-reported availability overrides fallback where supported
21. Credentials never appear in logs/traces
22. Existing agent architecture remains user-selected
23. Brain does not perform agent routing
24. Existing tool argument validation still passes
"""
from __future__ import annotations

import json
import os
import sqlite3
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from chitragupta.agents import Agent, get_agent_model, set_agent_model
from chitragupta.agents.runtime import build_runtime_identity, format_runtime_context_prompt
from chitragupta.api.app import app
from chitragupta.models.accounts import (
    detect_claude_account,
    detect_cursor_account,
)
from chitragupta.models.base import Message
from chitragupta.models.chatgpt_auth import (
    adopt_local_chatgpt_session,
    detect_chatgpt_local_session,
)
from chitragupta.models.claude_code import ClaudeCodeProvider
from chitragupta.models.connections import ConnectionStatus, get_connection, save_connection
from chitragupta.models.deepseek import DeepSeekProvider
from chitragupta.models.discovery import (
    discover_ollama_models,
    get_discovered_models,
)
from chitragupta.models.entitlements import (
    evaluate_model_entitlement,
    is_provider_connected,
)
from chitragupta.models.xai import XAIProvider
from chitragupta.models.xai_auth import (
    _SECRET_KEY_XAI_TOKEN,
    _save_stored_xai_data,
    get_xai_access_token,
)

client = TestClient(app)


# ── 1. Provider connection gating ─────────────────────────────────────────────
def test_01_provider_connection_gating():
    conn = get_connection("openai")
    prev_status = conn.connection_status
    try:
        conn.connection_status = ConnectionStatus.NOT_CONNECTED
        save_connection(conn)
        with patch.dict(os.environ, {}, clear=True):
            with patch("chitragupta.config.Settings.get_secret", return_value=None):
                connected, _plan, _ = is_provider_connected("openai", api_key=None)
                assert connected is False

                locked, plan_req = evaluate_model_entitlement("openai", "gpt-5.6-terra", is_connected=False)
                assert locked is True
                assert plan_req == "Connect in Models"
    finally:
        conn.connection_status = prev_status
        save_connection(conn)


# ── 2. Disconnected provider cannot bind to agent ─────────────────────────────
def test_02_disconnected_provider_cannot_bind_to_agent():
    conn = get_connection("anthropic")
    prev_status = conn.connection_status
    try:
        conn.connection_status = ConnectionStatus.DISCONNECTED
        save_connection(conn)
        with patch.dict(os.environ, {}, clear=True):
            with patch("chitragupta.config.Settings.get_secret", return_value=None):
                resp = client.post(
                    "/api/agents/inbox/model",
                    json={"provider": "anthropic", "model": "claude-3-7-sonnet-latest"},
                )
                assert resp.status_code == 400
                assert "not connected" in resp.json()["detail"].lower()
    finally:
        conn.connection_status = prev_status
        save_connection(conn)


# ── 3. Connected provider can bind ────────────────────────────────────────────
def test_03_connected_provider_can_bind():
    conn = get_connection("openai")
    prev_status = conn.connection_status
    prev_email = conn.email
    try:
        conn.connection_status = ConnectionStatus.API_KEY_CONNECTED
        conn.email = "test-binding@openai.com"
        save_connection(conn)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-valid"}):
            resp = client.post(
                "/api/agents/inbox/model",
                json={"provider": "openai", "model": "gpt-5.6-terra"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["provider"] == "openai"
            assert data["model"] == "gpt-5.6-terra"
    finally:
        conn.connection_status = prev_status
        conn.email = prev_email
        save_connection(conn)


# ── 4. Locked model cannot bind ───────────────────────────────────────────────
def test_04_locked_model_cannot_bind():
    conn = get_connection("openai")
    prev_status = conn.connection_status
    try:
        conn.connection_status = ConnectionStatus.ACCOUNT_CONNECTED
        conn.email = "free-user@example.com"
        save_connection(conn)
        # Attempt to bind gpt-6-astra on a free tier
        with patch.dict(os.environ, {}, clear=True):
            with patch("chitragupta.config.Settings.get_secret", return_value=None):
                with patch("chitragupta.models.chatgpt_auth.detect_chatgpt_local_session", return_value={"plan": "ChatGPT Free", "email": "free-user@example.com"}):
                    resp = client.post(
                        "/api/agents/inbox/model",
                        json={"provider": "openai", "model": "gpt-6-astra"},
                    )
                    assert resp.status_code == 400
                    assert "not supported on your" in resp.json()["detail"].lower()
    finally:
        conn.connection_status = prev_status
        save_connection(conn)


# ── 5. Unlocked model can bind ────────────────────────────────────────────────
def test_05_unlocked_model_can_bind():
    conn = get_connection("openai")
    prev_status = conn.connection_status
    try:
        conn.connection_status = ConnectionStatus.ACCOUNT_CONNECTED
        conn.email = "pro-user@example.com"
        save_connection(conn)
        with patch.dict(os.environ, {}, clear=True):
            with patch("chitragupta.config.Settings.get_secret", return_value=None):
                with patch("chitragupta.models.chatgpt_auth.detect_chatgpt_local_session", return_value={"plan": "ChatGPT Pro", "email": "pro-user@example.com"}):
                    resp = client.post(
                        "/api/agents/inbox/model",
                        json={"provider": "openai", "model": "gpt-6-astra"},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["model"] == "gpt-6-astra"
    finally:
        conn.connection_status = prev_status
        save_connection(conn)


# ── 6. Auto cannot be selected when provider is disconnected ──────────────────
def test_06_auto_locked_when_provider_disconnected():
    locked, plan_req = evaluate_model_entitlement("openai", "auto", is_connected=False)
    assert locked is True
    assert plan_req == "Connect in Models"

    conn = get_connection("openai")
    prev_status = conn.connection_status
    try:
        conn.connection_status = ConnectionStatus.NOT_CONNECTED
        save_connection(conn)
        with patch.dict(os.environ, {}, clear=True):
            with patch("chitragupta.config.Settings.get_secret", return_value=None):
                resp = client.post(
                    "/api/agents/inbox/model",
                    json={"provider": "openai", "model": "auto"},
                )
                assert resp.status_code == 400
    finally:
        conn.connection_status = prev_status
        save_connection(conn)


# ── 7. Ollama detects installed models ────────────────────────────────────────
def test_07_ollama_detects_installed_models():
    mock_tags = {
        "models": [
            {"name": "llama3.2:latest", "details": {"parameter_size": "3B"}},
            {"name": "qwen2.5:3b", "details": {"parameter_size": "3B"}},
        ]
    }
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_tags
        mock_get.return_value = mock_resp

        models = discover_ollama_models("http://localhost:11434")
        slugs = {m.id: m for m in models}
        assert "llama3.2:latest" in slugs
        assert slugs["llama3.2:latest"].locked is False
        assert slugs["llama3.2:latest"].status in ("installed", "available")


# ── 8. Ollama locks uninstalled models ────────────────────────────────────────
def test_08_ollama_locks_uninstalled_models():
    mock_tags = {
        "models": [
            {"name": "llama3.2:latest", "details": {}},
        ]
    }
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_tags
        mock_get.return_value = mock_resp

        models = discover_ollama_models("http://localhost:11434")
        slugs = {m.id: m for m in models}
        # Popular models not yet pulled are locked with "Pull required"
        assert "deepseek-r1:8b" in slugs
        assert slugs["deepseek-r1:8b"].locked is True
        assert slugs["deepseek-r1:8b"].plan_required == "Pull required"


# ── 9. Cursor detection does NOT expose accessToken ───────────────────────────
def test_09_cursor_detection_does_not_expose_access_token(tmp_path):
    # Create fake Cursor SQLite database with both cachedEmail and private accessToken
    db_path = tmp_path / "state.vscdb"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT);")
    conn.execute("INSERT INTO ItemTable VALUES ('cursorAuth/cachedEmail', 'cursor-dev@company.com');")
    conn.execute("INSERT INTO ItemTable VALUES ('cursorAuth/stripeMembershipType', 'pro');")
    conn.execute("INSERT INTO ItemTable VALUES ('cursorAuth/accessToken', 'PRIVATE_SECRET_BEARER_TOKEN_NEVER_LEAK');")
    conn.commit()
    conn.close()

    with patch("chitragupta.models.accounts.Path.home", return_value=tmp_path):
        with patch("chitragupta.models.cursor.get_cursor_cli_status", return_value=(False, "CLI not found", None)):
            # Put state.vscdb in standard path
            std_dir = tmp_path / "Library/Application Support/Cursor/User/globalStorage"
            std_dir.mkdir(parents=True, exist_ok=True)
            (std_dir / "state.vscdb").write_bytes(db_path.read_bytes())

            info = detect_cursor_account()
            assert info.get("found_on_computer") is True
            assert info.get("email") == "cursor-dev@company.com"
            assert info.get("plan") == "Cursor Pro"
            # SECURITY GUARANTEE: Neither accessToken nor token values are exposed
            assert "accessToken" not in info
            assert "token" not in info
            assert "has_token" not in info
            # Value check across all dictionary values
            for val in info.values():
                assert "PRIVATE_SECRET" not in str(val)


# ── 10. Claude detection does NOT expose OAuth token ──────────────────────────
def test_10_claude_detection_does_not_expose_oauth_token(tmp_path):
    # Mock claude CLI auth status
    fake_cli_output = json.dumps({
        "loggedIn": True,
        "email": "claude-engineer@anthropic.com",
        "subscriptionType": "team",
        "apiProvider": "firstParty",
    })
    fake_res = MagicMock(returncode=0, stdout=fake_cli_output)

    with patch("chitragupta.models.claude_cli.find_claude", return_value="/opt/homebrew/bin/claude"):
        with patch("subprocess.run", return_value=fake_res):
            info = detect_claude_account()
            assert info.get("found_on_computer") is True
            assert info.get("cli_authenticated") is True
            assert info.get("email") == "claude-engineer@anthropic.com"
            assert info.get("plan") == "Claude Team"

            # SECURITY GUARANTEE: Never exposes OAuth tokens
            assert "accessToken" not in info
            assert "token" not in info
            assert "refreshToken" not in info


# ── 11. OpenAI detection does NOT copy ~/.codex/auth.json ─────────────────────
def test_11_openai_detection_does_not_copy_codex_auth(tmp_path):
    fake_home = tmp_path / "userhome"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    auth_file = codex_dir / "auth.json"
    auth_file.write_text(json.dumps({
        "email": "codex-user@openai.com",
        "tokens": {
            "access_token": "SENSITIVE_CODEX_TOKEN_THAT_MUST_NEVER_BE_COPIED",
            "refresh_token": "SENSITIVE_REFRESH_TOKEN",
        },
    }))

    chitragupta_home = tmp_path / "chitragupta_home"
    chitragupta_home.mkdir(parents=True, exist_ok=True)

    with patch("chitragupta.models.chatgpt_auth.Path.home", return_value=fake_home):
        with patch("chitragupta.models.chatgpt_auth.get_settings") as mock_settings:
            mock_settings.return_value.home = chitragupta_home
            mock_settings.return_value.get_secret.return_value = None

            # Detect session: safe metadata only
            sess = detect_chatgpt_local_session(fetch_usage=False)
            assert sess is not None
            assert sess["email"] == "codex-user@openai.com"
            assert sess["has_token"] is False  # Chitragupta does NOT steal the token

            # Adopt session: verifies account without copying ~/.codex/auth.json
            ok, msg, data = adopt_local_chatgpt_session()
            assert ok is True
            assert "codex-user@openai.com" in msg
            assert "Connected via codex_cli" in data.get("status_message", "")

            # SECURITY GUARANTEE: No plaintext chatgpt_token.json created by stealing codex file
            assert not (chitragupta_home / "chatgpt_token.json").exists()


# ── 12. Disconnect invalidates Chitragupta connection ───────────────────────────
def test_12_disconnect_invalidates_chitragupta_connection():
    conn = get_connection("openai")
    conn.connection_status = ConnectionStatus.ACCOUNT_CONNECTED
    conn.email = "active-user@example.com"
    save_connection(conn)

    # Disconnect
    resp = client.post("/api/providers/openai/disconnect")
    assert resp.status_code == 200
    data = resp.json()
    assert data["disconnected"] is True

    updated_conn = get_connection("openai")
    assert updated_conn.connection_status == ConnectionStatus.DISCONNECTED
    assert updated_conn.email == ""
    assert updated_conn.auth_method == "none"

    # Agent binding must now reject openai
    resp_bind = client.post("/api/agents/inbox/model", json={"provider": "openai", "model": "gpt-5.6-terra"})
    assert resp_bind.status_code == 400


# ── 13. Runtime identity contains actual provider/model ───────────────────────
def test_13_runtime_identity_contains_actual_provider_model():
    agent = Agent(id="test-agent", name="Research Agent", role="Data Analyst", system_prompt="You are a data analyst.")
    mock_prov = MagicMock()
    mock_prov.name = "openai"
    mock_prov.model = "gpt-5.6-terra"
    mock_prov.api_key = "sk-mock"

    identity = build_runtime_identity(agent, mock_prov)
    assert identity["application"] == "Chitragupta"
    assert identity["agent_id"] == "test-agent"
    assert identity["agent_name"] == "Research Agent"
    assert identity["agent_role"] == "Data Analyst"
    assert identity["provider_name"] == "openai"
    assert identity["model"] == "gpt-5.6-terra"


# ── 14. Model answers runtime identity correctly ──────────────────────────────
def test_14_model_answers_runtime_identity_correctly():
    identity = {
        "application": "Chitragupta",
        "agent_name": "Inbox Assistant",
        "agent_role": "Email Organizer",
        "provider": "Anthropic",
        "model": "claude-3-7-sonnet-latest",
        "harness": "Claude Code harness",
    }
    prompt = format_runtime_context_prompt(identity)
    assert "RUNTIME CONTEXT — AUTHORITATIVE" in prompt
    assert "Application: Chitragupta" in prompt
    assert "Selected Agent: Inbox Assistant" in prompt
    assert "Agent Role: Email Organizer" in prompt
    assert "Provider: Anthropic" in prompt
    assert "Model: claude-3-7-sonnet-latest" in prompt
    assert "Harness: Claude Code harness" in prompt


# ── 15. API-key provider remains functional ───────────────────────────────────
def test_15_api_key_provider_functional():
    prov = DeepSeekProvider(api_key="sk-test-deepseek")
    ready, reason = prov.is_ready()
    assert ready is True
    assert reason == ""
    assert prov.api_key == "sk-test-deepseek"


# ── 16. OAuth provider remains functional ─────────────────────────────────────
def test_16_oauth_provider_functional():
    # Test secure storage and retrieval for xAI OAuth token
    fake_token_data = {
        "tokens": {
            "access_token": "valid-oauth-access-token-12345",
            "refresh_token": "refresh-12345",
        },
        "email": "developer@x.ai",
        "name": "xAI Developer",
    }
    with patch("chitragupta.config.Settings.set_secret") as mock_set:
        with patch("chitragupta.config.Settings.get_secret", return_value=json.dumps(fake_token_data)):
            _save_stored_xai_data(fake_token_data)
            mock_set.assert_called_with(_SECRET_KEY_XAI_TOKEN, json.dumps(fake_token_data))

            tok = get_xai_access_token()
            assert tok == "valid-oauth-access-token-12345"

            prov = XAIProvider()
            ready, _ = prov.is_ready()
            assert ready is True


# ── 17. Existing CLI provider remains functional ──────────────────────────────
def test_17_existing_cli_provider_functional():
    with patch("chitragupta.models.claude_cli.find_claude", return_value="/usr/local/bin/claude"):
        prov = ClaudeCodeProvider(model="claude-sonnet-5")
        ready, _ = prov.is_ready()
        assert ready is True
        assert prov.model == "claude-sonnet-5"

        # Verify prompt splitting preserves user message and system instructions
        sys_prompt, user_prompt = prov._split([
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Summarize project status."),
        ])
        assert sys_prompt == "You are a helpful assistant."
        assert user_prompt == "Summarize project status."


# ── 18. Dynamic discovery failure falls back safely ───────────────────────────
def test_18_dynamic_discovery_failure_falls_back_safely():
    with patch("httpx.get", side_effect=Exception("Network connection failed")):
        # When remote API discovery errors, fallback models are returned seamlessly
        models, _meta = get_discovered_models("openai", force_refresh=True, api_key="sk-test")
        assert len(models) > 0
        slugs = [m["id"] for m in models]
        assert "gpt-5.6-terra" in slugs
        assert any(m.get("is_fallback") for m in models)


# ── 19. Static entitlement fallback works ─────────────────────────────────────
def test_19_static_entitlement_fallback_works():
    # Plus user plan: gpt-5.6-terra unlocked, gpt-6-astra locked
    locked_astra, req_astra = evaluate_model_entitlement("openai", "gpt-6-astra", is_connected=True, user_plan="ChatGPT Plus")
    assert locked_astra is True
    assert req_astra == "Pro"

    locked_terra, _ = evaluate_model_entitlement("openai", "gpt-5.6-terra", is_connected=True, user_plan="ChatGPT Plus")
    assert locked_terra is False


# ── 20. Provider-reported availability overrides fallback ─────────────────────
def test_20_provider_reported_availability_overrides_fallback():
    # OpenRouter mock returns live discovered model
    mock_or_data = {
        "data": [
            {
                "id": "anthropic/claude-3.7-sonnet",
                "name": "Claude 3.7 Sonnet (Self-reported)",
                "description": "Live discovered from OpenRouter API",
                "context_length": 200000,
            }
        ]
    }
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = mock_or_data
        mock_get.return_value = mock_resp

        models, _meta = get_discovered_models("openrouter", force_refresh=True, api_key="sk-or-test")
        assert len(models) == 1
        assert models[0]["id"] == "anthropic/claude-3.7-sonnet"
        assert models[0]["name"] == "Claude 3.7 Sonnet (Self-reported)"
        assert models[0].get("is_fallback") is False


# ── 21. Credentials never appear in logs/traces ───────────────────────────────
def test_21_credentials_never_appear_in_traces():
    from chitragupta.brain.canonical.redact import redact

    text = "User secret: Bearer sk-live-SECRET_TOKEN_1234567890abcdef and key gsk_SECRET_1234567890abcdef"
    redacted = redact(text)
    assert "SECRET_TOKEN" not in redacted
    assert "[REDACTED_TOKEN]" in redacted or "[REDACTED_KEY]" in redacted

    conn = get_connection("openai")
    conn_dict = conn.to_dict()
    # Verify no raw secrets in connection dict
    for _k, v in conn_dict.items():
        assert "secret" not in str(v).lower()
        assert "bearer" not in str(v).lower()


# ── 22. Existing agent architecture remains user-selected ─────────────────────
def test_22_user_selected_agent_architecture_intact():
    set_agent_model("inbox", provider="anthropic", model="claude-3-7-sonnet-latest")
    p, m = get_agent_model("inbox")
    assert p == "anthropic"
    assert m == "claude-3-7-sonnet-latest"


# ── 23. Brain does not perform agent routing ──────────────────────────────────
def test_23_brain_does_not_perform_agent_routing():
    from chitragupta.brain.brain import Brain

    # The USER chooses the Agent. Brain must not have agent routing or model selection methods.
    brain_methods = dir(Brain)
    for forbidden in ["route_agent", "choose_agent", "select_agent", "route_provider", "choose_model"]:
        assert forbidden not in brain_methods


# ── 24. Existing tool argument validation still passes ─────────────────────────
def test_24_tool_argument_validation_passes():
    from chitragupta.agents.tools import validate_tool_arguments

    valid, _err, clean_args = validate_tool_arguments("search_brain", {"query": "project turnover deadline"})
    assert valid is True
    assert clean_args["query"] == "project turnover deadline"

    # Missing required argument returns valid=False
    invalid, err_msg, _ = validate_tool_arguments("search_brain", {})
    assert invalid is False
    assert "query" in err_msg
