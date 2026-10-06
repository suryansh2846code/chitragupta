"""The first screen of onboarding: making an account.

Making an account is step one and there is no skip — a deliberate product
decision, recorded in `/CLAUDE.md` and `docs/ACCOUNTS-DESIGN.md` §0. Which makes
the important question here the dangerous one rather than the happy path:
**can this screen trap somebody?**

A gate whose key does not exist is not a strict app, it is a bricked one. So the
tests that matter are the three ways a user could arrive without a key — no
OAuth client in the build, an unreachable endpoint, a sign-in that never
finished — and each one has to leave them able to continue or able to retry.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "chitragupta" / "web" / "onboarding.html"
HARNESS = ROOT / "tests" / "js" / "onboarding_account.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                               reason="node is needed to execute the frontend")

GOOGLE = {"id": "google", "label": "Google", "available": True, "reason": "",
          "scopes": ["openid", "email", "profile"], "linked": False}
APPLE = {"id": "apple", "label": "Apple", "available": False,
         "reason": "Apple sign-in needs a web address of ours that it can send "
                   "you back to, which Chitragupta does not have yet.",
         "scopes": ["openid", "email", "name"], "linked": False}

SIGNED_OUT = {"signed_in": False, "user": None, "has_account": False,
              "account": None, "providers": [GOOGLE, APPLE]}
SIGNED_IN = {**SIGNED_OUT, "signed_in": True, "has_account": True,
             "user": {"email": "someone@example.com", "name": "Someone",
                      "provider": "google", "signed_in_at": 1_760_000_000}}
NO_CLIENT = {**SIGNED_OUT, "providers": [
    {**GOOGLE, "available": False,
     "reason": "This build has no Google sign-in client configured, so signing "
               "in is not available."},
    APPLE]}


def run(**plan) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS), str(PAGE)],
        input=json.dumps(plan), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"harness failed:\n{out.stdout}\n{out.stderr}"
    result = json.loads(out.stdout)
    assert "harness_error" not in result, result["harness_error"]
    return result


def posted(result: dict, path: str) -> list[dict]:
    return [c for c in result["calls"]
            if c["path"] == path and c["method"] == "POST"]


# ── it must not be possible to get stuck ────────────────────────────────────

def test_a_build_that_cannot_sign_in_does_not_trap_the_user():
    """The reason this screen has a way through at all.

    With no OAuth client there is no key for this gate, so holding somebody on
    it would brick the app for every build that ships without one. It opens
    itself and says why instead.
    """
    out = run(state=NO_CLIENT)
    assert out["passed"] is True, "a user with no way to sign in was trapped"
    # The reason is recorded rather than demanded of the user: a missing OAuth
    # client is not something they can do anything about.
    assert "no Google sign-in client" in out["note"]


def test_an_unreachable_account_endpoint_does_not_trap_the_user():
    """The first screen a user ever sees must not be a locked door because a
    request failed."""
    out = run(state={"__fail": "connection refused"})
    assert out["passed"] is True
    assert out["suppressions"], "the failure was swallowed without a word"


def test_a_sign_in_that_never_finished_leaves_the_screen_usable():
    """They closed the tab, or said no. The screen must be retryable, not a
    dead spinner — and it must not have let them through either."""
    out = run(state=SIGNED_OUT, press="google",
              finish={"__fail": "The sign-in was not finished. Try again."})
    assert out["passed"] is False
    assert "not finished" in out["note"]
    live = [b for b in out["buttons"] if not b["locked"]]
    assert live, "no way to try again"


def test_a_refused_begin_never_opens_a_browser():
    out = run(state=SIGNED_OUT, press="google",
              begin={"__fail": "not available"})
    assert posted(out, "/api/open-browser") == []
    assert out["passed"] is False
    assert "not available" in out["note"]


# ── the happy paths ─────────────────────────────────────────────────────────

def test_somebody_already_signed_in_never_sees_the_screen():
    out = run(state=SIGNED_IN)
    assert out["passed"] is True
    assert out["buttons"] == [], "the screen rendered for somebody signed in"


def test_signing_in_passes_the_step_and_hands_over_to_the_hero():
    out = run(state=SIGNED_OUT, press="google", finish=SIGNED_IN)
    assert out["passed"] is True
    assert posted(out, "/api/account/signin/begin")
    assert posted(out, "/api/open-browser")
    assert posted(out, "/api/account/signin/finish")
    assert out["focused"] == "paneHero", "the keyboard was left on a dead screen"


def test_the_provider_pressed_is_the_one_sent():
    out = run(state=SIGNED_OUT, press="google", finish=SIGNED_IN)
    body = posted(out, "/api/account/signin/begin")[0]["body"]
    assert body == {"provider": "google", "link": False}


# ── what the screen draws ───────────────────────────────────────────────────

def test_an_unavailable_provider_is_drawn_locked_with_its_reason():
    """Drawn, not hidden: a provider that is simply absent is indistinguishable
    from one nobody thought about. The reason is the title, because this page
    has no toast."""
    out = run(state=SIGNED_OUT)
    locked = [b for b in out["buttons"] if b["locked"]]
    assert len(locked) == 1
    assert "web address of ours" in locked[0]["title"]


def test_the_usable_provider_is_live():
    out = run(state=SIGNED_OUT)
    live = [b for b in out["buttons"] if not b["locked"]]
    assert [b["provider"] for b in live] == ["google"]


def test_a_provider_the_page_has_never_heard_of_is_drawn():
    """Adding Apple or Microsoft later must be server data, not a page edit."""
    invented = {"id": "someco", "label": "SomeCo", "available": True,
                "reason": "", "scopes": ["openid"], "linked": False}
    out = run(state={**SIGNED_OUT, "providers": [invented]})
    assert [b["provider"] for b in out["buttons"]] == ["someco"]


def test_the_first_screen_says_what_it_does_not_ask_for():
    """No **scope** may reach the first screen a user ever sees.

    Checked as scope strings, not as words: the first version looked for
    "calendar" and failed on the very sentence promising it is not asked for,
    which is the reassurance doing its job.
    """
    page = PAGE.read_text()
    start = page.index('id="paneAccount"')
    pane = page[start:page.index('id="paneHero"', start)]

    # Real scope spellings, not bare words: looking for "calendar." matched the
    # full stop at the end of "not your calendar." — the reassurance itself.
    for scope in ("gmail.readonly", "gmail.send", "drive.file", "drive.readonly",
                  "calendar.events", "googleapis.com/auth"):
        assert scope not in pane.lower(), f"a data scope reached screen one: {scope}"
    # And it says so in words a person reads.
    for promise in ("not your mail", "not your files", "not your calendar"):
        assert promise in pane


def test_the_screen_paints_itself_without_the_rest_of_boot():
    """The regression for "there is no login screen".

    `syncPanes()` is what puts `account` on the stage, and that class is what
    the CSS makes visible. It used to be reached only at the very end of the
    boot chain — after `refreshConnectors()` and `verifyLLM()` — so one throw
    anywhere in there left `.acct` at `opacity:0` with the hero showing, which
    from the outside is indistinguishable from the screen not existing. It
    shipped exactly that way.

    A screen's own visibility may not depend on two unrelated requests
    succeeding, so `acoLoad` paints on every path of its own.
    """
    out = run(state=SIGNED_OUT)
    assert out["passed"] is False, "the screen should still be up"
    assert out["synced"] >= 1, (
        "acoLoad decided to show the screen and never painted it — the stage "
        "class the CSS needs was left to the end of the boot chain")
    assert out["buttons"], "nothing was drawn"
