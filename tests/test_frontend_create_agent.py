"""Create-an-agent shows what the agent will be able to do, legibly.

This modal shipped unreadable. Two shared selectors reached into it:

* `.am-body input` was in the rule that styles the form fields — and a tool
  row's checkbox IS an input inside `.am-body`, so all thirty-odd of them
  rendered `width:100%` with the field's padding, and each label was squeezed
  down to its first letter;
* `.am-body label` matched the rows too, so every tool name came out uppercase
  and grey, sitting in the gutter beside a box.

The fix is not only CSS. A flat list of thirty-two checkboxes is a wall
whatever it looks like, so the rows now use the grouping the Agents & tools
panel already builds — `agentToolGroups` — which means building an agent and
editing one afterwards are the same list, not two designs of it.

`workspace.js` loads BEFORE `tools.js` and this modal calls four functions
tools.js declares. That works only because nothing calls it until every script
has run, which is a claim about runtime, so the modal is opened for real here.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"
INDEX = (WEB / "index.html").read_text()
CSS = (WEB / "styles.css").read_text()

BUILTIN = [
    {"name": "search_brain", "label": "Search", "category": "Memory",
     "description": "Search everything you have saved. It looks everywhere.",
     "source": "builtin", "connector": ""},
    {"name": "remember", "label": "Remember", "category": "Memory",
     "description": "Save a fact for later.", "source": "builtin", "connector": ""},
    {"name": "add_task", "label": "Add", "category": "Tasks",
     "description": "Add a task.", "source": "builtin", "connector": ""},
    {"name": "run_python", "label": "Run", "category": "Your Mac",
     "description": "Run Python on this Mac.", "source": "builtin", "connector": ""},
]
NOTION = {"name": "notion__search", "label": "Search Notion",
          "description": "Search pages in the workspace.",
          "source": "mcp", "connector": "Notion"}
LINEAR = {"name": "linear__issues", "label": "Issues",
          "description": "List issues assigned to you.",
          "source": "mcp", "connector": "Linear"}

READY = {"name": "notion", "label": "Notion", "ready": True, "reason": "", "mcp": True}
DOWN = {"name": "linear", "label": "Linear", "ready": False,
        "reason": "Linear needs signing in", "mcp": True}

CATEGORIES = ["Memory", "Tasks", "Your Mac"]


def run(**over) -> dict:
    payload = {"tools": [*BUILTIN, NOTION, LINEAR], "categories": CATEGORIES,
               "connectors": [READY, DOWN], **over}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/create_agent.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2500:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


@pytest.fixture(scope="module")
def opened() -> dict:
    return run()


def _group(out: dict, name: str) -> dict:
    return next(g for g in out["groups"] if g["name"] == name)


# ── it opens, and it opens across the load-order boundary ─────────────────
def test_the_modal_opens_and_draws_its_tools(opened):
    """`workspace.js` calls `agentToolGroups`, `toolBlockedReason`, `toolLabel`
    and `toolBlurb` — all declared in a file that loads after it."""
    assert opened["opened"] is True
    assert opened["groups"], "the tool list rendered nothing"


# ── the wall becomes a list ───────────────────────────────────────────────
def test_tools_are_grouped_by_where_they_come_from(opened):
    assert [g["name"] for g in opened["groups"]] == [
        "Notion", "Linear", "Memory", "Tasks", "Your Mac"]


def test_connectors_come_before_the_built_ins(opened):
    names = [g["name"] for g in opened["groups"]]
    assert names.index("Notion") < names.index("Memory")


def test_built_ins_follow_the_order_the_api_named(opened):
    """"Your Mac" belongs last whatever letter it starts with, and the layer
    that owns the categories already made that judgement."""
    names = [g["name"] for g in opened["groups"]]
    assert [n for n in names if n in CATEGORIES] == CATEGORIES


def test_every_tool_is_named_in_full(opened):
    """The regression: a label squeezed to its first letter."""
    assert _group(opened, "Notion")["tools"] == ["Search Notion"]
    assert _group(opened, "Memory")["tools"] == ["Search", "Remember"]


def test_a_row_says_what_the_tool_does(opened):
    assert "Save a fact for later." in opened["html"]


# ── never a control that cannot work ──────────────────────────────────────
def test_an_unreachable_connector_gets_a_reason_not_a_switch(opened):
    linear = _group(opened, "Linear")
    assert linear["switches"] == [], "a switch was offered for a connector that is down"
    assert linear["blocked"] == ["Linear needs signing in"]


def test_a_healthy_connector_does_get_a_switch(opened):
    assert _group(opened, "Notion")["switches"] == ["notion__search"]


# ── the switch is the app's, not the system's ─────────────────────────────
def test_the_control_is_a_switch_element_not_a_checkbox(opened):
    """A native checkbox paints itself with the SYSTEM accent — the blue that
    had no business on this screen and could never be made gold."""
    assert 'role="switch"' in opened["html"]
    assert "type=\"checkbox\"" not in opened["html"]


def test_the_switch_reports_its_state_to_assistive_tech(opened):
    assert 'aria-checked="true"' in opened["html"] or 'aria-checked="false"' in opened["html"]


def test_the_switch_is_painted_in_the_one_accent():
    """The builder draws the Agents & tools switch itself now — one renderer, so
    the accent cannot be right on one screen and wrong on the other."""
    rule = CSS.split(".at-toggle.is-on", 1)[1].split("}", 1)[0]
    assert "--north" in rule, "the switch is not in the app's accent"


def test_the_builder_has_no_second_set_of_tool_row_classes():
    """The regression this unification exists for: two renderers for one list,
    and the builder's copy two releases behind — no presets, no group
    sentences, no "allow all", thirteen category headings instead of four
    groups. A dead rule is the one the next person copies."""
    rules = re.sub(r"/\*[\s\S]*?\*/", "", CSS)     # the comments say why they went
    for gone in (".am-toggle", ".am-group-nm", ".am-card", ".am-nm", ".am-ds",
                 ".am-blocked", ".am-knob"):
        assert not re.search(rf"{re.escape(gone)}\b(?![-\w])", rules), \
            f"{gone} survived the unification"


# ── what it will be able to do, before it exists ──────────────────────────
def test_it_says_how_many_are_on(opened):
    assert re.fullmatch(r"\d+ of \d+ on", opened["count"]), opened["count"]


def test_flipping_a_switch_changes_the_count_and_what_is_created():
    out = run(toggle="remember")
    assert out["count"] == "1 of 5 on"
    assert out["posted"]["tools"] == ["search_brain"], out["posted"]


def test_create_sends_the_form_and_the_switches(opened):
    assert opened["posted"]["name"] == "Sales"
    assert opened["posted"]["role"] == "outreach"
    assert opened["posted"]["system_prompt"] == "be helpful"


# ── the selectors that caused it ──────────────────────────────────────────
def test_the_field_rule_cannot_reach_a_tool_row():
    """`.am-body input` matched every checkbox in the tool list. Scoped to the
    direct children it was written for."""
    rule = CSS.split("#rmName, #rmInterval", 1)[1].split("{", 1)[0]
    assert ".am-body > input" in rule
    assert re.search(r"(?<!> )\.am-body input\b", rule) is None


def test_no_rule_styles_every_label_in_the_body():
    """`.am-body label` uppercased the tool rows, which are labels too."""
    assert re.search(r"^\.am-body label\s*\{", CSS, re.M) is None
    assert ".am-label" in CSS


def test_no_label_in_the_automation_modal_is_left_unstyled():
    """The automation modal shares `.am-body`. When the blanket rule went, its
    labels went with it unless they carry a class of their own.

    The question is "is any label here unstyled?", not "do they all carry the
    same class". They deliberately do not: `.am-label` is a section heading and
    `.am-sub` is the small caption on a field inside a step, and collapsing
    them would mean "At" and "Name" being set in the same size.

    Checked against the stylesheet rather than against a list of class names,
    so a label given a class nobody ever wrote a rule for still fails.
    """
    import re

    body = INDEX.split('id="routineModal"', 1)[1].split('id="agentModal"', 1)[0]
    assert "<label>" not in body, "a bare label is left with no style"

    labels = re.findall(r'<label([^>]*)>', body)
    for attrs in labels:
        classes = re.search(r'class="([^"]*)"', attrs)
        assert classes, f"a label with no class: <label{attrs}>"
        for name in classes.group(1).split():
            assert f".{name}" in CSS, f"nothing styles .{name}"


def test_the_fields_cannot_be_squeezed_by_a_long_tool_list():
    """`.am-body` is a scrolling flex column, so without this the textarea
    shrank until its own placeholder was clipped in half.

    The selector appears twice — once in the shared field rule and once here —
    so every block that carries it is checked rather than the first one found.
    """
    blocks = [b.split("}", 1)[0]
              for b in CSS.split(".am-body > input, .am-body > textarea")[1:]]
    assert any("flex: none" in b for b in blocks), blocks


# ── one renderer, so the builder is the panel ─────────────────────────────
#
# "add a option to select all permission using one button while creating agent"
# — the presets and the per-group "Allow all" had shipped on Agents & tools and
# the builder never got them, because it was a SECOND renderer for the same
# list. It is the same one now, so this is the panel's behaviour executed
# through the modal: if it works there it works here, and neither can drift.
SPECS = [
    {"key": "on_device", "label": "Its own memory and your day", "always": True,
     "blurb": "Nothing here leaves this machine.",
     "read": ["search_brain", "who_is"], "change": ["remember"], "run": []},
    {"key": "mac", "label": "Your Mac", "always": False,
     "blurb": "Files and folders on this computer.",
     "read": [], "change": ["add_task"], "run": ["run_python"]},
]
#: `group`, `app` and `access` on the row, the way `describe_tools()` sends
#: them: the gate's axis, the screen's, and the tier.
#:
#: `who_is` is on this machine and is deliberately NOT one of the three names
#: the builder used to hand out: the card it belongs to is the one the panel
#: calls always on, and that is the claim the default has to keep.
WHO_IS = {"name": "who_is", "label": "People", "category": "Memory",
          "description": "Who someone is.", "source": "builtin", "connector": "",
          "group": "on_device", "app": "on_device", "access": "read"}
TIERED = [
    {**BUILTIN[0], "group": "on_device", "app": "on_device", "access": "read"},
    {**BUILTIN[1], "group": "on_device", "app": "on_device", "access": "write"},
    WHO_IS,
    {**BUILTIN[2], "group": "mac", "app": "mac", "access": "write"},
    {**BUILTIN[3], "group": "mac", "app": "mac", "access": "destructive"},
]
#: The cards, as `permission_apps()` sends them. Same shape the panel gets, and
#: the point of the fixture: the builder and the panel are one renderer.
APPS = [
    {"key": "on_device", "label": "Its own memory and your day", "always": True,
     "blurb": "Nothing here leaves this machine.",
     "read": ["search_brain", "who_is"], "change": ["remember"], "run": [],
     "write_label": "Write", "run_label": ""},
    {"key": "mac", "label": "Your Mac", "always": False,
     "blurb": "Files and folders on this computer.",
     "read": [], "change": ["add_task"], "run": ["run_python"],
     "write_label": "Write", "run_label": "Run code"},
]


def built(**over) -> dict:
    return run(tools=[*TIERED, NOTION], connectors=[READY],
               groups=SPECS, apps=APPS, **over)


@pytest.fixture(scope="module")
def builder() -> dict:
    return built()


def test_the_builder_says_what_each_card_is(builder):
    """The sentence the panel has had for two releases."""
    assert "Files and folders on this computer." in builder["html"]


def test_the_builder_draws_the_same_cards_as_the_panel(builder):
    """One renderer, so a card cannot land on one screen and not the other. It
    had not: the builder was still on the thirteen category headings two
    releases after the panel had moved on."""
    assert 'aria-label="Run code — Your Mac"' in builder["html"]
    assert 'aria-label="Write — Your Mac"' in builder["html"]


def test_a_tier_switch_in_the_builder_changes_the_draft_and_sends_nothing():
    """Nothing is saved until Create, so a press moves the object on screen —
    a PATCH here would be editing an agent that does not exist."""
    out = built(bulk="run_python")

    assert out["pressedBulk"] is True
    assert "run_python" in out["posted"]["tools"], out["posted"]
    assert not [c for c in out["calls"] if "PATCH" in str(c)], out["calls"]


def test_there_is_no_preset_row_in_the_builder(builder):
    """It went from both surfaces at once. A card is two switches now, so the
    row was a second way to do a one-press thing — and "Allow everything"
    silently included running code on this Mac."""
    assert "data-preset" not in builder["html"]
    assert "Allow everything" not in builder["html"]
    assert "Allow all" not in builder["html"]


def test_create_sends_the_tools_and_nothing_else(builder):
    """The `preset` field went with the row — the server resolves nothing now,
    so a key on the wire would be a field nobody reads."""
    assert "preset" not in builder["posted"], builder["posted"]
    assert isinstance(builder["posted"]["tools"], list)


def test_a_new_agent_gets_everything_that_cannot_leave_this_machine():
    """The panel tells the user that group is ALWAYS ON. A builder granting
    three of its ten made that sentence false — an agent created here could not
    say who somebody was while the screen promised it always could."""
    out = built()

    assert "who_is" in out["posted"]["tools"], out["posted"]["tools"]


def test_the_card_that_is_always_on_is_not_a_switch_here_either(builder):
    assert "Always on" in builder["html"]


def test_the_builder_offers_no_button_that_opens_a_screen_behind_it():
    """It is a modal. The Connectors screen would come up *behind* it, so the
    button reads as doing nothing — and closing the modal to reach it would
    throw away a half-typed form. The reason still names the route in words,
    which is the part that works from either screen."""
    out = run(tools=[*TIERED, NOTION], connectors=[READY, DOWN],
              groups=SPECS, apps=APPS)

    assert "Open Connectors" not in out["html"]
    assert "data-screen" not in out["html"]
    assert "Linear needs signing in" in out["html"], "it still says what is wrong"
