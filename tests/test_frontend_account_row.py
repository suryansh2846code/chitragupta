"""The Account screen's "You" row, executed rather than read.

`web/CLAUDE.md` requires a new render path and click handler to be run in a
test. The failures that would ship silently here are: a Sign in button on a
build that cannot sign in, a provider drawn live when the server said it is not
available, a privacy sentence that no longer matches what the server requests,
and a sign-in left waiting because the browser never came back.

The other thing asserted is the ask behind this whole package: **adding a
provider is server data, not a frontend change.** The scenarios below invent a
provider the frontend has never heard of, and the row draws it.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "chitragupta" / "web"
HARNESS = ROOT / "tests" / "js" / "account_row.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

GOOGLE_ROW = {"id": "google", "label": "Google", "available": True,
              "reason": "", "scopes": ["openid", "email", "profile"],
              "linked": False}
MICROSOFT_ROW = {"id": "microsoft", "label": "Microsoft", "available": False,
                 "reason": "This build has no Microsoft sign-in client "
                           "configured, so signing in with Microsoft is not "
                           "available.",
                 "scopes": ["openid", "email", "profile", "offline_access"],
                 "linked": False}
APPLE_ROW = {"id": "apple", "label": "Apple", "available": False,
             "reason": "Apple sign-in needs a web address of ours that it can "
                       "send you back to, which Chitragupta does not have yet. "
                       "Google sign-in works today.",
             "scopes": ["openid", "email", "name"], "linked": False}

NO_ACCOUNT = {
    "signed_in": False, "user": None, "has_account": False, "account": None,
    "device_id": "dev-abc", "devices_per_licence": 1, "enforced": False,
    "skippable": True, "signing_in_optional": True,
    "asks_for": "your name and email address",
    "providers": [GOOGLE_ROW, MICROSOFT_ROW, APPLE_ROW],
}

ACCOUNT = {"id": "acct-1", "created_at": 1_760_000_000,
           "display_name": "Someone", "email": "someone@example.com",
           "identities": [{"provider": "google", "email": "someone@example.com",
                           "name": "Someone", "linked_at": 1_760_000_000}],
           "providers": ["google"]}

SIGNED_IN = {
    **NO_ACCOUNT, "signed_in": True, "has_account": True, "account": ACCOUNT,
    "user": {"provider": "google", "email": "someone@example.com",
             "name": "Someone", "picture": "", "email_verified": True,
             "signed_in_at": 1_760_000_000},
    "providers": [{**GOOGLE_ROW, "linked": True}, MICROSOFT_ROW, APPLE_ROW],
}

SIGNED_OUT_WITH_ACCOUNT = {**NO_ACCOUNT, "has_account": True,
                           "account": ACCOUNT}


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


def posts(result: dict, path: str) -> list[dict]:
    return [r for r in result["requests"]
            if r["path"] == path and r["method"] == "POST"]


# ── making an account ───────────────────────────────────────────────────────

def test_no_account_yet_offers_the_usable_providers():
    first = step(run(answers={"/api/account/state": NO_ACCOUNT}), "loaded")
    assert "No account yet" in first["html"]
    live = [b for b in first["buttons"] if not b["locked"]]
    assert [b["provider"] for b in live] == ["google"]


def test_an_unavailable_provider_is_locked_rather_than_missing():
    """A provider that is simply absent is indistinguishable from one nobody
    thought about; one present and locked carries its reason."""
    first = step(run(answers={"/api/account/state": NO_ACCOUNT}), "loaded")
    locked = [b for b in first["buttons"] if b["locked"]]
    assert len(locked) == 2
    reasons = " ".join(b["soon"] for b in locked)
    assert "web address of ours" in reasons, "Apple's reason was not shown"
    assert "no Microsoft sign-in client" in reasons


def test_apple_and_microsoft_are_both_drawn():
    html = step(run(answers={"/api/account/state": NO_ACCOUNT}), "loaded")["html"]
    assert "Apple" in html
    assert "Microsoft" in html


def test_it_says_signing_in_is_optional_and_what_it_does_not_ask_for():
    html = step(run(answers={"/api/account/state": NO_ACCOUNT}), "loaded")["html"]
    assert "optional" in html
    assert "your name and email address" in html
    for not_asked in ("not your mail", "not your files", "not your calendar"):
        assert not_asked in html


def test_the_disclosure_comes_from_the_server():
    """So it cannot drift from the request."""
    changed = {**NO_ACCOUNT, "asks_for": "only your email address"}
    html = step(run(answers={"/api/account/state": changed}), "loaded")["html"]
    assert "only your email address" in html


# ── a provider the frontend has never heard of ─────────────────────────────

def test_a_brand_new_provider_needs_no_frontend_change():
    """The ask: adding Apple or Microsoft later is server data.

    This invents one that does not exist in `providers.py` at all. If the row
    drew it, nothing in the frontend is keyed on a provider's name.
    """
    invented = {"id": "someco", "label": "SomeCo", "available": True,
                "reason": "", "scopes": ["openid"], "linked": False}
    state = {**NO_ACCOUNT, "providers": [GOOGLE_ROW, invented]}
    first = step(run(answers={"/api/account/state": state}), "loaded")

    assert "SomeCo" in first["html"]
    assert "someco" in [b["provider"] for b in first["buttons"]]


def test_pressing_a_new_provider_sends_its_id():
    invented = {"id": "someco", "label": "SomeCo", "available": True,
                "reason": "", "scopes": ["openid"], "linked": False}
    state = {**NO_ACCOUNT, "providers": [invented]}
    result = run(answers={"/api/account/state": state,
                          "/api/account/signin/begin": {
                              "url": "https://someco.example/auth", "port": 1,
                              "provider": "someco", "linking": False},
                          "/api/account/signin/finish": SIGNED_IN},
                 provider="someco", actions=["press_signin"])
    sent = posts(result, "/api/account/signin/begin")
    assert sent[0]["body"] == {"provider": "someco", "link": False}


# ── signed out, but the account is still here ──────────────────────────────

def test_an_account_without_a_session_says_sign_in_again_not_start_over():
    """After a sign-out, or after restoring a backup onto a new Mac."""
    first = step(run(answers={"/api/account/state": SIGNED_OUT_WITH_ACCOUNT}),
                 "loaded")
    assert "Signed out" in first["html"]
    assert "This Mac has an account" in first["html"]
    assert "untouched" in first["html"]


# ── signed in ───────────────────────────────────────────────────────────────

def test_signed_in_shows_who_and_the_ways_in():
    first = step(run(answers={"/api/account/state": SIGNED_IN}), "loaded")
    assert "Someone" in first["html"]
    assert "acSignOut" in first["ids"]
    assert "Ways to sign in" in first["html"]
    assert "One Mac per licence" in first["html"]


def test_the_only_way_in_cannot_be_removed():
    """An account nobody can sign into is not an unlink, it is a deletion."""
    html = step(run(answers={"/api/account/state": SIGNED_IN}), "loaded")["html"]
    assert "your only way in" in html
    assert "ac-unlink" not in html


def test_a_second_linked_provider_can_be_removed():
    two = {**SIGNED_IN, "providers": [
        {**GOOGLE_ROW, "linked": True},
        {**MICROSOFT_ROW, "available": True, "reason": "", "linked": True},
        APPLE_ROW]}
    html = step(run(answers={"/api/account/state": two}), "loaded")["html"]
    assert "ac-unlink" in html
    assert "your only way in" not in html


def test_linking_says_why_it_is_safe():
    """We never join two accounts because their emails match, and the screen
    explains that rather than leaving it a mystery."""
    html = step(run(answers={"/api/account/state": SIGNED_IN}), "loaded")["html"]
    assert "needs you signed in" in html
    assert "email addresses match" in html


def test_adding_a_provider_sends_the_link_flag():
    linkable = {**SIGNED_IN, "providers": [
        {**GOOGLE_ROW, "linked": True},
        {**MICROSOFT_ROW, "available": True, "reason": ""},
        APPLE_ROW]}
    result = run(answers={"/api/account/state": linkable,
                          "/api/account/signin/begin": {
                              "url": "https://login.microsoftonline.com/x",
                              "port": 1, "provider": "microsoft",
                              "linking": True},
                          "/api/account/signin/finish": linkable},
                 provider="microsoft", actions=["press_link"])
    sent = posts(result, "/api/account/signin/begin")
    assert sent[0]["body"] == {"provider": "microsoft", "link": True}


def test_unlinking_asks_first_and_says_the_account_stays():
    result = run(answers={"/api/account/state": SIGNED_IN,
                          "/api/account/unlink": SIGNED_IN},
                 provider="microsoft", actions=["press_unlink"])
    assert result["confirms"], "unlinking did not ask"
    assert "account and everything in it stays" in result["confirms"][0]
    assert posts(result, "/api/account/unlink")


# ── the flow ────────────────────────────────────────────────────────────────

def test_signing_in_begins_opens_a_browser_and_finishes():
    result = run(answers={
        "/api/account/state": [NO_ACCOUNT, SIGNED_IN],
        "/api/account/signin/begin": {
            "url": "https://accounts.google.com/o/oauth2/v2/auth?x=1",
            "port": 54321, "provider": "google", "linking": False},
        "/api/account/signin/finish": SIGNED_IN,
    }, actions=["press_signin"])

    opened = posts(result, "/api/open-browser")
    assert len(opened) == 1
    assert opened[0]["body"]["url"].startswith("https://accounts.google.com/")

    after = step(result, "press_signin")
    assert after["busy"] is False, "the button was left waiting"
    assert "acSignOut" in after["ids"]


def test_a_sign_in_that_never_comes_back_does_not_stay_waiting():
    """Close the browser tab and the button must come back."""
    result = run(answers={
        "/api/account/state": NO_ACCOUNT,
        "/api/account/signin/begin": {"url": "https://accounts.google.com/x",
                                      "port": 1, "provider": "google",
                                      "linking": False},
    }, fail_on="/signin/finish",
        fail_detail="The sign-in was not finished. Try again.",
        actions=["press_signin"])

    after = step(result, "press_signin")
    assert after["busy"] is False
    assert "Waiting" not in after["html"]
    assert any("not finished" in t for t in result["toasts"]), result["toasts"]


def test_a_refused_begin_never_opens_a_browser():
    result = run(answers={"/api/account/state": NO_ACCOUNT},
                 fail_on="/signin/begin", fail_detail="not available",
                 actions=["press_signin"])
    assert posts(result, "/api/open-browser") == []
    assert step(result, "press_signin")["busy"] is False
    assert result["toasts"]


def test_cancelling_clears_the_attempt_and_the_button():
    result = run(answers={"/api/account/state": NO_ACCOUNT,
                          "/api/account/signin/cancel": {"cancelled": True}},
                 actions=["press_cancel"])
    assert posts(result, "/api/account/signin/cancel")
    assert step(result, "press_cancel")["busy"] is False


# ── signing out ─────────────────────────────────────────────────────────────

def test_signing_out_asks_first_and_says_what_is_kept():
    """A user signing out must not fear they are deleting their brain."""
    result = run(answers={"/api/account/state": SIGNED_IN,
                          "/api/account/signout": SIGNED_OUT_WITH_ACCOUNT},
                 actions=["press_signout"])
    assert result["confirms"], "signing out did not ask"
    asked = result["confirms"][0]
    assert "brain" in asked
    assert "stay exactly as they are" in asked
    assert posts(result, "/api/account/signout")


def test_declining_the_confirm_signs_nobody_out():
    result = run(answers={"/api/account/state": SIGNED_IN}, confirm=False,
                 actions=["press_signout"])
    assert posts(result, "/api/account/signout") == []
    assert "acSignOut" in step(result, "press_signout")["ids"]
