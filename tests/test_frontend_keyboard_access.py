"""The workspace must be operable from a keyboard.

Agent rows are `<div>`s with an `onclick`. Before this, that meant the rail was
mouse-only: you could not switch agents without a pointer, and the
`.agent:focus-visible` rule in the stylesheet could never fire because nothing
could focus a `<div>` with no `tabindex`. The markup now says `role="button"`,
which is a promise that Enter and Space work — so the promise is executed here
rather than read.

Escape is checked the same way. With two modals open the handler has to close
the *topmost* one, and close it by clicking that modal's own close button so its
existing teardown still runs. Both of those look identical in the source when
they are wrong.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
APP_JS = ROOT / "chitragupta/web/app.js"
HARNESS = ROOT / "tests/js/a11y_keyboard.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


@pytest.fixture(scope="module")
def report() -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr[:600]}"
    return json.loads(proc.stdout)


# ── the agent rail ────────────────────────────────────────────────────────
def test_agent_rows_are_reachable_by_keyboard(report):
    rail = report["agentRail"]
    assert rail["hasTabindex"], "agent rows have no tabindex — the rail is mouse-only"
    assert rail["hasRole"], "agent rows have no role, so they announce as nothing"


def test_agent_rows_have_an_accessible_name(report):
    assert report["agentRail"]["hasAriaLabel"], "an agent row announces as 'button'"


def test_enter_selects_an_agent(report):
    rail = report["agentRail"]
    assert rail["keydownAttached"], "no keydown handler was attached to the rows"
    assert rail["selectedAfterEnter"] == "inbox-2", (
        f"Enter did not select the row: {rail['selectedAfterEnter']!r}")
    assert rail["enterPreventedDefault"], "Enter must not also scroll or submit"


def test_space_selects_an_agent(report):
    """role='button' promises Space as well as Enter."""
    assert report["agentRail"]["selectedAfterSpace"] == "lead-1"


def test_the_row_control_does_not_also_select_the_row(report):
    assert report["agentRail"]["deleteKeyDidNotSelect"]


def test_the_row_control_is_a_labelled_button(report):
    """The ⋯ that opens the agent's profile.

    This asserted the same two things about the ✕ that used to delete an agent
    from the row. The control changed; the property did not — a trailing row
    control has to be a real button with a label, or it is unreachable from the
    keyboard and announces as nothing.
    """
    rail = report["agentRail"]
    assert rail["rowControlIsButton"], "the row control is not a <button>"
    assert rail["rowControlHasLabel"], "the row control announces only as '⋯'"


def test_every_agent_has_a_row_control_not_just_the_custom_ones(report):
    """The ✕ was on custom agents only, so a preset's row had no control at all
    — and the one thing you could do from the rail was the destructive one."""
    assert report["agentRail"]["rowControlCount"] == 2


# ── escape and focus ──────────────────────────────────────────────────────
def test_escape_does_nothing_when_no_modal_is_open(report):
    assert report["escape"]["closedWithNoneOpen"] == []


def test_opening_a_modal_moves_focus_into_it(report):
    """And onto a field, not onto the ✕ that happens to come first in the DOM."""
    assert report["escape"]["focusWentIntoA"] == ["modalA:field"]


def test_escape_closes_the_topmost_modal_via_its_own_close_button(report):
    assert report["escape"]["clickedForTopmost"] == ["modalB:close"], (
        "Escape closed the wrong modal, or bypassed its teardown")


def test_closing_a_modal_restores_focus_to_whatever_opened_it(report):
    """B was opened while focus sat in A's field, so closing B returns it there
    — not to the top of the page, which is where focus goes if nobody tracks
    it, and which silently strands a keyboard user."""
    assert report["escape"]["focusRestoredAfterClose"] == ["modalA:field"]


def test_escape_then_closes_the_one_underneath(report):
    assert report["escape"]["clickedForRemaining"] == ["modalA:close"]
