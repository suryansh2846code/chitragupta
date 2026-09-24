"""Signing in to a site, and saying so when the sign-in lapses.

`browser/signin.py` shipped with four endpoints and no screen. The flow it
describes has three states and the middle one is the one worth testing:
`still_signing_in` is **not a failure**. It is our guess that the user is still
on the login page, and the second press of Done has to overrule it with
`force: true` — because the check is a heuristic over the address the browser
landed on, it will be wrong on some site, and a user who cannot overrule it is
locked out of an account that is already theirs.

Contract: docs/development/connected-sites.md.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

LIVE = {"connecting": True, "host": "linkedin.com", "url": "https://linkedin.com",
        "still_signing_in": False, "note": ""}
WAITING = {**LIVE, "still_signing_in": True,
           "note": "The browser still looks like it is on a sign-in page. Finish "
                   "signing in, then press Done again — or press Done again "
                   "anyway if you know you are in."}
#: Google refusing us for being an automated browser — the one waiting state a
#: second press cannot overrule, because it is not our guess that was wrong.
REFUSED = {**LIVE, "still_signing_in": True, "sso_refused": True,
           "note": "Google would not sign you in through this browser — it only "
                   "allows its own sign-in from an ordinary browser window."}
IDLE = {"connecting": False}


def drive(states, press, replies=None, risk_value="", typed="example.com") -> dict:
    payload = {"states": states, "press": press, "replies": replies or {},
               "riskValue": risk_value, "typed": typed}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/connect_site.mjs"), str(WEB / "browser.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


def posts(out: dict, path: str) -> list[dict]:
    return [c["body"] for c in out["calls"]
            if c.get("path") == path and c.get("method") == "POST"]


# ── the three states ──────────────────────────────────────────────────────
def test_idle_offers_an_address_and_nothing_else():
    f = drive([IDLE], ["poll"])["frames"][0]
    assert f["idleHidden"] is False
    assert f["liveHidden"] is True


def test_while_connecting_it_says_where_the_window_is():
    """"It is a separate window" — otherwise people look for it inside the app."""
    f = drive([LIVE], ["poll"])["frames"][0]
    assert f["liveHidden"] is False and f["idleHidden"] is True
    assert "linkedin.com" in f["message"]
    assert "separate window" in f["message"]
    assert f["doneLabel"] == "Done"


def test_still_signing_in_is_not_rendered_as_a_failure():
    f = drive([WAITING], ["poll"])["frames"][0]
    assert f["waiting"] is True, "the waiting state has no styling of its own"
    assert f["error"] == "", "a guess about the login page was shown as an error"


def test_the_servers_sentence_is_shown_not_one_of_ours():
    """It knows whether this is the first ask or the second. Two places writing
    that sentence is two places that can disagree."""
    assert drive([WAITING], ["poll"])["frames"][0]["message"] == WAITING["note"]


# ── the override, which is the point ──────────────────────────────────────
def test_the_first_done_asks_and_the_second_overrules():
    out = drive([IDLE, LIVE, WAITING, WAITING], ["poll", "poll", "done", "poll", "done"],
                replies={"/api/browser/connect/finish":
                         {"ok": False, "still_signing_in": True, "host": "linkedin.com",
                          "error": "That still looks like a sign-in page."}})
    sent = posts(out, "/api/browser/connect/finish")
    assert [b["force"] for b in sent] == [False, True], sent


def test_the_button_says_what_the_second_press_does():
    out = drive([IDLE, LIVE, WAITING], ["poll", "poll", "done"],
                replies={"/api/browser/connect/finish":
                         {"ok": False, "still_signing_in": True,
                          "error": "That still looks like a sign-in page."}})
    assert out["frames"][-1]["doneLabel"] == "Done anyway"


def test_a_refusal_is_not_dressed_up_as_something_to_overrule():
    """The other waiting state. Google said no, `force` is turned down too, and
    a button reading "Done anyway" would promise an override that cannot happen
    — failing twice and explaining itself neither time. It reads "Done", and
    starts working the moment they sign in the way the note describes."""
    f = drive([REFUSED], ["poll"])["frames"][0]

    assert f["doneLabel"] == "Done"
    assert f["message"] == REFUSED["note"], "the server's sentence, not ours"
    assert f["error"] == "", "not rendered as a failure of theirs"


def test_a_refusal_never_sends_force():
    """It follows from the label — `force` is read off the button — and it is
    the half that would otherwise reach the server."""
    out = drive([REFUSED, REFUSED], ["poll", "done"],
                replies={"/api/browser/connect/finish":
                         {"ok": False, "still_signing_in": True,
                          "sso_refused": True, "error": REFUSED["note"]}})

    assert [b["force"] for b in posts(out, "/api/browser/connect/finish")] == [False]


def test_anything_started_can_be_stopped():
    out = drive([LIVE], ["poll", "cancel"])
    assert posts(out, "/api/browser/connect/cancel") == [None]


def test_a_refusal_in_the_body_is_shown():
    """`POST /connect` answers `{ok: false, error}` rather than raising, so a
    refusal has to be read out of the body — awaiting it and assuming success
    is how "already signing in to X" became a silent no-op."""
    out = drive([IDLE], ["go"], typed="x.com",
                replies={"/api/browser/connect":
                         {"ok": False, "error": "Already signing in to x.com."}})
    assert out["frames"][-1]["error"] == "Already signing in to x.com."


def test_an_empty_address_does_nothing():
    """Not a refusal and not an error — pressing Connect with nothing typed is
    a misfire, and a message about it would be noise."""
    out = drive([IDLE], ["go"], typed="")
    assert [c for c in out["calls"] if c.get("method") == "POST"] == []
    assert out["frames"][-1]["error"] == ""


# ── the risk notice the doc requires ──────────────────────────────────────
@pytest.mark.parametrize("site,warned", [
    ("linkedin.com", True), ("https://www.linkedin.com/feed", True),
    ("m.x.com", True), ("news.bbc.co.uk", False), ("example.com", False)])
def test_the_risky_sites_are_named_before_connecting(site, warned):
    """"Say the risk in the UI, once, before the user connects one of the bottom
    four. Not buried in a doc." Matched on the registered domain, so a subdomain
    or a pasted URL does not slip past it."""
    assert drive([IDLE], [])["risk"][site] is warned


def test_the_warning_appears_as_the_address_is_typed():
    out = drive([IDLE], ["risk"], risk_value="linkedin.com")
    assert "LinkedIn watches for automation" in out["frames"][-1]["risk"]


# ── a lapsed session ──────────────────────────────────────────────────────
def lapsed(steps) -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/lapsed_session.mjs"), str(WEB / "chat.js")],
        input=json.dumps({"steps": steps}), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


SIGNED_OUT = ("You are signed out of linkedin.com. The user needs to connect it "
              "again under Connectors — tell them, and do not try to sign in or "
              "read around it.")


def test_a_lapsed_session_names_the_whole_host():
    """`[^\\s.]+` stopped at the first dot and turned linkedin.com into
    "linkedin" — a site the user does not have and cannot reconnect."""
    assert lapsed([{"kind": "tool_result", "result": SIGNED_OUT}])["hosts"] == ["linkedin.com"]


def test_it_reads_as_an_errand_not_an_error():
    html = lapsed([{"kind": "tool_result", "result": SIGNED_OUT}])["html"]
    assert "the permission is fine" in html
    assert "Reconnect linkedin.com" in html


def test_the_reconnect_button_is_wired():
    assert lapsed([{"kind": "tool_result", "result": SIGNED_OUT}])["wired"] is True


def test_each_site_is_named_once():
    out = lapsed([{"kind": "tool_result", "result": "You are signed out of x.com. A."},
                  {"kind": "tool_result", "result": SIGNED_OUT},
                  {"kind": "tool_result", "result": "You are signed out of x.com. B."}])
    assert out["hosts"] == ["x.com", "linkedin.com"]


def test_an_ordinary_turn_produces_no_card():
    assert lapsed([{"kind": "tool_result", "result": "Found 3 memories."}])["hosts"] == []


def test_the_card_is_not_inside_the_trace_fold():
    """The trace is collapsed by default. A logged-out account is not trace
    detail — it is the one thing the user has to act on."""
    src = (WEB / "chat.js").read_text()
    body = src.split("function addTrace(steps) {", 1)[1]
    before_guard = body.split("if (!steps.length) return;", 1)[0]
    assert "reconnectCard" in before_guard, "the card is built after the early return"


def test_a_sign_in_survives_a_refresh():
    """The state is the server's, so reopening the panel has to find the flow
    again. Without this a refresh strands a browser window that nobody can then
    finish or cancel."""
    src = (WEB / "browser.js").read_text()
    loader = src.split("async function loadBrowserSites()", 1)[1].split("\n}", 1)[0]
    assert "loadConnectState()" in loader
