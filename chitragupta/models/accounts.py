"""Local account detection and binding for AI providers (Google, Claude, Cursor, OpenAI, xAI).

Discovers and safely bridges accounts found on this computer:
- Google: via ~/.chitragupta/google_token.json and google_account.json
- Claude / Anthropic: via ~/.claude.json (Claude Code / Anthropic OAuth session)
- Cursor: via Cursor global storage state.vscdb (cursorAuth/cachedEmail, accessToken)
- OpenAI / ChatGPT: via OpenAI config / stored credentials
- xAI: via saved developer credentials

Credentials are NEVER exposed or duplicated into logs or memory.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..log import suppressed
from .cache import ttl_cached
from .connections import ACCOUNT, API_KEY, ConnectionStatus, get_connection, save_connection


# Not cached: it costs ~60ms, and it is the one detector whose answer can change
# from an env var rather than a write we control.
def detect_google_account() -> dict[str, Any]:
    """Detect Gemini API key configuration."""
    conn = get_connection("gemini")
    is_disconnected = (conn.account_status == ConnectionStatus.DISCONNECTED)
    with suppressed("from .gemini import resolve_gemini_credentials, GeminiCredential …"):
        from .gemini import GeminiCredentialSource, resolve_gemini_credentials
        cred = resolve_gemini_credentials()
        if cred.valid and cred.source == GeminiCredentialSource.API_KEY:
            return {
                "provider": "gemini",
                "connected": not is_disconnected,
                "email": conn.email or "Google AI Studio Developer",
                "name": "Google AI Studio",
                "plan": "Google Gemini (API Key)",
                "auth_method": "api_key",
                "found_on_computer": True,
                "has_api_key": True,
                "credential_source": "api_key",
                "credential_valid": True,
            }
    return {"provider": "gemini", "connected": False, "found_on_computer": False}


def _claude_plan_label(raw: str | None) -> str:
    """Map Anthropic's organizationType / subscriptionType to a plan name.

    Values seen in the wild: 'claude_max', 'claude_pro', 'team', 'enterprise'.
    An unrecognised value must not silently read as Free — that locks models the
    user is actually paying for.
    """
    t = (raw or "").lower()
    if "enterprise" in t:
        return "Claude Enterprise"
    if "team" in t:
        return "Claude Team"
    if "max" in t:
        return "Claude Max"
    if "pro" in t:
        return "Claude Pro"
    return "Claude Free"


@ttl_cached(4.0)
def detect_claude_account() -> dict[str, Any]:
    """Detect Claude Pro / Anthropic account found on this computer via official Claude CLI.

    Queries `claude auth status` where available. Does NOT harvest or duplicate
    OAuth tokens or private credential files.
    """
    conn = get_connection("claude")
    is_disconnected = (conn.account_status == ConnectionStatus.DISCONNECTED)

    cli_installed = False
    cli_authenticated = False
    cli_email = None
    cli_plan = None

    # 1. Prefer official Claude CLI status
    with suppressed("from .claude_cli import find_claude …"):
        from .claude_cli import find_claude
        claude_bin = find_claude()
        if claude_bin:
            cli_installed = True
            import subprocess
            res = subprocess.run(
                [claude_bin, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                with suppressed("cstatus = json.loads(res.stdout) …"):
                    cstatus = json.loads(res.stdout)
                    if cstatus.get("loggedIn"):
                        cli_authenticated = True
                        cli_email = cstatus.get("email")
                        raw_sub = (cstatus.get("subscriptionType") or "").lower()
                        cli_plan = _claude_plan_label(raw_sub)

    # 2. Check non-secret metadata from config file if CLI status was not available
    email = cli_email
    plan = cli_plan or "Claude Free"
    name = "Claude User"
    disabled_models: dict[str, str] = {}
    found_file = False

    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    candidates = [
        Path(config_dir) / ".claude.json" if config_dir else None,
        Path.home() / ".claude.json",
        Path.home() / ".claude/claude.json",
    ]
    for p in candidates:
        if not p or not p.exists():
            continue
        with suppressed("data = json.loads(p.read_text()) …"):
            data = json.loads(p.read_text())
            oa = data.get("oauthAccount") or {}
            f_email = oa.get("emailAddress")
            if f_email:
                found_file = True
                if not email:
                    email = f_email
                if not cli_plan:
                    plan = _claude_plan_label(oa.get("organizationType"))
                name = oa.get("displayName") or oa.get("fullName") or "Claude User"

            for opt in data.get("additionalModelOptionsCache") or []:
                if isinstance(opt, dict) and opt.get("disabled"):
                    val = opt.get("value") or opt.get("label") or ""
                    disabled_models[val] = opt.get("description") or "Update Required"
            if found_file:
                break

    found = bool(cli_installed or found_file or email)
    if found:
        return {
            "provider": "claude",
            "connected": False if is_disconnected else (conn.account_connected),
            "email": email or "Claude User",
            "name": name,
            "plan": plan,
            "auth_method": "account",
            "found_on_computer": True,
            "cli_installed": cli_installed,
            "cli_authenticated": cli_authenticated,
            "disabled_models": disabled_models,
        }

    return {
        "provider": "claude",
        "connected": False,
        "found_on_computer": False,
        "cli_installed": cli_installed,
        "cli_authenticated": False,
    }


def _cursor_plan_from_storage() -> str | None:
    """Cursor's tier, read from the app's non-secret local storage.

    Only `stripeMembershipType` is queried — never a token."""
    candidates = [
        Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb",
        Path.home() / ".config/Cursor/User/globalStorage/state.vscdb",
    ]
    for db_path in candidates:
        if not db_path.exists():
            continue
        with suppressed("db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) …"):
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = db.execute(
                "SELECT value FROM ItemTable WHERE key = 'cursorAuth/stripeMembershipType'"
            ).fetchone()
            if row and row[0]:
                tier = str(row[0]).strip().lower()
                if "pro" in tier:
                    return "Cursor Pro"
                if any(k in tier for k in ("business", "enterprise", "team")):
                    return "Cursor Business"
                return "Cursor Free"
    return None


@ttl_cached(4.0)
def detect_cursor_account() -> dict[str, Any]:
    """Detect Cursor account found on this computer via official CLI or non-secret metadata.

    NEVER extracts private access tokens, session keys, or database secrets.
    """
    conn = get_connection("cursor")
    is_disconnected = (conn.account_status == ConnectionStatus.DISCONNECTED)

    # 1. The CLI is what we actually run, so its identity wins. The Cursor
    #    *app* caches a different account in its sqlite, and preferring that
    #    left the card showing the old email after a CLI sign-in.
    cli_signed_in = False
    cli_email = None
    cli_name = None
    with suppressed("from .cursor import cursor_cli_auth_status …"):
        from .cursor import cursor_cli_auth_status
        st = cursor_cli_auth_status()
        cli_signed_in = bool(st.get("authenticated"))
        if cli_signed_in:
            cli_email = st.get("email")
            cli_name = st.get("name")

    if cli_signed_in and cli_email:
        # Identity from the CLI (it is what we run); plan from the Cursor app's
        # non-secret sqlite, which is the only place the tier is recorded.
        return {
            "provider": "cursor",
            "connected": False if is_disconnected else conn.account_connected,
            "email": cli_email,
            "name": cli_name or "Cursor User",
            "plan": _cursor_plan_from_storage() or "Cursor",
            "auth_method": "cli",
            "found_on_computer": True,
            "signed_in": True,
            "cli_authenticated": True,
        }

    # 2. Check non-secret metadata in local SQLite storage (cachedEmail, stripeMembershipType only)
    email = cli_email
    plan = "Cursor Pro" if cli_signed_in else "Cursor Free"
    found_storage = False

    candidates = [
        Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb",
        Path.home() / ".config/Cursor/User/globalStorage/state.vscdb",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Cursor/User/globalStorage/state.vscdb")

    for db_path in candidates:
        if not db_path.exists():
            continue
        with suppressed("conn_sql = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) …"):
            conn_sql = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            # Query ONLY non-secret metadata keys. NEVER query cursorAuth/accessToken!
            cursor_rows = dict(conn_sql.execute(
                "SELECT key, value FROM ItemTable WHERE key IN ('cursorAuth/cachedEmail', 'cursorAuth/stripeMembershipType')"
            ).fetchall())
            db_email = cursor_rows.get("cursorAuth/cachedEmail")
            membership = (cursor_rows.get("cursorAuth/stripeMembershipType") or "free").lower()
            if db_email:
                found_storage = True
                if not email:
                    email = db_email
                if "pro" in membership:
                    plan = "Cursor Pro"
                elif any(k in membership for k in ("business", "enterprise")):
                    plan = "Cursor Business"
                else:
                    plan = "Cursor Free"
                break

    found = bool(cli_signed_in or found_storage or email)
    if found:
        return {
            "provider": "cursor",
            "connected": False if is_disconnected else (conn.account_connected),
            "email": email or "Cursor User",
            "name": "Cursor User",
            "plan": plan,
            "auth_method": "account",
            "found_on_computer": True,
            "signed_in": True,
            "cli_authenticated": cli_signed_in,
        }

    return {"provider": "cursor", "connected": False, "found_on_computer": False}


@ttl_cached(4.0)
def detect_openai_account() -> dict[str, Any]:
    """Detect OpenAI connection or ChatGPT subscription state."""
    conn = get_connection("openai")
    is_disconnected = (conn.account_status == ConnectionStatus.DISCONNECTED)

    local = None
    with suppressed("from .chatgpt_auth import detect_chatgpt_local_session …"):
        from .chatgpt_auth import detect_chatgpt_local_session
        local = detect_chatgpt_local_session(fetch_usage=True)

    if not is_disconnected and conn.email and (conn.account_connected or conn.api_key_connected):
        plan = (local.get("plan") if local else None) or ("ChatGPT Free" if conn.auth_method == "account" else "OpenAI Developer")
        usage = local.get("usage") if local else None
        return {
            "provider": "openai",
            "connected": True,
            "email": conn.email or (local.get("email") if local else "OpenAI User"),
            "name": conn.account_display_name or (local.get("name") if local else "OpenAI Account"),
            "plan": plan,
            "usage": usage,
            "auth_method": conn.auth_method,
            "found_on_computer": True,
            "source": local.get("source") if local else "turnover",
        }

    if local and local.get("email"):
        return {
            "provider": "openai",
            "connected": False if is_disconnected else (local.get("source") == "turnover" and conn.account_connected),
            "email": local["email"],
            "name": local.get("name") or "ChatGPT User",
            "plan": local.get("plan") or "ChatGPT Free",
            "usage": local.get("usage"),
            "auth_method": "account",
            "found_on_computer": True,
            "source": local.get("source"),
        }
    return {"provider": "openai", "connected": False, "found_on_computer": False}


def detect_xai_account() -> dict[str, Any]:
    """Detect xAI Grok connection state."""
    conn = get_connection("xai")
    is_disconnected = (conn.account_status == ConnectionStatus.DISCONNECTED)
    if not is_disconnected and conn.email and (conn.account_connected or conn.api_key_connected):
        return {
            "provider": "xai",
            "connected": True,
            "email": conn.email or "Grok User",
            "name": conn.account_display_name or "xAI Grok",
            "plan": "Grok Account",
            "auth_method": conn.auth_method,
            "found_on_computer": True,
        }
    return {"provider": "xai", "connected": False, "found_on_computer": False}


@ttl_cached(4.0)
def detect_all_accounts() -> dict[str, dict[str, Any]]:
    """Return all detected local provider accounts."""
    claude_acct = detect_claude_account()
    return {
        "gemini": detect_google_account(),
        "claude": claude_acct,
        "claude-code": claude_acct,
        "cursor": detect_cursor_account(),
        "openai": detect_openai_account(),
        "xai": detect_xai_account(),
    }


def connect_local_account(provider: str) -> tuple[bool, str, dict[str, Any]]:
    """Bind a detected on-computer account for the given provider to TURNOVER."""
    pid = provider.lower()
    if pid == "claude-code":
        from .claude_cli import find_claude
        if not find_claude():
            return False, "Claude CLI not found on this computer", {}
        info = detect_claude_account()
        conn = get_connection("claude-code")
        now = datetime.now(UTC).isoformat()
        conn.auth_method = "cli"
        conn.email = info.get("email") or "Claude CLI"
        conn.account_display_name = info.get("name") or "Claude Code CLI"
        conn.set_credential(ACCOUNT, ConnectionStatus.ACCOUNT_CONNECTED)
        conn.connected_at = conn.connected_at or now
        conn.last_verified_at = now
        conn.status_message = f"Connected via Claude CLI ({info.get('plan', 'Claude')})"
        save_connection(conn)
        return True, f"Connected Claude Code CLI ({conn.email})", conn.to_dict()

    if pid in ("anthropic", "claude"):
        info = detect_claude_account()
        if not info.get("found_on_computer") or not info.get("email"):
            return False, "No Claude account found on this computer (~/.claude.json)", {}
        conn = get_connection("claude")
        now = datetime.now(UTC).isoformat()
        conn.auth_method = "account"
        conn.email = info["email"]
        conn.account_display_name = info.get("name") or "Claude User"
        conn.set_credential(ACCOUNT, ConnectionStatus.ACCOUNT_CONNECTED)
        conn.connected_at = conn.connected_at or now
        conn.last_verified_at = now
        conn.status_message = f"Connected to {info.get('plan', 'Claude')}"
        save_connection(conn)
        return True, f"Connected {info['email']}", conn.to_dict()

    if pid == "cursor":
        info = detect_cursor_account()
        if not info.get("found_on_computer") or not info.get("email"):
            return False, "No Cursor account found in Cursor application storage", {}
        conn = get_connection("cursor")
        now = datetime.now(UTC).isoformat()
        conn.auth_method = "account"
        conn.email = info["email"]
        conn.account_display_name = info.get("name") or "Cursor User"
        conn.set_credential(ACCOUNT, ConnectionStatus.ACCOUNT_CONNECTED)
        conn.connected_at = conn.connected_at or now
        conn.last_verified_at = now
        conn.status_message = f"Connected to {info.get('plan', 'Cursor')}"
        save_connection(conn)
        return True, f"Connected {info['email']}", conn.to_dict()

    if pid in ("gemini", "google"):
        info = detect_google_account()
        if not info.get("has_api_key"):
            return False, "Google Gemini uses API key authentication. Please enter GEMINI_API_KEY in Models & Accounts.", {}
        conn = get_connection("gemini")
        now = datetime.now(UTC).isoformat()
        conn.auth_method = "api_key"
        conn.email = info.get("email") or "Google AI Studio Developer"
        conn.account_display_name = "Google AI Studio"
        conn.set_credential(API_KEY, ConnectionStatus.API_KEY_CONNECTED)
        conn.connected_at = conn.connected_at or now
        conn.last_verified_at = now
        conn.status_message = "Connected via GEMINI_API_KEY"
        save_connection(conn)
        return True, "Connected Google Gemini (API Key)", conn.to_dict()

    if pid == "openai":
        from .chatgpt_auth import adopt_local_chatgpt_session, detect_chatgpt_local_session
        info = detect_chatgpt_local_session(fetch_usage=False)
        if info and info.get("email"):
            ok, msg, data = adopt_local_chatgpt_session()
            if ok:
                return True, msg, data
        conn = get_connection("openai")
        if conn.email and (conn.account_connected or conn.api_key_connected):
            return True, f"OpenAI already connected ({conn.email})", conn.to_dict()
        return False, "No ChatGPT / Codex session found on this computer. Use OAuth sign-in or set OPENAI_API_KEY.", {}

    if pid in ("xai", "grok"):
        from ..config import get_settings
        key = get_settings().get_secret("XAI_API_KEY")
        from .xai_auth import detect_xai_local_session
        info = detect_xai_local_session()
        if not info and not key:
            return False, "No xAI session or XAI_API_KEY found. Sign in or enter an API key.", {}
        conn = get_connection("xai")
        email = (info.get("email") if info else None) or conn.email or "xAI Account"
        now = datetime.now(UTC).isoformat()
        conn.auth_method = "account" if info else "api_key"
        conn.email = email
        conn.account_display_name = (info.get("name") if info else None) or "xAI Grok"
        # This was written as `set_credential(...) if info else API_KEY_CONNECTED`
        # — a conditional *expression* whose else branch is a bare enum value,
        # so connecting with an API key recorded no credential at all while
        # still reporting success. Credentials are independent; set the one we
        # actually have.
        if info:
            conn.set_credential(ACCOUNT, ConnectionStatus.ACCOUNT_CONNECTED)
        else:
            conn.set_credential(API_KEY, ConnectionStatus.API_KEY_CONNECTED)
        conn.connected_at = conn.connected_at or now
        conn.last_verified_at = now
        conn.status_message = "Connected to xAI Grok"
        save_connection(conn)
        return True, f"Connected {email}", conn.to_dict()

    return False, f"Unknown provider {provider}", {}
