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
    rule = CSS.split(".am-toggle.is-on", 1)[1].split("}", 1)[0]
    assert "--north" in rule, "the switch is not in the app's accent"


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


def test_both_modals_use_the_same_label_class():
    """The automation modal shares `.am-body`. When the blanket rule went, its
    labels went with it unless they carry the class too.

    Counted against the labels that are actually there rather than against a
    number. The number was 5, and the next field added to this modal made it 6
    — at which point the test says "6 != 5", which is not the defect it exists
    to catch and is fixed by editing the test. Comparing the two counts asks
    the real question: is any label here unstyled?
    """
    body = INDEX.split('id="routineModal"', 1)[1].split('id="agentModal"', 1)[0]
    assert "<label>" not in body, "a bare label is left with no style"
    assert body.count('class="am-label"') == body.count("<label"), (
        "a label in the automation modal does not carry .am-label")


def test_the_fields_cannot_be_squeezed_by_a_long_tool_list():
    """`.am-body` is a scrolling flex column, so without this the textarea
    shrank until its own placeholder was clipped in half.

    The selector appears twice — once in the shared field rule and once here —
    so every block that carries it is checked rather than the first one found.
    """
    blocks = [b.split("}", 1)[0]
              for b in CSS.split(".am-body > input, .am-body > textarea")[1:]]
    assert any("flex: none" in b for b in blocks), blocks
