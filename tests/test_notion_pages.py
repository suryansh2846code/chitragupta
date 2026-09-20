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
