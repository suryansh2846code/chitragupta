"""A crashed sync resumes from the last page that landed, not from zero.

`base._finish` stores a watermark only for a clean pass:

    if result.cursor and not result.cancelled and not result.errors:

That rule is right and must not be relaxed — a cursor from a half-finished pass
moves past records nobody fetched. What it costs is that a 2,000-item sync
failing at item 1,900 starts again at zero, every time. The fix is to make the
*unit* smaller: checkpoint per page, after the page is committed.

Two orderings, and only one of them is survivable:

* ingest → commit → checkpoint: a crash loses the page we were on. It is
  fetched again next pass, dedup absorbs it.
* checkpoint → ingest: a crash loses the page **and reports success for it**.
  The records are gone and nothing will ever go back.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import sync_state
from chitragupta.connectors.sync_state import DEFAULT_RESOURCE


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from chitragupta.config import get_settings
    from chitragupta.connectors import db

    monkeypatch.setattr(get_settings(), "home", tmp_path, raising=False)
    db.reset_for_tests()
    yield
    db.reset_for_tests()


# ── a position always exists ──────────────────────────────────────────────


def test_a_connector_that_has_never_synced_has_a_position():
    """The beginning is a position. Returning None would make every caller
    write the same `or SyncState(...)` and one of them would forget."""
    state = sync_state.get("gmail:a1")

    assert state.cursor == ""
    assert not state.in_flight


# ── per connection, per resource ──────────────────────────────────────────


def test_two_accounts_do_not_share_a_cursor():
    """What `connector_state` could not express: each pass moved the other
    account's watermark."""
    sync_state.checkpoint("gmail:personal", cursor="p100")
    sync_state.checkpoint("gmail:work", cursor="w200")

    assert sync_state.get("gmail:personal").cursor == "p100"
    assert sync_state.get("gmail:work").cursor == "w200"


def test_two_resource_types_do_not_share_a_cursor():
    """A connector reading files *and* comments has two positions. One cursor
    for both moves each past the other's records."""
    sync_state.checkpoint("gdrive:a1", "file", cursor="f10")
    sync_state.checkpoint("gdrive:a1", "comment", cursor="c20")

    assert sync_state.get("gdrive:a1", "file").cursor == "f10"
    assert sync_state.get("gdrive:a1", "comment").cursor == "c20"
    assert {s.resource_type for s in sync_state.for_connection("gdrive:a1")} \
        == {"file", "comment"}


# ── attempt and success are different facts ───────────────────────────────


def test_starting_a_pass_moves_the_attempt_and_not_the_success():
    """A single `last_sync` cannot tell "it ran nine minutes ago" from "it
    last worked nine days ago", and those are the two things a health row has
    to say."""
    sync_state.complete("gmail:a1")
    worked_at = sync_state.get("gmail:a1").last_success

    sync_state.begin("gmail:a1")

    after = sync_state.get("gmail:a1")
    assert after.last_attempt > ""
    assert after.last_success == worked_at, "an attempt is not a success"


def test_a_clean_finish_moves_the_success():
    sync_state.begin("gmail:a1")
    sync_state.complete("gmail:a1")

    state = sync_state.get("gmail:a1")
    assert state.last_success
    assert not state.in_flight
    assert state.error == ""


# ── the resumable half ────────────────────────────────────────────────────


def test_a_failed_pass_keeps_the_pages_that_landed():
    """The whole point. `base._finish` has to discard the cursor because its
    unit is the pass; here the unit is the page, and the pages that landed
    still landed."""
    sync_state.begin("gmail:a1")
    sync_state.checkpoint("gmail:a1", cursor="page3", items=300)

    sync_state.fail("gmail:a1", error="the connection dropped")

    state = sync_state.get("gmail:a1")
    assert state.cursor == "page3", "resume from page 3, not from zero"
    assert state.items_processed == 300
    assert state.error


def test_a_cancelled_pass_keeps_them_too():
    sync_state.begin("gmail:a1")
    sync_state.checkpoint("gmail:a1", cursor="page2", items=200)

    sync_state.interrupted("gmail:a1")

    state = sync_state.get("gmail:a1")
    assert state.cursor == "page2"
    assert not state.in_flight
    assert state.error == "", "stopping on purpose is not an error"


