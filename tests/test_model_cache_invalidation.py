"""Regression tests for provider connection -> model unlock.

The model catalog cached its *entitlement* result alongside the discovery
result, and `registry.get_model_catalog` then mutated those cached rows in
place. A user who signed in successfully kept seeing every model locked until
the one-hour discovery TTL expired. These tests pin the fixed behaviour:
discovery may be cached, lock state never is.
"""
import json
import re

import pytest
from web_sources import app_source

from chitragupta.models import connection_state, discovery
from chitragupta.models.discovery import (
    DiscoveredModel,
    clear_model_cache,
    get_discovered_models,
    normalize_provider_id,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_model_cache()
    yield
    clear_model_cache()


def _stub_discovery(monkeypatch, models):
    """Count discovery calls so we can prove caching still works."""
    calls = {"n": 0}

    def fake(pid, api_key):
        calls["n"] += 1
        return list(models), {}

    monkeypatch.setattr(discovery, "_discover_raw", fake)
    return calls


def _stub_connection(monkeypatch, state):
    def fake(pid, api_key=None):
        if state["connected"]:
            return True, state.get("plan"), state.get("meta", {})
        return False, None, {}

    monkeypatch.setattr(connection_state, "is_provider_connected", fake)


# ── the bug ───────────────────────────────────────────────────────────────
def test_connecting_a_provider_unlocks_models_without_waiting_for_ttl(monkeypatch):
    calls = _stub_discovery(monkeypatch, [DiscoveredModel("deepseek-chat", "DeepSeek V3", "")])
    state = {"connected": False}
    _stub_connection(monkeypatch, state)

    before, _ = get_discovered_models("deepseek")
    assert before[0]["locked"] is True
    assert before[0]["plan_required"] == "Connect in Models"

    # User signs in. No force_refresh, no TTL expiry — it must unlock now.
    state.update(connected=True, plan="DeepSeek Account")
    after, _ = get_discovered_models("deepseek")

    assert after[0]["locked"] is False
    assert after[0]["plan_required"] is None
    assert after[0]["status"] == "available"
    # ...while the expensive part stayed cached.
    assert calls["n"] == 1


def test_disconnecting_relocks_models_immediately(monkeypatch):
    _stub_discovery(monkeypatch, [DiscoveredModel("deepseek-chat", "DeepSeek V3", "")])
    state = {"connected": True, "plan": "DeepSeek Account"}
    _stub_connection(monkeypatch, state)

    assert get_discovered_models("deepseek")[0][0]["locked"] is False
    state["connected"] = False
    assert get_discovered_models("deepseek")[0][0]["locked"] is True


def test_callers_cannot_poison_the_cache(monkeypatch):
    """registry.get_model_catalog used to set locked=True on these very dicts."""
    _stub_discovery(monkeypatch, [DiscoveredModel("mock-1", "Mock Test Model", "")])
    _stub_connection(monkeypatch, {"connected": True, "plan": "Mock"})

    first, _ = get_discovered_models("mock")
    first[0]["locked"] = True
    first[0]["name"] = "MUTATED"
    first[0]["plan_required"] = "Connect in Models"

    second, _ = get_discovered_models("mock")
    assert second[0]["name"] == "Mock Test Model"
    assert second[0]["locked"] is False
    assert second[0]["plan_required"] is None


def test_account_meta_survives_a_cache_hit(monkeypatch):
    """A cache hit used to return an empty meta dict, blanking the account card."""
    _stub_discovery(monkeypatch, [DiscoveredModel("gpt-5.5", "GPT-5.5", "")])
    _stub_connection(monkeypatch, {
        "connected": True, "plan": "ChatGPT Free",
        "meta": {"email": "user@example.com"},
    })

    _, first_meta = get_discovered_models("openai")
    _, cached_meta = get_discovered_models("openai")
    assert first_meta.get("email") == "user@example.com"
    assert cached_meta.get("email") == "user@example.com"


# ── cache invalidation plumbing ───────────────────────────────────────────
def test_clear_provider_cache_also_drops_discovery(monkeypatch):
    from chitragupta.models.registry import clear_provider_cache

    calls = _stub_discovery(monkeypatch, [DiscoveredModel("mock-1", "Mock", "")])
    _stub_connection(monkeypatch, {"connected": True, "plan": "Mock"})

    get_discovered_models("mock")
    get_discovered_models("mock")
    assert calls["n"] == 1, "second read should have been served from cache"

    clear_provider_cache("mock")
    get_discovered_models("mock")
    assert calls["n"] == 2, "credential change must force re-discovery"


def test_clear_model_cache_resolves_provider_aliases(monkeypatch):
    calls = _stub_discovery(monkeypatch, [DiscoveredModel("claude-opus-5", "Opus 5", "")])
    _stub_connection(monkeypatch, {"connected": True, "plan": "Claude Pro"})

    get_discovered_models("claude")
    clear_model_cache("anthropic")          # alias of the same provider
    get_discovered_models("claude")
    assert calls["n"] == 2


@pytest.mark.parametrize("alias,canonical", [
    ("anthropic", "claude"), ("google", "gemini"), ("grok", "xai"),
    ("CLAUDE", "claude"), ("openai", "openai"),
])
def test_provider_alias_normalisation(alias, canonical):
    assert normalize_provider_id(alias) == canonical


# ── the swallowed ImportError in the disconnect endpoint ──────────────────
def test_oauth_providers_expose_a_working_disconnect():
    """app.py imported `_stop_xai_server_async`, which never existed — the
    ImportError was swallowed, so xAI tokens were never actually deleted."""
    from chitragupta.models import chatgpt_auth, xai_auth

    for mod in (chatgpt_auth, xai_auth):
        assert callable(getattr(mod, "disconnect", None)), f"{mod.__name__}.disconnect missing"
        mod.disconnect()   # must not raise on a machine with no stored token


def test_disconnect_endpoint_clears_xai_credentials(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app
    from chitragupta.models import xai_auth

    token_file = tmp_path / "xai_token.json"
    token_file.write_text(json.dumps({"tokens": {"access_token": "secret"}}))
    monkeypatch.setattr(xai_auth, "_token_storage_path", lambda: token_file)
    monkeypatch.setattr(xai_auth, "_save_stored_xai_data", lambda data: None)

    resp = TestClient(app).post("/api/providers/xai/disconnect")
    assert resp.status_code == 200
    assert resp.json()["disconnected"] is True
    assert not token_file.exists(), "stored xAI token must be deleted on disconnect"


# ── provider surface parity ───────────────────────────────────────────────
def test_composer_picker_offers_every_selectable_provider():
    """The agent-tab picker silently omitted claude-code and subscription, so
    the one Claude path that actually works could not be chosen."""
    from chitragupta.models.registry import PRIMARY_PROVIDERS

    app_js = app_source()
    block = re.search(r"const COMPOSER_PROVIDERS = \[(.*?)\];", app_js, re.S)
    assert block, "COMPOSER_PROVIDERS not found in app.js"
    listed = set(re.findall(r'id:\s*"([^"]+)"', block.group(1)))

    expected = set(PRIMARY_PROVIDERS) - {"mock"}   # mock is a test fixture, not a choice
    assert expected <= listed, f"missing from composer picker: {sorted(expected - listed)}"


def test_subscription_is_not_connected_without_a_gateway(monkeypatch):
    """It used to hardcode `return True` — advertising models every call failed on."""
    monkeypatch.delenv("CHITRAGUPTA_SUBSCRIPTION_BASE_URL", raising=False)
    connected, plan, _ = connection_state.is_provider_connected("subscription")
    assert connected is False and plan is None

    monkeypatch.setenv("CHITRAGUPTA_SUBSCRIPTION_BASE_URL", "http://localhost:8080/v1")
    connected, plan, meta = connection_state.is_provider_connected("subscription")
    assert connected is True
    assert meta["host"] == "http://localhost:8080/v1"
