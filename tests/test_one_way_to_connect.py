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


def test_the_two_with_vendor_servers_declare_it():
    """Both had their write path removed in favour of MCP. The declaration is
    what stops them being offered as a second way to do the same thing."""
    assert REGISTRY["notion"].prefer_mcp == "notion"
    assert REGISTRY["linear"].prefer_mcp == "linear"


def test_nothing_else_claims_a_server_it_does_not_have():
    """A connector that stood down for a server nobody ships would simply
    disappear — the user would lose the source with no way to get it back."""
    for name, cls in REGISTRY.items():
        if name in ("notion", "linear"):
            continue
        assert not cls.prefer_mcp, f"{name} stands down for nothing"


def test_the_on_device_sources_can_never_stand_down():
    """There is no MCP server for the Messages database on this Mac. These
    are the connectors that exist *because* nothing else can reach them, and
    a rule that retired them would take the sources away entirely."""
    for name in ("imessage", "apple_mail", "notes", "apple_calendar",
                 "apple_health", "files"):
        assert not REGISTRY[name].prefer_mcp, (
            f"{name} is on-device — no server can replace it")


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

    for kept in ("gmail", "gcal", "github", "files", "notes"):
        assert kept in names, f"{kept} disappeared"


def test_any_source_added_as_a_server_hides_its_unconfigured_built_in(catalog):
    """The universal half of the rule, not just the two that were retired.

    Add a server whose id matches ANY built-in and the built-in stops being
    offered — so "two of the same app on one screen" cannot happen again for
    a source nobody has thought about yet.
    """
    route = catalog(["github"])

    assert "github" not in _names(route)


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
