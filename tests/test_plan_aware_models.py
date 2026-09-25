"""Model availability must follow the user's own plan, on any machine.

Availability used to be decided by a hardcoded tier table, so every user saw
whatever list a developer wrote down — and a provider's own answer about the
account was thrown away. These tests pin the authority order:
connection -> the provider's answer -> static tables (fallback only).
"""
import json
import re

import pytest
from web_sources import app_source

from chitragupta.models import connection_state, discovery
from chitragupta.models.discovery import DiscoveredModel, clear_model_cache, get_discovered_models
from chitragupta.models.entitlements import evaluate_model_entitlement
from chitragupta.models.registry import _LOCALITY, MODEL_CATALOG, PRIMARY_PROVIDERS


@pytest.fixture(autouse=True)
def _clean():
    clear_model_cache()
    yield
    clear_model_cache()


def _serve(monkeypatch, models, *, connected=True, plan=None):
    monkeypatch.setattr(discovery, "_discover_raw", lambda pid, api_key: (list(models), {}))
    monkeypatch.setattr(connection_state, "is_provider_connected",
                        lambda pid, api_key=None: (connected, plan, {}))


# ── the provider's answer is authoritative ───────────────────────────────
def test_provider_verdict_beats_the_static_table(monkeypatch):
    """gpt-5.6-sol is 'Pro' in the table; if the account reports it available,
    it is available."""
    _serve(monkeypatch, [DiscoveredModel("gpt-5.6-sol", "Sol", "", locked=False)],
           plan="ChatGPT Free")
    assert get_discovered_models("openai")[0][0]["locked"] is False


def test_provider_verdict_can_lock_what_the_table_allows(monkeypatch):
    """gpt-5.5 is Free in the table; if the account says no, it stays locked."""
    _serve(monkeypatch,
           [DiscoveredModel("gpt-5.5", "GPT-5.5", "", locked=True, plan_required="Pro")],
           plan="ChatGPT Pro")
    row = get_discovered_models("openai")[0][0]
    assert row["locked"] is True and row["plan_required"] == "Pro"


def test_static_table_still_applies_to_fallback_rows(monkeypatch):
    """A hardcoded row carries no provider answer, so the table decides."""
    _serve(monkeypatch,
           [DiscoveredModel("gpt-6-astra", "Astra", "", is_fallback=True)],
           plan="ChatGPT Free")
    row = get_discovered_models("openai")[0][0]
    assert row["locked"] is True and row["plan_required"] == "Pro"


def test_two_accounts_on_one_build_see_different_models(monkeypatch):
    """The core promise: availability is per user, not per build."""
    catalog = [DiscoveredModel("gpt-6-astra", "Astra", ""),
               DiscoveredModel("gpt-5.5", "GPT-5.5", "")]

    def as_account(plan, available):
        rows = [DiscoveredModel(m.id, m.name, "", locked=m.id not in available,
                                plan_required=None if m.id in available else "Pro")
                for m in catalog]
        _serve(monkeypatch, rows, plan=plan)
        clear_model_cache()
        return {r["id"]: r["locked"] for r in get_discovered_models("openai")[0]}

    free = as_account("ChatGPT Free", {"gpt-5.5"})
    pro = as_account("ChatGPT Pro", {"gpt-5.5", "gpt-6-astra"})

    assert free["gpt-6-astra"] is True and free["gpt-5.5"] is False
    assert pro["gpt-6-astra"] is False and pro["gpt-5.5"] is False


def test_connection_gate_outranks_everything(monkeypatch):
    _serve(monkeypatch, [DiscoveredModel("gpt-5.5", "GPT-5.5", "", locked=False)],
           connected=False)
    row = get_discovered_models("openai")[0][0]
    assert row["locked"] is True and row["plan_required"] == "Connect in Models"


def test_locked_models_stay_visible_with_a_reason(monkeypatch):
    """Greyed-out-with-a-reason, never hidden."""
    _serve(monkeypatch,
           [DiscoveredModel("a", "A", "", locked=True, plan_required="Pro"),
            DiscoveredModel("b", "B", "", locked=False)],
           plan="ChatGPT Free")
    rows = get_discovered_models("openai")[0]
    assert len(rows) == 2
    locked = next(r for r in rows if r["locked"])
    assert locked["plan_required"] and locked["status"] == "locked"


def test_provider_reported_lock_without_a_reason_still_explains_itself():
    locked, why = evaluate_model_entitlement("openai", "x", is_connected=True,
                                             provider_reported=(True, None))
    assert locked is True and why


# ── catalogs must not drift or carry retired ids ─────────────────────────
RETIRED = {
    "claude-fable-5-1", "claude-3-7-sonnet", "claude-3-7-sonnet-latest",
    "claude-3-5-sonnet", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest",
    "grok-3", "grok-3-mini", "grok-2-latest", "grok-2-vision-latest", "grok-2-1212",
    "gemini-2.0-flash", "anthropic/claude-3.7-sonnet", "claude-3.7-sonnet",
}


@pytest.mark.parametrize("pid", PRIMARY_PROVIDERS)
def test_no_provider_offers_a_retired_model(pid):
    ids = {m["id"] for m in MODEL_CATALOG[pid]["models"]}
    assert not (ids & RETIRED), f"{pid} lists retired ids: {sorted(ids & RETIRED)}"


@pytest.mark.parametrize("pid", PRIMARY_PROVIDERS)
def test_default_model_is_offered_and_not_retired(pid):
    entry = MODEL_CATALOG[pid]
    default = entry["default_model"]
    assert default not in RETIRED, f"{pid} defaults to a retired model"
    assert default in {m["id"] for m in entry["models"]}, \
        f"{pid} defaults to a model it does not list"


def test_frontend_fallback_catalog_matches_the_python_catalog():
    """app.js::FALLBACK_CATALOG is generated from MODEL_CATALOG — if this fails,
    regenerate it rather than hand-editing."""
    src = app_source()
    block = re.search(r"const FALLBACK_CATALOG = \[(.*?)\n\];", src, re.S)
    assert block, "FALLBACK_CATALOG not found"
    js = json.loads("[" + block.group(1) + "]")

    for entry in js:
        pid = entry["id"]
        expected = [m["id"] for m in MODEL_CATALOG[pid]["models"]]
        assert [m["id"] for m in entry["models"]] == expected, f"{pid} drifted"
        assert entry["default_model"] == MODEL_CATALOG[pid]["default_model"]
        assert entry["locality"] == _LOCALITY[pid][0]
