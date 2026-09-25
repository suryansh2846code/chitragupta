"""A credential one test writes must not still be there for the next one.

This is **A13** in `docs/AUDIT.md`, closed here. The symptom was that
`pytest -k "docs or claude"` failed on
`test_connecting_claude_code_does_not_connect_the_api_provider` while the full
run passed: an earlier test in that selection saved `ANTHROPIC_API_KEY`, and
`claude` reads as connected whenever a key is stored, so the assertion that
connecting claude-code leaves the API provider alone saw a provider that looked
connected for a reason nothing in that file had done.

Two tests, in this order, are the whole thing: the first writes credentials, the
second finds them gone. They are deliberately written as a *pair that depends on
order*, which is normally the thing to avoid — here it is the only way to assert
that order does not matter. Renaming either so they no longer run adjacently
does not break them; it makes them stop testing anything, which is why they say
so in their names.

`conftest.py::_no_leaked_credentials` is what makes the second one pass.
"""
from __future__ import annotations

from chitragupta.config import get_settings
from chitragupta.models.connections import (
    ConnectionStatus,
    ProviderConnection,
    get_connection,
    save_connection,
)

KEY = "ANTHROPIC_API_KEY"
LEAKED = "sk-leaked-by-the-previous-test"


def test_1_a_test_may_write_a_credential_and_see_its_own_write():
    """The fixture restores *after*, so a test still reads what it wrote."""
    get_settings().set_secret(KEY, LEAKED)
    # Through `set_credential`, because setting the per-credential fields on a
    # fresh object leaves the rollup disagreeing with them and `save_connection`
    # then honours the rollup — the documented pre-split behaviour.
    conn = ProviderConnection(provider="claude", email="someone@example.test")
    conn.set_credential("account", ConnectionStatus.ACCOUNT_CONNECTED)
    save_connection(conn)

    assert get_settings().get_secret(KEY) == LEAKED
    assert get_connection("claude").account_connected is True


def test_2_the_next_test_does_not_inherit_it():
    """The assertion A13 was about. `CHITRAGUPTA_HOME` is one directory for the
    whole run, so without a per-test reset both of those are still here."""
    assert get_settings().get_secret(KEY) != LEAKED, (
        "an API key written by another test is still stored — a provider will "
        "read as connected that nobody connected")

    conn = get_connection("claude")
    assert conn.account_connected is False, (
        "a provider connection written by another test survived it")
    assert conn.email != "someone@example.test"


def test_3_a_stored_key_is_what_made_claude_look_connected():
    """The mechanism, pinned so the fix cannot be mistaken for the symptom.

    Nothing is wrong with this behaviour — a key in the store *is* a credential,
    and `_stored_api_key` only ignores one the user explicitly disconnected. The
    bug was that a test's key outlived it, not that a key counts.
    """
    from chitragupta.models.connection_state import provider_credentials
    from chitragupta.models.registry import clear_provider_cache

    assert provider_credentials("claude")["api_key"]["connected"] is False

    get_settings().set_secret(KEY, LEAKED)
    clear_provider_cache()
    assert provider_credentials("claude")["api_key"]["connected"] is True
