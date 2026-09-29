"""The card you correct before a session is saved.

Everything about training happens in chat: the user describes a session, this
appears, they fix whatever was misread, they confirm. There is no training
screen and there is not going to be one.

The claim the card makes is narrow and worth pinning exactly: **what executes is
what is on the card at the moment Confirm is pressed** — not what the model
proposed. An edit the user made and a request that ignored it would be the worst
possible version of this, and it is invisible in the rendered HTML, so the
harness captures the request body.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

SESSION = {
    "type": "log_workout",
    "params": {"blocks": [
        {"exercise": "Squat", "sets": 5, "reps": 5, "weight": 100, "rpe": 8},
        {"exercise": "Bench press", "sets": 3, "reps": 8, "weight": 60},
    ]},
}


def _run(action, result, edits=None):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps({"action": action, "result": result, "edits": edits}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _parse(text):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card.mjs"), str(WEB / "app.js")],
        input=json.dumps({"text": text}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def card():
    return _run(SESSION, {"ok": True, "detail": "logged"})


# ── it renders, in chat, as something you can change ─────────────────────
def test_it_rendered(card):
    assert card["error"] is None, card["error"]


def test_it_is_a_card_not_a_form_somewhere_else(card):
    assert "Log this session" in card["html"]
    assert "needs your confirmation" in card["html"]


def test_every_number_is_editable(card):
    """Five fields per block: exercise, sets, reps, weight, RPE."""
    assert card["fieldCount"] == 10, "some of the session cannot be corrected"


def test_it_says_what_the_units_are(card):
    """A weight typed in pounds and stored as kilos is a wrong trend.

    Read from `text`, not `html` — the fields are appended elements, so
    `innerHTML` cannot see them and an assertion against it would pass or fail
    for the wrong reason.
    """
    assert "kg" in card["text"]
    assert "bodyweight" in card["text"]
    assert "before you confirm" in card["text"]


def test_it_shows_the_work_done(card):
    """5×5×100 + 3×8×60 = 3,940.

    From `text` for the reason the test above this one already gives: the total
    is a *derived* line now, drawn as an appended element so it can be redrawn
    when the user corrects a weight. `innerHTML` cannot see it.
    """
    assert "3,940 kg" in card["text"]


# ── the claim: what executes is what is on screen ────────────────────────
def test_an_edit_reaches_the_request():
    """The model heard 100 kg. It was 105."""
    out = _run(SESSION, {"ok": True}, edits={"set": {"3": 105}})
    blocks = out["sent"]["params"]["blocks"]
    assert blocks[0]["weight"] == 105, "the correction was thrown away"
    assert blocks[0]["exercise"] == "Squat", "editing one field changed another"


def test_an_exercise_name_can_be_corrected():
    out = _run(SESSION, {"ok": True}, edits={"set": {"0": "Front squat"}})
    assert out["sent"]["params"]["blocks"][0]["exercise"] == "Front squat"


def test_a_block_the_model_invented_can_be_dropped():
    out = _run(SESSION, {"ok": True}, edits={"drop": [1]})
    blocks = out["sent"]["params"]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["exercise"] == "Squat"


def test_confirming_unchanged_sends_what_was_proposed():
    out = _run(SESSION, {"ok": True})
    blocks = out["sent"]["params"]["blocks"]
    assert [b["exercise"] for b in blocks] == ["Squat", "Bench press"]
    assert blocks[0]["weight"] == 100


def test_the_agent_is_still_told_which_agent_confirmed():
    out = _run(SESSION, {"ok": True})
    assert "agent_id" in out["sent"]["params"]


# ── parsing ──────────────────────────────────────────────────────────────
def test_the_session_is_one_card_not_one_per_exercise():
    out = _parse('Nice work.\n<action type="log_workout">'
                 '[{"exercise":"Squat","sets":5,"reps":5,"weight":100},'
                 '{"exercise":"Row","sets":3,"reps":10,"weight":40}]</action>')
    assert len(out["actions"]) == 1
    assert len(out["actions"][0]["params"]["blocks"]) == 2
    assert "<action" not in out["clean"]


def test_broken_json_shows_no_card_rather_than_a_wrong_one():
    assert _parse('<action type="log_workout">{squat: 5x5}</action>')["actions"] == []


def test_an_empty_session_shows_no_card():
    assert _parse('<action type="log_workout">[]</action>')["actions"] == []


# ── only the types that should be editable are ───────────────────────────
def test_a_message_card_is_not_editable():
    """Editing is for data entry, where a misread is easy and found late."""
    out = _run({"type": "message_send",
                "params": {"app": "telegram", "chat": "@dana", "text": "hi"}},
               {"ok": True})
    assert out["fieldCount"] == 0
