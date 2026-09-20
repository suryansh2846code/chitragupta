/**
 * The shape vocabulary.
 *
 * Each primitive is a function that returns points on its own surface inside
 * the unit box [-0.5, 0.5]^3. The renderer scales those by the part's
 * width/height/depth, rotates, translates and projects them; nothing here knows
 * about cameras or SVG.
 *
 * Two rules every primitive in this file obeys:
 *
 *   1. **It is convex.** The silhouette shortcut in `hull.js` is only correct
 *      for convex solids. A concave character is built by stacking convex parts
 *      — ears are separate parts, not a notch cut out of a head.
 *   2. **It samples its own extremes.** The hull can only find a corner that
 *      was handed to it, so polyhedra return exactly their vertices and curved
 *      solids sample densely enough that the worst-case turn between adjacent
 *      silhouette points stays well under the corner threshold in `hull.js`.
 *
 * Sample counts are tuned so that the worst-case gap between two adjacent
 * silhouette points stays under a third of a pixel at a 512px export — the
 * point below which a curve stops looking like a curve and starts looking
 * slightly soft. They are deliberately not adaptive: a character has a handful
 * of parts, the clouds are cached, and a fixed count keeps `toSVG()` output
 * byte-identical for the same document, which is what makes the SVG diffable
 * and the tests worth writing.
 */

import { TAU } from "./math.js";

const HALF = 0.5;

function ring(n, radius, y, out, squash = 1) {
  for (let i = 0; i < n; i++) {
    const t = (i / n) * TAU;
    out.push([Math.cos(t) * radius, y, Math.sin(t) * radius * squash]);
  }
}

/** Sphere / ellipsoid. Non-uniform scale later turns one into the other. */
function sphere(lon = 40, lat = 20) {
  const pts = [];
  pts.push([0, HALF, 0], [0, -HALF, 0]);
  for (let j = 1; j < lat; j++) {
    const phi = (j / lat) * Math.PI;
    const y = Math.cos(phi) * HALF;
    const r = Math.sin(phi) * HALF;
    ring(lon, r, y, pts);
  }
  return pts;
}

/** Axis-aligned box. Eight points is the entire silhouette, under any rotation. */
function box() {
  const pts = [];
  for (const x of [-HALF, HALF]) {
    for (const y of [-HALF, HALF]) {
      for (const z of [-HALF, HALF]) pts.push([x, y, z]);
    }
  }
  return pts;
}

/**
 * Box with rounded corners, built the honest way: a sphere of radius `r`
 * swept to each of the eight corners. The flat faces come out flat because the
 * corner patches share their tangent planes, and `simplify()` in `hull.js`
 * collapses the collinear run back down to two points.
 */
function roundedBox(r = 0.18, seg = 8) {
  const rad = Math.min(r, HALF - 1e-3);
  const inner = HALF - rad;
  const pts = [];
  for (const sx of [-1, 1]) {
    for (const sy of [-1, 1]) {
      for (const sz of [-1, 1]) {
        for (let i = 0; i <= seg; i++) {
          const phi = (i / seg) * (Math.PI / 2);
          for (let j = 0; j <= seg; j++) {
            const th = (j / seg) * (Math.PI / 2);
            const x = Math.sin(phi) * Math.cos(th);
            const z = Math.sin(phi) * Math.sin(th);
            const y = Math.cos(phi);
            pts.push([sx * (inner + rad * x), sy * (inner + rad * y), sz * (inner + rad * z)]);
          }
        }
      }
    }
  }
  return pts;
}

/**
 * Pill along Y: a straight barrel capped with two domes.
 *
 * This is the one primitive that cannot be defined in the unit box alone. A
 * capsule's caps are round in *world* proportions, so how much of its height
 * they consume depends on how tall the part was scaled — squeeze a unit-box
 * capsule into a wide part and the caps have to become wide and shallow, not
 * tall and narrow. `aspect` (the part's smaller horizontal extent over its
 * height) is therefore part of the geometry, and part of the cache key.
 */
function capsule(aspect = 1, lon = 36, cap = 9) {
  const a = Math.min(1, Math.max(0.02, aspect));
  const capH = HALF * a;          // vertical reach of one cap
  const straight = HALF - capH;   // half-height of the barrel
  const pts = [[0, HALF, 0], [0, -HALF, 0]];
  for (let i = 1; i <= cap; i++) {
    const phi = (i / (cap + 1)) * (Math.PI / 2);
    const y = straight + Math.cos(phi) * capH;
    const rr = Math.sin(phi) * HALF;
    ring(lon, rr, y, pts);
    ring(lon, rr, -y, pts);
  }
  ring(lon, HALF, straight, pts);
  ring(lon, HALF, -straight, pts);
  if (straight > 1e-4) ring(lon, HALF, 0, pts);
  return pts;
}

/** Cylinder along Y. */
function cylinder(lon = 36) {
  const pts = [];
  ring(lon, HALF, HALF, pts);
  ring(lon, HALF, -HALF, pts);
  // A couple of intermediate rings so a tilted cylinder's silhouette picks up
  // the curve of the barrel rather than cutting straight between the rims.
  ring(lon, HALF, HALF / 3, pts);
  ring(lon, HALF, -HALF / 3, pts);
  return pts;
}

/** Cone: apex up, circular base. */
function cone(lon = 44) {
  const pts = [[0, HALF, 0]];
  ring(lon, HALF, -HALF, pts);
  ring(lon, HALF / 2, 0, pts);
  return pts;
}

