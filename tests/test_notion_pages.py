"""Finding a Notion page, so that writing to one is possible at all.

**This is a bug the tests did not catch and a user did, on the first try.**

`notion_append` shipped requiring a `page_id`. The sync has stored that uuid in
every memory's metadata since it was written, and nothing surfaced it — not
`search_source`, which returns recalled prose, and not anything else. So the
action was complete, verified, undoable, allow-listed and **impossible to
call**. Every test around it passed because every test passed the id in.

The third time this exact gap has appeared here: `list_mail` had it (describe a
message, cannot archive it), `calendar_lookup` had it (describe a meeting,
cannot move it). Both were fixed by showing the id already held. So is this.

What it looked like from the outside is the part worth keeping. Asked to *"add
Dishu to the Mera store page"*, the agent did not say "I have no way to address
that page". It produced a **reason** — that Notion needed authorizing in
claude.ai's connector settings. That is a different product, for a permission
that does not exist, and `prompt._BRAIN` forbids that exact sentence in as many
words. It was said anyway.

**A capability gap does not surface as an error. It surfaces as a plausible
lie**, and no amount of prompt text fixes it, because the model is not
disobeying — it is filling a hole. The hole is the bug.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import notion_tools


class FakeNotion:
    """A connector that is connected, and knows some pages."""

    def __init__(self, pages=None, *, ready=True, why="", raises=None):
        self._pages = pages if pages is not None else []
        self._ready = ready
        self._why = why
        self._raises = raises
        self.asked: list[tuple[str, int]] = []

    def is_configured(self):
        return self._ready, self._why

    def search_pages(self, about="", limit=10):
        self.asked.append((about, limit))
        if self._raises:
            return {"ok": False, "error": self._raises, "pages": []}
        return {"ok": True, "pages": self._pages}


PAGE = {"id": "a1b2c3d4-e5f6", "title": "Mera store",
        "url": "https://notion.so/a1b2c3d4", "edited": "2026-09-18"}


@pytest.fixture
def notion(monkeypatch):
    def install(**kw):
        fake = FakeNotion(**kw)
        monkeypatch.setattr(notion_tools, "_notion", lambda: fake)
        return fake
    return install


# ── the id, which is the whole point ─────────────────────────────────────
def test_it_returns_the_page_id_an_action_needs(notion):
    """Without this line there is no path from "the Mera store page" to
    anything `notion_append` can be given."""
    notion(pages=[PAGE])

    out = notion_tools.notion_pages("mera store")

    assert "a1b2c3d4-e5f6" in out
    assert "page_id=" in out, "the id has to be labelled as the thing to pass"
    assert "Mera store" in out


def test_it_tells_the_model_not_to_guess_one(notion):
    """A guessed uuid writes into somebody else's page."""
    notion(pages=[PAGE])

    assert "Never guess a page id" in notion_tools.notion_pages()


def test_what_the_user_asked_for_reaches_notion(notion):
    """Not a fixed listing filtered locally — the search term goes through, so
    a page made since the last sync is findable."""
    fake = notion(pages=[PAGE])

    notion_tools.notion_pages("mera store", limit=5)

    assert fake.asked == [("mera store", 5)]


# ── the sentence that must never be invented again ───────────────────────
def test_a_disconnected_notion_says_so_in_this_apps_terms(notion):
    """The observed failure was an invented authorization step in claude.ai's
    settings. The tool has to give the model a true sentence to repeat, and
    that sentence has to name where Notion is actually set up."""
    out = notion(ready=False, why="click setup to paste your integration secret")
    text = notion_tools.notion_pages("mera store")

    assert not text.ok
    assert "not connected" in text
    assert "Connectors" in text
    assert "Chitragupta" in text
    assert "no authorization to do anywhere else" in text


def test_it_never_mentions_another_product(notion):
    """`prompt._BRAIN` forbids the model saying this. The tool must not hand
    it the words either."""
    notion(ready=False, why="not set up")
    text = notion_tools.notion_pages()

    for wrong in ("claude.ai", "ChatGPT", "session authorization"):
        assert wrong.lower() not in text.lower()


# ── nothing found is not nothing exists ──────────────────────────────────
def test_no_match_explains_notions_own_sharing_model(notion):
    """An integration sees only pages somebody added it to, so "no match" is
    usually "not shared with it yet". Told "no such page", a user goes looking
    for a typo; the actual fix is three clicks in Notion."""
    notion(pages=[])

    out = notion_tools.notion_pages("mera store")

    assert "Connections" in out
    assert "Add" in out


