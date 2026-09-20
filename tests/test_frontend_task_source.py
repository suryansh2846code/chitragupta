"""A task made out of a thread can get back to the thread.

The backend half is in `test_thread_to_task.py`: the action stores `source`
and `source_ref`, and they survive into the row. This is the half that makes
it worth storing — without a way back on screen, the link is a column nobody
ever reads and the user searches their inbox anyway.

The harness clicks, rather than asserting on HTML. A render assertion would
have passed with no handler attached at all, which is the blindness
`frontend-testing.md` exists about.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

FROM_EMAIL = {
    "id": "a1", "title": "Send Rahul the revised figures",
    "due": "2026-09-25", "done": 0,
    "source": "email", "source_ref": "t7",
}

TYPED_BY_HAND = {
    "id": "a2", "title": "Book the dentist", "due": None, "done": 0,
    "source": "", "source_ref": "",
}


def _run(tasks):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/task_source.mjs"), str(WEB / "app.js")],
        input=json.dumps({"tasks": tasks}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def listed():
    return _run([FROM_EMAIL, TYPED_BY_HAND])


def test_the_list_rendered(listed):
    assert listed["error"] is None
    assert "Send Rahul the revised figures" in listed["html"]
    assert "Book the dentist" in listed["html"]


def test_only_the_task_with_a_thread_offers_a_way_back(listed):
    """Never a control that cannot work. A "from email" link on a task typed
    into the box has nowhere to go."""
    assert listed["sourceLinks"] == 1


def test_the_due_date_still_shows_beside_it(listed):
    """The origin shares a line with the due date, and the due date is the
    more important of the two — it must not have been displaced."""
    assert "Sep" in listed["html"] or "due" in listed["html"].lower()
    assert "email" in listed["html"]


def test_clicking_it_opens_the_thread_that_was_stored(listed):
    """The whole point. And the id comes from the row's `source_ref`, not from
    anything reconstructed — a task whose link opened the wrong conversation
    would be worse than no link."""
    assert listed["pressed"] == "ok", listed["pressed"]

    opens = [c for c in listed["calls"] if c["url"] == "/api/open-browser"]
    assert len(opens) == 1
    assert opens[0]["method"] == "POST"
    assert opens[0]["body"]["url"].endswith("/t7")


def test_it_asks_the_backend_rather_than_navigating_itself():
    """`/api/open-browser` is the one door out, and it refuses anything that
    is not http(s). A page that called `window.open` would be going around
    the check that exists for exactly this."""
    out = _run([FROM_EMAIL])

    assert any(c["url"] == "/api/open-browser" for c in out["calls"])
    assert out["calls"][0]["body"]["url"].startswith("https://")


def test_a_task_from_before_this_shipped_renders_without_a_link():
    """An existing tasks.db has no `source` column until the migration runs,
    so the field arrives undefined rather than empty."""
    out = _run([{k: v for k, v in FROM_EMAIL.items()
                 if k not in ("source", "source_ref")}])

    assert out["error"] is None
    assert out["sourceLinks"] == 0
    assert "Send Rahul the revised figures" in out["html"]


def test_an_empty_task_list_still_renders():
    out = _run([])

    assert out["error"] is None
    assert "No open tasks" in out["html"]
