"""The allow-list where a person can see and change it.

The list is the entire consent model for the browser, so it has to live
somewhere a user would look — `docs/BROWSER.md`: *"a logged-in site is a
connection, and the Connectors panel must say so"*. Anything else and there is no
single screen answering "what can this app reach on my behalf".

Three things have to hold and none is visible in the markup:

* adding and removing actually call the API, with the address the user typed
* a refused address shows **the server's sentence** — "only https addresses can
  be used" tells somebody what to change; a 400 does not
* the setup button appears only when there is something to set up *and* a way to
  drive it. `drivable: false` is a build that can store the list but not open a
  page, and a button that cannot work reads as the app being broken
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

SITE = {"origin": "https://payroll.example.com", "host": "payroll.example.com",
        "may_read": True, "may_act": False, "note": ""}

BASE = {"installed": False, "drivable": False, "state": "idle", "message": "",
        "percent": 0, "error": "", "approx_mb": 150, "sites": [SITE]}


def _run(status, add_error=None):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/browser_sites.mjs"), str(WEB / "app.js")],
        input=json.dumps({"status": status, "addError": add_error}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def panel():
    return _run(BASE)


def test_it_rendered(panel):
    assert panel["error"] is None, panel["error"]


# ── the list ────────────────────────────────────────────────────────────
def test_an_allowed_site_is_listed_with_what_it_allows(panel):
    """"read only" said out loud on every row, rather than implied by the
    absence of anything else."""
    assert "payroll.example.com" in panel["rendered"]["list"]
    assert "read only" in panel["rendered"]["list"]


def test_a_site_that_may_be_changed_says_so_differently():
    """Nothing grants this today. The row has to be honest the day something
    does, or a standing write permission would be invisible."""
    acting = {**BASE, "sites": [{**SITE, "may_act": True}]}

    assert "read &amp; change" in _run(acting)["rendered"]["list"]


def test_nothing_allowed_is_stated_rather_than_left_blank():
    """An empty box reads as a failure to load. This is the correct starting
    state and the panel says so."""
    out = _run({**BASE, "sites": []})

    assert "No sites yet" in out["rendered"]["list"]
    assert "cannot open any page" in out["rendered"]["list"]


# ── using it ────────────────────────────────────────────────────────────
def test_adding_a_site_posts_what_was_typed(panel):
    assert panel["added"] == "ok", panel["added"]
    posts = [c for c in panel["addCalls"]
             if c["url"] == "/api/browser/sites" and c["method"] == "POST"]
    assert [p["body"]["url"] for p in posts] == ["payroll.example.com"]


def test_adding_clears_the_field_and_says_what_happened(panel):
    assert panel["inputAfter"] == ""
    assert any("can now read" in t for t in panel["toasts"]), panel["toasts"]


def test_removing_a_site_deletes_it_by_host(panel):
    assert panel["removed"] == "ok", panel["removed"]
    assert any(c["method"] == "DELETE"
               and c["url"].endswith("/payroll.example.com")
               for c in panel["removeCalls"]), panel["removeCalls"]


def test_removing_says_what_it_means_not_what_it_did():
    """"Agents can no longer read x" is the consequence; "deleted" is the
    mechanism."""
    out = _run(BASE)

    assert any("no longer read" in t for t in out["toasts"]), out["toasts"]


def test_a_refused_address_shows_the_servers_own_sentence():
    """The whole point of returning prose from the API. A person who typed
    `http://…` needs to be told which part to change."""
    out = _run(BASE, add_error="only https addresses can be used — http traffic "
                               "can be read and changed on the way")

    assert out["errHidden"] is False
    assert "only https addresses" in out["errText"]


def test_a_failed_add_does_not_clear_what_was_typed():
    """Making somebody retype an address we rejected is punishing them for our
    error message."""
    out = _run(BASE, add_error="that is not a website address")

    assert out["inputAfter"] == "payroll.example.com"


# ── setup, and the button that must not appear ──────────────────────────
def test_a_build_that_cannot_drive_a_browser_offers_no_setup_button(panel):
    """`/CLAUDE.md`: never show a control that cannot work."""
    assert panel["rendered"]["setupHidden"] is True
    assert "Opening pages is coming" in panel["rendered"]["stateText"]


def test_the_list_still_works_in_a_build_that_cannot_drive(panel):
    """The grants are stored either way, so the section is useful before the
    download exists — which is why it is not hidden wholesale."""
    assert "payroll.example.com" in panel["rendered"]["list"]


def test_when_drivable_the_size_is_said_before_anything_downloads():
    out = _run({**BASE, "drivable": True})

    assert out["rendered"]["setupHidden"] is False
    assert "Set up browsing" in out["rendered"]["setupText"]
    assert "150" in out["rendered"]["stateText"]
    assert "One-time" in out["rendered"]["stateText"]


def test_a_running_download_shows_progress_not_a_bare_spinner():
    out = _run({**BASE, "drivable": True, "state": "running",
                "message": "Downloading…", "percent": 42})

    assert out["rendered"]["setupHidden"] is True
    assert "Downloading…" in out["rendered"]["stateText"]
    assert "42%" in out["rendered"]["stateText"]


def test_a_failed_download_can_be_retried():
    """A dead end with no button is the failure that gets reported as "it just
    doesn't work"."""
    out = _run({**BASE, "drivable": True, "state": "error",
                "message": "Could not set up the browser: network unreachable",
                "error": "network unreachable"})

    assert out["rendered"]["setupHidden"] is False
    assert "Try again" in out["rendered"]["setupText"]
    assert "network unreachable" in out["rendered"]["stateText"]


def test_a_finished_setup_stops_talking_about_itself():
    out = _run({**BASE, "drivable": True, "installed": True, "state": "done"})

    assert out["rendered"]["setupHidden"] is True
    assert out["rendered"]["stateHidden"] is True


# ── letting an agent change things, which is a second decision ───────────
def test_allowing_changes_is_offered_per_site(panel):
    """Not folded into the press that allowed the site at all. "Let an agent
    read my LinkedIn" and "let an agent type into my LinkedIn" are different
    sentences, and a screen that collapsed them would be asking the second
    while the user answered the first."""
    assert "Allow changes" in panel["rendered"]["list"]


def test_allowing_changes_posts_to_that_sites_own_switch(panel):
    assert panel["acted"] == "ok", panel["acted"]
    posted = [c for c in panel["actCalls"] if c["method"] == "POST"]
    assert posted, panel["actCalls"]
    assert posted[0]["url"].endswith("/payroll.example.com/acting")
    body = posted[0]["body"]
    if isinstance(body, str):
        body = json.loads(body)
    assert body["allowed"] is True


def test_turning_it_on_says_what_it_does_and_does_not_do(panel):
    """The dangerous misreading is "I have let the agent loose on this site".
    Every act still collects a card; what this decides is whether that card may
    ever appear."""
    assert any("can now" in t or "change things" in t for t in panel["toasts"]), (
        panel["toasts"])
