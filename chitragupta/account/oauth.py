"""One OAuth flow, for every provider that can use a loopback redirect.

RFC 8252, *OAuth 2.0 for Native Apps*: the app opens the system browser, the
provider redirects back to a loopback address the app is listening on, and
**PKCE** is what makes that safe without a confidential secret. The app already
does exactly this for connectors (`connectors/google_auth.py` calls
`run_local_server(port=0)`), which is the precedent this follows.

**Nothing here names a provider.** Everything that differs — the endpoints, the
scopes, the issuer rule, which claims are the subject, the extra authorize
parameters — is a field on `providers.Provider`. That is the whole reason Apple
and Microsoft can be added as a registry entry and a client id rather than as a
branch in this file. `providers.py` carries the table.

**Which is also why Apple is not available.** `Provider.loopback` is False for
it: Apple requires a registered `https://` Return URL on a verified domain and
refuses loopback, so no amount of code here makes it work without a server. It
is declared so the screen can say that rather than leaving Apple mysteriously
absent.

**PKCE, and why each piece is there:**

* `code_verifier` — 32 random bytes, never leaves this process. The
  authorisation code is worthless without it, so a code intercepted on the
  loopback hop (another local process racing the browser) cannot be redeemed.
* `code_challenge` — its SHA-256, sent up front. **S256, never `plain`**:
  `plain` sends the verifier itself and buys nothing.
* `state` — compared on the way back, so a callback the app did not start is
  refused.
* `nonce` — bound into the ID token by the provider and checked in
  `tokens.verify`, so a token from an earlier sign-in cannot be replayed into a
  later one.

**No `client_secret` is ever sent.** An installed-app secret ships inside every
copy of the app, so the security is PKCE plus the loopback redirect — which is
what `docs/DISTRIBUTION.md` already records about the shipped Google client.
Sending the secret would imply a confidentiality a public download cannot have.

**One client id for Google today.** It reuses the connector's, which is why
Google sign-in works with nothing to register; the separation that matters to a
user (two consents, two token stores, two revocations) is unaffected.
`CHITRAGUPTA_ACCOUNT_CLIENT_ID` points at a dedicated client when there is one,
and Microsoft and Apple have settings of their own — see
`Provider.client_setting`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from ..config import get_settings
from ..log import get_logger
from . import providers, session, tokens
from .providers import Provider

log = get_logger(__name__)

#: How long the loopback listener waits before giving up. Generous, because a
#: Google sign-in can involve a password, 2FA and an account chooser — the HUD
#: uses 180s for provider sign-ins for the same reason.
TIMEOUT = 180.0

#: What the browser lands on. Plain, self-contained, and it closes nothing
#: automatically: a tab that vanishes leaves a user unsure whether it worked.
_DONE_PAGE = b"""<!doctype html><meta charset="utf-8">
<title>Signed in</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;margin:16vh auto;
max-width:28rem;padding:0 1.5rem;color:#2b2724}h1{font-size:1.25rem;margin:0 0 .5rem}
p{color:#6b645d;margin:0}</style>
<h1>You are signed in.</h1><p>You can close this tab and go back to Chitragupta.</p>
"""
_FAILED_PAGE = b"""<!doctype html><meta charset="utf-8">
<title>Sign-in failed</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;margin:16vh auto;
max-width:28rem;padding:0 1.5rem;color:#2b2724}h1{font-size:1.25rem;margin:0 0 .5rem}
p{color:#6b645d;margin:0}</style>
<h1>That did not work.</h1><p>Close this tab and try again in Chitragupta.</p>
"""


def client_id(provider: Provider) -> str:
    """The OAuth client to sign in with, for this provider.

    Its own setting if configured. Google additionally falls back to the client
    that already ships for connectors, which is why it works out of the box —
    see the module docstring on what that does and does not share. No other
    provider has a shipped client to fall back to, so an unconfigured one is
    simply unavailable rather than silently borrowing Google's.
    """
    configured = str(getattr(get_settings(), provider.client_setting, "") or "")
    if configured.strip():
        return configured.strip()
    if provider.id == providers.GOOGLE.id:
        return _shipped_client_id()
    return ""


def _shipped_client_id() -> str:
    from pathlib import Path
    for candidate in (
        Path(__file__).resolve().parent.parent / "data" / "google_client.json",
        get_settings().home / "google_client_secret.json",
    ):
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text())
        except Exception:
            continue
        for shape in ("installed", "web"):
            found = (data.get(shape) or {}).get("client_id")
            if found:
                return str(found)
    return ""


def is_available(provider: Provider) -> tuple[bool, str]:
    """Can this provider be used? And if not, what to tell the user.

    Two different "no"s, and they must not read alike. **No loopback** means a
    server is missing and nothing the user can do will help — that is Apple.
    **No client id** means this build was not configured, which is ours to fix.
    Checked before a button is drawn: a sign-in control that could only fail is
    the control-that-cannot-work `/CLAUDE.md` keeps warning about.
    """
    if not provider.loopback:
        return False, providers.NEEDS_SERVER.format(label=provider.label)
    if not client_id(provider):
        return False, providers.NEEDS_CLIENT.format(label=provider.label)
    return True, ""


def available() -> list[Provider]:
    """Every provider that can actually be used right now."""
    return [p for p in providers.REGISTRY.values() if is_available(p)[0]]


@dataclass
class Attempt:
    """One sign-in in progress.

    The verifier lives here and nowhere else — not on disk, not in a log, not in
    a URL. It exists for as long as the browser hop and then goes.
    """
    provider: Provider
    verifier: str
    challenge: str
    state: str
    nonce: str
    redirect_uri: str
    port: int
    started: float = field(default_factory=time.time)

    @property
    def authorize_url(self) -> str:
        params = {
            "client_id": client_id(self.provider),
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.provider.scopes),
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
            "state": self.state,
            "nonce": self.nonce,
        }
        # Whatever this provider needs on top — `prompt`, `access_type`,
        # `response_mode`. A field rather than a branch per provider.
        params.update(self.provider.extra_auth_params)
        return self.provider.auth_uri + "?" + urllib.parse.urlencode(params)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def begin(provider: Provider | None = None) -> Attempt:
    """Mint the PKCE material and a loopback redirect for one sign-in."""
    # Through the registry, not by naming one:  is an id, so adding a
    # provider or changing which is default touches  alone.
    provider = provider or providers.get(providers.DEFAULT)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    port = _free_port()
    return Attempt(
        provider=provider,
        verifier=verifier,
        challenge=challenge,
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        # `127.0.0.1`, not `localhost`: a native-app flow accepts the literal
        # loopback address on any port, and a name can be resolved somewhere
        # unexpected.
        redirect_uri=f"http://127.0.0.1:{port}",
        port=port,
    )


class _Callback(BaseHTTPRequestHandler):
    """Catches the one redirect, answers a page, and records the query."""

    received: dict[str, str] = {}

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Callback.received = {k: v[0] for k, v in query.items() if v}
        ok = "code" in _Callback.received
        body = _DONE_PAGE if ok else _FAILED_PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Silence. The default writes every request to stderr."""


def wait_for_code(attempt: Attempt, timeout: float = TIMEOUT) -> str:
    """Serve one request on the loopback port and return the code.

    Raises `TimeoutError` if the user never came back, and `ValueError` if the
    callback was not the one this attempt started — a mismatched `state` means
    the redirect belongs to somebody else's flow.
    """
    _Callback.received = {}
    server = HTTPServer(("127.0.0.1", attempt.port), _Callback)
    server.timeout = timeout

    def serve() -> None:
        server.handle_request()

    thread = threading.Thread(target=serve, daemon=True,
                              name="chitragupta-account-callback")
    thread.start()
    thread.join(timeout)
    server.server_close()

    got = dict(_Callback.received)
    _Callback.received = {}

    if not got:
        raise TimeoutError("the sign-in was not completed")
    if got.get("error"):
        # Google's own word for what went wrong — usually `access_denied`, which
        # is the user deciding rather than a failure.
        raise ValueError(got["error"])
    if got.get("state") != attempt.state:
        log.info("refused a sign-in callback whose state did not match")
        raise ValueError("that sign-in could not be verified")
    code = got.get("code")
    if not code:
        raise ValueError("that sign-in could not be verified")
    return code


def exchange(attempt: Attempt, code: str, *,
             link_to_existing: bool = False) -> session.Identity:
    """Trade the code for tokens, verify the ID token, and store the session.

    The verifier goes up here and proves the code belongs to this attempt. **No
    client secret is sent** — see the module docstring.
    """
    import httpx

    provider = attempt.provider
    response = httpx.post(provider.token_uri, timeout=30.0, data={
        "client_id": client_id(provider),
        "code": code,
        "code_verifier": attempt.verifier,
        "grant_type": "authorization_code",
        "redirect_uri": attempt.redirect_uri,
    })
    if response.status_code >= 400:
        log.info("the %s token exchange failed: %s", provider.id,
                 response.status_code)
        raise ValueError(f"{provider.label} refused that sign-in — try again")
    payload = response.json()

    id_token = payload.get("id_token")
    if not id_token:
        raise ValueError("that sign-in could not be verified")

    # The one place claims become trusted. Everything downstream takes claims,
    # never a raw token, so there is no second path that could skip this.
    claims = tokens.verify(id_token, provider=provider,
                           client_id=client_id(provider), nonce=attempt.nonce)

    return session.adopt(
        provider, claims, link_to_existing=link_to_existing,
        refresh_token=str(payload.get("refresh_token") or ""),
        access_token=str(payload.get("access_token") or ""),
        expires_in=float(payload.get("expires_in") or 0),
        scopes=str(payload.get("scope") or "").split(),
    )
