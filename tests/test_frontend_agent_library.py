"""The Agent Library screen renders, filters, and says what a card must say.

`node --check` passes on the temporal-dead-zone ReferenceError that blanked the
model drawer, and a source-order assertion once passed while a card was being
written into a detached container. So this executes the render path in the real
load order and reads the cards back out of the container they went into.

The assertions are about what a card has to carry BEFORE somebody adds an
agent: what it cannot work without, and what it can do to their machine.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

LIBRARY = {
    "categories": ["Run your day", "Work & career", "Your Mac"],
    "templates": [
        {"id": "inbox", "name": "Inbox", "role": "email",
         "category": "Run your day", "description": "Triages your mail.",
         "works_with": ["gmail"], "needs": ["gmail"], "missing": ["gmail"],
         "runs_code": False, "touches_files": True, "in_roster": True},
        {"id": "engineer", "name": "Engineer", "role": "code",
         "category": "Work & career", "description": "Works in your repositories.",
         "works_with": ["github", "files"], "needs": [], "missing": [],
         "runs_code": True, "touches_files": True, "in_roster": False},
        {"id": "files", "name": "Files", "role": "disk", "category": "Your Mac",
         "description": "Finds what is taking up room.",
         "works_with": ["files"], "needs": ["files"], "missing": ["files"],
         "runs_code": True, "touches_files": True, "in_roster": False},
    ],
}


@pytest.fixture(scope="module")
def run() -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/agent_library.mjs"), str(WEB / "app.js")],
        input=json.dumps({"library": LIBRARY}),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _card(run, name):
    return next(c for c in run["cards"] if name in c["text"])


def test_it_ran(run):
    assert run["error"] is None, run["error"]
    assert run["screenVisible"] is True


def test_every_template_became_a_card(run):
    assert run["cardCount"] == len(LIBRARY["templates"])
    assert "/api/agents/library" in run["calls"]


def test_a_card_says_what_it_cannot_work_without(run):
    """Never offer a control that cannot work."""
    assert "Connect Gmail first" in _card(run, "Inbox")["text"]


def test_a_folder_is_opened_not_connected(run):
    """"Connect Folders you choose first" is not a sentence anyone would say."""
    text = _card(run, "Files")["text"]
    assert "Open a folder for it first" in text
    assert "Connect Folders" not in text


def test_an_agent_that_runs_code_says_so_before_it_is_added(run):
    """Adding an agent is the consent, so the consent has to be informed."""
    assert "runs code on your Mac" in _card(run, "Engineer")["text"]
    assert "runs code on your Mac" not in _card(run, "Inbox")["text"]


def test_the_library_adds_and_never_takes_away(run):
    """A card the user already has offers no action at all.

    It used to read "In your roster · Remove", which did the same thing the
    agent profile calls Retire — one act, two names, two screens, and only one
    of them said what was kept. Removing an agent is a decision made in its own
    profile now, beside the permanent one it has to be told apart from, so the
    card states the fact and offers nothing.
    """
    mine, theirs = _card(run, "Inbox"), _card(run, "Engineer")
    assert mine["buttons"] == [], "the Library still offers a way to remove"
    assert "In your roster" in mine["text"]
    assert theirs["buttons"] == ["Add agent →"]
    assert "is-mine" in mine["className"]
    assert "is-mine" not in theirs["className"]


def test_the_shelves_are_real_and_filter(run):
    assert run["categoryButtons"][0] == "All"
    for name in LIBRARY["categories"]:
        assert name in run["categoryButtons"]
    # One template is on the first shelf; filtering must replace the grid, not
    # append to it — the bug this harness caught on its first run.
    assert run["filteredCount"] == 1, (
        f"filtering left {run['filteredCount']} cards on screen")
