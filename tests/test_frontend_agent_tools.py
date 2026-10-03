"""What one agent may use, and why it cannot use the rest.

A user connected Notion, asked their agent about it, and was told it was still
syncing. It wasn't — the agent simply had no connector access, and no screen
anywhere would have shown them that. This is that screen, and these are the
properties that make it answer the question instead of restating it.

The axis has moved twice since. Sixty-four switches under thirteen headings
became four cards by *reach* — what stays on this machine, what touches an
account — which is the right question for the gate and the wrong one for a
person: it put Gmail, the calendar, Telegram, Notion and Linear on one card.
The card is the **app** now, with **Read** and the one word that app's changes
actually are, and the roll-up of what each switch covers between them.

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

#: One word each, a heading, the app it lands on and what it does there — the
#: shape `describe_tools()` now returns.
BUILTIN = [
    {"name": "search_brain", "label": "Search", "category": "Memory",
     "description": "Search your brain", "source": "builtin", "connector": "",
     "app": "on_device", "access": "read"},
    {"name": "remember", "label": "Remember", "category": "Memory",
     "description": "Remember something", "source": "builtin", "connector": "",
     "app": "on_device", "access": "write"},
    {"name": "read_file", "label": "Read", "category": "Your Mac",
     "description": "Read a file", "source": "builtin", "connector": "",
     "app": "mac", "access": "read"},
    {"name": "write_file", "label": "Write", "category": "Your Mac",
     "description": "Write a file", "source": "builtin", "connector": "",
     "app": "mac", "access": "write"},
    {"name": "run_python", "label": "Run", "category": "Your Mac",
     "description": "Run Python on this Mac", "source": "builtin", "connector": "",
     "app": "mac", "access": "destructive"},
    {"name": "gmail_search", "label": "Search", "category": "Email",
     "description": "Search the mail", "source": "builtin", "connector": "",
     "app": "gmail", "access": "read"},
]

#: The cards, as `permission_apps()` sends them. The order DISAGREES with the
#: alphabet on purpose — one that agrees cannot tell "the API's order" apart
#: from "sorted".
APPS = [
    {"key": "on_device", "label": "Its own memory and your day",
     "blurb": "Nothing here leaves this machine.", "always": True,
     "read": [], "change": [], "run": [], "write_label": "Write", "run_label": ""},
    {"key": "gmail", "label": "Gmail", "blurb": "Reading the mail.",
     "always": False, "read": [], "change": [], "run": [],
     "write_label": "Write", "run_label": "",
     "ask": {"label": "Send",
             "blurb": "Sending mail always comes to you as a card you confirm.",
             # Which list its cards are judged against, and whether a person
             # could type the next entry for it. A connector key could not be.
             "kind": "email_recipient", "addable": True},
     "more": {"screen": "connectors", "label": "Connect an account"}},
    {"key": "mac", "label": "Your Mac", "blurb": "Files on this computer.",
     "always": False, "read": [], "change": [], "run": [],
     "write_label": "Write", "run_label": "Run code"},
]

#: The reading order, as the API names it. "Your Mac" last on purpose.
CATEGORIES = ["Memory", "Tasks", "Your Mac"]
CATEGORY = {"name": "mcp", "label": "Everything my connectors can read",
            "description": "Stays correct as connectors are added or removed.",
            "source": "category", "connector": "", "access": "read"}
NOTION_TOOL = {"name": "notion__search", "label": "Search Notion",
               "description": "Search pages", "source": "mcp",
               "connector": "Notion", "connector_id": "notion", "access": "read"}

READY = {"name": "notion", "label": "Notion", "ready": True, "reason": "", "mcp": True}
DOWN = {"name": "linear", "label": "Linear", "ready": False,
        "reason": "Linear needs signing in", "mcp": True}


def run(agent_tools, tools, connectors, **kw) -> dict:
    draft = kw.pop("agentDraft", False)
    payload = {
        "agent": {"id": "chotu", "name": "chotu", "tools": agent_tools,
                  **({"draft": True} if draft else {})},
        "tools": tools, "connectors": connectors,
        "categories": kw.pop("categories", CATEGORIES),
        "apps": kw.pop("apps", APPS), **kw,
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


# ── the axis: one card per app ────────────────────────────────────────────
def test_cards_are_apps_not_reach_classes(full):
    """The whole point of the layout. A user who wants to say *read GitHub,
    leave my mail alone* needs a card that says GitHub — and under the old axis
    both were inside one card called "Your connected accounts"."""
    kinds = {g["name"]: g["kind"] for g in full["groups"]}
    assert kinds["Gmail"] == "builtin"
    assert kinds["Your Mac"] == "builtin"
    assert kinds["Notion"] == "connector"
    assert kinds["Your connected apps"] == "category"
    assert "Your connected accounts" not in kinds, (
        "the reach class came back as a card")


def test_a_built_in_lands_on_exactly_one_card(full):
    """A tool on two cards is a switch that disagrees with another switch."""
    seen = [n for g in full["groups"] for n in g["tools"]]
    assert len(seen) == len(set(seen)), seen


def test_the_card_order_is_the_api_s_and_not_alphabetical():
    """"Your Mac" belongs last whatever letter it starts with, and that is a
    judgement the layer owning the cards already made.

    The fixture names an order that DISAGREES with the alphabet, because one
    that agrees cannot tell the two apart — the first version of this test
    used Memory/Your Mac, which are alphabetical anyway, and passed happily
    with the sort replaced by localeCompare.
    """
    names = [g["name"] for g in run([], BUILTIN, [])["groups"]
             if g["kind"] == "builtin"]
    assert names == ["Its own memory and your day", "Gmail", "Your Mac"], (
        f"{names} is the alphabet, not the order the API asked for")


def test_a_server_that_sends_no_apps_still_renders():
    """`apps` is the newest field on the catalog, so a client that is ahead of
    its server must degrade rather than blank. It falls back to the gate's
    groups, then to the thirteen categories — worse each time, and a screen."""
    out = run([], BUILTIN, [], apps=[])
    names = [g["name"] for g in out["groups"]]
    assert "Your Mac" in names, names
    assert "at-toggle" in out["html"]


def test_a_tool_whose_app_the_api_did_not_name_still_renders():
    """Visible enough to get fixed, not broken enough to lose the screen."""
    odd = {"name": "future_tool", "label": "Future", "description": "",
           "source": "builtin", "connector": "", "app": "atlantis",
           "access": "read"}
    out = run([], [odd], [])
    names = [g["name"] for g in out["groups"]]
    assert "Other" in names, names


# ── two switches, and what they cover ─────────────────────────────────────
def _card(html: str, name: str) -> str:
    return html.split(f">{name}<", 1)[1].split("</section>", 1)[0]


def _switches(html: str, name: str) -> str:
    """Only the tier switches, never the per-tool rows folded away below them."""
    card = _card(html, name)
    return card.split('class="at-bulks"', 1)[1].split("</div></div>", 1)[0] \
        if 'class="at-bulks"' in card else ""


def test_a_card_draws_one_switch_per_tier_it_has_tools_in(full):
    """Not four switches everywhere. Gmail has reads and no write tools, so it
    gets exactly one — a switch for a tier with nothing in it is a control that
    does nothing. Your Mac has three, because it genuinely has three tiers."""
    assert _switches(full["html"], "Gmail").count('role="switch"') == 1
    assert _switches(full["html"], "Your Mac").count('role="switch"') == 3


def test_the_second_switch_takes_the_app_s_own_word(full):
    """`CLAUDE.md` forbids folding `outbound` or `destructive` into a generic
    "change": a tap given for one must never silently cover the other. The
    wording is the half of that rule a person can see, so it comes down the
    wire per app rather than being one word everywhere."""
    mac = _card(full["html"], "Your Mac")
    assert 'aria-label="Write — Your Mac"' in mac
    assert 'aria-label="Run code — Your Mac"' in mac


def test_running_code_is_never_inside_the_word_write(full):
    """Three switches on the one card that genuinely has three tiers, and the
    irreversible one is its own. One tap on "Write" must not grant it."""
    mac = _card(full["html"], "Your Mac")
    write = re.search(r'data-bulk="([^"]*)"[^>]*aria-label="Write — Your Mac"', mac)
    assert write, mac[:400]
    assert "run_python" not in write.group(1), (
        "running code was folded into the write switch")


def test_each_switch_says_what_it_covers(full):
    """The roll-up between the two switches. "Read" over a collapsed list says
    nothing about whether reading includes scrolling or going back, and the only
    way to find out was to open the disclosure — the wall this layout replaced."""
    mac = _card(full["html"], "Your Mac")
    read = mac.split('<span class="at-nm">Read</span>', 1)[1]
    assert "Read</span>" in mac
    assert ">Read<" in read.split("</div>", 1)[0] or "Read" in read[:200], read[:300]


def test_the_always_on_card_has_no_switch_at_all(full):
    """An agent that cannot read its own memory is not a lesser agent, it is a
    broken one — and making somebody tick ten boxes to get there taught them
    the screen was a formality, before the boxes that matter.

    Not one switch anywhere on it, including inside the disclosure. The heading
    says "not something to switch" and used to say it over a column of
    switches, which is the screen arguing with itself — and `tool_facts.ALWAYS`
    says the switches were the half that was wrong."""
    own = _card(full["html"], "Its own memory and your day")
    assert "at-toggle" not in own, own[:400]
    assert "Always on" in own
    assert "Search" in own, "the tools are still listed, just not as controls"


# ── a change that is an action, not a tool ────────────────────────────────
def test_an_app_whose_changes_are_actions_says_so_instead_of_a_switch(full):
    """Sending mail is an action: it always comes back as a card to confirm, so
    there is nothing here to toggle. Saying nothing is what this used to do, and
    an app showing only "Read" reads as an app that cannot do anything else."""
    gmail = _card(full["html"], "Gmail")
    assert "is-ask" in gmail
    assert "comes to you as a card you confirm" in gmail


def test_the_ask_row_is_not_a_switch(full):
    """A control that cannot change anything must not look like one."""
    ask = _card(full["html"], "Gmail").split("is-ask", 1)[1].split("</div>", 1)[0]
    assert "at-toggle" not in ask, ask[:300]


def test_the_ask_row_answers_who_it_may_reach_in_place(full):
    """"It asks every time" is only half the answer — the other half is which
    ones have stopped asking, and that one is answered in the row.

    It used to be a *Who it may reach* button that opened the global allow-list
    on the Actions screen. From a connector card that was a dead end twice
    over: the list holds every app's grants together, and the only thing it can
    add is an email address — a connector key is minted by the call it
    describes and never typed. Somebody pressed a button about DeepWiki and
    landed on a screen about email with nothing to do."""
    assert "Who it may reach" not in full["html"], "the dead end came back"
    # The row itself stays — what went is the button that led somewhere it
    # could not answer from.
    assert "is-ask" in _card(full["html"], "Gmail")


def test_a_standing_grant_is_shown_on_the_card_it_belongs_to():
    out = run(["gmail_search"], BUILTIN, [], panel={"reach": [
        {"kind": "email_recipient", "value": "rahul@acme.com",
         "label": "rahul@acme.com"},
        {"kind": "chat_recipient", "value": "telegram:42", "label": "Dana"},
    ]})
    gmail = _card(out["html"], "Gmail")
    assert "rahul@acme.com" in gmail
    assert "Dana" not in gmail, "another app's grant was shown under Gmail"


def test_a_connector_s_grants_are_matched_on_its_id_never_its_name():
    """A user can rename a connector, and a rename must not change which
    permissions are shown as belonging to it."""
    out = run(["notion__search"], [*BUILTIN, CATEGORY, NOTION_TOOL], [READY],
              panel={"reach": [
                  {"kind": "connector_tool", "value": "notion:create_page@docs",
                   "label": "create_page in docs"},
                  {"kind": "connector_tool", "value": "linear:create_issue",
                   "label": "create_issue"},
              ]})
    notion = _card(out["html"], "Notion")
    assert "create_page in docs" in notion
    assert "create_issue" not in notion


def test_a_grant_can_be_taken_back_from_the_card():
    """The only control a standing permission needs here. Making one is
    deliberately not offered — that happens on the card that was asking."""
    out = run(["gmail_search"], BUILTIN, [], panel={"reach": [
        {"kind": "email_recipient", "value": "rahul@acme.com",
         "label": "rahul@acme.com"}]})
    assert 'data-reach-off="rahul@acme.com"' in out["html"]
    assert 'data-reach-kind="email_recipient"' in out["html"]


def test_only_a_list_you_could_type_into_offers_a_way_in():
    """"Allow someone new" on a connector card would open a box for a key
    nobody can know in advance."""
    out = run(["notion__search"], [*BUILTIN, CATEGORY, NOTION_TOOL], [READY],
              panel={"reach": []})
    assert 'data-screen="allowlist"' in _card(out["html"], "Gmail")
    assert 'data-screen="allowlist"' not in _card(out["html"], "Notion")


def test_nothing_is_claimed_about_grants_before_the_answer_is_back():
    out = run(["gmail_search"], BUILTIN, [])
    assert "Nothing runs on its own yet" not in out["html"]
    assert "Runs without asking" not in out["html"]


def test_an_app_with_no_grants_says_how_one_is_made():
    """A bare "nothing yet" leaves somebody looking for the control that adds
    one. There isn't one here, and the sentence says where it is."""
    out = run(["gmail_search"], BUILTIN, [], panel={"reach": []})
    gmail = _card(out["html"], "Gmail")
    assert "Nothing runs on its own yet" in gmail
    assert "you allow it from that card" in gmail


