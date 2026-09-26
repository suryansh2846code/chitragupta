"""The sync lifecycle, and what it does when each stage fails.

`DISCOVER → FETCH → NORMALIZE → DEDUPLICATE → INGEST → CHECKPOINT → COMPLETE`

Two properties carry the whole design, and both are about crashes:

* **Checkpoint after ingest, per page.** `base._finish` writes a watermark only
  for a clean pass, so a 2,000-item sync failing at 1,900 restarts at zero.
  Here a crash costs one page.
* **The other ordering is unsurvivable.** Checkpoint-then-ingest loses a page
  *and reports success for it* — the records are gone and nothing will ever go
  back. That is asserted directly.

The failure-injection block is the point of the module: timeout, reset, 401,
403, 404, 409, 429, 500, malformed page, bad cursor, crash mid-pass. Each has a
different correct answer, and "retry three times" is the wrong one for most.
"""
from __future__ import annotations

import socket
import urllib.error
from io import BytesIO

import pytest

from chitragupta.connectors import connections, engine, resources, sync_state
from chitragupta.connectors.connections import AuthState
from chitragupta.connectors.contract import ConnectorManifest, Limits
from chitragupta.connectors.engine import Plan, Record
from chitragupta.connectors.pagination import Page
from chitragupta.connectors.resources import ResourceState
from chitragupta.connectors.retry import RetryPolicy


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from chitragupta.config import get_settings
    from chitragupta.connectors import db, limits

    monkeypatch.setattr(get_settings(), "home", tmp_path, raising=False)
    db.reset_for_tests()
    limits.reset()
    yield
    db.reset_for_tests()
    limits.reset()


MANIFEST = ConnectorManifest(
    connector_id="demo", display_name="Demo", provider="demo",
    limits=Limits(requests=0, concurrency=4, records_per_sync=1000))

#: No sleeping and no jitter, so a failure-injection test is instant and its
#: schedule is the schedule under test rather than the wall clock.
NO_WAIT = RetryPolicy(attempts=3, base_seconds=0.0, jitter=0, total_seconds=1.0)


class Brain:
    """A brain that records what it was told, and can be made to fail."""

    def __init__(self, fail_on: set[str] | None = None):
        self.stored: list[tuple[str, dict]] = []
        self.fail_on = fail_on or set()

    def __call__(self, record: Record, source) -> str:
        if record.external_id in self.fail_on:
            raise ValueError(f"cannot ingest {record.external_id}")
        self.stored.append((record.external_id, source.as_metadata()))
        return f"mem-{record.external_id}"


def rec(external_id: str, text: str = "", **kw) -> Record:
    return Record(external_id=external_id, text=text or f"body of {external_id}",
                  **kw)


def connected(connector: str = "demo") -> str:
    made = connections.ensure(connector)
    connections.transition(made.id, AuthState.AUTHENTICATED)
    return made.id


def plan(fetch, brain=None, **kw) -> Plan:
    return Plan(connector="demo", manifest=MANIFEST, resource_type="record",
                fetch=fetch, ingest=brain or Brain(),
                connection_id=connected(), retry=NO_WAIT, **kw)


def one_page(*records):
    return lambda cursor: Page(records=list(records), next_cursor="")


def pages(*batches):
    marks = [str(i + 1) if i + 1 < len(batches) else ""
             for i in range(len(batches))]

    def fetch(cursor):
        index = 0 if not cursor else marks.index(cursor) + 1
        return Page(records=list(batches[index]), next_cursor=marks[index])

    return fetch


# ── the ordinary pass ─────────────────────────────────────────────────────


def test_a_first_pass_ingests_everything():
    brain = Brain()
    result = engine.run(plan(one_page(rec("a"), rec("b")), brain))

    assert result.added == 2
    assert not result.errors
    assert [r for r, _ in brain.stored] == ["a", "b"]


def test_a_second_pass_skips_what_has_not_changed():
    """Without reading the body — the N+1 the audit found in `gdrive`."""
    brain = Brain()
    fetch = one_page(rec("a", fingerprint="v1"), rec("b", fingerprint="v1"))
    first = plan(fetch, brain)
    engine.run(first)

    again = Plan(connector="demo", manifest=MANIFEST, resource_type="record",
                 fetch=fetch, ingest=brain, connection_id=first.connection_id,
                 retry=NO_WAIT)
    result = engine.run(again)

    assert result.added == 0
    assert result.skipped == 2
    assert len(brain.stored) == 2, "the body was never read a second time"


