"""The Details panel renders, and every control in it actually does something.

This is the screen that answers the four questions `/CLAUDE.md` says a user
must be able to answer about a connected source — *what was imported, from
which account, when, and can I stop it* — and none of them had a screen before,
because nothing underneath could answer them.

`node --check` passes on every failure pinned here, which is why the panel is
executed rather than grepped:

* a handler bound to markup that was never written into the container;
* a `data-` attribute the renderer spells one way and the binder another;
* a body sent as a JSON **string** — `_encodeBody` only adds the JSON header
  for an object, so a stringified body arrives as `text/plain` and FastAPI
  answers 422. That is the bug `core.js` records against `/api/open-browser`
  and that the Disconnect button shipped with: the dialog appeared, OK did
  nothing, and nothing said why;
* a capability shown as `send:email`, which is an internal on a screen.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = ROOT / "chitragupta" / "web" / "app.js"
HARNESS = ROOT / "tests" / "js" / "connector_details.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

MANIFEST = {
    "connector_id": "gmail", "display_name": "Gmail", "provider": "google",
    "reads": ["read:email", "search:email"],
    "writes": ["create:draft", "send:email", "delete:draft"],
    "required_scopes": ["gmail.readonly", "gmail.send"],
    "limits": {"records_per_sync": 600, "requests": 240,
               "imposed_by": "Chitragupta"},
}

DATA = {
    "connector": "gmail", "label": "Gmail",
    "accounts": [{
        "connection": {"id": "gmail:a1b2", "display": "Work",
                       "account": "me@work.test", "paused": False,
                       "says": "Connected."},
        "counts": {"total": 482, "active": 480, "deleted": 2},
        "sync": [],
    }],
}

HEALTH = {"connectors": [{
    "connection_id": "gmail:a1b2", "connector": "gmail", "account": "Work",
    "state": "healthy", "says": "Up to date.", "ok": True,
    "needs_the_user": False, "last_success": "2026-09-26T09:00:00+00:00",
    "next_sync": "on the sync timer", "items": 482, "errors": [],
}]}


def run(**scenario) -> dict:
    payload = {"name": "gmail", "label": "Gmail", "manifest": MANIFEST,
               "data": DATA, "health": HEALTH, **scenario}
    out = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"harness failed:\n{out.stderr}"
    result = json.loads(out.stdout)
    assert result["ok"], f"the panel threw: {result['error']}"
    return result


# ── it renders ────────────────────────────────────────────────────────────


def test_the_panel_opens_and_names_the_account():
    found = run()

    assert "Work" in found["html"]
    assert found["title"] == "Gmail"


def test_it_says_what_was_imported_and_what_became_of_it():
    """The question that had no answer, because nothing tied a memory to the
    record it came from."""
    found = run()

    assert "482 items imported" in found["html"]
    assert "2 since removed at the source" in found["html"]


def test_it_says_when_and_what_comes_next():
    found = run()

    assert "last updated" in found["html"]
    assert "on the sync timer" in found["html"]


def test_it_separates_what_can_be_read_from_what_can_be_changed():
    found = run()

    assert "Can reach" in found["html"]
    assert "Can change" in found["html"]


def test_a_capability_is_a_sentence_not_an_identifier():
    """`send:email` is the machine's spelling. A person reads "Send email"."""
    found = run()

    assert not found["showsRawCapability"], found["html"]
    assert found["capabilitySentence"] == "Send email"
    assert "Send email" in found["html"]


def test_the_limits_are_ours_and_say_so():
    found = run()

    assert "Those are our limits" in found["html"]
    assert "600 records" in found["html"]


def test_a_source_with_nothing_imported_still_renders():
    """A control panel that disappears when there is nothing in it is a
    control panel the user cannot use to fix that."""
    found = run(data={"connector": "gmail", "label": "Gmail", "accounts": []})

    assert "Nothing has been imported" in found["html"]
    assert found["ok"]


def test_reading_the_panel_never_reaches_the_source():
    """Three calls, all of them reading state we already hold. The expensive
    version is `GET /api/connectors`, which is in the probe lane."""
    found = run()

    asked = {c["url"] for c in found["calls"]}
    assert asked == {"/api/connectors/gmail/manifest",
                     "/api/connectors/gmail/data",
                     "/api/connectors/health"}


# ── every control does something ──────────────────────────────────────────


def test_all_three_controls_are_rendered_and_bound():
    """Rendered is half of it. A button with no handler looks identical."""
    found = run(click="pause")

    assert found["buttons"] == {"pause": 1, "resync": 1, "forget": 1}
    assert found["bound"] == 1, "the Pause button was rendered with no handler"


def test_pausing_posts_to_the_pause_route_for_that_account():
    found = run(click="pause")

    call = found["calls"][0]
    assert call["url"] == "/api/connectors/gmail/pause"
    assert call["method"] == "POST"
    assert json.loads(call["body"])["connection_id"] == "gmail:a1b2"


def test_a_paused_account_offers_resume_instead():
    paused = json.loads(json.dumps(DATA))
    paused["accounts"][0]["connection"]["paused"] = True

    found = run(data=paused, click="pause")

    assert found["pauseLabel"] == "Resume syncing"
    assert found["calls"][0]["url"] == "/api/connectors/gmail/resume"


def test_every_body_is_an_object_not_a_json_string():
    """`_encodeBody` only adds the JSON header for an object. A stringified
    body arrives as text/plain, FastAPI answers 422, and the dialog silently
    does nothing — the exact bug the Disconnect button shipped with."""
    for control in ("pause", "resync"):
        found = run(click=control)
        for call in found["calls"]:
            if call["method"] == "POST":
                assert call["bodyType"] == "string", (
                    "the harness sees what fetch received, which is always a "
                    "string by then")
                # What matters is that `api()` did the encoding, which it only
                # does for an object — a hand-stringified body would arrive
                # here byte-identical but with no JSON header set.
                assert json.loads(call["body"]) == {"connection_id": "gmail:a1b2"}


def test_syncing_everything_again_asks_first_and_promises_no_deletion():
    found = run(click="resync")

    assert found["calls"][0]["url"] == "/api/connectors/gmail/resync"
    assert any("read everything again" in t for t in found["toasts"])


def test_deleting_the_data_names_the_account_it_is_deleting_for():
    """With two accounts, a delete that did not name one would take the wrong
    account's data."""
    found = run(click="forget")

    call = found["calls"][0]
    assert call["method"] == "DELETE"
    assert "connection_id=gmail%3Aa1b2" in call["url"]


def test_declining_the_confirmation_sends_nothing():
    """A destructive control that fires on Cancel is worse than one that does
    not exist."""
    found = run(click="forget", confirm=False)

    assert found["calls"] == []


def test_a_control_reports_what_happened_and_refreshes():
    found = run(click="forget")

    assert found["toasts"], "the user was told nothing"
    assert found["reloaded"], "the screen still shows the old numbers"
