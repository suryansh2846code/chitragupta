"""A test fixture is never a thing a user can pick.

`mock` is a deterministic offline provider that exists so the agent stack can
be exercised with no network and no key. It sat in `PRIMARY_PROVIDERS`, which
is also what the UI lists from — so **"Mock (Offline) (Ready)"** was a
selectable option in two real settings dropdowns, *Brain enrichment* and
*Default AI for new agents*. It reports itself ready, so it sorted in beside
providers that genuinely work.

The composer's picker never showed it, but only because `models.js` keeps a
second hand-curated list. That is the drift this pins: one exclusion,
`registry.HIDDEN_PROVIDERS`, read by every UI-facing listing.

It stays registered and stays in `MODEL_CATALOG` — an agent bound to `mock-1`
keeps working, and the whole test suite depends on that. What it loses is the
shop window.
"""
import pytest

from chitragupta.models.registry import (
    _REGISTRY,
    HIDDEN_PROVIDERS,
    MODEL_CATALOG,
    PRIMARY_PROVIDERS,
    VISIBLE_PROVIDERS,
    get_model_catalog,
    list_providers,
)


def test_the_mock_provider_is_hidden():
    assert "mock" in HIDDEN_PROVIDERS


def test_no_hidden_provider_is_offered_in_the_model_catalog():
    """`get_model_catalog()` feeds the enrichment picker and the per-agent
    binding screen."""
    ids = {p["id"] for p in get_model_catalog()}
    # Named as well as derived: an empty HIDDEN_PROVIDERS would make the
    # set-intersection form pass while the mock was back in the dropdown.
    assert "mock" not in ids, "Mock (Offline) is selectable as an enrichment model"
    assert not (ids & HIDDEN_PROVIDERS), f"a test fixture is selectable: {ids & HIDDEN_PROVIDERS}"


def test_no_hidden_provider_is_offered_in_the_provider_list():
    """`list_providers()` feeds *Default AI for new agents*."""
    ids = {p["id"] for p in list_providers() if "id" in p}
    assert "mock" not in ids, "Mock (Offline) is selectable as the default AI"
    assert not (ids & HIDDEN_PROVIDERS)


@pytest.mark.parametrize("name", sorted(HIDDEN_PROVIDERS))
def test_a_hidden_provider_still_works_for_everything_else(name):
    """Hidden is about the shop window, not about the plumbing. If this fails,
    hiding it broke the suite's own offline provider."""
    assert name in _REGISTRY, "the provider is no longer constructible"
    assert name in MODEL_CATALOG, "the provider lost its catalog entry"
    assert name in PRIMARY_PROVIDERS, "hiding is done by exclusion, not deletion"


def test_visible_is_derived_and_not_a_second_hand_written_list():
    """Two hand-maintained lists is how one of them ends up showing the mock."""
    assert [p for p in PRIMARY_PROVIDERS if p not in HIDDEN_PROVIDERS] == VISIBLE_PROVIDERS
    assert len(VISIBLE_PROVIDERS) == len(PRIMARY_PROVIDERS) - len(HIDDEN_PROVIDERS)


def test_the_real_providers_all_survived():
    """The exclusion must not take anything a user actually needs with it."""
    for real in ["claude", "cursor", "gemini", "xai", "openai", "deepseek", "ollama"]:
        assert real in VISIBLE_PROVIDERS, f"{real} was hidden by accident"
