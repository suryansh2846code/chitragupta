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
Near-black cool ground · cool-white drifting particles that wire into a
constellation · **monospace-forward** technical type · hairline **HUD** overlays
(coordinates, brackets, status) · one warm **gold "true-north" accent**.

## Tokens
- `--ground: #060810` (cool near-black) · `--ground-2: #0b0e1a` (panel)
- `--star: #dfe7f2` (cool white — particles + text) · `--muted: #7a8397`
- `--line: rgba(223,231,242,.10)` (hairlines)
- `--north: #f5c877` (warm pole-star gold — the ONE accent: live/hover/primary)
- `--north-dim: rgba(245,200,119,.14)`

## Mark
A **yantra** — the bhupura square with its four gates, a lotus ring, the inner
rotated square, and the **bindu** at the centre. The bindu is the hook: the one
point being written down, which is why it is the only thing in the mark that
ever takes `--north`.

One master, `docs/brand/yantra.png` — 1024px, white, the shape carried entirely
in the **alpha channel**. Nothing downstream is hand-cut:

- `scripts/make-brand-assets.py` writes `brand-mark.png` (the rail and the
  onboarding paint it with CSS `mask`, so it takes the colour of the surface it
  sits on) and `favicon-32/64.png` (gold baked in — the tab is the one place CSS
  cannot reach, and gold is the only hue that holds on a light *and* a dark tab).
- `packaging/make-icon.py` lays it on the night sky for `icon.icns`, gold bindu
  and all.

It does not shrink forever. Below ~20px the lotus and the gates merge and it
stops being this mark; the rail runs it at 22px for that reason, and the app
icon falls back to the core — the rotated square and its bindu — at 32px and
below.

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
