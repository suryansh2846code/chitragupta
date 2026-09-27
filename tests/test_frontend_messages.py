"""Reading a message, which is the one thing the list is for.

A long report was clamped to four lines with nothing to press: the text ran out
mid-word and there was nowhere else to go for the rest. Worse, the stored copy
was itself cut at 400 characters, so the missing half did not exist anywhere —
the list was not previewing something, it was all there was.

So the claims are: a message opens when you press it, pressing it marks it read,
and pressing a button inside it does neither — a delete that also toggled the
row would be a list that argues with the pointer.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

LONG = ("The WhatsApp page didn't load the chat interface — it just shows a "
        "browser compatibility notice. WhatsApp Web is rejecting the browser "
        "Chitragupta drives it with: it shows “WhatsApp works with Google "
        "Chrome 100+” instead of the chat, so I can't see whether Dev has "
        "a new message. This has nothing to do with whether a message arrived.")

MESSAGES = [
    {"id": "m1", "agent_id": "chief-of-staff", "agent_name": "Chief of Staff",
     "kind": "result", "title": "Running kit check when Dev messages",
     "body": LONG, "source": "automation", "source_id": "run-1",
     "created_at": "2026-09-26T16:11:00+00:00", "read_at": "", "unread": True},
    {"id": "m2", "agent_id": "chief-of-staff", "agent_name": "Chief of Staff",
     "kind": "needs_you", "title": "Notify on mail", "body": "Short one.",
     "source": "", "source_id": "",
     "created_at": "2026-09-26T16:06:00+00:00", "read_at": "x", "unread": False},
]


def drive(script=None, messages=None, unread=1):
    # `messages if messages is not None`, because an empty list is falsy and
    # `messages or MESSAGES` quietly hands the empty-state test a full list.
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/inbox_messages.mjs"), str(WEB / "app.js")],
        input=json.dumps({"messages": MESSAGES if messages is None else messages, "unread": unread,
                          "script": script or []}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# ── the whole message is on the page ───────────────────────────────────────

def test_the_whole_message_is_rendered_not_a_prefix():
    """Clamping is a CSS decision that one tap undoes. Cutting the string is
    not — and cutting it is what it used to do."""
    out = drive()
    assert LONG[-60:] in out["html"], "the end of the message never reached the page"
    assert "…" not in out["html"].replace("&hellip;", "")


def test_pressing_a_message_opens_it():
    out = drive([{"op": "press", "id": "m1"}])
    assert out["opened"]["m1"] is True


def test_pressing_it_again_closes_it():
    """A list where everything you have ever looked at stays open is a list you
    scroll past."""
    out = drive([{"op": "press", "id": "m1"}, {"op": "press", "id": "m1"}])
    assert out["opened"]["m1"] is False


def test_opening_one_leaves_the_others_alone():
    out = drive([{"op": "press", "id": "m1"}])
    states = {s["id"]: s["open"] for s in out["states"]}
    assert states == {"m1": True, "m2": False}


# ── reading it is what marks it read ───────────────────────────────────────

def test_opening_an_unread_message_marks_it_read():
    """No separate tick to press. A list where you have to say "yes I read
    that" is a list with a chore in it."""
    out = drive([{"op": "press", "id": "m1"}])

    assert any(c["method"] == "POST" and c["url"].endswith("/m1/read")
               for c in out["calls"]), out["calls"]
    assert {s["id"]: s["unread"] for s in out["states"]}["m1"] is False


def test_opening_one_that_is_already_read_says_nothing():
    """A POST per glance would be a request per glance."""
    out = drive([{"op": "press", "id": "m2"}])
    assert not any(c["url"].endswith("/m2/read") for c in out["calls"])


def test_pressing_a_button_inside_the_row_does_not_toggle_it():
    """Delete and "Open run" live inside the row. A row that also toggled would
    argue with the pointer — you would press Delete and watch it expand."""
    out = drive([{"op": "pressButton", "id": "m1"}])
    assert out["opened"]["m1"] is False
    assert not any(c["url"].endswith("/m1/read") for c in out["calls"])


# ── how many are waiting ───────────────────────────────────────────────────

def test_the_rail_shows_how_many_are_unread():
    assert drive(unread=3)["unreadBadge"] == "3"


def test_a_big_number_does_not_stretch_the_rail():
    assert drive(unread=42)["unreadBadge"] == "9+"


def test_nothing_unread_shows_no_number():
    """A zero is a thing to read and dismiss. Nothing is nothing."""
    assert drive(unread=0)["unreadBadge"] == ""


def test_mark_all_read_is_offered_only_when_something_is_unread():
    assert drive(unread=0)["readAllHidden"] is True
    assert drive(unread=2)["readAllHidden"] is False


# ── what it says when there is nothing ─────────────────────────────────────

def test_an_empty_list_says_what_would_appear_there():
    out = drive(messages=[], unread=0)
    assert "something to tell you" in out["html"]
