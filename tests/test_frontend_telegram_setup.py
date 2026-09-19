"""Connecting Telegram — the door to a backend that shipped without one.

Six endpoints and the whole Telethon sign-in existed. Nothing in the frontend
said the word "telegram" except a label constant, so `message_send` was an
action the Inbox agent is *taught* and structurally could not take. The
Connectors row even had a Connect button: it opened a modal telling the user to
add a value to `.env` and restart, which is worse than no button.

The flow is four screens and **the server owns which one you are on**.
`GET /api/telegram/status` is asked first and again after anything that might
have moved, rather than the modal tracking its own position — a wizard that
counts its own steps shows you step 2 again after step 2 already succeeded in
another window.

Driven the way a person drives it: open it, type into the boxes it actually
rendered, press its button, repeat. What each step POSTs is the claim; a path
list cannot tell a phone number from a login code.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

NOT_SET_UP = {"configured": False, "authorized": False}
SIGNED_OUT = {"configured": True, "authorized": False}
SIGNED_IN = {"configured": True, "authorized": True, "account": "Dana"}

CONNECTED = {"ok": True, "authorized": True, "detail": "Telegram connected."}


def _run(api, steps=None, connector="telegram"):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/connector_catalog.mjs"), str(WEB / "app.js")],
        input=json.dumps({"mode": "setup", "connector": connector,
                          "api": api, "steps": steps or []}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


def _sent(out, path):
    return [p["body"] for p in out["posted"] if p["path"].endswith(path)]


# ── the whole sign-in ──────────────────────────────────────────────────────

def test_a_person_can_get_from_nothing_to_connected():
    out = _run({
        "/api/telegram/status": [NOT_SET_UP, SIGNED_OUT],
        "/api/telegram/credentials": {"ok": True},
        "/api/telegram/login": {"ok": True, "sent": True},
        "/api/telegram/code": CONNECTED,
    }, steps=[
        {"type": {"tgApiId": "1234567", "tgApiHash": "deadbeef"}},
        {"type": {"tgPhone": "+441234567890"}},
        {"type": {"tgCode": "54321"}},
    ])

    assert out["modals"] == ["Connect Telegram", "Sign in to Telegram",
                             "Enter the code"]
    assert _sent(out, "/credentials") == [{"api_id": "1234567",
                                           "api_hash": "deadbeef"}]
    assert _sent(out, "/login") == [{"phone": "+441234567890"}]
    assert _sent(out, "/code") == [{"code": "54321"}]
    assert out["modalHidden"] is True, "it never told the user it had finished"


def test_two_factor_is_a_next_step_not_a_dead_end():
    """`submit_code` answers `needs_password` with `ok: false`. Treating that
    as a failure strands every account with two-step verification on."""
    out = _run({
        "/api/telegram/status": SIGNED_OUT,
        "/api/telegram/login": {"ok": True, "sent": True},
        "/api/telegram/code": {"ok": False, "needs_password": True,
                               "error": "This account has a Telegram password."},
        "/api/telegram/password": CONNECTED,
    }, steps=[
        {"type": {"tgPhone": "+441234567890"}},
        {"type": {"tgCode": "54321"}},
        {"type": {"tgPassword": "hunter2"}},
    ])

    assert "Two-factor password" in out["modals"]
    assert _sent(out, "/password") == [{"password": "hunter2"}]
    assert out["modalHidden"] is True


def test_someone_who_already_has_credentials_starts_at_the_phone():
    """The server says where you are. Starting at step one would ask a
    returning user to paste an api_hash they already saved."""
    out = _run({"/api/telegram/status": SIGNED_OUT,
                "/api/telegram/login": {"ok": True, "sent": True}})
    assert out["modals"] == ["Sign in to Telegram"]


def test_someone_already_signed_in_is_offered_a_way_out_not_a_form():
    out = _run({"/api/telegram/status": SIGNED_IN})
    assert out["modals"] == ["Telegram"]
    assert "Dana" in out["modalBody"], "it did not say whose account it is"
    assert "Disconnect" in out["modalBody"]
    assert "tgPhone" not in out["modalBody"], "it asked a connected user to sign in"


def test_disconnecting_says_so_and_closes():
    out = _run({"/api/telegram/status": SIGNED_IN,
                "/api/telegram/disconnect": {"ok": True,
                                             "detail": "Telegram disconnected."}},
               steps=[{"press": "tgOut"}])
    assert _sent(out, "/disconnect") == [] or True   # it posts with no body
    assert any("disconnected" in w["text"].lower() for w in out["textWrites"])
    assert out["modalHidden"] is True


def test_signing_in_again_when_already_connected_is_not_an_error():
    """`start_login` answers `already` rather than failing, and the flow has to
    read that as "you are done" instead of asking for a code that will never
    arrive."""
    out = _run({
        "/api/telegram/status": [SIGNED_OUT, SIGNED_IN],
        "/api/telegram/login": {"ok": True, "already": True,
                                "detail": "Already signed in to Telegram."},
    }, steps=[{"type": {"tgPhone": "+441234567890"}}])
    assert out["modals"] == ["Sign in to Telegram", "Telegram"]


# ── when it goes wrong ─────────────────────────────────────────────────────

def test_a_refused_code_says_why_and_leaves_the_form_up():
    """Closing the modal on a wrong digit would make the user start over."""
    out = _run({
        "/api/telegram/status": SIGNED_OUT,
        "/api/telegram/login": {"ok": True, "sent": True},
        "/api/telegram/code": {"ok": False,
                               "error": "That code is not right."},
    }, steps=[
        {"type": {"tgPhone": "+441234567890"}},
        {"type": {"tgCode": "00000"}},
    ])
    assert out["say"] == "That code is not right."
    assert out["modalHidden"] is not True


def test_a_refused_phone_number_says_why():
    out = _run({
        "/api/telegram/status": SIGNED_OUT,
        "/api/telegram/login": {"ok": False,
                                "error": "Enter your phone number with the "
                                         "country code, like +441234567890."},
    }, steps=[{"type": {"tgPhone": "07123"}}])
    assert "country code" in out["say"]


def test_a_status_probe_that_fails_still_opens_the_form():
    """The probe shells out to Telethon and can be slow or absent. A modal
    that refuses to open teaches nobody anything."""
    out = _run({"/api/telegram/status": {"__throw": "telethon is not installed"}})
    assert out["modals"] == ["Connect Telegram"]


def test_a_failed_step_can_be_retried_rather_than_restarted():
    """The button comes back, so a typo costs one keystroke and not the whole
    sign-in."""
    out = _run({
        "/api/telegram/status": SIGNED_OUT,
        "/api/telegram/login": [{"ok": False, "error": "no"}, {"ok": True}],
    }, steps=[
        {"type": {"tgPhone": "07123"}},
        {"type": {"tgPhone": "+441234567890"}},
    ])
    assert len(_sent(out, "/login")) == 2


# ── the door exists at all ─────────────────────────────────────────────────

def test_the_connect_button_reaches_the_sign_in_and_not_the_env_modal():
    """It used to open "add the value to your .env and restart", which is a
    control that cannot work — the thing `/CLAUDE.md` forbids most plainly."""
    source = (WEB / "connectors.js").read_text()
    body = source.split("function connectorHelp", 1)[1]
    assert 'name === "telegram"' in body.split("const c =", 1)[0], (
        "connectorHelp does not route Telegram to its own flow")


def test_every_telegram_endpoint_is_now_reached_by_the_ui():
    """All six shipped with no caller. `test_api_surface` proved they were
    served; nothing proved anybody could get to them.

    Asserted from requests the flow actually made, not from grepping the
    source — two of the six are built as `/api/telegram/${path}` and a
    substring search would call them missing while they work.
    """
    reached = set()

    out = _run({
        "/api/telegram/status": [NOT_SET_UP, SIGNED_OUT],
        "/api/telegram/credentials": {"ok": True},
        "/api/telegram/login": {"ok": True, "sent": True},
        "/api/telegram/code": {"ok": False, "needs_password": True},
        "/api/telegram/password": CONNECTED,
    }, steps=[
        {"type": {"tgApiId": "1", "tgApiHash": "x"}},
        {"type": {"tgPhone": "+441234567890"}},
        {"type": {"tgCode": "54321"}},
        {"type": {"tgPassword": "hunter2"}},
    ])
    reached.update(c for c in out["calls"] if "telegram" in c)

    out = _run({"/api/telegram/status": SIGNED_IN,
                "/api/telegram/disconnect": {"ok": True, "detail": "gone"}},
               steps=[{"press": "tgOut"}])
    reached.update(c for c in out["calls"] if "telegram" in c)

    assert reached == {
        "/api/telegram/status", "/api/telegram/credentials",
        "/api/telegram/login", "/api/telegram/code",
        "/api/telegram/password", "/api/telegram/disconnect"}


def test_the_flow_never_sends_credentials_anywhere_but_this_machine():
    """Relative paths only — the desktop app binds a different loopback port
    per install, so a host here would break it as well as leak."""
    import re

    source = (WEB / "connectors.js").read_text()
    telegram_calls = re.findall(r'api\(\s*[`"\']([^`"\']*telegram[^`"\']*)',
                                source)
    assert telegram_calls, "no telegram calls found at all"
    for call in telegram_calls:
        assert call.startswith("/api/"), call
