"""The workspace has to keep looking like the product it ships inside.

`docs/DESIGN-BRIEF.md` picks one ground, one cool white, and exactly one warm
accent. The workspace had drifted a long way from it: a different near-black
from the onboarding's, a blue `--accent` doing forty different jobs, and three
separate Tailwind ramps pasted in around the provider cards. The user walked
through a gold-and-starlight onboarding and then landed somewhere blue.

None of that is catchable by eye once it is spread over 1,600 lines, so it is
caught here instead.
"""
import re
from pathlib import Path

import pytest

WEB = Path(__file__).parent.parent / "chitragupta/web"
CSS = (WEB / "styles.css").read_text()
INDEX = (WEB / "index.html").read_text()
ONBOARDING = (WEB / "onboarding.html").read_text()


def _root_tokens(text: str) -> dict:
    block = re.search(r":root\s*\{(.*?)\n\}", text, re.S)
    assert block, "no :root block"
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block.group(1)))


# ── one palette, not three ────────────────────────────────────────────────
#: The generic-AI blue the brief exists to avoid, plus the Tailwind defaults
#: that arrived with the provider cards. Every one of these was in the file.
BANNED = [
    "#6ea8fe", "110,168,254", "#3b82f6", "#60a5fa", "#7ec0ff", "#3f7fe0",
    "#10b981", "#34d399", "#a5b4fc", "#f59e0b", "#fbbf24", "#facc15",
    "#2dd4bf", "#c084fc", "#9ca3af", "#a1a1aa", "#71717a", "#6b7280",
    "#f3f4f6", "#f4f4f5", "#e5e7eb", "#d1d5db", "#fafafa", "#ededed",
]


@pytest.mark.parametrize("literal", BANNED)
def test_no_off_palette_literals_in_the_workspace(literal):
    assert literal not in CSS, (
        f"{literal} is back in styles.css — use a token from :root instead")


def test_gold_is_the_accent_and_blue_is_gone():
    tokens = _root_tokens(CSS)
    assert tokens["--north"].strip() == "#f5c877"
    # --accent is the legacy name ~40 rules still use; it must point at gold.
    assert "--north" in tokens["--accent"], (
        f"--accent is {tokens['--accent']!r}, so those rules are not gold")


def test_workspace_and_onboarding_stand_on_the_same_ground():
    """The seam this whole pass existed to close."""
    ws, ob = _root_tokens(CSS), _root_tokens(ONBOARDING)
    for token in ("--ground", "--star", "--north"):
        assert ws[token].strip() == ob[token].strip(), (
            f"{token} differs: workspace {ws[token]!r} vs onboarding {ob[token]!r}")


def test_every_css_variable_used_is_defined():
    """`var(--text-dim, …)` silently resolved to its fallback forever."""
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", CSS))
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", CSS))
    assert not (used - defined), f"undefined CSS variables: {sorted(used - defined)}"


# ── motion ────────────────────────────────────────────────────────────────
def test_reduced_motion_is_respected():
    """The brief asks for a static field. There is a canvas particle loop, a
    pulsing status dot and three spinners; none of it asked."""
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    # The app's JavaScript, not `app.js` — `_lessMotion` lives in core.js now,
    # and a grep of one file out of several passes for the wrong reason.
    from web_sources import app_source
    assert "prefers-reduced-motion" in app_source(), (
        "the canvas is a rAF loop that CSS cannot reach — it has to ask too")


# ── accessible names ──────────────────────────────────────────────────────
BUTTON = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.S)


def _icon_only_buttons():
    """Buttons whose visible text is empty or a lone glyph like ✕ or ‹."""
    for attrs, inner in BUTTON.findall(INDEX):
        if "hidden" in attrs:
            continue
        text = re.sub(r"<[^>]+>", "", inner).strip()
        if len(text) <= 1:
            yield attrs, text


def test_icon_only_buttons_have_an_accessible_name():
    """`title` is a tooltip. A screen reader announces such a button as
    'button', and the ✕ ones as 'times'.

    ``data-soon`` is the one other source of a name, and it is a stronger one,
    not an exemption: `markSoon()` writes the label *and* the tooltip from that
    single string, so the two cannot drift the way two hand-written copies can.
    An `aria-label` spelled out here as well would be the copy that drifts, and
    `markSoon` would overwrite it anyway. That the name really lands is asserted
    at runtime in ``test_frontend_locked_controls.py``, which clicks the control
    rather than reading the markup.
    """
    unnamed = [
        re.search(r'id="([^"]+)"', a).group(1) if 'id="' in a else a.strip()[:40]
        for a, _ in _icon_only_buttons()
        if "aria-label" not in a and "data-soon" not in a
    ]
    assert not unnamed, f"icon-only buttons with no accessible name: {unnamed}"


def test_every_dialog_is_labelled_by_an_element_that_exists():
    ids = set(re.findall(r'id="([^"]+)"', INDEX))
    for ref in re.findall(r'aria-labelledby="([^"]+)"', INDEX):
        assert ref in ids, f"aria-labelledby points at #{ref}, which does not exist"


def test_modals_announce_themselves_as_modal_dialogs():
    opened = INDEX.count('class="modal-bg"')
    dialogs = INDEX.count('role="dialog"')
    assert dialogs >= opened, (
        f"{opened} modal backgrounds but only {dialogs} role=dialog")
