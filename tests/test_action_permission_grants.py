"""Granting a standing permission from the card that asked for it.

`permissions.check()` refuses every unattended outbound action whose recipient
is not on the allow-list, and `approvals` holds it for one tap. That half
worked. The other half — putting somebody *on* the list — had three endpoints
and no way to reach them from the product: `GET`, `POST` and `DELETE
/api/agents/permissions` had zero callers in `chitragupta/web/`.

So a routine created to email the same person every week asked forever, and
nothing in the app could ever stop it asking. This file covers the contract that
closes that, and it is a contract precisely because the halves can ship apart:

    An approval row carries `blocked` — the addresses the allow-list refused,
    as `permissions.check()` computed them, beside the sentence in `reason`
    rather than inside it. `blocked` is empty when no allow-list could have
    helped (`NEVER_UNATTENDED`), and that emptiness is what tells the UI not to
    offer a standing grant.

The frontend half is pinned in `tests/js/` and by
`test_frontend_calls_real_endpoints.py`. If the address were recovered from the
`reason` sentence instead, both sides would still pass their own tests and would
disagree about what an injected `to:` field contained — which is the exact
failure `permissions._every_address_in` exists to prevent.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import approvals, permissions


@pytest.fixture(autouse=True)
def clean_slate():
    """Each test starts with nothing queued and nobody permitted."""
    conn = approvals._conn()
    conn.execute("DELETE FROM action_approvals")
    conn.commit()
    perms = permissions._conn()
    perms.execute("DELETE FROM action_permissions")
    perms.commit()
    yield


def _queue_an_email(to: str) -> dict:
    """Queue through the real seam, so the verdict is the real one."""
    approvals.run_or_queue("send_email", {"to": to, "subject": "the report"})
    return approvals.pending()[0]


def test_a_blocked_action_names_who_was_blocked(clean_slate):
    """The address arrives as data, which is what the grant is written against."""
    row = _queue_an_email("dana@work.test")

    assert row["blocked"] == ["dana@work.test"]
    assert "dana@work.test" in row["reason"], "and still reads as a sentence"


def test_every_blocked_recipient_is_listed_not_just_the_first(clean_slate):
    """A `to:` field written by whoever wrote the email the agent just read.

    `permissions._every_address_in` exists because `allowed@work.test
    attacker@evil.test` once read as the allowed address alone. The UI must be
    offered *both*, or a user granting "the address on the card" would be
    granting one of two and never learn about the other.
    """
    permissions.grant("allowed@work.test")

    row = _queue_an_email("allowed@work.test attacker@evil.test")

    assert row["blocked"] == ["attacker@evil.test"]


def test_an_action_no_allow_list_can_clear_offers_no_grant(clean_slate):
    """`create_routine` is refused whatever the list says.

    (`mcp_action` used to be in this sentence. It is grantable now — per
    `server:tool` — except for irreversible verbs, which still land here.)

    Offering "always allow" there would be a button that cannot work, which is
    the failure mode `CLAUDE.md` names first: it reads as the app being broken.
    """
    approvals.run_or_queue("create_routine", {"name": "daily digest"})
    row = approvals.pending()[0]

    assert row["blocked"] == []
    assert row["reason"], "but it still says why"


def test_granting_the_blocked_address_lets_the_next_one_through(clean_slate):
    """The whole point: the same routine stops asking.

    This is the behaviour a user is buying when they press the button, and it is
    the one thing neither half's own tests would catch if the address the UI
    granted differed by so much as a display name.
    """
    row = _queue_an_email("Dana <dana@work.test>")
    (blocked,) = row["blocked"]

    permissions.grant(blocked)

    assert permissions.check(
        "send_email", {"to": "Dana <dana@work.test>"}).allowed is True


def test_a_grant_is_revocable_by_the_value_it_was_granted_with(clean_slate):
    """The Settings list shows what `list_permissions()` returns and deletes by
    the same string, so a grant a user can see is always one they can remove."""
    permissions.grant("dana@work.test")
    listed = permissions.list_permissions()

    assert [p["value"] for p in listed] == ["dana@work.test"]
    assert permissions.revoke(listed[0]["value"]) is True
    assert permissions.list_permissions() == []


def test_granting_one_recipient_does_not_release_the_other(clean_slate):
    """Half a grant must fail closed.

    Otherwise "always allow the address I read on the card" would quietly
    authorise an address the user never saw, which is the injection this whole
    subsystem is built against.
    """
    permissions.grant("allowed@work.test")

    verdict = permissions.check(
        "send_email", {"to": "allowed@work.test attacker@evil.test"})

    assert verdict.allowed is False
    assert verdict.blocked_recipients == ("attacker@evil.test",)


def test_an_upgrade_in_place_keeps_the_queue(tmp_path, monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists.

    So a user upgrading with approvals already waiting keeps the old table, and
    the next action to queue INSERTs a column that is not there. It fails at the
    *write*, not the read — `_public` tolerates a missing key, which is why the
    obvious version of this test (load an old row, check it parses) passes with
    the migration deleted and proves nothing. Queuing is what breaks, and
    queuing is the whole promise of this module: an action it cannot store is an
    action silently dropped, which is the failure its docstring opens with.

    The old schema is built by hand rather than from `_SCHEMA`, because reusing
    it would test the new shape against itself and pass forever.
    """
    import sqlite3

    from chitragupta.config import Settings

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(approvals, "get_settings", lambda: Settings(home=home))

    old = sqlite3.connect(str(home / "agents.db"))
    old.executescript("""
        CREATE TABLE action_approvals (
            id TEXT PRIMARY KEY, routine_id TEXT NOT NULL DEFAULT '',
            routine_name TEXT NOT NULL DEFAULT '', agent_id TEXT NOT NULL DEFAULT '',
            action_type TEXT NOT NULL, params_json TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending', result TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, decided_at TEXT);
        INSERT INTO action_approvals
            (id, action_type, params_json, summary, reason, status, created_at)
        VALUES ('old', 'send_email', '{"to": "dana@work.test"}',
                'Email to dana@work.test', 'waiting', 'pending',
                '2026-01-01T00:00:00+00:00');
    """)
    old.commit()
    old.close()

    monkeypatch.setattr(permissions, "get_settings", lambda: Settings(home=home))
    approvals.run_or_queue("send_email", {"to": "new@work.test"})

    rows = {r["id"]: r for r in approvals.pending()}

    assert "old" in rows, "the action already waiting survived the upgrade"
    assert rows["old"]["blocked"] == [], "and offers no grant it cannot honour"
    new = next(r for r in rows.values() if r["id"] != "old")
    assert new["blocked"] == ["new@work.test"], "and the next one can be stored"
