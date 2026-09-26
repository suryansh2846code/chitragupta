"""Calendar on the shared engine — the shape that needs no second request.

Gmail and Drive both pay one request per record for the content. Calendar does
not: `events.list` answers with whole events, so the listing *is* the content and
there is no `hydrate` stage. That is worth a test of its own, because a stage
added here would fetch what we already have and nobody would notice.

The two real bugs it had were not about cost:

* **It read one page.** `maxResults=250` and whatever came back — so a calendar
  with more than 250 events across the ±360-day window silently lost the rest.
  No error, no mention, just fewer events than the user has.
* **A cancelled meeting stayed forever.** Cancellations were invisible, so agents
  kept citing a meeting that was called off. Google says so explicitly with
  `status: "cancelled"` once `showDeleted` is on — a fact, rather than the
  inference a sweep would need over a window that cannot support one.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import resources
from chitragupta.connectors.gcal import GoogleCalendarConnector

from . import harness


def event(index: int, **over) -> dict:
    base = {
        "id": f"evt{index}",
        "summary": f"Design review {index}",
        "start": {"dateTime": f"2026-11-{index % 28 + 1:02d}T10:00:00Z"},
        "end": {"dateTime": f"2026-11-{index % 28 + 1:02d}T11:00:00Z"},
        "location": "Room 4",
        "description": f"Agenda item {index}.",
        "updated": f"2026-09-20T{index % 24:02d}:00:00.000Z",
        "htmlLink": f"https://calendar.google.test/event?eid={index}",
    }
    return {**base, **over}


@pytest.fixture
def calendar(monkeypatch, fake_module, tmp_path):
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 3)
    return connector, kwargs


def wire(connector, monkeypatch, fake_module, events, *, page_size=2500):
    """Point the connector at a specific set of events."""
    service = harness.FakeCalendarService(events, page_size=page_size)
    harness.install_google(monkeypatch, fake_module, service)
    return service


# ── no second request, and that is the point ──────────────────────────────


def test_the_listing_is_the_content_so_there_is_no_hydrate_stage():
    """Gmail and Drive both need one. A stage here would re-fetch what the
    listing already carried, and nothing would report it."""
    connector = GoogleCalendarConnector(store=object())

    assert not hasattr(connector, "_hydrate")


def test_an_event_is_stored_from_the_listing_alone(calendar):
    connector, kwargs = calendar

    result = harness.sync(connector, kwargs)

    assert result.added == 3
    stored = connector.store.list(source="gcal", limit=10)
    assert stored
    assert "Design review" in stored[0].text
    assert "Room 4" in stored[0].text


# ── it reads every page ───────────────────────────────────────────────────


def test_a_calendar_bigger_than_one_page_is_read_whole(monkeypatch, fake_module,
                                                       tmp_path):
    """The bug: `maxResults=250` and one request, so everything past the first
    page was silently lost."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    service = wire(connector, monkeypatch, fake_module,
                   [event(i) for i in range(7)], page_size=2)

    result = harness.sync(connector, {**kwargs, "max_results": 100})

    assert result.added == 7, "events past the first page were dropped"
    assert service.listed >= 4, "it never asked for a second page"


def test_the_budget_is_still_honoured(monkeypatch, fake_module, tmp_path):
    """Paging must not mean reading a whole calendar when the caller asked for
    less."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    wire(connector, monkeypatch, fake_module, [event(i) for i in range(20)],
         page_size=3)

    result = harness.sync(connector, {**kwargs, "limit": 5})

    assert result.added == 5


# ── a cancelled meeting stops being current ───────────────────────────────


def test_a_cancellation_is_recorded_rather_than_ignored(monkeypatch,
                                                        fake_module, tmp_path):
    """It used to stay in the brain forever and agents kept citing it."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    events = [event(0), event(1)]
    wire(connector, monkeypatch, fake_module, events)
    harness.sync(connector, kwargs)
    connection = connector.connection()
    assert not resources.get(connection.id, "event", "evt1").stale

    events[1] = event(1, status="cancelled",
                      updated="2026-09-21T10:00:00.000Z")
    wire(connector, monkeypatch, fake_module, events)
    harness.sync(connector, kwargs)

    held = resources.get(connection.id, "event", "evt1")
    assert held is not None and held.stale


