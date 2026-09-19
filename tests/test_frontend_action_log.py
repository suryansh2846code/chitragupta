"""What your agents did, and what can still be taken back.

The approvals queue is consent *before* an action runs. This is the other half,
and it is the half that makes agreeing to unattended work reasonable: a person
who cannot review what their agents did has only ever been asked to trust them.
`action_log` has stored every action since Phase 0; until this screen, nothing
read it back.

Three claims, none of them visible from the markup alone, so the harness runs
the real loader and presses the real button:

* **Undo appears only where an inverse exists and has not been used.** A sent
  email has none; an action already taken back has nothing left to take. A
  button that quietly does nothing is the exact failure this column prevents.
* **Pressing it sends that row's own log id** — the inverse needs the result
  the service returned, which the row does not otherwise hold.
* **A failure is shown as a failure**, with what went wrong, rather than
  disappearing into a list of things that look like they worked.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"


def _entry(**over):
    base = {"id": "e0", "action_type": "set_reminder", "summary": "Reminder: x",
            "risk": "green", "ok": True, "verified": False, "reversible": False,
            "undone": False, "detail": "", "origin": "chat",
            "created_at": "2026-09-19T12:00:00+00:00", "result": {}}
    base.update(over)
    return base


SENT_EMAIL = _entry(
    id="e1", action_type="send_email", risk="amber",
    summary="Email “Revised proposal” to rahul@work.test",
    verified=True, detail="Email sent",
    result={"verified_at": "2026-09-19T15:42:00+00:00"})

REMINDER = _entry(
    id="e2", summary="Reminder: call Rahul", reversible=True, origin="routine",
    detail="Reminder set", result={"undo_label": "Cancel it"})

FAILED_TRIAGE = _entry(
    id="e3", action_type="mail_triage", risk="red", summary="Archive 9 emails",
    ok=False, detail="Gmail needs re-authorization", origin="approval")

ALREADY_UNDONE = _entry(
    id="e4", action_type="create_event", risk="amber",
    summary="Calendar event “Standup”", reversible=True, undone=True,
    detail="Event created", result={"undo_label": "Remove the event"})


def _run(entries, undo_result=None):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_log.mjs"), str(WEB / "app.js")],
        input=json.dumps({"entries": entries, "undoResult": undo_result}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# ── what a person reads ────────────────────────────────────────────────────

def test_each_action_is_one_row_in_the_state_it_ended_in():
    out = _run([SENT_EMAIL, REMINDER, FAILED_TRIAGE, ALREADY_UNDONE])
    assert out["states"] == ["ok", "ok", "err", "undone"]


def test_an_empty_log_says_so_instead_of_showing_nothing():
    out = _run([])
    assert out["emptyHidden"] is False
    assert out["html"] == ""


def test_a_failure_shows_what_went_wrong():
    """It would otherwise sit in the list looking like everything else that
    worked, which is the version of this screen that misleads."""
    out = _run([FAILED_TRIAGE])
    assert "Gmail needs re-authorization" in out["text"]


def test_a_verified_action_reports_the_time_the_service_confirmed():
    out = _run([SENT_EMAIL])
    assert "confirmed" in out["text"]


def test_an_unverified_action_claims_no_confirmation():
    out = _run([REMINDER])
    assert "confirmed" not in out["text"]


def test_where_an_action_came_from_is_said_in_the_users_words():
    """A routine acting on its own is a different fact about your week than a
    card you tapped. "chat" is the ordinary case and says nothing — a label on
    every row is a label nobody reads."""
    out = _run([REMINDER, FAILED_TRIAGE, SENT_EMAIL])
    assert "an automation" in out["text"]
    assert "you approved it" in out["text"]
    assert "chat" not in out["text"]


def test_an_undone_action_says_it_was_taken_back():
    out = _run([ALREADY_UNDONE])
    assert "taken back" in out["text"]


# ── undo ───────────────────────────────────────────────────────────────────

def test_undo_is_offered_only_where_an_inverse_exists_and_is_unused():
    out = _run([SENT_EMAIL, REMINDER, FAILED_TRIAGE, ALREADY_UNDONE])
    assert out["undoButtons"] == ["e2"], (
        "a sent email, a failure, or an action already taken back offered an "
        "Undo that could not work")


def test_the_button_carries_the_label_the_server_chose():
    out = _run([REMINDER])
    assert "Cancel it" in out["text"]


def test_pressing_undo_sends_that_rows_log_id():
    out = _run([REMINDER], undo_result={"ok": True,
                                        "detail": "Reminder cancelled"})
    undo_calls = [c for c in out["calls"] if "/api/actions/undo" in c["url"]]
    assert len(undo_calls) == 1
    assert undo_calls[0]["body"] == {"log_id": "e2"}
    assert "Reminder cancelled" in out["toasts"]


def test_the_list_is_reloaded_after_an_undo():
    """The row is now telling the user something that is no longer true."""
    out = _run([REMINDER], undo_result={"ok": True, "detail": "Cancelled"})
    reads = [c for c in out["calls"]
             if "/api/actions/log" in c["url"] and c["method"] == "GET"]
    assert len(reads) >= 2


def test_a_refused_undo_says_why_and_still_reloads():
    """Usually it means the world moved — the reminder already fired, the event
    was deleted in Calendar. Either way the row is stale."""
    out = _run([REMINDER],
               undo_result={"ok": False, "error": "That is no longer there."})
    assert "That is no longer there." in out["toasts"]
    reads = [c for c in out["calls"]
             if "/api/actions/log" in c["url"] and c["method"] == "GET"]
    assert len(reads) >= 2
