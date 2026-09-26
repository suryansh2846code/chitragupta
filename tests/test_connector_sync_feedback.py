"""Pressing Sync tells the user something, and pressing it twice syncs once.

`syncConn` reached for `.conn`, `.conn-sub` and `.dot`. The row renders
`.cn-row`, `.cn-sub` and a `.cn-logo` carrying `data-state`. `.conn` survived
only as a leftover CSS rule, so the lookup returned null — and because every
following line used `?.`, **all three pieces of feedback silently did nothing**:

* "syncing…" never appeared, so a first Gmail pass looked like a dead button for
  minutes;
* the Sync button was never disabled, so a second click started a second sync;
* an error never reached the row, only a toast that scrolls away.

Every one of those passes `node --check`, and a source-order assertion would
have passed too. Only clicking it finds them, which is what `tests/js/` exists
for and what `web/CLAUDE.md` requires of a new click handler.

The second half is the health row. The row used to work out staleness from a
timestamp and could therefore say exactly three things. It now shows what the
server says — including the two states a timestamp can never imply: a sign-in
that has run out, which the user must act on, and a service rate-limiting us,
which they must not.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = ROOT / "chitragupta" / "web" / "app.js"
HARNESS = ROOT / "tests" / "js" / "connector_sync_feedback.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

GMAIL = {
    "name": "gmail", "label": "Gmail", "ready": True, "kind": "builtin",
    "on_device": False, "state": {"last_sync": "2026-09-26T09:00:00+00:00",
                                  "status": "ok"},
}


def health(state, says, ok=False):
    return [{"connector": "gmail", "connection_id": "gmail:a1", "account": "Work",
             "state": state, "says": says, "ok": ok, "needs_the_user": False,
             "last_success": "2026-09-26T09:00:00+00:00", "items": 12,
             "next_sync": "on the sync timer", "errors": []}]


def run(**scenario) -> dict:
    payload = {"connector": GMAIL, **scenario}
    out = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"harness failed:\n{out.stderr}"
    result = json.loads(out.stdout)
    assert result["ok"], f"the row or the click threw: {result['error']}"
    return result


# ── the row is findable at all ────────────────────────────────────────────


def test_sync_finds_the_row_it_is_supposed_to_update():
    """The whole bug in one assertion. `.conn` matched nothing."""
    found = run()

    assert found["rowFound"], (
        "syncConn could not find the row — its selector does not match what "
        "_cnRowHtml renders, so every piece of feedback below is a no-op")


# ── what the user sees while it runs ──────────────────────────────────────


def test_the_row_says_it_is_syncing_while_it_syncs():
    found = run(hold=True)

    assert found["midSub"] == "Syncing…"


def test_the_mark_stays_on_the_logo_while_it_syncs():
    """Without a `syncing` rule the mark vanished mid-sync, so the busiest
    moment was the one that looked disconnected."""
    found = run(hold=True)

    assert found["midLogoState"] == "syncing"


def test_the_button_is_shut_while_it_runs():
    found = run(hold=True)

    assert found["midButtonDisabled"] is True


def test_a_second_press_does_not_start_a_second_sync():
    """A first Gmail pass runs for minutes. That is a long time to be able to
    double-fire it."""
    found = run(hold=True)

    assert found["callsDuringHold"] == 1
    assert found["syncCalls"] == 1


def test_the_button_is_usable_again_afterwards():
    found = run()

    assert found["finalButtonDisabled"] is False


# ── what it says when it goes wrong ───────────────────────────────────────


def test_a_failure_puts_the_reason_on_the_row_not_just_in_a_toast():
    """A toast scrolls away; the row is where the user looks next. And the
    reason is the server's sentence, which names what to do — it used to be
    replaced with the word "error"."""
    found = run(syncResult={"added": 0, "skipped": 0,
                            "errors": ["Gmail needs you to sign in again."]})

    assert found["finalSub"] == "Gmail needs you to sign in again."
    assert any("sign in again" in t for t in found["toasts"])


def test_a_successful_sync_refreshes_the_screen():
    found = run()

    assert found["reloaded"]
    assert any("+3 added" in t for t in found["toasts"])


def test_the_body_is_an_object_so_it_is_sent_as_json():
    """`api()` only sets the JSON header for an object. A hand-stringified body
    arrives as text/plain and FastAPI answers 422 — the bug `core.js` records
    against /api/open-browser, which this call had too."""
    found = run()

    posted = [c for c in found["calls"] if c["url"].endswith("/sync")]
    assert posted and posted[0]["method"] == "POST"
    assert posted[0]["hasJsonHeader"], "the body was not encoded as JSON"


def test_local_files_opens_the_folder_picker_instead():
    found = run(connector={**GMAIL, "name": "files", "label": "Local Files"})

    assert found["picker"]
    assert found["syncCalls"] == 0


# ── the row shows what the server says ────────────────────────────────────


def test_a_sign_in_that_ran_out_is_said_on_the_row():
    """The state a timestamp can never imply, and the one the user has to act
    on. Before this the row read "Connected · last synced 26 Sep"."""
    found = run(health=health("auth_required",
                              "Sign in to Gmail again to keep this up to date."))

    assert "Sign in to Gmail again" in found["html"]
    assert "Sign in again" in found["html"], "and the badge says so too"


def test_being_rate_limited_does_not_read_as_something_to_fix():
    """It clears by itself. A row that said "error" would send the user looking
    for a problem that is not theirs."""
    found = run(health=health(
        "rate_limited",
        "Gmail is asking us to slow down. This will pick up on its own."))

    assert "pick up on its own" in found["html"]
    assert "Not working" not in found["html"]


def test_a_healthy_source_still_reads_as_connected():
    found = run(health=health("healthy", "Up to date.", ok=True))

    assert "Connected" in found["html"]
    assert "is-stale" not in found["html"]


def test_a_source_the_server_knows_nothing_about_falls_back_to_the_timestamp():
    """Most of the list on a first run. Deleting the fallback would leave every
    unconnected row blank."""
    found = run(health=[])

    assert found["rowFound"]
    assert "Connected" in found["html"]


def test_an_unknown_state_from_a_newer_server_is_not_a_blank_row():
    """Reading it as "not working" is the conservative answer; rendering
    nothing would be a row that says the source is fine."""
    found = run(health=health("something_new", "Something we do not know."))

    assert "Not working" in found["html"]
