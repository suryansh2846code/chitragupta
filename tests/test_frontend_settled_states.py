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

**The fourth state arrived, and it was already broken when this file said that.**
`unknown` is what a card becomes when the confirm request never comes back — no
network, a 500, a timeout. That path settled nothing at all: the buttons had
already been replaced by "Working…", so the card kept a stale spinner, a tag
reading *needs your confirmation* and a red error underneath, for the rest of
the session. It was the only exit `markAnswered` did not cover, which is why one
settled renderer did not catch it.

It is not a shade of `failed`. A 500 from `/api/actions/execute` can arrive after
the email has gone, so "it didn't work" is a claim nobody checked and a button
would offer to send it twice. And it is not a dead end either: the action log
knows what really ran, so `settledState` prefers a logged run over an `unknown`
record and the card corrects itself to "done · confirmed 3:42 PM" on the next
render.
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
    # Reversible, and the Undo it earns is labelled per action rather than
    # "Undo" — which is the half a reopened card used to get wrong by not
    # offering one at all.
    "create_event": {"label": "Create calendar event",
                     "identity": ["title", "start"],
                     "fields": ["title", "start", "end", "description"],
                     "risk": "amber", "reversible": True,
                     "undo_label": "Remove the event"},
}

ROUTINE = {"type": "create_routine",
           "params": {"name": "Notify on mail", "trigger": "new_email",
                      "agent": "chotu", "instruction": "Watch for mail."}}
EMAIL = {"type": "send_email",
         "params": {"to": "a@b.c", "subject": "Hi", "body": "Hello"}}
WORKOUT = {"type": "log_workout",
           "params": {"blocks": [{"exercise": "Squat", "sets": 5}]}}

STATES = ["done", "cancelled", "failed", "unknown"]


def logged(action, *, ok):
    return {"type": action["type"], "params": dict(action["params"]),
            "ok": ok, "detail": "It worked" if ok else "there is no agent "
                                                       "called “cheif of staff”",
            "verified_at": "", "log_id": "L1", "reversible": True, "at": ""}


def drive(action=None, *, plan=None, state=None, key=None, ran=None,
          fail=None):
    payload = {"catalog": CATALOG, "result": {"ok": True, "detail": "made"}}
    if ran is not None:
        payload["ran"] = ran
    if fail is not None:
        payload["failRequest"] = fail
    if plan is not None:
        payload["plan"] = plan
    else:
        payload["action"] = action
    if state == "failed":
        payload["ran"] = [logged(action or plan["steps"][0], ok=False)]
    elif state == "done":
        payload["ran"] = [logged(action or plan["steps"][0], ok=True)]
    elif state in ("cancelled", "unknown"):
        payload["cardState"] = {key: {"state": state}}
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


# ── the fourth state: we tried and we do not know ──────────────────────────

def confirm_but_lose_the_reply(action=None, *, plan=None):
    """Press Confirm against a request that never comes back.

    `api()` rejects on any non-2xx, so a 500, a timeout and a dropped connection
    all arrive here as a throw. `/cards/` is deliberately still allowed through,
    so what the card told the server can be read back.
    """
    return drive(action, plan=plan, fail="/api/actions/execute")


def test_a_card_whose_request_never_came_back_does_not_keep_working():
    """The bug. "Working…" sat where the buttons had been for the rest of the
    session, because this was the one exit that settled nothing."""
    out = confirm_but_lose_the_reply(EMAIL)
    assert out["settledAfter"] == "unknown", out["afterConfirm"]
    assert "Working" not in out["actionsAfter"], out["actionsAfter"]


def test_it_stops_saying_it_needs_confirming():
    """The same contradiction the failed card had, reached by another route."""
    out = confirm_but_lose_the_reply(EMAIL)
    assert "needs your confirmation" not in out["tagAfter"].lower()
    assert out["tagAfter"] == "couldn\u2019t tell", out["tagAfter"]


def test_it_does_not_claim_the_email_failed():
    """A 500 can arrive after the mail has left. "It didn't work" would be a
    claim nobody checked — and the user's next move depends on which it was."""
    out = confirm_but_lose_the_reply(EMAIL)
    said = out["afterConfirm"]
    assert "may or may not have gone through" in said, said
    assert "ac-unsure" in said
    # The reason is kept, quietly, under the sentence that matters.
    assert "Internal Server Error" in said


def test_it_says_how_to_find_out():
    """A state with no way forward is a dead end. The agent can read the sent
    folder back; the card cannot."""
    out = confirm_but_lose_the_reply(EMAIL)
    assert "Ask the agent" in out["afterConfirm"]


def test_it_offers_no_button_to_press_again():
    """The duplicate hazard is the whole reason this is not left pending."""
    out = confirm_but_lose_the_reply(EMAIL)
    assert "Confirm &" not in out["actionsAfter"]


def test_it_tells_the_server_so_a_reload_agrees():
    """Otherwise the card comes back pending tomorrow, offering to send an email
    that may already be in somebody's inbox."""
    out = confirm_but_lose_the_reply(EMAIL)
    told = [r for r in out["remembered"] if r.get("state") == "unknown"]
    assert told, out["remembered"]


def test_a_plan_whose_request_never_came_back_is_the_same():
    """And the worse one: this button runs every step, so a plan card that came
    back pending would be a second copy of the whole plan."""
    out = confirm_but_lose_the_reply(plan={"rationale": "One thing.",
                                           "steps": [ROUTINE]})
    assert out["settledAfter"] == "unknown", out["afterConfirm"]
    assert "Approve &" not in out["actionsAfter"]
    assert [r for r in out["remembered"] if r.get("state") == "unknown"]


