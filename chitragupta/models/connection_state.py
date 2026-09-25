"""What the user has actually got — credentials, accounts, and plans.

The half of entitlement that goes and *finds out*, as opposed to the half that
decides (`entitlement_rules.py`, pure) and the half that chooses a model to run
(`entitlements.py`, which needs the catalog).

Split out because `discovery` needs exactly this and nothing else from
entitlement: it decorates each catalog row with whether the user can run it, so
it has to know whether they are connected. Taking that from `entitlements` made
`discovery` and `entitlements` mutually dependent — `entitlements` needs the
catalog to pick a usable model — and that pair was the last load-bearing edge
holding `models/` together as one unreadable component.

The layering this establishes, bottom to top:

    errors, base, streaming          nothing above them
    entitlement_rules                pure policy, stdlib only
    connection_state                 this file: what is connected
    discovery                        the catalog, decorated with the above
    entitlements                     choosing a model to actually run
    registry                         everything

`entitlements` re-exports every name here, so existing imports are unchanged.
"""
from __future__ import annotations

from typing import Any

from ..log import suppressed

# Providers that hold an account credential *and* an API key independently.
_DUAL_CREDENTIAL_PROVIDERS = {"openai", "claude", "cursor"}

_PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "cursor": "CURSOR_API_KEY",
    "xai": "XAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _stored_api_key(pid: str, api_key: str | None = None) -> str:
    """The API key for this provider, ignoring one the user has disconnected."""
    import os

    from .base import _saved_key
    from .connections import ConnectionStatus, get_connection

    if api_key:
        return api_key
    env = _PROVIDER_KEY_ENV.get(pid)
    if not env:
        return ""
    if get_connection(pid).api_key_status == ConnectionStatus.DISCONNECTED:
        return ""
    return os.environ.get(env) or _saved_key(env) or ""


def _detect_account(pid: str) -> dict[str, Any]:
    """Live account-credential state, independent of any API key."""
    from .connections import ConnectionStatus, get_connection

    conn = get_connection(pid)
    if conn.account_status == ConnectionStatus.DISCONNECTED:
        return {}

    if pid in ("claude", "claude-code"):
        # Finding a CLI or a config file on the machine is *detection*, not
        # consent. The user must connect the provider before we will use it.
        if not conn.account_connected:
            return {}
        from .accounts import detect_claude_account
        acct = detect_claude_account()
        if pid == "claude-code":
            from .claude_cli import find_claude
            return acct if find_claude() else {}
        return acct

    if pid == "cursor":
        from .accounts import detect_cursor_account
        return detect_cursor_account() if conn.account_connected else {}

    if pid == "openai":
        from .chatgpt_auth import detect_chatgpt_local_session
        session = detect_chatgpt_local_session(fetch_usage=False)
        if session and (conn.account_connected or session.get("has_token")):
            return session
        return {}

    if pid == "xai":
        # An OAuth token is deliberately NOT an account credential here: it
        # authenticates at api.x.ai and is then refused for billing. The
        # subscription runs through xAI's CLI instead.
        from .grok_cli import find_grok_cli, grok_cli_auth_status

        # The binary being on disk is detection, and detection is not an
        # account: an installed-but-signed-out CLI was reported here as a Grok
        # subscription, which put a "Connected" badge on a provider that could
        # not answer a single message.
        if find_grok_cli() and grok_cli_auth_status().get("authenticated"):
            return {"email": conn.email or "Grok CLI", "plan": "Grok subscription"}
        return {}

    return {}


