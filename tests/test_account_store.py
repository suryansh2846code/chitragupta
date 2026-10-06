"""The account record, and adding a provider without touching the flow.

Two things this file is about, and the second is the one that was asked for:

1. **An account is not a session.** It has an id we minted, it holds more than
   one identity, and it survives a sign-out and a machine change.
2. **Apple and Microsoft are an entry in `providers.py` and a client id.**
   The tests here fail if the flow, the verifier or the routes ever learn a
   provider's name.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from chitragupta.account import oauth, providers, session, store
from chitragupta.config import get_settings
from chitragupta.core import exclusions

ROOT = pathlib.Path(__file__).resolve().parent.parent / "chitragupta"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    place = tmp_path / "home"
    place.mkdir()
    monkeypatch.setattr(get_settings(), "home", place)
    session.delete_account()
    yield place
    session.delete_account()


# ── making an account ───────────────────────────────────────────────────────

def test_a_first_sign_in_creates_an_account():
    account, created = store.adopt("google", "sub-1", email="a@example.com",
                                   name="A Person")
    assert created is True
    assert account.id
    assert account.created_at > 0
    assert account.providers == ["google"]
    assert account.as_dict()["email"] == "a@example.com"


def test_the_account_id_is_ours_not_the_providers():
    """An account outlives the identity it was created from, and the whole
    point of linking is that more than one can reach it."""
    account, _ = store.adopt("google", "sub-1")
    assert "sub-1" not in account.id


def test_signing_in_again_with_the_same_identity_does_not_make_a_second():
    first, created_first = store.adopt("google", "sub-1", email="a@example.com")
    second, created_second = store.adopt("google", "sub-1", email="a@example.com")
    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert len(second.identities) == 1


def test_a_changed_email_is_picked_up_without_making_a_new_account():
    """People change both their email and their name, and a stale one on screen
    looks like the wrong account."""
    store.adopt("google", "sub-1", email="old@example.com", name="Old")
    account, created = store.adopt("google", "sub-1", email="new@example.com",
                                   name="New")
    assert created is False
    assert account.as_dict()["email"] == "new@example.com"


def test_the_account_survives_a_sign_out():
    store.adopt("google", "sub-1", email="a@example.com")
    session.forget()
    assert store.exists() is True, "signing out deleted the account"
    assert session.signed_in() is False


def test_deleting_the_account_is_a_different_thing_from_signing_out():
    store.adopt("google", "sub-1")
    session.delete_account()
    assert store.exists() is False
    assert session.signed_in() is False


def test_a_corrupt_record_reads_as_no_account(home):
    (home / store.ACCOUNT_FILE).write_text("{ this is not json")
    assert store.load() is None
    assert store.exists() is False


# ── the linking rule, which is a security rule ──────────────────────────────

def test_an_unknown_identity_on_a_machine_with_an_account_is_refused():
    """Never auto-link by email: a provider that does not verify address
    ownership lets an attacker register the victim's address and inherit their
    account."""
    store.adopt("google", "sub-1", email="same@example.com")
    with pytest.raises(store.AlreadyAnAccountError) as raised:
        store.adopt("microsoft", "tenant:object", email="same@example.com")

    # Not a dead end — it carries which account exists and offers the way out.
    assert raised.value.account.providers == ["google"]
    assert "link" in str(raised.value).lower()


def test_linking_adds_a_provider_to_the_same_account():
    first, _ = store.adopt("google", "sub-1", email="a@example.com")
    account = store.link("microsoft", "tenant:object", email="a@work.example")

    assert account.id == first.id, "linking made a second account"
    assert sorted(account.providers) == ["google", "microsoft"]


def test_linking_the_same_identity_twice_changes_nothing():
    store.adopt("google", "sub-1")
    store.link("microsoft", "tenant:object")
    account = store.link("microsoft", "tenant:object")
    assert len(account.identities) == 2


def test_signing_in_with_a_linked_provider_finds_the_account():
    """The point of linking: either way in reaches the same account."""
    first, _ = store.adopt("google", "sub-1")
    store.link("microsoft", "tenant:object")

    account, created = store.adopt("microsoft", "tenant:object")
    assert created is False
    assert account.id == first.id


def test_unlinking_leaves_the_account_reachable():
    store.adopt("google", "sub-1")
    store.link("microsoft", "tenant:object")
    account = store.unlink("microsoft")
    assert account.providers == ["google"]


def test_unlinking_the_last_way_in_is_refused():
    """An account nobody can sign into is not an unlink, it is a deletion."""
    store.adopt("google", "sub-1")
    with pytest.raises(ValueError, match="only way into this account"):
        store.unlink("google")
    assert store.exists() is True


def test_no_subject_ever_reaches_a_page():
    """It is the one field stable enough to correlate people by."""
    store.adopt("google", "a-very-stable-subject", email="a@example.com")
    shown = json.dumps(store.load().as_dict())
    assert "a-very-stable-subject" not in shown
    assert "a@example.com" in shown


# ── the account travels in a backup; the credential does not ────────────────

def test_the_account_record_is_archived():
    """So a restored Mac is still the same account, and the user signs in again
    rather than starting over."""
    verdict = exclusions.classify(store.ACCOUNT_FILE)
    assert verdict.archived is True
    assert verdict.reason


def test_the_record_on_disk_holds_no_token():
    """Tier 0 never travels, and this file does."""
    store.adopt("google", "sub-1", email="a@example.com")
    raw = (get_settings().home / store.ACCOUNT_FILE).read_text().lower()
    for banned in ("refresh_token", "access_token", "bearer", "secret"):
        assert banned not in raw, f"{banned} reached an archived file"


# ── adding a provider is data, not code ─────────────────────────────────────

def test_all_three_providers_are_declared():
    """A provider absent from the registry is indistinguishable from one nobody
    thought about; one present and unavailable carries its reason."""
    assert sorted(providers.REGISTRY) == ["apple", "google", "microsoft"]


def test_google_is_usable_and_apple_is_not_and_the_reasons_differ():
    """Two different "no"s that must not read alike: Apple needs a server,
    which no user can fix; a missing client id is ours to configure."""
    google_ok, _ = oauth.is_available(providers.GOOGLE)
    apple_ok, apple_why = oauth.is_available(providers.APPLE)

    assert google_ok is True
    assert apple_ok is False
    assert "web address of ours" in apple_why
    assert "Google sign-in works today" in apple_why


def test_apple_is_unavailable_because_of_loopback_not_configuration(monkeypatch):
    """Giving Apple a client id must not make it look usable — the constraint
    is the redirect, and no setting changes it."""
    monkeypatch.setattr(get_settings(), "account_apple_client_id",
                        "a.real.looking.client.id")
    ok, why = oauth.is_available(providers.APPLE)
    assert ok is False
    assert "web address of ours" in why


def test_microsoft_is_buildable_and_only_needs_a_client_id(monkeypatch):
    """Microsoft allows the same loopback flow, so it is one setting away."""
    ok, why = oauth.is_available(providers.MICROSOFT)
    assert ok is False
    assert "no Microsoft sign-in client configured" in why

    monkeypatch.setattr(get_settings(), "account_microsoft_client_id", "ms-client")
    ok, _ = oauth.is_available(providers.MICROSOFT)
    assert ok is True, "Microsoft needed more than a client id"


def test_a_microsoft_sign_in_builds_a_microsoft_url(monkeypatch):
    monkeypatch.setattr(get_settings(), "account_microsoft_client_id", "ms-client")
    url = oauth.begin(providers.MICROSOFT).authorize_url
    assert url.startswith("https://login.microsoftonline.com/")
    assert "client_id=ms-client" in url
    assert "offline_access" in url, "no refresh token without this on Microsoft"
    assert "code_challenge_method=S256" in url


def test_microsoft_identity_is_the_tenant_and_the_object_together():
    """An `oid` is unique only within a tenant, so reading `sub` for every
    provider is how two tenants' users end up sharing an id."""
    claims = {"tid": "tenant-a", "oid": "object-1", "sub": "ignored"}
    assert providers.MICROSOFT.subject_of(claims) == "tenant-a:object-1"
    # And a different tenant with the same object id is a different person.
    other = {"tid": "tenant-b", "oid": "object-1"}
    assert providers.MICROSOFT.subject_of(other) != \
        providers.MICROSOFT.subject_of(claims)


