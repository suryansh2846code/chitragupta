"""Dynamic model discovery and capability detection for TURNOVER / Chitragupta.

Queries official provider endpoints to retrieve actual available models,
capabilities (tools, vision, reasoning, structured output), and context windows.
Eliminates stale hardcoded model IDs and guarantees live catalog truth.
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from ..log import suppressed
from . import errors
from .base import _saved_key

_CACHE_TTL = 3600  # 1 hour cache unless refreshed

# pid -> (fetched_at, raw discovered models, discovery metadata).
# Holds the *unentitled* discovery result only; lock state is never cached.
_MODEL_CACHE: dict[str, tuple[float, list[dict[str, Any]], dict[str, Any]]] = {}

_PROVIDER_ALIASES = {"anthropic": "claude", "google": "gemini", "grok": "xai"}


def normalize_provider_id(provider_id: str) -> str:
    """Collapse provider aliases onto their canonical id."""
    pid = (provider_id or "").lower().strip()
    return _PROVIDER_ALIASES.get(pid, pid)


def clear_model_cache(provider_id: str | None = None) -> None:
    """Drop cached discovery results so the next read re-queries the provider.

    Call this whenever credentials change (sign-in, disconnect, key saved) — the
    entitlement pass already runs live, but a freshly connected provider should
    also re-discover the model list its account actually has access to.
    """
    if provider_id is None:
        _MODEL_CACHE.clear()
    else:
        _MODEL_CACHE.pop(normalize_provider_id(provider_id), None)


@dataclass
class DiscoveredModel:
    id: str
    name: str
    desc: str
    context_window: int | None = None
    context_window_approximate: bool = False
    tool_calling: bool = True
    structured_output: bool = True
    streaming: bool = True
    vision: bool = False
    reasoning: bool = False
    status: str = "available"
    locked: bool = False
    plan_required: str | None = None
    is_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _detect_capabilities(model_id: str, desc: str = "") -> dict[str, Any]:
    mid = model_id.lower()
    d = desc.lower()

    # Reasoning models
    is_reasoning = any(k in mid for k in ("o1", "o3", "reasoner", "r1", "thinking", "terra", "luna", "sol", "astra", "gpt-5", "gpt-6", "opus", "fable", "sonnet-5")) or "reasoning" in d
    if "3-7-sonnet" in mid or "opus" in mid or "fable" in mid:
        is_reasoning = True

    # Vision models
    is_vision = any(k in mid for k in ("vision", "4o", "gemini", "claude-3", "claude-", "vl", "pixtral", "gpt-5", "gpt-6", "terra", "luna", "sol", "astra", "opus", "sonnet", "fable")) or "vision" in d

    # Tool calling support
    # (Almost all current flagship models support tools; legacy/completion models don't)
    no_tools = any(k in mid for k in ("instruct-preview", "base", "embed", "tts", "whisper", "dall-e"))
    tool_calling = not no_tools

    # Context window heuristic fallback (marked internally as approximate)
    context_window = 128_000
    if "gemini" in mid:
        context_window = 1_000_000 if "1.5" in mid or "2.5" in mid else 128_000
    elif any(k in mid for k in ("claude", "sonnet", "haiku", "opus", "fable", "mythos")):
        context_window = 200_000
    elif any(k in mid for k in ("gpt-5", "gpt-6", "terra", "luna", "sol", "astra")):
        context_window = 272_000
    elif "gpt-4o" in mid or "gpt-4-turbo" in mid:
        context_window = 128_000
    elif any(k in mid for k in ("o1", "o3")):
        context_window = 200_000
    elif "grok" in mid:
        context_window = 131_072
    elif "deepseek" in mid:
        context_window = 64_000

    return {
        "reasoning": is_reasoning,
        "vision": is_vision,
        "tool_calling": tool_calling,
        "context_window": context_window,
        "context_window_approximate": True,
        "structured_output": True,
        "streaming": True,
        "status": "available",
    }


def _chatgpt_subscription_models() -> list[DiscoveredModel]:
    """Retrieve models available through ChatGPT Subscription / Codex, locking unsupported models."""
    from .chatgpt_auth import resolve_subscription_models

    try:
        supported_slugs, slug_meta = resolve_subscription_models()[:2]
    except Exception:
        supported_slugs, slug_meta = set(), {}
    custom_descs = {
        slug: (m["name"], m["desc"], m["context_window"]) for slug, m in slug_meta.items()
    }

    # Full catalog of Codex / ChatGPT models with standard tier requirements
    catalog_specs = [
        ("gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work", 272_000, True, True, None),
        ("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model", 272_000, True, True, None),
        ("gpt-5.6-sol", "GPT-5.6-Sol", "Flagship agentic coding model for complex tasks", 272_000, True, True, "Pro"),
        ("gpt-6-astra", "GPT-6-Astra", "Our most capable model for complex, demanding work", 272_000, True, True, "Pro"),
        ("gpt-reserve", "GPT-Reserve", "Fast and affordable backup agentic coding model", 272_000, True, True, None),
        ("o3-mini", "o3-mini", "High-speed STEM and code reasoning", 200_000, False, True, "Plus"),
        ("gpt-5.5", "GPT-5.5", "Proven previous-generation coding and general model", 272_000, True, True, None),
    ]

    models: list[DiscoveredModel] = []
    seen: set[str] = set()

    for slug, d_name, d_desc, ctx, vision, reasoning, default_req in catalog_specs:
        seen.add(slug)
        if slug in custom_descs:
            c_name, c_desc, c_ctx = custom_descs[slug]
            d_name = c_name or d_name
            d_desc = c_desc or d_desc
            ctx = c_ctx or ctx

        is_supported = slug in supported_slugs if supported_slugs else default_req is None

        locked = not is_supported
        plan_req = None if is_supported else (default_req or "Pro")
        models.append(DiscoveredModel(
            id=slug,
            name=d_name,
            desc=d_desc,
            context_window=ctx,
            vision=vision,
            reasoning=reasoning,
            locked=locked,
            plan_required=plan_req,
            status="available" if is_supported else "locked",
        ))

    # Add any extra models found in cache not in standard catalog
    for slug in supported_slugs:
        if slug not in seen and not slug.startswith("codex-auto"):
            c_name, c_desc, c_ctx = custom_descs.get(slug, (slug.replace("-", " ").title(), "Agentic coding model", 272_000))
            models.append(DiscoveredModel(
                id=slug,
                name=c_name,
                desc=c_desc,
                context_window=c_ctx,
                vision=True,
                reasoning=True,
                locked=False,
                plan_required=None,
                status="available",
            ))

    return models


def discover_openai_models(api_key: str | None = None) -> tuple[list[DiscoveredModel], dict[str, Any]]:
    key = api_key or os.environ.get("OPENAI_API_KEY") or _saved_key("OPENAI_API_KEY")
    account_info: dict[str, Any] = {}

    # Check if ChatGPT Subscription is active
    with suppressed("from .chatgpt_auth import get_chatgpt_access_token, detect_chatg …"):
        from .chatgpt_auth import detect_chatgpt_local_session, get_chatgpt_access_token
        sess = detect_chatgpt_local_session(fetch_usage=False)
        has_sub = bool(get_chatgpt_access_token() or (sess and sess.get("has_token")))
        if sess and sess.get("email"):
            account_info["email"] = sess.get("email")
            account_info["name"] = sess.get("name")
            account_info["plan"] = sess.get("plan")
        if not key and has_sub:
            return _chatgpt_subscription_models(), account_info

    if not key:
        return _fallback_openai(), account_info

    headers = {"Authorization": f"Bearer {key}"}

    # 1. Fetch account identity from /v1/me if permitted
    with suppressed("me_resp = httpx.get('https://api.openai.com/v1/me', headers=head …"):
        me_resp = httpx.get("https://api.openai.com/v1/me", headers=headers, timeout=4.0)
        if me_resp.status_code == 200:
            me_data = me_resp.json()
            account_info["email"] = me_data.get("email")
            account_info["account_id"] = me_data.get("id")
            account_info["name"] = me_data.get("name")
            orgs = me_data.get("orgs", {}).get("data", [])
            if orgs:
                account_info["organization"] = orgs[0].get("name") or orgs[0].get("id")

    # 2. Discover models from OpenAI API
    try:
        resp = httpx.get("https://api.openai.com/v1/models", headers=headers, timeout=6.0)
        if resp.status_code != 200:
            return _fallback_openai(), account_info

        data = resp.json().get("data", [])
        models: list[DiscoveredModel] = []
        # Filter for chat / reasoning models
        chat_prefixes = ("gpt-5", "gpt-6", "gpt-4o", "gpt-4", "o1", "o3", "chatgpt")
        for m in sorted(data, key=lambda x: x.get("created", 0), reverse=True):
            mid = m.get("id", "")
            if not any(mid.startswith(p) for p in chat_prefixes):
                continue
            if any(x in mid for x in ("audio", "realtime", "transcription", "tts", "moderation", "embedding")):
                continue

            caps = _detect_capabilities(mid)
            models.append(DiscoveredModel(
                id=mid,
                name=mid.replace("-latest", "").replace("-", " ").title(),
                desc=f"Official OpenAI model ({caps['context_window'] // 1000}k context)",
                **caps,
            ))

        return models[:16] if models else _fallback_openai(), account_info
    except Exception:
        return _fallback_openai(), account_info


def discover_anthropic_models(api_key: str | None = None) -> list[DiscoveredModel]:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or _saved_key("ANTHROPIC_API_KEY")
    if not key:
        return _fallback_anthropic()

    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    try:
        resp = httpx.get("https://api.anthropic.com/v1/models", headers=headers, timeout=6.0)
        if resp.status_code != 200:
            return _fallback_anthropic()

        data = resp.json().get("data", [])
        models: list[DiscoveredModel] = []
        seen = set()
        for m in data:
            mid = m.get("id", "")
            if not any(k in mid for k in ("claude", "opus", "sonnet", "haiku", "fable", "mythos", "3-7", "3-5")):
                continue
            seen.add(mid)
            display_name = m.get("display_name") or mid.replace("-", " ").title()
            caps = _detect_capabilities(mid)
            locked = "fable" in mid.lower()
            plan_req = "Team / Enterprise (v2.1.255+)" if locked else None
            models.append(DiscoveredModel(
                id=mid,
                name=display_name,
                desc=f"Official Anthropic model ({caps['context_window'] // 1000}k context)",
                locked=locked,
                plan_required=plan_req,
                **caps,
            ))
        # Merge flagship models if not present in API listing
        for fb in _fallback_anthropic():
            if fb.id not in seen:
                models.append(fb)
        return models if models else _fallback_anthropic()
    except Exception:
        return _fallback_anthropic()


def discover_gemini_models(api_key: str | None = None) -> list[DiscoveredModel]:
    from .gemini import resolve_gemini_credentials
    cred = resolve_gemini_credentials(api_key=api_key)
    if not cred.valid or not cred.secret:
        return _fallback_gemini()

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={cred.secret}"
        resp = httpx.get(url, timeout=6.0)
        if resp.status_code != 200:
            return _fallback_gemini()

        items = resp.json().get("models", [])
        models: list[DiscoveredModel] = []
        for m in items:
            name = m.get("name", "").replace("models/", "")
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue
            if "embedding" in name or "aqa" in name:
                continue
            if not any(k in name for k in ("2.5", "2.0")):
                continue
            display_name = m.get("displayName") or name.replace("-", " ").title()
            caps = _detect_capabilities(name, m.get("description", ""))
            input_limit = m.get("inputTokenLimit")
            if input_limit:
                caps["context_window"] = input_limit
            models.append(DiscoveredModel(
                id=name,
                name=display_name,
                desc=m.get("description") or "Google Gemini model",
                locked=False,
                **caps,
            ))
        return models if models else _fallback_gemini()
    except Exception:
        return _fallback_gemini()


def discover_xai_models(api_key: str | None = None) -> list[DiscoveredModel]:
    key = api_key or os.environ.get("XAI_API_KEY") or _saved_key("XAI_API_KEY")
    if not key:
        # No key: a subscription runs through xAI's CLI, and `grok models`
        # reports exactly what this account can use.
        from .grok_cli import grok_cli_models

        try:
            cli_models = grok_cli_models()
        except Exception:
            cli_models = []
        if cli_models:
            return [DiscoveredModel(
                m, m.replace("-", " ").title(),
                "Runs on your Grok subscription via the Grok CLI",
                256_000, vision=True, reasoning=True,
            ) for m in cli_models]
        return _fallback_xai()

    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = httpx.get("https://api.x.ai/v1/models", headers=headers, timeout=6.0)
        if resp.status_code != 200:
            return _fallback_xai()

        items = resp.json().get("data", [])
        models: list[DiscoveredModel] = []
        for m in items:
            mid = m.get("id", "")
            caps = _detect_capabilities(mid)
            models.append(DiscoveredModel(
                id=mid,
                name=mid.replace("-", " ").title(),
                desc=f"xAI Grok live model ({caps['context_window'] // 1000}k context)",
                **caps,
            ))
        return models if models else _fallback_xai()
    except Exception:
        return _fallback_xai()


def discover_deepseek_models(api_key: str | None = None) -> list[DiscoveredModel]:
    key = api_key or os.environ.get("DEEPSEEK_API_KEY") or _saved_key("DEEPSEEK_API_KEY")
    if not key:
        return _fallback_deepseek()

    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = httpx.get("https://api.deepseek.com/models", headers=headers, timeout=6.0)
        if resp.status_code != 200:
            return _fallback_deepseek()

        items = resp.json().get("data", [])
        models: list[DiscoveredModel] = []
        for m in items:
            mid = m.get("id", "")
            caps = _detect_capabilities(mid)
            models.append(DiscoveredModel(
                id=mid,
                name="DeepSeek " + mid.replace("deepseek-", "").title(),
                desc=f"Official DeepSeek live model ({caps['context_window'] // 1000}k context)",
                **caps,
            ))
        return models if models else _fallback_deepseek()
    except Exception:
        return _fallback_deepseek()


def discover_openrouter_models(api_key: str | None = None) -> list[DiscoveredModel]:
    key = api_key or os.environ.get("OPENROUTER_API_KEY") or _saved_key("OPENROUTER_API_KEY")
    if not key:
        return _fallback_openrouter()
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = httpx.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=6.0)
        if resp.status_code != 200:
            return _fallback_openrouter()

        items = resp.json().get("data", [])
        models: list[DiscoveredModel] = []
        for m in items:
            mid = m.get("id", "")
            if ":batch" in mid or ":free" in mid:
                continue
            # Filter to only latest generation models
            is_latest = any(k in mid.lower() for k in (
                "claude-3.7", "claude-3-7", "claude-3.5",
                "gpt-5", "gpt-6", "o3",
                "deepseek-r1", "deepseek-v3", "deepseek-chat",
                "llama-3.3", "qwen-2.5", "grok-4"
            ))
            if not is_latest:
                continue
            name = m.get("name") or mid
            ctx = m.get("context_length") or 128_000
            caps = _detect_capabilities(mid, m.get("description", ""))
            caps["context_window"] = ctx
            models.append(DiscoveredModel(
                id=mid,
                name=name,
                desc=m.get("description") or f"OpenRouter model ({ctx // 1000}k context)",
                **caps,
            ))
            if len(models) >= 20:
                break
        return models if models else _fallback_openrouter()
    except Exception:
        return _fallback_openrouter()


def discover_ollama_models(host: str | None = None) -> list[DiscoveredModel]:
    url = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    installed_names: set[str] = set()
    installed_models: list[DiscoveredModel] = []
    with suppressed("resp = httpx.get(f'{url}/api/tags', timeout=3.0) …"):
        resp = httpx.get(f"{url}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            items = resp.json().get("models", [])
            for m in items:
                name = m.get("name", "")
                if name:
                    installed_names.add(name)
                    installed_names.add(name.split(":")[0])
                    size_gb = round(m.get("size", 0) / (1024 ** 3), 1)
                    caps = _detect_capabilities(name)
                    caps.pop("status", None)
                    installed_models.append(DiscoveredModel(
                        id=name,
                        name=name,
                        desc=f"Local Ollama model ({size_gb} GB, installed)",
                        locked=False,
                        status="available",
                        **caps,
                    ))

    catalog_ollama = [
        ("llama3.2", "Llama 3.2", "Compact offline local model", 128_000),
        ("qwen2.5:3b", "Qwen 2.5 (3B)", "Compact fast local model", 32_000),
        ("llama3.3:70b", "Llama 3.3 (70B)", "Latest flagship open weights model", 128_000),
        ("qwen2.5-coder:7b", "Qwen 2.5 Coder (7B)", "Strong multilingual local model", 32_000),
        ("deepseek-r1:8b", "DeepSeek R1 (8B)", "Local reasoning model", 64_000),
    ]

    models: list[DiscoveredModel] = list(installed_models)
    seen = {m.id for m in models}

    for mid, name, desc, ctx in catalog_ollama:
        base = mid.split(":")[0]
        is_installed = mid in installed_names or base in installed_names
        if mid not in seen and not any(m.id.startswith(base) for m in models):
            caps = _detect_capabilities(mid)
            caps.pop("status", None)
            caps["context_window"] = ctx
            models.append(DiscoveredModel(
                id=mid,
                name=name,
                desc=desc,
                locked=not is_installed,
                plan_required="Pull required" if not is_installed else None,
                status="available" if is_installed else "locked",
                **caps,
            ))

    return models if models else _fallback_ollama()


# ── Fallback static definitions when offline or unconfigured ─────────────────

def _mark_fallback(models: list[DiscoveredModel]) -> list[DiscoveredModel]:
    for m in models:
        m.is_fallback = True
        m.context_window_approximate = True
    return models


def _fallback_openai() -> list[DiscoveredModel]:
    is_free = True
    with suppressed("from .chatgpt_auth import detect_chatgpt_local_session …"):
        from .chatgpt_auth import detect_chatgpt_local_session
        sess = detect_chatgpt_local_session(fetch_usage=False)
        plan = (sess and sess.get("plan", "")) or ""
        if any(k in plan.lower() for k in ("pro", "team", "business", "enterprise")):
            is_free = False

    return _mark_fallback([
        DiscoveredModel("gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work", 272_000, vision=True, reasoning=True),
        DiscoveredModel("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model", 272_000, vision=True, reasoning=True),
        DiscoveredModel("gpt-5.6-sol", "GPT-5.6-Sol", "Flagship agentic coding model for complex tasks", 272_000, vision=True, reasoning=True, locked=is_free, plan_required="Pro"),
        DiscoveredModel("gpt-6-astra", "GPT-6-Astra", "Our most capable model for complex, demanding work", 272_000, vision=True, reasoning=True, locked=is_free, plan_required="Pro"),
        DiscoveredModel("gpt-reserve", "GPT-Reserve", "Fast and affordable backup agentic coding model", 272_000, vision=True, reasoning=True),
        DiscoveredModel("o3-mini", "o3-mini", "High-speed STEM and code reasoning", 200_000, reasoning=True, locked=is_free, plan_required="Plus"),
        DiscoveredModel("gpt-5.5", "GPT-5.5", "Proven previous-generation coding model", 272_000, vision=True, reasoning=True),
        DiscoveredModel("gpt-5.4", "GPT-5.4", "Earlier-generation coding model", 272_000, vision=True, reasoning=True),
        DiscoveredModel("gpt-5.4-mini", "GPT-5.4-Mini", "Compact, fast earlier-generation model", 272_000, vision=True),
    ])


def _fallback_anthropic() -> list[DiscoveredModel]:
    is_free = True
    with suppressed("from .accounts import detect_claude_account …"):
        from .accounts import detect_claude_account
        acct = detect_claude_account()
        plan = acct.get("plan", "").lower()
        if "pro" in plan or "subscription" in plan or "team" in plan:
            is_free = False

    return _mark_fallback([
        DiscoveredModel("claude-opus-5", "Claude Opus 5", "Frontier intelligence, deep synthesis & complex architecture", 200_000, vision=True, reasoning=True, locked=is_free, plan_required="Pro" if is_free else None),
        DiscoveredModel("claude-sonnet-5", "Claude Sonnet 5", "Flagship agentic coding & reasoning workhorse", 200_000, vision=True, reasoning=True, locked=is_free, plan_required="Pro" if is_free else None),
        DiscoveredModel("claude-fable-5", "Claude Fable 5", "Most capable for the hardest, longest-running work", 200_000, vision=True, reasoning=True, locked=is_free, plan_required="Pro" if is_free else None),
        DiscoveredModel("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "Fast & responsive everyday model", 200_000, vision=True, locked=False),
    ])


def _fallback_gemini() -> list[DiscoveredModel]:
    from .gemini import resolve_gemini_credentials
    cred = resolve_gemini_credentials()
    is_ready = cred.valid

    return _mark_fallback([
        DiscoveredModel("gemini-3.7-flash", "Gemini 3.7 Flash", "Latest fast reasoning & multimodal model", 1_000_000, vision=True, reasoning=True, locked=not is_ready, plan_required="API Key / AI Studio" if not is_ready else None),
        DiscoveredModel("gemini-3.6-flash", "Gemini 3.6 Flash", "Fast reasoning & multimodal", 1_000_000, vision=True, reasoning=True, locked=not is_ready, plan_required="API Key / AI Studio" if not is_ready else None),
        DiscoveredModel("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "Deep reasoning across code & complex tasks", 1_000_000, vision=True, reasoning=True, locked=not is_ready, plan_required="API Key / AI Studio" if not is_ready else None),
        DiscoveredModel("gemini-2.5-pro", "Gemini 2.5 Pro", "Previous-generation deep reasoning", 1_000_000, vision=True, reasoning=True, locked=not is_ready, plan_required="API Key / AI Studio" if not is_ready else None),
        DiscoveredModel("gemini-2.5-flash", "Gemini 2.5 Flash", "Previous-generation speed & multimodal", 1_000_000, vision=True, locked=not is_ready, plan_required="API Key / AI Studio" if not is_ready else None),
    ])


def _fallback_xai() -> list[DiscoveredModel]:
    has_key = bool(os.environ.get("XAI_API_KEY") or _saved_key("XAI_API_KEY"))
    return _mark_fallback([
        DiscoveredModel("grok-4.6", "Grok 4.6", "Latest flagship reasoning model", 256_000, vision=True, reasoning=True, locked=not has_key, plan_required="Connect xAI" if not has_key else None),
        DiscoveredModel("grok-4.5", "Grok 4.5", "Strong reasoning & tool calling", 256_000, vision=True, reasoning=True, locked=not has_key, plan_required="Connect xAI" if not has_key else None),
        DiscoveredModel("grok-4.3", "Grok 4.3", "Fast general-purpose reasoning", 256_000, reasoning=True, locked=not has_key, plan_required="Connect xAI" if not has_key else None),
    ])


def _fallback_deepseek() -> list[DiscoveredModel]:
    return _mark_fallback([
        DiscoveredModel("deepseek-chat", "DeepSeek Chat", "Current chat model (alias — always the latest)", 128_000, locked=False),
        DiscoveredModel("deepseek-reasoner", "DeepSeek Reasoner", "Current reasoning model (alias — always the latest)", 128_000, reasoning=True, locked=False),
    ])


def _fallback_openrouter() -> list[DiscoveredModel]:
    return _mark_fallback([
        DiscoveredModel("anthropic/claude-sonnet-5", "Claude Sonnet 5", "Via OpenRouter gateway", 200_000, vision=True, reasoning=True, locked=False),
        DiscoveredModel("anthropic/claude-opus-5", "Claude Opus 5", "Via OpenRouter gateway", 200_000, vision=True, reasoning=True, locked=False),
        DiscoveredModel("openai/gpt-5.6-terra", "GPT-5.6-Terra", "Via OpenRouter gateway", 272_000, vision=True, reasoning=True, locked=False),
        DiscoveredModel("deepseek/deepseek-r1", "DeepSeek R1", "Via OpenRouter gateway", 64_000, reasoning=True, locked=False),
        DiscoveredModel("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B", "Via OpenRouter gateway", 128_000, locked=False),
    ])


def _fallback_ollama() -> list[DiscoveredModel]:
    return _mark_fallback([
        DiscoveredModel("llama3.2", "Llama 3.2", "Compact offline local model", 128_000, locked=False),
        DiscoveredModel("qwen2.5:3b", "Qwen 2.5 (3B)", "Compact fast local model", 32_000, locked=False),
        DiscoveredModel("llama3.3:70b", "Llama 3.3 (70B)", "Latest flagship open weights model", 128_000, locked=True, plan_required="Pull required"),
        DiscoveredModel("qwen2.5-coder:7b", "Qwen 2.5 Coder (7B)", "Strong multilingual local model", 32_000, locked=True, plan_required="Pull required"),
        DiscoveredModel("deepseek-r1:8b", "DeepSeek R1 (8B)", "Local reasoning model", 64_000, reasoning=True, locked=True, plan_required="Pull required"),
    ])


def _fallback_cursor() -> list[DiscoveredModel]:
    is_free = True
    with suppressed("from .accounts import detect_cursor_account …"):
        from .accounts import detect_cursor_account
        acct = detect_cursor_account()
        plan = acct.get("plan", "").lower()
        if "pro" in plan or "business" in plan or "enterprise" in plan:
            is_free = False

    # Ids verified against `agent --list-models` on 2026-09-12. Shown only
    # before the CLI exists; its own list replaces these once installed.
    return _mark_fallback([
        DiscoveredModel("auto", "Auto", "Let Cursor pick the best model", 200_000, vision=True, reasoning=True),
        DiscoveredModel("claude-opus-5-high", "Claude Opus 5", "Frontier reasoning via Cursor", 1_000_000, vision=True, reasoning=True, locked=is_free, plan_required="Cursor Pro" if is_free else None),
        DiscoveredModel("claude-sonnet-5-thinking-high", "Claude Sonnet 5 Thinking", "Balanced agentic coding via Cursor", 1_000_000, vision=True, reasoning=True, locked=is_free, plan_required="Cursor Pro" if is_free else None),
        DiscoveredModel("gpt-5.3-codex", "Codex 5.3", "OpenAI Codex via Cursor", 272_000, vision=True, reasoning=True, locked=is_free, plan_required="Cursor Pro" if is_free else None),
        DiscoveredModel("composer-2.5", "Composer 2.5", "Cursor's own fast model", 200_000, reasoning=True, locked=is_free, plan_required="Cursor Pro" if is_free else None),
    ])


def _discover_raw(pid: str, api_key: str | None) -> tuple[list[DiscoveredModel], dict[str, Any]]:
    """Run the provider's live model discovery. Network-bound, so this is the
    only part that gets cached — entitlement is always evaluated fresh."""
    discovery_meta: dict[str, Any] = {}

    if pid == "openai":
        models, meta = discover_openai_models(api_key)
        discovery_meta.update(meta)
        return models, discovery_meta
    if pid == "claude":
        return discover_anthropic_models(api_key), discovery_meta
    if pid == "gemini":
        return discover_gemini_models(api_key), discovery_meta
    if pid == "xai":
        return discover_xai_models(api_key), discovery_meta
    if pid == "deepseek":
        return discover_deepseek_models(api_key), discovery_meta
    if pid == "openrouter":
        return discover_openrouter_models(api_key), discovery_meta
    if pid == "ollama":
        return discover_ollama_models(), discovery_meta
    if pid == "cursor":
        # Cursor has no models API — its CLI is the only source, and that list
        # is the account's real one. A free plan can run ONLY `auto`; the CLI
        # refuses every named model, so listing them selectable would be a
        # control that cannot work.
        from .accounts import detect_cursor_account
        from .cursor import cursor_cli_models

        try:
            listed = cursor_cli_models()
        except Exception:
            listed = []
        if not listed:
            return _fallback_cursor(), discovery_meta

        try:
            free_only = "free" in (detect_cursor_account().get("plan") or "").lower()
        except Exception:
            free_only = False
        return [DiscoveredModel(
            mid, label, "Runs on your Cursor plan via the Cursor CLI",
            200_000, vision=True, reasoning=True,
            locked=free_only and mid != "auto",
            plan_required="Cursor Pro" if (free_only and mid != "auto") else None,
        ) for mid, label in listed], discovery_meta
    if pid == "claude-code":
        return [
            DiscoveredModel("claude-code", "Claude Code (Auto)", "Let the Claude CLI pick its active model", 200_000),
            DiscoveredModel("claude-opus-5", "Claude Opus 5", "Frontier intelligence & autonomous engineering via Claude CLI", 200_000, vision=True, reasoning=True),
            DiscoveredModel("claude-sonnet-5", "Claude Sonnet 5", "Flagship agentic coding and reasoning workhorse", 200_000, vision=True, reasoning=True),
            DiscoveredModel("claude-fable-5", "Claude Fable 5", "Most capable for the hardest, longest-running work", 200_000, vision=True, reasoning=True),
            DiscoveredModel("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "Fast & responsive everyday model", 200_000, vision=True),
        ], discovery_meta
    if pid == "subscription":
        return [
            DiscoveredModel("gpt-5.6-terra", "GPT-5.6-Terra (Subscription)", "Codex subscription agentic coding model", 272_000, vision=True, reasoning=True),
            DiscoveredModel("claude-opus-5", "Claude Opus 5 (Subscription)", "Anthropic flagship reasoning model", 200_000, vision=True, reasoning=True),
            DiscoveredModel("claude-sonnet-5", "Claude Sonnet 5 (Subscription)", "Next-gen agentic coding model", 200_000, vision=True, reasoning=True),
            DiscoveredModel("claude-fable-5", "Claude Fable 5 (Subscription)", "Most capable Claude subscription model", 200_000, vision=True, reasoning=True),
        ], discovery_meta
    if pid == "mock":
        return [DiscoveredModel("mock-1", "Mock Test Model", "Offline test fixture", 32_000)], discovery_meta
    return [], discovery_meta


def get_discovered_models(provider_id: str, force_refresh: bool = False,
                          api_key: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retrieve discovered models for a provider, evaluating connection status & plan entitlements.

    Discovery (the network call) is cached for `_CACHE_TTL`; **entitlement is not**.
    Locking depends on live connection state, so caching it would leave a provider's
    models locked for up to an hour after the user successfully signs in.
    """
    pid = normalize_provider_id(provider_id)

    now = time.time()
    cached = _MODEL_CACHE.get(pid)
    if force_refresh or cached is None or (now - cached[0]) >= _CACHE_TTL:
        found, discovery_meta = _discover_raw(pid, api_key)
        raw_models = [m.to_dict() for m in found]
        _MODEL_CACHE[pid] = (now, raw_models, discovery_meta)
    else:
        _, raw_models, discovery_meta = cached

    # The rules and the connection state, NOT `entitlements` — that module
    # also picks a model to run, which needs this catalog, and importing it
    # from here made the two mutually dependent. It was the last edge
    # holding `models/` together as one component.
    from .connection_state import is_provider_connected
    from .entitlement_rules import evaluate_model_entitlement

    is_connected, user_plan, detected_meta = is_provider_connected(pid, api_key)

    context: dict[str, Any] = {}
    if detected_meta and "disabled_models" in detected_meta:
        context["disabled_models"] = detected_meta["disabled_models"]
    if pid == "ollama":
        context["installed_models"] = {
            m["id"] for m in raw_models if "installed" in (m.get("desc") or "").lower()
        }

    # Copy every row: the cache must never be mutated by a caller.
    out: list[dict[str, Any]] = []
    for cached_model in raw_models:
        m = dict(cached_model)
        # A model that came from the provider itself (live query, codex cache,
        # CLI options, installed ollama tags) carries that provider's own
        # verdict. Only a hardcoded fallback row has nothing to report.
        reported = None if m.get("is_fallback") else (m.get("locked", False),
                                                      m.get("plan_required"))
        locked, plan_req = evaluate_model_entitlement(
            provider=pid,
            model_id=m["id"],
            is_connected=is_connected,
            user_plan=user_plan,
            context=context,
            provider_reported=reported,
        )
        m["locked"] = locked
        m["plan_required"] = plan_req
        m["status"] = "available" if not locked else "locked"
        out.append(m)

    account_meta: dict[str, Any] = {**detected_meta, **discovery_meta}
    return out, account_meta


def _usable_model_ids(provider: str) -> list[str]:
    """Ids this user can actually run, for `errors` to name in a message.

    Registered below rather than imported by `errors`, which is the bottom of
    this package and must not reach up into the catalog. See
    `errors.set_alternatives_supplier`.
    """
    return [m["id"] for m in get_discovered_models(provider)[0]
            if not m.get("locked")]


errors.set_alternatives_supplier(_usable_model_ids)
