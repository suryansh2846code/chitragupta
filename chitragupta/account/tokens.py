"""Verifying an ID token, properly — for any provider.

An ID token is a JWT Google signed. Reading its payload is one base64 decode and
tells you nothing trustworthy; **verifying** it means checking the signature
against Google's published keys and then checking four claims. Both halves
matter, and the app already contains a cautionary example of doing only the
first: `models/chatgpt_auth._decode_jwt_payload` is honestly named
*"unverified"* because it is for display, and reusing it here would mean
accepting a token anybody could mint.

What is checked, and what each one stops:

| check | what it stops |
|---|---|
| RS256 signature against the JWKS | a forged token |
| `iss` | a token from another issuer entirely |
| `aud` == our client id | a valid Google token minted for a *different app* |
| `exp` / `iat` | a token that has expired, or is from the future |
| `nonce` | a token replayed from an earlier sign-in |

The `aud` one is the subtle one: without it, any app's Google token would sign
somebody in here, including one issued to an attacker's own client.

**No new dependency.** RS256 is `cryptography`, which became a base dependency
for encrypted backups — so verification costs nothing extra. The JWKS is fetched
and cached **per provider**, because every issuer rotates keys and a signature
that fails against a stale cache must be retried against a fresh one before it
is called invalid.

**Nothing here knows the name of a provider.** The issuer rule, the audience and
the discovery URL all arrive as a `Provider` from `providers.py`, so adding
Apple or Microsoft adds no branch to this file. Microsoft is why
`accepts_issuer` is a method rather than a set membership test: its `iss`
carries the tenant id.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from ..log import get_logger
from .providers import Provider

log = get_logger(__name__)

#: Seconds of clock skew tolerated either way. A Mac whose clock is a minute
#: fast must not be unable to sign in.
LEEWAY = 120

#: How long a fetched JWKS is trusted before being re-fetched.
JWKS_TTL = 3600.0

TIMEOUT = 10.0

#: `{provider id: {"at": when, "keys": {kid: jwk}}}`. Per provider, because two
#: issuers' key sets must never be able to verify each other's tokens.
_jwks_cache: dict[str, dict[str, Any]] = {}


class TokenError(Exception):
    """An ID token that cannot be trusted.

    One exception for every reason, deliberately: the caller shows "that sign-in
    could not be verified" either way, and distinguishing "expired" from "wrong
    audience" in a user-visible message tells an attacker which part to fix.
    The specific cause is logged.
    """


def _b64url(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:
        raise TokenError("that sign-in could not be verified") from exc


def _unverified_header(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("that sign-in could not be verified")
    try:
        return json.loads(_b64url(parts[0]))
    except Exception as exc:
        raise TokenError("that sign-in could not be verified") from exc


def jwks(provider: Provider, force: bool = False) -> dict[str, Any]:
    """One provider's signing keys, by `kid`. Cached for `JWKS_TTL`.

    The JWKS URI is read out of the provider's discovery document rather than
    hardcoded, because that indirection is the documented way every issuer is
    allowed to move its keys.
    """
    now = time.time()
    cached = _jwks_cache.get(provider.id)
    if not force and cached and cached["keys"] and now - cached["at"] < JWKS_TTL:
        return dict(cached["keys"])

    import httpx

    discovery = httpx.get(provider.discovery_url, timeout=TIMEOUT).json()
    uri = discovery.get("jwks_uri")
    if not uri:
        raise TokenError("that sign-in could not be verified")
    found = httpx.get(uri, timeout=TIMEOUT).json()
    keys = {k["kid"]: k for k in found.get("keys", []) if k.get("kid")}
    if not keys:
        raise TokenError("that sign-in could not be verified")
    _jwks_cache[provider.id] = {"at": now, "keys": keys}
    return dict(keys)


def _public_key(jwk: dict[str, Any]):
    from cryptography.hazmat.primitives.asymmetric import rsa

    def number(field: str) -> int:
        return int.from_bytes(_b64url(jwk[field]), "big")

    try:
        return rsa.RSAPublicNumbers(e=number("e"), n=number("n")).public_key()
    except Exception as exc:
        raise TokenError("that sign-in could not be verified") from exc


def _check_signature(token: str, jwk: dict[str, Any]) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    if jwk.get("alg") not in (None, "RS256"):
        raise TokenError("that sign-in could not be verified")
    header, payload, signature = token.split(".")
    try:
        _public_key(jwk).verify(
            _b64url(signature), f"{header}.{payload}".encode(),
            padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise TokenError("that sign-in could not be verified") from exc


def verify(token: str, *, provider: Provider, client_id: str,
           nonce: str | None = None,
           now: float | None = None) -> dict[str, Any]:
    """The token's claims, or `TokenError`. Never returns unverified claims.

    `nonce` is compared when given — the caller generated it, so a token from an
    earlier sign-in cannot be replayed into a later one.
    """
    if not token or not client_id:
        raise TokenError("that sign-in could not be verified")

    kid = str(_unverified_header(token).get("kid") or "")
    if not kid:
        raise TokenError("that sign-in could not be verified")
    keys = jwks(provider)
    jwk = keys.get(kid)
    if jwk is None:
        # Every issuer rotates keys. A miss is a stale cache far more often
        # than a forgery, so refetch once before calling the token invalid.
        keys = jwks(provider, force=True)
        jwk = keys.get(kid)
    if jwk is None:
        log.info("no %s signing key matched kid=%r", provider.id, kid)
        raise TokenError("that sign-in could not be verified")

    _check_signature(token, jwk)

    try:
        claims: dict[str, Any] = json.loads(_b64url(token.split(".")[1]))
    except Exception as exc:
        raise TokenError("that sign-in could not be verified") from exc

    moment = now if now is not None else time.time()

    # The provider owns this rule, because Microsoft's issuer carries a tenant
    # id and cannot be an exact match. Neither matching is a refusal — there is
    # no "accept anything" branch.
    if not provider.accepts_issuer(claims.get("iss")):
        log.info("rejected an ID token from iss=%r for %s",
                 claims.get("iss"), provider.id)
        raise TokenError("that sign-in could not be verified")

    # The subtle one. Without it, a valid token from this very issuer, minted
    # for somebody else's app, would sign a user in here.
    audience = claims.get("aud")
    if audience != client_id:
        log.info("rejected an ID token for a different audience")
        raise TokenError("that sign-in could not be verified")

    try:
        expires = float(claims["exp"])
        issued = float(claims.get("iat", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("that sign-in could not be verified") from exc
    if moment > expires + LEEWAY:
        log.info("rejected an expired ID token")
        raise TokenError("that sign-in has expired — try again")
    if issued and issued > moment + LEEWAY:
        log.info("rejected an ID token issued in the future")
        raise TokenError("that sign-in could not be verified")

    if nonce is not None and claims.get("nonce") != nonce:
        log.info("rejected an ID token whose nonce did not match")
        raise TokenError("that sign-in could not be verified")

    # Google sets this false for an unverified address. Identity is keyed on
    # `sub`, never on email, so this is recorded rather than enforced — but a
    # caller that ever matches accounts by email must read it.
    claims.setdefault("email_verified", False)
    return claims


def forget_cached_keys() -> None:
    """Drop every provider's JWKS cache. For tests, and a failure worth retrying."""
    _jwks_cache.clear()
