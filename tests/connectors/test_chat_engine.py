"""Slack and Telegram on the shared engine — conversations, then messages.

These are the **fourth** shape: two levels deep. A page is one conversation and a
record is one message, so a checkpoint lands per conversation and a pass
interrupted at the twentieth channel resumes there rather than at the first.

Neither needs `hydrate` — `history` already answers with whole messages — so the
win here is *not* a drop in request count the way it was for mail and documents.
It is four other things, and each is a test below:

* **Slack was never paced.** Slack allows roughly one request a second per
  method; a pass makes one listing call plus one per conversation, in a burst,
  with no gap and no retry. A `ratelimited` answer became a sentence that
  `history` then swallowed — so the pass reported **success with conversations
  quietly missing**.
* **`since` was accepted and ignored by both**, while `incremental` claimed
  True. Slack can filter server-side and now does; Telegram cannot in the same
  shape, so its declaration is False and honest.
* **A message edited after we read it was never updated.** The fingerprint is a
  digest of the text — free here, because the text arrives with the listing.
* **Nothing recorded which conversation a message came from.** A message memory
  said `source="slack"` and carried a title, so *"which channel was this?"* was
  structurally unanswerable.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import resources, sync_state
from chitragupta.connectors.slack import SlackConnector
from chitragupta.connectors.telegram import TelegramConnector

from . import harness

#: Both connectors, run through the same tests. The shape is identical and so is
#: nearly all of the behaviour; a test written for one and not the other is a
#: property that silently holds for half the messaging connectors.
CHATTY = ["slack", "telegram"]


@pytest.fixture(params=CHATTY)
def chatty(request, monkeypatch, fake_module, tmp_path):
    connector, kwargs = harness.build(harness.BY_NAME[request.param], monkeypatch,
                                      fake_module, tmp_path, 6)
    return connector, kwargs


# ── a page is a conversation, a record is a message ───────────────────────


def test_every_message_in_every_conversation_is_read(chatty):
    connector, kwargs = chatty

    result = harness.sync(connector, kwargs)

    assert result.added == 6
    stored = connector.store.list(source=connector.name, limit=20)
    assert len(stored) == 6


def test_a_checkpoint_lands_per_conversation(chatty):
    """So a pass interrupted at the twentieth channel resumes there. The unit
    used to be the whole pass."""
    connector, kwargs = chatty
    harness.sync(connector, kwargs)

    state = sync_state.get(connector.connection().id, "message")
    assert state.items_processed == 6
    assert state.last_success


def test_an_unreadable_conversation_does_not_end_the_pass(chatty, monkeypatch):
    """A private channel the token lost access to, or a chat that has been
    deleted. Decision H2, at the conversation level."""
    connector, kwargs = chatty
    real = type(connector).history
    first = {"seen": False}

    def sometimes(self, chat_id, limit=50, **kw):
        if not first["seen"]:
            first["seen"] = True
            raise RuntimeError("that conversation cannot be read")
        return real(self, chat_id, limit=limit, **kw)

    monkeypatch.setattr(type(connector), "history", sometimes)

    result = harness.sync(connector, kwargs)

    assert not result.errors
    assert result.added > 0, "one bad conversation took the rest with it"


# ── identity, and an edit ─────────────────────────────────────────────────


def test_a_second_pass_adds_nothing(chatty):
    connector, kwargs = chatty
    harness.sync(connector, kwargs)

    result = harness.sync(connector, kwargs)

    assert result.added == 0


def test_a_message_is_identified_by_its_conversation_and_its_id(chatty):
    """A Slack `ts` and a Telegram message id are both unique *within* a
    conversation and not across them. Unscoped, two channels would collide."""
    connector, kwargs = chatty
    harness.sync(connector, kwargs)

    held = resources.for_connection(connector.connection().id)
    assert held
    assert all(":" in r.external_id for r in held)
    assert len({r.external_id for r in held}) == len(held)


def test_an_edited_message_is_read_again(chatty, monkeypatch):
    """The text arrives with the listing, so catching an edit costs nothing —
    where in a mailbox it would have cost a request per message."""
    connector, kwargs = chatty
    harness.sync(connector, kwargs)

    # Edit the first message of the first conversation in place.
    if connector.name == "slack":
        first = next(iter(connector.fake_history.values()))
        first[0]["text"] = "Actually, the launch moved to November."
    else:
        import dataclasses
        key = next(iter(connector.fake_history))
        held = connector.fake_history[key]
        held[0] = dataclasses.replace(
            held[0], text="Actually, the launch moved to November.")

    result = harness.sync(connector, kwargs)

    assert result.added == 1, "an edited message was treated as unchanged"


# ── provenance ────────────────────────────────────────────────────────────


def test_a_message_records_which_conversation_it_came_from(chatty):
    """It said `source="slack"` and carried a title, so "which channel was
    this?" could not be answered from the record at all."""
    import json

    connector, kwargs = chatty
    harness.sync(connector, kwargs)

    stored = connector.store.list(source=connector.name, limit=5)
    raw = stored[0].metadata
    metadata = json.loads(raw) if isinstance(raw, str) else raw

    assert metadata["chat"], metadata
    assert metadata["chat_id"]
    assert metadata["sender"]
    source = metadata["source"]
    assert source["connector"] == connector.name
    assert source["resource_type"] == "message"
    assert source["run_id"]


