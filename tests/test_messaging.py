"""Every conversation the user has, behind one shape.

Two apps landed together on purpose: one app is a feature, two is a contract.
If Telegram and Slack each needed their own tool, their own action and their own
card, a third would need a fourth of each, and the tool list would grow by two
entries per app for a difference nobody is asking about.

So these tests are mostly about the seam — that an app is a class and nothing
above `messaging.py` knows which one it is talking to.

Which apps are reachable at all, and why WhatsApp and LinkedIn are not:
docs/MESSAGING.md
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chitragupta import actions, messaging
from chitragupta.agents import connector_grants, message_tools
from chitragupta.agents.approvals import describe
from chitragupta.agents.permissions import (
    CHAT_RECIPIENT,
    EMAIL_RECIPIENT,
    OUTBOUND_ACTIONS,
    check,
    grant,
    recipients_of,
    revoke,
)
from chitragupta.agents.tools import TOOL_DEFS, TOOL_IMPLS
from chitragupta.messaging import Chat, Message


class FakeApp:
    """A messaging connector, as small as the contract allows."""

    def __init__(self, name="telegram", label="Telegram", ready=True):
        self.name, self.label, self._ready = name, label, ready
        self.sent: list[tuple[str, str]] = []

    def is_configured(self):
        return self._ready, "" if self._ready else "not set up"

    def chats(self, limit=30):
        return [Chat(id="101", name="Dana", kind="dm", unread=2),
                Chat(id="202", name="#standup", kind="channel")]

    def history(self, chat_id, limit=50):
        # Oldest first is the promise every app has to keep.
        return [Message(id="1", sender="Dana", at="2026-09-01T09:00:00+00:00",
                        text="are we still on for six?"),
                Message(id="2", sender="me", at="2026-09-01T09:05:00+00:00",
                        text="yes", outgoing=True)]

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"ok": True, "id": "9", "detail": f"Message sent on {self.label}"}


@pytest.fixture
def one_app(monkeypatch):
    app = FakeApp()
    monkeypatch.setattr(messaging, "apps", lambda: [app])
    return app


@pytest.fixture
def allowed(monkeypatch):
    """An agent that has been granted every messaging app."""
    monkeypatch.setattr(connector_grants, "may_use", lambda _a, _c: True)


# ── the seam ─────────────────────────────────────────────────────────────
def test_an_app_is_just_a_class_with_three_methods():
    """Adding an app must not mean adding a tool, an action and a card."""
    for method in ("chats", "history", "send"):
        assert method in messaging.Messenger.__annotations__ or hasattr(
            messaging.Messenger, method), f"{method} left the contract"


def test_a_connector_that_only_ingests_is_not_a_messaging_app():
    from chitragupta.connectors import get_connector

    assert not messaging._carries_conversations(get_connector("files"))


def test_both_shipped_apps_implement_it():
    from chitragupta.connectors import get_connector

    for name in ("telegram", "slack"):
        assert messaging._carries_conversations(get_connector(name)), name


def test_an_app_that_is_not_set_up_is_not_offered(monkeypatch):
    """A control that cannot do anything reads as a broken app."""
    monkeypatch.setattr(messaging, "apps",
                        lambda: [a for a in [FakeApp(ready=False)]
                                 if a.is_configured()[0]])
    assert messaging.app_ids() == []


def test_a_chat_target_is_scoped_by_app():
    """Allowing @dana on Telegram must not allow #dana in Slack."""
    assert messaging.target("telegram", "@Dana") == "telegram:@dana"
    assert messaging.target("slack", "@Dana") != messaging.target("telegram", "@Dana")


# ── reading ──────────────────────────────────────────────────────────────
def test_list_chats_gives_the_id_needed_to_act(one_app, allowed):
    out = message_tools.list_chats()
    assert out.ok
    assert "id=101" in out and "Dana" in out
    assert "2 unread" in out


def test_a_conversation_reads_oldest_first(one_app, allowed):
    """A conversation read backwards is answered backwards."""
    out = message_tools.read_chat("telegram", "101")
    assert out.ok
    assert out.index("are we still on for six?") < out.index("yes")


def test_nothing_connected_says_so_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(messaging, "apps", list)
    out = message_tools.list_chats()
    assert not out.ok
    assert "No messaging apps are connected" in out


# ── permission, for a tool the gate cannot see the target of ─────────────
def test_an_agent_asks_before_reading_a_messaging_app(one_app, monkeypatch):
    monkeypatch.setattr(connector_grants, "may_use", lambda _a, _c: False)

    listed = message_tools.list_chats()
    assert not listed.ok
    # Not just "Telegram" — the fallback text names Telegram and Slack as apps
    # that CAN be added, so asserting the word alone passes on a connector that
    # was silently dropped. The claim is that it asks for permission.
    assert "permission" in listed, "it did not ask; it just said nothing is connected"

    one = message_tools.read_chat("telegram", "101")
    assert not one.ok
    assert "permission" in one


def test_a_blocked_app_is_named_never_silently_dropped(one_app, monkeypatch):
    """An agent that cannot see Slack tells the user there is no Slack."""
    monkeypatch.setattr(connector_grants, "may_use", lambda _a, _c: False)
    out = message_tools.list_chats()
    assert "permission" in out, "a connected app was reported as not connected"
    assert out.count("Telegram") > 1, "it did not name the app it needs"


