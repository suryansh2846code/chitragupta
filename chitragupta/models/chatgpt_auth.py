"""OAuth PKCE flow and token manager for ChatGPT subscriptions.

Sign-in runs the OpenAI OAuth PKCE flow against a loopback server on port 1455
(the redirect URI is registered, so the port is fixed — it cannot fall back):
1. Build a PKCE challenge and open the OpenAI consent page in the browser.
2. Exchange the returned authorization code for access/refresh/id tokens.
3. Decode identity + plan from the token claims and store tokens in Chitragupta's
   secret store, never in another application's files.
4. Record the connection so the catalog reflects it immediately.

An existing `~/.codex/auth.json` session is also detected, so a user already
signed in to the Codex CLI does not have to authorize a second time.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import secrets
import threading
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings
from ..log import suppressed
from .connections import ACCOUNT, ConnectionStatus, get_connection, save_connection

logger = logging.getLogger(__name__)

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# Who the authorization request says it is.
#
# This must match CLIENT_ID above. That client is the Codex CLI's public PKCE
# client — the sanctioned way to reach a ChatGPT subscription, the same shape as
# the Claude and Cursor backends — and its originator is `codex_cli_rs`.
#
# It used to read `opencode`, which is a different product's identifier. Sending
# another project's name to the vendor misreports who is calling, and leaves
# every Chitragupta user's sign-in breakable by a decision aimed at somebody else.
# An identifier is either the vendor's, or ours; it is never a third party's.
ORIGINATOR = "codex_cli_rs"
AUTH_BASE_URL = "https://auth.openai.com"
REDIRECT_PORT = 1455
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"
SCOPE = "openid profile email offline_access"


_SECRET_KEY_CHATGPT_TOKEN = "CHITRAGUPTA_CHATGPT_TOKEN"


def _token_storage_path() -> Path:
    return get_settings().home / "chatgpt_token.json"


def _load_stored_chatgpt_data() -> dict[str, Any] | None:
    """Retrieve Chitragupta-owned ChatGPT token payload from secure Keychain store."""
    settings = get_settings()
    raw = settings.get_secret(_SECRET_KEY_CHATGPT_TOKEN)
    if raw:
        with suppressed("return json.loads(raw)"):
            return json.loads(raw)
    # Auto-migrate legacy plaintext file to keychain if present
    legacy = settings.home / "chatgpt_token.json"
    if legacy.exists():
        with suppressed("data = json.loads(legacy.read_text()) …"):
            data = json.loads(legacy.read_text())
            settings.set_secret(_SECRET_KEY_CHATGPT_TOKEN, json.dumps(data))
            legacy.unlink(missing_ok=True)
            return data
    return None


def _save_stored_chatgpt_data(data: dict[str, Any] | None) -> None:
    """Persist Chitragupta-owned ChatGPT token payload into secure Keychain store."""
    settings = get_settings()
    if data is None:
        settings.set_secret(_SECRET_KEY_CHATGPT_TOKEN, None)
    else:
        settings.set_secret(_SECRET_KEY_CHATGPT_TOKEN, json.dumps(data))
    legacy = settings.home / "chatgpt_token.json"
    if legacy.exists():
        legacy.unlink(missing_ok=True)


def _codex_auth_path() -> Path:
    return Path.home() / ".codex/auth.json"


def _codex_models_cache_path() -> Path:
    return Path.home() / ".codex/models_cache.json"


def _decode_jwt_payload(jwt_token: str) -> dict[str, Any]:
    """Decode unverified JWT payload for identity extraction without third-party libs."""
    if not jwt_token or "." not in jwt_token:
        return {}
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        data = base64.urlsafe_b64decode(payload.encode("utf-8"))
        return json.loads(data.decode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse JWT payload: %s", exc)
        return {}


_CHATGPT_PLAN_LABELS: dict[str, str] = {
    "free": "ChatGPT Free",
    "go": "ChatGPT Go",
    "plus": "ChatGPT Plus",
    "pro": "ChatGPT Pro",
    "prolite": "ChatGPT Pro",
    "team": "ChatGPT Team",
    "self_serve_business_usage_based": "ChatGPT Business",
    "business": "ChatGPT Business",
    "enterprise_cbp_usage_based": "ChatGPT Enterprise",
    "enterprise": "ChatGPT Enterprise",
    "edu": "ChatGPT Education",
}

_USAGE_CACHE: dict[str, Any] = {}
_USAGE_CACHE_TIME: float = 0.0
_USAGE_CACHE_TTL: float = 30.0


def _format_chatgpt_plan(raw_plan: str | None) -> str:
    if not raw_plan:
        return "ChatGPT Free"
    clean = str(raw_plan).strip().lower()
    return _CHATGPT_PLAN_LABELS.get(clean, f"ChatGPT {clean.title()}")


def _identity_from_tokens(data: dict[str, Any]) -> tuple[str, str, str]:
    """Extract (plan_name, email, display_name) from stored tokens/claims."""
    tokens = data.get("tokens") or {}
    access_tok = tokens.get("access_token") or ""
    id_tok = tokens.get("id_token") or ""

    claims = _decode_jwt_payload(access_tok) or {}
    id_claims = _decode_jwt_payload(id_tok) or {}

    auth_info = claims.get("https://api.openai.com/auth") or id_claims.get("https://api.openai.com/auth") or {}
    raw_plan = auth_info.get("chatgpt_plan_type") or data.get("plan_type")

    profile_info = claims.get("https://api.openai.com/profile") or id_claims.get("https://api.openai.com/profile") or {}
    email = data.get("email") or profile_info.get("email") or claims.get("email") or id_claims.get("email") or ""
    name = data.get("name") or profile_info.get("name") or claims.get("name") or id_claims.get("name") or ""

    return _format_chatgpt_plan(raw_plan), email, name


# Model slugs each ChatGPT plan tier can run. Consulted ONLY when the Codex
# models cache is absent — the cache is the account's own answer and always wins.
# Single source of truth: both catalog discovery and inference read this.
_FREE_PLAN_SLUGS = {"gpt-5.6-terra", "gpt-5.6-luna", "gpt-reserve", "gpt-5.5", "codex-auto-review"}
_PLUS_PLAN_SLUGS = _FREE_PLAN_SLUGS | {"o3-mini"}
_PRO_PLAN_SLUGS = _PLUS_PLAN_SLUGS | {"gpt-6-astra", "gpt-5.6-sol"}


def _slugs_for_plan(plan: str | None) -> set[str]:
    """Model slugs a ChatGPT plan is expected to run, as a conservative fallback."""
    p = (plan or "").lower()
    if any(k in p for k in ("pro", "team", "business", "enterprise", "edu")):
        return set(_PRO_PLAN_SLUGS)
    if "plus" in p or "go" in p:
        return set(_PLUS_PLAN_SLUGS)
    return set(_FREE_PLAN_SLUGS)


def resolve_subscription_models() -> tuple[set[str], dict[str, dict[str, Any]], str]:
    """Resolve what the signed-in ChatGPT account can actually run.

    Returns (supported slugs, per-slug metadata, plan label). Prefers the Codex
    models cache written by the account itself; falls back to the plan tables
    above when it is missing.
    """
    supported: set[str] = set()
    meta: dict[str, dict[str, Any]] = {}

    cache_path = _codex_models_cache_path()
    if cache_path.exists():
        try:
            for m in json.loads(cache_path.read_text()).get("models", []):
                slug = m.get("slug")
                if not slug:
                    continue
                supported.add(slug)
                meta[slug] = {
                    "name": m.get("display_name") or slug.replace("-", " ").title(),
                    "desc": m.get("description") or "Codex agentic coding model",
                    "context_window": m.get("context_window", 272_000),
                }
        except Exception:
            logger.warning("Could not read Codex models cache at %s", cache_path)

    session = detect_chatgpt_local_session(fetch_usage=False)
    plan = (session and session.get("plan")) or "ChatGPT Free"

    if not supported:
        supported = _slugs_for_plan(plan)

    return supported, meta, plan


def get_chatgpt_subscription_usage(force_refresh: bool = False) -> dict[str, Any] | None:
    """Fetch live usage limits and rate limit windows from ChatGPT backend."""
    global _USAGE_CACHE, _USAGE_CACHE_TIME
    now = time.time()
    if not force_refresh and _USAGE_CACHE and (now - _USAGE_CACHE_TIME < _USAGE_CACHE_TTL):
        return _USAGE_CACHE

    token = get_chatgpt_access_token()
    if not token:
        return None

    account_id = None
    plan_from_jwt = "free"
    with suppressed("claims = _decode_jwt_payload(token) …"):
        claims = _decode_jwt_payload(token)
        auth_claims = claims.get("https://api.openai.com/auth") or {}
        account_id = auth_claims.get("chatgpt_account_id")
        plan_from_jwt = auth_claims.get("chatgpt_plan_type") or "free"

    try:
        import urllib.request
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "codex_cli_rs/0.153.4",
            "Accept": "application/json",
        }
        if account_id:
            headers["chatgpt-account-id"] = account_id

        req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                rate_limit = body.get("rate_limit") or {}
                primary = rate_limit.get("primary_window") or {}
                windows = []
                if primary:
                    duration_secs = primary.get("limit_window_seconds")
                    duration_mins = (duration_secs // 60) if duration_secs else None
                    if duration_mins is None:
                        label = "Primary limit"
                    elif duration_mins == 10080:
                        label = "Weekly limit"
                    elif duration_mins == 1440:
                        label = "Daily limit"
                    elif duration_mins % 1440 == 0:
                        label = f"{duration_mins // 1440}-day limit"
                    elif duration_mins % 60 == 0:
                        label = f"{duration_mins // 60}-hour limit"
                    else:
                        label = f"{duration_mins}-minute limit"

                    reset_at = primary.get("reset_at")
                    resets_at_ms = int(reset_at * 1000) if reset_at else None
                    windows.append({
                        "id": "primary",
                        "label": label,
                        "usedPercent": primary.get("used_percent", 0),
                        "resetsAt": resets_at_ms,
                    })

                usage_res = {
                    "state": "available",
                    "updatedAt": int(time.time() * 1000),
                    "windows": windows,
                    "plan": _format_chatgpt_plan(body.get("plan_type") or plan_from_jwt),
                    "planType": body.get("plan_type") or plan_from_jwt,
                }
                _USAGE_CACHE = usage_res
                _USAGE_CACHE_TIME = time.time()
                return usage_res
    except Exception as exc:
        logger.warning("ChatGPT live usage query failed: %s", exc)

    if _USAGE_CACHE:
        return _USAGE_CACHE
    return None


def detect_chatgpt_local_session(fetch_usage: bool = True) -> dict[str, Any] | None:
    """Detect existing ChatGPT / Codex authentication on this machine without credential theft."""
    # 1. First check Chitragupta's own securely stored token in Keychain
    stored = _load_stored_chatgpt_data()
    if stored:
        with suppressed("plan_name, email, name = _identity_from_tokens(stored) …"):
            plan_name, email, name = _identity_from_tokens(stored)
            if email:
                usage = get_chatgpt_subscription_usage() if fetch_usage else None
                if usage and usage.get("plan"):
                    plan_name = usage["plan"]
                return {
                    "source": "turnover",
                    "email": email,
                    "name": name or "ChatGPT User",
                    "plan": plan_name,
                    "usage": usage,
                    "has_token": True,
                }

    # 2. Check ~/.codex/auth.json for existing local account presence metadata (NEVER copy tokens)
    codex_auth = _codex_auth_path()
    if codex_auth.exists():
        with suppressed("data = json.loads(codex_auth.read_text()) …"):
            data = json.loads(codex_auth.read_text())
            plan_name, email, name = _identity_from_tokens(data)
            if email:
                return {
                    "source": "codex_cli",
                    "email": email,
                    "name": name or "ChatGPT User",
                    "plan": plan_name,
                    "usage": None,
                    "has_token": False,
                }

    return None


def adopt_local_chatgpt_session() -> tuple[bool, str, dict[str, Any]]:
    """Bind a detected ChatGPT / Codex session without copying its credentials."""
    info = detect_chatgpt_local_session(fetch_usage=False)
    if not info or not info.get("email"):
        return False, "No local ChatGPT session found on this computer", {}

    # Bind connection state without duplicating another app's secret tokens
    now = datetime.now(UTC).isoformat()
    conn = get_connection("openai")
    source = info.get("source")
    conn.auth_method = "cli" if source == "codex_cli" else "account"
    conn.email = info["email"]
    conn.account_display_name = info.get("name") or "ChatGPT User"
    conn.set_credential(ACCOUNT, ConnectionStatus.ACCOUNT_CONNECTED)
    conn.status_message = f"Connected via {source or 'ChatGPT'} ({info['email']})"
    conn.connected_at = conn.connected_at or now
    conn.last_verified_at = now
    save_connection(conn)

    return True, f"Connected {info['email']}", conn.to_dict()


class _AuthServerState:
    def __init__(self):
        self.lock = threading.Lock()
        self.server: http.server.HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.verifier: str = ""
        self.state: str = ""
        self.auth_url: str = ""
        self.status: str = "idle"  # idle | waiting | success | error
        self.error_message: str = ""
        self.connected_email: str = ""
        self.started_at: float = 0


_GLOBAL_AUTH_STATE = _AuthServerState()


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            req_path = parsed.path.rstrip("/")
            if req_path == "/cancel":
                with _GLOBAL_AUTH_STATE.lock:
                    _GLOBAL_AUTH_STATE.status = "error"
                    _GLOBAL_AUTH_STATE.error_message = "Sign-in cancelled by user"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Login cancelled")
                _stop_server_async()
                return

            if req_path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            error = qs.get("error", [None])[0]
            error_desc = qs.get("error_description", [error])[0]
            code = qs.get("code", [None])[0]
            state = qs.get("state", [None])[0]

            if error:
                with _GLOBAL_AUTH_STATE.lock:
                    _GLOBAL_AUTH_STATE.status = "error"
                    _GLOBAL_AUTH_STATE.error_message = error_desc or error
                self._render_response(
                    title="Chitragupta - Authorization Failed",
                    heading="✕ Authorization Failed",
                    message=error_desc or error,
                    is_error=True,
                )
                _stop_server_async()
                return

            with _GLOBAL_AUTH_STATE.lock:
                expected_state = _GLOBAL_AUTH_STATE.state
                verifier = _GLOBAL_AUTH_STATE.verifier

            if not code:
                self._render_response(
                    title="Chitragupta - Missing Code",
                    heading="✕ Missing Authorization Code",
                    message="No authorization code received from OpenAI.",
                    is_error=True,
                )
                _stop_server_async()
                return

            if expected_state and state != expected_state:
                self._render_response(
                    title="Chitragupta - Invalid State",
                    heading="✕ Security Verification Failed",
                    message="OAuth state mismatch. Please try again.",
                    is_error=True,
                )
                _stop_server_async()
                return

            # Perform token exchange
            try:
                token_resp = httpx.post(
                    f"{AUTH_BASE_URL}/oauth/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": REDIRECT_URI,
                        "client_id": CLIENT_ID,
                        "code_verifier": verifier,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30.0,
                )
                if token_resp.status_code != 200:
                    raise RuntimeError(
                        f"Token exchange returned status {token_resp.status_code}: {token_resp.text[:200]}"
                    )

                token_data = token_resp.json()
                id_token = token_data.get("id_token", "")
                claims = _decode_jwt_payload(id_token)
                email = claims.get("email") or "ChatGPT User"
                name = claims.get("name") or "ChatGPT Account"

                # Persist tokens securely in OS Keychain
                payload = {
                    "auth_mode": "chatgpt_subscription",
                    "tokens": token_data,
                    "email": email,
                    "name": name,
                    "last_refresh": datetime.now(UTC).isoformat(),
                }
                _save_stored_chatgpt_data(payload)

                # Update DB connection record
                now = datetime.now(UTC).isoformat()
                conn = get_connection("openai")
                conn.auth_method = "account"
                conn.email = email
                conn.account_display_name = name
                conn.set_credential(ACCOUNT, ConnectionStatus.ACCOUNT_CONNECTED)
                conn.status_message = f"Connected to ChatGPT ({email})"
                conn.connected_at = conn.connected_at or now
                conn.last_verified_at = now
                save_connection(conn)

                # Reported to a leaf, not to the registry. Whatever holds
                # credential-derived state has registered a clearer with
                # `cache.on_credentials_change`; an auth module does not need
                # to know which modules those are, and importing the top of the
                # package to find out is what put it in a cycle.
                with suppressed("dropping cached state after a ChatGPT sign-in"):
                    from .cache import credentials_changed
                    credentials_changed()

                with _GLOBAL_AUTH_STATE.lock:
                    _GLOBAL_AUTH_STATE.status = "success"
                    _GLOBAL_AUTH_STATE.connected_email = email

                self._render_response(
                    title="Chitragupta - Authorization Successful",
                    heading="✓ Authorization Successful",
                    message=f"Your ChatGPT account ({email}) is now connected to Chitragupta. You can close this tab and return to the app.",
                    is_error=False,
                )
            except Exception as exc:
                logger.exception("Error during ChatGPT token exchange")
                with _GLOBAL_AUTH_STATE.lock:
                    _GLOBAL_AUTH_STATE.status = "error"
                    _GLOBAL_AUTH_STATE.error_message = str(exc)
                self._render_response(
                    title="Chitragupta - Token Exchange Failed",
                    heading="✕ Authorization Error",
                    message=f"Failed to exchange token with OpenAI: {exc}",
                    is_error=True,
                )

            _stop_server_async()
        except Exception as top_exc:
            logger.exception("Unhandled error in ChatGPT OAuth callback handler")
            with suppressed("self._render_response("):
                self._render_response(
                    title="Chitragupta - Error",
                    heading="✕ Sign-In Processing Error",
                    message=str(top_exc),
                    is_error=True,
                )
            _stop_server_async()

    def _render_response(self, title: str, heading: str, message: str, is_error: bool = False):
        color = "#fc533a" if is_error else "#10b981"
        bg = "#111827"
        card_bg = "#1f2937"
        html = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>{title}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        display: flex;
        justify-content: center;
        align-items: center;
        min-height: 100vh;
        margin: 0;
        background: {bg};
        color: #f3f4f6;
      }}
      .container {{
        text-align: center;
        padding: 2.5rem;
        background: {card_bg};
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 16px;
        box-shadow: 0 20px 40px rgba(0,0,0,0.5);
        max-width: 440px;
      }}
      h1 {{
        color: {color};
        margin: 0 0 1rem 0;
        font-size: 1.5rem;
      }}
      p {{
        color: #9ca3af;
        font-size: 0.95rem;
        line-height: 1.5;
        margin-bottom: 1.5rem;
      }}
      .btn {{
        display: inline-block;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.15);
        color: #f3f4f6;
        padding: 8px 16px;
        border-radius: 8px;
        font-size: 0.9rem;
        text-decoration: none;
        cursor: pointer;
      }}
    </style>
  </head>
  <body>
    <div class="container">
      <h1>{heading}</h1>
      <p>{message}</p>
      <button class="btn" onclick="window.close()">Close Window</button>
    </div>
    <script>
      setTimeout(() => {{
        try {{ window.close(); }} catch (_) {{}}
      }}, 2500);
    </script>
  </body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def _stop_server_async():
    def _runner():
        time.sleep(1.0)
        with _GLOBAL_AUTH_STATE.lock:
            if _GLOBAL_AUTH_STATE.server:
                with suppressed("_GLOBAL_AUTH_STATE.server.shutdown() …"):
                    _GLOBAL_AUTH_STATE.server.shutdown()
                    _GLOBAL_AUTH_STATE.server.server_close()
                _GLOBAL_AUTH_STATE.server = None
                _GLOBAL_AUTH_STATE.thread = None

    threading.Thread(target=_runner, daemon=True).start()





def start_chatgpt_oauth_flow() -> tuple[bool, str, str]:
    """Start the native OAuth PKCE loopback flow for ChatGPT / OpenAI on port 1455."""
    with _GLOBAL_AUTH_STATE.lock:
        # Cleanly stop any existing server before starting a fresh one
        if _GLOBAL_AUTH_STATE.server:
            with suppressed("_GLOBAL_AUTH_STATE.server.shutdown() …"):
                _GLOBAL_AUTH_STATE.server.shutdown()
                _GLOBAL_AUTH_STATE.server.server_close()
            _GLOBAL_AUTH_STATE.server = None
            _GLOBAL_AUTH_STATE.thread = None

        verifier = "".join(
            secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
            for _ in range(43)
        )
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        state = secrets.token_hex(16)

        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": state,
            "originator": ORIGINATOR,
        }
        auth_url = f"{AUTH_BASE_URL}/oauth/authorize?{urllib.parse.urlencode(params)}"

        try:
            http.server.HTTPServer.allow_reuse_address = True
            server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _OAuthCallbackHandler)
        except OSError as exc:
            # The redirect URI is registered against this exact port, so there is
            # no fallback — another sign-in already holds it.
            logger.warning("Could not bind to port %s: %s", REDIRECT_PORT, exc)
            return False, auth_url, (
                f"Port {REDIRECT_PORT} is already in use — another ChatGPT "
                "sign-in is in progress (quit `codex login` and try again)."
            )

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        _GLOBAL_AUTH_STATE.server = server
        _GLOBAL_AUTH_STATE.thread = thread
        _GLOBAL_AUTH_STATE.verifier = verifier
        _GLOBAL_AUTH_STATE.state = state
        _GLOBAL_AUTH_STATE.auth_url = auth_url
        _GLOBAL_AUTH_STATE.status = "waiting"
        _GLOBAL_AUTH_STATE.started_at = time.time()
        _GLOBAL_AUTH_STATE.error_message = ""
        _GLOBAL_AUTH_STATE.connected_email = ""

        return True, auth_url, "Waiting for browser sign-in"


def disconnect() -> None:
    """Forget the stored ChatGPT credential and stop any in-flight sign-in."""
    _save_stored_chatgpt_data(None)
    _token_storage_path().unlink(missing_ok=True)
    _stop_server_async()


def get_oauth_flow_status() -> dict[str, Any]:
    """Check current status of ongoing OAuth sign-in."""
    with _GLOBAL_AUTH_STATE.lock:
        return {
            "status": _GLOBAL_AUTH_STATE.status,
            "error": _GLOBAL_AUTH_STATE.error_message,
            "email": _GLOBAL_AUTH_STATE.connected_email,
            "auth_url": _GLOBAL_AUTH_STATE.auth_url,
        }


def get_chatgpt_access_token() -> str | None:
    """Retrieve active ChatGPT access token from secure Keychain store or local session, refreshing if expired."""
    data = _load_stored_chatgpt_data()

    if not data:
        # Fall back to an existing Codex CLI session so a user already signed in
        # there does not have to authorize twice. Read-only: a refreshed token is
        # written to Chitragupta's own store, never back into ~/.codex.
        codex_auth = _codex_auth_path()
        if codex_auth.exists():
            with suppressed("d = json.loads(codex_auth.read_text()) …"):
                d = json.loads(codex_auth.read_text())
                if d.get("tokens", {}).get("access_token"):
                    data = d

    if not data:
        return None

    try:
        tokens = data.get("tokens", {})
        tok = tokens.get("access_token")
        if not tok:
            return None

        # Check expiration
        claims = _decode_jwt_payload(tok)
        exp = claims.get("exp", 0)
        if exp and time.time() > exp - 180:
            ref_tok = tokens.get("refresh_token")
            if ref_tok:
                try:
                    r = httpx.post(
                        f"{AUTH_BASE_URL}/oauth/token",
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": ref_tok,
                            "client_id": CLIENT_ID,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=15.0,
                    )
                    if r.status_code == 200:
                        new_toks = r.json()
                        data["tokens"] = new_toks
                        data["last_refresh"] = datetime.now(UTC).isoformat()
                        _save_stored_chatgpt_data(data)
                        return new_toks.get("access_token")
                except Exception as refresh_exc:
                    logger.warning("Failed to refresh ChatGPT token: %s", refresh_exc)

        return tok
    except Exception:
        return None


def chat_with_chatgpt_subscription(
    messages: list[Any],
    *,
    model: str | None = None,
    tools: list[Any] | None = None,
    timeout: float = 120.0,
) -> Any:
    """Execute a chat completion request through ChatGPT Subscription backend."""
    import uuid

    from .base import ChatResult, ToolCall

    token = get_chatgpt_access_token()
    if not token:
        return ChatResult(text="⚠️ ChatGPT account not connected or session expired. Please Sign in with ChatGPT in Models.")

    input_items = []
    for m in messages:
        if m.role in ("user", "system"):
            input_items.append({"role": m.role, "content": m.content})
        elif m.role == "assistant":
            if m.content:
                input_items.append({"role": "assistant", "content": m.content})
            for tc in m.tool_calls:
                args_str = json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else str(tc.arguments or "{}")
                input_items.append({
                    "type": "function_call",
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": args_str,
                })
        elif m.role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": m.tool_call_id or "",
                "output": m.content or "",
            })

    tools_payload = []
    if tools:
        for t in tools:
            tools_payload.append({
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            })

    # 1. What this account's plan can actually run
    supported_models, _meta, user_plan = resolve_subscription_models()

    # The RULES, not the module that also goes looking for credentials. An
    # auth module importing the catalog is an upward edge, and this one was
    # load-bearing in the cycle. See `entitlement_rules`.
    from .entitlement_rules import (
        evaluate_model_entitlement,
        get_best_unlocked_model,
    )

    # 2. Select and validate model
    req_model = (model or "").strip()
    if not req_model or req_model.lower() in ("auto", "default"):
        # Auto-pick best available model supported by user's plan
        chosen_model = get_best_unlocked_model(
            provider="openai",
            available_models=list(supported_models) if supported_models else ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
            is_connected=True,
            user_plan=user_plan,
        ) or "gpt-5.6-terra"
    else:
        locked, plan_req = evaluate_model_entitlement("openai", req_model, is_connected=True, user_plan=user_plan)
        if locked or (supported_models and req_model not in supported_models):
            # Only name a plan when the entitlement tables actually said one.
            # This defaulted to "Pro", so a model the account simply does not
            # list was reported as "requires Pro" — a specific, confident claim
            # nobody had checked. We know it is not on this plan; we do not
            # know which plan would have it.
            req_plan = plan_req
            display_avail = [m for m in sorted(supported_models) if not m.startswith("codex-auto")] if supported_models else ["gpt-5.6-terra", "gpt-5.6-luna"]
            avail_str = ", ".join(f"`{m}`" for m in display_avail)
            # Name the *other* way out, not just the one that failed. This path
            # is only reached when there is no OpenAI API key — a user who has
            # paid for API credits is told their ChatGPT plan is too small,
            # which is true and useless, because the thing they can act on is
            # the key they already have.
            needs = f" (requires **{req_plan}**)" if req_plan else ""
            return ChatResult(
                text=f"🔒 Model `{req_model}` is not supported on your "
                     f"**{user_plan}** plan{needs}.\n\n"
                     f"On this plan you can use: {avail_str}.\n\n"
                     "Pick one of those in the model selector — or, if you have "
                     "OpenAI **API credits**, add your API key in Models & "
                     "Accounts and this model will work. A ChatGPT plan and API "
                     "credits are billed separately and cover different models."
            )
        chosen_model = req_model

    payload = {
        "model": chosen_model,
        "store": False,
        "stream": True,
        "input": input_items,
    }
    if tools_payload:
        payload["tools"] = tools_payload

    from .streaming import raise_for_status

    full_text = ""
    calls = []
    try:
        with httpx.stream(
            "POST",
            "https://chatgpt.com/backend-api/codex/responses",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        ) as resp:
            raise_for_status(resp)
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                with suppressed("evt = json.loads(data_str) …"):
                    evt = json.loads(data_str)
                    etype = evt.get("type")
                    if etype == "response.output_text.delta":
                        full_text += evt.get("delta", "")
                    elif etype == "response.output_item.done":
                        item = evt.get("item", {})
                        if item.get("type") == "function_call":
                            cid = item.get("call_id") or item.get("id") or str(uuid.uuid4())
                            cname = item.get("name", "")
                            cargs = item.get("arguments", "{}")
                            try:
                                parsed_args = json.loads(cargs)
                            except Exception:
                                parsed_args = {}
                            calls.append(ToolCall(id=cid, name=cname, arguments=parsed_args))
    except httpx.HTTPStatusError as exc:
        from .errors import ErrorKind, classify_http

        err = classify_http("ChatGPT", exc.response.status_code, exc.response.text,
                            model=chosen_model)
        if err.kind is ErrorKind.AUTH:
            err.message = ("Your ChatGPT session expired. Sign in again in "
                           "Models & Accounts.")
        return ChatResult(text=err.as_reply())
    except Exception as exc:
        from .errors import classify_exception

        return ChatResult(text=classify_exception("ChatGPT", exc,
                                                  model=chosen_model).as_reply())

    return ChatResult(text=full_text, tool_calls=calls)