# ── and it corrects itself ─────────────────────────────────────────────────

def test_a_logged_run_beats_an_unknown_record():
    """The point of the state. The send went through and the reply was lost, so
    the log has the answer the screen never got — and the card has to take it."""
    key = key_of(EMAIL)
    out = drive(EMAIL, state="unknown", key=key, ran=[logged(EMAIL, ok=True)])
    assert out["settled"] == "done", out["text"]
    assert "It worked" in out["text"]


def test_a_reloaded_unknown_card_does_not_say_it_was_cancelled():
    """**The dangerous render, and the one my own tests missed at first.**

    `settledCard`'s result line was a three-way choice where everything that was
    not `done` or `failed` fell through to "Cancelled". So a card reopened after
    a lost reply told the user nothing had happened — over an email that may
    already be in somebody's inbox. Found by reverting the renderer arm and
    watching *nothing* go red: the state was right, the sentence was a lie, and
    every assertion I had was about buttons and tags.
    """
    out = drive(EMAIL, state="unknown", key=key_of(EMAIL), ran=[])
    assert "Cancelled" not in out["text"], out["text"]
    assert "may or may not have gone through" in out["text"], out["text"]
    assert "Ask the agent" in out["text"], out["text"]


def test_an_unknown_with_nothing_in_the_log_stays_unknown():
    """It must not fall back to pending: nothing in the log is not evidence that
    nothing happened, and a button here is the duplicate."""
    out = drive(EMAIL, state="unknown", key=key_of(EMAIL), ran=[])
    assert out["settled"] == "unknown", out["text"]
    assert "Confirm &" not in out["text"]


def test_a_recorded_cancellation_still_beats_the_log():
    """The precedence exception is only for `unknown`. A cancellation exists
    nowhere else, so letting the log win would bring back a card the user said
    no to, claiming it had run."""
    out = drive(EMAIL, state="cancelled", key=key_of(EMAIL),
                ran=[logged(EMAIL, ok=True)])
    assert out["settled"] == "cancelled", out["text"]


# ── an Undo that survives a reload ─────────────────────────────────────────
#
# The record has carried `log_id` and `reversible` since it was written, and
# nothing read either. So the Undo a user could see at 3:42 was gone by the time
# they reloaded — which is when a person actually notices the date was wrong.
# The action log is still where an older one is found; these two just have to
# agree about a card that is on screen.

REVERSIBLE = {"type": "create_event",
              "params": {"title": "Budget review", "start": "Fri 2pm"}}


def reopened(action, *, record):
    """Draw `action` as a card already answered, from a stored record."""
    key = drive(action)["cardKey"]
    payload = {"catalog": CATALOG, "action": action,
               "result": {"ok": True, "detail": "made"},
               "cardState": {key: record},
               "undoResult": {"ok": True, "detail": "Event removed"}}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


DID_RUN = {"state": "done", "detail": "Event created", "log_id": "L4",
           "reversible": True, "undo_label": "Remove the event"}


def test_a_reopened_card_still_offers_to_take_it_back():
    out = reopened(REVERSIBLE, record=DID_RUN)
    assert out["settled"] == "done"
    assert out["hasUndo"], "the Undo was only ever on the live card"


def test_it_wears_the_words_the_server_chose():
    """"Undo" over an action whose inverse is "Move it to the bin" describes the
    wrong thing, and for a connector action the label is decided per run."""
    out = reopened(REVERSIBLE, record=DID_RUN)
    assert out["undoLabel"] == "Remove the event", out["undoLabel"]


def test_pressing_it_takes_the_action_back():
    """A rendered button that does nothing is the failure this whole column is
    supposed to prevent, so the handler is invoked."""
    out = reopened(REVERSIBLE, record=DID_RUN)
    assert out["undoSent"] == {"log_id": "L4"}, out["undoSent"]
    assert "Event removed" in out["afterUndo"], out["afterUndo"]


def test_it_stops_offering_after_it_has_been_used():
    """Otherwise the record still says `reversible` and the button comes back
    over something already taken back — which can only answer "that was already
    undone"."""
    out = reopened(REVERSIBLE, record=DID_RUN)
    told = [r for r in out["remembered"] if r.get("reversible") is False]
    assert told, out["remembered"]
    assert told[-1]["state"] == "done", "it must not stop being done"


def test_an_action_with_no_inverse_offers_nothing():
    """A sent email has no undo and must not pretend to."""
    out = reopened(EMAIL, record={"state": "done", "detail": "Sent",
                                  "log_id": "L5", "reversible": False})
    assert not out["hasUndo"]


def test_a_cancelled_card_offers_nothing_either():
    """There is nothing to take back: it never ran."""
    out = reopened(REVERSIBLE, record={"state": "cancelled", "reversible": True,
                                       "log_id": "L6"})
    assert not out["hasUndo"]


def test_a_settled_plan_offers_nothing():
    """Its record keeps one log id — the last step's — so an Undo here would
    take back one of several and look like it had taken back all of them."""
    plan = {"rationale": "One thing.", "steps": [REVERSIBLE]}
    key = drive(plan=plan)["cardKey"]
    payload = {"catalog": CATALOG, "plan": plan,
               "result": {"ok": True, "detail": "made"},
               "cardState": {key: DID_RUN}}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    assert out["settled"] == "done"
    assert not out["hasUndo"]