def test_the_acting_agent_travels_with_the_turn():
    token = connector_grants.acting_as("inbox")
    try:
        assert connector_grants.acting() == "inbox"
    finally:
        connector_grants.stop_acting(token)
    assert connector_grants.acting() == ""


# ── sending ──────────────────────────────────────────────────────────────
def test_sending_reaches_the_app(one_app, monkeypatch):
    monkeypatch.setattr(messaging, "get_app", lambda n: one_app)
    out = actions.run_now("message_send",
                          {"app": "telegram", "chat": "101", "text": "on my way"})
    assert out["ok"]
    assert one_app.sent == [("101", "on my way")]


def test_an_empty_message_is_not_sent(one_app, monkeypatch):
    monkeypatch.setattr(messaging, "get_app", lambda n: one_app)
    out = actions.run_now("message_send", {"app": "telegram", "chat": "101", "text": "  "})
    assert not out["ok"]
    assert one_app.sent == []


def test_the_message_is_the_tag_body():
    parsed = actions.parse_actions(
        '<action type="message_send" app="telegram" chat="@dana">'
        "See you at six.</action>")
    assert parsed[0]["params"] == {"app": "telegram", "chat": "@dana",
                                  "text": "See you at six."}


# ── who an unattended agent may reach ────────────────────────────────────
def test_a_message_reaches_someone_so_it_is_outbound():
    assert "message_send" in OUTBOUND_ACTIONS


def test_an_unattended_message_waits_for_an_allowed_recipient():
    params = {"app": "telegram", "chat": "@dana", "text": "hi"}
    assert recipients_of("message_send", params) == ["telegram:@dana"]
    assert not check("message_send", params).allowed

    grant("telegram:@dana", kind=CHAT_RECIPIENT)
    try:
        assert check("message_send", params).allowed
    finally:
        revoke("telegram:@dana", kind=CHAT_RECIPIENT)


def test_a_chat_permission_is_not_an_email_permission():
    """Two lists, because a chat id is not an address."""
    grant("telegram:@dana", kind=CHAT_RECIPIENT)
    try:
        from chitragupta.agents.permissions import is_permitted

        assert is_permitted("telegram:@dana", kind=CHAT_RECIPIENT)
        assert not is_permitted("telegram:@dana", kind=EMAIL_RECIPIENT)
    finally:
        revoke("telegram:@dana", kind=CHAT_RECIPIENT)


def test_an_unknown_chat_fails_closed():
    """A missing conversation must never read as "reaches nobody".

    This test asserted `recipients_of(...) == []` for its first two years,
    which is *literally* "reaches nobody" — the opposite of its own docstring.
    It stayed green because the second assertion held: the handler refuses to
    send to an empty chat. So the protection was real but it was at the wrong
    end, and `permissions.check()` — which reads an empty recipient list as
    "nothing to allow-list" and returns allowed — was letting the action past
    the gate entirely. An unattended agent proposing a message with an
    unreadable `chat` got no card, and whether anything reached a person came
    down to a handler's argument validation.

    Both halves now, gate first.
    """
    from chitragupta.agents.permissions import check

    params = {"app": "telegram", "chat": "", "text": "hi"}

    assert recipients_of("message_send", params) == ["an unidentified conversation"]
    assert not check("message_send", params).allowed, (
        "the gate let an unaddressed message through")
    # …and the handler still refuses too, rather than sending to nowhere.
    assert not actions.run_now("message_send", params)["ok"]


# ── the card ─────────────────────────────────────────────────────────────
def test_the_card_names_the_app_because_that_is_half_the_decision(one_app, monkeypatch):
    monkeypatch.setattr(messaging, "apps", lambda: [one_app])
    line = describe("message_send", {"app": "telegram", "chat": "@dana", "text": "hi"})
    assert "Telegram" in line and "@dana" in line
    assert "message_send" not in line


# ── the tools exist and are addressable ──────────────────────────────────
def test_the_tools_are_registered():
    for name in ("list_chats", "read_chat"):
        assert name in TOOL_DEFS and name in TOOL_IMPLS


def test_the_multi_app_tools_are_not_gated_by_name():
    """They reach whichever app the model named, so the name cannot decide."""
    assert "list_chats" not in connector_grants.FIRST_PARTY_TOOLS
    assert "read_chat" not in connector_grants.FIRST_PARTY_TOOLS


def test_telegram_is_the_client_api_not_the_bot_api():
    """A bot only sees chats it was added to — which is not the user's life."""
    source = (SimpleNamespace(),)  # keep ruff quiet about the unused import
    del source
    from chitragupta.connectors import telegram_auth

    assert telegram_auth.API_ID == "TELEGRAM_API_ID"
    assert "my.telegram.org" in telegram_auth.NEEDS_CREDENTIALS


def test_slack_needs_no_new_dependency():
    """It is the official Web API over httpx, already a base dependency."""
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    base = " ".join(data["project"]["dependencies"])
    assert "httpx" in base
    optional = data["project"]["optional-dependencies"]
    assert "slack" not in optional
    assert "telethon" in " ".join(optional["telegram"])
