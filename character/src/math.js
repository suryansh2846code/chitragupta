/**
 * Just enough linear algebra to put a 3D primitive on a 2D screen.
 *
 * Everything here is plain arrays and plain numbers. No classes, no allocation
 * discipline beyond "don't allocate in the hot loop if it is easy not to" —
 * a character is a few hundred points, not a scene graph, and the renderer runs
 * at pointer-move rate rather than per frame.
 *
 * Convention, fixed once so nothing downstream has to guess:
 *   +X right, +Y up, +Z toward the viewer. Right-handed.
 *   Angles are radians everywhere inside the renderer; the scene document
 *   stores degrees because a person reads degrees. `deg()` is the only border.
 */

export const TAU = Math.PI * 2;

export const deg = (d) => (d * Math.PI) / 180;
export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
export const lerp = (a, b, t) => a + (b - a) * t;

/** Column-major 3x3, stored [m00,m01,m02, m10,m11,m12, m20,m21,m22] row-major. */
export function matIdentity() {
  return [1, 0, 0, 0, 1, 0, 0, 0, 1];
}

export function matMul(a, b) {
  const o = new Array(9);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      o[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
    }
  }
  return o;
}

export function matRotX(t) {
  const s = Math.sin(t), c = Math.cos(t);
  return [1, 0, 0, 0, c, -s, 0, s, c];
}

export function matRotY(t) {
  const s = Math.sin(t), c = Math.cos(t);
  return [c, 0, s, 0, 1, 0, -s, 0, c];
}

export function matRotZ(t) {
  const s = Math.sin(t), c = Math.cos(t);
  return [c, -s, 0, s, c, 0, 0, 0, 1];
}

/**
 * Euler angles in radians, applied Z then X then Y.
 *
 * The order matters and this one was chosen on purpose: yaw last means the
 * "turn to look at the cursor" rotation composes on top of whatever pose the
 * part was authored in, instead of tumbling it. Change the order and every
 * saved character with a non-zero part rotation moves.
 */
export function matEuler(rx, ry, rz) {
  return matMul(matRotY(ry), matMul(matRotX(rx), matRotZ(rz)));
}

export function matApply(m, p) {
  const [x, y, z] = p;
  return [
    m[0] * x + m[1] * y + m[2] * z,
    m[3] * x + m[4] * y + m[5] * z,
    m[6] * x + m[7] * y + m[8] * z,
  ];
}

/** Rotate a whole point cloud in place-ish (returns a new array). */
export function matApplyAll(m, pts) {
  const out = new Array(pts.length);
  for (let i = 0; i < pts.length; i++) out[i] = matApply(m, pts[i]);
  return out;
}

export function vAdd(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
export function vSub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
export function vScale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
export function vDot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

export function vNorm(a) {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}

/**
 * A tiny deterministic PRNG (mulberry32) plus a string hash to seed it.
 *
 * Deterministic because an agent's generated character must be the same on
 * every launch and on every machine. `Math.random()` here would mean an agent
 * that looks different after a refresh, which reads as a bug about identity,
 * not as variety.
 */
export function hashString(s) {
  let h = 2166136261 >>> 0;
  const str = String(s == null ? "" : s);
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

export function rng(seed) {
  let a = (typeof seed === "number" ? seed : hashString(seed)) >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Pick from a list with a generator from `rng`. */
export function pick(next, list) {
  return list[Math.floor(next() * list.length) % list.length];
}

/** Integer in [lo, hi]. */
export function pickInt(next, lo, hi) {
  return lo + Math.floor(next() * (hi - lo + 1));
}
