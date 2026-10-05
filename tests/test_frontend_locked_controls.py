"""A control that is drawn but not built yet says so, and refuses the tap.

The mic and the waveform in the composer shipped as full-strength buttons with
no click handler anywhere. "Never show a control that cannot work" is the rule,
and the one shape allowed past it is a control that *says* it cannot work yet:
dulled, lock-badged, and answering a tap with when it arrives.

These assertions are about the shape rather than those two buttons. Every
``data-soon`` control in ``index.html`` is run through the real ``markSoon`` and
clicked, so a third one added later is covered without touching this file — and
every assertion is parametrised over whatever the page marks.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"


@pytest.fixture(scope="module")
def run() -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/locked_controls.mjs"), str(WEB / "app.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def controls(run) -> list[dict]:
    """Zipped so each assertion names the control it failed on.

    ``strict`` because the three lists are one list of controls, reported in
    three pieces — a short one means the harness dropped a control somewhere,
    and silently truncating would hide exactly the control that broke.
    """
    return [
        {**c, **p, **k}
        for c, p, k in zip(run["marked"], run["painted"], run["clicked"], strict=True)
    ]


def test_the_page_marks_the_controls_that_cannot_work_yet(run):
    """Without this the rest of the file passes by having nothing to check."""
    ids = {c["id"] for c in run["marked"]}
    assert {"micBtn", "waveformBtn"} <= ids, (
        f"the composer's voice buttons are not marked data-soon — found {ids}")


def test_a_tap_is_refused_wherever_it_lands(run):
    """Delegated and in the capture phase, so a control drawn later is covered
    and no handler one of them might grow can run first."""
    assert run["listenerInstalled"], (
        "core.js installed no capture-phase click listener, so every locked "
        "control is locked only in appearance")


def test_it_is_dulled(controls):
    for c in controls:
        assert "is-soon" in c["classes"], (
            f"{c['id']} carries data-soon but not the class that dims it and "
            "draws the lock — it still reads as a live button")


def test_it_reads_as_unavailable_to_a_screen_reader(controls):
    for c in controls:
        assert c["aria"] == "true", f"{c['id']} is not aria-disabled"


def test_it_says_what_it_is_and_that_it_is_coming(controls):
    """The one thing the user asked for: a reminder, not a dead end."""
    for c in controls:
        for where in ("title", "label"):
            text = c[where] or ""
            assert c["soon"] in text, (
                f"{c['id']}'s {where} is {text!r} and never names {c['soon']!r}")
            assert "locked" in text.lower(), f"{c['id']}'s {where} never says it is locked"
            assert "future update" in text.lower(), (
                f"{c['id']}'s {where} is {text!r} — it says no when it arrives, "
                "which is the half that makes it a roadmap rather than a refusal")


def test_the_tooltip_and_the_label_cannot_drift_apart(controls):
    """Both are written from `data-soon`, so they are one string, not two."""
    for c in controls:
        assert c["title"] == c["label"], (
            f"{c['id']} shows {c['title']!r} and announces {c['label']!r}")


def test_clicking_one_explains_instead_of_doing_nothing(controls):
    for c in controls:
        assert c["prevented"] and c["stopped"], (
            f"a click on {c['id']} was not stopped")
        assert c["soon"] in (c["toast"] or ""), (
            f"clicking {c['id']} toasted {c['toast']!r}")


def test_a_locked_control_is_still_reachable_by_keyboard(controls):
    """It exists to be read. `tabindex="-1"` hides the explanation from exactly
    the user least able to see that the button is dimmed."""
    for c in controls:
        assert c["tabindex"] != "-1", (
            f"{c['id']} is out of the tab order, so a keyboard user never "
            "learns why it is dulled")


def test_the_lock_is_painted_by_css_not_by_a_child_node(controls):
    """`applyIcons()` assigns innerHTML to these buttons, so a badge appended
    inside one would be wiped on the next repaint."""
    css = (WEB / "styles.css").read_text()
    assert ".is-soon::after" in css, "the lock badge is not a pseudo-element"
    assert re.search(r"\.is-soon\s*\{[^}]*opacity", css), (
        "`.is-soon` does not dim anything — the lock would sit on a button "
        "that still looks live")


def test_hover_does_not_restore_it_to_full_strength():
    """Every control in this app lifts on hover. A locked one lifting back to
    normal is the exact "it works, I promise" signal this exists to avoid."""
    css = (WEB / "styles.css").read_text()
    hover = re.search(r"\.is-soon:hover[^{]*\{([^}]*)\}", css)
    assert hover, "`.is-soon` has no hover rule, so `.cmp-bar-btn:hover` wins"
    assert "opacity" in hover.group(1), (
        "the hover rule does not pin the opacity, so hovering can brighten it "
        "back to a live-looking button")
