"""A source is offered one way, not two.

Reported from a screenshot: the Connectors screen listed **Notion** twice —
once under the built-in connectors with a *Connect* button, once under custom
sources with a green **CONNECTED** badge. Linear the same. The user had
connected the MCP one; the agent proposed a write down the built-in path; the
card rendered, asked for approval, ran, and failed with *"Notion is not
connected."*

Nothing was broken. Both things were working exactly as written. There were
simply two of them, both called Notion, and no rule saying which wins — so the
agent picked, and had no way to pick correctly.

The rule now: **where the vendor ships an MCP server, the built-in connector
is not offered.** OAuth instead of a pasted integration secret, the vendor's
own schema instead of five hand-written methods, forty-five tools instead of
five. `Connector.prefer_mcp` names the server that supersedes it.

The exception is the one that keeps it honest: **a connector somebody has
already configured stays visible.** Hiding a thing the user set up, because we
changed our mind about which route is better, is losing their state to our
decision — which `/CLAUDE.md` forbids in as many words.
"""
from __future__ import annotations

import asyncio

import pytest

from chitragupta.connectors import REGISTRY

#: Retired in favour of the vendor's own server. Each had its write path
#: removed in the same change; none of them is on this Mac.
RETIRED = {"notion": "notion", "linear": "linear", "github": "github"}


def test_the_ones_with_vendor_servers_declare_it():
    """Each had its write path removed in favour of MCP. The declaration is
    what stops them being offered as a second way to do the same thing."""
    for name, server in RETIRED.items():
        assert REGISTRY[name].prefer_mcp == server


def test_nothing_else_claims_a_server_it_does_not_have():
    """A connector that stood down for a server nobody ships would simply
    disappear — the user would lose the source with no way to get it back."""
    for name, cls in REGISTRY.items():
        if name in RETIRED:
            continue
        assert not cls.prefer_mcp, f"{name} stands down for nothing"


def test_a_retired_connector_keeps_no_way_to_write():
    """Retiring a source is not a UI change. The built-in stays and keeps
    syncing for anybody who had it configured — and loses its write methods,
    because two ways to change somebody's account is the bug this whole file
    is named after, one layer below the screen it was reported on."""
    for name in RETIRED:
        cls = REGISTRY[name]
        for verb in ("comment", "create_issue", "delete_comment", "send",
                     "create_page", "update"):
            assert not hasattr(cls, verb), (
                f"{name} is retired but can still {verb}()")


def test_the_on_device_sources_can_never_stand_down():
    """There is no MCP server for the Messages database on this Mac. These
    are the connectors that exist *because* nothing else can reach them, and
    a rule that retired them would take the sources away entirely."""
    for name in ("imessage", "apple_mail", "notes", "apple_calendar",
                 "apple_health", "files"):
        assert not REGISTRY[name].prefer_mcp, (
            f"{name} is on-device — no server can replace it")


def _rows(route) -> list[dict]:
    """The screen's rows. The endpoint is async because it sits in the probe
    lane — starting every MCP server is what makes it slow."""
    payload = route.connectors()
    if asyncio.iscoroutine(payload):
        payload = asyncio.run(payload)
    return payload["connectors"]


def _names(route) -> set[str]:
    """The screen's payload. The endpoint is async because it sits in the
    probe lane — starting every MCP server is what makes it slow."""
    payload = route.connectors()
    if asyncio.iscoroutine(payload):
        payload = asyncio.run(payload)
    return {row["name"] for row in payload["connectors"]}


@pytest.fixture
def catalog(monkeypatch):
    """The Connectors screen's own payload, with the MCP servers controllable."""
    from types import SimpleNamespace

    from chitragupta.api.routes import connectors as route

    def install(server_ids):
        monkeypatch.setattr(
            route, "get_brain",
            lambda: SimpleNamespace(store=SimpleNamespace(
                all_connector_state=dict)))
        import chitragupta.connectors.mcp_source as mcp

        # Real specs, not a stand-in: the row builder reads `as_dict()` and
        # several fields off them, and a namespace that answers three of
        # those is a fake that tests the fake.
        monkeypatch.setattr(
            mcp, "list_servers",
            lambda: [mcp.MCPServerSpec(id=i, name=i.title())
                     for i in server_ids])
        # The MCP rows themselves are not what this file is about, and
        # building them for real would start every server.
        monkeypatch.setattr(mcp.MCPConnector, "status",
                            lambda self: (True, "", False))
        return route
    return install


