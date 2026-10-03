"""Sixty-four switches became one card per app, executed against the real render.

The panel asked "which tool is this?" sixty-four times, in thirteen categories,
before the user had sent the agent a single message — the moment they know least
about what it will need. That became four cards by **reach**: what stays on this
machine, what touches an account, a website, your files. Better, and still the
wrong question — it is the *gate's* question. It put Gmail, the calendar,
Telegram, Notion and Linear on one card called "Your connected accounts", so a
user who wanted to say *read GitHub, leave my mail alone* had no control that
said it.

The card is the **app** now, and on it are **Read** and the one word that app's
changes actually are. Both axes are derived server-side from the capability each
tool declares and sent down on the row; this screen renders them and decides
nothing.

Executed rather than asserted on source order. `node --check` passes on the
temporal-dead-zone `ReferenceError` that once blanked this whole panel, and
`tests/js/CLAUDE.md`'s rule is the one that found it: if you add a render path
or a click handler, run it.
"""
from __future__ import annotations

import pytest
from test_frontend_agent_tools import run

#: Rows as `describe_tools()` sends them — `app` and `access` beside the label
#: and category they replaced as the thing the panel arranges by. `group` rides
#: along because it is still the gate's axis and the panel's fallback.
GROUPED = [
    {"name": "search_brain", "label": "Search", "category": "Memory",
     "description": "Search your brain.", "source": "builtin", "connector": "",
     "group": "on_device", "app": "on_device", "access": "read"},
    {"name": "remember", "label": "Remember", "category": "Memory",
     "description": "Remember something.", "source": "builtin", "connector": "",
     "group": "on_device", "app": "on_device", "access": "write"},
    {"name": "browse_open", "label": "Open page", "category": "Websites you allow",
     "description": "Open a page.", "source": "builtin", "connector": "",
     "group": "websites", "app": "browser", "access": "read"},
    {"name": "browse_read", "label": "Re-read", "category": "Websites you allow",
     "description": "Read it again.", "source": "builtin", "connector": "",
     "group": "websites", "app": "browser", "access": "read"},
    {"name": "browse_click", "label": "Click", "category": "Websites you allow",
     "description": "Click something.", "source": "builtin", "connector": "",
     "group": "websites", "app": "browser", "access": "write"},
    {"name": "run_python", "label": "Run", "category": "Your Mac",
     "description": "Run Python.", "source": "builtin", "connector": "",
     "group": "mac", "app": "mac", "access": "destructive"},
]

#: What each card IS, in the server's words. The panel renders these and never
#: writes them, for the reason every other label on that screen comes down the
#: wire — including `write_label` and `run_label`, the words on the second and
#: third switches.
APPS = [
    {"key": "on_device", "label": "Its own memory and your day", "always": True,
     "blurb": "Nothing here leaves this machine.",
     "read": ["search_brain"], "change": ["remember"], "run": [],
     "write_label": "Write", "run_label": ""},
    {"key": "browser", "label": "The browser", "always": False,
     "blurb": "Pages on sites you have allowed.",
     "read": ["browse_open", "browse_read"], "change": ["browse_click"], "run": [],
     "write_label": "Change", "run_label": ""},
    {"key": "mac", "label": "Your Mac", "always": False,
     "blurb": "Files and folders on this computer.",
     "read": [], "change": [], "run": ["run_python"],
     "write_label": "Write", "run_label": "Run code"},
]

#: The gate's axis, kept so the fallback path can be exercised.
SPECS = [
    {"key": "on_device", "label": "Its own memory and your day", "always": True,
     "blurb": "Nothing here leaves this machine.",
     "read": ["search_brain"], "change": ["remember"], "run": []},
    {"key": "websites", "label": "Websites", "always": False,
     "blurb": "Pages on sites you have allowed.",
     "read": ["browse_open", "browse_read"], "change": ["browse_click"], "run": []},
    {"key": "mac", "label": "Your Mac", "always": False,
     "blurb": "Files and folders on this computer.",
     "read": [], "change": [], "run": ["run_python"]},
]


@pytest.fixture(scope="module")
def tiered() -> dict:
    return run(["search_brain", "remember"], GROUPED, [], apps=APPS, groups=SPECS)


def test_built_ins_are_grouped_by_the_app_they_belong_to(tiered):
    """Not by the thirteen subject headings, and not by how far they reach.
    "The browser" is a thing a person has an opinion about; "websites" as a
    reach class is a thing the gate has an opinion about."""
    builtin = [g["name"] for g in tiered["groups"] if g["kind"] == "builtin"]

    assert builtin == ["Its own memory and your day", "The browser", "Your Mac"]


