#!/usr/bin/env python3
"""Draw `packaging/icon.icns` from the product's own design tokens.

The icon is generated rather than hand-drawn so it cannot drift from the brief
it is supposed to embody. Every colour here is copied from `docs/DESIGN-BRIEF.md`
and `chitragupta/web/styles.css` — the night-sky ground, the cool-white particles,
and the one warm gold pole-star that is the app's single accent.

The mark is the yantra the sidebar wears (`.brand-mark` in styles.css, cut from
`docs/brand/yantra.png`), laid on the night sky with its **bindu** — the point
at the centre — carrying the gold. That placement is the brand hook in a shape:
a field of scattered things, and one bright point for the one being written down.

What the diamond version of this icon drew inside the mark — a constellation and
a needle reaching up to a pole star — is gone. It could be, because the diamond
was an outline with an empty middle; the yantra is a filled silhouette with its
own lattice, and anything drawn behind it is simply not visible. The particle
field around it still carries that half of the idea.

Three things make it survive being shrunk to 16px in a Finder list:

* the gold bindu is the only saturated thing in the frame, so it reads as a dot
  of colour long after the lattice behind it has blurred to texture,
* the detail drops in tiers rather than scaling (see `render`), and
* at 32px and below the mark falls back to its own core, because the full
  yantra at that size is a smudge — the same finding `styles.css` records for
  the rail.

Deterministic on purpose — a fixed seed means rebuilding produces byte-identical
PNGs, so an icon change shows up in review as an intentional diff.

    ./.venv/bin/python packaging/make-icon.py
"""
from __future__ import annotations

import math
import random
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
MASTER = HERE.parent / "docs" / "brand" / "yantra.png"

# ── Tokens, lifted from the brief ────────────────────────────────────────────
GROUND_TOP = (10, 14, 26)        # #0a0e1a — the sky is not flat; it lifts
GROUND_BOT = (3, 5, 10)          # #03050a — --rail
STAR = (223, 231, 242)           # #dfe7f2 — --star, cool white
NORTH = (245, 200, 119)          # #f5c877 — --north, the ONE accent

# macOS icon geometry (Big Sur and later): on a 1024pt canvas the rounded
# square occupies 824pt, centred, leaving the margin the system expects. Getting
# this wrong is why a custom icon looks subtly too big next to Apple's.
CANVAS = 1024
SQUIRCLE = 824
SUPERSAMPLE = 2                  # draw at 2x, downsample — Pillow has no AA

# The mark's footprint inside the squircle. Smaller than the diamond's was: the
# yantra is a dense lattice where the diamond was two strokes, and at the old
# 0.57 it crowded the corners and lost the sky it is supposed to sit in.
MARK_FRAC = 0.62
BINDU_R = 0.085                  # bindu radius, measured off the master art
CORE_FRAC = 0.345                # the inner square's half-width in the master,
                                 # which is what sets the bindu's ratio once the
                                 # core is drawn at full size instead of cropped

#: Every size `iconutil` requires, as (pixel size, iconset filename).
ICONSET = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]


def _squircle_mask(size: int, radius_ratio: float = 0.2265) -> Image.Image:
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
    # r is Apple's corner radius; the exponent shapes how quickly it turns.
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


def _ground(size: int) -> Image.Image:
    """A vertical gradient, so the sky has a top and a bottom."""
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(                       # type: ignore[assignment]
            round(GROUND_TOP[c] + (GROUND_BOT[c] - GROUND_TOP[c]) * t)
            for c in range(3))
    return img.resize((size, size), Image.Resampling.BILINEAR)


