"""A stored model id must never outlive the provider's catalog.

Model choices are persisted — in an agent's binding, in localStorage — but a
provider retires ids underneath them. Nothing re-checked the stored value, so a
binding saved before a retirement sent the dead id straight to the provider and
the user got `400 Model not found` on every message.
"""
from unittest.mock import patch

import pytest

from chitragupta.models import connection_state, discovery
from chitragupta.models.discovery import DiscoveredModel, clear_model_cache
from chitragupta.models.entitlements import resolve_usable_model

LIVE = [DiscoveredModel("grok-4.6", "Grok 4.6", ""),
        DiscoveredModel("grok-4.5", "Grok 4.5", ""),
        DiscoveredModel("grok-4.3", "Grok 4.3", "")]


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(discovery, "_discover_raw", lambda pid, k: (list(LIVE), {}))
    monkeypatch.setattr(connection_state, "is_provider_connected",
                        lambda pid, api_key=None: (True, "xAI Grok", {}))
    clear_model_cache()
    yield
    clear_model_cache()


@pytest.mark.parametrize("retired", ["grok-2-latest", "grok-2-1212", "grok-3"])
def test_retired_ids_are_replaced_not_sent(connected, retired):
    usable, replaced = resolve_usable_model("xai", retired)
    assert usable in {m.id for m in LIVE}
    assert replaced == retired


def test_a_live_model_passes_through_untouched(connected):
    assert resolve_usable_model("xai", "grok-4.5") == ("grok-4.5", None)


def test_no_model_resolves_to_one_the_account_can_run(connected):
    """"Auto" means the best model this account offers, not a hardcoded id.

    This asserted `(None, None)` — "no model stays no model" — which sounds
    conservative and was not. None means `registry.default_model` decides, and
    that is a constant chosen long before any user had an account. A ChatGPT
    Free account was refused for `gpt-5.6-terra` it had never selected, from
    the one setting that is supposed to be the safe choice.

    Inventing a model where we have *no evidence* is still wrong, and still
    does not happen — a fallback catalog or a failed discovery returns None.
    """
    usable, replaced = resolve_usable_model("xai", None)

    assert usable in {m.id for m in LIVE}
    assert replaced is None, "nothing was replaced — the user asked for Auto"


def test_a_plan_locked_model_is_replaced_with_one_the_user_can_run(monkeypatch):
    models = [DiscoveredModel("grok-4.6", "Grok 4.6", "", locked=True, plan_required="Tier 2"),
              DiscoveredModel("grok-4.3", "Grok 4.3", "")]
    monkeypatch.setattr(discovery, "_discover_raw", lambda pid, k: (models, {}))
    monkeypatch.setattr(connection_state, "is_provider_connected",
                        lambda pid, api_key=None: (True, "Tier 1", {}))
    clear_model_cache()
    usable, replaced = resolve_usable_model("xai", "grok-4.6")
    assert usable == "grok-4.3" and replaced == "grok-4.6"


def test_discovery_failure_never_blocks_a_turn(monkeypatch):
    monkeypatch.setattr(discovery, "_discover_raw",
                        lambda pid, k: (_ for _ in ()).throw(RuntimeError("network down")))
    clear_model_cache()
    assert resolve_usable_model("xai", "grok-4.6") == ("grok-4.6", None)


def test_nothing_usable_falls_back_to_the_provider_default(monkeypatch):
    monkeypatch.setattr(discovery, "_discover_raw",
                        lambda pid, k: ([DiscoveredModel("grok-4.6", "G", "", locked=True,
                                                         plan_required="Connect in Models")], {}))
    monkeypatch.setattr(connection_state, "is_provider_connected",
                        lambda pid, api_key=None: (False, None, {}))
    clear_model_cache()
    usable, replaced = resolve_usable_model("xai", "grok-2-latest")
    assert usable is None, "a model we know will be rejected must not be sent"
    assert replaced == "grok-2-latest"


# ── the binding repairs itself ───────────────────────────────────────────
def test_a_stale_agent_binding_is_repaired(connected, monkeypatch):
    from chitragupta.agents.agent_models import get_agent_model, set_agent_model
    from chitragupta.agents.runtime import run_turn

    set_agent_model("inbox", "xai", "grok-2-latest")
    assert get_agent_model("inbox") == ("xai", "grok-2-latest")

    with patch("chitragupta.agents.runtime.get_provider") as gp:
        gp.return_value.is_ready.return_value = (False, "stop here")
        run_turn("inbox", "hello")

    assert get_agent_model("inbox") == ("xai", "grok-4.6"), "the dead id was left in place"


def test_a_per_request_override_does_not_rewrite_the_binding(connected):
    from chitragupta.agents.agent_models import get_agent_model, set_agent_model
    from chitragupta.agents.runtime import run_turn

    set_agent_model("inbox", "xai", "grok-4.5")
    with patch("chitragupta.agents.runtime.get_provider") as gp:
        gp.return_value.is_ready.return_value = (False, "stop here")
        run_turn("inbox", "hello", provider_name="xai", model_name="grok-2-1212")

    assert get_agent_model("inbox") == ("xai", "grok-4.5"), \
        "a one-off request overwrote the agent's saved choice"


# ── substitution requires positive evidence ──────────────────────────────
def test_absence_from_a_fallback_list_is_not_evidence(monkeypatch):
    """A hardcoded list is not the account's catalog, so an unlisted id may
    still be perfectly valid — swapping it would silently change the model."""
    monkeypatch.setattr(discovery, "_discover_raw",
                        lambda pid, k: ([DiscoveredModel("grok-4.6", "G", "", is_fallback=True)], {}))
    monkeypatch.setattr(connection_state, "is_provider_connected",
                        lambda pid, api_key=None: (True, "xAI Grok", {}))
    clear_model_cache()
    assert resolve_usable_model("xai", "grok-4.9-brand-new") == ("grok-4.9-brand-new", None)


def test_backends_that_accept_any_model_are_left_alone():
    """`mock` takes any string; its one-entry catalog must not swap ids."""
    assert resolve_usable_model("mock", "mock-custom-model") == ("mock-custom-model", None)
