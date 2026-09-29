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
                    "fields": ["blocks", "note"], "required": ["blocks"]},
    # Nothing required: a draft with no recipient yet is a real thing to ask
    # for. The control for every test above.
    "create_draft": {"label": "Save a draft", "risk": "green",
                     "fields": ["to", "cc", "subject", "body"], "required": []},
    # …and the one that could not work, which nothing had written down.
    "send_email": {"label": "Send email", "risk": "amber",
                   "fields": ["to", "cc", "subject", "body"],
                   "required": ["to"]},
    # A group: an address *or* the anyone-with-the-link flag.
    "drive_share": {"label": "Share a document", "risk": "amber",
                    "fields": ["file_id", "email", "role", "anyone"],
                    "required": ["file_id", ["email", "anyone"]],
                    "only_when_set": ["anyone"]},
    "create_followup": {"label": "Track a follow-up", "risk": "green",
                        "fields": ["about", "who", "due"],
                        "required": [["about", "who"]]},
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
    box to fix it is on the same card.

    Read off `.ac-missing` rather than the card's text: this passed against the
    card's text because the *editor label* for the same field says "Agent" too,
    so it would have passed with no sentence at all.
    """
    out = drive(MISSING)
    assert "Agent" in out["missingSay"], out["missingSay"]
    assert "before this can run" in out["missingSay"]


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
    out = drive({"type": "create_draft",
                 "params": {"subject": "Notes", "body": "Later."}})
    assert not out["blocked"], out["text"]


# ── the refusals nobody had written down ───────────────────────────────────
#
# Each of these offered a live button whose only possible outcome was the
# handler's own complaint. They were found by walking the registry, not by a
# user reporting them — see `test_action_required_fields.py`.

def test_an_email_with_no_recipient_does_not_offer_to_send():
    """`_draft_or_send` has always answered "a recipient (to) is required"."""
    out = drive({"type": "send_email",
                 "params": {"subject": "Proposal", "body": "Here it is."}})
    assert out["blocked"], out["text"]
    assert "To" in out["missingSay"], out["missingSay"]


def test_a_session_with_every_exercise_removed_does_not_offer_to_log():
    """An empty list is missing, not present. `String([])` is `""` either way;
    a `{}` would have read as filled, which is why this asks the value rather
    than its spelling."""
    out = drive({"type": "log_workout", "params": {"blocks": []}})
    assert out["blocked"], out["text"]


# ── a group: either one will do ────────────────────────────────────────────

def test_a_follow_up_with_only_a_person_named_still_offers_to_act():
    """The over-strict half of the same bug: the handler fills `about` in from
    `who` ("Waiting on Rahul"), so a flat list hid a button that worked."""
    out = drive({"type": "create_followup",
                 "params": {"who": "Rahul", "due": "tomorrow 9am"}})
    assert not out["blocked"], out["text"]


def test_a_follow_up_with_neither_is_still_blocked():
    out = drive({"type": "create_followup", "params": {"due": "tomorrow 9am"}})
    assert out["blocked"], out["text"]


def test_it_names_both_ways_out_of_a_group():
    """"Needs About" over a card that would also take a name is an instruction
    to do the wrong thing."""
    out = drive({"type": "create_followup", "params": {}})
    assert "About or Who" in out["missingSay"], out["missingSay"]


def test_a_link_share_needs_no_address():
    """"Anyone with the link" reaches an unbounded audience and names nobody.
    Requiring an address would hide the button over something that works."""
    out = drive({"type": "drive_share",
                 "params": {"file_id": "f1", "anyone": "true"}})
    assert not out["blocked"], out["text"]


def test_a_share_with_neither_an_address_nor_a_link_is_blocked():
    out = drive({"type": "drive_share", "params": {"file_id": "f1"}})
    assert out["blocked"], out["text"]


# ── the flag that turns sharing into publishing ────────────────────────────

def test_a_link_share_says_so_on_the_card():
    """`anyone` was not in `fields`, so the card asking permission to publish a
    document never mentioned that it would be public.

    And then it was in `fields`, which was worse in a way only a picture showed:
    the registry fallback drew a box labelled **Anyone** containing the word
    **true**. A flag is not a field and a boolean is not something anybody types
    — it is a sentence, and the title says which of the two things this is.
    """
    out = drive({"type": "drive_share",
                 "params": {"file_id": "f1", "anyone": "true"}})
    assert "Publish this document" in out["text"], out["text"]
    assert "Anyone with the link" in out["text"], out["text"]
    assert "Anyone" not in out["editableFields"], out["editableFields"]
    assert "true" not in out["text"], "the flag is showing as its own value"


def test_an_ordinary_share_does_not_say_it_is_public():
    """The other half: a card that warned about publishing on every share would
    teach the user to skim past the warning."""
    out = drive({"type": "drive_share",
                 "params": {"file_id": "f1", "email": "rahul@work.test"}})
    assert "Share a document" in out["text"], out["text"]
    assert "Anyone with the link" not in out["text"], out["text"]


def test_an_ordinary_share_grows_no_empty_anyone_box():
    """An empty box reads as something the user forgot to fill in."""
    out = drive({"type": "drive_share",
                 "params": {"file_id": "f1", "email": "rahul@work.test"}})
    assert "Anyone" not in out["editableFields"], out["editableFields"]
