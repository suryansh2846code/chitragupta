"""Model providers: what the user can run, what they are signed in to, and signing in."""
from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, HTTPException

from ...config import get_settings
from ...log import get_logger, suppressed
from ...models import list_providers
from ..concurrency import probes_a_provider
from ..schemas import SecretIn

log = get_logger(__name__)
router = APIRouter()


# ── agents & chat ─────────────────────────────────────────────────────────
@router.get("/api/models/catalog")
@probes_a_provider
def models_catalog(refresh: bool = False):
    from ...models.registry import get_model_catalog
    return {"catalog": get_model_catalog(force_refresh=refresh)}


@router.get("/api/usage")
def usage_get():
    """Cumulative LLM token usage (persisted). Includes a context-window 'limit'
    for the active model when known."""
    from ... import usage as _usage
    from ...models.registry import _LOCALITY, context_window
    s = get_settings()
    data = _usage.get()
    active = data["by_provider"].get(s.model_provider, {})
    ctx = context_window(active.get("model") or s.model_name)
    locality = _LOCALITY.get(s.model_provider, ("cloud", ""))[0]
    return {"total": data["total"], "by_provider": data["by_provider"],
            "active_provider": s.model_provider, "active": active,
            "context_window": ctx, "locality": locality, "updated": data["updated"]}


@router.post("/api/usage/reset")
def usage_reset():
    from ... import usage as _usage
    _usage.reset()
    return {"reset": True}


# ── models & connectors ───────────────────────────────────────────────────
@router.get("/api/providers")
@probes_a_provider
def providers():
    s = get_settings()
    return {"active": s.model_provider, "providers": list_providers()}


@router.get("/api/providers/capabilities")
def get_provider_capabilities_endpoint():
    from ...models.capabilities import list_capabilities
    return {"capabilities": list_capabilities()}


@router.get("/api/providers/connections")
def get_provider_connections_endpoint():
    from ...models.connections import list_connections
    return {"connections": [c.to_dict() for c in list_connections()]}


@router.get("/api/providers/detected-accounts")
@probes_a_provider
def get_detected_accounts_endpoint():
    from ...models.accounts import detect_all_accounts
    return {"accounts": detect_all_accounts()}


@router.post("/api/providers/{name}/connect-local")
@probes_a_provider
def connect_local_provider_endpoint(name: str):
    from ...models.accounts import connect_local_account
    from ...models.registry import clear_provider_cache
    ok, msg, data = connect_local_account(name)
    if not ok:
        raise HTTPException(400, msg)
    clear_provider_cache()
    return {"ok": True, "message": msg, "connection": data}


@router.get("/api/providers/{name}/cli")
@probes_a_provider
def provider_cli_status(name: str):
    """Is the vendor CLI this provider needs installed, and by us?"""
    from ...models.cli_manager import PROVIDER_CLI, install_status

    vendor = PROVIDER_CLI.get(name.lower())
    if not vendor:
        return {"installable": False, "vendor": None}
    return install_status(vendor)


@router.post("/api/providers/{name}/cli/install")
@probes_a_provider
def provider_cli_install(name: str):
    """Download and pin the vendor CLI. Runs in the background so a UI refresh
    cannot kill it; poll GET /api/providers/{name}/cli for progress."""
    from ...models.cli_manager import PROVIDER_CLI, start_install

    vendor = PROVIDER_CLI.get(name.lower())
    if not vendor:
        raise HTTPException(400, f"'{name}' does not use a managed CLI")
    from ...models.registry import clear_provider_cache
    clear_provider_cache(name)
    return start_install(vendor)


@router.post("/api/providers/{name}/cli/uninstall")
@probes_a_provider
def provider_cli_uninstall(name: str):
    from ...models.cli_manager import PROVIDER_CLI, uninstall_cli

    vendor = PROVIDER_CLI.get(name.lower())
    if not vendor:
        raise HTTPException(400, f"'{name}' does not use a managed CLI")
    removed = uninstall_cli(vendor)
    from ...models.registry import clear_provider_cache
    clear_provider_cache(name)
    return {"removed": removed, "vendor": vendor}


