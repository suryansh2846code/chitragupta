"""Correcting a proposal before confirming it, and taking it back after.

Three claims, and none of them is visible in the rendered HTML — which is why
this runs the real render and the real click handlers rather than reading the
source:

* **What executes is what is on the card when Confirm is pressed.** The card
  used to be editable for exactly one action (`EDITABLE = { log_workout: true }`),
  so the one where a typo is worst — an email — sent you back to the agent to
  ask for the same message with one word changed. The fields now come from
  `/api/actions/catalog`, which is the registry's own list.
* **Undo is offered only where an inverse exists.** A sent email has none, and
  a button that quietly does nothing is worse than no button.
* **The tier is stated in the user's words.** "amber" means nothing to a
  person; "this leaves your machine" does.

The harness is `tests/js/action_card_plain.mjs`. It seeds `ACTION_CATALOG`
because the card is normally filled at boot, and a harness that never boots
renders a card with no fields and no Undo — the two things most worth checking.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

#: What the server publishes for these two, copied rather than imported: the
#: point is that the frontend renders whatever the registry says, so a test
#: that imported the registry would only prove it agrees with itself.
CATALOG = {
    "send_email": {
        "label": "Send email", "fields": ["to", "cc", "subject", "body", "attach"],
        "risk": "amber", "reversible": False, "undo_label": "",
        "always_ask_because": "",
    },
    "create_draft": {
        "label": "Save a draft", "fields": ["to", "cc", "subject", "body", "attach"],
        "risk": "green", "reversible": True, "undo_label": "Discard it",
        "always_ask_because": "",
    },
    "set_reminder": {
        "label": "Set reminder", "fields": ["message", "at"],
        "risk": "green", "reversible": True, "undo_label": "Cancel it",
        "always_ask_because": "",
    },
    "mail_triage": {
        "label": "Inbox changes", "fields": ["items"],
        "risk": "red", "reversible": True, "undo_label": "Put them back",
        "always_ask_because": "Changing your inbox always needs your approval.",
    },
}

EMAIL = {"type": "send_email",
         "params": {"to": "rahul@work.test", "subject": "Proposl",
                    "body": "Here is the revised version."}}

SENT = {"ok": True, "detail": "Email sent to rahul@work.test",
        "verified": True, "verified_at": "2026-09-19T15:42:00+05:30",
        "reversible": False, "log_id": "L1"}


def _run(action, result, *, edits=None, catalog=CATALOG, undo_result=None):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps({"action": action, "result": result, "edits": edits,
                          "catalog": catalog, "undoResult": undo_result}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# ── correcting before confirming ───────────────────────────────────────────

def test_an_email_card_offers_a_box_for_every_typeable_field():
    out = _run(EMAIL, SENT)
    assert out["editableFields"] == ["To", "Cc", "Subject", "Body"]


def test_a_file_path_gets_no_text_box():
    """A half-typed path is an attachment that silently vanishes, and the
    folder grants would have to re-check whatever was typed anyway. Changing
    what is attached means asking the agent."""
    out = _run({**EMAIL, "params": {**EMAIL["params"],
                                    "attach": "/Users/x/Docs/deck.pdf"}}, SENT)
    assert "Attach" not in out["editableFields"]


# ── drafting ───────────────────────────────────────────────────────────────

DRAFT = {"type": "create_draft",
         "params": {"to": "rahul@work.test", "subject": "Proposal",
                    "body": "Here it is.", "cc": "lead@work.test",
                    "attach": "/Users/x/Docs/deck.pdf", "thread_id": "t7"}}

SAVED = {"ok": True, "detail": "Draft saved to rahul@work.test",
         "reversible": True, "undo_label": "Discard it", "log_id": "L1"}


def test_a_draft_card_never_says_send():
    """A person reading the card has to be able to tell a thing that was
    prepared from a thing that has gone."""
    out = _run(DRAFT, SAVED)
    assert "Save a draft" in out["text"]
    assert "Send email" not in out["text"]
    assert "Confirm & save" in out["text"]


def test_a_draft_card_says_it_reaches_nobody():
    out = _run(DRAFT, SAVED)
    assert out["risk"] == "green"
    assert "Reaches nobody" in out["text"]


def test_an_attachment_is_named_but_its_path_is_not():
    """The path is long enough to push the subject off the card, and the user
    already knows where their own file is. The name is what they are checking:
    that it is the right document."""
    out = _run(DRAFT, SAVED)
    assert "deck.pdf" in out["text"]
    assert "/Users/x/Docs" not in out["text"]


def test_a_reply_says_it_joins_the_existing_conversation():
    out = _run(DRAFT, SAVED)
    assert "existing conversation" in out["text"]


def test_a_draft_can_be_discarded_from_the_card():
    out = _run(DRAFT, SAVED,
               undo_result={"ok": True, "detail": "Draft discarded"})
    assert out["hasUndo"]
    assert out["undoLabel"] == "Discard it"
    assert "Draft discarded" in out["afterUndo"]


def test_confirming_a_draft_still_carries_the_fields_nobody_can_type():
    """`attach` and `thread_id` are not editable, which must not mean they are
    dropped — a draft that quietly lost its attachment is the bug."""
    out = _run(DRAFT, SAVED)
    assert out["sent"]["params"]["attach"] == "/Users/x/Docs/deck.pdf"
    assert out["sent"]["params"]["thread_id"] == "t7"


def test_the_corrected_subject_is_what_gets_sent_not_the_proposed_one():
    """The whole feature. The agent proposed "Proposl"; the user fixed it, and
    the request must carry the fix — a confirm that silently used the original
    would be the worst possible version of this."""
    out = _run(EMAIL, SENT, edits={"setField": {"Subject": "Proposal"}})
    assert out["sent"]["params"]["subject"] == "Proposal"
    assert out["sent"]["params"]["to"] == "rahul@work.test"
    assert out["sent"]["type"] == "send_email"


def test_a_card_with_no_catalog_still_confirms():
    """The catalog is a convenience, never a dependency. A fetch that failed at
    boot must not take the card down with it."""
    out = _run(EMAIL, SENT, catalog={})
    assert out["editableFields"] == []
    assert out["sent"]["params"]["subject"] == "Proposl"


def test_structured_fields_get_no_text_box():
    """`items` is a list of messages. A textarea containing JSON is not a
    correction anybody can make safely, so the generic editor skips it and the
    bespoke rendering stands."""
    triage = {"type": "mail_triage",
              "params": {"items": [{"id": "m1", "do": "archive",
                                    "subject": "Newsletter"}]}}
    out = _run(triage, {"ok": True, "detail": "Archived 1", "reversible": True,
                        "undo_label": "Put them back", "log_id": "L2"})
    assert out["editableFields"] == []


# ── the tier, in the user's words ──────────────────────────────────────────

def test_an_outbound_card_says_it_leaves_the_machine():
    out = _run(EMAIL, SENT)
    assert out["risk"] == "amber"
    assert "leaves your machine" in out["text"]


def test_a_red_card_gives_the_reason_the_registry_wrote_for_it():
    """One sentence per action — a user told "creating automations always needs
    your approval" about their inbox learns nothing."""
    triage = {"type": "mail_triage", "params": {"items": []}}
    out = _run(triage, {"ok": True, "detail": "done", "log_id": "L2"})
    assert out["risk"] == "red"
    assert "Changing your inbox always needs your approval." in out["text"]


