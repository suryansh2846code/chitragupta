"""The Appearance tab of the agent profile, clicked rather than read.

`tests/js/appearance_screen.mjs` evaluates the workspace against a small DOM,
opens an agent's profile on that tab, opens another agent's, and presses Save.
Source-order assertions cannot see any of that: the editor is mounted by a
package loaded from another `<script>` tag entirely, and which agent's document
it was handed is decided at call time.

This was a settings screen of its own until the agent profile existed. What it
tested that no longer exists — a rail item, a panel, a roster of every agent —
went with it; what it tested that still happens is below, reached the new way.

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


def test_the_editor_is_reached_through_the_agent_profile(report):
    """It was a settings screen with a roster of every agent across the top.

    The roster only ever answered "which agent am I editing", and the profile
    has answered that before the tab is drawn — so the screen, the rail item
    and the roster all went, and this is what replaced them. That the rail no
    longer offers either removed panel is asserted in
    `test_frontend_agent_profile.py`, next to the rest of the popup.
    """
    assert report["opened"]["editorMounted"] is True


def test_it_opens_on_the_agent_whose_profile_it_is(report):
    """Not on an empty frame, and not on whichever agent was open last."""
    assert report["picked"]["mounts"] == 1
    assert report["picked"]["name"] == "launch"


def test_opening_another_agent_hands_the_editor_that_agent(report):
    """The bug this guards against is one editor showing another agent's face —
    it used to be a click on the roster, and it is a different profile now."""
    assert report["picked"]["afterClick"] == "research"


def test_saved_presets_are_shared_across_agents(report):
    """A person's scratch shelf belongs to them, not to one agent."""
    assert report["picked"]["storageKey"] == "chitragupta_character_presets"


def test_save_sends_the_document_to_that_agent_s_endpoint(report):
    """The agent in the URL is the agent whose profile is open, and a scene
    goes with it."""
    assert report["saved"] is not None, "Save sent no request"
    assert report["saved"]["url"] == "/api/agents/research/avatar"
    assert report["saved"]["hasScene"] is True