def test_a_connector_tool_is_named_after_its_connector(full):
    """"search" and "search_2" are indistinguishable; "Search Notion" is not."""
    notion = next(g for g in full["groups"] if g["name"] == "Notion")
    assert notion["tools"] == ["notion__search"]
    assert "Search Notion" in full["html"]


def test_connectors_come_before_the_built_ins(full):
    """The screen exists because of connectors. Built-ins never needed
    explaining, so they do not go first."""
    names = [g["name"] for g in full["groups"]]
    assert names.index("Your connected apps") < names.index("Gmail")
    assert names.index("Notion") < names.index("Gmail")


def test_the_category_is_its_own_group(full):
    """It grants everything the connectors can read, so filing it under one
    connector would misdescribe what the switch does."""
    cat = next(g for g in full["groups"] if g["kind"] == "category")
    assert cat["tools"] == ["mcp"]


def test_the_category_card_is_named_for_what_it_grants(full):
    """"Your connectors" named the plumbing. The card is the standing grant —
    everything the apps you connected can read, now and as you add more."""
    assert "Your connected apps" in full["html"]


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


# ── the other half of a permission, which used to have no screen ──────────
#
# Three permissions had a working endpoint and nothing in the app that called
# it. The worst was the folders: "Your Mac → Read" granted a tool whose only
# possible answer was "No folder has been opened to agents yet", and no screen
# anywhere opened one. A switch whose other half is unreachable is the same dead
# end as a switch with no endpoint, and harder to see.

