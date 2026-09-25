"""Every source says where it reads from and who wrote the client.

Two facts, and the screen had neither. A row said *Gmail · Connected* — not
whether anything left the Mac to get that mail, and not whether the thing
talking to Google was ours or Google's own server. Both matter, and they
matter to different questions:

* **Where it reads from** is the product promise. "Nothing leaves this
  machine" is the sentence at the top of the screen, and it is true of six
  sources and not of the rest. Grouping by topic — mail, chat, docs, code —
  answered a question nobody was asking, because every row already says what
  it gives you.
* **Who wrote the client** is who to chase when it misbehaves. A built-in is
  ours to fix. A vendor's MCP server is theirs, and it can change under us.

The grouping used to be a map in `web/connectors.js` keyed by connector name,
and **four connectors were missing from it** — Slack, Telegram, Apple Health
and Google Fit fell through to a heading reading *Custom sources*, about four
things nobody had customised. A fact the backend already knows does not get a
second copy in the frontend; `Connector.runs_on_device` is now the only one.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from chitragupta.connectors import REGISTRY

WEB = pathlib.Path(__file__).resolve().parent.parent / "chitragupta" / "web"

#: Reads something already on this machine. No account, nothing leaves.
ON_DEVICE = {"files", "notes", "imessage", "apple_mail", "apple_calendar",
             "apple_health"}


@pytest.mark.parametrize("name", sorted(ON_DEVICE))
def test_the_on_device_sources_say_so(name):
    assert REGISTRY[name].runs_on_device is True


@pytest.mark.parametrize(
    "name", sorted(set(REGISTRY) - ON_DEVICE))
def test_everything_else_is_an_account(name):
    """Claiming on-device for a source that calls out to a service would put
    it under a heading promising nothing leaves this Mac. That is not a
    cosmetic mistake — it is the one sentence the whole product rests on."""
    assert REGISTRY[name].runs_on_device is False


def test_every_connector_is_classified_by_the_class_not_a_map():
    """The bug that started this. A frontend map keyed by connector name will
    always drift, because adding a connector does not make you edit it."""
    source = (WEB / "connectors.js").read_text()
    assert "group:" not in source.split("CONNECTOR_GROUPS")[0], (
        "CONNECTOR_META is carrying a second copy of where a source runs")
    for name in REGISTRY:
        assert not (f'{name}:' in source and f'{name}: {{ group:' in source)


def test_no_source_falls_into_a_heading_that_is_not_true():
    """The four that did. Every row must land in a section that describes it,
    and the sections are derived, so this holds for connectors not yet written."""
    from chitragupta.api.routes import connectors as route

    rows = asyncio.run(route.connectors())["connectors"]
    for row in rows:
        assert "on_device" in row, f"{row['name']} cannot be placed"
        assert isinstance(row["on_device"], bool)
        assert row["kind"] in ("builtin", "mcp", "custom"), row["kind"]


def test_the_row_says_which_kind_it_is():
    from chitragupta.api.routes import connectors as route

    rows = {r["name"]: r for r in asyncio.run(route.connectors())["connectors"]}
    assert rows["gmail"]["kind"] == "builtin"
    assert all(r["kind"] == "mcp" for n, r in rows.items() if n.startswith("mcp:"))


def test_a_local_server_is_not_filed_as_an_account():
    """A stdio MCP server is a process on this Mac reading this Mac. The
    section follows where the data is, not who wrote the code — otherwise
    'On this Mac' would be a claim about authorship."""
    from chitragupta.connectors.mcp_source import MCPServerSpec

    assert MCPServerSpec(id="x", name="X", transport="stdio").is_remote is False
    assert MCPServerSpec(id="y", name="Y", transport="http").is_remote is True


def test_the_legend_defines_every_tag_it_can_show():
    """A tag nobody defined is an abbreviation, and `MCP` is the one users
    would have no way to look up."""
    page = (WEB / "index.html").read_text()
    script = (WEB / "connectors.js").read_text()

    assert "cn-legend" in page
    for label in ("Built-in", "MCP", "Custom"):
        assert f">{label}</span>" in page, f"{label} is shown but never explained"
        assert f'label: "{label}"' in script
