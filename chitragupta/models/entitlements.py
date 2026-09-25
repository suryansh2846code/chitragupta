"""Provider-Reported & Dynamic Model Entitlement Engine for TURNOVER / Chitragupta.

Determines model accessibility dynamically based on:
1. Provider Live Discovery & Capability Reporting (authoritative source of truth).
2. Verified Account Access & Scopes (runtime identity, session validity, and actual API capabilities).
3. Provider-Reported Constraints (e.g. CLI update prerequisites reported by installed tools).
4. Conservative Static Fallback Mappings (safe fallback tier metadata when provider APIs do not expose real-time entitlement endpoints).

**The rules themselves are not here.** Deciding whether a plan may run a model
is a pure function of its arguments and lives in `entitlement_rules.py`; this
module is the half that goes and *finds out* what the user has, which reaches
into every provider. Providers that re-check a model before sending it import
the rules directly — otherwise a provider imports the catalog, which is how all
of `models/` became one fourteen-module knot.

Every rules name is re-exported below, so `from .entitlements import
evaluate_model_entitlement` reads exactly as it always did.
"""
from __future__ import annotations

from .connection_state import (
    is_provider_connected,
    provider_credentials,
)
from .entitlement_rules import (
    CLAUDE_TIER_ENTERPRISE,
    CLAUDE_TIER_FREE,
    CLAUDE_TIER_MAX,
    CLAUDE_TIER_PRO,
    CLAUDE_TIER_TEAM,
    CURSOR_TIER_BUSINESS,
    CURSOR_TIER_FREE,
    CURSOR_TIER_PRO,
    GEMINI_TIER_API,
    GEMINI_TIER_FREE,
    OPENAI_TIER_API,
    OPENAI_TIER_FREE,
    OPENAI_TIER_PLUS,
    OPENAI_TIER_PRO,
    TIER_ANONYMOUS,
    XAI_TIER_1,
    XAI_TIER_2,
    PlanTier,
    evaluate_model_entitlement,
    get_best_unlocked_model,
    normalize_plan_tier,
)

#: Declared so the re-exports above are the module's stated surface rather
#: than imports that happen to sit there. `entitlement_rules` and
#: `connection_state` are where these now live; this keeps every existing
#: `from .entitlements import ...` reading exactly as it did.
__all__ = [
    "CLAUDE_TIER_ENTERPRISE",
    "CLAUDE_TIER_FREE",
    "CLAUDE_TIER_MAX",
    "CLAUDE_TIER_PRO",
    "CLAUDE_TIER_TEAM",
    "CURSOR_TIER_BUSINESS",
    "CURSOR_TIER_FREE",
    "CURSOR_TIER_PRO",
    "GEMINI_TIER_API",
    "GEMINI_TIER_FREE",
    "OPENAI_TIER_API",
    "OPENAI_TIER_FREE",
    "OPENAI_TIER_PLUS",
    "OPENAI_TIER_PRO",
    "TIER_ANONYMOUS",
    "XAI_TIER_1",
    "XAI_TIER_2",
    "PlanTier",
    "evaluate_model_entitlement",
    "get_best_unlocked_model",
    "is_provider_connected",
    "normalize_plan_tier",
    "provider_credentials",
    "resolve_usable_model",
]

_ACCEPTS_ANY_MODEL = {"mock"}


def _best_for_account(provider_id: str) -> str | None:
    """The strongest model this account is actually offered, or None.

    None is still the right answer when we cannot tell — a discovery failure or
    a hardcoded fallback list is not evidence about this account, and guessing
    from one would override a model the user can legitimately run.
    """
    try:
        from .discovery import get_discovered_models
        offered, _ = get_discovered_models(provider_id)
    except Exception:
        return None
    if not offered or any(m.get("is_fallback") for m in offered):
        return None
    unlocked = [m["id"] for m in offered if not m.get("locked")]
    if not unlocked:
        return None
    return get_best_unlocked_model(
        provider=provider_id, available_models=unlocked,
        is_connected=True, user_plan=None) or unlocked[0]


def resolve_usable_model(provider_id: str, model: str | None) -> tuple[str | None, str | None]:
    """Map a *requested* model onto one this user can actually run right now.

    A model id is chosen once and then persisted — in an agent's binding, in
    localStorage — but the provider's catalog moves underneath it. Sending a
    retired or plan-locked id straight through produces a provider 400 on the
    user's next message, so every request is re-checked against what the
    account currently offers.

    Returns (usable_model, replaced) where `replaced` is the original id when a
    substitution happened, else None. A `None` model means "let the provider
    pick its own default".
    """
    if provider_id.lower() in _ACCEPTS_ANY_MODEL:
        return (model or None), None

    if not model:
        # "Auto" has to mean "the best model this account can actually run",
        # not "whatever id is hardcoded as the provider's default". Returning
        # None here deferred to `registry.default_model`, which for OpenAI is
        # `gpt-5.6-terra` — a model a ChatGPT Free account cannot run. The user
        # then got "requires Pro" about a model they never chose, from the one
        # setting that is supposed to be the safe choice.
        return _best_for_account(provider_id), None

    try:
        from .discovery import get_discovered_models
        offered, _ = get_discovered_models(provider_id)
    except Exception:
        # Never block a turn on a discovery failure — honour what was asked.
        return model, None

    if not offered:
        return model, None

    by_id = {m["id"]: m for m in offered}
    match = by_id.get(model)
    if match is not None and not match.get("locked"):
        return model, None

    if match is None and any(m.get("is_fallback") for m in offered):
        # We are showing a hardcoded list, not the account's real catalog, so an
        # id being absent from it proves nothing. Substituting here would swap a
        # model the user can legitimately run.
        return model, None

    # Only ever choose among models discovery already marked unlocked. That
    # flag was computed against what this account reports it can run
    # (`discovery.py`: `locked = not is_supported`), and it is the only place
    # the user's plan is actually known.
    #
    # This used to hand `get_best_unlocked_model` the *whole* list with
    # `user_plan=None`, which re-derived entitlement with no plan to check
    # against — so every model looked unlocked and the repair returned the
    # highest-priority one. For a ChatGPT Free account that is `gpt-5.6-terra`:
    # the function whose entire job is to replace a plan-locked model handed
    # back a plan-locked model, and the user got "requires Pro" on a model they
    # never chose.
    unlocked = [m["id"] for m in offered if not m.get("locked")]
    substitute = get_best_unlocked_model(
        provider=provider_id,
        available_models=unlocked,
        is_connected=True,
        user_plan=None,
    ) or (unlocked[0] if unlocked else None)

    if substitute and substitute != model:
        return substitute, model
    if substitute:
        return substitute, None
    # Nothing is usable — fall back to the provider's own default rather than
    # sending an id we know the provider will reject.
    return None, model