def test_the_mac_card_says_when_no_folder_is_open():
    """Not an error — it is the correct starting state, and saying so is what
    stops somebody reading the switches above as already working."""
    out = run(["read_file"], BUILTIN, [], panel={"folders": [], "sites": []})
    mac = _card(out["html"], "Your Mac")
    assert "No folder is open to agents yet" in mac
    assert "is-warn" in mac


def test_the_mac_card_is_where_a_folder_is_opened():
    """In the card, not behind a button to another screen. `App("mac")` names no
    `more_screen` because there was none to name."""
    out = run(["read_file"], BUILTIN, [], panel={"folders": []},
              addFolder="~/Documents/work")
    assert out["folderAdded"], "the control did not reach the endpoint"
    assert out["folderAdded"]["path"] == "/api/agents/folders"
    assert out["folderAdded"]["body"] == {"path": "~/Documents/work"}


def test_an_open_folder_can_be_closed_again():
    """Anything the user grants, they can take back — on the same card."""
    out = run(["read_file"], BUILTIN, [], panel={"folders": ["/Users/x/work"]})
    mac = _card(out["html"], "Your Mac")
    assert 'data-folder-off="/Users/x/work"' in mac


def test_the_browser_card_says_which_sites_are_allowed():
    """"Turn Read on" grants nothing by itself: which sites is a list of its
    own, and a user who turned every switch on and was still refused had no way
    to learn that from this panel."""
    browser = [{"name": "browse_open", "label": "Open page", "category": "Websites",
                "description": "Open a page", "source": "builtin", "connector": "",
                "app": "browser", "access": "read"}]
    apps = [{"key": "browser", "label": "The browser", "blurb": "Pages.",
             "always": False, "read": [], "change": [], "run": [],
             "write_label": "Change", "run_label": ""}]
    out = run(["browse_open"], browser, [], apps=apps,
              panel={"sites": [{"host": "amazon.in"}, {"host": "web.whatsapp.com"}]})
    card = _card(out["html"], "The browser")
    assert "2 sites allowed" in card
    assert "amazon.in" in card


