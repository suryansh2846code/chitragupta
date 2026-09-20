# Chitragupta — Design Brief ("Living Constellation")

> The visual identity for the app + onboarding. Chosen 3 Sep 2026 from a
> reference the user picked: the **dark generative particle / point-cloud**
> aesthetic (near-black, white particle fields forming organic shapes,
> monospace/HUD type, constellation/node graphs).

## Brand hook
**Chitragupta** is the keeper of the record — in Hindu tradition the scribe who
holds the account of what each person has actually done. That is the product:
it remembers your work, and it can always say where a thing came from. So the
brain is a **living constellation** — memories as drifting star-particles, the
knowledge graph as the lines between them — on a night sky, with one warm gold
star for the point currently being written down.

(The identity predates the name and survived it: the night sky, the particle
field and the single gold accent were chosen for the *brain*, not for the
wordmark, which is why renaming cost nothing here. The `--north` token keeps its
name as a colour, not as a claim about compasses.)

## Aesthetic: "Living Constellation"
**Charcoal ground** · cool-white drifting particles that wire into a
constellation · **monospace-forward** technical type · hairline **HUD** overlays
(coordinates, brackets, status) · one warm **gold "true-north" accent**.

The ground is a **blackboard**: a charcoal slate you write on, which is what the
product is. It was a cool blue near-black until 2026-09-19; every surface carried
a blue cast, and against the gold it pulled the shell toward "console". Charcoal
is quieter and lets the one accent be the only colour in the room. The particles
and the constellation are unchanged — they are chalk on slate now rather than
stars on sky, and the metaphor survives the swap.

The hue is a hair warm (~30°, ~4% saturation) rather than dead neutral: a
perfectly grey charcoal turns slightly blue beside the gold and beside the cream
of the brand tile.

## Tokens
The grounds are a **ramp**, not independent colours — the rail is the chrome the
workspace floats on, `--surface` sits deliberately between it and `--panel` so
cards drawn in `--panel` still read as raised. Keep the order when adjusting any
of them.

- `--rail: #151413` · `--ground: #191817` · `--surface: #1d1c1a` · `--ground-2: #232120`
- raised fills: `--panel-2: #2a2826` · `--user: #2f2d2b` · `--raise: #35322f`
- `--star: #dfe7f2` (cool white — particles + text) · `--muted: #928c85` · `--faint: #756f68`
- `--line: rgba(223,231,242,.10)` (hairlines)
- `--north: #f5c877` (warm pole-star gold — the ONE accent: live/hover/primary)
- `--north-dim: rgba(245,200,119,.14)`

`--muted` and `--faint` are warm greys because the ground is. They were blue-greys
chosen against a blue ground; left alone they read as a mistake rather than as a
shade. Their contrast against `--ground` is matched to what they replaced (~2.8:1
and ~2.1:1), so nothing became harder to read.

**Onboarding declares its own copy** of this palette — it is a standalone page —
so both must move together. Changing one alone is how the seam between arriving
and being here comes back.

## Mark
A **yantra** — the bhupura square with its four gates, a lotus ring, the inner
rotated square, and the **bindu** at the centre. It is drawn art, and it is kept
as drawn: the mark is not restyled per surface, and nothing is picked out of it
for emphasis.

**One appearance, everywhere.** The logo is the ink (`#1b1b19`) on its own paper
(`#fdfcf8`), in a rounded tile — in the Dock, in the rail, on the onboarding and
in the browser tab. Both colours are sampled from the drawing rather than taken
from the tokens above: the brief governs the product's surfaces, and the logo is
a specific piece of art. It is the one thing in the product the palette does not
reach, and the tokens must not be applied to it.

This replaced a masking scheme where each surface tinted the silhouette itself —
white in the rail, `--north` gold on the onboarding, gold again in the tab. It
saved a file and cost the identity: three colours, none of them the one the mark
was drawn in. The tile also brings its own ground, so the mark survives any
surface; the reversed-out version disappeared on a light browser tab strip.

One master, `docs/brand/yantra.png` — 1024px, white, the shape carried entirely
in the **alpha channel**. Nothing downstream is hand-cut:

- `scripts/make-brand-assets.py` writes `brand-tile.png` (rail + onboarding) and
  `favicon-32/64.png`.
- `packaging/make-icon.py` writes `icon.icns`, which keeps Apple's exact
  superellipse; the web tiles use a plain rounded rectangle, because below 128px
  the two are indistinguishable.

It does not shrink forever. Below ~20px the lotus and the gates merge and it
stops being this mark, which is why the rail runs it at 22px. Nothing simplifies
the mark at small sizes — not the favicon, not the icon. A simplified small size
would mean the logo in a Finder list is a different shape from the logo in the
Dock, and ink on paper survives the shrink better than a reversed-out mark does
anyway: the contrast is higher and the mark sits bigger in frame.

## Type
- **Mono-forward** (system mono: `ui-monospace, 'SF Mono', 'JetBrains Mono',
  Menlo, monospace`). Headline = mono, light (300), tight letter-spacing.
  Labels = uppercase mono, letter-spaced. Body may use system sans for long reads.
- Distinctly *not* Inter / Space Grotesk (the AI defaults).

## Motion
- Page-load: particles drift in + wire into faint constellation lines (brain
  "igniting"); headline rises/fades in.
- Cursor: nearby particles connect to the pointer with hairlines.
- One pulsing gold pole-star. Respect `prefers-reduced-motion` (static field).

## Layout principles
- Full-viewport **Canvas** particle field as living background.
- **Left-aligned** content column (centered is the AI default), max ~640px.
- **HUD framing**: corner brackets, a status line ("● LOCAL · offline-ready"),
  a live coordinate/particle readout.
- Connect choices = **hairline technical rows** (not rounded chat bubbles), mono
  labels + a gold `→` on hover. NOT numbered (they're choices, not a sequence).
- Commit to **dark single-theme** (deliberate — a night sky isn't "light mode").

## Rollout
- Build the **first screen** first (welcome / connect), then every onboarding
  screen in this aesthetic, then the workspace. Reference: `TURNSTONE-TEARDOWN.md`
  for flow + content; this brief for the look.
