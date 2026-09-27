"""A card that has been acted on never wears the proposal look again.

Two of the three settled states were drawn properly and the third was not:
a failure kept the amber **Confirm & create**, the whole editable form, and a
tag reading *needs your confirmation* — directly above the sentence explaining
why it had already been attempted and failed. One card saying both things.

The reason it happened is worth more than the fix: `done` and `cancelled` went
through a settled renderer and `failed` was patched into the pending one. Two
paths, so the third state was always going to drift from the other two. There
is one renderer now, `settledCard`, and both `actionCard` and `planCard` use it
— so a fourth state, or a new kind of card, cannot be half-done.

Retrying is asking the agent again. Pressing a card from yesterday is the
duplicate-action hazard this whole mechanism exists to remove.
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
    "create_routine": {"label": "Create automation", "identity": ["name"],
                       "fields": ["name", "trigger", "agent", "instruction"],
                       "risk": "red", "reversible": True,
                       "always_ask_because": "Creating automations always "
                                             "needs your approval."},
    "send_email": {"label": "Send email", "identity": ["to", "subject"],
                   "fields": ["to", "cc", "subject", "body"], "risk": "amber"},
    "log_workout": {"label": "Log session", "fields": ["blocks", "note"],
                    "risk": "green"},
}

ROUTINE = {"type": "create_routine",
           "params": {"name": "Notify on mail", "trigger": "new_email",
                      "agent": "chotu", "instruction": "Watch for mail."}}
EMAIL = {"type": "send_email",
         "params": {"to": "a@b.c", "subject": "Hi", "body": "Hello"}}
WORKOUT = {"type": "log_workout",
           "params": {"blocks": [{"exercise": "Squat", "sets": 5}]}}

STATES = ["done", "cancelled", "failed"]


def logged(action, *, ok):
    return {"type": action["type"], "params": dict(action["params"]),
            "ok": ok, "detail": "It worked" if ok else "there is no agent "
                                                       "called “cheif of staff”",
            "verified_at": "", "log_id": "L1", "reversible": True, "at": ""}


def drive(action=None, *, plan=None, state=None, key=None):
    payload = {"catalog": CATALOG, "result": {"ok": True, "detail": "made"}}
    if plan is not None:
        payload["plan"] = plan
    else:
        payload["action"] = action
    if state == "failed":
        payload["ran"] = [logged(action or plan["steps"][0], ok=False)]
    elif state == "done":
        payload["ran"] = [logged(action or plan["steps"][0], ok=True)]
    elif state == "cancelled":
        payload["cardState"] = {key: {"state": "cancelled"}}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


def key_of(action):
    return drive(action)["cardKey"]


# ── the bug ────────────────────────────────────────────────────────────────

def test_a_failed_card_does_not_still_ask_to_be_confirmed():
    """One card saying both "needs your confirmation" and "there is no agent
    called that" — the second sentence only exists because the first was
    already answered."""
    out = drive(ROUTINE, state="failed")
    assert out["settled"] == "failed"
    assert "needs your confirmation" not in out["text"].lower(), out["text"]
    assert "Confirm &" not in out["text"], out["text"]


def test_it_says_what_went_wrong():
    out = drive(ROUTINE, state="failed")
    assert "cheif of staff" in out["text"], out["text"]


def test_a_failed_card_has_no_form_to_fill_in_either():
    """The boxes are for correcting a proposal. Over a card that has already
    run they invite an edit that goes nowhere."""
    out = drive(ROUTINE, state="failed")
    assert out["editableFields"] == [], out["editableFields"]


# ── and every state is uniform, for every card ─────────────────────────────

@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("action,name", [(ROUTINE, "routine"),
                                         (EMAIL, "email"),
                                         (WORKOUT, "workout")])
def test_no_settled_card_of_any_type_keeps_a_button(state, action, name):
    """The claim is about every card, not about automations. `log_workout`
    grows its own editor and `send_email` is amber — both used to reach the
    button-wiring below the settled branch."""
    out = drive(action, state=state, key=key_of(action))
    assert out["settled"] == state, out["text"]
    assert "Confirm &" not in out["text"], out["text"]
    assert out["editableFields"] == [], out["editableFields"]


@pytest.mark.parametrize("state", STATES)
def test_every_settled_state_still_says_what_kind_of_card_it_is(state):
    out = drive(ROUTINE, state=state, key=key_of(ROUTINE))
    assert out["kind"] == "Automation", out


@pytest.mark.parametrize("state", STATES)
def test_a_plan_is_uniform_too(state):
    plan = {"rationale": "One thing.", "steps": [ROUTINE]}
    out = drive(plan=plan, state=state, key=drive(plan=plan)["cardKey"])
    assert out["settled"] == state, out["text"]
    assert "Approve &" not in out["text"], out["text"]


# ── the control ────────────────────────────────────────────────────────────

def test_an_unanswered_card_still_asks_and_still_has_its_form():
    """A change that settled everything would pass every test above."""
    out = drive(ROUTINE)
    assert out["settled"] == ""
    assert "Confirm &" in out["text"]
    assert out["editableFields"], "a pending card must still be correctable"
