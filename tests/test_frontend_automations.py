"""The automation screen, executed rather than read.

`node --check` and source-order assertions both pass while a TDZ error or a
detached container has broken the screen, which is why `tests/js/` exists at
all. These drive the real render paths with the whole page evaluated, exactly
as the browser loads it.

The two claims worth this much machinery:

* **A run that correctly declined must not read as a failure.** `blocked` means
  the conditions did not hold or a permission said no — the system working. A
  user who sees enough red badges stops reading them, and then misses the one
  that mattered.
* **An injection attempt must be surfaced.** The fence makes the attack fail
  silently; a user who is never told cannot go and look at the email.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "chitragupta" / "web"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

AUTOMATION = {
    "id": "a1", "name": "Client replies", "goal": "clients get a reply",
    "state": "active", "enabled": True, "runs": 3, "waiting": 0,
    "next_run": "2026-09-29T08:00:00+00:00", "last_run": "",
    "history": [
        {"id": "r1", "state": "completed", "created_at": "2026-09-28T09:00:00+00:00",
         "outcome": "Drafted a reply to Ana", "reason": ""},
        {"id": "r2", "state": "blocked", "created_at": "2026-09-27T09:00:00+00:00",
         "outcome": "", "reason": "conditions did not hold: domain is elsewhere.test"},
        {"id": "r3", "state": "escalated", "created_at": "2026-09-26T09:00:00+00:00",
         "outcome": "", "reason": "it needs permission to send_email"},
    ],
}

RUN = {
    "id": "r1", "state": "completed", "automation_id": "a1",
    "reason": "", "actions_used": 1, "model_calls_used": 2,
    "trigger": {"kind": "email.received", "subject": "Invoice 12"},
    "context": {"chars": 1200, "pieces": [{}, {}], "injection_attempts": []},
}

STEPS = [
    {"kind": "condition", "name": "conditions", "state": "done",
     "result": {"detail": "all conditions held"}, "error": ""},
    {"kind": "plan", "name": "agent turn", "state": "done", "result": {}, "error": ""},
    {"kind": "action", "name": "send_email", "state": "done", "result": {}, "error": ""},
    {"kind": "verify", "name": "send_email", "state": "done",
     "result": {"detail": "found in Sent"}, "error": ""},
]


def _run(automation=None, run=None, steps=None):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/automation_history.mjs"), str(WEB / "app.js")],
        input=json.dumps({"automation": automation or AUTOMATION,
                          "run": run or RUN, "steps": steps or STEPS}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


def test_the_history_panel_opens_and_lists_every_run():
    out = _run()
    assert out["historyHidden"] is False
    assert out["historyHtml"].count('class="auto-run"') == 3


def test_a_declined_run_does_not_read_as_a_failure():
    """`blocked` is the system correctly deciding not to act."""
    out = _run()
    assert "Did not apply" in out["historyHtml"]
    assert "Failed" not in out["historyHtml"]


def test_an_escalated_run_says_it_needs_the_user():
    out = _run()
    assert "needs you" in out["historyHtml"].lower()


def test_an_automation_that_never_ran_says_so_rather_than_showing_nothing():
    out = _run(automation={**AUTOMATION, "history": [], "runs": 0})
    assert "has not run yet" in out["historyHtml"]
    # The button class, not the container's — `auto-runs` contains `auto-run`,
    # and the looser check passes whether or not a row was drawn.
    assert 'class="auto-run"' not in out["historyHtml"]


def test_opening_a_run_shows_the_lifecycle_in_order():
    out = _run()
    html = out["runHtml"]
    for phrase in ("Checked the conditions", "Worked out what to do",
                   "Did send_email", "Confirmed send_email"):
        assert phrase in html, phrase
    assert html.index("Checked the conditions") < html.index("Did send_email")


def test_the_run_says_what_it_was_started_by():
    out = _run()
    assert "email.received" in out["runHtml"]
    assert "Invoice 12" in out["runHtml"]


def test_the_run_says_how_much_it_saw_and_spent():
    """An unattended system that will not say what it cost is one nobody
    leaves on."""
    out = _run()
    assert "1200 characters" in out["runHtml"].replace(",", "")
    assert "1 action" in out["runHtml"] and "2 model call" in out["runHtml"]


def test_an_injection_attempt_is_shown_to_the_user():
    hostile = {**RUN, "context": {**RUN["context"],
                                  "injection_attempts": ["Body of the email (m1)"]}}
    out = _run(run=hostile)
    assert "instructions" in out["runHtml"]
    assert "Body of the email (m1)" in out["runHtml"]
    assert "ignored" in out["runHtml"]


def test_a_clean_run_does_not_warn_about_injection():
    out = _run()
    assert "ignored" not in out["runHtml"]


def test_the_run_detail_asks_the_server_for_that_exact_run():
    out = _run()
    assert "/api/automations/a1/runs/r1" in out["calls"]


def test_names_are_escaped():
    """The automation's name is user-typed and lands in `innerHTML`."""
    out = _run(automation={**AUTOMATION, "name": '<img src=x onerror=alert(1)>'})
    assert "<img" not in out["historyHtml"]
    assert "&lt;img" in out["historyHtml"]