/** Half an ellipsoid, sitting on its flat face and filling the unit box. */
function dome(lon = 36, lat = 14) {
  const pts = [[0, HALF, 0]];
  for (let j = 1; j <= lat; j++) {
    const phi = (j / lat) * (Math.PI / 2);
    ring(lon, Math.sin(phi) * HALF, HALF - (1 - Math.cos(phi)), pts);
  }
  return pts;
}

/**
 * Teardrop: a ball below, drawn up to a point above.
 *
 * Built as a sphere and the cone that is *tangent* to it, rather than as a
 * trigonometric profile. A profile function like `sin(phi)(1-cos(phi))` looks
 * like a teardrop plotted on paper and renders as a lemon: it goes to zero
 * radius at both ends, so the shape comes to a point at the bottom too, and a
 * ghost ends up standing on a spike.
 *
 * Tangency is what makes the join invisible. The cone meets the sphere where
 * their surfaces already share a tangent plane, so there is no crease to see
 * and the convex hull runs straight through it.
 */
function teardrop(lon = 36, lat = 16, cone = 10) {
  const rb = 0.34;                 // ball radius, before the fill-the-box rescale
  const yc = -HALF + rb;           // ball centre, resting on the bottom of the box
  const d = HALF - yc;             // apex to ball centre
  const yTangent = yc + (rb * rb) / d;
  const rTangent = (rb * Math.sqrt(d * d - rb * rb)) / d;
  // Widen everything so the fattest ring touches the unit box, the way every
  // other primitive here does — otherwise this one shape would render smaller
  // than its stated width and depth.
  const k = HALF / rb;

  const pts = [[0, HALF, 0], [0, -HALF, 0]];
  for (let j = 1; j <= lat; j++) {
    const y = yc - rb + ((yTangent - (yc - rb)) * j) / lat;
    const dy = (y - yc) / rb;
    const r = rb * Math.sqrt(Math.max(0, 1 - dy * dy));
    ring(lon, r * k, y, pts);
  }
  for (let j = 1; j < cone; j++) {
    const t = j / cone;
    ring(lon, rTangent * (1 - t) * k, yTangent + (HALF - yTangent) * t, pts);
  }
  return pts;
}

/** Octahedron — the "diamond" in the shape row. */
function diamond() {
  return [
    [0, HALF, 0], [0, -HALF, 0],
    [HALF, 0, 0], [-HALF, 0, 0],
    [0, 0, HALF], [0, 0, -HALF],
  ];
}

/** Square-based pyramid. */
function pyramid() {
  const pts = [[0, HALF, 0]];
  for (const x of [-HALF, HALF]) for (const z of [-HALF, HALF]) pts.push([x, -HALF, z]);
  return pts;
}

/** Truncated pyramid — the trapezoid in the shape row. `top` is the top scale. */
function frustum(top = 0.55) {
  const pts = [];
  for (const x of [-HALF, HALF]) for (const z of [-HALF, HALF]) {
    pts.push([x, -HALF, z]);
    pts.push([x * top, HALF, z * top]);
  }
  return pts;
}

/** Right triangular prism — the wedge. */
function wedge() {
  const pts = [];
  for (const z of [-HALF, HALF]) {
    pts.push([-HALF, -HALF, z], [HALF, -HALF, z], [-HALF, HALF, z]);
  }
  return pts;
}

/**
 * The registry. `SHAPES` is the contract between the document schema, the
 * editor's shape picker and this file — adding a shape means adding it here and
 * nowhere else, and an unknown shape falls back to a sphere rather than
 * throwing, because a document from a newer version of this package must still
 * render something rather than blanking the avatar.
 */
const BUILDERS = {
  sphere: () => sphere(),
  ellipsoid: () => sphere(),
  box: () => box(),
  "rounded-box": (p) => roundedBox(p && p.round != null ? p.round * 0.5 : 0.18),
  capsule: (p) => capsule(p && p.aspect != null ? p.aspect : 1),
  cylinder: () => cylinder(),
  cone: () => cone(),
  dome: () => dome(),
  teardrop: () => teardrop(),
  diamond: () => diamond(),
  pyramid: () => pyramid(),
  frustum: (p) => frustum(p && p.taper != null ? p.taper : 0.55),
  wedge: () => wedge(),
};

export const SHAPES = Object.keys(BUILDERS);

/**
 * Human-facing labels, kept beside the builders so the editor never has to
 * hold a second list that can drift out of sync with this one.
 */
export const SHAPE_LABELS = {
  sphere: "Sphere",
  ellipsoid: "Ellipsoid",
  box: "Cube",
  "rounded-box": "Rounded cube",
  capsule: "Capsule",
  cylinder: "Cylinder",
  cone: "Cone",
  dome: "Dome",
  teardrop: "Teardrop",
  diamond: "Diamond",
  pyramid: "Pyramid",
  frustum: "Trapezoid",
  wedge: "Wedge",
};

const _cache = new Map();

const r2 = (v) => (v == null ? "" : Math.round(v * 100) / 100);

/**
 * Surface points for a shape, memoised.
 *
 * The sample cloud depends only on the shape name and its two shape-specific
 * knobs — never on scale, rotation or position, all of which are applied by the
 * renderer afterwards. That is what makes caching safe, and it is why dragging
 * a size slider does not rebuild any geometry.
 */
export function shapePoints(shape, params) {
  const build = BUILDERS[shape] || BUILDERS.sphere;
  const key = `${shape}|${r2(params && params.round)}|${r2(params && params.taper)}|${r2(params && params.aspect)}`;
  let pts = _cache.get(key);
  if (!pts) {
    pts = build(params);
    _cache.set(key, pts);
  }
  return pts;
}
