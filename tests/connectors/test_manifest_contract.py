"""Every connector describes itself, and the description is derived.

`test_contract.py` freezes the *interface* — a label, a platform gate, a
`sync()` that fails softly. This freezes the **declaration**: what the
connector can do, how it signs in, how a second pass avoids being the first
one, and how hard we will push the vendor.

Why it has to be a test rather than a convention: the four booleans that
preceded it (`auto_sync`, `incremental`, `runs_on_device`, `platforms`) were
all conventions, and each one was silently wrong on at least one connector
until something asserted it. `auto_sync` was a hand-maintained list of five in
the scheduler and six connectors were excluded by nothing but absence;
`runs_on_device` was a map in the frontend with four connectors missing.

A derived manifest cannot drift from the class. What it *can* do is be empty,
and an empty capability set is indistinguishable from a read-only connector
unless something asks — which is what `declares_nothing` and the first test
here are for.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import REGISTRY
from chitragupta.connectors.capability import Access, Capability
from chitragupta.connectors.contract import (
    AuthMethod,
    ConnectorManifest,
    EventSource,
    Limits,
    PaginationStrategy,
    SyncStrategy,
    manifest_of,
)
from chitragupta.connectors.custom_api import CustomAPIConnector
from chitragupta.connectors.mcp_source import MCPConnector, MCPServerSpec

CLASSES = sorted(REGISTRY.items())
IDS = [name for name, _ in CLASSES]


# ── every connector declares itself ───────────────────────────────────────


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_a_connector_says_what_it_can_do(name, cls):
    """An empty capability set reads as "read-only" and means "never asked"."""
    manifest = cls().manifest()

    assert not manifest.declares_nothing, (
        f"{name} declares no capabilities — a reader cannot tell that from a "
        f"read-only connector, which is why `declares_nothing` exists")
    assert all(isinstance(c, Capability) for c in manifest.capabilities)


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_the_manifest_is_derived_from_the_class(name, cls):
    """Never written beside it — the failure mode of every hand-maintained
    list this repo has had."""
    manifest = cls().manifest()

    assert manifest.connector_id == cls.name
    assert manifest.display_name == cls.label
    assert manifest.sync.incremental is bool(cls.incremental)
    assert manifest.sync.scheduled is bool(cls.auto_sync)
    assert manifest.on_device is bool(cls.runs_on_device)
    assert manifest.superseded_by == cls.prefer_mcp


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_the_resources_come_from_the_capabilities(name, cls):
    """So a connector cannot claim a resource it has no verb on."""
    manifest = cls().manifest()

    assert manifest.resources == frozenset(
        c.resource for c in manifest.capabilities)


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_every_manifest_is_json_shaped(name, cls):
    """It crosses to the frontend, so every value has to be a plain one."""
    import json

    payload = cls().manifest().as_dict()

    assert json.loads(json.dumps(payload)) == payload


# ── the declarations have to be honest ────────────────────────────────────


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_an_authentication_method_is_one_we_recognise(name, cls):
    assert isinstance(cls().manifest().auth, AuthMethod)


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_an_on_device_connector_signs_in_to_nothing(name, cls):
    """The product promise, as an assertion. A connector that reads something
    already on this Mac cannot also be holding an account credential — if it
    is, one of the two declarations is a lie and the Connectors screen groups
    it under the wrong heading."""
    manifest = cls().manifest()
    if manifest.on_device:
        assert manifest.auth in (AuthMethod.LOCAL, AuthMethod.NONE), (
            f"{name} says it runs on device and signs in with "
            f"{manifest.auth.value}")
        assert not manifest.required_scopes


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_a_connector_that_keeps_no_watermark_reports_no_overlap(name, cls):
    """120 minutes of deliberate overlap on a connector that re-reads its
    whole window anyway is a number describing nothing."""
    manifest = cls().manifest()
    if not manifest.sync.incremental:
        assert manifest.sync.overlap_minutes == 0


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_a_watermarked_connector_overlaps_deliberately(name, cls):
    """Re-fetching is free — dedup is a content hash with a unique index — and
    missing an email that landed mid-sync is not."""
    manifest = cls().manifest()
    if manifest.sync.incremental:
        assert manifest.sync.overlap_minutes > 0


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_the_limits_are_ours_and_say_so(name, cls):
    """MCP publishes no rate limits and most REST vendors publish theirs only
    in prose. A ceiling this app chose that is read as the vendor's is a
    manifest that has started lying — `mcp_manifest` learned this first."""
    limits = cls().manifest().limits.as_dict()

    assert limits["imposed_by"] == "Chitragupta"
    assert limits["concurrency"] >= 1
    assert limits["records_per_sync"] > 0


@pytest.mark.parametrize("name,cls", CLASSES, ids=IDS)
def test_nothing_claims_a_webhook_nobody_receives(name, cls):
    """`EventSource.WEBHOOK` is representable and unused: a local-first Mac app
    has no public URL. Declaring one would put "event-driven" on a row that
    polls. `docs/CONNECTOR-PLATFORM.md` §7."""
    assert cls().manifest().events.source is not EventSource.WEBHOOK


# ── the two connectors that are one class per configuration ───────────────


def test_a_custom_app_describes_the_app_not_the_class():
    """`CustomAPIConnector` is constructed per user definition, so its id and
    label live on the instance. A classmethod manifest would have reported the
    class for every one of them."""
    app = {"id": "acme", "name": "Acme Tickets", "base_url": "https://acme.test"}

    manifest = CustomAPIConnector(app, store=object()).manifest()

    assert manifest.connector_id == "custom:acme"
    assert manifest.display_name == "Acme Tickets"


def test_a_custom_app_can_only_ever_read():
    """A connector a user writes in a form must not be a way to describe an
    arbitrary side effect. There is no write path, so there is no write
    capability and the floor has nothing to let through."""
    app = {"id": "acme", "name": "Acme", "base_url": "https://acme.test"}

    manifest = CustomAPIConnector(app, store=object()).manifest()

    assert manifest.access is Access.READ
    assert manifest.writes == frozenset()


def test_an_mcp_connector_reports_the_servers_own_authentication():
    """Linear runs a full OAuth server; GitHub's takes a pasted token; a local
    stdio server signs in to nothing. One class, three answers."""
    def auth_of(**kw):
        spec = MCPServerSpec(id="s", name="S", **kw)
        return MCPConnector(spec, store=object()).manifest().auth

    assert auth_of(transport="http", url="https://x.test", auth="oauth") \
        is AuthMethod.OAUTH2
    assert auth_of(transport="http", url="https://x.test", auth="token") \
        is AuthMethod.API_KEY
    assert auth_of(transport="stdio", command="x", auth="none") \
        is AuthMethod.LOCAL


def test_an_mcp_manifest_is_a_ceiling_and_says_record():
    """The per-server truth is `mcp_manifest.manifest_of`, from the probe the
    caller already paid for. Restating it here would mean starting a server to
    answer a question about a class — and `Resource.RECORD` is the honest name
    for a verb whose resource we genuinely do not know."""
    spec = MCPServerSpec(id="s", name="S", transport="stdio", command="x")

    manifest = MCPConnector(spec, store=object()).manifest()

    assert manifest.access is Access.DESTRUCTIVE
    assert {c.resource.value for c in manifest.capabilities} == {"record"}


# ── the derivation itself ─────────────────────────────────────────────────


def test_a_class_that_declares_nothing_gets_a_manifest_that_says_so():
    """Not an exception, and not a manifest that quietly claims it does
    nothing — a fact a contract test can then name out loud."""
    class Bare:
        name = "bare"
        label = "Bare"

    manifest = manifest_of(Bare)

    assert manifest.declares_nothing
    assert manifest.access is Access.READ
    assert manifest.auth is AuthMethod.NONE


def test_permits_fails_closed():
    from chitragupta.connectors.capability import parse

    manifest = ConnectorManifest(
        connector_id="x", display_name="X", provider="x",
        capabilities=frozenset({parse("read:email")}))

    assert manifest.permits(parse("read:email"))
    assert not manifest.permits(parse("send:email"))


def test_defaults_are_the_conservative_ones():
    """A connector that says nothing must not be described as scheduled,
    incremental, resumable or event-driven."""
    from chitragupta.connectors.contract import EventSupport, SyncSupport

    sync, events, limits = SyncSupport(), EventSupport(), Limits()

    assert sync.strategy is SyncStrategy.FULL
    assert not sync.incremental and not sync.resumable and not sync.scheduled
    assert sync.pagination is PaginationStrategy.NONE
    assert events.source is EventSource.NONE
    assert limits.requests == 0