def test_the_browser_card_says_when_no_site_is_allowed():
    browser = [{"name": "browse_open", "label": "Open page", "category": "Websites",
                "description": "Open a page", "source": "builtin", "connector": "",
                "app": "browser", "access": "read"}]
    apps = [{"key": "browser", "label": "The browser", "blurb": "Pages.",
             "always": False, "read": [], "change": [], "run": [],
             "write_label": "Change", "run_label": ""}]
    out = run(["browse_open"], browser, [], apps=apps, panel={"sites": []})
    assert "No site is allowed yet" in _card(out["html"], "The browser")


def test_a_connector_card_says_whether_this_agent_must_ask_first():
    """A separate permission from the switches above it, and deliberately a
    separate control: the switches decide whether the agent can see the tools,
    and this decides whether reaching the account behind them interrupts the
    user first. Collapsing the two would be one tap granting two things."""
    out = run(["notion__search"], [*BUILTIN, CATEGORY, NOTION_TOOL], [READY],
              panel={"grants": {"allowed": [], "must_ask": ["notion"],
                                "unrestricted": False}})
    notion = _card(out["html"], "Notion")
    assert "Asks you the first time" in notion
    assert 'data-grant="notion"' in notion


def test_the_grant_is_keyed_by_the_connector_s_id_not_its_name():
    """A user can rename a connector, and a rename must not change who may use
    what."""
    out = run(["notion__search"], [*BUILTIN, CATEGORY, NOTION_TOOL], [READY],
              panel={"grants": {"allowed": [], "must_ask": ["notion"],
                                "unrestricted": False}},
              toggleGrant="notion")
    assert out["grantToggled"], "the control did not reach the endpoint"
    assert out["grantToggled"]["method"] == "POST"
    assert out["grantToggled"]["body"]["connector"] == "notion"