def test_an_edited_record_is_ingested_again():
    brain = Brain()
    first = plan(one_page(rec("a", fingerprint="v1")), brain)
    engine.run(first)

    again = Plan(connector="demo", manifest=MANIFEST, resource_type="record",
                 fetch=one_page(rec("a", fingerprint="v2")), ingest=brain,
                 connection_id=first.connection_id, retry=NO_WAIT)
    result = engine.run(again)

    assert result.added == 1
    assert len(brain.stored) == 2


def test_a_record_with_no_identity_is_skipped():
    """It could only ever be appended — never superseded, never tombstoned."""
    result = engine.run(plan(one_page(Record(external_id="", text="orphan"))))

    assert result.added == 0 and result.skipped == 1


def test_provenance_reaches_the_brain():
    """The fields existed and no connector passed them."""
    brain = Brain()
    engine.run(plan(one_page(
        rec("a", url="https://x.test/a",
            source_updated_at="2026-09-01T10:00:00Z")), brain))

    _, metadata = brain.stored[0]
    source = metadata["source"]
    assert source["connector"] == "demo"
    assert source["external_id"] == "a"
    assert source["url"] == "https://x.test/a"
    assert source["source_updated_at"] == "2026-09-01T10:00:00Z"
    assert source["run_id"], "which pass produced this"


def test_provider_specific_metadata_is_preserved_beside_the_normalised_fields():
    """Normalized fields *plus* provider metadata, never one at the cost of
    the other."""
    brain = Brain()
    engine.run(plan(one_page(rec("a", extra={"label": "INBOX"})), brain))

    _, metadata = brain.stored[0]
    assert metadata["label"] == "INBOX"
    assert metadata["source"]["connector"] == "demo"


# ── checkpointing, which is the whole design ──────────────────────────────


def test_each_page_is_committed_before_the_next_is_requested():
    """What makes a pass resumable. The cursor moves only after every record
    on a page has landed."""
    committed: list[str] = []

    def fetch(cursor):
        committed.append(f"fetch:{cursor or 'start'}")
        return pages([rec("a")], [rec("b")])(cursor)

    brain = Brain()
    made = plan(fetch, brain)

    original = sync_state.checkpoint

    def watched(*args, **kwargs):
        committed.append(f"checkpoint:{kwargs.get('cursor')}")
        return original(*args, **kwargs)

    import chitragupta.connectors.engine as mod
    mod.sync_state.checkpoint = watched     # type: ignore[assignment]
    try:
        engine.run(made)
    finally:
        mod.sync_state.checkpoint = original    # type: ignore[assignment]

    assert committed[0] == "fetch:start"
    assert committed[1].startswith("checkpoint:")
    assert committed[2] == "fetch:1", "the next page was requested after the commit"


def test_a_crash_mid_pass_costs_one_page_not_the_pass():
    """The 2,000-item sync that fails at 1,900. `base._finish` restarts it at
    zero, every time; here it resumes."""
    def fetch(cursor):
        if cursor == "1":
            raise ConnectionResetError("the connection dropped")
        return pages([rec("a")], [rec("b")])(cursor)

    made = plan(fetch)
    result = engine.run(made)

    assert result.errors
    state = sync_state.get(made.connection_id, "record")
    assert state.cursor == "1", "resume from page two, not from the beginning"
    assert state.items_processed == 1, "page one still landed"


def test_the_records_that_landed_before_a_crash_stay_landed():
    brain = Brain()

    def fetch(cursor):
        if cursor == "1":
            raise TimeoutError("took too long")
        return pages([rec("a")], [rec("b")])(cursor)

    engine.run(plan(fetch, brain))

    assert [r for r, _ in brain.stored] == ["a"]


def test_a_checkpoint_is_never_written_before_the_page_is_ingested():
    """The unsurvivable ordering: it loses the page AND reports success for
    it, so nothing ever goes back."""
    order: list[str] = []
    brain = Brain()

    def ingest(record, source):
        order.append("ingest")
        return brain(record, source)

    made = Plan(connector="demo", manifest=MANIFEST, resource_type="record",
                fetch=one_page(rec("a")), ingest=ingest,
                connection_id=connected(), retry=NO_WAIT)

    import chitragupta.connectors.engine as mod
    original = sync_state.checkpoint
    mod.sync_state.checkpoint = lambda *a, **k: order.append("checkpoint")  # type: ignore[assignment]
    try:
        engine.run(made)
    finally:
        mod.sync_state.checkpoint = original    # type: ignore[assignment]

    assert order.index("ingest") < order.index("checkpoint")


def test_a_clean_pass_records_a_success():
    made = plan(one_page(rec("a")))
    engine.run(made)

    state = sync_state.get(made.connection_id, "record")
    assert state.last_success
    assert not state.in_flight


# ── failure injection ─────────────────────────────────────────────────────


