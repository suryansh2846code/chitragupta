"""What one agent may use, and why it cannot use the rest.

A user connected Notion, asked their agent about it, and was told it was still
syncing. It wasn't — the agent simply had no connector access, and no screen
anywhere would have shown them that. This is that screen, and these are the
properties that make it answer the question instead of restating it.

Everything here runs the real render and reads what landed. `node --check`
passes on a temporal-dead-zone ReferenceError, which is how a panel once
rendered blank while every test passed, and the interesting facts — which
group a tool landed in, whether a row is a switch or a reason, what the switch
sent — are all properties of the produced DOM.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

#: One word each, and a heading — the shape `describe_tools()` now returns.
BUILTIN = [
    {"name": "search_brain", "label": "Search", "category": "Memory",
     "description": "Search your brain", "source": "builtin", "connector": ""},
    {"name": "remember", "label": "Remember", "category": "Memory",
     "description": "Remember something", "source": "builtin", "connector": ""},
    {"name": "run_python", "label": "Run", "category": "Your Mac",
     "description": "Run Python on this Mac", "source": "builtin", "connector": ""},
]

#: The reading order, as the API names it. "Your Mac" last on purpose.
CATEGORIES = ["Memory", "Tasks", "Your Mac"]
CATEGORY = {"name": "mcp", "label": "Everything my connectors can read",
            "description": "Stays correct as connectors are added or removed.",
            "source": "category", "connector": ""}
NOTION_TOOL = {"name": "notion__search", "label": "Search Notion",
               "description": "Search pages", "source": "mcp", "connector": "Notion"}

READY = {"name": "notion", "label": "Notion", "ready": True, "reason": "", "mcp": True}
DOWN = {"name": "linear", "label": "Linear", "ready": False,
        "reason": "Linear needs signing in", "mcp": True}


def run(agent_tools, tools, connectors, **kw) -> dict:
    payload = {
        "agent": {"id": "chotu", "name": "chotu", "tools": agent_tools},
        "tools": tools, "connectors": connectors,
        "categories": kw.pop("categories", CATEGORIES), **kw,
    }
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/agent_tools.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2500:]
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def full() -> dict:
    return run(["search_brain"], [*BUILTIN, CATEGORY, NOTION_TOOL], [READY, DOWN],
               toggle="mcp")


# ── grouping: where a tool comes from ─────────────────────────────────────
def test_tools_are_grouped_by_where_they_come_from(full):
    kinds = {g["name"]: g["kind"] for g in full["groups"]}
    assert kinds["Memory"] == "builtin"
    assert kinds["Notion"] == "connector"
    assert kinds["Your connectors"] == "category"


def test_built_ins_are_split_by_subject_not_piled_under_one_heading(full):
    """Thirty-two rows under "Built in" is a wall nobody can choose from."""
    builtin = [g["name"] for g in full["groups"] if g["kind"] == "builtin"]
    assert "Built in" not in builtin
    assert {"Memory", "Your Mac"} <= set(builtin), builtin


def test_the_heading_order_is_the_api_s_and_not_alphabetical():
    """"Your Mac" belongs last whatever letter it starts with, and that is a
    judgement the layer owning the categories already made.

    The fixture names an order that DISAGREES with the alphabet, because one
    that agrees cannot tell the two apart — the first version of this test
    used Memory/Your Mac, which are alphabetical anyway, and passed happily
    with the sort replaced by localeCompare.
    """
    web = {"name": "web_search", "label": "Search", "category": "Web",
           "description": "", "source": "builtin", "connector": ""}
    mem = {"name": "search_brain", "label": "Search", "category": "Memory",
           "description": "", "source": "builtin", "connector": ""}
    out = run([], [web, mem], [], categories=["Web", "Memory"])
    names = [g["name"] for g in out["groups"] if g["kind"] == "builtin"]
    assert names == ["Web", "Memory"], (
        f"{names} is the alphabet, not the order the API asked for")


def test_a_tool_whose_category_the_api_did_not_name_still_renders():
    """Visible enough to get fixed, not broken enough to lose the screen."""
    odd = {"name": "future_tool", "label": "Future", "description": "",
           "source": "builtin", "connector": ""}
    out = run([], [odd], [])
    names = [g["name"] for g in out["groups"]]
    assert "Other" in names, names


def test_a_connector_tool_is_named_after_its_connector(full):
    """"search" and "search_2" are indistinguishable; "Search Notion" is not."""
    notion = next(g for g in full["groups"] if g["name"] == "Notion")
    assert notion["tools"] == ["notion__search"]
    assert "Search Notion" in full["html"]


def test_connectors_come_before_the_built_ins(full):
    """The screen exists because of connectors. Built-ins never needed
    explaining, so they do not go first."""
    names = [g["name"] for g in full["groups"]]
    assert names.index("Your connectors") < names.index("Memory")
    assert names.index("Notion") < names.index("Memory")


def test_the_category_is_its_own_group(full):
    """It grants everything the connectors can read, so filing it under one
    connector would misdescribe what the switch does."""
    cat = next(g for g in full["groups"] if g["kind"] == "category")
    assert cat["tools"] == ["mcp"]


def test_the_category_shows_its_label_never_its_stored_value(full):
    assert "Everything my connectors can read" in full["html"]
    assert ">mcp<" not in full["html"], "the protocol's acronym reached the screen"


def test_no_category_row_when_the_user_has_no_connectors():
    """The API omits the row when nothing is behind it; the screen must not
    invent one. A switch that grants nothing reads as a broken app."""
    out = run(["search_brain"], BUILTIN, [])
    assert all(g["kind"] != "category" for g in out["groups"])
    assert "Everything my connectors can read" not in out["html"]


# ── the toggle ────────────────────────────────────────────────────────────
def test_turning_a_tool_on_saves_the_whole_list(full):
    sent = full["toggled"]["sent"]
    assert sent == ["search_brain", "mcp"], sent


def test_it_saves_to_a_relative_path(full):
    """Never a host or port — the desktop app binds a different one per install."""
    assert full["toggled"]["path"] == "/api/agents/chotu/tools"
    assert not full["toggled"]["path"].startswith("http")


def test_the_switch_reports_its_state_to_assistive_tech(full):
    assert full["toggled"]["ariaAfter"] == "true"
    assert 'role="switch"' in full["html"]


def test_turning_one_off_sends_the_list_without_it():
    out = run(["search_brain", "mcp"], [*BUILTIN, CATEGORY, NOTION_TOOL], [READY], toggle="mcp")
    assert out["toggled"]["sent"] == ["search_brain"]


def test_a_failed_save_puts_the_switch_back():
    """A switch left showing a state that was never stored is worse than one
    that refuses: it says the agent can do something it cannot."""
    out = run(["search_brain"], [*BUILTIN, CATEGORY], [READY],
              toggle="mcp", failSave=True)
    assert out["toggled"]["onAfter"] is False
    assert out["toggled"]["ariaAfter"] == "false"
    assert out["agentToolsAfter"] == ["search_brain"]


def test_a_failed_save_explains_itself_in_the_row():
    """Beside the switch that lied, not in a toast that is gone by the time
    the user looks back at it."""
    out = run(["search_brain"], [*BUILTIN, CATEGORY], [READY],
              toggle="mcp", failSave=True)
    assert "Couldn't save" in out["rowError"], out["rowError"]


# ── what cannot be switched ───────────────────────────────────────────────
def test_an_unreachable_connector_states_the_reason(full):
    """Not a disabled toggle with no explanation, and the reason is the
    connector's own — written by the layer that failed."""
    assert "Linear needs signing in" in full["html"]