def _glow(size: int, centre: tuple[float, float], radius: float,
          colour: tuple[int, int, int], strength: float) -> Image.Image:
    """A soft radial bloom, drawn as a blurred disc on its own layer."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = centre
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                 fill=(*colour, round(255 * strength)))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.55))


def _mark(px: int, core: bool, gold: bool) -> Image.Image:
    """The yantra silhouette at `px` square, star-white, with a gold bindu.

    `core` swaps the lattice for the shape at the heart of it — the rotated
    square with the bindu inside — drawn rather than cropped. Cropping the
    master was tried first and failed: the crop that holds the inner square also
    catches the lotus tips and the gate stubs, and at 32px those arrive as grey
    noise around the centre instead of as the mark. Drawn, the same geometry is
    two clean shapes that survive the size.
    """
    if core:
        mark = Image.new("RGBA", (px, px), (*STAR, 0))
        draw = ImageDraw.Draw(mark)
        c, h = px / 2.0, px / 2.0
        draw.polygon([(c, c - h), (c + h, c), (c, c + h), (c - h, c)],
                     fill=(*STAR, 236))
        if gold:
            # Bigger than the master's bindu in proportion, because the square
            # around it is bigger too — the ratio is what has to hold, not the
            # absolute radius.
            r = px * BINDU_R / (2 * CORE_FRAC)
            draw.ellipse([c - r, c - r, c + r, c + r], fill=(*NORTH, 255))
        return mark

    alpha = Image.open(MASTER).convert("RGBA").getchannel("A")
    alpha = alpha.resize((px, px), Image.Resampling.LANCZOS)
    mark = Image.new("RGBA", (px, px), (*STAR, 0))
    mark.paste((*STAR, 238), (0, 0), alpha)
    if not gold:
        return mark

    # The bindu, over the top, clipped to the mark's own alpha — so the gold can
    # never spill past the dot the artwork actually draws at the centre.
    r, c = px * BINDU_R, px / 2.0
    dot = Image.new("RGBA", (px, px), (*NORTH, 0))
    ImageDraw.Draw(dot).ellipse([c - r, c - r, c + r, c + r], fill=(*NORTH, 255))
    dot.putalpha(ImageChops.multiply(dot.getchannel("A"), alpha))
    mark.alpha_composite(dot)
    return mark


def render(size: int) -> Image.Image:
    """The artwork, at `size` px square, alpha outside the squircle.

    The detail drops in tiers, the way an icon set is drawn rather than scaled.
    Each threshold below was chosen by rendering the size and looking at it:

    * **>=256** everything: particle field, bloom, full yantra, gold bindu.
    * **>=64** drops the particles. Against the yantra's own lattice — far busier
      than the diamond this replaced — they stop being texture and become dirt.
    * **>=32** falls back to the mark's core — the rotated square and its bindu
      — and drops the bloom, which at that size stops being a light source
      behind the gold and becomes a brown haze over it.
    * **16** is that square alone. The gold goes: a bindu that small smears into
      the white around it and turns the whole icon muddy.
    """
    s = size * SUPERSAMPLE
    scale = s / CANVAS                       # everything below is in 1024-space
    particles = size >= 256
    core = size < 64
    bloom = size >= 64
    gold = size >= 32

    def u(v: float) -> float:
        return v * scale

    inset = (CANVAS - SQUIRCLE) / 2.0
    box = (u(inset), u(inset), u(inset + SQUIRCLE), u(inset + SQUIRCLE))
    cx = cy = s / 2.0

    # ── the night sky, clipped to the squircle ──────────────────────────────
    art = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ground = _ground(round(u(SQUIRCLE))).convert("RGBA")
    art.paste(ground, (round(box[0]), round(box[1])))

    # The mark grows as the detail falls away — with nothing else in the frame
    # at 16px it can afford the room, and it needs it to read.
    frac = MARK_FRAC if not core else (0.66 if gold else 0.70)
    mark_px = round(u(SQUIRCLE * frac))
    half_m = mark_px / 2.0

    # A gold bloom behind the bindu lifts the sky locally, so the accent looks
    # like a light source rather than a sticker. It shrinks faster than the icon
    # does — at small sizes the bloom would otherwise BE the icon.
    if bloom:
        art.alpha_composite(
            _glow(s, (cx, cy), u(190) if particles else u(64), NORTH,
                  0.22 if particles else 0.55))
    if particles:
        art.alpha_composite(_glow(s, (cx, cy + u(140)), u(300), STAR, 0.035))

    # ── particle field ──────────────────────────────────────────────────────
    if particles:
        draw = ImageDraw.Draw(art)
        rng = random.Random(20260916)        # fixed seed → reproducible PNGs
        for _ in range(90):
            px_, py_ = rng.uniform(box[0], box[2]), rng.uniform(box[1], box[3])
            # Keep the field off the mark; texture must not fight the shape.
            if max(abs(px_ - cx), abs(py_ - cy)) < half_m * 1.10:
                continue
            r = u(rng.uniform(1.6, 4.4))
            a = round(255 * rng.uniform(0.07, 0.30))
            draw.ellipse([px_ - r, py_ - r, px_ + r, py_ + r], fill=(*STAR, a))

    # ── the mark ────────────────────────────────────────────────────────────
    art.alpha_composite(_mark(mark_px, core, gold),
                        (round(cx - half_m), round(cy - half_m)))

    # ── clip, and downsample to the requested size ──────────────────────────
    mask = Image.new("L", (s, s), 0)
    mask.paste(_squircle_mask(round(u(SQUIRCLE))), (round(box[0]), round(box[1])))
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
