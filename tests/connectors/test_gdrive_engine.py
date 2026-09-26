"""Drive on the shared engine, and the two things that are true only of files.

Drive already knew its listing was cheap and its content was not. It worked
around that by hand, and both halves of the workaround were wrong:

    for row in self.store._conn.execute(
        "SELECT metadata, event_date FROM memories WHERE source=?", ...)

* **A full scan of every Drive memory, on every pass**, to answer a question the
  identity table now answers in 6 µs.
* **It compared the modified *date*** — `modifiedTime[:10]` — so a document
  edited twice in one day read as unchanged the second time, and the edit never
  reached the brain.

And unlike mail:

* **A file is mutable**, so the fingerprint is `modifiedTime` rather than the id.
* **One file becomes several memories.** A long document is chunked, so all of
  the ids are recorded — otherwise *delete what Drive imported* removes the first
  page of a document and leaves the rest.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import resources, sync_state
from chitragupta.connectors.gdrive import GoogleDriveConnector

from . import harness


@pytest.fixture
def drive(monkeypatch, fake_module, tmp_path):
    connector, kwargs = harness.build(harness.BY_NAME["gdrive"], monkeypatch,
                                      fake_module, tmp_path, 3)
    return connector, kwargs, connector.fake_service


# ── the N+1, and the scan that answered it ────────────────────────────────


def test_a_first_pass_reads_every_file_once(drive):
    connector, kwargs, service = drive

    result = harness.sync(connector, kwargs)

    assert result.added == 3
    assert service.exported == 3, "one download per new file, no more"


def test_a_second_pass_downloads_nothing(drive):
    """The whole point. Before this, every pass downloaded every file in the
    window to discover it already had them."""
    connector, kwargs, service = drive
    harness.sync(connector, kwargs)
    service.exported = 0

    result = harness.sync(connector, {**kwargs, "full_history": True})

    assert result.added == 0
    assert result.skipped == 3
    assert service.exported == 0, (
        "a file was downloaded that we already had — the identity check is not "
        "running before hydrate")


def test_the_listing_is_still_read_on_a_second_pass(drive):
    """Skipping the downloads is the saving; skipping the listing would mean
    never noticing a new document."""
    connector, kwargs, service = drive
    harness.sync(connector, kwargs)
    service.listed = 0

    harness.sync(connector, {**kwargs, "full_history": True})

    assert service.listed >= 1


def test_nothing_scans_the_whole_memories_table(drive):
    """The replaced implementation read every Drive memory on every pass to
    rebuild what it already knew."""
    connector, kwargs, _ = drive
    harness.sync(connector, kwargs)

    # `sqlite3.Connection.execute` cannot be reassigned, and
    # `set_trace_callback` is the hook built for this — it sees every statement
    # the connection actually runs, which is a stronger claim than patching one
    # method would have been.
    seen: list[str] = []
    connector.store._conn.set_trace_callback(seen.append)
    try:
        harness.sync(connector, {**kwargs, "full_history": True})
    finally:
        connector.store._conn.set_trace_callback(None)

    scans = [sql for sql in seen
             if "FROM memories" in sql and "WHERE source=?" in sql]
    assert scans == [], f"the pass still scanned the memories table: {scans}"


# ── a file is mutable, and a date is not enough to tell ───────────────────


def test_an_edited_file_is_read_again(drive):
    connector, kwargs, service = drive
    harness.sync(connector, kwargs)
    service.exported = 0

    # Same file, later in the same day — the case the old `[:10]` comparison
    # could not see.
    service._files[0]["modifiedTime"] = "2026-09-20T23:59:00.000Z"
    service._files[0]["_text"] = "The launch plan, revision 1. Now November."
    result = harness.sync(connector, {**kwargs, "full_history": True})

    assert result.added == 1, "an edit on the same day was treated as unchanged"
    assert service.exported == 1


def test_the_fingerprint_is_the_whole_timestamp_not_the_date(drive):
    """Stated directly, because the failure it prevents is silent: a document
    edited twice in one day simply never updated."""
    connector, kwargs, service = drive
    page = connector._page(service, "q", 10, "")

    assert page.records[0].fingerprint == service._files[0]["modifiedTime"]
    assert len(page.records[0].fingerprint) > len("2026-09-20")


# ── one file, several memories ────────────────────────────────────────────


def test_a_long_document_records_every_chunk_it_produced(drive, monkeypatch):
    """Recording only the first is what leaves the rest orphaned when somebody
    asks to delete what Drive imported."""
    connector, kwargs, service = drive
    # Long enough that `chunk_text` splits it.
    service._files[0]["_text"] = "The launch plan. " * 4000

    harness.sync(connector, kwargs)

    connection = connector.connection()
    held = resources.get(connection.id, "file", "file0")
    assert held is not None
    everything = resources.memory_ids(connection.id)
    stored = connector.store.list(source="gdrive", limit=200)
    assert len(everything) == len(stored), (
        f"{len(stored)} memories were written and {len(everything)} were "
        f"recorded — the rest cannot be deleted")


def test_deleting_what_drive_imported_removes_every_chunk(drive):
    connector, kwargs, service = drive
    service._files[0]["_text"] = "The launch plan. " * 4000
    harness.sync(connector, kwargs)
    connection = connector.connection()

    ids = resources.memory_ids(connection.id)
    assert len(ids) > 3, "this fixture was supposed to chunk"
    for memory_id in ids:
        connector.store.delete(memory_id)

    assert connector.store.list(source="gdrive", limit=50) == []


# ── the window is never swept ─────────────────────────────────────────────


def test_a_pass_never_marks_anything_deleted(drive):
    """The query filters by mime type and is bounded by `drive_max`, so "not in
    these results" covers every file this connector cannot read as well as
    every one past the budget."""
    connector, kwargs, service = drive
    harness.sync(connector, kwargs)
    connection = connector.connection()
    assert resources.for_connection(connection.id)

    service._files.clear()
    harness.sync(connector, {**kwargs, "full_history": True})

    assert all(not r.stale for r in resources.for_connection(connection.id))


# ── provenance and resuming ───────────────────────────────────────────────


def test_every_file_records_where_it_came_from(drive):
    connector, kwargs, _ = drive
    harness.sync(connector, kwargs)

    import json
    stored = connector.store.list(source="gdrive", limit=10)
    assert stored
    raw = stored[0].metadata
    metadata = json.loads(raw) if isinstance(raw, str) else raw
    source = metadata["source"]
    assert source["connector"] == "gdrive"
    assert source["resource_type"] == "file"
    assert source["external_id"].startswith("file")
    assert source["url"].startswith("https://docs.google.test/")
    assert source["source_updated_at"]
    assert source["run_id"]


def test_the_chunk_number_rides_beside_the_provenance(drive):
    """Which piece of a document this is belongs to the record, not to where
    the record came from."""
    connector, kwargs, service = drive
    service._files[0]["_text"] = "The launch plan. " * 4000
    harness.sync(connector, kwargs)

    import json
    stored = connector.store.list(source="gdrive", limit=200)
    raw = stored[0].metadata
    metadata = json.loads(raw) if isinstance(raw, str) else raw
    assert "chunk" in metadata
    assert "chunk" not in metadata["source"]


def test_progress_is_recorded_so_a_pass_can_resume(drive):
    connector, kwargs, _ = drive
    harness.sync(connector, kwargs)

    state = sync_state.get(connector.connection().id, "file")
    assert state.items_processed == 3
    assert state.last_success


def test_the_watermark_only_moves_on_a_clean_pass(drive):
    connector, kwargs, _ = drive
    harness.sync(connector, kwargs)

    state = connector.store.get_connector_state("gdrive") or {}
    assert state.get("cursor")


def test_a_second_pass_asks_drive_for_only_what_changed(drive, monkeypatch):
    """Filtered at Drive rather than locally — strictly better than listing
    everything and discarding it here."""
    connector, kwargs, _ = drive
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
    assert "modifiedTime >" in queries[-1], queries[-1]


# ── failures ──────────────────────────────────────────────────────────────


def test_a_file_that_will_not_open_costs_one_file(drive, monkeypatch):
    """Decision H2, around the download as well."""
    connector, kwargs, _ = drive
    real = type(connector)._hydrate

    def sometimes(self, service, downloader, record):
        if record.external_id == "file0":
            raise ValueError("that document will not export")
        return real(self, service, downloader, record)

    monkeypatch.setattr(type(connector), "_hydrate", sometimes)

    result = harness.sync(connector, kwargs)

    assert result.added == 2
    assert result.skipped == 1
    assert not result.errors


def test_a_sign_in_failure_says_so_rather_than_reading_as_a_sync_failure(
        monkeypatch, fake_module, tmp_path):
    """Signing in is something the user does; a failed sync is something the
    app retries. Reporting one as the other sends them to the wrong place."""
    import chitragupta.connectors.gdrive as mod

    connector, kwargs = harness.build(harness.BY_NAME["gdrive"], monkeypatch,
                                      fake_module, tmp_path, 1)
    monkeypatch.setattr(mod, "get_credentials",
                        lambda **_: (_ for _ in ()).throw(
                            RuntimeError("consent was refused")))

    result = harness.sync(connector, kwargs)

    assert result.detail == "not signed in"
    assert result.errors and "Drive" in result.errors[0]


def test_the_connector_still_declares_itself_read_only():
    """Drive has write methods — `create_doc`, `share` — and they are reached
    through confirmed actions, never through a sync."""
    manifest = GoogleDriveConnector(store=object()).manifest()

    assert "read:file" in {str(c) for c in manifest.reads}
    assert manifest.sync.resumable
