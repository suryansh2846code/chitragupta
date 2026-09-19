#!/usr/bin/env python3
"""Derive the web brand assets from the one master mark.

The mark is the yantra in `docs/brand/yantra.png` — a 1024px **alpha
silhouette**, white pixels throughout, shape carried entirely in the alpha
channel. It is stored that way on purpose: every surface that wears the mark
(`.brand-mark` in the rail, `.brand .mark` on onboarding) paints it with CSS
`mask`, taking its colour from the surface around it. A file with a colour baked
in would need a second file the day the palette moves.

From that one master this script writes:

* `brand-mark.png` (128) — white + alpha, the mask source for both surfaces.
* `favicon-64.png` / `favicon-32.png` — gold (`--north`) + alpha. The tab is the
  one place CSS cannot reach, so the accent is baked in there; gold is the only
  hue in the palette that holds against both a light and a dark tab strip.

The 3% bleed matches what the mark carried before, so swapping the file does not
change the optical weight of the rail. Sizes follow the `<link rel="icon">`
tags in `index.html` and `onboarding.html`.

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
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "docs" / "brand" / "yantra.png"
WEB = ROOT / "chitragupta" / "web"

NORTH = (245, 200, 119)          # #f5c877 — --north, the one accent
STAR = (255, 255, 255)           # the mask source is pure white; CSS colours it

#: (path, pixel size, RGB to flood behind the alpha)
TARGETS = [
    (WEB / "brand-mark.png", 128, STAR),
    (WEB / "favicon-64.png", 64, NORTH),
    (WEB / "favicon-32.png", 32, NORTH),
]


#: Measured off the artwork: the paper sits at ~251 and the ink at ~25-35. They
#: are read as levels rather than thresholded so the drawing's own antialiasing
#: survives into the alpha channel — a hard cut would leave the lotus edges
#: jagged at every size below 128.
ART_PAPER, ART_INK = 250.0, 35.0
BLEED = 0.03                     # the margin the mark carried before this file


def master_from_art(art: Path, size: int = 1024) -> Image.Image:
    """Ink-on-paper artwork -> the square alpha silhouette we keep as master."""
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
    return tint(square.resize((size, size), Image.Resampling.LANCZOS), STAR)


def tint(alpha: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    """A flat colour cut to `alpha` — the shape lives only in the alpha channel."""
    flat = Image.new("RGB", alpha.size, rgb)
    out = flat.convert("RGBA")
    out.putalpha(alpha)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-art", type=Path, metavar="PNG",
                    help="rebuild docs/brand/yantra.png from ink-on-paper artwork")
    args = ap.parse_args()

    if args.from_art:
        MASTER.parent.mkdir(parents=True, exist_ok=True)
        master_from_art(args.from_art).save(MASTER)
        print(f"✓ {MASTER.relative_to(ROOT)}  (master, from {args.from_art})")

    master = Image.open(MASTER).convert("RGBA").getchannel("A")
    for path, size, rgb in TARGETS:
        # LANCZOS on the alpha alone: resampling the composite would drag the
        # colour into the transparent margin and fringe the edge.
        tint(master.resize((size, size), Image.Resampling.LANCZOS), rgb).save(path)
        print(f"✓ {path.relative_to(ROOT)}  ({size}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
