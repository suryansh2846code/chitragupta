"""A card the user already answered comes back answered.

An action card is drawn from the `<action>` tag inside a stored message, and the
outcome only ever lived in the DOM. So reopening a conversation rebuilt every
card from scratch: an automation the user had created an hour ago came back
offering **Confirm & create**, with a fresh set of editable fields.

That is not cosmetic. The obvious thing to do with a button marked "Confirm &
create" is press it, and the result is a second automation — or a second email.

The claims: a settled card is drawn as settled, the key it settles under is the
same one it is drawn under next time, and answering a card records it.
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
        "label": "New automation",
        "fields": ["name", "trigger", "agent", "instruction"],
        "risk": "red", "reversible": True, "undo_label": "Delete it",
        "always_ask_because": "Creating automations always needs your approval.",
    },
}

AUTOMATION = {"type": "create_routine", "params": {
    "name": "Running kit check when Dev messages on WhatsApp",
    "trigger": "schedule", "agent": "chief-of-staff",
    "instruction": "Open web.whatsapp.com and look for a message from Dev."}}

MADE = {"ok": True, "detail": "Automation created — runs every 15 min",
        "verified": False, "reversible": True, "log_id": "L7"}


def drive(action=None, result=None, *, card_state=None, confirm=False,
          press=None):
    payload = {"action": action or AUTOMATION, "result": result or MADE,
               "catalog": CATALOG}
    if card_state is not None:
        payload["cardState"] = card_state
    if confirm:
        payload["edits"] = {}          # the harness presses Confirm when set
    if press:
        payload["press"] = press
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# ── a card that was never answered ─────────────────────────────────────────

def test_an_unanswered_card_still_asks():
    """The control. A guard that hid every card would also pass the test
    below."""
    out = drive()
    assert out["settled"] == ""
    assert "Confirm &" in out["text"]


def test_it_carries_the_key_it_will_be_found_by():
    out = drive()
    assert out["cardKey"].startswith("create_routine|")
    assert out["cardKey"].endswith("#1"), "the position is part of the key"


# ── a card that was ───────────────────────────────────────────────────────

def key_of(action=None):
    return drive(action)["cardKey"]


def test_a_card_that_was_confirmed_does_not_offer_to_do_it_again():
    """The bug. An automation created an hour ago came back offering to create
    it, and the obvious thing to do with that button is press it."""
    settled = {key_of(): {"state": "done",
                          "detail": "Automation created — runs every 15 min"}}
    out = drive(card_state=settled)

    assert out["settled"] == "done"
    assert "Confirm &" not in out["text"]
    assert "Automation created" in out["text"]


def test_a_confirmed_card_says_when_it_was_verified():
    """Rung 5 survives the reload too: "confirmed 3:42 PM" is what the service
    said happened, and a card that lost it would claim less than it knows."""
    settled = {key_of(): {"state": "done", "detail": "Sent",
                          "verified_at": "2026-09-26T15:42:00+05:30"}}
    out = drive(card_state=settled)
    assert "confirmed" in out["text"]


def test_a_cancelled_card_stays_cancelled():
    out = drive(card_state={key_of(): {"state": "cancelled"}})
    assert out["settled"] == "cancelled"
    assert "Cancelled" in out["text"]
    assert "Confirm &" not in out["text"]


def test_a_failed_card_shows_the_failure_and_keeps_its_buttons():
    """A failure is not settled in the way the other two are: the second
    attempt is the one that counts, so the card has to stay pressable."""
    out = drive(card_state={key_of(): {"state": "failed",
                                       "detail": "Gmail said no"}})
    assert out["settled"] == "failed"
    assert "Gmail said no" in out["text"]


# ── answering one records it ───────────────────────────────────────────────

def test_confirming_tells_the_server_what_happened():
    """Otherwise the next reload draws it pending again, which is the bug with
    an extra step."""
    out = drive(confirm=True)

    posted = [r for r in out["remembered"] if "/cards/" in r["url"]]
    assert posted, out["remembered"]
    assert posted[0]["state"] == "done"
    assert "Automation created" in posted[0]["detail"]
    assert posted[0]["log_id"] == "L7"


def test_the_key_is_escaped_on_the_way_out():
    """Every key ends in `#n`, and a `#` in a URL is a fragment — sent raw, the
    server is handed the key with its position cut off and stores the answer
    under something nothing is ever drawn with. The card would come back
    pending, which is the bug this whole mechanism is about."""
    out = drive(confirm=True)

    url = next(r["url"] for r in out["remembered"] if "/cards/" in r["url"])
    assert "#" not in url, url
    assert url.endswith("%231"), url


def test_cancelling_is_recorded_too():
    """The other half of settling one. A cancelled card that came back pending
    is the same bug wearing the other answer — the user said no an hour ago and
    is asked again."""
    out = drive(press="cancel")

    posted = [r for r in out["remembered"] if "/cards/" in r["url"]]
    assert posted, out["remembered"]
    assert posted[0]["state"] == "cancelled"
    assert "Cancelled" in out["afterCancel"]


def test_a_failed_confirm_is_recorded_as_failed():
    out = drive(result={"ok": False, "error": "Gmail said no"}, confirm=True)

    posted = [r for r in out["remembered"] if "/cards/" in r["url"]]
    assert posted and posted[0]["state"] == "failed"
    assert "Gmail said no" in posted[0]["detail"]


# ── the key is about the card, not about when it was drawn ────────────────

def test_the_same_proposal_keys_the_same_every_time():
    """The whole mechanism rests on this: a message has no id the frontend can
    see, so the key is built from the action itself."""
    assert key_of() == key_of()


def test_a_different_proposal_keys_differently():
    other = {"type": "create_routine", "params": {
        **AUTOMATION["params"], "name": "Something else entirely"}}
    assert key_of() != key_of(other)
