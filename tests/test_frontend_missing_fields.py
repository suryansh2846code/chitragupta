"""A card never offers a button that cannot work.

An automation card arrived with an empty **Agent** box and a cheerful
**Confirm & create**. Pressing it could only ever return "say which agent" —
and the readback line above filled the gap with the word *personal*, which is
not an agent anybody has. The card named a value that did not exist and offered
to act on it.

Driven by `ActionSpec.required`, so it is one rule for every action rather than
a check about `agent` bolted onto the automation branch: a message with no chat
and a reminder with no text are the same fault.

The boxes are right there, so this asks rather than blocks — fill the field in
and the button comes back.
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
    "create_routine": {"label": "Create automation", "risk": "red",
                       "fields": ["name", "trigger", "agent", "at", "days",
                                  "instruction"],
                       "required": ["agent", "instruction"],
                       "identity": ["name"],
                       "always_ask_because": "Creating automations always "
                                             "needs your approval."},
    "message_send": {"label": "Send a message", "risk": "amber",
                     "fields": ["app", "chat", "text", "at"],
                     "required": ["app", "chat", "text"]},
    "log_workout": {"label": "Log session", "risk": "green",
                    "fields": ["blocks", "note"], "required": []},
}

WHOLE = {"type": "create_routine",
         "params": {"name": "Morning training nudge", "trigger": "daily",
                    "at": "8:00am", "days": "mon,wed,fri",
                    "agent": "Health & Fitness",
                    "instruction": "Send a desktop notification."}}
MISSING = {"type": "create_routine",
           "params": {**WHOLE["params"], "agent": ""}}


def drive(action, *, fill=None):
    payload = {"action": action, "catalog": CATALOG,
               "result": {"ok": True, "detail": "made"}}
    if fill is not None:
        payload["edits"] = {"setField": fill}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# ── the bug ────────────────────────────────────────────────────────────────

def test_a_card_missing_a_required_field_does_not_offer_to_act():
    """"Confirm & create" over an empty Agent box could only ever fail."""
    out = drive(MISSING)
    assert out["blocked"], out["text"]


def test_it_says_which_field_is_missing():
    """"Something is wrong" is not an instruction anybody can follow, and the
    box to fix it is on the same card."""
    out = drive(MISSING)
    assert "Agent" in out["text"], out["text"]


def test_it_does_not_invent_a_value_for_the_empty_field():
    """The readback said "· personal". There is no agent called personal — the
    card named something that does not exist, over a button that would act."""
    out = drive(MISSING)
    assert "personal" not in out["text"].lower(), out["text"]


def test_the_boxes_are_still_there_to_fix_it_with():
    """Blocking without offering the fix would be worse than the bug."""
    out = drive(MISSING)
    assert out["editableFields"], "nothing to correct it with"


def test_filling_the_field_in_lets_it_act_again():
    """The check runs on what is on the card now, not on what was proposed —
    the same rule the confirm itself follows."""
    out = drive(MISSING, fill={"Agent": "Health & Fitness"})
    assert out["afterConfirm"], out
    assert "THREW" not in str(out["afterConfirm"]), out["afterConfirm"]
    assert out["sent"] and out["sent"]["params"]["agent"] == "Health & Fitness"


# ── every action, not just automations ─────────────────────────────────────

@pytest.mark.parametrize("field", ["app", "chat", "text"])
def test_a_message_missing_any_of_its_own_needs_is_the_same_fault(field):
    """Driven by the registry, so a new action is covered by declaring what it
    needs rather than by editing the card."""
    out = drive({"type": "message_send",
                 "params": {"app": "telegram", "chat": "4411",
                            "text": "I'll send it tonight.", field: ""}})
    assert out["blocked"], out["text"]


# ── the control ────────────────────────────────────────────────────────────

def test_a_complete_card_still_acts():
    """A change that blocked every card would pass every test above."""
    out = drive(WHOLE)
    assert not out["blocked"], out["text"]
    assert "Confirm &" in out["text"]


def test_an_action_that_requires_nothing_is_never_blocked():
    out = drive({"type": "log_workout", "params": {"blocks": []}})
    assert not out["blocked"], out["text"]
