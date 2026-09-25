"""Provider registry — resolves the configured 'bring your own model' backend.

Turnstone lets you bring a model four ways; this mirrors that:
  1. subscription  -> your paid ChatGPT/Claude/Cursor session, via a local
                      OpenAI-compatible gateway (CHITRAGUPTA_SUBSCRIPTION_BASE_URL).
  2. api-key        -> anthropic / openai (your own key).
  3. openrouter     -> hundreds of models behind one key.
  4. ollama         -> free local open-source models, fully offline.
A `mock` provider keeps the whole agent stack testable with no network/keys.
"""
from __future__ import annotations

import os
import uuid
from functools import lru_cache

from ..config import get_settings
from ..log import get_logger
from . import cache
from .anthropic import AnthropicProvider
from .base import DEFAULT_MAX_OUTPUT, ChatResult, LLMProvider, ToolCall
from .claude_code import ClaudeCodeProvider
from .cursor import CursorProvider
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .openai_compat import OllamaProvider, OpenAICompatProvider, OpenRouterProvider
from .xai import XAIProvider

log = get_logger(__name__)


class SubscriptionProvider(OpenAICompatProvider):
    """Use an existing paid subscription (ChatGPT/Claude/Cursor) via a local
    OpenAI-compatible gateway that holds your session — no API key billing.

    Point CHITRAGUPTA_SUBSCRIPTION_BASE_URL at the gateway (e.g. a local
    subscription proxy). This is the hard, ToS-sensitive path Turnstone
    advertises; Chitragupta treats it as a pluggable gateway rather than
    reverse-engineering each vendor's private auth.
    """
    name = "subscription"
    default_base = "http://localhost:8080/v1"
    default_model = "gpt-5.6-terra"
    key_env = "CHITRAGUPTA_SUBSCRIPTION_KEY"
    key_required = False

    def is_ready(self) -> tuple[bool, str]:
        base = os.environ.get("CHITRAGUPTA_SUBSCRIPTION_BASE_URL")
        if not base:
            return False, "set CHITRAGUPTA_SUBSCRIPTION_BASE_URL to your subscription gateway"
        self.base_url = base.rstrip("/")
        return True, ""


#: Every mock reply opens with this. The offline model is the default on a
#: fresh install, so it is what answers before anything is connected — and its
#: replies are prose in a chat bubble, indistinguishable from a real model's.
#: Two of them in a row read as "the app is broken" rather than "nothing is
#: connected yet", which is a different problem with a different fix.
_OFFLINE_NOTE = ("_Offline model — no AI provider is connected, so this is a "
                 "canned reply from your brain. Pick a model in **Model** "
                 "settings._\n\n")


class MockProvider(LLMProvider):
    """Deterministic offline provider. Calls search_brain once, then answers
    from the tool result — enough to exercise the full agent tool loop with no
    network or API key.

    Every reply is prefixed so it cannot be mistaken for a real model's: this
    is what runs before anything is connected, and an unmarked canned answer
    is worse than an honest refusal.
    """
    name = "mock"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or "mock-1"


    def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        """Stream the canned reply a few words at a time.

        The offline model is how this app runs on first launch with no keys, so
        it is also the only way to exercise the streaming path end to end — the
        SSE endpoint, a reconnect, the incremental render — without spending
        anyone's tokens.
        """
        from .streaming import StreamEvent, from_result

        result = self.chat(messages, tools=tools, temperature=temperature,
                           max_tokens=max_tokens)
        if result.tool_calls or not result.text:
            yield from from_result(result)
            return
        words = result.text.split(" ")
        for i in range(0, len(words), 3):
            chunk = " ".join(words[i:i + 3])
            yield StreamEvent("text", chunk if i == 0 else " " + chunk)
        yield StreamEvent("done", result=result)

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        last = messages[-1]
        tool_names = {t.name for t in (tools or [])}
        # If we just got a tool result, produce a final answer from it.
        if last.role == "tool":
            return ChatResult(text=_OFFLINE_NOTE + f"Based on your brain: {last.content[:400]}")
        # Otherwise, if a brain-search tool exists and we haven't used it, do so.
        already = any(m.role == "tool" for m in messages)
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if "search_brain" in tool_names and not already:
            return ChatResult(text="", tool_calls=[ToolCall(
                id=str(uuid.uuid4()), name="search_brain",
                arguments={"query": user[:120] or "context"},
            )])
        return ChatResult(text=f"(mock) You said: {user[:200]}")


