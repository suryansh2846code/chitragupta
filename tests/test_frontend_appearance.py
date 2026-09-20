"""The Appearance screen, clicked rather than read.

`tests/js/appearance_screen.mjs` evaluates the workspace against a small DOM,
clicks the settings-rail item, reads the roster it drew, picks an agent and
presses Save. Source-order assertions cannot see any of that: the routing is one
delegation among five, the roster is built element by element, and the avatar is
drawn by a package loaded from another `<script>` tag entirely.

The renderer's own behaviour is not retested here — it has 31 tests of its own
in `character/test/`. This is about the seam between it and the app.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "chitragupta" / "web"
HARNESS = ROOT / "tests" / "js" / "appearance_screen.mjs"

AGENTS = ["Inbox", "Launch", "Research"]


@pytest.fixture(scope="module")
def report() -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), str(WEB / "app.js")],
        capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr[:600]}"
    return json.loads(proc.stdout)


def test_the_harness_ran_clean(report):
    assert report["error"] is None, report["error"]


def test_the_settings_rail_item_opens_the_appearance_panel(report):
    """Every left-nav item opens a screen — including the newest one.

    The rail routes by a chain of `if (to === …)`, and a new item with no branch
    falls through to `closeModelScreen()`: the screen shuts and nothing opens,
    which reads as a dead button rather than as an error.
    """
    assert report["opened"]["screen"] is True
    # Exactly one panel visible. Two means a panel was left showing underneath,
    # which is how a "page" quietly becomes two pages stacked.
    assert report["opened"]["panel"] == ["appearance"]


def test_every_agent_gets_a_card_and_every_card_gets_a_face(report):
    """Nobody should ever see an empty avatar slot.

    No agent in this fixture has a saved avatar, so all three faces come from
    the generated character. A card with no `<svg>` in it is the whole feature
    failing silently — the screen would still look populated.
    """
    assert report["roster"]["cards"] == len(AGENTS)
    assert report["roster"]["withFaces"] == len(AGENTS)
    assert report["roster"]["names"] == AGENTS


def test_it_opens_on_the_agent_being_talked_to(report):
    """Not on an empty frame that asks you to pick someone first."""
    assert report["picked"]["mounts"] == 1
    assert report["picked"]["name"] == "launch"


def test_picking_an_agent_hands_the_editor_that_agent(report):
    """The bug this guards against is one editor showing another agent's face.

    Clicking the first card must load *its* document, not re-mount whatever was
    already open.
    """
    assert report["picked"]["afterClick"] == "inbox"


def test_saved_presets_are_shared_across_agents(report):
    """A person's scratch shelf belongs to them, not to one agent."""
    assert report["picked"]["storageKey"] == "chitragupta_character_presets"


def test_save_sends_the_document_to_that_agent_s_endpoint(report):
    """The agent in the URL is the agent that was picked, and a scene goes with it."""
    assert report["saved"] is not None, "Save sent no request"
    assert report["saved"]["url"] == "/api/agents/inbox/avatar"
    assert report["saved"]["hasScene"] is True
