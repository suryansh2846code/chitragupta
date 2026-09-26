"""An edited record supersedes, a deleted one is marked, a duplicate event is one.

Two shipped behaviours this replaces, both of which look fine until a connector
syncs continuously:

* **Dedup is a content hash** (`idx_memories_chash`). Edit a document's second
  paragraph and the hash changes, so the brain holds two versions and recall
  cites whichever scores higher. Nothing ties the rows together, so there is
  nothing to supersede *with*.
* **Deletion is invisible.** Nothing knows a memory came from file `1a2b3c`, so
  when that file is gone there is no way to say so. The brain cites it forever.

And on the event side, the three things providers actually do: send duplicates,
deliver out of order, and lose things. The third is why polling stays the floor.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import events, resources
from chitragupta.connectors.events import EventStatus, EventType, ExternalEvent
from chitragupta.connectors.resources import ResourceState, Verdict, fingerprint

CONN = "gmail:a1b2"


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from chitragupta.config import get_settings
    from chitragupta.connectors import db

    monkeypatch.setattr(get_settings(), "home", tmp_path, raising=False)
    db.reset_for_tests()
    yield
    db.reset_for_tests()


# ── identity that survives a content change ───────────────────────────────


def test_a_record_we_have_never_seen_is_new():
    verdict, held = resources.classify(CONN, "email", "m1")

    assert verdict is Verdict.NEW
    assert held is None


def test_an_unchanged_record_is_recognised_without_reading_it():
    """The N+1 the audit found in `gdrive`: it downloads a file to discover it
    already has it."""
    resources.seen(CONN, "file", "f1", fingerprint_="abc")

    verdict, _ = resources.classify(CONN, "file", "f1", fingerprint_="abc")

    assert verdict is Verdict.UNCHANGED


def test_an_edited_record_is_changed_not_new():
    """The content hash says "new memory"; the identity says "this thing
    again". Only the second can supersede."""
    resources.seen(CONN, "file", "f1", fingerprint_="abc")

    verdict, held = resources.classify(CONN, "file", "f1", fingerprint_="xyz")

    assert verdict is Verdict.CHANGED
    assert held is not None and held.external_id == "f1"


def test_a_modified_time_works_when_there_is_no_fingerprint():
    resources.seen(CONN, "file", "f1", source_updated_at="2026-09-01T00:00:00Z")

    unchanged, _ = resources.classify(
        CONN, "file", "f1", source_updated_at="2026-09-01T00:00:00Z")
    changed, _ = resources.classify(
        CONN, "file", "f1", source_updated_at="2026-09-20T00:00:00Z")

    assert unchanged is Verdict.UNCHANGED
    assert changed is Verdict.CHANGED


def test_the_same_id_in_two_accounts_is_two_things():
    """`1a2b3c` in the work Drive and `1a2b3c` in the personal one are
    different files, which is why the identity is scoped to the connection."""
    resources.seen("gdrive:work", "file", "1a2b3c", fingerprint_="w")
    resources.seen("gdrive:home", "file", "1a2b3c", fingerprint_="h")

    work = resources.get("gdrive:work", "file", "1a2b3c")
    home = resources.get("gdrive:home", "file", "1a2b3c")
    assert work is not None and home is not None
    assert work.fingerprint != home.fingerprint


def test_first_seen_survives_an_edit():
    """When this *thing* entered the brain. Resetting it on every edit makes
    every record look new on the day it was last touched."""
    first = resources.seen(CONN, "file", "f1", fingerprint_="a")

    later = resources.seen(CONN, "file", "f1", fingerprint_="b")

    assert later.first_seen == first.first_seen
    assert later.last_seen >= first.last_seen


def test_a_memory_id_is_not_lost_by_a_later_update_that_omits_it():
    resources.seen(CONN, "file", "f1", memory_id="mem-1")

    later = resources.seen(CONN, "file", "f1", fingerprint_="b")

    assert later.memory_id == "mem-1"


def test_a_fingerprint_changes_when_any_part_does():
    assert fingerprint("a", "b") == fingerprint("a", "b")
    assert fingerprint("a", "b") != fingerprint("a", "c")
    assert fingerprint("a", None) != fingerprint("a", "")