def test_a_cancellation_marks_and_never_erases(monkeypatch, fake_module,
                                               tmp_path):
    """A meeting that was called off is still something the user may ask about,
    and "that was cancelled" is more use to an agent than silence."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    events = [event(0)]
    wire(connector, monkeypatch, fake_module, events)
    harness.sync(connector, kwargs)
    before = len(connector.store.list(source="gcal", limit=50))

    events[0] = event(0, status="cancelled")
    wire(connector, monkeypatch, fake_module, events)
    harness.sync(connector, kwargs)

    assert len(connector.store.list(source="gcal", limit=50)) == before


def test_an_event_cancelled_before_we_ever_saw_it_is_simply_skipped(
        monkeypatch, fake_module, tmp_path):
    """A tombstone for something never ingested is a row describing nothing."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    wire(connector, monkeypatch, fake_module, [event(0, status="cancelled")])

    result = harness.sync(connector, kwargs)

    assert result.added == 0
    assert resources.for_connection(connector.connection().id) == []


def test_it_asks_for_cancellations_at_all(monkeypatch, fake_module, tmp_path):
    """`showDeleted` is the whole mechanism — without it a cancellation is an
    absence, and absence over a bounded window means nothing."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    asked: list[dict] = []
    service = wire(connector, monkeypatch, fake_module, [event(0)])
    original = service.list
    service.list = lambda **kw: (asked.append(kw), original(**kw))[1]

    harness.sync(connector, kwargs)

    assert asked and asked[0].get("showDeleted") is True


# ── a window is never swept ───────────────────────────────────────────────


def test_an_event_that_aged_out_of_the_window_is_not_deleted(monkeypatch,
                                                             fake_module,
                                                             tmp_path):
    """Sweeping a ±180-day window would tombstone every event that merely aged
    out of it — which is every event, eventually."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    wire(connector, monkeypatch, fake_module, [event(0), event(1)])
    harness.sync(connector, kwargs)
    connection = connector.connection()

    # The window moves on and no longer covers them.
    wire(connector, monkeypatch, fake_module, [])
    harness.sync(connector, kwargs)

    assert all(not r.stale for r in resources.for_connection(connection.id))


# ── a moved meeting is re-read; an untouched one is not ───────────────────


def test_an_unchanged_event_is_skipped(calendar):
    connector, kwargs = calendar
    harness.sync(connector, kwargs)

    result = harness.sync(connector, kwargs)

    assert result.added == 0
    assert result.skipped == 3


def test_a_moved_meeting_is_read_again(monkeypatch, fake_module, tmp_path):
    """An event is mutable, so the change detector is Google's own `updated`
    rather than the id."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    events = [event(0)]
    wire(connector, monkeypatch, fake_module, events)
    harness.sync(connector, kwargs)

    events[0] = event(0, summary="Design review, moved",
                      updated="2026-09-25T09:00:00.000Z")
    wire(connector, monkeypatch, fake_module, events)
    result = harness.sync(connector, kwargs)

    assert result.added == 1


# ── an event is dated by when it happens ──────────────────────────────────


def test_an_event_is_filed_under_when_it_happens_not_when_it_was_edited(
        monkeypatch, fake_module, tmp_path):
    """Provenance derives the date from `updated`; the start has to win, or next
    month's meeting is filed under today."""
    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 0)
    wire(connector, monkeypatch, fake_module, [
        event(0, start={"dateTime": "2026-11-14T10:00:00Z"},
              updated="2026-09-20T08:00:00.000Z")])

    harness.sync(connector, kwargs)

    stored = connector.store.list(source="gcal", limit=5)
    assert stored[0].event_date == "2026-11-14", stored[0].event_date


def test_every_event_records_where_it_came_from(calendar):
    connector, kwargs = calendar
    harness.sync(connector, kwargs)

    import json
    stored = connector.store.list(source="gcal", limit=5)
    raw = stored[0].metadata
    metadata = json.loads(raw) if isinstance(raw, str) else raw
    source = metadata["source"]
    assert source["connector"] == "gcal"
    assert source["resource_type"] == "event"
    assert source["external_id"].startswith("evt")
    assert source["run_id"]


# ── failures ──────────────────────────────────────────────────────────────


def test_a_sign_in_failure_says_so_rather_than_reading_as_a_sync_failure(
        monkeypatch, fake_module, tmp_path):
    import chitragupta.connectors.gcal as mod

    connector, kwargs = harness.build(harness.BY_NAME["gcal"], monkeypatch,
                                      fake_module, tmp_path, 1)
    monkeypatch.setattr(mod, "get_credentials",
                        lambda **_: (_ for _ in ()).throw(
                            RuntimeError("consent was refused")))

    result = harness.sync(connector, kwargs)

    assert result.detail == "not signed in"
    assert result.errors


def test_it_still_keeps_no_watermark():
    """The window is bounded around today, so a watermark would only hide edits
    to events that have not moved in time — which is most edits."""
    manifest = GoogleCalendarConnector(store=object()).manifest()

    assert manifest.sync.incremental is False
    assert manifest.sync.overlap_minutes == 0
    assert manifest.sync.resumable is True