def test_a_superseded_connector_is_not_offered(catalog):
    """The screenshot. With Notion added as a custom source, the built-in row
    must not be sitting above it offering to connect the same thing again."""
    route = catalog(["notion"])

    names = _names(route)

    assert "notion" not in names


def test_it_is_not_offered_even_before_the_server_is_added(catalog):
    """Not only deduplicated — *retired*. Offering it to somebody who has
    neither would be offering the worse of two routes to a new user, which is
    how they end up with the one that needs a pasted secret."""
    route = catalog([])

    names = _names(route)

    assert "notion" not in names
    assert "linear" not in names


def test_the_sources_that_have_no_server_are_untouched(catalog):
    """The retirement must be narrow. Everything on-device still appears."""
    route = catalog(["notion", "linear"])

    names = _names(route)

    for kept in ("gmail", "gcal", "files", "notes"):
        assert kept in names, f"{kept} disappeared"


def test_any_source_added_as_a_server_hides_its_unconfigured_built_in(
        catalog, monkeypatch):
    """The universal half of the rule, not just the ones that were retired.

    Add a server whose id matches ANY built-in and the built-in stops being
    offered — so "two of the same app on one screen" cannot happen again for
    a source nobody has thought about yet. `gdrive` is the subject because it
    is deliberately NOT retired: the rule has to hold for a connector that is
    staying, or it is only the retirement wearing a different name.

    Its state is pinned rather than read off this machine — the developer who
    happens to have Drive connected would otherwise see a different test from
    the one CI runs.
    """
    monkeypatch.setattr(REGISTRY["gdrive"], "is_configured",
                        lambda self: (False, "not set up"), raising=False)
    route = catalog(["gdrive"])

    assert "gdrive" not in _names(route)


def test_a_configured_built_in_is_never_hidden_by_a_server(catalog, monkeypatch):
    """The line that keeps this safe.

    Gmail and Calendar are what every mail and diary action in this app is
    built on. If somebody adds an MCP Gmail, hiding a working built-in would
    take away state they own and break seventeen jobs silently. A source they
    have set up stays visible whatever we think is better.
    """
    from chitragupta.connectors import REGISTRY

    monkeypatch.setattr(REGISTRY["gmail"], "is_configured",
                        lambda self: (True, ""), raising=False)
    route = catalog(["gmail"])

    assert "gmail" in _names(route)


# ── the other direction: the catalog must not offer what a built-in reaches ──
#
# `prefer_mcp` only ever answered half the question. It keeps a built-in off
# the Connectors screen where a vendor server wins — and nothing kept **Add a
# connector** from offering a server where the built-in wins. So GitHub sat on
# the Connectors screen with a green CONNECTED badge while the catalog, two
# clicks away, offered to connect GitHub. Same bug as the Notion screenshot,
# arriving from the opposite end.
#
# The fix is structural rather than a list of names to remember: an entry
# declares the built-in it duplicates (`CatalogEntry.same_as`), one function
# decides which of the two is the route, and both screens read that function.


def test_every_catalog_entry_that_shadows_a_built_in_says_so():
    """The guard that makes this hold for connectors nobody has written yet.

    An entry whose id is already a connector name is, by construction, a
    second route to that source. Adding one without declaring the pair is how
    the GitHub duplicate got here, and it is a diff nobody notices — so the
    matching is mechanical and the declaration is enforced, not remembered.
    """
    from chitragupta.connectors.mcp_catalog import CATALOG

    for entry in CATALOG:
        if entry.id in REGISTRY:
            assert entry.same_as == entry.id, (
                f"catalog entry '{entry.id}' is also a built-in connector and "
                f"must declare same_as='{entry.id}'")


def test_a_declared_twin_is_a_connector_that_exists():
    """A pairing that names nothing silently stops deduplicating, and the
    symptom is the duplicate coming back — months later, on a user's machine
    and not on ours."""
    from chitragupta.connectors.mcp_catalog import CATALOG

    for entry in CATALOG:
        if entry.same_as:
            assert entry.same_as in REGISTRY, (
                f"catalog entry '{entry.id}' stands in for "
                f"'{entry.same_as}', which is not a connector")