def test_an_unreachable_connector_is_listed_at_all(full):
    """Contributing no tools, it would otherwise be absent — and absent reads
    as "Chitragupta lost it" rather than "sign in again"."""
    assert any(g["name"] == "Linear" for g in full["groups"])


def test_a_blocked_group_says_why_and_offers_no_switch(full):
    """A connector that cannot answer contributes no tools, so there is nothing
    to switch and nothing to disclose. It is listed, it says why, and it shows
    no control — a control that cannot work reads as the app being broken.

    The reason used to be drawn **twice**: once on the heading and once inside a
    card behind a disclosure reading "Show all 0 tools". With four unreachable
    connectors that was the entire first screen of the panel, so the duplicate
    and its disclosure went and the heading kept the sentence."""
    linear = full["html"].split("Linear", 1)[1].split("</section>", 1)[0]

    assert "at-toggle" not in linear, linear[:300]
    assert linear.count("Linear needs signing in") == 1, linear[:400]
    assert "Show all 0 tools" not in linear
    assert "at-card" not in linear, "an empty card under a heading says less than nothing"


def test_a_blocked_row_links_to_where_it_is_fixed(full):
    assert "Open Connectors" in full["html"]


def test_that_link_opens_connectors():
    out = run(["search_brain"], [*BUILTIN, CATEGORY], [DOWN], clickFix=True)
    assert out["openedConnectors"] >= 1, "the fix link did not open Connectors"


