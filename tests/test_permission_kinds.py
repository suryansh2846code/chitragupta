"""Standing grants go on the list the gate actually reads.

There are two allow-lists — email recipients and chat recipients — and until
now every route into them assumed the first. So:

* The **"Always allow" button on a Telegram card wrote `telegram:@dana` onto
  the email list.** `check("message_send", …)` reads the chat list, never
  matched it, and asked again every single time. The user tapped a button, was
  told "@dana won't be asked about again", and nothing happened.
* A chat grant that *was* stored correctly was **invisible** on the screen that
  exists to review and revoke them, because the listing filtered to email. A
  standing permission nobody can see is not one anybody agreed to keep.

Both were latent while Telegram had no connect UI. Shipping that door made the
first one live, which is why it is fixed here rather than noted.

The mapping from action to list lives in `permissions.RECIPIENT_KINDS` and
nowhere else — the approval row carries it, so the frontend never guesses.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from chitragupta.agents import approvals
from chitragupta.agents import permissions as p
from chitragupta.api.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    for row in p.all_permissions():
        p.revoke(row["value"], kind=row["kind"])
    yield
    for row in p.all_permissions():
        p.revoke(row["value"], kind=row["kind"])


# ── the bug ────────────────────────────────────────────────────────────────

def test_a_chat_grant_stored_as_email_does_not_work():
    """The failure this file exists for, written out so the fix below means
    something: it is not that the grant is on the wrong row, it is that the
    button silently does nothing."""
    p.grant("telegram:@dana", kind=p.EMAIL_RECIPIENT)
    assert not p.check("message_send",
                       {"app": "telegram", "chat": "@dana"}).allowed


def test_a_chat_grant_on_the_chat_list_works():
    p.grant("telegram:@dana", kind=p.CHAT_RECIPIENT)
    assert p.check("message_send", {"app": "telegram", "chat": "@dana"}).allowed


def test_an_approval_says_which_list_a_grant_would_go_on():
    """Sent by the server because there is one mapping and it lives in
    `RECIPIENT_KINDS`. The frontend guessing it is how this broke."""
    queued = approvals.queue("message_send",
                             {"app": "telegram", "chat": "@dana"},
                             blocked=("telegram:@dana",))
    row = next(r for r in client.get("/api/agents/approvals").json()["approvals"]
               if r["id"] == queued["id"])
    assert row["kind"] == p.CHAT_RECIPIENT


def test_an_email_approval_says_email():
    queued = approvals.queue("send_email", {"to": "dana@work.test"},
                             blocked=("dana@work.test",))
    row = next(r for r in client.get("/api/agents/approvals").json()["approvals"]
               if r["id"] == queued["id"])
    assert row["kind"] == p.EMAIL_RECIPIENT


def test_an_approval_no_allow_list_can_clear_names_no_list():
    """`mail_triage` is refused for a reason no grant fixes, and offering one
    would be a control that cannot work."""
    queued = approvals.queue("mail_triage", {"items": []})
    row = next(r for r in client.get("/api/agents/approvals").json()["approvals"]
               if r["id"] == queued["id"])
    assert row["kind"] == ""
    assert row["blocked"] == []


def test_granting_from_an_approval_is_honoured_by_the_gate():
    """End to end: the shape the card posts must actually clear the action."""
    queued = approvals.queue("message_send",
                             {"app": "telegram", "chat": "@dana"},
                             blocked=("telegram:@dana",))
    row = next(r for r in client.get("/api/agents/approvals").json()["approvals"]
               if r["id"] == queued["id"])

    client.post("/api/agents/permissions",
                json={"value": "telegram:@dana", "kind": row["kind"],
                      "note": "allowed from an approval"})
    assert p.check("message_send", {"app": "telegram", "chat": "@dana"}).allowed


# ── being able to see and take back what was granted ───────────────────────

def test_every_list_is_shown_not_only_the_email_one():
    """A standing permission the user cannot see is one they cannot take
    back, and `permissions.py` is explicit that this list is the whole
    boundary."""
    p.grant("dana@work.test", kind=p.EMAIL_RECIPIENT)
    p.grant("telegram:@dana", kind=p.CHAT_RECIPIENT)

    shown = {r["value"] for r in
             client.get("/api/agents/permissions").json()["permissions"]}
    assert shown == {"dana@work.test", "telegram:@dana"}


def test_each_row_says_which_list_in_words():
    """"chat_recipient" is our column name, not a sentence."""
    p.grant("telegram:@dana", kind=p.CHAT_RECIPIENT)
    row = client.get("/api/agents/permissions").json()["permissions"][0]
    assert row["kind_label"] == "Messaging"


def test_removing_takes_the_one_it_was_asked_for():
    """The same handle can be a person on two apps. Remove has to be
    unambiguous about which permission it takes away."""
    p.grant("dana", kind=p.EMAIL_RECIPIENT)
    p.grant("dana", kind=p.CHAT_RECIPIENT)

    client.delete("/api/agents/permissions/dana?kind=chat_recipient")
    left = {(r["value"], r["kind"]) for r in
            client.get("/api/agents/permissions").json()["permissions"]}
    assert left == {("dana", p.EMAIL_RECIPIENT)}


def test_removing_without_a_kind_still_means_email():
    """Older callers, and the hand-typed box on the settings screen."""
    p.grant("dana@work.test", kind=p.EMAIL_RECIPIENT)
    client.delete("/api/agents/permissions/dana@work.test")
    assert client.get("/api/agents/permissions").json()["permissions"] == []


def test_granting_without_a_kind_still_means_email():
    client.post("/api/agents/permissions", json={"value": "dana@work.test"})
    assert p.check("send_email", {"to": "dana@work.test"}).allowed


# ── the frontend sends what it was given ───────────────────────────────────

def test_the_approval_card_posts_the_kind_the_server_named():
    """Source-level, because the behavioural harness for this path fakes the
    request layer — and the whole bug was a field that was never sent."""
    from pathlib import Path

    source = (Path(__file__).parent.parent
              / "chitragupta/web/workspace.js").read_text()
    block = source.split("const allowAlways", 1)[1].split("};", 1)[0]
    assert "kind: row.kind" in block, (
        "the card is guessing the allow-list again")


def test_the_settings_row_can_remove_from_the_right_list():
    from pathlib import Path

    source = (Path(__file__).parent.parent
              / "chitragupta/web/workspace.js").read_text()
    assert "data-allowkind" in source
    assert "kind=${encodeURIComponent(kind)}" in source
