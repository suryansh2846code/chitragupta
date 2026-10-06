"""The version row, executed rather than read.

`web/CLAUDE.md` requires a new render path and a new click handler to be run in
a test. The three failures that matter here are all invisible to
`node --check`: a Check button drawn on a build with no feed (pressing it could
only fail), a privacy sentence that stops matching what the request actually
sends, and a toggle that shows the state it asked for rather than the state the
server confirmed.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "chitragupta" / "web"
HARNESS = ROOT / "tests" / "js" / "updates_row.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

SENDS = ["app", "os", "arch"]

NO_FEED = {"current": "0.1.0", "configured": False, "enabled": False,
           "checked": False, "reason": "", "checked_at": 0, "update": None,
           "sends": SENDS, "interval_hours": 24, "due": False, "feed": ""}

UP_TO_DATE = {**NO_FEED, "configured": True, "enabled": True, "checked": True,
              "checked_at": 1_760_000_000, "feed": "https://x.invalid/f.json"}

AVAILABLE = {**UP_TO_DATE,
             "update": {"version": "0.2.0",
                        "url": "https://x.invalid/Chitragupta-0.2.0.dmg",
                        "notes": "Fixes a crash on launch",
                        "published": "2026-10-06", "critical": False}}


def run(**scenario) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS), str(WEB)],
        input=json.dumps(scenario), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, f"harness failed:\n{out.stdout}\n{out.stderr}"
    result = json.loads(out.stdout)
    assert "harness_error" not in result, result["harness_error"]
    assert result["steps"][0]["html"], "the row rendered nothing at all"
    return result


def step(result: dict, label: str) -> dict:
    for s in result["steps"]:
        if s["label"] == label:
            return s
    raise AssertionError(f"no step {label!r}")


# ── a build with no feed ────────────────────────────────────────────────────

def test_no_check_button_when_there_is_no_feed():
    """Pressing it could only fail, so it is not drawn."""
    first = step(run(answers=[NO_FEED]), "loaded")
    assert "upCheck" not in first["ids"]
    assert "upToggle" not in first["ids"]
    assert "0.1.0" in first["html"]
    assert "no update feed" in first["html"]


def test_an_unconfigured_build_does_not_claim_to_be_up_to_date():
    """It has not looked. Saying "you are up to date" would be a claim nobody
    checked."""
    html = step(run(answers=[NO_FEED]), "loaded")["html"]
    assert "will not check" in html


# ── up to date ──────────────────────────────────────────────────────────────

def test_up_to_date_says_so_and_offers_a_check():
    first = step(run(answers=[UP_TO_DATE]), "loaded")
    assert "up to date" in first["html"]
    assert "upCheck" in first["ids"]
    assert "upGet" not in first["ids"], "nothing to get when there is nothing new"


def test_the_privacy_sentence_names_what_the_server_said_it_sends():
    """The disclosure comes from `state.sends`, not from a list in the frontend
    — so it cannot drift from the request."""
    html = step(run(answers=[UP_TO_DATE]), "loaded")["html"]
    assert "this app's version" in html
    assert "macOS version" in html
    assert "Apple silicon or Intel" in html
    assert "nothing that identifies you" in html


def test_a_server_that_sends_fewer_fields_is_described_as_such():
    """If the request ever shrinks, the sentence shrinks with it."""
    fewer = {**UP_TO_DATE, "sends": ["app"]}
    html = step(run(answers=[fewer]), "loaded")["html"]
    assert "this app's version" in html
    assert "macOS version" not in html


# ── an update exists ────────────────────────────────────────────────────────

def test_an_available_update_is_named_with_a_way_to_get_it():
    first = step(run(answers=[AVAILABLE]), "loaded")
    assert "0.2.0" in first["html"]
    assert "upGet" in first["ids"]
    assert "Fixes a crash on launch" in first["html"]


def test_it_does_not_pretend_to_install_itself():
    """Nothing here replaces a signed, notarised app. Saying so is the honest
    version of not having Sparkle yet."""
    html = step(run(answers=[AVAILABLE]), "loaded")["html"]
    assert "will not replace itself" in html


def test_getting_it_goes_through_the_open_browser_guard():
    """A release URL arrives from a server, so it is not ours to hand to the
    system opener unchecked — `/api/open-browser` accepts http(s) only."""
    result = run(answers=[AVAILABLE], actions=["press_get"])
    assert result["get_existed"] is True
    opened = [r for r in result["requests"] if r["path"] == "/api/open-browser"]
    assert len(opened) == 1
    assert opened[0]["body"]["url"].endswith(".dmg")


# ── pressing Check ──────────────────────────────────────────────────────────

def test_pressing_check_asks_the_server_and_redraws():
    result = run(answers=[UP_TO_DATE, AVAILABLE], actions=["press_check"])
    posted = [r for r in result["requests"]
              if r["path"] == "/api/updates/check" and r["method"] == "POST"]
    assert len(posted) == 1
    after = step(result, "press_check")
    assert "0.2.0" in after["html"]
    assert "upGet" in after["ids"]


def test_a_failed_check_does_not_leave_a_dead_button():
    """The button is inside the markup the re-render replaces, so it comes back
    enabled rather than stuck on "Checking…"."""
    result = run(answers=[UP_TO_DATE], actions=["press_check"],
                 fail_on="/api/updates/check")
    after = step(result, "press_check")
    assert "upCheck" in after["ids"]
    assert "Checking" not in after["html"]
    assert result["toasts"], "a failure the user pressed for must be reported"


# ── the toggle ──────────────────────────────────────────────────────────────

def test_the_toggle_reports_the_state_the_server_confirmed():
    off = {**UP_TO_DATE, "enabled": False}
    result = run(answers=[UP_TO_DATE, off], actions=["press_toggle"])
    posted = [r for r in result["requests"]
              if r["path"] == "/api/updates/settings"]
    assert len(posted) == 1
    assert posted[0]["body"] == {"enabled": False}

    before, after = step(result, "loaded"), step(result, "press_toggle")
    assert before["toggle_pressed"] == "true"
    assert after["toggle_pressed"] == "false"
    assert ">Off<" in after["html"]


def test_a_refused_toggle_does_not_show_the_state_it_wanted():
    """Turning it off and being refused must leave it reading On — a control
    that reports what it asked for rather than what happened is the lie this
    guards."""
    result = run(answers=[UP_TO_DATE], actions=["press_toggle"],
                 fail_on="/api/updates/settings")
    after = step(result, "press_toggle")
    assert after["toggle_pressed"] == "true", \
        "the toggle moved on a request the server refused"
    assert result["toasts"]