# ── empty ─────────────────────────────────────────────────────────────────
def test_an_agent_with_no_tools_is_not_a_blank_panel():
    out = run([], [], [])
    assert "no tools yet" in out["html"]
    assert "your brain is read on every" in out["html"], "it must say what still works"
    assert "Open Connectors" in out["html"], "it must offer the first thing worth adding"


# ── one readable line ─────────────────────────────────────────────────────
def test_a_connectors_own_instructions_do_not_become_the_page():
    """A connector's description is written FOR A MODEL by whoever wrote the
    server — Notion's search tool ships four hundred words about query_type and
    filter nesting. Rendered whole, twenty-seven of those are a page of prose
    where a list of switches should be."""
    wall = ("Before the first content search for this connection, call fetch "
            'with {"id":"self"} unless its current access result is already in '
            "context. Choose the content-search tool by "
            "current_tool_access.ai_search.status, not by query wording. " * 4)
    out = run([], [{"name": "notion__search", "label": "Search", "connector": "Notion",
                    "source": "mcp", "description": wall}],
              [{"name": "notion", "label": "Notion", "ready": True, "reason": "", "mcp": True}])
    shown = out["html"].split('class="at-ds"', 1)[1].split("</span>", 1)[0]
    assert len(shown) < 200, f"{len(shown)} characters reached the row"
    assert "query wording" not in shown, "it kept going past the first sentence"


def test_a_short_description_is_left_alone():
    out = run([], BUILTIN, [])
    assert "Search your brain" in out["html"]


def test_trimming_is_display_only():
    """The full text is still what the model is given. Nothing here may change
    what a tool does — only how much of its manual is on screen."""
    src = (WEB / "tools.js").read_text()
    blurb = src.split("function toolBlurb", 1)[1].split("\n}", 1)[0]
    for mutation in ("row.description =", "t.description =", "delete "):
        assert mutation not in blurb, f"toolBlurb mutates the row: {mutation}"


# ── the protocol is ours to know, not the user's ──────────────────────────
# These two rules moved here when the read-only Tools drawer was deleted. It
# was a weaker copy of this panel — same endpoint, same grouping, no switches —
# and it carried the only tests for them. The drawer is gone; the rules are not.
ACRONYM = re.compile(r"\bmcp\b", re.I)

#: What a person actually reads. Tags and their attributes are stripped, because
#: the tool's stored id legitimately rides in `data-tool=` — the rule is about
#: the words on the screen, and an assertion over raw HTML fails on the id while
#: a visible heading could still say it.
def _visible(html: str) -> str:
    return re.sub(r"<[^>]*>", " ", html or "")


def test_the_protocol_is_never_named_on_screen(full):
    """`mcp_source.py`: "To the user this is a connector. The acronym never
    reaches the UI, exactly as 'vendor CLI' never reaches the sign-in card."
    The payload says "mcp" four times over; the screen must not say it once."""
    text = _visible(full["html"])
    hit = ACRONYM.search(text)
    assert not hit, ("the protocol's acronym reached the screen:\n"
                     + text[max(0, hit.start() - 120):][:300])


def test_the_id_may_carry_it_because_the_id_is_not_read(full):
    """The guard above is narrow on purpose. A tool's stored id is what the
    switch sends back, and stripping it to satisfy a text rule would break the
    save — so the id keeps the acronym and the page never shows it."""
    assert 'data-tool="mcp"' in full["html"]


def test_a_connector_that_did_not_name_itself_is_still_not_named_after_it():
    """A row can arrive with a source and no connector label. Vague is
    survivable; naming the protocol is not — and passing it off as one of ours
    is worse, because then the user cannot disconnect what is reading for them."""
    out = run([], [{"name": "do_thing", "label": "Do", "description": "",
                    "source": "mcp", "connector": ""}], [])
    assert not ACRONYM.search(_visible(out["html"])), out["html"]
    names = [g["name"] for g in out["groups"]]
    assert "Built in" not in names, "a connector's tool was passed off as one of ours"


# ── a connector's own tools are the one wall left ────────────────────────
def test_a_connector_group_offers_allow_all(full):
    """A connector's tools carry no tier — a server does not declare one — so
    they get no Read/Change switches and the disclosure was the only control
    they had. Twenty-five Notion tools, one at a time, is the wall this screen
    was built to remove."""
    notion = full["html"].split("Notion", 1)[1]

    assert 'data-bulk="notion__search"' in notion


def test_an_unreachable_connector_still_offers_no_such_button(full):
    """Never a control that cannot work."""
    linear = full["html"].split(">Linear<", 1)[1].split("</section>", 1)[0]

    assert "Allow all" not in linear