_REGISTRY: dict[str, type[LLMProvider]] = {
    "claude": AnthropicProvider,
    "anthropic": AnthropicProvider,
    "claude-code": ClaudeCodeProvider,
    "cursor": CursorProvider,
    "gemini": GeminiProvider,
    "google": GeminiProvider,
    "xai": XAIProvider,
    "grok": XAIProvider,
    "openai": OpenAICompatProvider,
    "deepseek": DeepSeekProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
    "subscription": SubscriptionProvider,
    "mock": MockProvider,
}


# Canonical provider keys (deduplicating aliases for catalog & UI presentation)
PRIMARY_PROVIDERS = [
    "claude",
    "cursor",
    "gemini",
    "xai",
    "openai",
    "deepseek",
    "ollama",
    "openrouter",
    "claude-code",
    "subscription",
    "mock",
]


# Where each backend sends your context at query time. "local" = stays on your
# Mac; "cloud" = the injected brain context is sent off-device to that service.
_LOCALITY = {
    "mock":         ("local", "Nothing leaves your Mac (offline)."),
    "ollama":       ("local", "Runs on your Mac — your context stays on-device."),
    "cursor":       ("local", "Connects to your local Cursor session bridge or API."),
    "claude-code":  ("cloud", "Sent to Anthropic through the Claude CLI."),
    "claude":       ("cloud", "Sent to Anthropic's API."),
    "anthropic":    ("cloud", "Sent to Anthropic's API."),
    "gemini":       ("cloud", "Sent to Google Gemini API."),
    "google":       ("cloud", "Sent to Google Gemini API."),
    "xai":          ("cloud", "Sent to xAI Grok API."),
    "grok":         ("cloud", "Sent to xAI Grok API."),
    "openai":       ("cloud", "Sent to OpenAI's API."),
    "deepseek":     ("cloud", "Sent to DeepSeek's API."),
    "openrouter":   ("cloud", "Sent to OpenRouter (and the chosen model's host)."),
    "subscription": ("cloud", "Sent via your gateway to the model provider."),
}

# approximate context windows (tokens) by model-name substring — the only "limit"
# we can know without a provider account API; matched loosely, shown as approximate.
_CONTEXT_WINDOW = {
    "opus": 200_000, "sonnet": 200_000, "haiku": 200_000, "claude": 200_000,
    "gpt-6": 400_000, "gpt-5": 272_000, "terra": 272_000, "luna": 272_000, "sol": 272_000, "astra": 400_000,
    "gpt-4o": 128_000, "gpt-4.1": 1_000_000, "gpt-4": 128_000,
    "o1": 200_000, "o3": 200_000, "o4": 200_000, "gemini": 1_000_000,
    "grok": 131_072, "cursor": 128_000,
    "llama": 128_000, "qwen": 32_000, "mistral": 32_000, "deepseek": 64_000,
}


