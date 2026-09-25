"""One shape for every provider sign-in.

Each provider grew its own module, its own state machine and its own endpoint
(`/providers/openai/oauth-status`, `/providers/xai/oauth-status`,
`/providers/claude/oauth-status`, `/providers/claude/submit-code`), so the API
and the frontend both had to special-case providers by name. This module puts a
single protocol in front of them:

    start(provider)   -> AuthStart    begin sign-in (or explain why there is none)
    status(provider)  -> AuthStatus   poll an in-flight sign-in
    submit_code(...)  -> (ok, msg)    for flows that hand back a code
    cancel(provider)                  abandon an in-flight sign-in

The flows themselves are unchanged — this is the seam, not a rewrite. Adding a
provider means registering a flow here; no route and no UI branch changes.
"""
from __future__ import annotations

import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import UTC
from typing import Any, Protocol

from .capabilities import get_capabilities


@dataclass
class AuthStart:
    provider_id: str
    started: bool = False
    detail: str = ""
    auth_url: str = ""
    brand_name: str = ""
    browser_opened: bool = False
    requires_code: bool = False
    # Why a sign-in could not begin, when it could not.
    api_key_only: bool = False
    key_env: str = ""
    cli_required: bool = False
    cli_found: bool = False
    cli_installable: bool = False      # we can fetch it for the user
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(d.pop("extra") or {})
        return d


@dataclass
class AuthStatus:
    provider_id: str
    status: str = "idle"          # idle | waiting | success | error
    error: str = ""
    email: str = ""
    auth_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuthFlow(Protocol):
    provider_id: str

    def start(self) -> AuthStart: ...
    def status(self) -> AuthStatus: ...


def _mark_account_connected(pid: str, email: str | None, plan: str) -> None:
    """Record a CLI sign-in as a connected account credential."""
    from datetime import datetime

    from .connections import ACCOUNT, ConnectionStatus, get_connection, save_connection
    from .registry import clear_provider_cache

    conn = get_connection(pid)
    if conn.account_connected and (not email or conn.email == email):
        return
    now = datetime.now(UTC).isoformat()
    conn.auth_method = "cli"
    conn.email = email or conn.email or plan
    conn.account_display_name = conn.account_display_name or plan
    conn.set_credential(ACCOUNT, ConnectionStatus.ACCOUNT_CONNECTED)
    conn.connected_at = conn.connected_at or now
    conn.last_verified_at = now
    conn.status_message = f"Connected via CLI ({plan})"
    save_connection(conn)
    clear_provider_cache(pid)


def _open(url: str) -> bool:
    if not url:
        return False
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