def _start_sign_in(name: str) -> dict:
    """Begin sign-in for a provider — or explain why it has none.

    A plain function because two routes do this: `/auth/start` and the older
    `/signin` alias. Calling one route handler from another stopped working when
    handlers moved onto their own thread lane and began returning coroutines —
    and a shared implementation is what they both wanted regardless.
    """
    from ...models.auth_flows import get_flow

    started = get_flow(name).start().to_dict()
    if started.get("started"):
        # The floating card is raised by the page, not here: under --dev this
        # process is a separate uvicorn with no handle on the webview.
        from .. import desktop_bridge
        started["timeout_seconds"] = desktop_bridge.signin_timeout_seconds()
    return started


def _sign_in_status(name: str) -> dict:
    """Poll an in-flight sign-in. Shared with the per-provider `oauth-status`
    aliases."""
    from ...models.auth_flows import get_flow
    return get_flow(name).status().to_dict()


def _submit_sign_in_code(name: str, payload: dict) -> dict:
    """Hand an authorization code back to a flow that asked for one. Shared with
    the `/claude/submit-code` alias."""
    from ...models.auth_flows import get_flow

    code = (payload or {}).get("code", "").strip()
    if not code:
        raise HTTPException(400, "code is required")
    flow = get_flow(name)
    submit = getattr(flow, "submit_code", None)
    if submit is None:
        raise HTTPException(400, f"'{name}' sign-in does not use an authorization code")
    ok, msg = submit(code)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@router.post("/api/providers/{name}/auth/start")
@probes_a_provider
def auth_start_endpoint(name: str):
    """Begin sign-in for a provider — or explain why it has none."""
    return _start_sign_in(name)


@router.get("/api/providers/{name}/auth/status")
@probes_a_provider
def auth_status_endpoint(name: str):
    """Poll an in-flight sign-in: idle | waiting | success | error."""
    return _sign_in_status(name)


@router.post("/api/providers/{name}/auth/code")
@probes_a_provider
def auth_submit_code_endpoint(name: str, payload: dict):
    """Hand an authorization code back to a flow that asked for one."""
    return _submit_sign_in_code(name, payload)


@router.post("/api/providers/{name}/auth/cancel")
@probes_a_provider
def auth_cancel_endpoint(name: str):
    from ...models.auth_flows import get_flow

    flow = get_flow(name)
    cancel = getattr(flow, "cancel", None)
    if cancel is not None:
        cancel()
    return {"cancelled": True, "provider_id": name}


# ── back-compat aliases for the pre-unification routes ────────────────────
@router.post("/api/providers/{name}/signin")
@probes_a_provider
def signin_provider_endpoint(name: str):
    return _start_sign_in(name)


@router.get("/api/providers/openai/oauth-status")
@probes_a_provider
def openai_oauth_status_endpoint():
    return _sign_in_status("openai")


@router.get("/api/providers/xai/oauth-status")
@probes_a_provider
def xai_oauth_status_endpoint():
    return _sign_in_status("xai")


@router.get("/api/providers/claude/oauth-status")
@probes_a_provider
def claude_oauth_status_endpoint():
    return _sign_in_status("claude")


@router.post("/api/providers/claude/submit-code")
@probes_a_provider
def claude_submit_code_endpoint(payload: dict):
    return _submit_sign_in_code("claude", payload)


@router.get("/api/providers/{name}/models")
@probes_a_provider
def get_provider_models_endpoint(name: str, refresh: bool = False):
    from ...models.discovery import get_discovered_models
    models, meta = get_discovered_models(name, force_refresh=refresh)
    return {"models": models, "account_meta": meta}