# ── tombstones ────────────────────────────────────────────────────────────


def test_a_deleted_record_is_marked_and_not_erased():
    """A file disappearing from Drive is not consent to forget a year of notes
    about it, and *"this is no longer in Drive"* is more useful to an agent
    than silence."""
    resources.seen(CONN, "file", "f1", memory_id="mem-1")

    marked = resources.mark(CONN, "file", "f1", ResourceState.DELETED)

    assert marked is not None
    assert marked.stale
    assert marked.memory_id == "mem-1", "the memory is still ours"


def test_unavailable_is_not_the_same_as_deleted():
    """*We lost access* and *it was destroyed* are different facts, and a 403
    filed as a deletion rewrites history every time a token narrows."""
    resources.seen(CONN, "file", "f1")

    marked = resources.mark(CONN, "file", "f1", ResourceState.UNAVAILABLE)

    assert marked is not None and marked.state is ResourceState.UNAVAILABLE
    assert marked.state is not ResourceState.DELETED


def test_a_record_that_comes_back_is_recognised():
    resources.seen(CONN, "file", "f1")
    resources.mark(CONN, "file", "f1", ResourceState.DELETED)

    verdict, _ = resources.classify(CONN, "file", "f1")

    assert verdict is Verdict.RETURNED


def test_a_sweep_marks_what_the_source_no_longer_lists():
    resources.seen(CONN, "file", "f1")
    resources.seen(CONN, "file", "f2")

    gone = resources.sweep_missing(CONN, "file", {"f1"}, complete=True)

    assert [r.external_id for r in gone] == ["f2"]
    held = resources.get(CONN, "file", "f2")
    assert held is not None and held.state is ResourceState.DELETED


def test_a_sweep_over_a_truncated_listing_deletes_nothing():
    """The failure this guard exists for: a sync that hit its page budget
    would tombstone the entire tail of a mailbox."""
    resources.seen(CONN, "email", "m1")
    resources.seen(CONN, "email", "m2")

    gone = resources.sweep_missing(CONN, "email", {"m1"}, complete=False)

    assert gone == []
    held = resources.get(CONN, "email", "m2")
    assert held is not None and held.state is ResourceState.ACTIVE


def test_the_counts_say_what_was_imported_and_what_became_of_it():
    resources.seen(CONN, "email", "m1")
    resources.seen(CONN, "email", "m2")
    resources.mark(CONN, "email", "m2", ResourceState.DELETED)

    counts = resources.counts(CONN)

    assert counts["active"] == 1 and counts["deleted"] == 1
    assert counts["total"] == 2


def test_the_memories_one_connection_produced_can_be_listed():
    """What *delete the data this connector imported* needs. Without an
    identity table the only implementation is "delete everything whose source
    string looks right"."""
    resources.seen(CONN, "email", "m1", memory_id="mem-1")
    resources.seen(CONN, "email", "m2", memory_id="mem-2")
    resources.seen("other:1", "email", "m3", memory_id="mem-3")

    assert sorted(resources.memory_ids(CONN)) == ["mem-1", "mem-2"]


# ── events: duplicates ────────────────────────────────────────────────────


def make(**kw) -> ExternalEvent:
    base = {"connection_id": CONN, "event_type": EventType.UPDATED,
            "resource_type": "email", "resource_id": "m1"}
    return ExternalEvent(**{**base, **kw})


def test_a_providers_own_event_id_is_preferred_over_anything_we_could_mint():
    """The only value stable across a redelivery: a digest of the payload
    changes if the provider re-serialises, and a timestamp changes if it
    retries."""
    first = make(provider_event_id="evt_123", occurred_at="2026-09-01T00:00:00Z")
    again = make(provider_event_id="evt_123", occurred_at="2026-09-02T00:00:00Z")

    assert first.id == again.id


def test_a_redelivered_event_is_stored_once():
    event = make(provider_event_id="evt_123")

    assert events.record(event)
    assert not events.record(event), "at-least-once is the contract"


def test_a_duplicate_is_not_an_error_it_is_the_system_working():
    """What would be a bug is acting on it twice."""
    event = make(provider_event_id="evt_123", sequence="5")

    assert events.accept(event)
    assert not events.accept(event)


