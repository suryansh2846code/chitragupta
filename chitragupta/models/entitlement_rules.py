"""Whether a plan may run a model — the rules, with nothing plugged in.

Split out of `entitlements.py`, which does two jobs. One is *policy*: given a
provider, a model id, whether the account is connected and which plan it is on,
is this model available? That is a pure function of its arguments — it reads no
credential, probes no CLI and imports nothing from this package. The other job
is *detection*: go and find out what the user actually has, which necessarily
reaches into every provider module.

Keeping both in one file meant the policy could not be used without the
detection, and providers need the policy: `claude_code` and `chatgpt_auth` both
re-check a model before sending it — correctly, because a stored model id is a
request, not a fact. Those two imports ran upward from a provider into the
catalog and were load-bearing in a fourteen-module cycle that made `models/`
impossible to read a piece at a time.

So the rules live here, at the bottom, where anything may depend on them and
they depend on nothing. `entitlements` re-exports every name, so existing
imports read exactly as they did.

Everything below is a **conservative static fallback**. The authoritative
answer is always the provider's own, resolved per user — see
[`/CLAUDE.md`](../../CLAUDE.md) -> Models.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanTier:
    name: str
    level: int  # Higher number = higher capability / privileges


# Standard tier levels for providers
TIER_ANONYMOUS = PlanTier("Anonymous", 0)

# OpenAI / ChatGPT tiers
OPENAI_TIER_FREE = PlanTier("Free", 10)
OPENAI_TIER_PLUS = PlanTier("Plus", 20)
OPENAI_TIER_PRO = PlanTier("Pro", 30)
OPENAI_TIER_API = PlanTier("API Key", 40)

# Claude / Anthropic tiers
CLAUDE_TIER_FREE = PlanTier("Free", 10)
CLAUDE_TIER_PRO = PlanTier("Pro", 20)
CLAUDE_TIER_MAX = PlanTier("Max", 25)
CLAUDE_TIER_TEAM = PlanTier("Team", 30)
CLAUDE_TIER_ENTERPRISE = PlanTier("Enterprise", 40)

# Cursor tiers
CURSOR_TIER_FREE = PlanTier("Free", 10)
CURSOR_TIER_PRO = PlanTier("Pro", 20)
CURSOR_TIER_BUSINESS = PlanTier("Business", 30)

# Google Gemini tiers
GEMINI_TIER_FREE = PlanTier("Free", 10)
GEMINI_TIER_API = PlanTier("API Key / AI Studio", 20)

# xAI tiers
XAI_TIER_1 = PlanTier("Tier 1", 10)
XAI_TIER_2 = PlanTier("SuperGrok / Tier 2", 20)


def normalize_plan_tier(provider: str, plan_str: str | None) -> PlanTier:
    """Map any raw plan string or organizationType into a normalized PlanTier."""
    p = (plan_str or "").strip().lower()
    pid = provider.lower()

    if pid in ("openai", "chatgpt"):
        if any(k in p for k in ("pro", "team", "business", "enterprise", "edu", "self_serve")):
            return OPENAI_TIER_PRO
        if "plus" in p or "go" in p:
            return OPENAI_TIER_PLUS
        if "api" in p or "developer" in p:
            return OPENAI_TIER_API
        if "free" in p:
            return OPENAI_TIER_FREE
        return OPENAI_TIER_FREE if plan_str else TIER_ANONYMOUS

    if pid in ("claude", "anthropic", "claude-code"):
        if any(k in p for k in ("enterprise", "api")):
            return CLAUDE_TIER_ENTERPRISE
        if "team" in p:
            return CLAUDE_TIER_TEAM
        if "max" in p:
            return CLAUDE_TIER_MAX
        if any(k in p for k in ("pro", "subscription")):
            return CLAUDE_TIER_PRO
        if "free" in p:
            return CLAUDE_TIER_FREE
        return CLAUDE_TIER_FREE if plan_str else TIER_ANONYMOUS

    if pid == "cursor":
        if any(k in p for k in ("business", "enterprise")):
            return CURSOR_TIER_BUSINESS
        if "pro" in p:
            return CURSOR_TIER_PRO
        if "free" in p:
            return CURSOR_TIER_FREE
        return CURSOR_TIER_FREE if plan_str else TIER_ANONYMOUS

    if pid in ("gemini", "google"):
        if any(k in p for k in ("api", "ai studio", "developer", "vertex")):
            return GEMINI_TIER_API
        if p:
            return GEMINI_TIER_FREE
        return TIER_ANONYMOUS

    if pid in ("xai", "grok"):
        if any(k in p for k in ("tier 2", "supergrok", "tier2", "pro")):
            return XAI_TIER_2
        if p:
            return XAI_TIER_1
        return TIER_ANONYMOUS

    return PlanTier(plan_str or "Standard", 10) if plan_str else TIER_ANONYMOUS


# Required tier per model
_MODEL_TIER_REQUIREMENTS: dict[str, dict[str, PlanTier]] = {
    "openai": {
        "gpt-5.5": OPENAI_TIER_FREE,
        "gpt-5.4": OPENAI_TIER_FREE,
        "gpt-5.4-mini": OPENAI_TIER_FREE,
        "gpt-5.6-terra": OPENAI_TIER_FREE,
        "gpt-5.6-luna": OPENAI_TIER_FREE,
        "gpt-reserve": OPENAI_TIER_FREE,
        "codex-auto-review": OPENAI_TIER_FREE,
        "o3-mini": OPENAI_TIER_PLUS,
        "o1-mini": OPENAI_TIER_PLUS,
        "gpt-6-astra": OPENAI_TIER_PRO,
        "gpt-5.6-sol": OPENAI_TIER_PRO,
        "o1": OPENAI_TIER_PRO,
        "o3": OPENAI_TIER_PRO,
    },
    "claude": {
        "claude-haiku-4-5": CLAUDE_TIER_FREE,
        "claude-opus-5": CLAUDE_TIER_PRO,
        "claude-sonnet-5": CLAUDE_TIER_PRO,
        # Fable is a subscription model, not an org-plan one — the "Team /
        # Enterprise" gate previously here came from misreading the CLI's
        # `cc-update-required-1` entry, which is a CLI *version* requirement.
        "claude-fable-5": CLAUDE_TIER_PRO,
    },
    "claude-code": {
        "claude-code": CLAUDE_TIER_FREE,
        "claude-haiku-4-5": CLAUDE_TIER_FREE,
        "claude-opus-5": CLAUDE_TIER_PRO,
        "claude-sonnet-5": CLAUDE_TIER_PRO,
        "claude-fable-5": CLAUDE_TIER_PRO,
    },
    "cursor": {
        # A free Cursor plan can run ONLY `auto`; every named model is refused
        # by the CLI ("Named models unavailable").
        "auto": CURSOR_TIER_FREE,
    },
    "gemini": {
        "gemini-3.7-flash": GEMINI_TIER_FREE,
        "gemini-3.6-flash": GEMINI_TIER_FREE,
        "gemini-2.5-flash": GEMINI_TIER_FREE,
        "gemini-3.1-pro-preview": GEMINI_TIER_API,
        "gemini-2.5-pro": GEMINI_TIER_API,
    },
    "xai": {
        "grok-4.3": XAI_TIER_1,
        "grok-4.5": XAI_TIER_1,
        "grok-4.6": XAI_TIER_2,
    },
    "subscription": {
        "gpt-5.6-terra": PlanTier("Standard", 10),
        "claude-opus-5": PlanTier("Standard", 10),
        "claude-sonnet-5": PlanTier("Standard", 10),
        "claude-fable-5": PlanTier("Standard", 10),
    },
}


# Some providers gate by exception rather than by list: Cursor's free plan runs
# ONLY `auto`, so anything not named above needs Pro. Without this an unlisted
# model falls through to "no restriction" and is offered, then refused by the
# CLI.
_PROVIDER_DEFAULT_TIER: dict[str, PlanTier] = {
    "cursor": CURSOR_TIER_PRO,
}


def evaluate_model_entitlement(
    provider: str,
    model_id: str,
    is_connected: bool,
    user_plan: str | None = None,
    context: dict[str, Any] | None = None,
    provider_reported: tuple[bool, str | None] | None = None,
) -> tuple[bool, str | None]:
    """Decide whether THIS user, on THEIR plan, can run this model.

    Authority runs strictly in this order — the first answer wins:

      1. **Connection.** Nothing is usable until the user connects the provider.
      2. **The provider's own answer** (`provider_reported`): what the account
         itself says it can run — a live ``/v1/models`` query made with the
         user's credential, the Codex models cache, the Claude CLI's model
         options, the locally installed Ollama tags. This is the truth and it
         is never second-guessed by the tables below.
      3. **Static tier tables.** A conservative guess used ONLY when step 2 has
         nothing to say: the user is not connected yet, or discovery failed and
         a hardcoded fallback list is being shown.

    Passing `provider_reported` is what makes availability match the user's real
    plan on any machine, rather than whatever list was hardcoded at build time.

    Returns (locked, plan_required); `plan_required` explains a lock.
    """
    pid = provider.lower()
    mid = model_id.lower()
    ctx = context or {}

    # Gate 1: Provider must be connected in the Models section
    if not is_connected:
        return True, "Connect in Models"

    # Gate 2: Ollama installed model check
    if pid == "ollama":
        installed_names = {str(x).lower() for x in (ctx.get("installed_models") or set())}
        base = mid.split(":")[0]
        is_installed = (
            mid in installed_names
            or base in installed_names
            or any(inst.split(":")[0] == base for inst in installed_names)
        )
        if not is_installed:
            return True, "Pull required"
        return False, None

    # Gate 3: Explicit session disablement flags (e.g. CLI update requirement in ~/.claude.json)
    disabled_models = ctx.get("disabled_models") or {}
    for d_pattern, reason in disabled_models.items():
        if d_pattern.lower() in mid:
            return True, reason or "Update Required"

    # Gate 4: the account's own answer. A model the provider handed us for this
    # user's credential is available to this user, whatever our tables guess.
    if provider_reported is not None:
        reported_locked, reported_plan = provider_reported
        if reported_locked:
            return True, reported_plan or "Not available on your plan"
        return False, None

    # Gate 5: static tier requirements — a fallback for models we are listing
    # without having asked the provider (not connected, or discovery failed).
    prov_reqs = _MODEL_TIER_REQUIREMENTS.get(pid, {})
    # Exact match or normalized slug match
    req_tier = prov_reqs.get(model_id) or prov_reqs.get(mid)
    if not req_tier:
        # Check substring match
        for m_key, tier in prov_reqs.items():
            if m_key in mid:
                req_tier = tier
                break

    if not req_tier:
        req_tier = _PROVIDER_DEFAULT_TIER.get(pid)
    if not req_tier:
        # Model has no special tier restriction -> available once connected
        return False, None

    user_tier = normalize_plan_tier(pid, user_plan)

    if user_tier.level >= req_tier.level:
        return False, None

    return True, req_tier.name


def get_best_unlocked_model(
    provider: str,
    available_models: list[str],
    is_connected: bool,
    user_plan: str | None = None,
    context: dict[str, Any] | None = None,
) -> str | None:
    """Find the highest-priority model that is currently unlocked for the user."""
    if not is_connected or not available_models:
        return None

    pid = provider.lower()
    unlocked = [
        m for m in available_models
        if not evaluate_model_entitlement(pid, m, is_connected, user_plan, context)[0]
    ]
    if not unlocked:
        return None

    # Priority preferences when choosing automatic default
    priority_order = [
        "claude-opus-5", "claude-sonnet-5", "claude-fable-5",
        "gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna",
        "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.1-pro-preview",
        "gemini-2.5-flash", "gemini-2.5-pro",
        "auto",
        "grok-4.6", "grok-4.5", "grok-4.3",
        "deepseek-chat", "deepseek-reasoner",
        "llama3.2", "qwen2.5",
    ]

    for pref in priority_order:
        for m in unlocked:
            if pref in m.lower():
                return m

    return unlocked[0]