# ── flows ────────────────────────────────────────────────────────────────
class ApiKeyOnlyFlow:
    """No interactive sign-in exists — say so instead of opening a browser."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    def start(self) -> AuthStart:
        from .registry import _REGISTRY

        caps = get_capabilities(self.provider_id)
        key_env = getattr(_REGISTRY.get(self.provider_id), "key_env", "") or ""
        name = caps.display_name if caps else self.provider_id
        return AuthStart(
            provider_id=self.provider_id, started=False, api_key_only=True,
            key_env=key_env, auth_url=caps.official_auth_url if caps else "",
            detail=(f"{name} uses API key authentication. Set {key_env} in "
                    "Models & Accounts." if key_env
                    else f"{name} uses API key authentication."),
        )

    def status(self) -> AuthStatus:
        return AuthStatus(provider_id=self.provider_id, status="idle")


class ChatGPTFlow:
    provider_id = "openai"

    def start(self) -> AuthStart:
        from .chatgpt_auth import start_chatgpt_oauth_flow

        ok, auth_url, msg = start_chatgpt_oauth_flow()
        opened = _open(auth_url) if ok and auth_url else False
        return AuthStart(
            provider_id=self.provider_id, started=ok, auth_url=auth_url,
            brand_name="ChatGPT", browser_opened=opened,
            detail=("Opened ChatGPT sign-in in your browser — choose an account "
                    "to continue." if opened else msg),
        )

    def status(self) -> AuthStatus:
        from .chatgpt_auth import get_oauth_flow_status

        d = get_oauth_flow_status()
        return AuthStatus(provider_id=self.provider_id, status=d.get("status", "idle"),
                          error=d.get("error", ""), email=d.get("email", ""),
                          auth_url=d.get("auth_url", ""))


class ClaudeFlow:
    provider_id = "claude"

    def start(self) -> AuthStart:
        from .claude_auth import find_claude_cli, start_claude_login_flow

        ok, auth_url, msg = start_claude_login_flow()
        has_cli = bool(find_claude_cli())
        opened = True if (has_cli and ok) else _open(auth_url)
        return AuthStart(
            provider_id=self.provider_id, started=True, auth_url=auth_url,
            brand_name="Claude", browser_opened=opened, requires_code=True,
            detail=msg or "Opened Claude authorization — sign in to your account.",
        )

    def status(self) -> AuthStatus:
        from .claude_auth import get_claude_auth_status

        d = get_claude_auth_status()
        return AuthStatus(provider_id=self.provider_id, status=d.get("status", "idle"),
                          error=d.get("error", ""), email=d.get("email", ""),
                          auth_url=d.get("auth_url", ""))

    def submit_code(self, code: str) -> tuple[bool, str]:
        from .claude_auth import submit_claude_auth_code

        return submit_claude_auth_code(code)


class CursorFlow:
    """Cursor's own CLI owns sign-in — `agent login` opens
    authenticator.cursor.sh itself, because the OAuth client belongs to the CLI.
    We spawn it and poll `agent status`, so the user sees the same
    click -> browser -> connected flow as any other provider."""

    provider_id = "cursor"

    def start(self) -> AuthStart:
        from .cursor import cursor_cli_auth_status, start_cursor_cli_login

        st = cursor_cli_auth_status()
        if not st["installed"]:
            return AuthStart(
                provider_id=self.provider_id, started=False, brand_name="Cursor",
                cli_required=True, cli_found=False, cli_installable=True,
                auth_url="https://cursor.com/docs/cli/overview",
                detail="Cursor runs through its CLI. "
                       "Chitragupta can install it for you.")

        ok, msg = start_cursor_cli_login()
        return AuthStart(
            provider_id=self.provider_id, started=ok, brand_name="Cursor",
            browser_opened=ok, cli_found=True,
            auth_url="https://cursor.com/docs/cli/overview",
            detail=msg)

    def cancel(self) -> None:
        from .cursor import cancel_cli_login

        cancel_cli_login()

    def status(self) -> AuthStatus:
        from .cursor import cursor_cli_auth_status, login_progress

        st = cursor_cli_auth_status()
        if not st["installed"]:
            return AuthStatus(provider_id=self.provider_id, status="idle")

        progress = login_progress()
        if progress["in_flight"]:
            # A sign-in we started. Completion is the CLI's process exiting or
            # the account changing — NOT the session merely looking
            # authenticated, which is already true when switching accounts and
            # would report success before the user touched the browser.
            if not progress["done"]:
                return AuthStatus(provider_id=self.provider_id, status="waiting")
            _mark_account_connected("cursor", progress.get("email"), "Cursor")
            return AuthStatus(provider_id=self.provider_id, status="success",
                              email=progress.get("email") or "")

        if st.get("authenticated"):
            _mark_account_connected("cursor", st.get("email"), "Cursor")
            return AuthStatus(provider_id=self.provider_id, status="success",
                              email=st.get("email") or "")
        return AuthStatus(provider_id=self.provider_id, status="idle")


class GrokFlow:
    """xAI's CLI owns sign-in: `grok login --oauth` opens the Grok Build consent
    screen at accounts.x.ai. api.x.ai has no subscription path."""

    provider_id = "xai"

    def start(self) -> AuthStart:
        from .grok_cli import grok_cli_auth_status, start_grok_cli_login

        st = grok_cli_auth_status()
        if not st["installed"]:
            return AuthStart(
                provider_id=self.provider_id, started=False, brand_name="Grok",
                cli_required=True, cli_found=False, cli_installable=True,
                auth_url="https://x.ai/news/grok-build-cli",
                detail="Grok runs through its CLI. "
                       "Chitragupta can install it for you.")

        ok, msg = start_grok_cli_login()
        return AuthStart(
            provider_id=self.provider_id, started=ok, brand_name="Grok",
            browser_opened=ok, cli_found=True,
            auth_url="https://x.ai/news/grok-build-cli", detail=msg)

    def cancel(self) -> None:
        from .grok_cli import cancel_cli_login

        cancel_cli_login()

    def status(self) -> AuthStatus:
        from .grok_cli import grok_cli_auth_status, login_progress

        st = grok_cli_auth_status()
        if not st["installed"]:
            return AuthStatus(provider_id=self.provider_id, status="idle")

        progress = login_progress()
        if progress["in_flight"]:
            if not progress["done"]:
                return AuthStatus(provider_id=self.provider_id, status="waiting")
            _mark_account_connected("xai", None, "Grok subscription")
            return AuthStatus(provider_id=self.provider_id, status="success")

        if st.get("authenticated"):
            _mark_account_connected("xai", None, "Grok subscription")
            return AuthStatus(provider_id=self.provider_id, status="success")
        return AuthStatus(provider_id=self.provider_id, status="idle")


class ClaudeCodeFlow:
    """The Claude CLI owns its own sign-in; connecting is a local bind."""

    provider_id = "claude-code"

    def start(self) -> AuthStart:
        from .claude_cli import find_claude

        cli = find_claude()
        return AuthStart(
            provider_id=self.provider_id, started=False, brand_name="Claude Code",
            cli_required=True, cli_found=bool(cli),
            auth_url="https://docs.anthropic.com/en/docs/claude-code/overview",
            detail=("Claude CLI found — run `claude auth login` in a terminal if you "
                    "aren't signed in, then press Refresh." if cli else
                    "Claude Code runs through the Claude CLI. Install it "
                    "(`npm i -g @anthropic-ai/claude-code`), sign in, then press Refresh."),
        )

    def status(self) -> AuthStatus:
        from .claude_cli import find_claude

        return AuthStatus(provider_id=self.provider_id,
                          status="success" if find_claude() else "idle")


class BrowserFlow:
    """Generic: open the provider's documented auth page."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    def start(self) -> AuthStart:
        caps = get_capabilities(self.provider_id)
        url = caps.official_auth_url if caps else ""
        name = caps.display_name if caps else self.provider_id
        return AuthStart(provider_id=self.provider_id, started=bool(url), auth_url=url,
                         brand_name=name, browser_opened=_open(url),
                         detail=f"Opened {name} in your browser.")

    def status(self) -> AuthStatus:
        return AuthStatus(provider_id=self.provider_id, status="idle")


# xAI's OAuth flow (models/xai_auth.py) is intentionally NOT registered: it
# authenticates but grants no api.x.ai credits, so it can never yield a usable
# credential. The subscription path is the Grok CLI — see GrokFlow.
_FLOWS: dict[str, AuthFlow] = {
    "openai": ChatGPTFlow(),
    "claude": ClaudeFlow(),
    "cursor": CursorFlow(),
    "claude-code": ClaudeCodeFlow(),
    "xai": GrokFlow(),
}


def get_flow(provider_id: str) -> AuthFlow:
    """The sign-in flow for a provider, derived from its capabilities."""
    from .discovery import normalize_provider_id

    pid = normalize_provider_id(provider_id)
    caps = get_capabilities(pid)
    if caps and caps.api_key_only:
        return ApiKeyOnlyFlow(pid)
    flow = _FLOWS.get(pid)
    if flow is not None:
        return flow
    return BrowserFlow(pid)