def test_items_accumulate_across_a_resumed_pass():
    """So a resumed sync reports what it has done in total rather than what
    this attempt did."""
    sync_state.checkpoint("gmail:a1", cursor="p1", items=100)
    sync_state.checkpoint("gmail:a1", cursor="p2", items=100)

    assert sync_state.get("gmail:a1").items_processed == 200


# ── surviving a crash ─────────────────────────────────────────────────────


def test_a_pass_that_never_finished_is_visible_afterwards():
    """A `last_attempt` with no ending. The only way to tell a resumable
    position from a finished one."""
    sync_state.begin("gmail:a1")
    sync_state.checkpoint("gmail:a1", cursor="page7")

    assert sync_state.get("gmail:a1").in_flight


def test_recovery_reports_a_crash_and_does_not_reset_the_cursor():
    """The cursor is the last committed page and is correct. The only thing
    wrong is the in-flight marker — clearing that is what lets the next pass
    resume rather than be refused as already running."""
    sync_state.begin("gmail:a1")
    sync_state.checkpoint("gmail:a1", cursor="page7", items=700)

    found = sync_state.recover("gmail:a1")

    assert [s.cursor for s in found] == ["page7"]
    after = sync_state.get("gmail:a1")
    assert after.cursor == "page7"
    assert not after.in_flight


def test_recovery_on_a_clean_connection_finds_nothing():
    sync_state.begin("gmail:a1")
    sync_state.complete("gmail:a1")

    assert sync_state.recover("gmail:a1") == []


def test_state_outlives_the_process():
    from chitragupta.connectors import db

    sync_state.checkpoint("gmail:a1", cursor="page9", items=900)

    db.reset_for_tests()          # as if the app had been quit and reopened

    assert sync_state.get("gmail:a1").cursor == "page9"


# ── versioning ────────────────────────────────────────────────────────────


def test_a_cursor_from_an_older_connector_version_is_not_resumed_into():
    """A cursor written by version 1 means something under version 2 only if
    nothing about the fetch changed. Resuming regardless is how a bump
    silently skips everything the new shape would have read."""
    sync_state.checkpoint("gmail:a1", cursor="v1-page3", version=1)

    state = sync_state.get("gmail:a1")

    assert state.resumable_from(1) == "v1-page3"
    assert state.resumable_from(2) == "", "re-scan rather than resume"


def test_an_empty_resume_means_the_normal_window_not_nothing():
    """The same contract `base.Connector.since()` documents. Callers that read
    it as "fetch nothing" sync nothing, silently, forever."""
    assert sync_state.get("new:connection").resumable_from(1) == ""


# ── starting over ─────────────────────────────────────────────────────────


def test_clearing_a_position_makes_the_next_pass_re_read():
    sync_state.checkpoint("gmail:a1", cursor="page9", items=900)

    sync_state.clear("gmail:a1")

    assert sync_state.get("gmail:a1").cursor == ""


def test_clearing_one_resource_leaves_the_others():
    sync_state.checkpoint("gdrive:a1", "file", cursor="f1")
    sync_state.checkpoint("gdrive:a1", "comment", cursor="c1")

    sync_state.clear("gdrive:a1", "file")

    assert sync_state.get("gdrive:a1", "file").cursor == ""
    assert sync_state.get("gdrive:a1", "comment").cursor == "c1"


# ── a run id ties a pass to everything it produced ────────────────────────


def test_a_pass_gets_an_id_that_travels():
    run = sync_state.begin("gmail:a1")

    assert run
    assert sync_state.get("gmail:a1").run_id == run


def test_two_passes_get_different_ids():
    first = sync_state.begin("gmail:a1")
    second = sync_state.begin("gmail:a1")

    assert first != second


def test_the_default_resource_is_named_not_blank():
    """So a row is legible, and so a second resource type can be added later
    without the first one's rows meaning "all of them"."""
    assert DEFAULT_RESOURCE
    sync_state.checkpoint("x:1", cursor="c")

    assert sync_state.get("x:1").resource_type == DEFAULT_RESOURCE
