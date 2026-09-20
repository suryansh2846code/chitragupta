"""Job 10 — "tell Rahul I'll send it tonight".

The messaging mirror of job 3, and the stakes are higher for a reason that has
nothing to do with the words: **there is no draft.** An email to the wrong
person can be prepared and looked at first; a message is delivered the instant
it is approved, to somebody's phone, and no app this talks to lets us take it
back.

Two real gaps, and the first had been live since `message_send` was added:

**A time was parsed and thrown away.** `execute()` honoured `at` for a
hardcoded tuple of `("send_email", "create_event")`, and `message_send` joined
the registry later without anybody revisiting it. So *"tell Rahul at six that
I am running late"* dropped the six and sent immediately — no error, and the
user finds out from Rahul. Scheduling is declared on the spec now, the way
every other tier is, and an action that cannot be scheduled REFUSES an `at`
rather than running now.

**Nothing read the message back.** A `send` that returns is the app saying it
accepted the request, which is not the same as the message being in the
conversation — a bot removed from a group fails in a way that looks like
success from here.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import REGISTRY, Risk, execute, run_now


class FakeApp:
    """One conversation, and whatever was sent into it."""

    name = "telegram"
    label = "Telegram"

    def __init__(self, *, accept=True, readable=True):
        self.accept = accept
        self.readable = readable
        self.sent: list[tuple[str, str]] = []
        self.messages: list[dict] = []

    def send(self, chat, text):
        self.sent.append((chat, text))
        if not self.accept:
            return {"ok": False, "error": "no"}
        # The app accepted it. Whether it ARRIVED is a separate question, and
        # a fake that always appends would answer it for the code under test.
        if self.readable:
            self.messages.append({"text": text})
        return {"ok": True, "detail": "sent"}

    def history(self, chat, limit=40):
        if not self.readable:
            raise RuntimeError("cannot read this conversation")
        return self.messages[-limit:]


@pytest.fixture
def app(monkeypatch):
    def install(**kw):
        fake = FakeApp(**kw)
        import chitragupta.messaging as messaging

        monkeypatch.setattr(messaging, "get_app", lambda name: fake)
        return fake
    return install


def _send(**params):
    return run_now("message_send",
                   {"app": "telegram", "chat": "4411",
                    "text": "I'll send it tonight", **params})


# ── the time that was being thrown away ──────────────────────────────────
def test_a_message_for_later_is_not_sent_now(app):
    """The bug. "Tell Rahul at six" parsed the six, dropped it, and sent the
    message immediately — with no error anywhere."""
    fake = app()

    result = execute("message_send",
                     {"app": "telegram", "chat": "4411",
                      "text": "running late", "at": "in 3 hours"})

    assert result["ok"]
    assert result.get("scheduled") is True, "it sent instead of scheduling"
    assert fake.sent == [], "the message went out immediately"


def test_the_scheduled_card_says_when(app):
    app()

    result = execute("message_send",
                     {"app": "telegram", "chat": "4411", "text": "hi",
                      "at": "in 3 hours"})

    assert "scheduled for" in result["detail"]
    assert "Send a message" in result["detail"], (
        "it announced a message as something else")


def test_scheduling_is_declared_by_the_action_not_a_list_in_execute():
    """The shape of the bug, not just the instance.

    A tuple inside `execute()` is a second place to remember, and the one
    somebody forgets is whichever is furthest from the code they are writing.
    `message_send` was added to the registry and not to that tuple.
    """
    assert REGISTRY["message_send"].schedulable is True
    assert REGISTRY["send_email"].schedulable is True
    assert REGISTRY["create_event"].schedulable is True


def test_an_action_that_cannot_be_scheduled_refuses_rather_than_running_now(app):
    """Running now is the one outcome the user definitely did not ask for."""
    result = execute("github_comment",
                     {"url": "https://github.com/acme/api/issues/7",
                      "body": "later please", "at": "tomorrow"})

    assert result["ok"] is False
    assert "cannot be scheduled" in result["error"]


def test_an_action_whose_own_field_is_a_time_still_works():
    """`set_reminder`, `create_routine` and `log_workout` take `at` as their
    OWN argument — when to ping, when to run, when the workout happened.
    Scheduling those would be scheduling a scheduler, and refusing them would
    break reminders outright."""
    result = execute("set_reminder", {"message": "stand up", "at": "in 2 hours"})

    assert result["ok"] is True
    assert not result.get("scheduled"), "a reminder was scheduled as an action"


def test_an_unreadable_time_is_refused_not_guessed(app):
    fake = app()

    result = execute("message_send",
                     {"app": "telegram", "chat": "4411", "text": "hi",
                      "at": "sometime-ish"})

    assert result["ok"] is False
    assert fake.sent == []


def test_the_user_can_see_and_clear_the_time_before_confirming():
    """`at` is on the card. A field the user cannot see is a decision they
    cannot correct before it fires."""
    assert "at" in REGISTRY["message_send"].fields


# ── did it actually arrive ───────────────────────────────────────────────
def test_a_sent_message_is_read_back(app):
    app()

    result = _send()

    assert result["ok"]
    assert result["verified"] is True


def test_a_conversation_we_could_not_read_is_unverified_not_failed(app):
    """Unverified is not failed. The message may well have arrived; we simply
    did not see it, and saying "failed" would send the user chasing a message
    that is sitting on somebody's phone."""
    app(readable=False)

    result = _send()

    assert result["ok"] is True, "an unreadable history was reported as failure"
    # `_finish` sets `verified` only when it is true, so ABSENT is how the
    # whole registry spells "we did not check" — asserting `is False` here
    # would be inventing a third state the rest of the app does not have.
    assert not result.get("verified")


def test_a_message_the_app_refused_is_a_failure(app):
    app(accept=False)

    assert _send()["ok"] is False


def test_verification_tolerates_the_app_rewrapping_the_text(app):
    """Apps trim, re-wrap and occasionally append. An exact match would report
    every delivered message as unconfirmed."""
    fake = app()
    fake.messages.append({"text": "  I'll send it   tonight  \n(edited)"})

    assert _send()["verified"] is True


# ── the tier is unchanged ────────────────────────────────────────────────
def test_it_still_reaches_a_person_and_is_still_gated():
    """Nothing about scheduling or verifying may make a message cheaper to
    send. It is amber, against the chat list, as it was."""
    from chitragupta.agents.permissions import CHAT_RECIPIENT, check

    spec = REGISTRY["message_send"]
    assert spec.risk is Risk.AMBER
    assert spec.recipient_kind == CHAT_RECIPIENT
    assert not check("message_send",
                     {"app": "telegram", "chat": "4411", "text": "hi"}).allowed


def test_it_declares_no_undo():
    """Deliberately. No messaging app here lets us unsend, and a button that
    claims to would be the worst kind of lie — the user stops worrying."""
    assert REGISTRY["message_send"].undo is None
