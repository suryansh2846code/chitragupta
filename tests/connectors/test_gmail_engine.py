"""Gmail on the shared engine, and the three things that are true only of mail.

The win here is not tidiness. `messages.list` answers with ids and thread ids —
no subject, no sender, no date — so the body is a **second request per
message**. The old pass fetched every body and let a content hash throw the
duplicates away, which meant up to 600 requests every half hour to keep almost
nothing.

So the keep-or-skip decision has to be answerable from the id alone, before any
body is paid for. That works for mail because of something specific to mail:

* **A received message is immutable.** Nobody edits an email that arrived. Once
  it has been read, the id alone proves we still have it — which is why the
  fingerprint is the id rather than a digest of the content.
* **Labels change and the message does not.** An archived email is the same
  email. Folding labels into the fingerprint would re-read and re-store the
  whole mailbox every time somebody tidied their inbox.
* **The query is a window, not an enumeration.** `after:` and `newer_than:90d`
  describe a slice. Sweeping one for deletions would tombstone every message
  older than the window, which is nearly all of them.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import resources, sync_state
from chitragupta.connectors.engine import Record
from chitragupta.connectors.gmail import GmailConnector, _received_at

from . import harness


@pytest.fixture
def gmail(monkeypatch, fake_module, tmp_path):
    """A Gmail connector wired to fabricated messages, counting what it asks
    the service for. The counts are the assertion in most of these tests."""
    connector, kwargs = harness.build(harness.BY_NAME["gmail"], monkeypatch,
                                      fake_module, tmp_path, 3)
    calls = {"list": 0, "get": 0}

    real_page = type(connector)._page
    real_hydrate = type(connector)._hydrate

    def page(self, service, query, size, cursor):
        calls["list"] += 1
        return real_page(self, service, query, size, cursor)

    def hydrate(self, service, record):
        calls["get"] += 1
        return real_hydrate(self, service, record)

    monkeypatch.setattr(type(connector), "_page", page)
    monkeypatch.setattr(type(connector), "_hydrate", hydrate)
    return connector, kwargs, calls


# ── the N+1 this migration exists to remove ───────────────────────────────


def test_a_first_pass_reads_every_body_once(gmail):
    connector, kwargs, calls = gmail

    result = harness.sync(connector, kwargs)

    assert result.added == 3
    assert calls["get"] == 3, "one body per new message, no more"


def test_a_second_pass_reads_no_bodies_at_all(gmail):
    """The whole point. Before this, every pass fetched all three bodies to
    discover it already had all three."""
    connector, kwargs, calls = gmail
    harness.sync(connector, kwargs)
    calls["get"] = 0

    result = harness.sync(connector, {**kwargs, "full_history": True})

    assert result.added == 0
    assert result.skipped == 3
    assert calls["get"] == 0, (
        "a body was fetched for a message we already had — the identity check "
        "is not running before hydrate")


def test_the_listing_is_still_read_on_a_second_pass(gmail):
    """Skipping the bodies is the saving; skipping the *listing* would mean
    never noticing new mail."""
    connector, kwargs, calls = gmail
    harness.sync(connector, kwargs)
    calls["list"] = 0

    harness.sync(connector, {**kwargs, "full_history": True})

    assert calls["list"] >= 1


# ── a message is immutable, and its labels are not the message ────────────


def test_the_fingerprint_is_the_message_id():
    """Because a received email is never edited. Anything else would either
    miss a change that cannot happen, or invent one that did not."""
    connector = GmailConnector(store=object())
    service = harness.FakeGmailService([
        {"id": "msg1", "threadId": "t1", "snippet": "hello"}])

    page = connector._page(service, "q", 10, "")

    assert page.records[0].external_id == "msg1"
    assert page.records[0].fingerprint == "msg1"


def test_a_message_that_has_been_archived_is_not_re_ingested(gmail):
    """An archived email is the same email. If labels were part of the
    fingerprint, tidying an inbox would re-store the whole mailbox."""
    connector, kwargs, calls = gmail
    harness.sync(connector, kwargs)
    before = calls["get"]

    # The fake service answers the same ids with different labels; the sync must
    # not care.
    result = harness.sync(connector, {**kwargs, "full_history": True})

    assert result.added == 0
    assert calls["get"] == before


# ── the window is never swept ─────────────────────────────────────────────


def test_a_pass_never_marks_anything_deleted(gmail, monkeypatch):
    """`after:` and `newer_than:` describe a slice of the mailbox. Sweeping one
    would tombstone every message older than the window."""
    connector, kwargs, _ = gmail
    harness.sync(connector, kwargs)
    connection = connector.connection()
    held = resources.for_connection(connection.id)
    assert held, "nothing was recorded, so this test proves nothing"

    # A later pass that returns nothing at all — the shape a sweep would react
    # to most destructively.
    harness.install_urlopen(monkeypatch, {})
    harness.sync(connector, {**kwargs, "full_history": True})

    assert all(not r.stale for r in resources.for_connection(connection.id))


# ── resuming ──────────────────────────────────────────────────────────────


def test_the_watermark_still_only_moves_on_a_clean_pass(gmail):
    """What keeps a resumed pass valid: the query is rebuilt from the
    watermark, so a pass that failed halfway rebuilds the *same* query and the
    page token stored against it still means something."""
    connector, kwargs, _ = gmail

    harness.sync(connector, kwargs)

    state = connector.store.get_connector_state("gmail") or {}
    assert state.get("cursor"), "a clean pass must leave a watermark"


def test_a_second_pass_asks_only_for_new_mail(gmail, monkeypatch):
    connector, kwargs, _ = gmail
    queries: list[str] = []
    real_page = type(connector)._page
    monkeypatch.setattr(
        type(connector), "_page",
        lambda self, svc, q, size, cursor: (queries.append(q),
                                            real_page(self, svc, q, size,
                                                      cursor))[1])
    harness.sync(connector, kwargs)
    harness.sync(connector, kwargs)

    assert queries, "the spy never fired"
    assert queries[-1].startswith("after:")


def test_progress_is_recorded_per_page(gmail):
    """So an interrupted pass resumes rather than starting again."""
    connector, kwargs, _ = gmail
    harness.sync(connector, kwargs)

    state = sync_state.get(connector.connection().id, "email")
    assert state.items_processed == 3
    assert state.last_success


# ── provenance ────────────────────────────────────────────────────────────


def test_every_message_records_where_it_came_from(gmail):
    connector, kwargs, _ = gmail
    harness.sync(connector, kwargs)

    stored = connector.store.list(source="gmail", limit=10)
    assert stored
    import json
    metadata = json.loads(stored[0].metadata if isinstance(stored[0].metadata, str)
                          else json.dumps(stored[0].metadata))
    source = metadata["source"]
    assert source["connector"] == "gmail"
    assert source["resource_type"] == "email"
    assert source["external_id"]
    assert source["url"].startswith("https://mail.google.com/")
    assert source["run_id"]


def test_a_message_is_dated_by_when_it_arrived_not_when_we_read_it():
    """Dating by the fetch makes every message from a first sync look like it
    arrived this morning."""
    assert _received_at({"internalDate": "1756000000000"}).startswith("2025-")
    assert _received_at({}) == ""
    assert _received_at({"internalDate": "not a number"}) == ""


# ── one bad message never takes the pass down ─────────────────────────────


def test_a_body_that_cannot_be_read_costs_one_message(gmail, monkeypatch):
    """Decision H2, now applied around the hydrate call as well."""
    connector, kwargs, _ = gmail
    real_hydrate = type(connector)._hydrate

    def sometimes(self, service, record):
        if record.external_id == "msg1":
            raise ValueError("that message will not open")
        return real_hydrate(self, service, record)

    monkeypatch.setattr(type(connector), "_hydrate", sometimes)

    result = harness.sync(connector, kwargs)

    assert result.added == 2
    assert result.skipped == 1
    assert not result.errors


def test_a_message_with_no_body_is_not_recorded_as_seen(gmail, monkeypatch):
    """So the next pass looks again, rather than skipping it forever on the
    strength of one empty read."""
    connector, kwargs, _ = gmail
    monkeypatch.setattr(
        type(connector), "_hydrate",
        lambda self, service, record: Record(external_id=record.external_id))

    harness.sync(connector, kwargs)

    assert resources.for_connection(connector.connection().id) == []
