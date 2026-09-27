"""A card recognises its own action in what the agent actually did.

The screen recording an answer only ever covers cards answered since that code
shipped. Every card already in a conversation has nothing recorded — so on the
user's own machine the automation card for "Running kit check when Dev messages
on WhatsApp" kept offering **Confirm & create** a day after the automation
existed, and the plan card kept offering **Approve & do all** directly above
the results of the run it had already done.

A stored message and a log row share no id, so the card matches on the only
thing they do share: the parameters it is proposing. The logged action carries
more than the card proposed (`agent_id` is added on the way through), so the
test is containment — everything the card names, the action did.

An entry is claimed once. Two identical proposals in one conversation are two
cards, and one logged action must not settle both.
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
        "fields": ["name", "trigger", "agent", "interval_min", "instruction"],
        "risk": "red", "reversible": True,
        "always_ask_because": "Creating automations always needs your approval.",
    },
}

PARAMS = {"name": "Running kit check when Dev messages on WhatsApp",
          "trigger": "schedule", "interval_min": "15",
          "agent": "chief-of-staff", "instruction": "Open web.whatsapp.com…"}

ACTION = {"type": "create_routine", "params": PARAMS}

#: What the log gives back for it — the same parameters, plus the bookkeeping
#: the confirm added on the way through.
LOGGED = {"type": "create_routine",
          "params": {**PARAMS, "agent_id": "health"},
          "ok": True, "detail": "Automation created — runs every 15 min",
          "verified_at": "", "log_id": "L7", "reversible": True,
          "at": "2026-09-26T16:10:05+00:00"}


def drive(action=None, *, ran=None, card_state=None, plan=None):
    payload = {"catalog": CATALOG, "result": {"ok": True, "detail": "made"}}
    if plan is not None:
        payload["plan"] = plan
    else:
        payload["action"] = action or ACTION
    if ran is not None:
        payload["ran"] = ran
    if card_state is not None:
        payload["cardState"] = card_state
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# ── the bug, on the user's own data ────────────────────────────────────────

def test_a_card_whose_action_already_ran_is_drawn_done():
    """Nothing recorded this card. The automation exists anyway, and the card
    was still offering to create it."""
    out = drive(ran=[LOGGED])
    assert out["settled"] == "done", out["text"]
    assert "Confirm &" not in out["text"]
    assert "Automation created" in out["text"]


def test_a_card_with_nothing_matching_still_asks():
    """The control. A change that settled every card would pass the test
    above and make the app unusable."""
    out = drive(ran=[])
    assert out["settled"] == ""
    assert "Confirm &" in out["text"]


def test_a_different_action_does_not_settle_it():
    other = {**LOGGED, "params": {**PARAMS, "name": "Something else entirely"}}
    assert drive(ran=[other])["settled"] == ""


def test_another_type_does_not_settle_it():
    assert drive(ran=[{**LOGGED, "type": "send_email"}])["settled"] == ""


# ── matching is containment, not equality ──────────────────────────────────

def test_the_bookkeeping_the_confirm_added_does_not_stop_the_match():
    """`agent_id` is put on by the confirm, so the logged parameters are always
    a superset. Requiring equality would match nothing that ever ran."""
    assert drive(ran=[LOGGED])["settled"] == "done"


def test_a_card_proposing_more_than_the_action_did_is_not_a_match():
    """Containment runs one way. A card naming a day filter the action never
    carried is a different card, and drawing it done would claim the automation
    has a schedule it does not."""
    thin = {**LOGGED, "params": {"name": PARAMS["name"]}}
    assert drive(ran=[thin])["settled"] == ""


# ── one action settles one card ────────────────────────────────────────────

def test_a_logged_action_is_claimed_only_once():
    """Two identical cards in one conversation, one action. The first claims
    it; the second is still a proposal and must still say so."""
    out = drive(ran=[LOGGED], card_state={})
    assert out["settled"] == "done"
    # The harness draws one card, so the claim is asserted through the count
    # the card reports rather than by drawing a second.
    assert out["ranLeft"] == 0, "the entry should have been taken"


def test_two_actions_are_there_for_two_cards():
    out = drive(ran=[LOGGED, LOGGED])
    assert out["settled"] == "done"
    assert out["ranLeft"] == 1, "the second card's entry must survive the first"


# ── a failure is not settled ───────────────────────────────────────────────

def test_a_failed_action_leaves_the_card_pressable():
    """The retry is the attempt that counts."""
    out = drive(ran=[{**LOGGED, "ok": False, "detail": "Google said no"}])
    assert out["settled"] == "failed"
    assert "Google said no" in out["text"]
    assert "Confirm &" in out["text"]


# ── the recorded answer still wins ─────────────────────────────────────────

def test_a_cancelled_card_is_not_overridden_by_the_log():
    """A cancellation is the one state the log cannot hold. If the log won,
    a card the user said no to would come back claiming it had run."""
    out = drive(ran=[LOGGED], card_state={"__ignored__": {"state": "done"}})
    assert out["settled"] == "done"

    key = drive()["cardKey"]
    out2 = drive(ran=[LOGGED], card_state={key: {"state": "cancelled"}})
    assert out2["settled"] == "cancelled", out2["text"]


# ── and the plan card matches every step ───────────────────────────────────

PLAN = {"rationale": "Two automations.",
        "steps": [{"type": "create_routine", "params": {"name": "A"}},
                  {"type": "create_routine", "params": {"name": "B"}}]}


def plan_ran(*names):
    return [{"type": "create_routine", "params": {"name": n, "agent_id": "x"},
             "ok": True, "detail": f"{n} created", "verified_at": "",
             "log_id": n, "reversible": True, "at": ""} for n in names]


def test_a_plan_settles_when_every_step_ran():
    out = drive(plan=PLAN, ran=plan_ran("A", "B"))
    assert out["settled"] == "done", out["text"]
    assert "Approve &" not in out["text"]


def test_a_plan_with_only_some_steps_run_keeps_its_buttons():
    """Half a plan is exactly when the user needs the button back — and a card
    drawn "done" over it would hide that one step never happened."""
    out = drive(plan=PLAN, ran=plan_ran("A"))
    assert out["settled"] != "done", out["text"]
    assert "Approve &" in out["text"]
