"""Signing in to Chitragupta with Google.

The security of this lives almost entirely in `tokens.verify`, so that is what
most of this file is about. A real RSA key is generated per test and real tokens
are signed with it — the alternative is stubbing the verifier, which would test
nothing.

The property the rest of the file is about is the one CLAUDE.md cares about:
**nothing local may depend on a session.** An app that cannot open without
signing in is not the app this is being added to.
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from starlette.testclient import TestClient

from chitragupta.account import oauth, providers, session, tokens
from chitragupta.api.app import app
from chitragupta.config import get_settings

GOOGLE = providers.GOOGLE
CLIENT = "123-abc.apps.googleusercontent.com"
KID = "test-key-1"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, signing_key):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(get_settings(), "home", home)
    monkeypatch.setattr(get_settings(), "account_client_id", CLIENT)
    tokens.forget_cached_keys()
    session.delete_account()
    # Serve our own key as Google's, so a real signature is really checked.
    monkeypatch.setattr(tokens, "jwks",
                        lambda provider, force=False: {KID: _jwk(signing_key)})
    yield home
    session.delete_account()
    tokens.forget_cached_keys()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwk(key) -> dict:
    numbers = key.public_key().public_numbers()
    size = (numbers.n.bit_length() + 7) // 8
    return {
        "kid": KID, "alg": "RS256", "kty": "RSA",
        "n": _b64(numbers.n.to_bytes(size, "big")),
        "e": _b64(numbers.e.to_bytes(3, "big")),
    }


def _token(key, *, kid: str = KID, **claims) -> str:
    """A real RS256 JWT, signed with the test key."""
    now = time.time()
    body = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "sub": "google-subject-0001",
        "email": "someone@example.com",
        "email_verified": True,
        "name": "Someone",
        "iat": now,
        "exp": now + 3600,
    }
    body.update(claims)
    header = _b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    payload = _b64(json.dumps(body).encode())
    signature = key.sign(f"{header}.{payload}".encode(),
                         padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64(signature)}"


# ── the token is actually verified ──────────────────────────────────────────

def test_a_properly_signed_token_is_accepted(signing_key):
    claims = tokens.verify(_token(signing_key), provider=GOOGLE, client_id=CLIENT)
    assert claims["sub"] == "google-subject-0001"
    assert claims["email"] == "someone@example.com"


def test_a_token_signed_by_somebody_else_is_refused(signing_key):
    """The whole point. A JWT is only worth what its signature is worth."""
    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(tokens.TokenError):
        tokens.verify(_token(impostor), provider=GOOGLE, client_id=CLIENT)


def test_a_tampered_payload_is_refused(signing_key):
    good = _token(signing_key)
    header, payload, signature = good.split(".")
    body = json.loads(base64.urlsafe_b64decode(payload + "=="))
    body["email"] = "attacker@example.com"
    forged = f"{header}.{_b64(json.dumps(body).encode())}.{signature}"
    with pytest.raises(tokens.TokenError):
        tokens.verify(forged, provider=GOOGLE, client_id=CLIENT)


def test_a_token_for_a_different_app_is_refused(signing_key):
    """The subtle one: a *valid* Google token minted for somebody else's client
    must not sign a user in here."""
    other = _token(signing_key, aud="999-someone-else.apps.googleusercontent.com")
    with pytest.raises(tokens.TokenError):
        tokens.verify(other, provider=GOOGLE, client_id=CLIENT)


def test_a_token_from_another_issuer_is_refused(signing_key):
    with pytest.raises(tokens.TokenError):
        tokens.verify(_token(signing_key, iss="https://evil.example"),
                      provider=GOOGLE, client_id=CLIENT)


def test_an_expired_token_is_refused(signing_key):
    now = time.time()
    stale = _token(signing_key, iat=now - 7200, exp=now - 3600)
    with pytest.raises(tokens.TokenError):
        tokens.verify(stale, provider=GOOGLE, client_id=CLIENT)


def test_a_little_clock_skew_is_tolerated(signing_key):
    """A Mac a minute out must still be able to sign in."""
    now = time.time()
    just_expired = _token(signing_key, iat=now - 3600, exp=now - 30)
    assert tokens.verify(just_expired, provider=GOOGLE, client_id=CLIENT)["sub"]


def test_a_token_from_the_future_is_refused(signing_key):
    far = time.time() + 86400
    with pytest.raises(tokens.TokenError):
        tokens.verify(_token(signing_key, iat=far, exp=far + 3600),
                      provider=GOOGLE, client_id=CLIENT)


def test_a_replayed_nonce_is_refused(signing_key):
    """A token from an earlier sign-in cannot be pushed into a later one."""
    issued = _token(signing_key, nonce="the-first-attempt")
    assert tokens.verify(issued, provider=GOOGLE, client_id=CLIENT, nonce="the-first-attempt")
    with pytest.raises(tokens.TokenError):
        tokens.verify(issued, provider=GOOGLE, client_id=CLIENT, nonce="a-later-attempt")


@pytest.mark.parametrize("junk", ["", "not.a.jwt", "a.b", "a.b.c.d",
                                  "...", "eyJhbGciOiJSUzI1NiJ9"])
def test_rubbish_is_refused_without_raising_something_else(signing_key, junk):
    with pytest.raises(tokens.TokenError):
        tokens.verify(junk, provider=GOOGLE, client_id=CLIENT)


def test_an_unknown_key_id_is_refused(signing_key):
    with pytest.raises(tokens.TokenError):
        tokens.verify(_token(signing_key, kid="some-other-key"),
                      provider=GOOGLE, client_id=CLIENT)


def test_no_client_id_means_nothing_verifies(signing_key):
    """A build with no client configured must not accept everything."""
    with pytest.raises(tokens.TokenError):
        tokens.verify(_token(signing_key), provider=GOOGLE, client_id="")


# ── PKCE ────────────────────────────────────────────────────────────────────

def test_the_challenge_is_the_sha256_of_the_verifier():
    import hashlib
    attempt = oauth.begin()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(attempt.verifier.encode()).digest()).rstrip(b"=").decode()
    assert attempt.challenge == expected


def test_every_attempt_is_unique():
    """A reused state or nonce defeats the thing it is there for."""
    a, b = oauth.begin(), oauth.begin()
    assert a.verifier != b.verifier
    assert a.state != b.state
    assert a.nonce != b.nonce


def test_the_authorize_url_asks_for_s256_and_a_loopback_redirect():
    url = oauth.begin().authorize_url
    assert "code_challenge_method=S256" in url, "plain PKCE buys nothing"
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A" in url
    assert "response_type=code" in url
    assert "access_type=offline" in url, "no refresh token without this"
    assert "prompt=select_account" in url


def test_the_verifier_never_appears_in_the_url():
    """It is the one value that must not travel. Only its hash does."""
    attempt = oauth.begin()
    assert attempt.verifier not in attempt.authorize_url


# ── scopes: this is not the Gmail connector ─────────────────────────────────

def test_only_identity_scopes_are_requested():
    """Adding a data scope here is how this becomes the Gmail connector by
    accident. A first-run screen asking to read somebody's mail is the thing
    they quit over."""
    assert GOOGLE.scopes == ("openid", "email", "profile")
    url = oauth.begin().authorize_url
    for forbidden in ("gmail", "drive", "calendar", "contacts", "photos"):
        assert forbidden not in url, f"{forbidden} scope leaked into sign-in"


def _fake_google(monkeypatch, signing_key, attempt, **claims) -> dict:
    """Stand in for Google's token endpoint, and record what was sent.

    The ID token carries the attempt's own nonce, because `tokens.verify`
    checks it — the first version of this helper omitted it and the exchange
    was correctly refused, which is the nonce doing its job.
    """
    sent: dict = {}

    class _Reply:
        status_code = 200

        @staticmethod
        def json():
            return {"id_token": _token(signing_key, nonce=attempt.nonce,
                                       **claims),
                    "access_token": "at", "refresh_token": "rt",
                    "expires_in": 3600, "scope": "openid email profile"}

    def fake_post(url, timeout=None, data=None):
        sent.update(data or {})
        return _Reply()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def test_the_token_exchange_sends_what_google_requires(monkeypatch, signing_key):
    """**This test used to assert the opposite, and the opposite was broken.**

    It read "no client_secret is ever sent", reasoning that a secret shipped
    inside every copy of an app is not confidential and that sending it would
    imply otherwise. The first half is true and is why PKCE carries the
    security here. The second half confused *not confidential* with *not
    required*: measured against Google's live endpoint with a deliberately
    invalid code, which is enough to tell the two apart —

        without it -> invalid_request  "client_secret is missing."
        with it    -> invalid_grant    "Bad Request"   (the code, as expected)

    — so every real sign-in failed at the exchange while this test stayed
    green. No test could have found it; a real sign-in did.
    """
    attempt = oauth.begin()
    sent = _fake_google(monkeypatch, signing_key, attempt)

    oauth.exchange(attempt, "an-auth-code")
    assert sent["client_secret"], "Google refuses an exchange without it"
    assert sent["code_verifier"] == attempt.verifier, "PKCE is still the security"
    assert sent["grant_type"] == "authorization_code"
    assert sent["redirect_uri"] == attempt.redirect_uri


def test_a_provider_that_does_not_want_a_secret_is_not_sent_one():
    """Microsoft's public-client flow genuinely omits it, so the field is a
    provider's declaration rather than something every exchange does."""
    assert providers.MICROSOFT.sends_client_secret is False
    assert oauth.client_secret(providers.MICROSOFT) == ""
    # And Google's is only read because Google asked for it.
    assert providers.GOOGLE.sends_client_secret is True
    assert oauth.client_secret(providers.GOOGLE)


