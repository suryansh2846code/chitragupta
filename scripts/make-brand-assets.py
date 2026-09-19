#!/usr/bin/env python3
"""Derive every brand asset the web UI uses from the one master mark.

The mark is the yantra in `docs/brand/yantra.png` — a 1024px **alpha
silhouette**, white throughout, shape carried entirely in the alpha channel.

Everywhere the logo appears it appears the same way: the ink on its own paper,
in a rounded tile, exactly as the Dock icon wears it. That is a deliberate
change from what these assets used to be. They were a *mask* — a silhouette the
surface painted in its own colour, white in the rail and gold on the onboarding
— which meant the logo was a different colour in every place it appeared and
none of them was the colour it was drawn in. One artwork, one appearance.

The dark UI is why a tile is needed rather than the ink alone: `#1b1b19` on the
`#03050a` rail is invisible. The tile carries its own paper with it, so the mark
is legible on any ground, including a light browser tab strip — which the gold
favicon it replaces was never reliable on.

    ./.venv/bin/python scripts/make-brand-assets.py

The master itself is derived, once, from the ink-on-paper artwork the mark was
drawn as — black yantra on a cream field, no alpha at all. `--from-art` redoes
that step: it reads the luminance as ink coverage, trims the paper away and
squares the result, so a redrawn logo can be dropped in without anyone hand-
cutting a silhouette in an image editor.

    ./.venv/bin/python scripts/make-brand-assets.py --from-art logo.png
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "docs" / "brand" / "yantra.png"
WEB = ROOT / "chitragupta" / "web"

# ── Sampled from the artwork, not from the brief ─────────────────────────────
# `docs/DESIGN-BRIEF.md` governs the product's surfaces; the logo is a specific
# piece of art, and these are its own two colours. Kept identical to the pair in
# `packaging/make-icon.py` so the tile and the Dock icon cannot drift apart.
PAPER = (253, 252, 248)
INK = (27, 27, 25)

MARK_FRAC = 0.78                 # mark width as a fraction of the tile
RADIUS_FRAC = 0.225              # corner radius, matching macOS proportions
SUPERSAMPLE = 4                  # draw big, downsample — Pillow has no AA

#: (path, pixel size). Sizes follow the `<link rel="icon">` tags in index.html
#: and onboarding.html, plus one tile big enough for the rail at 2x.
TARGETS = [
    (WEB / "brand-tile.png", 128),
    (WEB / "favicon-64.png", 64),
    (WEB / "favicon-32.png", 32),
]

ART_PAPER, ART_INK = 250.0, 35.0  # measured off the drawing: paper ~251, ink ~25-35
BLEED = 0.03                      # the margin the mark carried before this file


def master_from_art(art: Path, size: int = 1024) -> Image.Image:
    """Ink-on-paper artwork -> the square alpha silhouette we keep as master.

    Read as levels rather than thresholded, so the drawing's own antialiasing
    survives into the alpha channel — a hard cut leaves the lotus edges jagged
    at every size below 128.
    """
    grey = np.asarray(Image.open(art).convert("L"), dtype=np.float32)
    coverage = np.clip((ART_PAPER - grey) / (ART_PAPER - ART_INK), 0.0, 1.0)
    alpha = Image.fromarray((coverage * 255).round().astype(np.uint8), "L")

    # Trim the paper, then re-centre on a square — the yantra's side gates make
    # it wider than tall, and padding to the long edge keeps it from stretching.
    alpha = alpha.crop(alpha.point(lambda v: 255 if v > 24 else 0).getbbox())
    w, h = alpha.size
    side = round(max(w, h) * (1 + 2 * BLEED))
    square = Image.new("L", (side, side), 0)
    square.paste(alpha, ((side - w) // 2, (side - h) // 2))
    square = square.resize((size, size), Image.Resampling.LANCZOS)

    out = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    out.putalpha(square)
    return out


def tile(size: int) -> Image.Image:
    """The logo as it is worn everywhere: ink on paper, in a rounded tile.

    A circular-corner rounded rectangle, not the superellipse the Dock icon
    uses. At 16-128px the two are indistinguishable — the difference is what
    Apple's shape does over hundreds of pixels — and `packaging/make-icon.py`
    keeps the exact geometry for the one place it shows.
    """
    s = size * SUPERSAMPLE

    shape = Image.new("L", (s, s), 0)
    ImageDraw.Draw(shape).rounded_rectangle(
        [0, 0, s - 1, s - 1], radius=s * RADIUS_FRAC, fill=255)

    art = Image.new("RGBA", (s, s), (*PAPER, 255))
    mark_px = round(s * MARK_FRAC)
    alpha = Image.open(MASTER).convert("RGBA").getchannel("A")
    alpha = alpha.resize((mark_px, mark_px), Image.Resampling.LANCZOS)
    offset = round((s - mark_px) / 2.0)
    art.paste(INK, (offset, offset), alpha)

    art.putalpha(shape)
    return art.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-art", type=Path, metavar="PNG",
                    help="rebuild docs/brand/yantra.png from ink-on-paper artwork")
    args = ap.parse_args()

    if args.from_art:
        MASTER.parent.mkdir(parents=True, exist_ok=True)
        master_from_art(args.from_art).save(MASTER)
        print(f"✓ {MASTER.relative_to(ROOT)}  (master, from {args.from_art})")

    for path, size in TARGETS:
        tile(size).save(path)
        print(f"✓ {path.relative_to(ROOT)}  ({size}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