MODEL_CATALOG = {
    "claude": {
        "id": "claude",
        "label": "Claude (Anthropic)",
        "icon": "spark",
        "default_model": "claude-sonnet-5",
        "key_env": "ANTHROPIC_API_KEY",
        "key_url": "https://console.anthropic.com/settings/keys",
        "models": [
            {"id": "claude-opus-5", "name": "Claude Opus 5", "desc": "Frontier intelligence & highest-capacity reasoning"},
            {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "desc": "Balanced speed, coding & agentic reasoning"},
            {"id": "claude-fable-5", "name": "Claude Fable 5", "desc": "Most capable for the hardest, longest-running work"},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "desc": "Fast & responsive everyday model"},
        ],
    },
    "cursor": {
        "id": "cursor",
        "label": "Cursor",
        "icon": "terminal",
        "default_model": "auto",
        "key_env": "CURSOR_API_KEY",
        "key_url": "https://cursor.com/docs/cli/overview",
        # Verified against `agent --list-models` (2026-09-12). The earlier list
        # here — cursor-fast, cursor-small, claude-sonnet-5 — was invented; the
        # CLI rejects all of it. The CLI's own list replaces this once installed.
        "models": [
            {"id": "auto", "name": "Auto", "desc": "Let Cursor pick the best model for each turn"},
            {"id": "claude-opus-5-high", "name": "Claude Opus 5", "desc": "Frontier reasoning via Cursor", "locked": True, "plan_required": "Cursor Pro"},
            {"id": "claude-sonnet-5-thinking-high", "name": "Claude Sonnet 5 Thinking", "desc": "Balanced agentic coding via Cursor", "locked": True, "plan_required": "Cursor Pro"},
            {"id": "gpt-5.3-codex", "name": "Codex 5.3", "desc": "OpenAI Codex via Cursor", "locked": True, "plan_required": "Cursor Pro"},
            {"id": "composer-2.5", "name": "Composer 2.5", "desc": "Cursor's own fast model", "locked": True, "plan_required": "Cursor Pro"},
        ],
    },
    "gemini": {
        "id": "gemini",
        "label": "Google Gemini",
        "icon": "globe",
        "default_model": "gemini-3.6-flash",
        "key_env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/apikey",
        "models": [
            {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash", "desc": "Latest fast reasoning & multimodal model"},
            {"id": "gemini-3.6-flash", "name": "Gemini 3.6 Flash", "desc": "Fast reasoning & multimodal"},
            {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview", "desc": "Deep reasoning across complex domains"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "desc": "Previous-generation deep reasoning"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "Previous-generation speed & multimodal"},
        ],
    },
    "xai": {
        "id": "xai",
        "label": "xAI (Grok)",
        "icon": "zap",
        "default_model": "grok-4.6",
        "key_env": "XAI_API_KEY",
        "key_url": "https://console.x.ai",
        "models": [
            {"id": "grok-4.6", "name": "Grok 4.6", "desc": "Latest flagship reasoning model"},
            {"id": "grok-4.5", "name": "Grok 4.5", "desc": "Strong reasoning & tool calling"},
            {"id": "grok-4.3", "name": "Grok 4.3", "desc": "Fast general-purpose reasoning"},
        ],
    },
    "openai": {
        "id": "openai",
        "label": "OpenAI",
        "icon": "spark",
        "default_model": "gpt-5.6-terra",
        "key_env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys",
        "models": [
            {"id": "gpt-5.6-terra", "name": "GPT-5.6-Terra", "desc": "Balanced agentic coding model for everyday work"},
            {"id": "gpt-5.6-luna", "name": "GPT-5.6-Luna", "desc": "Fast and affordable agentic coding model"},
            {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol", "desc": "Flagship agentic coding model for complex tasks", "locked": True, "plan_required": "Pro"},
            {"id": "gpt-6-astra", "name": "GPT-6-Astra", "desc": "Our most capable model for complex, demanding work", "locked": True, "plan_required": "Pro"},
            {"id": "gpt-reserve", "name": "GPT-Reserve", "desc": "Fast and affordable backup agentic coding model"},
            {"id": "o3-mini", "name": "o3-mini", "desc": "Fast STEM & code reasoning", "locked": True, "plan_required": "Plus"},
            {"id": "gpt-5.5", "name": "GPT-5.5", "desc": "Proven previous-generation coding model"},
            {"id": "gpt-5.4", "name": "GPT-5.4", "desc": "Earlier-generation coding model"},
            {"id": "gpt-5.4-mini", "name": "GPT-5.4-Mini", "desc": "Compact, fast earlier-generation model"},
        ],
    },
    "deepseek": {
        "id": "deepseek",
        "label": "DeepSeek",
        "icon": "chip",
        "default_model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "key_url": "https://platform.deepseek.com/api_keys",
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "desc": "Current chat model (alias — always the latest)"},
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "desc": "Current reasoning model (alias — always the latest)"},
        ],
    },
    "ollama": {
        "id": "ollama",
        "label": "Ollama (Local)",
        "icon": "laptop",
        "default_model": "llama3.2",
        "key_env": "",
        "key_url": "https://ollama.com",
        "models": [
            {"id": "llama3.2", "name": "Llama 3.2", "desc": "Compact offline local model (Installed)"},
            {"id": "qwen2.5:3b", "name": "Qwen 2.5 (3B)", "desc": "Compact multilingual & coding model (Installed)"},
            {"id": "llama3.3:70b", "name": "Llama 3.3 (70B)", "desc": "Latest flagship open weights model", "locked": True, "plan_required": "Pull required"},
            {"id": "qwen2.5-coder:7b", "name": "Qwen 2.5 Coder (7B)", "desc": "Strong multilingual & coding local model", "locked": True, "plan_required": "Pull required"},
            {"id": "deepseek-r1:8b", "name": "DeepSeek R1 (8B)", "desc": "Local reasoning model", "locked": True, "plan_required": "Pull required"},
        ],
    },
    "openrouter": {
        "id": "openrouter",
        "label": "OpenRouter",
        "icon": "router",
        "default_model": "anthropic/claude-sonnet-5",
        "key_env": "OPENROUTER_API_KEY",
        "key_url": "https://openrouter.ai/keys",
        "models": [
            {"id": "anthropic/claude-sonnet-5", "name": "Claude Sonnet 5", "desc": "Via OpenRouter"},
            {"id": "anthropic/claude-opus-5", "name": "Claude Opus 5", "desc": "Via OpenRouter"},
            {"id": "openai/gpt-5.6-terra", "name": "GPT-5.6-Terra", "desc": "Via OpenRouter"},
            {"id": "deepseek/deepseek-r1", "name": "DeepSeek R1", "desc": "Via OpenRouter"},
            {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B", "desc": "Via OpenRouter"},
        ],
    },
    "claude-code": {
        "id": "claude-code",
        "label": "Claude Code CLI",
        "icon": "terminal",
        "default_model": "claude-code",
        "key_env": "",
        "key_url": "https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview",
        "models": [
            {"id": "claude-code", "name": "Claude Code Session", "desc": "Let the Claude CLI pick its active model"},
            {"id": "claude-opus-5", "name": "Claude Opus 5", "desc": "Frontier intelligence & deep reasoning (CLI session)"},
            {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "desc": "Balanced agentic coding (CLI session)"},
            {"id": "claude-fable-5", "name": "Claude Fable 5", "desc": "Most capable for the hardest, longest-running work"},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "desc": "Fast & responsive everyday model"},
        ],
    },
    "subscription": {
        "id": "subscription",
        "label": "Subscription Gateway",
        "icon": "key",
        "default_model": "gpt-5.6-terra",
        "key_env": "CHITRAGUPTA_SUBSCRIPTION_KEY",
        "key_url": "",
        "models": [
            {"id": "gpt-5.6-terra", "name": "GPT-5.6-Terra (Subscription)", "desc": "Codex subscription agentic coding model"},
            {"id": "claude-opus-5", "name": "Claude Opus 5 (Subscription)", "desc": "Anthropic flagship reasoning model"},
            {"id": "claude-sonnet-5", "name": "Claude Sonnet 5 (Subscription)", "desc": "Next-gen agentic coding model"},
            {"id": "claude-fable-5", "name": "Claude Fable 5 (Subscription)", "desc": "Most capable Claude subscription model"},
        ],
    },
    "mock": {
        "id": "mock",
        "label": "Mock (Offline)",
        "icon": "box",
        "default_model": "mock-1",
        "key_env": "",
        "key_url": "",
        "models": [
            {"id": "mock-1", "name": "Mock Test Model", "desc": "Offline deterministic test fixture"},
        ],
    },
}