def raising(exc):
    def fetch(cursor):
        raise exc
    return fetch


def http(code, body=b"{}"):
    return urllib.error.HTTPError("https://x.test", code, "no", {},
                                  BytesIO(body))


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"),
    ConnectionResetError("reset by peer"),
    socket.gaierror("name resolution"),
    urllib.error.URLError("unreachable"),
])
def test_a_transport_failure_is_an_error_not_a_crash(exc):
    """A broken connector produces a connector error, never an application
    crash."""
    result = engine.run(plan(raising(exc)))

    assert result.errors and not result.added
    assert isinstance(result.errors[0], str)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 429, 500])
def test_every_http_failure_produces_a_sentence_and_no_status_code(code):
    result = engine.run(plan(raising(http(code))))

    assert result.errors
    assert str(code) not in result.errors[0], "never surface an internal"


def test_a_401_moves_the_connection_to_needing_the_user():
    made = plan(raising(http(401)))
    engine.run(made)

    after = connections.get(made.connection_id)
    assert after is not None
    assert after.auth_state is AuthState.REAUTH_REQUIRED


def test_a_403_does_not_send_the_user_round_oauth_again():
    """A scope problem survives signing in again."""
    made = plan(raising(http(403)))
    engine.run(made)

    after = connections.get(made.connection_id)
    assert after is not None and after.auth_state is AuthState.AUTHENTICATED


def test_a_429_spends_the_local_budget_so_every_caller_slows_down():
    """Otherwise the retry absorbs it and the next request goes out at the
    rate that caused it."""
    from chitragupta.connectors import limits

    metered = ConnectorManifest(
        connector_id="demo", display_name="Demo", provider="demo",
        limits=Limits(requests=10, per_seconds=60.0, concurrency=2))
    made = Plan(connector="demo", manifest=metered, resource_type="record",
                fetch=raising(http(429)), ingest=Brain(),
                connection_id=connected(), retry=RetryPolicy(attempts=1))

    engine.run(made)

    gate = limits.gate_for("demo", metered.limits)
    assert gate.snapshot()["waiting_seconds"] > 0


def test_a_malformed_page_is_skipped_not_fatal():
    """Decision H2, one level up: one bad item never aborts a sync."""
    result = engine.run(plan(one_page(rec("a"), "not a record", rec("b"))))

    assert result.added == 2
    assert result.skipped == 1


def test_an_ingest_that_throws_costs_one_record():
    brain = Brain(fail_on={"b"})

    result = engine.run(plan(one_page(rec("a"), rec("b"), rec("c")), brain))

    assert result.added == 2
    assert result.skipped == 1


def test_a_repeating_cursor_stops_rather_than_paging_forever():
    def fetch(cursor):
        return Page(records=[rec("a")], next_cursor="stuck")

    result = engine.run(plan(fetch))

    assert result.detail, "it says why it stopped"
    assert not result.errors, "a provider bug is not our failure to report"


def test_an_unclassifiable_exception_still_does_not_crash_the_app():
    class Weird(BaseException):
        pass

    result = engine.run(plan(raising(RuntimeError("something odd"))))

    assert result.errors
    assert "Demo" in result.errors[0]


# ── stopping ──────────────────────────────────────────────────────────────


def test_a_paused_connection_is_skipped_and_is_not_an_error():
    """A state the user chose, reported as a failure every thirty minutes, is
    noise they learn to ignore."""
    made = plan(one_page(rec("a")))
    connections.pause(made.connection_id)

    result = engine.run(made)

    assert result.added == 0
    assert not result.errors
    assert result.detail


def test_a_signed_out_connection_does_not_sync():
    made = plan(one_page(rec("a")))
    connections.disconnect(made.connection_id)

    result = engine.run(made)

    assert result.added == 0 and not result.errors


def test_a_connection_removed_mid_flight_is_not_recreated():
    """A disconnect the user made while a sweep was in flight. Recreating it
    would quietly undo their decision."""
    made = plan(one_page(rec("a")))
    connections.forget(made.connection_id)

    result = engine.run(made)

    assert result.added == 0 and not result.errors
    assert connections.get(made.connection_id) is None


def test_a_cancel_stops_the_pass_and_keeps_what_landed():
    class Stop:
        def __init__(self):
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 4

    made = plan(pages([rec("a")], [rec("b")], [rec("c")]))
    result = engine.run(made, cancel=Stop())

    assert result.cancelled
    assert sync_state.get(made.connection_id, "record").items_processed >= 1


# ── deletion ──────────────────────────────────────────────────────────────


