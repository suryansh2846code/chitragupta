"""A card shows each value once, above the button, and never stops being true.

Three faults with one cause. The rows were HTML strings built per action, and
the editable boxes were built from the registry — so neither half could know
what the other had already said:

* **every value appeared twice.** "To rahul@work.test" as a printed row, and
  then a labelled box with the same address in it. An email card said its
  recipient, subject and whole body twice over.
* **after an edit the first copy was wrong.** The box is what the button runs;
  the row above it still showed what the model had proposed. A card describing
  something other than what its button runs is the one thing a card may never do,
  and this was that — reachable by typing.
* **the form sat below the button.** The boxes were appended after `.ac-result`,
  so the thing you are meant to correct was underneath the thing you press, and
  tabbing off Confirm went *forwards* into the fields it had already run with.

`actionFace` returns rows as data now, each carrying the registry field it is a
readback `owns`. A field with a box has no row; a row with no field — a sentence,
an attachment, one of seventeen emails — always keeps it.

Two lines are *derived* rather than owned: an automation's "Runs weekdays at
8:00 AM" and a session's total volume. Dropping them because their inputs have
boxes would delete the most important sentence on the card, so they are redrawn
on every keystroke instead.
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
                                reason="node is needed to execute the frontend")


def _catalog() -> dict:
    from chitragupta.actions import catalog

    return catalog()


def drive(action, *, edits=None, catalog=None):
    payload = {"action": action, "result": {"ok": True, "detail": "done"},
               "catalog": _catalog() if catalog is None else catalog}
    if edits is not None:
        payload["edits"] = edits
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


ADDRESS = "rahul@work.test"
SUBJECT = "Revised proposal"
BODY = "Here is the revised version."
EMAIL = {"type": "send_email",
         "params": {"to": ADDRESS, "subject": SUBJECT, "body": BODY}}

ROUTINE = {"type": "create_routine",
           "params": {"name": "Morning brief", "trigger": "daily",
                      "at": "8:00am", "days": "mon,tue,wed,thu,fri",
                      "agent": "Chief of Staff",
                      "instruction": "Summarise what came in overnight."}}

SESSION = {"type": "log_workout",
           "params": {"blocks": [
               {"exercise": "Squat", "sets": 5, "reps": 5, "weight": 100}]}}


def occurrences(text: str, needle: str) -> int:
    return text.count(needle)


# ── once, not twice ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [ADDRESS, SUBJECT, BODY])
def test_an_email_card_says_each_thing_once(value):
    """The readback printed it and the box repeated it."""
    out = drive(EMAIL)
    assert occurrences(out["text"], value) == 1, (
        f"{value!r} appears {occurrences(out['text'], value)} times:\n"
        f"{out['text']}")


def test_the_value_is_in_the_box_rather_than_the_row():
    """Which of the two survived matters: the box is the one that can be
    corrected, and the one the button reads."""
    out = drive(EMAIL)
    assert out["editableFields"] == ["To", "Cc", "Subject", "Body"]
    assert f"<b>To</b> {ADDRESS}" not in out["html"], out["html"]


def test_a_row_no_box_can_replace_is_still_there():
    """`attach` is not typeable, so nothing duplicates the attachment line and
    dropping it would lose the only mention of a file leaving the machine."""
    out = drive({"type": "send_email",
                 "params": {**EMAIL["params"], "attach": "/Users/x/deck.pdf",
                            "thread_id": "t7"}})
    assert "deck.pdf" in out["text"]
    assert "existing conversation" in out["text"]


def test_an_empty_field_draws_no_bare_label():
    """An empty row reads as something the user forgot. `.ac-missing` is what
    says a field is needed."""
    out = drive({"type": "send_email", "params": {"subject": "", "body": "Hi"}})
    assert "<b>Subject</b> </div>" not in out["html"], out["html"]


# ── above the button ───────────────────────────────────────────────────────

def test_the_form_comes_before_the_buttons():
    """Tab order follows the DOM, so a form below the button is a form a
    keyboard user reaches only after pressing it.

    Checked by what the card placed it *before*, by object identity: the slot
    holding the buttons is part of the card's own markup, so a fake DOM cannot
    say where the editor ended up — only what it was measured against.
    """
    out = drive(EMAIL)
    placed = {i["what"]: i["before"] for i in out["insertedBefore"]}
    assert placed.get("ac-edit") == "ac-actions", out["insertedBefore"]


# ── and it keeps up ────────────────────────────────────────────────────────

def test_the_schedule_readback_follows_the_box():
    """An automation's card promises when it will run. Correct the time and the
    promise has to move with it — otherwise the card says 8am over a button that
    creates 9am."""
    before = drive(ROUTINE)
    assert "Weekdays at 8:00am" in before["text"], before["text"]

    after = drive(ROUTINE, edits={"setField": {"At": "9:30am"}})
    assert "Weekdays at 9:30am" in after["text"], after["text"]
    assert "8:00am" not in after["text"], (
        "the card still promises the time the model proposed, over a button "
        "that would create the corrected one")


def test_the_schedule_readback_survives_a_changed_trigger():
    """The rule the server applies: a time of day beats the trigger the model
    reached for. The live line has to apply it too, on every keystroke."""
    out = drive(ROUTINE, edits={"setField": {"Trigger": "new_email"}})
    assert "new email" in out["text"].lower(), out["text"]


def test_the_session_total_follows_the_weights():
    """5×5×100 is 2,500 kg. Correct it to 105 and the total is 2,625."""
    before = drive(SESSION)
    assert "2,500 kg" in before["text"], before["text"]

    after = drive(SESSION, edits={"set": {"3": 105}})
    assert "2,625 kg" in after["text"], after["text"]
    assert "2,500 kg" not in after["text"]


def test_dropping_an_exercise_takes_it_out_of_the_total():
    """The editor can remove a whole block, which is the largest a frozen total
    can be wrong by."""
    out = drive({"type": "log_workout", "params": {"blocks": [
        {"exercise": "Squat", "sets": 5, "reps": 5, "weight": 100},
        {"exercise": "Bench", "sets": 3, "reps": 8, "weight": 60}]}},
        edits={"drop": [1]})
    assert "2,500 kg" in out["text"], out["text"]


# ── the control ────────────────────────────────────────────────────────────

def test_a_card_with_no_boxes_still_shows_everything():
    """Nothing typeable, so nothing is skipped: every row has to stand. This is
    the shape the approvals queue and every settled card render in."""
    out = drive({"type": "mail_triage", "params": {"items": [
        {"id": "m1", "do": "archive", "subject": "Your receipt"}]}})
    assert out["editableFields"] == []
    assert "Your receipt" in out["text"]
    assert "Nothing is deleted" in out["text"]


def test_what_executes_is_still_what_is_on_the_card():
    """The claim none of this may break."""
    out = drive(EMAIL, edits={"setField": {"Subject": "Fixed"}})
    assert out["sent"]["params"]["subject"] == "Fixed"
    assert out["sent"]["params"]["to"] == ADDRESS
