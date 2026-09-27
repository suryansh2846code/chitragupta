"""A plan you already approved comes back approved.

`actionCard` learned to remember what it settled as; `planCard` did not, and a
plan card is the one that matters most — its button runs *every* step at once.
So a plan of two automations, approved and created an hour ago, came back after
a refresh offering **Approve & do all**, with the results of the first run
printed in the conversation directly underneath it.

Pressing it again makes a second copy of everything in the plan.

The store and the endpoints are `test_action_cards.py`; that a single action
card draws itself settled is `test_frontend_card_state.py`.
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
    "create_routine": {
        "label": "Create automation",
        "fields": ["name", "trigger", "agent", "at", "days", "instruction"],
        "risk": "red", "reversible": True,
        "always_ask_because": "Creating automations always needs your approval.",
    },
}

PLAN = {
    "rationale": "Two weekly Sunday automations.",
    "steps": [
        {"type": "create_routine", "params": {
            "name": "Sunday Weekly Schedule Builder", "trigger": "daily",
            "at": "9am", "days": "sun", "agent": "chief-of-staff",
            "instruction": "Build next week."}},
        {"type": "create_routine", "params": {
            "name": "Sunday Weekly Review", "trigger": "daily",
            "at": "7pm", "days": "sun", "agent": "chief-of-staff",
            "instruction": "Review the week."}},
    ],
}

DONE = {"ok": True, "detail": "Both automations created",
        "steps": [], "skipped": [], "undoable": []}


def drive(*, card_state=None, press=None, result=None):
    payload = {"plan": PLAN, "catalog": CATALOG, "result": result or DONE}
    if card_state is not None:
        payload["cardState"] = card_state
    if press:
        payload["press"] = press
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


def key_of():
    return drive()["cardKey"]


# ── it has a key at all ────────────────────────────────────────────────────

def test_a_plan_card_carries_a_key():
    """Without one there is nothing to record an answer against, which is why
    this card was the one that never remembered."""
    assert key_of().startswith("plan|"), key_of()
    assert key_of().endswith("#1")


def test_the_same_plan_keys_the_same_every_time():
    assert key_of() == key_of()


def test_a_plan_with_different_steps_keys_differently():
    other = {**PLAN, "steps": [PLAN["steps"][0]]}
    proc = drive()
    payload = {"plan": other, "catalog": CATALOG, "result": DONE}
    run = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert json.loads(run.stdout)["cardKey"] != proc["cardKey"]


# ── the bug ────────────────────────────────────────────────────────────────

def test_an_approved_plan_does_not_offer_to_do_it_all_again():
    """The failure as it shipped: the results of the run were printed directly
    under a card still offering to run it."""
    out = drive(card_state={key_of(): {"state": "done",
                                       "detail": "Both automations created"}})
    assert out["settled"] == "done"
    assert "Approve &" not in out["text"], out["text"]
    assert "Both automations created" in out["text"]


def test_a_cancelled_plan_stays_cancelled():
    out = drive(card_state={key_of(): {"state": "cancelled"}})
    assert out["settled"] == "cancelled"
    assert "Approve &" not in out["text"], out["text"]


def test_an_unanswered_plan_still_asks():
    """The control. A guard that hid every plan card would pass both above."""
    out = drive()
    assert out["settled"] == ""
    assert "Approve &" in out["text"]


def test_a_failed_plan_keeps_its_buttons():
    """Half a plan running is exactly when the second attempt matters, so a
    failure is not settled the way the other two are."""
    out = drive(card_state={key_of(): {"state": "failed",
                                       "detail": "Google said no"}})
    assert out["settled"] == "failed"
    assert "Google said no" in out["text"]
    assert "Approve &" in out["text"]


# ── answering one records it ───────────────────────────────────────────────

def test_approving_tells_the_server_what_happened():
    posted = [r for r in drive()["remembered"] if "/cards/" in r["url"]]
    assert posted, "nothing was recorded, so the next reload asks again"
    assert posted[0]["state"] == "done"
    assert "Both automations created" in posted[0]["detail"]


def test_a_plan_that_failed_is_recorded_as_failed():
    out = drive(result={"ok": False, "detail": "Nothing was created",
                        "steps": [], "skipped": [], "undoable": []})
    posted = [r for r in out["remembered"] if "/cards/" in r["url"]]
    assert posted and posted[0]["state"] == "failed", out["remembered"]


def test_cancelling_a_plan_is_recorded():
    out = drive(press="cancel")
    posted = [r for r in out["remembered"] if "/cards/" in r["url"]]
    assert posted and posted[0]["state"] == "cancelled", out["remembered"]


# ── and the steps say what they are ────────────────────────────────────────

def test_a_step_names_the_automation_it_will_create():
    """Both steps read "create routine" — the same three words twice, over a
    button that creates two standing automations. A plan card the user cannot
    read is a plan card they approve blind."""
    text = drive()["text"]
    assert "Sunday Weekly Schedule Builder" in text, text
    assert "Sunday Weekly Review" in text, text


def test_a_step_says_when_that_automation_runs():
    """The schedule is the whole difference between the two, and between what
    the user asked for and what they got."""
    text = drive()["text"]
    assert "Sun" in text, text