@router.post("/api/providers/{name}/refresh")
@probes_a_provider
def refresh_provider_endpoint(name: str):
    from datetime import datetime

    from ...models.capabilities import get_capabilities
    from ...models.connections import ConnectionStatus, get_connection, save_connection
    from ...models.discovery import get_discovered_models
    from ...models.registry import _REGISTRY, clear_provider_cache

    cls = _REGISTRY.get(name)
    if cls is None:
        raise HTTPException(404, f"unknown provider '{name}'")
    clear_provider_cache()

    # Refresh must be able to finish a sign-in. A CLI login completes in the
    # browser long after our poll gave up, so ask the flow first — its status()
    # adopts a signed-in CLI and records the account.
    with suppressed("from ...models.auth_flows import get_flow …"):
        from ...models.auth_flows import get_flow
        get_flow(name).status()

    inst = cls()
    ready, reason = inst.is_ready()
    models, account_meta = get_discovered_models(name, force_refresh=True)

    caps = get_capabilities(name)
    conn = get_connection(name)
    now = datetime.now(UTC).isoformat()
    conn.last_verified_at = now
    conn.status_message = reason or ("Connected & ready" if ready else "")

    if ready:
        if conn.connection_status in (ConnectionStatus.NOT_CONNECTED, ConnectionStatus.DISCONNECTED, ConnectionStatus.ERROR):
            if conn.auth_method == "account" or (not getattr(inst, "api_key", None) and (conn.email or account_meta.get("email"))):
                conn.connection_status = ConnectionStatus.ACCOUNT_CONNECTED
            else:
                conn.connection_status = ConnectionStatus.API_KEY_CONNECTED if (caps and caps.api_key_supported) else ConnectionStatus.CONNECTED
        if not conn.connected_at:
            conn.connected_at = now
    else:
        if conn.connection_status != ConnectionStatus.DISCONNECTED:
            conn.connection_status = ConnectionStatus.NOT_CONNECTED

    if account_meta.get("email"):
        conn.email = account_meta["email"]
    if account_meta.get("name"):
        conn.account_display_name = account_meta["name"]
    if account_meta.get("account_id"):
        conn.account_id = account_meta["account_id"]

    usage = None
    plan = None
    if name == "openai":
        with suppressed("from ...models.chatgpt_auth import get_chatgpt_subscription_usag …"):
            from ...models.chatgpt_auth import detect_chatgpt_local_session, get_chatgpt_subscription_usage
            usage = get_chatgpt_subscription_usage(force_refresh=True)
            sess = detect_chatgpt_local_session(fetch_usage=False)
            if sess and sess.get("plan"):
                plan = sess["plan"]

    save_connection(conn)
    return {
        "ok": True,
        "ready": ready,
        "reason": reason,
        "connection": conn.to_dict(),
        "models": models,
        "account_meta": account_meta,
        "usage": usage,
        "plan": plan,
    }


@router.post("/api/providers/{name}/disconnect")
@probes_a_provider
def disconnect_provider_endpoint(name: str, scope: str = "all"):
    """Disconnect one credential, or both.

    `scope` is `account`, `api_key`, or `all`. An account and an API key are
    independent: removing the key must leave the signed-in account alone, and
    signing out must leave a saved key alone.
    """
    from datetime import datetime

    from ...models.connections import ACCOUNT, API_KEY, ConnectionStatus, get_connection, save_connection
    from ...models.registry import _REGISTRY, clear_provider_cache

    pid = name.lower()
    if scope not in ("all", ACCOUNT, API_KEY):
        raise HTTPException(400, f"scope must be 'account', 'api_key' or 'all' (got '{scope}')")
    cls = _REGISTRY.get(pid)
    if cls is None:
        raise HTTPException(404, f"unknown provider '{name}'")

    drop_key = scope in ("all", API_KEY)
    drop_account = scope in ("all", ACCOUNT)
    conn = get_connection(pid)
    now = datetime.now(UTC).isoformat()

    if drop_key:
        key_env = getattr(cls, "key_env", None)
        if key_env:
            get_settings().set_secret(key_env, None)
        if pid in ("gemini", "google"):
            get_settings().set_secret("GEMINI_API_KEY", None)
            get_settings().set_secret("GOOGLE_API_KEY", None)
        conn.set_credential(API_KEY, ConnectionStatus.DISCONNECTED)

    if drop_account:
        # Forget any stored OAuth credential and stop an in-flight sign-in.
        if pid in ("openai", "chatgpt"):
            from ...models.chatgpt_auth import disconnect as forget_chatgpt
            forget_chatgpt()
        elif pid in ("xai", "grok"):
            from ...models.xai_auth import disconnect as forget_xai
            forget_xai()
        conn.set_credential(ACCOUNT, ConnectionStatus.DISCONNECTED)
        conn.email = ""
        conn.account_display_name = ""
        conn.account_id = ""
        conn.auth_method = "api_key" if conn.api_key_connected else "none"

    conn.last_verified_at = now
    conn.status_message = {
        "all": "Disconnected by user",
        ACCOUNT: "Account signed out",
        API_KEY: "API key removed",
    }[scope]
    save_connection(conn)
    clear_provider_cache(pid)

    return {"disconnected": True, "scope": scope, "connection": conn.to_dict()}


