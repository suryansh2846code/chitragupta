"""The avatar renderer the app serves is the one in `character/`.

`character/` is a standalone, dependency-free package meant to be lifted into
its own repository (`git subtree split --prefix=character`). The app must not
import across that line, and `chitragupta/web/` has to hold everything the built
`.dmg` serves — so the bundle is *copied* there by `scripts/sync-character.sh`.

A copied file rots. This test is the thing that stops it: edit the package,
forget to sync, and the next run says so. Without it the failure mode is a user
launching the app months later against an avatar renderer that predates every
fix made since — and nothing anywhere would have complained.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "character" / "dist" / "character.global.js"
SERVED = ROOT / "chitragupta" / "web" / "character.js"
INDEX = ROOT / "chitragupta" / "web" / "index.html"


def test_the_served_bundle_matches_the_package():
    assert SERVED.exists(), "chitragupta/web/character.js is missing — run scripts/sync-character.sh"
    if not SOURCE.exists():
        pytest.skip("character/dist is not built here; the served copy is what ships")
    assert SERVED.read_bytes() == SOURCE.read_bytes(), (
        "chitragupta/web/character.js is out of date. Run scripts/sync-character.sh."
    )


def test_the_page_loads_the_renderer_before_anything_that_draws_with_it():
    """`core.js` calls `Character` from its own render paths.

    Script order in `index.html` is the dependency graph — see `web/CLAUDE.md`.
    A renderer loaded after its callers is not a late avatar; it is a
    `ReferenceError` at the top of the file every other script depends on.
    """
    html = INDEX.read_text()
    srcs = re.findall(r'<script\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', html)
    assert "/static/character.js" in srcs, "index.html does not load the avatar renderer"
    assert srcs.index("/static/character.js") < srcs.index("/static/core.js")


def test_the_bundle_is_self_contained():
    """No network at runtime, ever.

    The renderer is drawn into a page served from a random loopback port, and
    an avatar that reaches a CDN would be a local-first product phoning out for
    a face. It also taints the export canvas, which breaks "Download PNG"
    without breaking anything visible.
    """
    text = SERVED.read_text()
    for forbidden in ("https://cdn", "http://cdn", "//unpkg.com", "//cdnjs.", "importScripts("):
        assert forbidden not in text, f"the avatar bundle references {forbidden}"
    # `fetch(` and `XMLHttpRequest` would each mean the same thing.
    assert "XMLHttpRequest" not in text
    assert not re.search(r"\bfetch\s*\(", text), "the avatar bundle makes a network request"


def test_the_bundle_exposes_what_the_app_calls():
    """The app's whole contract with the package, in one place.

    Each of these is called by name from `core.js` or `appearance.js`. A rename
    inside the package would otherwise surface as a blank rail at runtime rather
    than as a failing test.
    """
    text = SERVED.read_text()
    for name in ("generateScene", "renderToString", "createCharacter", "mountEditor"):
        assert re.search(rf"\b{name}:", text), f"the avatar bundle no longer exports {name}"