def test_the_secret_never_reaches_the_authorize_url():
    """It belongs in the back-channel POST and nowhere a browser can see."""
    attempt = oauth.begin()
    secret = oauth.client_secret(attempt.provider)
    assert secret, "this test is vacuous without one"
    url = attempt.authorize_url
    assert secret not in url
    assert "client_secret" not in url


def test_a_full_exchange_signs_the_user_in(monkeypatch, signing_key):
    attempt = oauth.begin()
    _fake_google(monkeypatch, signing_key, attempt, email="who@example.com")

    who = oauth.exchange(attempt, "an-auth-code")
    assert who.email == "who@example.com"
    assert session.signed_in() is True


def test_an_exchange_whose_token_carries_the_wrong_nonce_is_refused(
        monkeypatch, signing_key):
    """A token minted for an earlier attempt must not complete a later one."""
    earlier = oauth.begin()
    later = oauth.begin()
    _fake_google(monkeypatch, signing_key, earlier)   # token for the OLD attempt

    with pytest.raises(tokens.TokenError):
        oauth.exchange(later, "an-auth-code")
    assert session.signed_in() is False


def test_a_refused_exchange_says_so_without_signing_anybody_in(monkeypatch):
    class _Refused:
        status_code = 400

        @staticmethod
        def json():
            return {"error": "invalid_grant"}

    import httpx
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **kw: _Refused())
    with pytest.raises(ValueError, match="refused that sign-in"):
        oauth.exchange(oauth.begin(), "a-stale-code")
    assert session.signed_in() is False