def context_window(model: str | None) -> int | None:
    if not model:
        return None
    m = model.lower()
    for k, v in _CONTEXT_WINDOW.items():
        if k in m:
            return v
    return None


from ..log import suppressed
from .capabilities import get_capabilities
from .connections import ConnectionStatus, get_connection, save_connection
from .discovery import get_discovered_models


def get_model_catalog(force_refresh: bool = False) -> list[dict]:
    """Return unified model catalog for UI and per-agent configuration."""
    catalog = []
    try:
        from .accounts import detect_all_accounts
        all_accts = detect_all_accounts()
    except Exception:
        all_accts = {}

    for pid in PRIMARY_PROVIDERS:
        entry = MODEL_CATALOG.get(pid)
        if not entry:
            continue
        cls = _REGISTRY.get(pid)
        ready = False
        reason = ""
        if cls:
            try:
                ready, reason = cls().is_ready()
            except Exception as exc:
                ready, reason = False, str(exc)

        # Dynamic model discovery & account metadata
        models, account_meta = get_discovered_models(pid, force_refresh=force_refresh)
        if not models:
            # Copy: the static catalog is module state and must not be mutated.
            models = [dict(m) for m in entry.get("models", [])]

        # Connection state & capabilities
        conn = get_connection(pid)
        caps = get_capabilities(pid)

        from .connection_state import is_provider_connected, provider_credentials
        is_conn, _user_plan, _ = is_provider_connected(pid)
        creds = provider_credentials(pid)

        acct = all_accts.get(pid)

        # NOTE: readiness is deliberately NOT written back into the connection
        # here. A credential discovered on the machine is surfaced through
        # `detected_account` so the user can choose to connect it; promoting it
        # to CONNECTED on their behalf is what made providers appear connected
        # that the user never authorised.

        if not is_conn:
            ready = False
            if conn.connection_status in (ConnectionStatus.CONNECTED, ConnectionStatus.ACCOUNT_CONNECTED, ConnectionStatus.API_KEY_CONNECTED):
                conn.connection_status = ConnectionStatus.ACCOUNT_FOUND_ON_COMPUTER if (acct and acct.get("found_on_computer")) else ConnectionStatus.NOT_CONNECTED
                conn.status_message = "Not connected for model inference"
                save_connection(conn)
            if conn.connection_status == ConnectionStatus.DISCONNECTED:
                reason = "Disconnected by user"
            elif not reason:
                reason = "Not connected"
            # Lock state comes from get_discovered_models()'s live entitlement
            # pass — never mutate these dicts, they may be the discovery cache.
            models = [{**m, "locked": True, "plan_required": "Connect in Models"}
                      for m in models]

        locality, destination = _LOCALITY.get(pid, ("cloud", "Sent to model provider."))
        catalog.append({
            "id": entry["id"],
            "label": entry["label"],
            "icon": entry.get("icon", "spark"),
            "default_model": entry["default_model"],
            "key_env": entry.get("key_env", ""),
            "key_url": entry.get("key_url", ""),
            "models": models,
            "ready": ready,
            "connected": is_conn,
            "locked": not is_conn,
            "lock_reason": "Connect in Models" if not is_conn else None,
            "reason": reason,
            "locality": locality,
            "destination": destination,
            "capabilities": caps.to_dict() if caps else None,
            "connection": conn.to_dict(),
            # Each credential reported separately: an account and an API key are
            # connected or disconnected independently of one another.
            "credentials": creds,
            "account_meta": account_meta,
            "detected_account": acct if (acct and acct.get("found_on_computer")) else None,
            "plan": acct.get("plan") if acct else None,
            "usage": acct.get("usage") if acct else None,
        })
    return catalog