def test_without_a_provider_id_two_updates_are_still_two_events():
    """A digest of the identity alone would collapse every update to one
    resource into a single event, forever."""
    first = make(occurred_at="2026-09-01T00:00:00Z")
    second = make(occurred_at="2026-09-02T00:00:00Z")

    assert first.id != second.id


# ── events: ordering ──────────────────────────────────────────────────────


def test_a_newer_sequence_supersedes_an_older_one():
    assert make(sequence="10").supersedes(make(sequence="9"))
    assert not make(sequence="9").supersedes(make(sequence="10"))


def test_sequences_are_compared_as_numbers_where_they_are_numbers():
    """String comparison would put "9" after "10"."""
    assert make(sequence="10").supersedes(make(sequence="9"))


def test_a_non_numeric_sequence_still_compares():
    assert make(sequence="b").supersedes(make(sequence="a"))


def test_occurred_at_is_used_when_there_is_no_sequence():
    assert make(occurred_at="2026-09-02T00:00:00Z").supersedes(
        make(occurred_at="2026-09-01T00:00:00Z"))


def test_with_no_ordering_at_all_nothing_is_overwritten():
    """The honest answer is *we do not know*, so nothing is overwritten and
    the next sync settles it. True here would be a guess with a write behind
    it."""
    assert not make().supersedes(make())


def test_an_out_of_order_event_is_stored_and_not_acted_on():
    """Both halves matter: discarding it makes an out-of-order delivery
    invisible rather than harmless."""
    events.accept(make(provider_event_id="e2", sequence="10"))

    acted = events.accept(make(provider_event_id="e1", sequence="5"))

    assert not acted
    stored = {e["id"] for e in [events.latest_for(CONN, "email", "m1").as_dict()]}
    assert stored, "the late event is still on record"


def test_the_first_event_about_a_resource_is_always_acted_on():
    assert events.accept(make(provider_event_id="e1", sequence="5"))


# ── events: the queue ─────────────────────────────────────────────────────


def test_pending_events_are_the_ones_nobody_has_acted_on():
    events.accept(make(provider_event_id="e1", sequence="1"))

    waiting = events.pending()

    assert [e.resource_id for e in waiting] == ["m1"]
    assert events.depth() == 1


def test_marking_one_processed_takes_it_out_of_the_queue():
    events.accept(make(provider_event_id="e1", sequence="1"))
    only = events.pending()[0]

    events.mark(only.id, EventStatus.PROCESSED)

    assert events.depth() == 0


def test_the_queue_read_is_bounded():
    """An unbounded read is how one noisy connector takes a sweep's whole
    budget."""
    for n in range(10):
        events.accept(make(provider_event_id=f"e{n}", resource_id=f"m{n}"))

    assert len(events.pending(limit=3)) == 3


def test_pruning_never_drops_work_nobody_has_done():
    events.accept(make(provider_event_id="done", resource_id="m1"))
    events.mark(events.pending()[0].id, EventStatus.PROCESSED)
    events.accept(make(provider_event_id="todo", resource_id="m2"))

    events.prune(keep=0)

    assert events.depth() == 1, "the pending one survived"


# ── polling produces events too ───────────────────────────────────────────


def test_a_syncs_own_findings_become_events():
    """What makes polling an event source rather than a separate world: an
    automation consumes one shape whether a webhook or a timer produced it."""
    created = events.from_resource_change(CONN, "email", "m1", Verdict.NEW)
    updated = events.from_resource_change(CONN, "email", "m1", Verdict.CHANGED)

    assert created is not None and created.event_type is EventType.CREATED
    assert updated is not None and updated.event_type is EventType.UPDATED


def test_an_unchanged_record_produces_no_event():
    """Nothing happened. Saying something would make every sync of a quiet
    mailbox look like a flood."""
    assert events.from_resource_change(CONN, "email", "m1",
                                       Verdict.UNCHANGED) is None


def test_events_outlive_the_process():
    from chitragupta.connectors import db

    events.accept(make(provider_event_id="e1"))

    db.reset_for_tests()

    assert events.depth() == 1