def test_a_message_is_dated_by_when_it_was_sent(chatty):
    connector, kwargs = chatty
    harness.sync(connector, kwargs)

    stored = connector.store.list(source=connector.name, limit=5)
    assert stored[0].event_date, "a message with no date cannot be recalled by one"


# ── a window is never swept ───────────────────────────────────────────────


def test_older_messages_are_not_tombstoned_when_they_fall_out_of_the_window(
        chatty):
    """A pass reads the recent end of each conversation. Sweeping that would
    tombstone every message older than the per-chat limit, which is nearly all
    of them."""
    connector, kwargs = chatty
    harness.sync(connector, kwargs)
    connection = connector.connection()
    assert resources.for_connection(connection.id)

    # A later pass where the conversations return nothing at all.
    if connector.name == "slack":
        for messages in connector.fake_history.values():
            messages.clear()
    else:
        for key in connector.fake_history:
            connector.fake_history[key] = []
    harness.sync(connector, kwargs)

    assert all(not r.stale for r in resources.for_connection(connection.id))


# ── Slack: the pacing, and the watermark it now honours ───────────────────


def test_slack_filters_at_the_service_on_a_second_pass(monkeypatch, fake_module,
                                                       tmp_path):
    """`since` was accepted and ignored, so every pass re-read every message in
    every conversation and leaned on the content hash to discard them."""
    connector, kwargs = harness.build(harness.BY_NAME["slack"], monkeypatch,
                                      fake_module, tmp_path, 4)
    harness.sync(connector, kwargs)
    connector.fake_calls.clear()

    harness.sync(connector, kwargs)

    reads = [params for method, params in connector.fake_calls
             if method == "conversations.history"]
    assert reads, "it never read a conversation"
    assert all(p.get("oldest") for p in reads), (
        "the watermark was not passed to Slack — every message was re-read")


def test_slack_asks_for_nothing_before_a_first_pass(monkeypatch, fake_module,
                                                    tmp_path):
    """No watermark yet means the normal window, never "nothing newer than
    nothing" — the contract `Connector.since()` documents."""
    connector, kwargs = harness.build(harness.BY_NAME["slack"], monkeypatch,
                                      fake_module, tmp_path, 3)

    harness.sync(connector, kwargs)

    reads = [params for method, params in connector.fake_calls
             if method == "conversations.history"]
    assert reads and not any(p.get("oldest") for p in reads)


def test_slack_requests_are_paced_through_its_own_budget():
    """Slack allows roughly one request a second per method, and a pass makes
    one listing call plus one per conversation in a burst."""
    from chitragupta.connectors import limits

    manifest = SlackConnector(store=object()).manifest()
    gate = limits.gate_for("slack", manifest.limits)

    assert manifest.limits.requests > 0, "Slack was unmetered"
    assert gate.limits.requests == manifest.limits.requests


def test_a_rate_limited_slack_call_is_retried_rather_than_losing_the_conversation(
        monkeypatch):
    """Slack says `ratelimited` with a **200**, so nothing in the HTTP layer
    sees it. Returned rather than raised, it became a sentence that `history`
    swallowed — and the pass reported success with conversations missing.
    """
    import httpx

    from chitragupta.config import get_settings
    from chitragupta.connectors import limits

    limits.reset()
    get_settings().set_secret("SLACK_USER_TOKEN", "xoxp-EXAMPLE-NOT-A-REAL-TOKEN")
    connector = SlackConnector(store=object())
    attempts = {"n": 0}

    class Answer:
        def __init__(self, payload):
            self._payload = payload
            self.headers = {}

        def json(self):
            return self._payload

    def post(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return Answer({"ok": False, "error": "ratelimited"})
        return Answer({"ok": True, "messages": []})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr("chitragupta.connectors.slack.RETRY",
                        __import__("chitragupta.connectors.retry",
                                   fromlist=["RetryPolicy"]).RetryPolicy(
                                       attempts=2, base_seconds=0.0, jitter=0,
                                       total_seconds=1.0))

    body = connector._call("conversations.history", channel="C0")

    assert body.get("ok") is True, body
    assert attempts["n"] == 2, "the rate-limited call was not tried again"


# ── the declarations are true now ─────────────────────────────────────────


def test_slack_says_it_keeps_a_watermark_and_does():
    manifest = SlackConnector(store=object()).manifest()

    assert manifest.sync.incremental is True
    assert manifest.sync.resumable is True


def test_telegram_says_it_keeps_no_watermark_because_it_does_not():
    """It said True while `sync()` accepted `since` and never used it — a
    manifest promising a filtered second pass that never happened."""
    manifest = TelegramConnector(store=object()).manifest()

    assert manifest.sync.incremental is False
    assert manifest.sync.overlap_minutes == 0
    assert manifest.sync.resumable is True


def test_both_still_carry_conversations(chatty):
    """The messaging trio is how `../messaging.py` finds them, and migrating the
    sync must not have disturbed it."""
    from chitragupta.messaging import _carries_conversations

    connector, _ = chatty
    assert _carries_conversations(connector)


def test_sending_is_still_the_only_write(chatty):
    connector, _ = chatty
    manifest = connector.manifest()

    assert {str(c) for c in manifest.writes} == {"send:message"}