def list_providers() -> list[dict]:
    out = []
    try:
        from .accounts import detect_all_accounts
        local_accounts = detect_all_accounts()
    except Exception:
        local_accounts = {}

    # Show primary providers in deterministic order
    for name in PRIMARY_PROVIDERS:
        cls = _REGISTRY.get(name)
        if not cls:
            continue
        try:
            ready, reason = cls().is_ready()
        except Exception as exc:
            ready, reason = False, str(exc)
        locality, destination = _LOCALITY.get(name, ("cloud", "Sent to the model provider."))
        conn = get_connection(name)

        from .connection_state import is_provider_connected, provider_credentials
        is_conn, _user_plan, _ = is_provider_connected(name)
        creds = provider_credentials(name)
        acct = local_accounts.get(name)
        if not is_conn:
            ready = False
            if conn.connection_status in (ConnectionStatus.CONNECTED, ConnectionStatus.ACCOUNT_CONNECTED, ConnectionStatus.API_KEY_CONNECTED):
                conn.connection_status = ConnectionStatus.ACCOUNT_FOUND_ON_COMPUTER if (acct and acct.get("found_on_computer")) else ConnectionStatus.NOT_CONNECTED
                conn.status_message = "Not connected for model inference"
                save_connection(conn)
            if conn.connection_status == ConnectionStatus.DISCONNECTED:
                reason = "Disconnected by user"
            elif not reason:
                reason = "Not connected"

        caps = get_capabilities(name)
        detected_dict = acct if (acct and acct.get("found_on_computer")) else None
        usage_data = acct.get("usage") if acct else None
        plan_name = acct.get("plan") if acct else None
        out.append({
            "name": name,
            "ready": ready,
            "connected": is_conn,
            "reason": reason,
            "locality": locality,
            "destination": destination,
            "connection": conn.to_dict(),
            "capabilities": caps.to_dict() if caps else None,
            "credentials": creds,
            "detected_account": detected_dict,
            "usage": usage_data,
            "plan": plan_name,
        })
    return out