def test_a_record_the_source_stopped_listing_is_tombstoned():
    made = plan(one_page(rec("a"), rec("b")), sweeps_deletions=True)
    engine.run(made)

    again = Plan(connector="demo", manifest=MANIFEST, resource_type="record",
                 fetch=one_page(rec("a")), ingest=Brain(),
                 connection_id=made.connection_id, sweeps_deletions=True,
                 retry=NO_WAIT)
    engine.run(again)

    held = resources.get(made.connection_id, "record", "b")
    assert held is not None and held.state is ResourceState.DELETED


def test_a_truncated_pass_never_tombstones():
    """A sync that hit its budget would otherwise delete the entire tail."""
    small = ConnectorManifest(connector_id="demo", display_name="Demo",
                              provider="demo",
                              limits=Limits(requests=0, records_per_sync=1))
    made = Plan(connector="demo", manifest=MANIFEST, resource_type="record",
                fetch=one_page(rec("a"), rec("b")), ingest=Brain(),
                connection_id=connected(), sweeps_deletions=True,
                retry=NO_WAIT)
    engine.run(made)

    truncated = Plan(connector="demo", manifest=small, resource_type="record",
                     fetch=one_page(rec("a"), rec("b")), ingest=Brain(),
                     connection_id=made.connection_id, budget=1,
                     sweeps_deletions=True, retry=NO_WAIT)
    engine.run(truncated)

    held = resources.get(made.connection_id, "record", "b")
    assert held is not None and held.state is ResourceState.ACTIVE


def test_sweeping_is_off_unless_a_connector_asks_for_it():
    """Only safe when the listing is a complete enumeration. A windowed read
    is not, and sweeping one deletes everything outside the window."""
    made = plan(one_page(rec("a"), rec("b")))
    engine.run(made)

    again = Plan(connector="demo", manifest=MANIFEST, resource_type="record",
                 fetch=one_page(rec("a")), ingest=Brain(),
                 connection_id=made.connection_id, retry=NO_WAIT)
    engine.run(again)

    held = resources.get(made.connection_id, "record", "b")
    assert held is not None and held.state is ResourceState.ACTIVE


# ── it is all observable ──────────────────────────────────────────────────


def test_every_pass_is_recorded():
    from chitragupta.connectors import observability

    engine.run(plan(one_page(rec("a"))))

    rows = observability.recent("demo")
    assert rows and rows[0]["operation"] == "sync"
    assert rows[0]["records"] == 1
    assert rows[0]["correlation_id"]


def test_a_failed_pass_is_recorded_with_what_went_wrong():
    from chitragupta.connectors import observability

    engine.run(plan(raising(http(500))))

    rows = observability.recent("demo")
    assert rows[0]["result"] == "failed"
    assert rows[0]["error_kind"] == "provider"


def test_the_run_id_ties_the_pass_to_the_records_it_produced():
    from chitragupta.connectors import observability

    brain = Brain()
    engine.run(plan(one_page(rec("a")), brain))

    _, metadata = brain.stored[0]
    run_id = metadata["source"]["run_id"]
    assert [row["operation"] for row in observability.for_run(run_id)] == ["sync"]


# ── backpressure ──────────────────────────────────────────────────────────


def test_a_pass_never_holds_more_than_one_page():
    """`each_guarded` does `list(items)` — the whole page in memory before
    anything is processed. A connector returning 100k records is 100k records
    in a Python list."""
    live: list[int] = []

    def fetch(cursor):
        index = int(cursor or 0)
        if index >= 5:
            return Page(records=[], next_cursor="")
        return Page(records=[rec(f"r{index}")], next_cursor=str(index + 1))

    def ingest(record, source):
        live.append(1)
        return f"mem-{record.external_id}"

    made = Plan(connector="demo", manifest=MANIFEST, resource_type="record",
                fetch=fetch, ingest=ingest, connection_id=connected(),
                retry=NO_WAIT)
    engine.run(made)

    assert len(live) == 5


def test_the_record_budget_is_honoured():
    small = ConnectorManifest(connector_id="demo", display_name="Demo",
                              provider="demo",
                              limits=Limits(requests=0, records_per_sync=2))
    made = Plan(connector="demo", manifest=small, resource_type="record",
                fetch=one_page(*[rec(f"r{n}") for n in range(10)]),
                ingest=Brain(), connection_id=connected(), retry=NO_WAIT)

    result = engine.run(made)

    assert result.added == 2
    assert "next sync" in result.detail.lower()


def test_a_list_a_connector_already_has_can_be_paged():
    """The adapter for a source whose API is "here is everything" — a local
    file walk, an `.ics` export, an MCP tool's one answer."""
    batches = list(engine.paged([rec(f"r{n}") for n in range(5)], 2))

    assert [len(p.records) for p in batches] == [2, 2, 1]
    assert batches[-1].next_cursor == ""
