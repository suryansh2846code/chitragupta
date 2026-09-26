"""The four guarantees `base.Connector` now makes, held to.

Each of these was previously either absent or written by hand once per
connector, which is the same thing as absent for the twelfth one.
"""
from __future__ import annotations

import threading

import pytest

from chitragupta.connectors import REGISTRY

from . import harness
from .harness import FAKES

IDS = [f.name for f in FAKES]
CLASSES = sorted(REGISTRY.items())
CLASS_IDS = [name for name, _ in CLASSES]


# ── 1.5 — auto-sync is a declared property, not a list elsewhere ────────────

@pytest.mark.parametrize("name,cls", CLASSES, ids=CLASS_IDS)
def test_every_connector_states_whether_it_auto_syncs(name, cls):
    """The scheduler used to hold five names and exclude the other six by
    omission. GitHub and Linear were token-backed sources a user connects and
    which then never refreshed again, and nothing recorded that as a choice."""
    assert isinstance(cls.auto_sync, bool)
    assert isinstance(cls.incremental, bool)


def test_the_scheduler_derives_its_list_from_the_classes():
    from chitragupta.scheduler import _auto_connectors

    derived = set(_auto_connectors())
    declared = {name for name, cls in REGISTRY.items()
                if cls.auto_sync and cls.supported_here()}
    assert derived == declared


def test_manual_sources_are_not_polled():
    """Notes is written by hand and Files is driven by remembered folders, so
    neither has anything for a timer to check."""
    from chitragupta.scheduler import _auto_connectors

    assert "notes" not in _auto_connectors()
    assert "files" not in _auto_connectors()


# ── 1.1 — the watermark ────────────────────────────────────────────────────

INCREMENTAL = [f for f in FAKES if f.name != "notes"]


@pytest.mark.parametrize("fake", INCREMENTAL, ids=[f.name for f in INCREMENTAL])
def test_a_successful_sync_stores_a_cursor(fake, monkeypatch, fake_module, tmp_path):
    conn, kwargs = harness.build(fake, monkeypatch, fake_module, tmp_path, 2)
    if not conn.incremental:
        pytest.skip(f"{fake.name} does not claim to be incremental")

    harness.sync(conn, kwargs)

    assert conn.last_cursor(), f"{fake.name} finished cleanly but stored no cursor"


@pytest.mark.parametrize("fake", INCREMENTAL, ids=[f.name for f in INCREMENTAL])
def test_a_failed_sync_does_not_move_the_watermark(fake, monkeypatch, fake_module,
                                                   tmp_path):
    """A cursor from a pass that did not finish would skip records nobody ever
    fetched, and nothing would go back for them."""
    conn, kwargs = harness.build(fake, monkeypatch, fake_module, tmp_path, 2)
    if not conn.incremental:
        pytest.skip(f"{fake.name} does not claim to be incremental")

    from chitragupta.connectors.base import SyncResult

    broken = SyncResult(connector=conn.name, cursor="2026-09-13T00:00:00+00:00",
                        errors=["something went wrong"])
    conn._finish(broken)

    assert not conn.last_cursor(), (
        f"{fake.name} advanced its watermark on a failed sync")


@pytest.mark.parametrize("fake", INCREMENTAL, ids=[f.name for f in INCREMENTAL])
def test_a_cancelled_sync_does_not_move_the_watermark(fake, monkeypatch,
                                                      fake_module, tmp_path):
    conn, kwargs = harness.build(fake, monkeypatch, fake_module, tmp_path, 2)
    if not conn.incremental:
        pytest.skip(f"{fake.name} does not claim to be incremental")

    from chitragupta.connectors.base import SyncResult

    conn._finish(SyncResult(connector=conn.name, cursor="2026-09-13T00:00:00+00:00",
                            cancelled=True))

    assert not conn.last_cursor()


def test_since_applies_an_overlap():
    """An item that lands *during* a sync must not sit just behind the new
    watermark forever. Re-fetching is free — dedup is a content hash — so the
    overlap is deliberately generous."""
    from datetime import UTC, datetime, timedelta

    from chitragupta.connectors.gmail import GmailConnector

    conn = GmailConnector()
    stamp = datetime.now(UTC)
    conn.store.set_connector_state(conn.name, cursor=stamp.isoformat())

    resume = datetime.fromisoformat(conn.since())

    assert resume < stamp, "since() returned the raw cursor with no overlap"
    assert stamp - resume >= timedelta(minutes=conn.overlap_minutes - 1)


