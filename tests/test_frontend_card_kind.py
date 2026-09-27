"""Every card says what kind of thing it is.

A conversation accumulates cards and they all look alike — same window chrome,
same title weight, same buttons. Scrolling back through one, the only way to
tell an automation from an email from a calendar change was to read the whole
card. A settled card is worse, because its buttons are gone and there is less
to recognise it by.

So each carries its kind, as a word rather than an action id, beside the status.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

CATALOG = {
    "create_routine": {"label": "Create automation", "fields": ["name"],
                       "risk": "red", "identity": ["name"]},
    "send_email": {"label": "Send email", "fields": ["to", "subject"],
                   "risk": "amber", "identity": ["to", "subject"]},
    "create_event": {"label": "Create calendar event",
                     "fields": ["title", "start"], "risk": "amber"},
    "set_reminder": {"label": "Set reminder", "fields": ["message"],
                     "risk": "green"},
    "something_new": {"label": "Do a new thing", "fields": ["x"],
                      "risk": "green"},
}


def kind_of(action=None, *, plan=None, ran=None):
    payload = {"catalog": CATALOG, "result": {"ok": True, "detail": "made"}}
    if plan is not None:
        payload["plan"] = plan
    else:
        payload["action"] = action
    if ran is not None:
        payload["ran"] = ran
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


ROUTINE = {"type": "create_routine", "params": {"name": "Sunday review"}}


# ── the label ──────────────────────────────────────────────────────────────

def test_a_card_carries_its_kind():
    out = kind_of(ROUTINE)
    assert out["kind"] == "Automation", out["kind"]
    assert "Automation" in out["text"]


def test_the_kind_is_a_word_not_an_action_id():
    """The user has never seen `create_routine` and it is not theirs to
    learn."""
    out = kind_of(ROUTINE)
    assert "create_routine" not in out["text"]


@pytest.mark.parametrize("action_type,params,expected", [
    ("send_email", {"to": "a@b.c", "subject": "Hi"}, "Email"),
    ("create_event", {"title": "Standup", "start": "9am"}, "Calendar"),
    ("set_reminder", {"message": "Water"}, "Reminder"),
])
def test_each_family_says_which_it_is(action_type, params, expected):
    out = kind_of({"type": action_type, "params": params})
    assert out["kind"] == expected, out["kind"]


def test_an_action_nobody_has_named_still_gets_a_kind():
    """A card with a blank chip beside a full one reads as broken. Every
    action added later has to land somewhere sensible without an edit here."""
    out = kind_of({"type": "something_new", "params": {"x": "1"}})
    assert out["kind"], "a card with no kind at all"


def test_a_plan_says_it_is_a_plan():
    """It is the card whose button does the most, and the one least like the
    others."""
    out = kind_of(plan={"rationale": "Two things.",
                        "steps": [ROUTINE, ROUTINE]})
    assert out["kind"] == "Plan", out["kind"]


# ── and keeps it once it is settled ────────────────────────────────────────

def test_a_settled_card_still_says_what_it_was():
    """This is where it earns its place: the buttons are gone, so there is
    less left to recognise the card by, not more."""
    ran = [{"type": "create_routine", "params": {"name": "Sunday review"},
            "ok": True, "detail": "Automation created", "verified_at": "",
            "log_id": "L1", "reversible": True, "at": ""}]
    out = kind_of(ROUTINE, ran=ran)
    assert out["settled"] == "done"
    assert out["kind"] == "Automation", out["kind"]
    assert "Automation" in out["text"]