def test_a_refusal_from_notion_is_passed_on_not_swallowed(notion):
    notion(raises="Notion cannot see that page.")

    out = notion_tools.notion_pages("x")

    assert not out.ok
    assert "cannot see" in out


# ── the invariant, so the next agent cannot inherit the hole ─────────────
def test_no_agent_can_hold_a_notion_action_without_a_way_to_find_a_page():
    """The rule that broke: an action addressed by uuid needs the read that
    produces one, or it is a control that cannot work.

    Written over every template rather than as a list beside the actions,
    because a list is the thing that gets forgotten — which is exactly how
    this shipped.
    """
    from chitragupta.agents.library import BY_ID, expand_tools

    for template in BY_ID.values():
        actions = set(template.actions or [])
        if not {"notion_append", "notion_create_page"} & actions:
            continue
        # `expand_tools` resolves the generalist's "everything" marker, so
        # this is the RESOLVED list — no escape hatch for the one agent that
        # happens to hold every tool today.
        tools = set(expand_tools(list(template.tools or [])))
        assert "notion_pages" in tools, (
            f"{template.id} can write to Notion and cannot find a page id")


def test_reaching_notion_goes_through_the_connector_gate():
    """`agents/CLAUDE.md`: an agent asks before it reaches a connector, and
    the asking is enforced in `loop.py` rather than in a prompt.

    This tool searches the user's real Notion, so it is a reach into a
    connector and has to be declared as one. It was not, for the first commit
    of its life — which is precisely the failure the map's own comment
    records: the gate stopped an agent reading a Notion page through MCP
    while `list_mail` read the whole inbox, because only one of the two was
    listed there.
    """
    from chitragupta.agents.connector_grants import connector_of

    assert connector_of("notion_pages") == "notion"


def test_every_first_party_tool_that_reaches_a_connector_is_gated():
    """The general form, so the next one cannot be forgotten either.

    Kept as an explicit pairing rather than derived, because the thing being
    checked is that somebody *thought about it* — a derivation would answer
    the same way whether or not the tool had been considered.
    """
    from chitragupta.agents.connector_grants import connector_of

    reaches = {
        "list_mail": "gmail", "gmail_search": "gmail", "read_thread": "gmail",
        "calendar_lookup": "gcal", "find_time": "gcal",
        "notion_pages": "notion",
    }
    for tool, connector in reaches.items():
        assert connector_of(tool) == connector, (
            f"{tool} reaches {connector} and is not behind the connector gate")


# ── there are two Notions, and only one of them is mine ──────────────────
def test_a_notion_connected_as_a_custom_source_is_not_called_disconnected(
        notion, monkeypatch):
    """Nearly shipped, and it would have been worse than the bug it fixed.

    A user can connect Notion two ways: the built-in connector, which wants
    an integration secret, or as a **custom source** — an MCP server. This
    tool drives the first. The second shows a green CONNECTED badge on the
    Connectors screen and is invisible to it.

    So the honest-sounding sentence "Notion is not connected" is, for that
    user, a flat contradiction of what their own screen says — and a model
    repeating it sends them to fix something that is not broken. Which is how
    this whole thread started.
    """
    notion(ready=False, why="click setup to paste your integration secret")
    monkeypatch.setattr(notion_tools, "_reachable_over_mcp", lambda: True)

    out = notion_tools.notion_pages("mera store")

    assert "not connected" not in out.lower()
    assert "connected as a custom source" in out
    # And it points at the thing that DOES work, by name.
    assert "notion-search" in out
    assert "Do not tell the user Notion is unavailable" in out


def test_with_neither_route_it_still_says_connect_it_here(notion, monkeypatch):
    """The other branch has to keep working: no built-in secret and no custom
    source really is disconnected, and the fix really is in this app."""
    notion(ready=False, why="click setup to paste your integration secret")
    monkeypatch.setattr(notion_tools, "_reachable_over_mcp", lambda: False)

    out = notion_tools.notion_pages("mera store")

    assert not out.ok
    assert "Connectors" in out and "Chitragupta" in out


def test_looking_for_the_other_route_never_starts_a_server(notion, monkeypatch):
    """Listing MCP tools starts every server the user has. A question asked on
    the way to saying "I cannot help" must not cost that, so this reads the
    server LIST and nothing more."""
    import inspect

    source = inspect.getsource(notion_tools._reachable_over_mcp)

    assert "list_servers" in source
    assert "list_tools" not in source, "it would start every server to answer"