def test_no_source_is_reachable_from_both_screens_at_once(catalog, monkeypatch):
    """The invariant itself, checked over every pairing rather than the one
    that was reported. Whatever each connector's state, a source appears on
    the Connectors screen or in the catalog — never both."""
    from chitragupta.api.routes import connectors as route
    from chitragupta.connectors.mcp_catalog import CATALOG

    twins = [e for e in CATALOG if e.same_as]
    assert twins, "nothing to check — the pairing declaration has gone missing"

    for entry in twins:
        for configured in (True, False):
            monkeypatch.setattr(REGISTRY[entry.same_as], "is_configured",
                                lambda self, c=configured: (c, ""),
                                raising=False)
            r = catalog([])
            # **Offered to connect**, which is not the same as visible. A
            # configured connector is shown because it is the user's state,
            # and its row carries Sync rather than Connect — that is not a
            # second way to set the source up, and a retired one sitting
            # beside the server meant to replace it is the migration, not the
            # bug. What must never happen twice is the *offer*.
            rows = {row["name"]: row for row in _rows(r)}
            offers_connect = (entry.same_as in rows
                              and not rows[entry.same_as]["ready"])
            catalog_rows = {c["id"]: c
                            for c in r.connector_catalog()["available"]}
            offers_add = (entry.id in catalog_rows
                          and not catalog_rows[entry.id]["added"])
            where = "configured" if configured else "not configured"
            assert not (offers_connect and offers_add), (
                f"{entry.name} can be connected two ways when its built-in "
                f"is {where}")
            assert offers_connect or offers_add or configured, (
                f"{entry.name} cannot be connected at all when its built-in "
                f"is {where}")
    assert route  # the module under test, imported for the reader


def test_a_configured_retired_connector_does_not_hide_its_replacement(
        catalog, monkeypatch):
    """The reported case, and the one that took two passes to get right.

    GitHub sat CONNECTED on the Connectors screen. The first fix read that as
    "the built-in is the route" and hid GitHub from the catalog — so the
    server meant to *replace* it was the one thing the user could not reach,
    and the screen looked exactly as before.

    Being shown and being the route are two questions. A retired connector is
    somebody's existing state; `prefer_mcp` says the server wins, and it says
    so whether or not the old one is still set up.
    """
    monkeypatch.setattr(REGISTRY["github"], "is_configured",
                        lambda self: (True, ""), raising=False)
    r = catalog([])
    rows = {row["name"]: row for row in _rows(r)}

    assert "github" in rows, "their own connector must not vanish under them"
    assert rows["github"]["superseded_by"] == "github", (
        "and the row has to say why it is the only one without a future")
    assert rows["github"]["can_disconnect"], (
        "a retirement the user cannot act on is a retirement in name only")
    assert "github" in {c["id"] for c in r.connector_catalog()["available"]}


def test_a_retired_built_in_leaves_its_catalog_entry_standing(catalog, monkeypatch):
    """The rule cuts both ways or it takes a source away. Notion's built-in
    stands down for the vendor's server, so the catalog is the *only* place
    Notion can be connected and must keep offering it."""
    monkeypatch.setattr(REGISTRY["notion"], "is_configured",
                        lambda self: (False, ""), raising=False)
    r = catalog([])

    assert "notion" not in _names(r)
    assert "notion" in {c["id"] for c in r.connector_catalog()["available"]}


def test_an_added_server_still_shows_in_the_catalog(catalog, monkeypatch):
    """Hiding what the user has already added is losing their state to our
    tidying. The row stays and reports 'already added' — which is a status,
    not a second way to connect."""
    monkeypatch.setattr(REGISTRY["notion"], "is_configured",
                        lambda self: (True, ""), raising=False)
    r = catalog(["notion"])

    entries = {c["id"]: c for c in r.connector_catalog()["available"]}
    assert entries["notion"]["added"] is True


def test_a_pairing_holds_when_the_two_names_differ(catalog):
    """`filesystem` is our `files`, and an id comparison would never have
    found it — the declaration is what pairs them.

    Which way the pair resolves is the rule doing its job rather than a
    preference: Local Files is on-device and `always_available`, so it is
    always the route, so the catalog never offers a second one. An id match
    would have left *A folder on this Mac* sitting in the catalog forever,
    offering to connect a folder the user can already point at — and needing
    Node installed to do it.
    """
    r = catalog([])

    assert "files" in _names(r)
    assert "filesystem" not in {c["id"]
                                for c in r.connector_catalog()["available"]}