def provider_credentials(provider_id: str, api_key: str | None = None) -> dict[str, dict[str, Any]]:
    """Report each credential a provider holds, independently.

    An account and an API key are separate things: removing one must never
    disconnect the other, and connecting one must never claim the other is
    connected. Everything downstream reads this, not a single shared status.
    """
    pid = provider_id.lower()
    if pid == "anthropic":
        pid = "claude"
    elif pid == "google":
        pid = "gemini"
    elif pid == "grok":
        pid = "xai"

    from .capabilities import get_capabilities

    caps = get_capabilities(pid)
    # A key-only provider has no account credential by definition. xAI is the
    # case that matters: an OAuth token authenticates but grants no api.x.ai
    # credits, so counting it as "connected" would unlock models that fail on
    # the first message.
    account_supported = bool(caps and not caps.api_key_only) and (
        pid in _DUAL_CREDENTIAL_PROVIDERS or pid in ("claude-code", "xai"))

    key = _stored_api_key(pid, api_key)
    account = _detect_account(pid) if account_supported else {}

    return {
        "api_key": {
            "connected": bool(key),
            "reference": _PROVIDER_KEY_ENV.get(pid, ""),
            "supported": bool(_PROVIDER_KEY_ENV.get(pid)),
        },
        "account": {
            "connected": bool(account),
            "email": account.get("email"),
            "plan": account.get("plan"),
            "supported": account_supported,
            "meta": account,
        },
    }


def is_provider_connected(provider_id: str, api_key: str | None = None) -> tuple[bool, str | None, dict[str, Any]]:
    """Whether a provider can run inference at all, plus plan and metadata.

    Connected if **either** credential works. The API key wins for plan
    reporting when both are present, matching how inference picks a path.
    """
    import os

    from .connections import ConnectionStatus, get_connection

    pid = provider_id.lower()
    if pid == "anthropic":
        pid = "claude"
    elif pid == "google":
        pid = "gemini"
    elif pid == "grok":
        pid = "xai"

    conn = get_connection(pid)

    # ── providers with no per-credential split ───────────────────────────
    if pid == "mock":
        return True, "Mock", {}

    if pid == "ollama":
        import httpx
        url = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        with suppressed("if httpx.get(f'{url.rstrip('/')}/api/tags', timeout=1.5).status_ …"):
            if httpx.get(f"{url.rstrip('/')}/api/tags", timeout=1.5).status_code == 200:
                return True, "Ollama Local", {"host": url}
        return False, None, {}

    if pid == "subscription":
        # Only "connected" once a gateway is actually configured — otherwise the
        # catalog advertises models that every request would fail on.
        base = os.environ.get("CHITRAGUPTA_SUBSCRIPTION_BASE_URL")
        if not base or conn.connection_status == ConnectionStatus.DISCONNECTED:
            return False, None, {}
        return True, "Subscription Gateway", {"host": base}

    if pid == "gemini":
        if conn.api_key_status == ConnectionStatus.DISCONNECTED:
            return False, None, {}
        from .gemini import resolve_gemini_credentials
        cred = resolve_gemini_credentials(api_key=api_key)
        if cred.valid:
            return True, "Google AI Studio", {
                "email": cred.email or conn.email or "API Key User",
                "source": "api_key",
                "has_api_key": True,
            }
        return False, None, {
            "source": "none",
            "error_reason": cred.error_reason,
            "has_api_key": False,
        }

    # ── the rest: either credential is enough ────────────────────────────
    creds = provider_credentials(pid, api_key)
    key_cred, account_cred = creds["api_key"], creds["account"]

    if key_cred["connected"]:
        plans = {
            "openai": "OpenAI Developer", "claude": "Anthropic API",
            "cursor": "Cursor API", "xai": "xAI Grok",
            "deepseek": "DeepSeek Account", "openrouter": "OpenRouter Account",
        }
        return True, plans.get(pid, "API Key"), {"email": conn.email or "Developer"}

    if account_cred["connected"]:
        meta = account_cred["meta"]
        plan = account_cred["plan"] or {
            "openai": "ChatGPT Account", "claude": "Claude Subscription",
            "cursor": "Cursor", "claude-code": "Claude CLI",
        }.get(pid)
        return True, plan, {**meta, "email": account_cred["email"] or conn.email}

    return False, None, {}


# Backends that accept any model string, so absence from a catalog means nothing.