def test_an_unreadable_cursor_falls_back_to_a_full_window():
    """A watermark whose format changed must cause a re-scan, never a no-op.

    Returning a broken value here would make the connector sync nothing, for
    ever, with no error — the worst available outcome.
    """
    from chitragupta.connectors.gmail import GmailConnector

    conn = GmailConnector()
    conn.store.set_connector_state(conn.name, cursor="not-a-timestamp")

    assert conn.since() is None


def test_full_history_ignores_the_watermark():
    from chitragupta.connectors.gmail import GmailConnector

    conn = GmailConnector()
    conn.store.set_connector_state(conn.name, cursor="2026-01-01T00:00:00+00:00")

    assert conn.since(full_history=True) is None


def test_the_second_gmail_sync_asks_only_for_new_mail(monkeypatch, fake_module,
                                                      tmp_path):
    """The point of the whole exercise: the background loop runs every 30
    minutes against a 90-day window, so without this it re-downloaded up to 600
    messages each time to discard nearly all of them."""
    queries: list[str] = []
    conn, kwargs = harness.build(harness.BY_NAME["gmail"], monkeypatch,
                                 fake_module, tmp_path, 2)
    # Spied at `_page`, which is where the query now goes. It used to be
    # `_list`, and that method is still there — `search_and_ingest` and
    # `list_inbox` use it — so a spy on it would have gone right on passing
    # while measuring nothing the sync does.
    real_page = type(conn)._page
    monkeypatch.setattr(type(conn), "_page",
                        lambda self, svc, q, size, cursor: (
                            queries.append(q),
                            real_page(self, svc, q, size, cursor))[1])

    harness.sync(conn, kwargs)
    harness.sync(conn, kwargs)

    assert "newer_than:" in queries[0], "the first pass should use the full window"
    assert queries[1].startswith("after:"), (
        f"the second pass re-requested the whole window: {queries[1]}")


# ── 1.3 — cancellation reaches inside the item loop ────────────────────────

@pytest.mark.parametrize("fake", FAKES, ids=IDS)
def test_a_cancelled_sync_stops_early(fake, monkeypatch, fake_module, tmp_path):
    """Cancellation used to be checked only *between* connectors, so stopping
    mid-Gmail still waited for every remaining message."""
    conn, kwargs = harness.build(fake, monkeypatch, fake_module, tmp_path, 4)
    already = threading.Event()
    already.set()

    result = conn.sync(interactive=False, cancel=already, **kwargs)

    assert result.added == 0, (
        f"{fake.name} ingested {result.added} items despite being cancelled "
        "before it started")


def test_cancelling_partway_keeps_what_landed(monkeypatch, fake_module, tmp_path):
    """Stopping is not rolling back: the items already stored stay stored, and
    the watermark does not move past the ones that never ran."""
    conn, kwargs = harness.build(harness.BY_NAME["github"], monkeypatch,
                                 fake_module, tmp_path, 6)

    class StopAfterTwo:
        def __init__(self): self.seen = 0
        def is_set(self):
            self.seen += 1
            return self.seen > 2

    result = conn.sync(interactive=False, cancel=StopAfterTwo(), **kwargs)

    assert result.cancelled
    assert 0 < result.added < 6
    assert not conn.last_cursor(), "a cancelled pass moved the watermark"


# ── 1.4 — progress ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("fake", FAKES, ids=IDS)
def test_progress_is_reported_per_item(fake, monkeypatch, fake_module, tmp_path):
    """A first sync that reports nothing until it finishes is a spinner with no
    end state, which CLAUDE.md calls a bug."""
    conn, kwargs = harness.build(fake, monkeypatch, fake_module, tmp_path, 3)
    ticks: list[tuple[int, int, str]] = []

    conn.sync(interactive=False, progress=lambda d, t, l: ticks.append((d, t, l)),
              **kwargs)

    assert ticks, f"{fake.name} reported no progress at all"
    done = [t[0] for t in ticks]
    assert done == sorted(done), "progress went backwards"
    assert all(t[2] for t in ticks), "progress carried no label to show"
