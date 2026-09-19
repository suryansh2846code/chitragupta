#!/usr/bin/env python3
"""Draw `packaging/icon.icns` from the mark as it was actually drawn.

The icon is the artwork, not an interpretation of it: the yantra in its own ink
on its own paper, scaled up to fill the tile. Nothing is added — no night sky,
no particle field, no gold on the bindu. An earlier version of this file put the
mark on the brand's dark ground with the bindu picked out in `--north`, and it
was wrong twice over. It read small — a busy lattice floating in a wide dark
margin, where the icons beside it in the Dock fill their tiles — and it was no
longer the mark anyone had approved.

So the two colours here are measured off the drawing rather than lifted from
`docs/DESIGN-BRIEF.md`. The brief's tokens govern the product's surfaces; this
file's job is to reproduce a specific piece of art, and sampling it is the only
way to be sure the Dock shows what the designer drew.

The shape comes from `docs/brand/yantra.png`, the same alpha master the web
assets are cut from, so the icon cannot drift from the mark in the sidebar.

One treatment at every size, deliberately. The tiered version this replaced
swapped in a simplified core below 64px; that is a real technique, but it means
the small icon is a different shape from the large one, and the mark is supposed
to stay the mark. Ink on paper carries far better when shrunk than white-on-dark
did anyway — the contrast is higher and the mark is now much bigger in frame.

Deterministic: rebuilding produces byte-identical PNGs, so an icon change shows
up in review as an intentional diff.

    ./.venv/bin/python packaging/make-icon.py
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
MASTER = HERE.parent / "docs" / "brand" / "yantra.png"

# ── Sampled from the artwork, not from the brief ─────────────────────────────
PAPER = (253, 252, 248)          # the warm off-white the mark was drawn on
INK = (27, 27, 25)               # the near-black it was drawn in

# macOS icon geometry (Big Sur and later): on a 1024pt canvas the rounded
# square occupies 824pt, centred, leaving the margin the system expects. Getting
# this wrong is why a custom icon looks subtly too big next to Apple's.
CANVAS = 1024
SQUIRCLE = 824
SUPERSAMPLE = 2                  # draw at 2x, downsample — Pillow has no AA

# How much of the tile the mark covers. The yantra's gates reach the edge of its
# own bounding box, so this is close to the practical maximum — past ~0.80 the
# gate tips start to graze the squircle's corners once it rounds them.
MARK_FRAC = 0.78

#: Every size `iconutil` requires, as (pixel size, iconset filename).
ICONSET = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]


def _squircle_mask(size: int) -> Image.Image:
    """Apple's rounded square, as a superellipse rather than circular corners.

    `ImageDraw.rounded_rectangle` gives circular corners, which read as visibly
    rounder and softer than the system shape when the icon sits in a dock beside
    Apple's own. A superellipse of exponent ~5 is the standard approximation and
    costs nothing here.
    """
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    n = 5.0
    half = size / 2.0
    points = []
    steps = 2048
    for i in range(steps):
        t = 2.0 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        x = half * math.copysign(abs(ct) ** (2.0 / n), ct)
        y = half * math.copysign(abs(st) ** (2.0 / n), st)
        points.append((half + x, half + y))
    draw.polygon(points, fill=255)
    return mask


def render(size: int) -> Image.Image:
    """The artwork, at `size` px square, alpha outside the squircle."""
    s = size * SUPERSAMPLE
    scale = s / CANVAS                       # everything below is in 1024-space
    inset = round((CANVAS - SQUIRCLE) / 2.0 * scale)
    side = round(SQUIRCLE * scale)

    art = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    art.paste(Image.new("RGBA", (side, side), (*PAPER, 255)), (inset, inset))

    # The mark, in ink, at the master's own alpha — so the drawing's antialiasing
    # is what softens the edges rather than anything invented here.
    mark_px = round(side * MARK_FRAC)
    alpha = Image.open(MASTER).convert("RGBA").getchannel("A")
    alpha = alpha.resize((mark_px, mark_px), Image.Resampling.LANCZOS)
    offset = round((s - mark_px) / 2.0)
    art.paste(INK, (offset, offset), alpha)

    # ── clip, and downsample to the requested size ──────────────────────────
    mask = Image.new("L", (s, s), 0)
    mask.paste(_squircle_mask(side), (inset, inset))
    art.putalpha(Image.composite(art.getchannel("A"), Image.new("L", (s, s), 0), mask))
    return art.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    if shutil.which("iconutil") is None:
        print("iconutil not found — macOS only.", file=sys.stderr)
        return 1

    iconset = HERE / "icon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir()

    master = render(1024)
    for px, filename in ICONSET:
        img = master if px == 1024 else render(px)
        img.save(iconset / filename)

    out = HERE / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    shutil.rmtree(iconset, ignore_errors=True)
    print(f"✓ {out}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
