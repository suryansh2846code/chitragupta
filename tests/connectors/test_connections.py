"""One provider, several accounts — and a credential lifecycle that fits.

`connector_state` is `PRIMARY KEY (connector)` and `google_auth._token_path()`
is one file, so `provider == account` was baked into storage and no amount of
care above it could help: a user with a personal and a work Gmail had one
Gmail, whichever they signed into last.

The two transitions worth their own tests are the ones that are easy to
collapse and expensive to get wrong:

* `EXPIRED` is ours to fix silently; `REAUTH_REQUIRED` is the user's. Merging
  them means either bothering the user for something automatic, or retrying
  something that will never work — and the second is the loop that locks
  accounts out.
* **A 403 never triggers a re-auth.** A scope problem survives signing in
  again, so the OAuth round trip teaches the user the app is broken.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import connections
from chitragupta.connectors.connections import AuthState
from chitragupta.connectors.errors import classify_http


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from chitragupta.config import get_settings
    from chitragupta.connectors import db

    monkeypatch.setattr(get_settings(), "home", tmp_path, raising=False)
    db.reset_for_tests()
    yield
    db.reset_for_tests()


# ── identity ──────────────────────────────────────────────────────────────


def test_one_provider_can_hold_several_accounts():
    """The whole reason this table exists."""
    personal = connections.ensure("gmail", account="me@personal.test")
    work = connections.ensure("gmail", account="me@work.test")

    assert personal.id != work.id
    assert {c.account for c in connections.for_connector("gmail")} == \
        {"me@personal.test", "me@work.test"}


def test_the_same_account_is_the_same_connection():
    """`ensure` is called at the top of every sync, so it has to be
    idempotent — otherwise a row is created per pass."""
    first = connections.ensure("gmail", account="me@work.test")
    again = connections.ensure("gmail", account="me@work.test")

    assert first.id == again.id
    assert len(connections.for_connector("gmail")) == 1


def test_the_existing_single_account_connectors_get_exactly_one_row():
    """An empty account is the single-account case, which is what every
    shipped connector is today."""
    for _ in range(3):
        connections.ensure("apple_mail")

    assert len(connections.for_connector("apple_mail")) == 1


def test_the_key_is_not_the_account_address():
    """An address can be renamed at the vendor, and a key that changes
    underneath the rows pointing at it is not a key."""
    made = connections.ensure("gmail", account="me@work.test")

    assert "me@work.test" not in made.id
    assert made.id.startswith("gmail:")


def test_a_connection_is_named_by_the_user_first():
    """Their word for it beats ours, and an internal id is never shown."""
    made = connections.ensure("gmail", account="me@work.test", label="Work")

    assert made.display == "Work"
    assert connections.ensure("gmail", account="x@y.test").display == "x@y.test"


# ── the lifecycle ─────────────────────────────────────────────────────────


def test_a_new_connection_starts_disconnected():
    """The resting state. Not "connected until proven otherwise" — detection
    is not consent, and a row that claims connected with no credential is the
    bug `/CLAUDE.md` names."""
    assert connections.ensure("slack").auth_state is AuthState.DISCONNECTED
    assert not connections.ensure("slack").runnable


@pytest.mark.parametrize("state,runnable", [
    (AuthState.AUTHENTICATED, True),
    (AuthState.EXPIRED, True),          # ours to fix; a refresh happens inline
    (AuthState.CONNECTING, False),
    (AuthState.REAUTH_REQUIRED, False),
    (AuthState.REVOKED, False),
    (AuthState.ERROR, False),
    (AuthState.DISCONNECTED, False),
])
def test_only_a_live_credential_may_sync(state, runnable):
    """A connector that keeps syncing against a dead credential produces one
    identical error every thirty minutes and turns a fixable problem into
    background noise."""
    made = connections.ensure("gmail")
    moved = connections.transition(made.id, state)

    assert moved is not None and moved.runnable is runnable


def test_expired_is_not_the_same_as_needing_the_user():
    """An expired access token with a live refresh token is ours to fix."""
    made = connections.ensure("gmail")

    expired = connections.transition(made.id, AuthState.EXPIRED)
    assert expired is not None and not expired.needs_the_user

    stuck = connections.transition(made.id, AuthState.REAUTH_REQUIRED)
    assert stuck is not None and stuck.needs_the_user


def test_every_state_has_something_to_say():
    """A status word alone is a diagnosis with no treatment."""
    for state in AuthState:
        assert connections.SENTENCE[state].strip()


# ── what a failure does to a credential ───────────────────────────────────


def test_a_401_moves_the_connection_to_needing_the_user():
    made = connections.ensure("gmail")
    connections.transition(made.id, AuthState.AUTHENTICATED)

    connections.on_error(made.id, classify_http("gmail", 401))

    after = connections.get(made.id)
    assert after is not None and after.auth_state is AuthState.REAUTH_REQUIRED


def test_a_403_leaves_the_credential_alone():
    """Signing in again produces the same credential with the same
    permissions. Offering the loop teaches the user the app is broken."""
    made = connections.ensure("gdrive")
    connections.transition(made.id, AuthState.AUTHENTICATED)

    connections.on_error(made.id, classify_http("gdrive", 403))

    after = connections.get(made.id)
    assert after is not None and after.auth_state is AuthState.AUTHENTICATED


@pytest.mark.parametrize("status", [429, 500, 503, 404])
def test_a_failure_that_is_not_about_the_credential_changes_nothing(status):
    made = connections.ensure("slack")
    connections.transition(made.id, AuthState.AUTHENTICATED)

    connections.on_error(made.id, classify_http("slack", status))

    after = connections.get(made.id)
    assert after is not None and after.auth_state is AuthState.AUTHENTICATED


# ── scopes ────────────────────────────────────────────────────────────────


def test_a_missing_scope_is_reported():
    made = connections.ensure("gmail")
    moved = connections.transition(made.id, AuthState.AUTHENTICATED,
                                   scopes=["gmail.readonly"])

    assert moved is not None
    assert moved.missing_scopes(("gmail.readonly", "gmail.send")) == \
        ["gmail.send"]


def test_an_unknown_scope_set_reports_nothing_missing():
    """Empty means *we do not know*, not *none*. Guessing puts a warning on a
    working connector, which is a control that cannot work."""
    made = connections.ensure("slack")

    assert made.missing_scopes(("chat:write",)) == []


# ── nothing secret, ever ──────────────────────────────────────────────────


def test_no_credential_is_stored_here():
    """This holds the *state* of a credential, which is safe to put on a
    screen. The credential itself stays in the Keychain."""
    made = connections.ensure("github")
    payload = made.as_dict()

    assert not any("token" in key or "secret" in key or key == "key"
                   for key in payload)


def test_a_vendors_refusal_is_scrubbed_before_it_is_stored():
    """`auth_detail` is shown on the Connectors page, so a token reaching it
    would be a token on a screen."""
    made = connections.ensure("github")

    moved = connections.transition(
        made.id, AuthState.ERROR,
        detail="rejected token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    assert moved is not None
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in moved.auth_detail


# ── pause, disconnect, forget — three different things ────────────────────


def test_pausing_stops_the_work_and_keeps_the_credential():
    made = connections.ensure("gmail")
    connections.transition(made.id, AuthState.AUTHENTICATED)

    paused = connections.pause(made.id)

    assert paused is not None
    assert paused.paused and not paused.runnable
    assert paused.auth_state is AuthState.AUTHENTICATED, "still signed in"


def test_resuming_puts_it_back():
    made = connections.ensure("gmail")
    connections.transition(made.id, AuthState.AUTHENTICATED)
    connections.pause(made.id)

    resumed = connections.pause(made.id, paused=False)

    assert resumed is not None and resumed.runnable


def test_disconnecting_gives_the_credential_back_and_keeps_the_row():
    """Disconnecting is not deleting. A user disconnecting Gmail has not asked
    to forget every email they ever read, and conflating the two makes
    disconnect a button nobody dares press."""
    made = connections.ensure("gmail")
    connections.transition(made.id, AuthState.AUTHENTICATED,
                           scopes=["gmail.readonly"])

    gone = connections.disconnect(made.id)

    assert gone is not None
    assert gone.auth_state is AuthState.DISCONNECTED
    assert gone.scopes == []
    assert connections.get(made.id) is not None, "the row survives"


def test_forgetting_removes_the_connection_and_what_was_keyed_to_it():
    from chitragupta.connectors import resources, sync_state

    made = connections.ensure("gmail")
    sync_state.checkpoint(made.id, "email", cursor="abc")
    resources.seen(made.id, "email", "m1")

    assert connections.forget(made.id)

    assert connections.get(made.id) is None
    assert sync_state.get(made.id, "email").cursor == ""
    assert resources.get(made.id, "email", "m1") is None


def test_forgetting_something_that_is_not_there_says_so():
    assert not connections.forget("gmail:nope")


# ── it survives a restart ─────────────────────────────────────────────────


def test_connections_outlive_the_process():
    from chitragupta.connectors import db

    made = connections.ensure("gmail", account="me@work.test", label="Work")
    connections.transition(made.id, AuthState.AUTHENTICATED)

    db.reset_for_tests()          # as if the app had been quit and reopened

    after = connections.get(made.id)
    assert after is not None
    assert after.label == "Work"
    assert after.auth_state is AuthState.AUTHENTICATED
