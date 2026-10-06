"""The Backup screen, executed rather than read.

`web/CLAUDE.md` requires a new render path and a new click handler to be run in
a test, because `node --check` passes on every failure that actually ships: a
handler bound to an id the renderer never emitted, a bar frozen at 0% because
its phase has no total, a passphrase left in a DOM node, a cancellation painted
in the red of a failure.

Each test drives `tests/js/backup_screen.mjs`, which renders with the real
`bkRender` and resolves ids **out of the markup that renderer produced** — so a
selector the screen does not emit finds nothing here, exactly as in a browser.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "chitragupta" / "web"
HARNESS = ROOT / "tests" / "js" / "backup_screen.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

NOT_SET_UP = {"set_up": False, "recovery_code_saved": False,
              "automatic": {"enabled": False, "possible": False,
                            "reason": "Automatic backups are off.",
                            "every_hours": 24, "keep": 7},
              "default_dir": "/tmp/bk", "backups": [],
              "withheld": {"google_token.json": "a Google refresh token",
                           "telegram": "a Telegram session"}}
SET_UP_UNSAVED = {**NOT_SET_UP, "set_up": True}
SET_UP_SAVED = {**NOT_SET_UP, "set_up": True, "recovery_code_saved": True}


def run(**scenario) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS), str(WEB)],
        input=json.dumps(scenario), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, f"harness failed:\n{out.stdout}\n{out.stderr}"
    result = json.loads(out.stdout)
    assert "harness_error" not in result, result["harness_error"]
    # Anti-vacuity: a harness that rendered nothing would pass every assertion
    # below that looks for an absence.
    assert result["steps"][0]["ids"], "the screen rendered no elements at all"
    return result


def step(result: dict, label: str) -> dict:
    for s in result["steps"]:
        if s["label"] == label:
            return s
    raise AssertionError(f"no step {label!r} in {[s['label'] for s in result['steps']]}")


def posts(result: dict, path: str) -> list[dict]:
    return [r for r in result["requests"]
            if r["path"] == path and r["method"] == "POST"]


# ── the screen draws its own controls ───────────────────────────────────────

def test_every_control_the_handlers_bind_to_is_actually_rendered():
    """The failure this guards: `bkBind` reaching for an id `bkRender` renamed."""
    ids = set(step(run(state=NOT_SET_UP), "rendered")["ids"])
    for needed in ("bkPhrase", "bkPhraseHint", "bkPath", "bkStart", "bkStop",
                   "bkProgress", "bkBarFill", "bkPhase", "bkOutcome",
                   "bkRestorePath", "bkInspect", "bkInspected"):
        assert needed in ids, f"{needed} is bound by a handler and never drawn"


def test_what_is_withheld_is_named_before_anything_is_asked_for():
    """Tier 0 is on the first screen, not discovered after a restore."""
    body = run(state=NOT_SET_UP)
    rendered = step(body, "rendered")
    assert rendered["code_tags"] >= 2, "each withheld item should be named"


# ── the standing recovery-code warning ──────────────────────────────────────

def test_no_warning_before_backup_is_set_up():
    assert step(run(state=NOT_SET_UP), "rendered")["has_warning"] is False


def test_a_warning_stands_until_the_code_is_confirmed_saved():
    result = run(state=SET_UP_UNSAVED, actions=["press_saved"],
                 responses={"/api/backup/recovery-code/saved":
                            {"recovery_code_saved": True},
                            "/api/backup/state": SET_UP_SAVED})
    assert step(result, "rendered")["has_warning"] is True
    assert result["#bkSaved_existed"] is True, \
        "the warning's button must be bound, not just drawn"
    assert posts(result, "/api/backup/recovery-code/saved")
    # Re-read state and repaint: the warning must go once it is answered.
    assert step(result, "press_saved")["has_warning"] is False


def test_no_warning_once_the_code_is_saved():
    assert step(run(state=SET_UP_SAVED), "rendered")["has_warning"] is False


# ── the passphrase gate ─────────────────────────────────────────────────────

def test_a_short_passphrase_says_how_much_more_and_sends_nothing():
    result = run(state=NOT_SET_UP,
                 actions=["type_short_passphrase", "press_backup"])
    hint = step(result, "type_short_passphrase")
    assert "more character" in hint["hint_text"]
    assert hint["hint_colour"], "a refusal must be visibly a refusal"
    assert not posts(result, "/api/backup/start"), \
        "a backup must not start on a passphrase the screen rejected"


def test_a_good_passphrase_starts_a_backup_and_is_then_dropped():
    result = run(state=NOT_SET_UP, actions=["type_passphrase", "press_backup"],
                 responses={"/api/backup/start": {"running": True,
                                                  "recovery_code": None,
                                                  "first_backup": False}})
    sent = posts(result, "/api/backup/start")
    assert len(sent) == 1
    assert sent[0]["body"]["passphrase"] == "a strong passphrase"
    assert step(result, "press_backup")["phrase_field"] == "", \
        "there is no reason for a passphrase to stay in the DOM afterwards"


# ── the recovery code, shown once ───────────────────────────────────────────

def test_the_code_is_shown_when_the_server_sends_one():
    code = "ABCD-EFGH-JKMN-PQRS-TVWX-YZ23-4567-89AB"
    result = run(state=NOT_SET_UP, actions=["type_passphrase", "press_backup"],
                 responses={"/api/backup/start": {"running": True,
                                                  "recovery_code": code,
                                                  "first_backup": True}})
    after = step(result, "press_backup")
    assert after["code_modal_hidden"] is False
    assert after["code_shown"] == code


def test_no_code_modal_on_a_later_backup():
    result = run(state=SET_UP_SAVED, actions=["type_passphrase", "press_backup"],
                 responses={"/api/backup/start": {"running": True,
                                                  "recovery_code": None,
                                                  "first_backup": False}})
    assert step(result, "press_backup")["code_shown"] == ""


def test_confirming_the_code_records_it_and_clears_it_from_the_page():
    code = "ABCD-EFGH-JKMN-PQRS-TVWX-YZ23-4567-89AB"
    result = run(state=NOT_SET_UP,
                 actions=["type_passphrase", "press_backup", "press_code_done"],
                 responses={"/api/backup/start": {"running": True,
                                                  "recovery_code": code,
                                                  "first_backup": True},
                            "/api/backup/recovery-code/saved":
                                {"recovery_code_saved": True},
                            "/api/backup/state": SET_UP_SAVED})
    assert posts(result, "/api/backup/recovery-code/saved")
    done = step(result, "press_code_done")
    assert done["code_modal_hidden"] is True
    assert done["code_shown"] == "", \
        "the code must not stay in the DOM once the dialog is closed"


def test_copy_puts_the_code_on_the_clipboard():
    code = "ABCD-EFGH-JKMN-PQRS-TVWX-YZ23-4567-89AB"
    result = run(state=NOT_SET_UP,
                 actions=["type_passphrase", "press_backup", "press_code_copy"],
                 responses={"/api/backup/start": {"running": True,
                                                  "recovery_code": code,
                                                  "first_backup": True}})
    assert result["clipboard"] == [code]


def test_a_refused_clipboard_tells_the_user_to_copy_it_by_hand():
    """The code must still be gettable when the clipboard API says no."""
    code = "ABCD-EFGH-JKMN-PQRS-TVWX-YZ23-4567-89AB"
    result = run(state=NOT_SET_UP, clipboard_fails=True,
                 actions=["type_passphrase", "press_backup", "press_code_copy"],
                 responses={"/api/backup/start": {"running": True,
                                                  "recovery_code": code,
                                                  "first_backup": True}})
    assert result["clipboard"] == []
    assert any("by hand" in t for t in result["toasts"]), result["toasts"]
    # and it is still on screen to select
    assert step(result, "press_code_copy")["code_shown"] == code


# ── progress ────────────────────────────────────────────────────────────────

def test_a_countable_phase_draws_a_real_bar():
    result = run(state=SET_UP_SAVED, actions=["sync"],
                 responses={"/api/backup/status": {
                     "running": True, "kind": "backup", "result": None,
                     "error": "", "cancelled": False,
                     "progress": {"phase": "Encrypting", "detail": "",
                                  "done": 3, "total": 4, "fraction": 0.75}}})
    s = step(result, "sync")
    assert s["bar_width"] == "75%"
    assert s["bar_indeterminate"] is False
    assert "Encrypting" in s["phase_text"] and "3/4" in s["phase_text"]
    assert s["stop_hidden"] is False, "a running job must be stoppable"
    assert s["start_disabled"] is True


def test_a_phase_with_no_total_is_a_stripe_not_a_bar_at_zero():
    """`Progress.fraction` returns 0.0 rather than inventing a percentage, so
    the screen must not draw that as a bar frozen at the start."""
    result = run(state=SET_UP_SAVED, actions=["sync"],
                 responses={"/api/backup/status": {
                     "running": True, "kind": "backup", "result": None,
                     "error": "", "cancelled": False,
                     "progress": {"phase": "Compressing", "detail": "",
                                  "done": 0, "total": 0, "fraction": 0.0}}})
    s = step(result, "sync")
    assert s["bar_indeterminate"] is True
    assert "Compressing" in s["phase_text"]
    assert "0/0" not in s["phase_text"], "a count of 0/0 tells the user nothing"


def test_a_finished_backup_says_where_it_went():
    result = run(state=SET_UP_SAVED, actions=["sync"],
                 responses={"/api/backup/status": {
                     "running": False, "kind": "backup", "cancelled": False,
                     "error": "", "progress": {}, "finished": 1760000000.0,
                     "result": {"path": "/tmp/bk/one.cgarch", "bytes": 2048,
                                "summary": "13 item(s), 0.1 MB",
                                "manifest": [], "withheld": {}}},
                     "/api/backup/state": SET_UP_SAVED})
    s = step(result, "sync")
    assert "/tmp/bk/one.cgarch" in s["outcome_text"]
    assert s["outcome_colour"] == "", "a success must not be painted as an error"
    assert s["progress_hidden"] is True
    assert s["stop_hidden"] is True


def test_stopping_is_not_reported_as_a_failure():
    """Stopping is the user succeeding at something they asked for."""
    result = run(state=SET_UP_SAVED, actions=["sync"],
                 responses={"/api/backup/status": {
                     "running": False, "kind": "backup", "cancelled": True,
                     "error": "", "result": None, "progress": {},
                     "finished": 1760000000.0}})
    s = step(result, "sync")
    assert "Stopped" in s["outcome_text"]
    assert s["outcome_colour"] == "", \
        "a cancellation in the colour of an error is a lie about what happened"


def test_a_failure_is_reported_as_one():
    result = run(state=SET_UP_SAVED, actions=["sync"],
                 responses={"/api/backup/status": {
                     "running": False, "kind": "backup", "cancelled": False,
                     "error": "could not write a backup to /nope",
                     "result": None, "progress": {}, "finished": 1760000000.0}})
    s = step(result, "sync")
    assert "could not write" in s["outcome_text"]
    assert s["outcome_colour"], "a failure must be visibly a failure"


# ── inspect, then restore ───────────────────────────────────────────────────

INSPECTED = {
    "path": "/tmp/x.cgarch", "created_at": "2026-10-05T12:00:00+00:00",
    "app_version": "0.1.0", "bytes": 4096,
    "manifest": [{"name": "chitragupta.db", "kind": "database", "bytes": 100}],
    "withheld": {"google_token.json": "a Google refresh token"},
    "reconnect_needed": ["google_token.json"],
    "unlock_methods": ["passphrase", "recovery"],
}


def test_looking_at_a_backup_needs_no_secret_and_names_the_reconnects():
    result = run(state=SET_UP_SAVED, actions=["press_inspect"],
                 responses={"/api/backup/inspect": INSPECTED})
    sent = posts(result, "/api/backup/inspect")
    assert len(sent) == 1
    assert "passphrase" not in (sent[0]["body"] or {}), \
        "inspect must not require a secret"
    html = step(result, "press_inspect")["inspected_html"]
    assert "google_token.json" in html
    assert "bkRestoreGo" in html
    assert "bkRestoreCode" in html, \
        "a backup with a recovery wrap must offer the code as a way in"


def test_a_backup_with_no_recovery_wrap_offers_only_a_passphrase():
    only_phrase = {**INSPECTED, "unlock_methods": ["passphrase"]}
    result = run(state=SET_UP_SAVED, actions=["press_inspect"],
                 responses={"/api/backup/inspect": only_phrase})
    html = step(result, "press_inspect")["inspected_html"]
    assert "bkRestoreCode" not in html, \
        "never offer a way in that this file does not have"


def test_a_file_that_cannot_be_read_says_so_in_place():
    result = run(state=SET_UP_SAVED, actions=["press_inspect"],
                 responses={"/api/backup/inspect":
                            {"__status": 400,
                             "__detail": "this is not a Chitragupta backup"}})
    html = step(result, "press_inspect")["inspected_html"]
    assert "not a Chitragupta backup" in html


def test_restore_sends_the_path_and_the_secret():
    result = run(state=SET_UP_SAVED,
                 actions=["press_inspect", "press_restore"],
                 responses={"/api/backup/inspect": INSPECTED,
                            "/api/backup/restore": {"running": True}})
    assert result["#bkRestoreGo_existed"] is True
    # The field was never typed into, so the screen should refuse rather than
    # send an empty secret.
    assert not posts(result, "/api/backup/restore"), \
        "a restore with no secret must not reach the server"
    assert result["toasts"], "and the user must be told why"


def test_a_finished_job_is_reported_once_and_does_not_loop():
    """The regression for a mutual recursion that hammered the server.

    `bkSync` finished a backup, called `bkLoad` to pick up the new file, and
    `bkLoad` ended by calling `bkSync` to join any running job — which saw the
    same finished job and called `bkLoad` again. Each turn made two network
    requests, so in a browser the symptom was not a hang but a tight loop. The
    harness hit its own 120-second timeout, which is how it was found.

    Two `sync` presses here, and the completion must be handled exactly once.
    """
    status = {"running": False, "kind": "backup", "cancelled": False,
              "error": "", "progress": {}, "finished": 1760000000.0,
              "result": {"path": "/tmp/bk/one.cgarch", "bytes": 2048,
                         "summary": "13 item(s)", "manifest": [], "withheld": {}}}
    result = run(state=SET_UP_SAVED, actions=["sync", "sync"],
                 responses={"/api/backup/status": status,
                            "/api/backup/state": SET_UP_SAVED})
    states = [r for r in result["requests"] if r["path"] == "/api/backup/state"]
    assert len(states) <= 2, (
        f"the screen re-read its state {len(states)} times for one finished "
        f"job — the recursion is back")


# ── the native file picker ──────────────────────────────────────────────────

def test_no_choose_button_without_the_desktop_bridge():
    """`chitragupta serve` has no pywebview, so the button must not be drawn.

    `/CLAUDE.md`: never show a control that cannot work. The path field is still
    there, so nothing becomes unreachable.
    """
    ids = set(step(run(state=SET_UP_SAVED), "rendered")["ids"])
    assert "bkPickOpen" not in ids
    assert "bkPickSave" not in ids
    assert "bkRestorePath" in ids, "the typed path must remain the fallback"
    assert "bkPath" in ids


def test_the_choose_buttons_appear_in_the_desktop_app():
    ids = set(step(run(state=SET_UP_SAVED, bridge={}), "rendered")["ids"])
    assert "bkPickOpen" in ids
    assert "bkPickSave" in ids


def test_picking_a_backup_fills_the_field_and_inspects_it():
    """One press should do the whole thing — pick, then show what is inside."""
    result = run(state=SET_UP_SAVED,
                 bridge={"open": "/Users/x/Desktop/mine.cgarch"},
                 actions=["press_pick_open"],
                 responses={"/api/backup/inspect": INSPECTED})
    assert result["picks"] == ["open"]
    after = step(result, "press_pick_open")
    assert after["restore_path_field"] == "/Users/x/Desktop/mine.cgarch"
    sent = posts(result, "/api/backup/inspect")
    assert len(sent) == 1
    assert sent[0]["body"]["path"] == "/Users/x/Desktop/mine.cgarch"
    assert "bkRestoreGo" in after["inspected_html"]


def test_picking_a_destination_fills_the_field_without_inspecting():
    result = run(state=SET_UP_SAVED,
                 bridge={"save": "/Users/x/Desktop/out.cgarch"},
                 actions=["press_pick_save"])
    assert step(result, "press_pick_save")["save_path_field"] \
        == "/Users/x/Desktop/out.cgarch"
    assert not posts(result, "/api/backup/inspect"), \
        "a save destination is not a file to read"


def test_the_save_panel_is_offered_a_dated_filename():
    """Twelve files all called chitragupta-backup.cgarch is a folder nobody can
    read, so the suggestion carries the date and time."""
    result = run(state=SET_UP_SAVED, bridge={"save": "/tmp/a.cgarch"},
                 actions=["press_pick_save"])
    assert len(result["picks"]) == 1
    suggested = result["picks"][0].split("save:", 1)[1]
    assert suggested.startswith("chitragupta-")
    assert suggested.endswith(".cgarch")
    assert any(ch.isdigit() for ch in suggested)


def test_cancelling_the_panel_leaves_a_typed_path_alone():
    """An empty answer is the user cancelling, which must not wipe their input."""
    result = run(state=SET_UP_SAVED, bridge={"open": ""},
                 actions=["press_pick_open"])
    assert result["picks"] == ["open"]
    assert not posts(result, "/api/backup/inspect"), \
        "cancelling must not inspect an empty path"
    assert step(result, "press_pick_open")["restore_path_field"] == ""


def test_a_panel_that_fails_says_to_type_the_path():
    result = run(state=SET_UP_SAVED, bridge={"open": "/tmp/x.cgarch"},
                 bridge_throws=True, actions=["press_pick_open"])
    assert any("type the path" in t for t in result["toasts"]), result["toasts"]


# ── backing up on its own ───────────────────────────────────────────────────

AUTO_OFF = {"enabled": False, "possible": False, "due": False,
            "reason": "Automatic backups are off.", "every_hours": 24,
            "keep": 7, "last_at": 0, "last_path": ""}
AUTO_ON = {**AUTO_OFF, "enabled": True, "possible": True,
           "reason": "Last backup 2 hours ago."}
AUTO_BLOCKED = {**AUTO_OFF, "enabled": True,
                "reason": "Back up once with your passphrase to let this run "
                          "on its own."}


def test_the_automatic_switch_is_offered_before_a_first_backup_exists():
    """Turning it on first is a reasonable thing to do; the reason line then
    says what is still needed, rather than the control being absent."""
    rendered = step(run(state={**NOT_SET_UP, "automatic": AUTO_OFF}), "rendered")
    assert "bkAutoToggle" in rendered["ids"]


def test_off_and_cannot_run_yet_are_not_the_same_sentence():
    """One sentence for both is how a user concludes the app is broken. The
    server owns the wording; the screen only draws it."""
    off = step(run(state={**SET_UP_SAVED, "automatic": AUTO_OFF}), "rendered")
    blocked = step(run(state={**SET_UP_SAVED, "automatic": AUTO_BLOCKED}),
                   "rendered")
    assert "Automatic backups are off." in off["body_html"]
    assert "Back up once with your passphrase" in blocked["body_html"]
    assert "Automatic backups are off." not in blocked["body_html"]


def test_the_interval_and_keep_controls_appear_only_when_it_is_on():
    off = step(run(state={**SET_UP_SAVED, "automatic": AUTO_OFF}), "rendered")
    on = step(run(state={**SET_UP_SAVED, "automatic": AUTO_ON}), "rendered")
    assert "bkAutoHours" not in off["ids"]
    assert "bkAutoHours" in on["ids"]
    assert "bkAutoKeep" in on["ids"]


def test_it_says_that_pruning_leaves_your_own_files_alone():
    """The feature deletes files on a timer, so it has to say what it will not
    touch."""
    on = step(run(state={**SET_UP_SAVED, "automatic": AUTO_ON}), "rendered")
    assert "left alone" in on["body_html"]


def test_turning_it_on_sends_only_that_field():
    """One request per change, so a switch cannot clobber the two dropdowns
    with stale values."""
    result = run(state={**SET_UP_SAVED, "automatic": AUTO_OFF},
                 actions=["press_auto_toggle"],
                 responses={"/api/backup/automatic": AUTO_ON})
    sent = posts(result, "/api/backup/automatic")
    assert len(sent) == 1
    assert sent[0]["body"] == {"enabled": True}
    assert step(result, "press_auto_toggle")["auto_pressed"] == "true"


def test_a_refused_change_does_not_show_the_state_it_wanted():
    result = run(state={**SET_UP_SAVED, "automatic": AUTO_OFF},
                 actions=["press_auto_toggle"],
                 responses={"/api/backup/automatic":
                            {"__status": 500, "__detail": "nope"}})
    assert step(result, "press_auto_toggle")["auto_pressed"] == "false", \
        "the switch moved on a request the server refused"
    assert result["toasts"]
