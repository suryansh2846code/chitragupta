"""The automation card says when the automation will actually run.

The card's readback understood two triggers out of three. `schedule` said
"every 60 min" and **everything else** said "on every new email" — so an
automation proposed as `trigger="daily" at="9am" days="sun"` was presented to
the user as running on every new email, with the day and the time shown
nowhere at all.

That is the one thing a card may never do: describe something other than what
its button runs. It has happened twice before on this card — `mcp_action`
rendered as "Create calendar event", and eight later actions after it — and the
fix both times was to stop the render path guessing.

The phrasing mirrors `core.schedule.describe_schedule`, and
`test_the_card_and_the_server_agree` is what keeps them mirrored.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from chitragupta.core.schedule import describe_schedule

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

CATALOG = {
    "create_routine": {
        "label": "New automation",
        "fields": ["name", "trigger", "agent", "at", "days", "interval_min",
                   "instruction"],
        "risk": "red", "reversible": True, "undo_label": "Delete it",
        "always_ask_because": "Creating automations always needs your approval.",
        "depends_on": {"at": ["trigger", ["daily"]],
                       "days": ["trigger", ["daily"]],
                       "interval_min": ["trigger", ["schedule"]]},
    },
}


def card(params):
    payload = {"action": {"type": "create_routine", "params": params},
               "result": {"ok": True, "detail": "made"}, "catalog": CATALOG}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out["text"]


SUNDAY = {"name": "Sunday review", "trigger": "daily", "at": "9am",
          "days": "sun", "agent": "chief-of-staff",
          "instruction": "Review the past week."}


# ── the bug ────────────────────────────────────────────────────────────────

def test_a_sunday_automation_does_not_say_on_every_new_email():
    """The failure exactly as it shipped. The user was about to approve a card
    that named the wrong trigger entirely."""
    runs = card(SUNDAY).split("Runs ", 1)[1].split(" \u00b7 ", 1)[0]
    assert "new email" not in runs.lower(), runs


def test_it_says_the_day_and_the_time():
    """Asserted on the **Runs** row, not merely somewhere on the card: the
    editable fields listed "At 9am" and "Days sun" the whole time, under a
    summary line that said something else. A card contradicting itself is not
    half-right."""
    text = card(SUNDAY)
    runs = text.split("Runs ", 1)[1].split(" \u00b7 ", 1)[0]
    assert "Sun" in runs, runs
    assert "9am" in runs or "9:00" in runs, runs


def test_a_daily_automation_with_no_day_says_every_day():
    text = card({**SUNDAY, "days": ""})
    runs = text.split("Runs ", 1)[1].split(" \u00b7 ", 1)[0]
    assert runs.lower().startswith("every day"), runs


# ── the other two triggers still read correctly ────────────────────────────

def test_new_email_still_says_so():
    """The control. A change that made every card say "Sun at 9am" would pass
    the test above."""
    text = card({"name": "Forward", "trigger": "new_email",
                 "agent": "chief-of-staff", "instruction": "Forward them."})
    runs = text.split("Runs ", 1)[1].split(" \u00b7 ", 1)[0]
    assert "new email" in runs.lower(), runs


def test_an_interval_still_names_its_interval():
    text = card({"name": "Check", "trigger": "schedule", "interval_min": "15",
                 "agent": "chief-of-staff", "instruction": "Check."})
    runs = text.split("Runs ", 1)[1].split(" \u00b7 ", 1)[0]
    assert "15" in runs, runs


# ── the rule the server applies, applied here too ──────────────────────────

def test_a_time_of_day_beats_the_trigger_the_model_reached_for():
    """`actions._create_routine` promotes any trigger with a time on it to
    `daily`, because a model reaches for the trigger it was shown first and
    then attaches `at="8am"` to it. The card has to apply the same rule or it
    describes a schedule the server will not create."""
    text = card({**SUNDAY, "trigger": "schedule", "interval_min": "60"})
    runs = text.split("Runs ", 1)[1].split(" \u00b7 ", 1)[0]
    assert "Sun" in runs, runs
    assert "60 min" not in runs, runs


def test_the_card_and_the_server_agree():
    """Two sources of phrasing that drift are worse than one that is wrong:
    the row and the card would say different things about one automation."""
    runs = card(SUNDAY).split("Runs ", 1)[1].split(" \u00b7 ", 1)[0]
    server = describe_schedule(
        {"trigger": "daily", "at_time": "09:00", "days": "sun"})
    # The server's words, allowing for the card keeping the user's own "9am"
    # rather than the stored 24-hour time.
    assert server.split(" at ")[0] in runs, (server, runs)