def test_a_card_says_what_it_is(tiered):
    """Sixty-four switches had sixty-four descriptions and no answer at all to
    "what am I deciding?"."""
    assert "Nothing here leaves this machine." in tiered["html"]
    assert "Pages on sites you have allowed." in tiered["html"]


def test_the_card_that_never_leaves_the_machine_is_not_a_switch(tiered):
    """An agent that cannot read its own memory is not a safer agent, it is a
    broken one — and ten boxes to get there teaches somebody the screen is a
    formality before they reach the boxes that matter."""
    assert "Always on" in tiered["html"]
    assert 'data-bulk="search_brain"' not in tiered["html"]
    assert 'data-tool="search_brain"' not in tiered["html"], (
        "the card says it is not something to switch, over a switch")


def test_reading_and_changing_a_website_are_separate_switches(tiered):
    """A single "allow websites" switch would mean allowing an agent to type
    into a site in order to let it read one."""
    assert 'data-bulk="browse_open browse_read"' in tiered["html"]
    assert 'data-bulk="browse_click"' in tiered["html"]


def test_the_second_switch_wears_the_app_s_own_word(tiered):
    """"Change" on a web page, "Write" on a file. One generic word across every
    card is a switch that was set for one app and granted another."""
    assert 'aria-label="Change — The browser"' in tiered["html"]
    assert 'aria-label="Run code — Your Mac"' in tiered["html"]


def test_running_code_is_offered_as_its_own_thing(tiered):
    """It has no inverse, so it must never ride along on "let it change
    things"."""
    assert 'data-bulk="run_python"' in tiered["html"]
    assert 'aria-label="Run code — Your Mac"' in tiered["html"]


def test_each_switch_says_what_it_covers(tiered):
    """The roll-up between the switches: a switch labelled "Read" over a
    collapsed list says nothing about what reading includes, and the only way
    to find out was to open the disclosure."""
    reading = tiered["html"].split('data-bucket="read"', 1)[1].split("</div>", 1)[0]
    assert "Open page" in reading and "Re-read" in reading, reading[:400]


def test_the_per_tool_rows_are_still_there_behind_a_disclosure(tiered):
    """The complaint was that per-tool control was the ONLY thing on offer, not
    that it should not exist."""
    assert 'data-tool="browse_click"' in tiered["html"]
    assert "Show all" in tiered["html"]


def test_one_switch_turns_on_every_tool_in_its_tier():
    """The whole point of the compaction."""
    out = run(["search_brain", "remember"], GROUPED, [], apps=APPS,
              bulk="browse_open browse_read")

    assert out["bulked"], "the bulk switch never ran"
    assert set(out["bulked"]["sent"]) >= {"browse_open", "browse_read"}


def test_a_card_switch_sends_exactly_one_request():
    """Looping over the per-tool toggle would fire one PATCH per tool, each
    sending the whole list — and whichever replied last would win, so turning a
    tier on could land as a tier half on, depending on the network."""
    out = run(["search_brain"], GROUPED, [], apps=APPS,
              bulk="browse_open browse_read")

    assert out["bulked"]["patches"] == 1


def test_turning_a_tier_off_removes_all_of_it_and_nothing_else():
    out = run(["search_brain", "browse_open", "browse_read"], GROUPED, [],
              apps=APPS, bulk="browse_open browse_read")

    assert "browse_open" not in out["bulked"]["sent"]
    assert "browse_read" not in out["bulked"]["sent"]
    assert "search_brain" in out["bulked"]["sent"], "it took an unrelated tool"


def test_a_partly_granted_tier_says_so_rather_than_lying():
    """Somebody who used the per-tool rows leaves a tier half on. A switch
    showing a plain "off" over two live tools would be the screen lying about
    what the agent can do."""
    out = run(["search_brain", "browse_open"], GROUPED, [], apps=APPS)

    assert "1 of 2 on" in out["html"]


def test_an_older_server_without_apps_falls_back_to_the_gate_s_groups():
    """`apps` is additive, and a client ahead of its server must degrade rather
    than blank. The reach classes are worse headings and they are headings."""
    out = run(["search_brain"], GROUPED, [], apps=[], groups=SPECS)

    names = [g["name"] for g in out["groups"] if g["kind"] == "builtin"]
    assert names == ["Its own memory and your day", "Websites", "Your Mac"]
    assert 'data-bulk="browse_open browse_read"' in out["html"]


def test_an_older_server_with_neither_still_renders():
    """Down to the thirteen categories — worse again, and a working screen. A
    panel that went blank because a field was absent would be a worse failure
    than the one being fixed."""
    out = run(["search_brain"], GROUPED, [], apps=[], groups=[])

    assert out["html"].strip(), "the panel went blank"
    assert 'data-tool="search_brain"' in out["html"]