@router.post("/api/providers/{name}/key")
@probes_a_provider
def save_provider_key(name: str, body: SecretIn):
    """Save (or clear) an LLM provider's API key from the UI — stored locally in
    ~/Library/Chitragupta/secrets.json and picked up by the provider on next use."""
    from datetime import datetime

    from ...models.connections import API_KEY, ConnectionStatus, get_connection, save_connection
    from ...models.discovery import get_discovered_models
    from ...models.registry import _REGISTRY, clear_provider_cache

    cls = _REGISTRY.get(name)
    if cls is None:
        raise HTTPException(404, f"unknown provider '{name}'")
    key_env = getattr(cls, "key_env", None)
    if not key_env:
        raise HTTPException(400, f"'{name}' does not use an API key")

    val = body.value.strip() if body.value else ""
    get_settings().set_secret(key_env, val or None)

    conn = get_connection(name)
    now = datetime.now(UTC).isoformat()
    conn.last_verified_at = now
    conn.credential_reference = key_env

    if not val:
        # Removing the API key must NOT sign the user out of their account.
        conn.set_credential(API_KEY, ConnectionStatus.DISCONNECTED)
        conn.status_message = "API key removed"
        save_connection(conn)
        clear_provider_cache(name)
        return {"saved": True, "ready": conn.account_connected,
                "reason": "" if conn.account_connected else f"set {key_env}",
                "connection": conn.to_dict()}

    try:
        inst = cls(api_key=val)
        ready, reason = inst.is_ready()
    except Exception as exc:
        ready, reason = False, str(exc)[:120]

    if ready:
        conn.set_credential(API_KEY, ConnectionStatus.API_KEY_CONNECTED)
        conn.connected_at = now
        conn.status_message = "Connected via API key"
        # Discover models & identity with new key
        with suppressed("_, meta = get_discovered_models(name, force_refresh=True, api_ke …"):
            _, meta = get_discovered_models(name, force_refresh=True, api_key=val)
            if meta.get("email"):
                conn.email = meta["email"]
            if meta.get("name"):
                conn.account_display_name = meta["name"]
            if meta.get("account_id"):
                conn.account_id = meta["account_id"]
    else:
        conn.set_credential(API_KEY, ConnectionStatus.ERROR)
        conn.status_message = reason

    save_connection(conn)
    clear_provider_cache(name)
    return {"saved": True, "ready": ready, "reason": reason, "connection": conn.to_dict()}


@router.post("/api/providers/{name}/test")
@probes_a_provider
def test_provider_key(name: str, body: SecretIn | None = None):
    """Test / verify an LLM provider connection with either a test key or saved key."""
    from ...models.registry import _REGISTRY
    cls = _REGISTRY.get(name)
    if cls is None:
        raise HTTPException(404, f"unknown provider '{name}'")
    test_key = body.value if (body and body.value is not None) else None
    try:
        inst = cls(api_key=test_key)
        ready, reason = inst.is_ready()
        if not ready:
            return {"ok": False, "message": reason}
        return {"ok": True, "message": f"{name} is connected and ready!"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)[:180]}