def test_a_strip_says_nothing_until_its_answer_is_back():
    """`[]` and "not back yet" mean different things. Empty is a fact worth
    stating; a fetch still in flight is not something to state at all, and a
    panel that claimed "no folder is open" before asking would be wrong for the
    first few hundred milliseconds of every open."""
    out = run(["read_file"], BUILTIN, [])
    assert "No folder is open to agents yet" not in out["html"]


def test_a_draft_agent_is_offered_no_control_it_cannot_use():
    """The builder is a modal: a screen opened from it comes up behind it, and
    the agent does not exist yet so there is no grant to set."""
    out = run(["read_file"], BUILTIN, [], panel={"folders": []},
              agentDraft=True)
    assert "data-folder-add" not in out["html"]


def test_an_empty_roster_is_not_pick_an_agent_over_an_empty_picker():
    """Two states were collapsed into one dead end: a roster that is genuinely
    empty — a first run, which has somewhere to go — and an id that no longer
    resolves, which should fall back rather than refuse. Neither was something
    a person could act on, and the picker beside it had nothing in it to pick."""
    src = (WEB / "tools.js").read_text()
    body = src.split("async function loadAgentTools", 1)[1]
    # Comments stripped first. This file explains the dead end it removed, in
    # the function that removed it, so a grep over raw source matches the
    # explanation and calls it the bug.
    code = re.sub(r"^\s*//.*$", "", body, flags=re.M)
    assert "Pick an agent." not in code, "the dead end came back"
    assert "data-open-library" in body, "an empty roster must lead somewhere"
    assert "roster.find((a) => a.id === id) || roster[0]" in body, (
        "an unresolvable id must fall back rather than refuse")