# ── where the other half of a permission is set ──────────────────────────
#
# "there is no option where i can give access to browser in agent and tools
# section" — there was, under a heading that said "Websites", and flipping it
# still would not have worked: WHICH sites an agent may touch is a list kept on
# the Connectors screen, and this panel never said so. A user who turned every
# switch here on and was still refused had nowhere to go next.
LINKED = [
    {**APPS[0]},
    {**APPS[1], "label": "Websites and the browser",
     "more": {"screen": "connectors", "label": "Choose which sites"}},
    {**APPS[2]},
]


@pytest.fixture(scope="module")
def linked() -> dict:
    return run(["search_brain", "remember"], GROUPED, [], apps=LINKED)


def test_a_card_says_where_the_rest_of_its_permission_is_set(linked):
    """A switch here grants the agent nothing on its own."""
    assert 'data-screen="connectors"' in linked["html"]
    assert "Choose which sites" in linked["html"]


def test_that_button_opens_the_screen_it_names(linked):
    """A control that goes nowhere is the dead end this closes, not a new one."""
    assert "connectors" in linked["screens"]


def test_a_card_with_nowhere_else_to_go_draws_no_such_button(linked):
    """"Your Mac" has no second screen — its folders are opened on the card
    itself — and a button that led nowhere would teach that these buttons lead
    nowhere."""
    mac = linked["html"].split(">Your Mac<", 1)[1].split("</section>", 1)[0]
    assert "data-screen" not in mac, mac[:400]


def test_an_older_server_that_names_no_screen_still_renders():
    out = run(["search_brain"], GROUPED, [], apps=APPS)

    assert out["html"].strip()
    assert 'data-screen="connectors"' not in out["html"]


# ── the screen must not argue with itself ────────────────────────────────
def test_turning_one_tool_on_moves_the_switch_above_it():
    """The other direction was handled from the start — a tier switch patches
    the rows under it — and this one was not, so a tool switched on inside the
    disclosure left the switch over it reading a plain "off"."""
    out = run(["search_brain", "remember"], GROUPED, [], apps=APPS,
              toggle="browse_open")

    reading = next(g for g in out["groupAfter"]
                   if g["bulk"] == "browse_open browse_read")
    assert reading["some"] is True, reading
    assert reading["part"] == "1 of 2 on", reading


def test_turning_the_last_tool_of_a_tier_on_fills_its_switch():
    out = run(["search_brain", "browse_open"], GROUPED, [], apps=APPS,
              toggle="browse_read")

    reading = next(g for g in out["groupAfter"]
                   if g["bulk"] == "browse_open browse_read")
    assert reading["on"] is True
    assert reading["part"] == "", "it is whole, so there is nothing to count"


# ── a repaint must not undo what the user opened ─────────────────────────
def test_a_disclosure_the_user_opened_survives_a_repaint():
    """Every permission that lands repaints the whole panel, and every list
    somebody had opened to decide with closed under them."""
    out = run(["search_brain"], GROUPED, [], apps=APPS, openGroup="The browser")

    assert out["reopened"] is True


# ── the row that is not a switch ─────────────────────────────────────────
def test_an_app_whose_changes_are_actions_draws_a_row_and_not_a_switch():
    """Sending mail is an action: it always comes back as a card to confirm, so
    there is nothing here to toggle. An app showing only "Read" reads as an app
    that cannot do anything else, which is false."""
    gmail = [{"key": "gmail", "label": "Gmail", "always": False,
              "blurb": "Reading the mail.",
              "read": ["gmail_search"], "change": [], "run": [],
              "write_label": "Write", "run_label": "",
              "ask": {"label": "Send",
                      "blurb": "Sending mail always comes to you as a card."}}]
    rows = [{"name": "gmail_search", "label": "Search", "category": "Email",
             "description": "Search the mail.", "source": "builtin",
             "connector": "", "app": "gmail", "access": "read"}]
    out = run(["gmail_search"], rows, [], apps=gmail)

    assert "is-ask" in out["html"]
    assert "Sending mail always comes to you as a card." in out["html"]
    ask = out["html"].split("is-ask", 1)[1].split("</div>", 1)[0]
    assert "at-toggle" not in ask, ask[:300]


def test_there_is_no_preset_row_and_no_allow_all():
    """Three buttons above the list and a bulk button per card made sense when a
    card was four switches over a wall of sixty-four. A card is two switches
    now, so the preset row was a second way to do a one-press thing — and
    "Allow everything" silently included running code on this Mac."""
    out = run(["search_brain"], GROUPED, [], apps=APPS)

    assert "data-preset" not in out["html"]
    assert "Allow all" not in out["html"]
    assert "Allow everything" not in out["html"]