def test_a_half_present_subject_is_no_subject():
    """Half an id would match the wrong person."""
    assert providers.MICROSOFT.subject_of({"tid": "tenant-a"}) == ""
    assert providers.MICROSOFT.subject_of({"oid": "object-1"}) == ""
    assert providers.GOOGLE.subject_of({}) == ""


def test_microsofts_issuer_is_matched_by_pattern_not_by_string():
    """Its `iss` carries the tenant id, so an exact-match set cannot express
    it — and "accept anything" is the same as not checking."""
    tenant = "https://login.microsoftonline.com/" \
             "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/v2.0"
    assert providers.MICROSOFT.accepts_issuer(tenant) is True
    assert providers.MICROSOFT.accepts_issuer("https://evil.example") is False
    assert providers.MICROSOFT.accepts_issuer(
        "https://login.microsoftonline.com/not-a-guid/v2.0") is False


@pytest.mark.parametrize("provider_id", ["google", "microsoft", "apple"])
def test_no_provider_accepts_a_missing_or_odd_issuer(provider_id):
    one = providers.get(provider_id)
    for junk in (None, "", 7, [], "https://accounts.google.com.evil.test"):
        assert one.accepts_issuer(junk) is False


def test_an_unknown_provider_is_refused_and_names_the_real_ones():
    with pytest.raises(KeyError) as raised:
        providers.get("facebook")
    message = str(raised.value)
    assert "google" in message and "apple" in message


def test_nothing_outside_providers_names_a_provider():
    """The ask: adding Apple or Microsoft must be an entry plus a client id.

    So the flow, the verifier, the session, the store and the routes may not
    contain a provider's name — a branch per provider is the
    `providerId === "x"` chain `/CLAUDE.md` forbids on render paths, one layer
    down. `providers.py` is where the names live, and `oauth.py` is allowed one
    mention: Google is the only provider with a shipped client to fall back to.
    """
    watched = {
        "account/oauth.py": 1,       # the shipped-client fallback
        "account/session.py": 0,
        "account/store.py": 0,
        "account/tokens.py": 0,
        "api/routes/account.py": 0,
    }
    for relative, allowed in watched.items():
        path = ROOT / relative
        tree = ast.parse(path.read_text())
        # Only real code: a name in a comment or docstring is explanation.
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                continue
            if isinstance(node, ast.Attribute) and node.attr in (
                    "GOOGLE", "APPLE", "MICROSOFT"):
                hits.append(node.attr)
        assert len(hits) <= allowed, (
            f"{relative} names {hits} — adding a provider should be an entry "
            f"in providers.py, not an edit here")