# ── the session ─────────────────────────────────────────────────────────────

def test_signing_in_stores_an_identity(signing_key):
    claims = tokens.verify(_token(signing_key), provider=GOOGLE, client_id=CLIENT)
    who = session.adopt(GOOGLE, claims, refresh_token="rt")
    assert who.subject == "google-subject-0001"
    assert session.signed_in() is True
    assert session.who().email == "someone@example.com"


def test_identity_is_keyed_on_the_subject_not_the_email(signing_key):
    """An email can be let go of and reissued; matching on one is how an account
    is taken over. Google's `sub` is opaque and permanent."""
    first = session.adopt(
        GOOGLE, tokens.verify(_token(signing_key, email="old@example.com"),
                      provider=GOOGLE, client_id=CLIENT))
    second = session.adopt(
        GOOGLE, tokens.verify(_token(signing_key, email="new@example.com"),
                      provider=GOOGLE, client_id=CLIENT))
    assert first.subject == second.subject
    assert session.who().email == "new@example.com"


def test_what_a_page_is_told_carries_no_token_and_no_subject(signing_key):
    session.adopt(GOOGLE, tokens.verify(_token(signing_key), provider=GOOGLE, client_id=CLIENT),
                  refresh_token="a-live-refresh-token", access_token="at")
    shown = session.who().as_dict()
    blob = json.dumps(shown)
    assert "a-live-refresh-token" not in blob
    assert "google-subject-0001" not in blob, \
        "the subject is the one field stable enough to correlate people by"
    assert shown["email"] == "someone@example.com"


def test_signing_out_leaves_the_gmail_connector_alone(signing_key, isolated):
    """CLAUDE.md: credentials are independent. A user signing out of the app has
    not asked to disconnect their mail."""
    connector_token = isolated / "google_token.json"
    connector_token.write_text('{"refresh_token": "the connector token"}')
    session.adopt(GOOGLE, tokens.verify(_token(signing_key), provider=GOOGLE, client_id=CLIENT),
                  refresh_token="rt")

    session.forget()

    assert session.signed_in() is False
    assert connector_token.exists(), "signing out deleted the connector token"
    assert "the connector token" in connector_token.read_text()


