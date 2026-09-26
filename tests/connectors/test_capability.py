"""A capability says what can be done, and an unknown one is refused.

The vocabulary exists so the permission layer can reason about `send:email`
without knowing whether Gmail, Apple Mail or somebody else's MCP server is
behind it. Three properties make that worth having, and each is a test here:

* **A verb carries its tier.** `delete:event` is destructive wherever it
  appears; nothing has to remember to mark it.
* **Unknown fails closed.** Not "probably a read" — a connector that could name
  its own capabilities would otherwise be able to name a write in a way that
  classifies as harmless.
* **Outbound is not write.** `create:draft` reaches nobody and `send:email`
  reaches a person; the product already treats those differently and a
  vocabulary that collapsed them would make one of the two wrong.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors.capability import (
    Access,
    Capability,
    Resource,
    UnknownCapabilityError,
    Verb,
    at_least,
    caps,
    parse,
    parse_all,
    strongest,
    writes_in,
)

# ── the vocabulary ────────────────────────────────────────────────────────


def test_every_verb_has_a_tier():
    """A verb added without one would fall to whatever `.get()`'s default was,
    and the tempting default classifies a new destructive verb as harmless."""
    for verb in Verb:
        assert isinstance(Capability(verb, Resource.RECORD).access, Access)


def test_a_capability_spells_itself_the_way_it_is_written():
    assert str(Capability(Verb.SEND, Resource.EMAIL)) == "send:email"


def test_parse_round_trips():
    assert parse("send:email") == Capability(Verb.SEND, Resource.EMAIL)
    assert str(parse("merge:pull_request")) == "merge:pull_request"


# ── unknown fails closed ──────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    "", "email", "gmail", "send email", "send:", ":email",
    "exfiltrate:email",           # unknown verb
    "send:mailbox",               # unknown resource
    "SEND:EMAIL::",               # not the spelling
])
def test_an_unrecognised_capability_is_refused_not_downgraded(raw):
    with pytest.raises(UnknownCapabilityError):
        parse(raw)


def test_a_capability_set_is_all_or_nothing():
    """A partially-parsed set silently narrows what a connector declared, and
    a connector whose `send:email` was dropped by a typo reads as one that
    cannot send — a control that stops working with no error."""
    with pytest.raises(UnknownCapabilityError):
        parse_all(["read:email", "teleport:email"])


def test_one_string_is_not_a_capability_set():
    """`parse_all("read:email")` would otherwise iterate characters."""
    with pytest.raises(UnknownCapabilityError):
        parse_all("read:email")


# ── the four tiers ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,tier", [
    ("read:email", Access.READ),
    ("search:email", Access.READ),
    ("download:file", Access.READ),
    ("create:draft", Access.WRITE),
    ("update:event", Access.WRITE),
    ("archive:email", Access.WRITE),
    ("send:email", Access.OUTBOUND),
    ("share:document", Access.OUTBOUND),
    ("delete:event", Access.DESTRUCTIVE),
    ("cancel:event", Access.DESTRUCTIVE),
    ("merge:pull_request", Access.DESTRUCTIVE),
])
def test_the_verb_decides_the_tier(raw, tier):
    assert parse(raw).access is tier


def test_a_draft_reaches_nobody_and_a_send_reaches_someone():
    """The distinction the whole tier split exists for: an unattended agent may
    prepare a draft overnight and may not send one."""
    assert not parse("create:draft").reaches_someone
    assert parse("send:email").reaches_someone


def test_reading_is_never_mistaken_for_changing():
    assert parse("read:email").reads_only
    assert not parse("create:draft").reads_only


def test_destructive_is_its_own_tier_not_a_louder_write():
    """`permissions` refuses to allow-list these, so they cannot be filed among
    ordinary changes — the same rule `mcp_manifest` applies to `needs_care`."""
    assert parse("delete:file").is_destructive
    assert not parse("update:file").is_destructive


def test_tiers_are_ordered_so_a_floor_is_a_comparison():
    assert at_least(Access.DESTRUCTIVE, Access.WRITE)
    assert at_least(Access.WRITE, Access.WRITE)
    assert not at_least(Access.READ, Access.WRITE)
    assert not at_least(Access.WRITE, Access.OUTBOUND)


# ── sets ──────────────────────────────────────────────────────────────────


def test_the_strongest_tier_in_a_set_is_what_the_connector_can_do_at_worst():
    assert strongest(caps("read:email", "send:email")) is Access.OUTBOUND
    assert strongest(caps("read:email", "delete:email")) is Access.DESTRUCTIVE


def test_a_connector_that_declares_nothing_cannot_do_anything():
    """Empty is READ, not an error and not a write: "cannot do anything" is
    not stronger than reading."""
    assert strongest(frozenset()) is Access.READ


def test_the_writes_are_separable_from_the_whole_list():
    """What a user is actually consenting to when they connect a source."""
    surface = caps("read:email", "search:email", "create:draft", "send:email")

    assert writes_in(surface) == caps("create:draft", "send:email")
