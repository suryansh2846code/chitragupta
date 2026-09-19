"""One card, several actions, one button.

Seventeen emails is seventeen decisions and one judgement. Rendering it as
seventeen cards asks a person to make that judgement seventeen times, and the
second one is already being made without reading.

What this pins, running the real render and the real click handler:

* **An action inside a plan is not also a loose card.** Rendering it twice is
  two buttons for one action, and the second one runs it again.
* **The card shows the worst tier, not the commonest.** Nine green archives
  beside one amber send is a card that sends an email.
* **Confirm posts the steps as proposed**, in order, to the plan endpoint.
* **A half-run plan names what never started.** "Six of nine" does not tell
  anybody *which* three did not happen.
* **Undo says how many it can take back**, because some of a plan is
  reversible and some is not.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

CATALOG = {
    "set_reminder": {"label": "Set reminder", "fields": ["message", "at"],
                     "risk": "green", "reversible": True,
                     "undo_label": "Cancel it", "always_ask_because": ""},
    "send_email": {"label": "Send email", "fields": ["to", "subject", "body"],
                   "risk": "amber", "reversible": False, "undo_label": "",
                   "always_ask_because": ""},
    "mail_triage": {"label": "Inbox changes", "fields": ["items"],
                    "risk": "red", "reversible": True,
                    "undo_label": "Put them back",
                    "always_ask_because":
                        "Changing your inbox always needs your approval."},
}

REMIND = {"type": "set_reminder", "params": {"message": "chase Rahul"}}
EMAIL = {"type": "send_email",
         "params": {"to": "rahul@work.test", "subject": "Proposal",
                    "body": "hi"}}
TRIAGE = {"type": "mail_triage", "params": {"items": [{"id": "m1",
                                                       "do": "archive"}]}}


def _run(*, plan=None, reply=None, result=None, undo_result=None,
         catalog=CATALOG):
    payload = {"catalog": catalog, "result": result, "undoResult": undo_result}
    if plan is not None:
        payload["plan"] = plan
    if reply is not None:
        payload["reply"] = reply
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


DONE = {"ok": True, "detail": "All 2 done.", "skipped": [], "undoable": ["L1"],
        "steps": [
            {"index": 0, "type": "set_reminder", "summary": "Reminder: chase Rahul",
             "result": {"ok": True, "log_id": "L1", "reversible": True}},
            {"index": 1, "type": "send_email",
             "summary": "Email “Proposal” to rahul@work.test",
             "result": {"ok": True, "log_id": "L2", "reversible": False}}]}

HALF_RAN = {
    "ok": False, "detail": "1 of 3 done, then “Email …” failed. 1 not started.",
    "undoable": ["L1"],
    "steps": [
        {"index": 0, "type": "set_reminder", "summary": "Reminder: chase Rahul",
         "result": {"ok": True, "log_id": "L1", "reversible": True}},
        {"index": 1, "type": "send_email", "summary": "Email “Proposal”",
         "result": {"ok": False, "error": "Gmail is not connected"}}],
    "skipped": [{"index": 2, "type": "set_reminder",
                 "summary": "Reminder: book the room"}]}


# ── the split ──────────────────────────────────────────────────────────────

def test_an_action_inside_a_plan_is_not_also_rendered_loose():
    """Two cards for one action is two buttons, and the second one runs it
    again."""
    out = _run(reply=(
        "Here you go.\n"
        '<plan rationale="two things">\n'
        '<action type="set_reminder" at="tomorrow">chase Rahul</action>\n'
        '<action type="set_reminder" at="friday">review the deck</action>\n'
        "</plan>\n"
        '<action type="set_reminder" at="monday">book the room</action>'))
    assert len(out["parsed"]["plans"]) == 1
    assert out["parsed"]["plans"][0]["steps"] == ["set_reminder", "set_reminder"]
    assert out["parsed"]["loose"] == ["set_reminder"], (
        "a plan's own actions leaked out as loose cards")


def test_the_prose_survives_and_the_tags_do_not():
    out = _run(reply='Morning.\n<plan><action type="set_reminder">x</action></plan>')
    assert "Morning." in out["parsed"]["clean"]
    assert "<plan" not in out["parsed"]["clean"]
    assert "<action" not in out["parsed"]["clean"]


def test_a_reply_with_no_plan_behaves_exactly_as_before():
    out = _run(reply='<action type="set_reminder" at="tomorrow">x</action>')
    assert out["parsed"]["plans"] == []
    assert out["parsed"]["loose"] == ["set_reminder"]


def test_a_plan_whose_actions_are_all_malformed_is_dropped():
    out = _run(reply='<plan><action type="mail_triage">{oops</action></plan>')
    assert out["parsed"]["plans"] == []
    assert out["parsed"]["loose"] == []


# ── the card ───────────────────────────────────────────────────────────────

def test_the_card_counts_the_actions_and_shows_what_was_understood():
    out = _run(plan={"rationale": "17 emails; 2 need you",
                     "steps": [REMIND, EMAIL]}, result=DONE)
    assert "2 actions ready" in out["text"]
    assert "17 emails; 2 need you" in out["text"]


def test_the_card_takes_the_worst_tier_not_the_commonest():
    out = _run(plan={"rationale": "", "steps": [REMIND, REMIND, EMAIL]},
               result=DONE)
    assert out["risk"] == "amber", (
        "two green steps outvoted the one that leaves the machine")


def test_a_red_step_makes_the_whole_plan_red_and_says_why():
    out = _run(plan={"rationale": "", "steps": [REMIND, TRIAGE]}, result=DONE)
    assert out["risk"] == "red"
    assert "Changing your inbox always needs your approval." in out["text"]


def test_an_all_green_plan_says_it_reaches_nobody():
    out = _run(plan={"rationale": "", "steps": [REMIND, REMIND]}, result=DONE)
    assert out["risk"] == "green"
    assert "Reaches nobody" in out["text"]


def test_every_step_is_named_so_the_card_can_be_read_not_trusted():
    out = _run(plan={"rationale": "", "steps": [REMIND, EMAIL]}, result=DONE)
    assert "chase Rahul" in out["text"]
    assert "rahul@work.test" in out["text"]


# ── confirming ─────────────────────────────────────────────────────────────

def test_confirm_posts_the_steps_in_order_to_the_plan_endpoint():
    out = _run(plan={"rationale": "", "steps": [REMIND, EMAIL]}, result=DONE)
    assert [s["type"] for s in out["sent"]["steps"]] == ["set_reminder",
                                                         "send_email"]
    assert out["sent"]["steps"][0]["params"]["message"] == "chase Rahul"


def test_each_step_reports_its_own_outcome():
    out = _run(plan={"rationale": "", "steps": [REMIND, EMAIL]}, result=DONE)
    assert "Reminder: chase Rahul" in out["afterConfirm"]
    assert "Email" in out["afterConfirm"]


def test_a_half_run_plan_names_what_never_started():
    """"Six of nine" does not tell anybody WHICH three did not happen, and a
    plan that half-ran is exactly when a person needs to know."""
    out = _run(plan={"rationale": "", "steps": [REMIND, EMAIL, REMIND]},
               result=HALF_RAN)
    assert "Gmail is not connected" in out["afterConfirm"]
    assert "book the room" in out["afterConfirm"]
    assert "not started" in out["afterConfirm"]


# ── undoing ────────────────────────────────────────────────────────────────

def test_undo_says_how_many_of_the_plan_it_can_take_back():
    """Some of a plan is reversible and some is not. A button saying "Undo"
    over a sent email promises something it cannot do."""
    out = _run(plan={"rationale": "", "steps": [REMIND, EMAIL]}, result=DONE,
               undo_result={"ok": True, "detail": "Took back 1 of 1."})
    assert out["hasUndo"]
    assert out["undoLabel"] == "Undo that"


def test_undo_posts_every_reversible_step_together():
    out = _run(plan={"rationale": "", "steps": [REMIND, EMAIL]}, result=DONE,
               undo_result={"ok": True, "detail": "Took back 1 of 1."})
    assert out["undoSent"] == {"log_ids": ["L1"]}
    assert "Took back 1 of 1." in out["afterUndo"]


def test_a_plan_with_nothing_reversible_offers_no_undo():
    nothing_back = {**DONE, "undoable": []}
    out = _run(plan={"rationale": "", "steps": [EMAIL]}, result=nothing_back)
    assert not out["hasUndo"]
