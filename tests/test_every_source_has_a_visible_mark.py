"""Every source gets a mark, and every mark can actually be seen.

Two decisions, and the second is the one that nearly shipped broken.

**Where a mark comes from.** The vendor's own published path wins, because it
is their shape rather than our recollection of it. Then the marks drawn by
hand in `connectors.js`, which cover the multi-colour ones nobody publishes as
a single path. Then a monogram — a letter, deliberately, rather than a rough
approximation of somebody's logo.

**What colour it is drawn in.** A single-path mark is filled with the brand's
own hex, and several brands are near-black by definition: GitHub is `#181717`,
Notion is `#000000`. Filled straight onto this ground those are marks you
cannot see at all — which is why the hand-drawn GitHub was already ink and not
black. So a colour below the floor is lifted toward white, keeping its hue.

The marks are fetched once by `scripts/fetch-brand-marks.py` and committed.
The page must never fetch one: an asset pulled from a vendor's CDN is a request
telling them which app the user is running, and not doing that is the product.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from chitragupta.connectors import REGISTRY
from chitragupta.connectors.mcp_catalog import CATALOG

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONNECTORS_JS = ROOT / "chitragupta" / "web" / "connectors.js"
HARNESS = ROOT / "tests" / "js" / "marks.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

#: Every source a user can see a row for, on either half of the screen.
EVERY_ID = sorted({*REGISTRY, *(e.id for e in CATALOG)})


def marks(ids: list[str]) -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), str(CONNECTORS_JS)],
        input=json.dumps(ids), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def test_every_source_resolves_to_something_drawable():
    """No blanks. A row with no mark is the one the eye skips."""
    for source, got in marks(EVERY_ID).items():
        assert got["kind"] in ("vendor", "drawn", "monogram"), source


def test_no_mark_is_too_dark_to_see():
    """The bug this file exists for. GitHub and Notion publish near-black, and
    the mark is filled with exactly that colour."""
    for source, got in marks(EVERY_ID).items():
        if got["luma"] is None:
            continue                      # derived hsl(), bright by construction
        assert got["luma"] >= 0.30, (
            f"{source} would be drawn at luma {got['luma']:.2f} on a dark "
            f"ground — {got['tint']}")


def test_a_bright_brand_keeps_its_own_colour():
    """The floor must only lift what needs lifting. Stripe is Stripe's purple
    and must not be washed out by a rule aimed at black."""
    got = marks(["stripe", "telegram", "canva"])

    assert got["stripe"]["tint"].lower() == "#635bff"
    assert got["telegram"]["tint"].lower() == "#26a5e4"


def test_the_two_that_publish_black_are_lifted():
    got = marks(["github", "notion"])

    for source in ("github", "notion"):
        assert got[source]["tint"].startswith("rgb("), (
            f"{source} kept its published near-black")
        assert got[source]["luma"] > 0.4


def test_apple_health_is_a_heart_and_not_apples_logo():
    """The icon set publishes Apple's logo under `apple`, and Apple Health is
    a heart. A vendor's mark is not necessarily the app's, and borrowing one
    for the other is a wrong answer that looks like a right one."""
    source = CONNECTORS_JS.read_text()

    assert "apple_health:" not in source.split("const CONNECTOR_ICONS")[0]
    assert got_heart(source), "the hand-drawn Apple Health heart is missing"
    assert marks(["apple_health"])["apple_health"]["kind"] == "drawn"


def got_heart(source: str) -> bool:
    block = source[source.index("apple_health: `"):]
    return "#FF2D55" in block[:400]


def test_the_page_never_fetches_a_mark():
    """The whole reason these are committed rather than linked. One `<img
    src=https://…>` here and every launch tells a vendor which app is running."""
    source = CONNECTORS_JS.read_text()
    marks_block = source[source.index("const BRAND_MARKS"):]
    marks_block = marks_block[:marks_block.index("\n};")]

    assert "http" not in marks_block
    assert not re.search(r"<img|fetch\(|url\(", marks_block)