# ── verify, on the card ────────────────────────────────────────────────────

def test_a_verified_send_shows_the_time_the_service_reported():
    out = _run(EMAIL, SENT)
    assert "confirmed" in out["afterConfirm"].lower()


def test_an_unverified_send_claims_no_confirmation():
    """"Sent" is what we asked for. Claiming a confirmation nobody made is the
    one thing this line must never do."""
    out = _run(EMAIL, {**SENT, "verified": False, "verified_at": ""})
    assert "confirmed" not in out["afterConfirm"].lower()
    assert "Email sent" in out["afterConfirm"]


# ── undo ───────────────────────────────────────────────────────────────────

REMINDER = {"type": "set_reminder",
            "params": {"message": "call Rahul", "at": "tomorrow 9am"}}
REMINDED = {"ok": True, "detail": "Reminder set for Sun Sep 20, 9:00 AM",
            "reversible": True, "undo_label": "Cancel it", "log_id": "L9"}


def test_a_reversible_action_offers_undo_labelled_by_the_server():
    out = _run(REMINDER, REMINDED)
    assert out["hasUndo"]
    assert out["undoLabel"] == "Cancel it"


def test_undo_posts_the_log_id_and_reports_what_came_back():
    """Addressed by log entry, because the inverse needs the *result* — the id
    the service handed back — which the card does not otherwise hold."""
    out = _run(REMINDER, REMINDED,
               undo_result={"ok": True, "detail": "Reminder cancelled"})
    assert out["undoSent"] == {"log_id": "L9"}
    assert "Reminder cancelled" in out["afterUndo"]


def test_a_sent_email_offers_no_undo_button():
    """It has left the machine. Nothing takes it back, so nothing offers to."""
    out = _run(EMAIL, SENT)
    assert not out["hasUndo"]


def test_an_action_that_failed_offers_no_undo():
    out = _run(REMINDER, {"ok": False, "error": "couldn't understand the time"})
    assert not out["hasUndo"]


def test_a_refused_undo_leaves_the_button_so_it_can_be_tried_again():
    out = _run(REMINDER, REMINDED,
               undo_result={"ok": False, "error": "That is no longer there."})
    assert "Cancel it" in out["afterUndo"], (
        "a refused undo removed its own button, so there was no way to retry")