def test_the_device_id_is_stable_and_not_a_hardware_serial():
    first = session.device_id()
    assert first and first == session.device_id()
    # Nothing derived from the machine: a hardware id follows a person across
    # reinstalls and across apps, which is tracking rather than licensing.
    import platform
    assert platform.node() not in first
    assert platform.machine() not in first


# ── it is never required ────────────────────────────────────────────────────

def test_nothing_local_consults_a_session():
    """The invariant: *first launch, no accounts — it must still open.*

    Asserted by reading the source rather than by hoping, because the failure
    is somebody adding one `if signed_in()` to a turn and nobody noticing until
    an offline user cannot chat.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "chitragupta"
    allowed = {"account", "api"}
    offenders = []
    for path in root.rglob("*.py"):
        top = path.relative_to(root).parts[0]
        if top in allowed:
            continue
        text = path.read_text()
        for marker in ("account.signed_in", "account.who",
                       "from .account import", "from ..account import"):
            if marker in text:
                offenders.append(f"{path.relative_to(root)}: {marker}")
    assert not offenders, (
        "a local feature reached for the account session: " + "; ".join(offenders))


def test_state_says_plainly_that_the_limit_is_not_enforced():
    """One Mac per licence is the decision; there is no server to enforce it.
    A page claiming otherwise would be the dishonest version of this."""
    state = session.state()
    assert state["devices_per_licence"] == 1
    assert state["enforced"] is False
    assert state["skippable"] is True


def test_a_build_with_no_client_says_so_rather_than_offering_a_dead_button(
        monkeypatch):
    monkeypatch.setattr(get_settings(), "account_client_id", "")
    monkeypatch.setattr(oauth, "_shipped", lambda field: "")
    ok, why = oauth.is_available(GOOGLE)
    assert ok is False
    assert "not available" in why


# ── the endpoints ───────────────────────────────────────────────────────────

def test_state_is_readable_before_anybody_signs_in(client):
    body = client.get("/api/account/state").json()
    assert body["signed_in"] is False
    assert body["user"] is None
    assert body["signing_in_optional"] is True
    assert body["device_id"]


def test_state_says_what_signing_in_asks_for(client):
    """The separation from the Gmail connector is the thing a user has to be
    able to see, so the API says it rather than leaving it to a doc."""
    body = client.get("/api/account/state").json()
    google_row = next(r for r in body["providers"] if r["id"] == "google")
    assert google_row["scopes"] == ["openid", "email", "profile"]
    assert "name and email" in body["asks_for"]
    for forbidden in ("gmail", "drive", "calendar"):
        assert forbidden not in json.dumps(body).lower()


def test_beginning_a_signin_returns_a_url_and_keeps_the_verifier(client):
    body = client.post("/api/account/signin/begin").json()
    assert body["url"].startswith("https://accounts.google.com/")
    assert "code_challenge_method=S256" in body["url"]
    # The verifier is the one value that must not travel.
    assert "code_verifier" not in body["url"]
    assert "verifier" not in json.dumps(body)


def test_finishing_without_beginning_is_a_conflict(client):
    client.post("/api/account/signin/cancel")
    assert client.post("/api/account/signin/finish").status_code == 409


def test_cancelling_drops_the_attempt(client):
    """CLAUDE.md: anything the user starts, they can stop."""
    client.post("/api/account/signin/begin")
    assert client.post("/api/account/signin/cancel").json()["cancelled"] is True
    assert client.post("/api/account/signin/finish").status_code == 409


def test_a_build_with_no_client_refuses_to_begin(client, monkeypatch):
    """Rather than opening a browser at a URL that cannot work."""
    monkeypatch.setattr(get_settings(), "account_client_id", "")
    monkeypatch.setattr(oauth, "_shipped", lambda field: "")
    refused = client.post("/api/account/signin/begin", json={"provider": "google"})
    assert refused.status_code == 400
    assert "not available" in refused.json()["detail"]

    rows = client.get("/api/account/state").json()["providers"]
    google_row = next(r for r in rows if r["id"] == "google")
    assert google_row["available"] is False


def test_signing_out_is_safe_when_nobody_is_signed_in(client):
    body = client.post("/api/account/signout").json()
    assert body["signed_in"] is False


def test_signing_out_through_the_api_clears_the_session(client, signing_key):
    session.adopt(GOOGLE, tokens.verify(_token(signing_key), provider=GOOGLE, client_id=CLIENT),
                  refresh_token="rt")
    assert client.get("/api/account/state").json()["signed_in"] is True

    body = client.post("/api/account/signout").json()
    assert body["signed_in"] is False
    assert body["user"] is None