def clear_provider_cache(provider: str | None = None) -> None:
    """Invalidate everything derived from a provider's credentials.

    Called from every path that changes a credential (sign-in, disconnect, key
    saved, refresh). Clears both the memoized provider instances — which capture
    the API key at construction time — and the model-discovery cache, so a
    provider the user just connected re-discovers its real model list instead of
    serving the disconnected snapshot.
    """
    from .cache import credentials_changed

    # The work now lives in the registered invalidators below, so a module that
    # only has a credential to report — `chatgpt_auth` finishing a sign-in —
    # can say so to a leaf instead of importing this one. This function stays
    # because it is the name forty call sites already use.
    credentials_changed(provider)


# ── model-provider compatibility ──────────────────────────────────────────
# When the global CHITRAGUPTA_MODEL_NAME is an Ollama model (e.g. 'qwen2.5:3b')
# and the user selects a different provider (e.g. Gemini), the fallback
# model name leaks into the wrong provider, causing 404 errors.
# This map checks whether a model "looks like" it belongs to a provider.
_PROVIDER_MODEL_PREFIXES: dict[str, tuple[str, ...]] = {
    "gemini":    ("gemini",),
    "google":    ("gemini",),
    "claude":    ("claude",),
    "anthropic": ("claude",),
    "openai":    ("gpt-", "o1", "o3", "o4", "chatgpt", "gpt", "codex"),
    "xai":       ("grok",),
    "grok":      ("grok",),
    "deepseek":  ("deepseek",),
    "ollama":    (),  # ollama accepts anything, no filtering needed
    "openrouter": (),  # openrouter uses slash-prefixed IDs, accept anything
}

def _compatible_model(provider_name: str, model: str | None) -> str | None:
    """Return model if it looks compatible with provider_name, else None.

    This prevents a stale global model name (from a different provider)
    from being passed to a provider that doesn't recognize it.  The
    provider will then use its own default_model.
    """
    if not model:
        return None
    prefixes = _PROVIDER_MODEL_PREFIXES.get(provider_name)
    if prefixes is None or len(prefixes) == 0:
        return model  # unknown or permissive provider — pass through
    m = model.lower().strip()
    if any(m.startswith(p) for p in prefixes):
        return model  # model belongs to this provider
    return None  # incompatible — let provider use its default


def _entitled_model(provider_name: str, model: str | None) -> str | None:
    """The nearest model to `model` that this account can actually run.

    A model id reaches us from storage — an agent binding, localStorage, a
    settings default — while the provider's catalog moves underneath it, so
    sending one straight through is how a ChatGPT Free account ended up asking
    for `gpt-5.6-terra` and being told it needs Pro. `run_turn` already
    re-checked, but every other caller that builds a provider itself (the lead
    agent's welcome, the onboarding digest) skipped the check and hit exactly
    that error on the user's first two screens.

    Doing it here makes the repair a property of *getting a provider* rather
    than of one route remembering to ask. A discovery failure never blocks the
    call: the requested id is honoured, as before.
    """
    from ..log import suppressed

    resolved = model
    with suppressed("from .entitlements import resolve_usable_model …"):
        from .entitlements import resolve_usable_model

        resolved, _replaced = resolve_usable_model(provider_name, model)
    return resolved


class UnknownProvider(LLMProvider):
    """A provider id this build does not recognise.

    The registry used to answer an unknown id with `MockProvider`, and the mock
    reports itself ready — so a stale id in localStorage, a rename, or a label
    that never mapped produced canned offline replies ("Based on your brain: …")
    while the picker still showed a real vendor. The user was told nothing, and
    the one number that would have given it away — token usage — stayed at zero
    for a reason that looked like the meter being broken.

    Not an exception. `get_provider` is called from a route handler, a brain
    enrichment pass and the agent loop, and an unrecognised id is user state
    that has drifted, not a programming error. Answering with a provider that is
    honestly *not ready* routes it into the message the app already has for
    exactly this — pick another model — instead of a 500 halfway through a turn.
    """

    def __init__(self, model: str | None = None, requested: str = "") -> None:
        # The requested id, so the message names what the user actually chose
        # rather than the word "unknown".
        self.name = requested or "unknown"
        self.model = model or ""

    def is_ready(self) -> tuple[bool, str]:
        return False, (f"'{self.name}' is not a model provider this version of "
                       "Chitragupta knows about")

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        # A chat() returns a ChatResult and never raises. Nothing should reach
        # here — `is_ready` is False — but a caller that skips the check gets
        # an empty answer rather than an exception it cannot translate.
        return ChatResult(text="")


