/**
 * Projected point cloud -> one smooth SVG outline.
 *
 * Every primitive this package draws is convex, and that is not an accident —
 * it is the whole reason the renderer can be this small. The silhouette of a
 * convex solid under any projection is exactly the convex hull of its projected
 * surface points, so there is no mesh to cut, no hidden-line removal, and no
 * boolean geometry. Concave characters are built by *stacking* convex parts,
 * which is also how the reference tool does it: the faint seam across a cat's
 * face is one part's outline crossing another's fill.
 *
 * The second half of this file is the part that makes it not look like a
 * polygon. A hull of a sphere is a 40-gon; drawn as line segments at 512px you
 * can count the sides. Catmull-Rom through the hull vertices fixes that — but
 * blindly smoothing also rounds the corners off a cube. So corners are detected
 * by turn angle and kept sharp, and only the gentle stretches are splined.
 */

/**
 * Monotone-chain convex hull. Returns vertices counter-clockwise in math
 * orientation (clockwise on screen, where Y grows downward).
 */
export function convexHull(points) {
  if (points.length < 4) return points.slice();
  const pts = points.slice().sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));

  const cross = (o, a, b) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

/**
 * Drop hull vertices that sit almost exactly on the segment between their
 * neighbours.
 *
 * Dense sampling puts dozens of nearly-collinear points along a flat face of a
 * box. They cost path length and, worse, they make the corner test below
 * compare two microscopic segments where floating-point noise dominates the
 * angle. `tol` is a distance in design units, of which there are 256 across.
 *
 * Kept well under half a unit on purpose. At 0.35 a curve drawn at 420px lost
 * up to half a pixel of shape per vertex, which is invisible one vertex at a
 * time and reads as a softness across a whole silhouette.
 */
export function simplify(ring, tol = 0.15) {
  if (ring.length < 4) return ring;
  const out = [];
  const n = ring.length;
  for (let i = 0; i < n; i++) {
    const prev = out.length ? out[out.length - 1] : ring[(i - 1 + n) % n];
    const cur = ring[i];
    const next = ring[(i + 1) % n];
    const dx = next[0] - prev[0], dy = next[1] - prev[1];
    const len = Math.hypot(dx, dy);
    if (len < 1e-6) continue;
    // perpendicular distance from `cur` to the prev->next line
    const d = Math.abs((cur[0] - prev[0]) * dy - (cur[1] - prev[1]) * dx) / len;
    if (d >= tol) out.push(cur);
  }
  return out.length >= 3 ? out : ring;
}

/**
 * The exponent that makes the spline **centripetal** rather than uniform.
 *
 * This single constant is the difference between a clean silhouette and a
 * lumpy one, and it took a visibly wobbly robot to find.
 *
 * A convex hull hands back points that are *not* evenly spaced: along the
 * rounded edge of a box the samples come from a UV grid on a corner sphere, so
 * they bunch in places and stretch in others. Uniform Catmull-Rom weights every
 * gap the same, so wherever the spacing changes the tangent is wrong for one
 * side of the join and the curve bulges past its own points. Around a shape
 * that is *nearly* straight, those bulges read as hand-drawn wobble.
 *
 * Centripetal parameterisation (alpha = 0.5) scales each tangent by the actual
 * distance between points. It is the standard fix, and it is provably free of
 * cusps and self-intersections — which uniform Catmull-Rom is not.
 */
const ALPHA = 0.5;

/** Knot spacing between two points, floored so coincident points cannot divide by zero. */
function knot(a, b) {
  const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
  return Math.max(1e-4, Math.pow(d, ALPHA));
}

/**
 * Closed ring -> SVG path data, splining the smooth stretches and keeping the
 * sharp corners sharp.
 *
 * `cornerAngle` is the exterior turn, in radians, above which a vertex counts
 * as a corner. The default sits between a dense sphere's steepest turn (about
 * 0.16 rad at 40 samples around) and a cube corner (pi/2), with a wide margin
 * on both sides; tighten it and spheres grow facets, loosen it and cubes melt.
 */
export function ringToPath(ring, { cornerAngle = 0.5, tension = 1, closed = true } = {}) {
  const n = ring.length;
  if (n === 0) return "";
  if (n === 1) return `M ${fmt(ring[0][0])} ${fmt(ring[0][1])} Z`;
  if (n === 2) return `M ${fmt(ring[0][0])} ${fmt(ring[0][1])} L ${fmt(ring[1][0])} ${fmt(ring[1][1])} Z`;

  const at = (i) => ring[((i % n) + n) % n];
  const idx = (i) => ((i % n) + n) % n;

  // A vertex is a corner when the direction of travel changes abruptly there.
  const sharp = new Array(n);
  for (let i = 0; i < n; i++) {
    const p = at(i - 1), c = at(i), q = at(i + 1);
    const a1 = Math.atan2(c[1] - p[1], c[0] - p[0]);
    const a2 = Math.atan2(q[1] - c[1], q[0] - c[0]);
    let d = Math.abs(a2 - a1);
    if (d > Math.PI) d = Math.PI * 2 - d;
    sharp[i] = d > cornerAngle;
  }

  let out = `M ${fmt(ring[0][0])} ${fmt(ring[0][1])}`;
  const segs = closed ? n : n - 1;

  for (let i = 0; i < segs; i++) {
    const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
    const startSharp = sharp[idx(i)], endSharp = sharp[idx(i + 1)];

    // Both ends are corners: this is a flat face of a polyhedron. A straight
    // line is exact, and a curve here could only be wrong.
    if (startSharp && endSharp) {
      out += ` L ${fmt(p2[0])} ${fmt(p2[1])}`;
      continue;
    }

    const d1 = knot(p0, p1), d2 = knot(p1, p2), d3 = knot(p2, p3);
    const c1 = [0, 0], c2 = [0, 0];
    for (let k = 0; k < 2; k++) {
      // Non-uniform Catmull-Rom tangents. At a corner the tangent is taken
      // one-sided, along this segment's own chord, so the curve leaves and
      // enters the corner cleanly instead of being dragged round it by the
      // neighbour on the far side.
      const m1 = startSharp
        ? (p2[k] - p1[k]) / d2
        : (p1[k] - p0[k]) / d1 - (p2[k] - p0[k]) / (d1 + d2) + (p2[k] - p1[k]) / d2;
      const m2 = endSharp
        ? (p2[k] - p1[k]) / d2
        : (p2[k] - p1[k]) / d2 - (p3[k] - p1[k]) / (d2 + d3) + (p3[k] - p2[k]) / d3;
      c1[k] = p1[k] + (m1 * d2 * tension) / 3;
      c2[k] = p2[k] - (m2 * d2 * tension) / 3;
    }
    out += ` C ${fmt(c1[0])} ${fmt(c1[1])}, ${fmt(c2[0])} ${fmt(c2[1])}, ${fmt(p2[0])} ${fmt(p2[1])}`;
  }
  return closed ? out + " Z" : out;
}

/** Two decimals. Beyond that is bytes nobody can see. */
export function fmt(n) {
  if (!Number.isFinite(n)) return "0";
  const r = Math.round(n * 100) / 100;
  return String(Object.is(r, -0) ? 0 : r);
}

/** Axis-aligned bounds of a 2D point list, as {minX,minY,maxX,maxY}. */
export function bounds2(points) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of points) {
    if (p[0] < minX) minX = p[0];
    if (p[0] > maxX) maxX = p[0];
    if (p[1] < minY) minY = p[1];
    if (p[1] > maxY) maxY = p[1];
  }
  if (minX === Infinity) return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  return { minX, minY, maxX, maxY };
}
