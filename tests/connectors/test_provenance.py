"""Where a record came from, carried all the way into the brain.

Six connectors passed a `metadata` dict before this and each invented its own
keys — `message_id`, `file_id`, `page_id`, `event_id`, `mcp_tool` — while
`source_id`, `uri` and `event_time` sat in `core/db.py` unused by every one of
them. So the brain knew a memory came from `"gmail"` and could not answer which
account, which message, when it was fetched, when it last changed at the
source, or which sync produced it.

Each of those is a test here, because each is something the product needs and
could not do:

* **attribution** — *"where did you get that"* was unanswerable;
* **conflict resolution** — two sources could only be ranked by which we read
  last, not by which is fresher at its own source;
* **deletion** — a tombstone needs an `external_id` to be keyed to;
* **debugging** — *"which sync put this here"* was a guess.
"""
from __future__ import annotations

from chitragupta.connectors.provenance import (
    CONNECTED_SOURCE,
    SourceRef,
    describe,
    of,
)


def ref(**kw) -> SourceRef:
    base = {"connector": "gmail", "connection_id": "gmail:a1b2",
            "provider": "google", "account": "me@work.test",
            "resource_type": "email", "external_id": "18f2c",
            "url": "https://mail.google.test/18f2c",
            "source_updated_at": "2026-09-20T09:14:00+00:00",
            "run_id": "7c1f9e2a"}
    return SourceRef(**{**base, **kw})


# ── identity ──────────────────────────────────────────────────────────────


def test_the_identity_is_scoped_to_the_account_not_the_connector():
    """`1a2b3c` in the work Drive and `1a2b3c` in the personal one are
    different files."""
    work = ref(connection_id="gdrive:work", external_id="1a2b3c")
    home = ref(connection_id="gdrive:home", external_id="1a2b3c")

    assert work.identity != home.identity


def test_a_record_with_no_external_id_is_incomplete():
    """Without one there is no identity, and a record with no identity can
    only ever be appended to the brain — never superseded, never tombstoned."""
    assert ref().complete
    assert not ref(external_id="").complete
    assert not ref(connection_id="").complete


# ── what reaches the brain ────────────────────────────────────────────────


def test_the_schema_fields_that_existed_and_nobody_used_are_populated():
    kwargs = ref().ingest_kwargs()

    assert kwargs["source"] == "gmail"
    assert kwargs["source_id"] == "gmail:a1b2/email/18f2c"
    assert kwargs["uri"] == "https://mail.google.test/18f2c"
    assert kwargs["event_time"] == "2026-09-20T09:14:00+00:00"
    assert kwargs["event_date"] == "2026-09-20"


def test_the_date_of_a_memory_is_when_it_happened_not_when_we_read_it():
    """Filling `event_date` from the fetch time makes every record from a
    first sync look like it happened today."""
    kwargs = ref(source_updated_at="").ingest_kwargs()

    assert "event_date" not in kwargs
    assert "event_time" not in kwargs


def test_retrieved_at_and_source_updated_at_are_different_facts():
    """*We read it an hour ago* and *it changed three weeks ago* are both
    needed, and one cannot stand in for the other."""
    source = ref().as_metadata()["source"]

    assert source["retrieved_at"] != source["source_updated_at"]
    assert source["source_updated_at"] == "2026-09-20T09:14:00+00:00"


def test_provenance_is_namespaced_so_a_connector_key_cannot_overwrite_it():
    """A connector's own `extra` keys are kept — normalized fields *plus*
    provider metadata — and cannot collide with where something came from."""
    metadata = ref(extra={"connector": "not-really", "label": "INBOX"}).as_metadata()

    assert metadata["source"]["connector"] == "gmail"
    assert metadata["label"] == "INBOX"
    assert metadata["connector"] == "not-really", "their key is still theirs"


def test_an_empty_field_is_left_out_rather_than_stored_as_nothing():
    """A `url` of `""` in the metadata is a link that 404s on a screen."""
    source = ref(url="", account="").as_metadata()["source"]

    assert "url" not in source
    assert "account" not in source


def test_the_run_that_produced_a_record_travels_with_it():
    """Turns *"which sync put this here"* from a guess into a join."""
    assert ref().as_metadata()["source"]["run_id"] == "7c1f9e2a"


def test_trust_travels_with_the_text():
    """So the reader of a memory knows a stranger wrote the body of it."""
    assert ref().as_metadata()["source"]["trust"] == CONNECTED_SOURCE


# ── reading it back ───────────────────────────────────────────────────────


def test_provenance_survives_a_round_trip_through_metadata():
    """The whole point: it has to be readable off a stored memory, not only
    constructible on the way in."""
    stored = ref().as_metadata()

    assert of(stored)["external_id"] == "18f2c"
    assert of(stored)["account"] == "me@work.test"


def test_a_memory_written_before_this_existed_reads_as_no_provenance():
    """A reader that raised on those would break recall for everything
    ingested up to now."""
    assert of({"from": "someone@example.test"}) == {}
    assert of(None) == {}
    assert of("not a dict") == {}


def test_it_describes_itself_in_one_line_a_person_can_read():
    assert describe(ref().as_metadata()) == "gmail · me@work.test · 2026-09-20"


def test_there_is_nothing_to_say_about_a_memory_with_no_provenance():
    """An attribution line reading "unknown · unknown" is worse than none, so
    a caller can put this on a row without checking first."""
    assert describe({}) == ""
    assert describe(None) == ""


def test_a_description_falls_back_to_when_we_read_it():
    said = describe(ref(source_updated_at="").as_metadata())

    assert said.startswith("gmail · me@work.test · ")


# ── it cannot be edited after the fact ────────────────────────────────────


def test_provenance_is_frozen():
    """Provenance that can be changed afterwards is provenance nobody can
    rely on."""
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        ref().external_id = "something else"      # type: ignore[misc]