@lru_cache
def get_provider(name: str | None = None, model: str | None = None) -> LLMProvider:
    name = (name or get_settings().model_provider or "mock").lower()
    cls = _REGISTRY.get(name)
    if cls is None:
        # Never silently substitute the offline mock. A typo, a retired id, or
        # a provider added to the catalog but never registered here all arrive
        # as an unknown name, and answering any of them with a confident canned
        # reply is the failure. The mock stays reachable by asking for it.
        log.warning("unknown model provider %r requested", name)
        p: LLMProvider = UnknownProvider(model=model, requested=name)
        _wrap_usage(p)
        return p
    safe_model = _entitled_model(name, _compatible_model(name, model))
    p = cls(model=safe_model)
    _wrap_usage(p)
    return p


#: Stamped on a `ChatResult` once its tokens have been counted. The agent loop
#: streams, and a provider with no real streaming answers a `stream()` by
#: calling its own `chat()` — so the same result arrives at both wrappers below.
#: Marking the object is what makes counting it exactly once need no shared
#: state, which matters because `get_provider` is cached and one instance is
#: handed to every concurrent turn.
_COUNTED = "_chitragupta_usage_counted"


def _wrap_usage(p: LLMProvider) -> None:
    """Count every model call, whichever way it was made.

    This used to wrap `chat()` alone and call itself the choke point. It was not:
    the agent loop calls `stream()`, and every provider that really streams —
    Anthropic with a key, anything OpenAI-compatible, the Claude and Grok CLIs,
    Cursor — overrides `stream()` and never touches `chat()`. So the single
    biggest spender in the app, the agent turn, recorded nothing, while the
    subscription paths that fall back to `chat()` recorded normally. The meter
    was wrong, and wrong differently for two users of the same app.

    Estimates from length when the provider does not report usage (the CLIs, the
    subscription gateway, the mock).
    """
    orig_chat = p.chat
    orig_stream = p.stream

    def _count(messages, result) -> None:
        if result is None or getattr(result, _COUNTED, False):
            return
        with suppressed("marking a ChatResult as counted"):
            setattr(result, _COUNTED, True)
        with suppressed("from ..usage import record …"):
            from ..usage import record
            tin = getattr(result, "input_tokens", 0)
            tout = getattr(result, "output_tokens", 0)
            if tin == 0 and tout == 0:
                tin = sum(len(getattr(m, "content", "") or "") for m in messages) // 4
                tout = len(getattr(result, "text", "") or "") // 4
            record(p.name, getattr(p, "model", None), tin, tout)

    def chat(messages, **kw):
        r = orig_chat(messages, **kw)
        _count(messages, r)
        return r

    def stream(messages, **kw):
        """Pass the events straight through, counting the one that carries the
        result. A turn the user stops never reaches `done`, so the tokens it
        did spend are counted from the partial on the way out."""
        result = None
        spent = []
        try:
            for event in orig_stream(messages, **kw):
                if getattr(event, "kind", "") == "text" and getattr(event, "text", ""):
                    spent.append(event.text)
                elif getattr(event, "kind", "") == "done":
                    result = getattr(event, "result", None)
                yield event
        finally:
            if result is None and spent:
                result = ChatResult(text="".join(spent))
            _count(messages, result)

    p.chat = chat
    p.stream = stream


@cache.on_credentials_change
def _drop_memoized_providers(_provider: str | None = None) -> None:
    """A provider instance captures its API key at construction time."""
    get_provider.cache_clear()


@cache.on_credentials_change
def _drop_discovered_models(provider: str | None = None) -> None:
    """Otherwise a provider the user just connected keeps serving the model
    list it had while disconnected."""
    from .discovery import clear_model_cache
    clear_model_cache(provider)
