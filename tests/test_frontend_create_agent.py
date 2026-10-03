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
#: `group` and `access` on the row, the way `describe_tools()` sends them.
#:
#: `who_is` is on this machine and is deliberately NOT one of the three names
#: the builder used to hand out: the group it belongs to is the one the panel
#: calls always on, and that is the claim the default has to keep.
WHO_IS = {"name": "who_is", "label": "People", "category": "Memory",
          "description": "Who someone is.", "source": "builtin", "connector": "",
          "group": "on_device", "access": "read"}
TIERED = [
    {**BUILTIN[0], "group": "on_device", "access": "read"},
    {**BUILTIN[1], "group": "on_device", "access": "write"},
    WHO_IS,
    {**BUILTIN[2], "group": "mac", "access": "write"},
    {**BUILTIN[3], "group": "mac", "access": "destructive"},
]
#: What each preset covers, as the route sends it — a PREVIEW, so a draft can
#: show what the button will do before there is an agent to ask about.
PRESETS = [
    {"key": "all", "label": "Allow everything",
     "blurb": "Every group, including running code on this Mac.",
     "tools": ["search_brain", "remember", "who_is", "add_task", "run_python"]},
    {"key": "read", "label": "Read only",
     "blurb": "It can look at everything and change nothing.",
     "tools": ["search_brain", "remember", "who_is"]},
    {"key": "none", "label": "Nothing yet",
     "blurb": "Only its own memory and your day.",
     "tools": ["search_brain", "remember", "who_is"]},
]


def built(**over) -> dict:
    return run(tools=[*TIERED, NOTION], connectors=[READY],
               groups=SPECS, presets=PRESETS, **over)


@pytest.fixture(scope="module")
def builder() -> dict:
    return built()


def test_the_builder_offers_the_presets(builder):
    """It compacted nothing and still made somebody set every switch, at the
    one moment they know least about what the agent will need."""
    for key in ("all", "read", "none"):
        assert f'data-preset="{key}"' in builder["html"]


def test_the_builder_says_what_each_group_is(builder):
    """The sentence the panel has had for two releases."""
    assert "Files and folders on this computer." in builder["html"]


def test_the_builder_offers_allow_all_per_group(builder):
    assert "Allow all" in builder["html"]


def test_one_press_turns_everything_on():
    """The ask. One button, every permission."""
    out = built(preset="all")

    assert out["pressedPreset"] is True
    on = set(re.findall(r'data-tool="([^"]*)" data-on="1"', out["html"]))
    every = set(re.findall(r'data-tool="([^"]*)"', out["html"]))
    assert on == every, sorted(every - on)


def test_one_press_includes_the_connector_the_user_added():
    """No preset can name a connector tool — they do not exist until somebody
    adds one — so a button saying "allow everything" that left Notion off would
    be the same button `preset_tools` already refuses to be."""
    out = built(preset="all")

    assert out["posted"]["tools"].count("notion__search") == 1, out["posted"]


def test_one_press_sends_the_presets_NAME_so_it_means_everything_NOW():
    """A screen left open while a tool shipped would otherwise send its own
    stale idea of the word and quietly withhold the new one."""
    out = built(preset="all")

    assert out["posted"]["preset"] == "all", out["posted"]


def test_a_switch_pressed_afterwards_cancels_the_preset():
    """The preset is resolved on the server, so leaving the name attached would
    re-grant everything and throw away the change the user just made — a switch
    that visibly moved and then did nothing."""
    out = built(preset="all", toggle="run_python")

    assert "preset" not in out["posted"], out["posted"]
    assert "run_python" not in out["posted"]["tools"]


def test_nothing_yet_leaves_only_what_cannot_leave_this_machine():
    out = built(preset="none")

    assert set(out["posted"]["tools"]) == {"search_brain", "remember", "who_is"}


def test_a_preset_the_server_sent_no_preview_for_changes_nothing():
    """It would have nothing to draw, and a button that visibly does nothing is
    worse than one that is not there."""
    bare = [{k: v for k, v in p.items() if k != "tools"} for p in PRESETS]
    out = run(tools=[*TIERED, NOTION], connectors=[READY], groups=SPECS,
              presets=bare, preset="all")

    assert "run_python" not in out["posted"]["tools"]


def test_a_new_agent_gets_everything_that_cannot_leave_this_machine():
    """The panel tells the user that group is ALWAYS ON. A builder granting
    three of its ten made that sentence false — an agent created here could not
    say who somebody was while the screen promised it always could."""
    out = built()

    assert "who_is" in out["posted"]["tools"], out["posted"]["tools"]


def test_the_group_that_is_always_on_is_not_a_switch_here_either(builder):
    assert "Always on" in builder["html"]


def test_the_builder_offers_no_button_that_opens_a_screen_behind_it():
    """It is a modal. The Connectors screen would come up *behind* it, so the
    button reads as doing nothing — and closing the modal to reach it would
    throw away a half-typed form. The reason still names the route in words,
    which is the part that works from either screen."""
    out = run(tools=[*TIERED, NOTION], connectors=[READY, DOWN],
              groups=SPECS, presets=PRESETS)

    assert "Open Connectors" not in out["html"]
    assert "data-screen" not in out["html"]
    assert "Linear needs signing in" in out["html"], "it still says what is wrong"
