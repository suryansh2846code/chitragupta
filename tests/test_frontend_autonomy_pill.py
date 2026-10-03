"""The composer's autonomy pill, driven through `tests/js/autonomy_pill.mjs`.

It edits the same setting as the agent profile's Persona tab, from where you
are when it matters — about to ask for something, deciding whether you want to
be asked back. So what is asserted is the seam rather than the drawing: whose
level it shows, what it sends, and what it does when the server says no.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent
APP_JS = ROOT / "chitragupta" / "web" / "app.js"
HARNESS = ROOT / "tests" / "js" / "autonomy_pill.mjs"


@pytest.fixture(scope="module")
def report():
    proc = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert not data.get("error"), data["error"]
    return data


def test_it_says_nothing_until_it_knows_the_level(report):
    """A pill reading "Mode" over an agent whose setting has not arrived is a
    control claiming to report something it has not been told."""
    assert report["hidden"]["beforeAnyAgent"]
    assert report["hidden"]["inPage"], "the markup does not ship it hidden"


def test_it_shows_the_open_agents_level(report):
    loaded = report["loaded"]
    assert loaded["shown"]
    assert loaded["label"] == "Ask before changing"
    assert loaded["rows"] == 3, "the three levels did not render"
    assert loaded["onRow"] == 1, "the stored level is not the one marked on"


def test_the_levels_come_from_the_server(report):
    """Three rows, drawn from what `/persona` sent — the same vocabulary the
    Persona tab uses, so the two cannot disagree about what the words mean."""
    assert report["loaded"]["rows"] == 3


def test_picking_a_level_sends_it_and_closes(report):
    picked = report["picked"]
    assert picked["url"].endswith("/persona")
    assert picked["body"] == {"autonomy": "on_its_own"}
    assert picked["label"] == "Acts on its own"
    assert picked["menuClosed"]


def test_switching_modes_invalidates_the_permission_panel(report):
    """Read only takes the changing tools away and leaving it puts them back,
    so a panel still showing the old switches is showing something other than
    what is true."""
    assert report["picked"]["invalidatedTools"]


def test_a_refused_save_puts_the_label_back(report):
    """The label moves before the server answers, so that a press is visibly a
    press. That is only honest if a refusal moves it back — otherwise the pill
    reports a mode the agent is not in."""
    assert report["failed"]["label"] == "Acts on its own"


def test_the_level_is_per_agent(report):
    """Showing the level of the agent you just left would be a label about
    somebody else."""
    assert report["perAgent"]["chotu"] == "Read only"
    assert report["perAgent"]["backToInbox"] == "Acts on its own"
