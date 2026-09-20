/**
 * Document -> render model. The whole 3D pipeline lives here.
 *
 * Per part: unit-box surface samples, scaled, rotated into world, rotated by
 * the view, projected through a pinhole camera, hulled, simplified, splined.
 * Per scene: parts sorted back to front, a face plane pinned to whichever part
 * carries it, and colours resolved from the palette and graded.
 *
 * What comes out is a "render model" — plain data describing paths, fills and
 * transforms, with no SVG in it. `svg.js` turns that into markup and
 * `renderer.js` turns it into live DOM, and because both read the same model
 * the exported file and the thing on screen cannot drift apart. That is the
 * only reason "Download SVG" can be trusted to match the preview.
 *
 * ── the coordinate systems, in order ────────────────────────────────────────
 *   local   unit box [-0.5, 0.5]^3, what `primitives.js` returns
 *   part    local * (width, height, depth) / 100 * UNIT, rotated, translated
 *   world   the character, roughly inside a ±150 box around the origin
 *   view    world rotated by yaw / pitch / roll
 *   screen  projected, scaled, centred in a fixed 256-unit square
 *
 * The output viewBox is always 256 units regardless of the requested pixel
 * size. Export size is a `width`/`height` attribute, never a re-render, so a
 * 1024px PNG and a 28px rail avatar are the same geometry and the same file.
 */

import { deg, matEuler, matApply, clamp, vNorm, vDot } from "./math.js";
import { shapePoints } from "./primitives.js";
import { convexHull, simplify, ringToPath, bounds2, fmt } from "./hull.js";
import { palette, grade, mix, readableInk } from "./palettes.js";
import { UNIT } from "./schema.js";

/** The fixed design square everything is drawn into. */
export const VIEWBOX = 256;

/**
 * World units per design pixel, at view scale 1.
 *
 * Picked so the default body — one unit cube at the default view scale of 1.28
 * — lands a little inside the 256 square, leaving room for the outline stroke
 * and the drop shadow to sit in frame rather than being clipped by it.
 */
const PIXELS_PER_UNIT = 1.7;

/**
 * Camera focal length in world units.
 *
 * Long enough that the perspective reads as a gentle turn of a solid object,
 * short enough that a part pushed forward genuinely grows. Shorter than about
 * 350 and a rotated head starts to look like a fish-eye photograph of itself.
 *
 * It is also the number the auto-fit pays for. The fit has to reserve room for
 * the worst case — the outermost point of the character swung all the way
 * toward the camera — and that reservation is `FOCAL / (FOCAL - radius)`. At
 * 620 a rabbit's ear tip could grow by 22% on its way round, so either the fit
 * gave up a fifth of the frame permanently, or the ear clipped at some angles.
 * A longer lens makes the reservation cheap (around 10%) while keeping the turn
 * clearly three-dimensional.
 */
const FOCAL = 1150;

/**
 * Design units per face unit.
 *
 * The face is authored in its own space so that eye size and spacing stay
 * stable when the head is resized — a head twice as wide should not get eyes
 * twice as far apart unless someone asks for that. The constant is calibrated
 * so the default 28 x 64 eyes at gap 41 sit on a default body exactly as the
 * reference builder draws them.
 */
const FACE_SCALE = 0.3;

/** Perspective divide. `z` is view-space depth, positive toward the viewer. */
function persp(z) {
  const d = FOCAL - z;
  return d > 1 ? FOCAL / d : FOCAL;
}

/**
 * Build the view matrix.
 *
 * `pose` is how the follow-cursor loop and any other live animation speak to
 * the renderer: it adds to the document's authored angles rather than replacing
 * them, so a character posed at a three-quarter angle still turns from *there*
 * toward the pointer. Replacing would make every character snap to front-on the
 * moment the pointer moved, which is the bug that makes this feature feel
 * broken rather than alive.
 */
export function viewMatrix(view, pose) {
  const p = pose || {};
  return matEuler(
    (view.pitch || 0) + (p.pitch || 0),
    (view.yaw || 0) + (p.yaw || 0),
    (view.roll || 0) + (p.roll || 0),
  );
}

function projector(view, pose, unitScale) {
  const p = pose || {};
  const scale = (view.scale || 1) * (p.scale || 1) * unitScale;
  const cx = VIEWBOX / 2 + (view.positionX || 0) * PIXELS_PER_UNIT * 0.5;
  const cy = VIEWBOX / 2 + (view.positionY || 0) * PIXELS_PER_UNIT * 0.5;
  return function project(v) {
    const f = persp(v[2]);
    // Screen Y grows downward; world Y grows upward. One negation, here, so
    // nothing else in the package has to remember which way is up.
    return [cx + v[0] * f * scale, cy - v[1] * f * scale];
  };
}

/**
 * World-space points for one part, memoised.
 *
 * This is the single most valuable cache in the package. A part's world points
 * depend only on its own fields — never on the camera, the view angle or the
 * pose — so while the follow loop turns a character sixty times a second, this
 * runs zero times. What is left in the hot path is one matrix multiply per
 * point plus the hull, which is what makes a rail of live characters affordable.
 *
 * Keyed on the part's JSON, because a part *is* its JSON: two parts that
 * serialise the same are the same geometry by definition.
 */
const _world = new Map();
const WORLD_CACHE_MAX = 256;

function worldPoints(part) {
  const key = JSON.stringify(part);
  const hit = _world.get(key);
  if (hit) return hit;
  const pts = partPoints(part);
  // A plain FIFO trim. An editor session touches a few hundred distinct parts
  // at most; an unbounded map here would hold every intermediate state of every
  // slider drag for the life of the page.
  if (_world.size >= WORLD_CACHE_MAX) _world.delete(_world.keys().next().value);
  _world.set(key, pts);
  return pts;
}

/**
 * The radius of the sphere around the origin that contains the whole character.
 *
 * Deliberately a sphere and not a box. The auto-fit below has to hold still
 * while the character turns — a fit computed from the projected bounding box
 * would rescale on every frame of a rotation, so the character would breathe in
 * and out as it looked around. A bounding sphere is rotation-invariant, so the
 * fit is computed once per document and then never moves.
 */
function boundingRadius(parts) {
  let r2 = 0;
  for (const part of parts) {
    for (const p of worldPoints(part)) {
      const d = p[0] * p[0] + p[1] * p[1] + p[2] * p[2];
      if (d > r2) r2 = d;
    }
  }
  return Math.sqrt(r2) || 1;
}

/** World-space transform for one part: scale, rotate, translate. */
function partPoints(part) {
  const aspect = Math.min(part.width, part.depth) / Math.max(1e-3, part.height);
  const local = shapePoints(part.shape, {
    round: part.round / 100,
    taper: part.taper / 100,
    aspect,
  });
  const sx = (part.width / 100) * UNIT;
  const sy = (part.height / 100) * UNIT;
  const sz = (part.depth / 100) * UNIT;
  const rot = matEuler(deg(part.rotationX), deg(part.rotationY), deg(part.rotationZ));
  const tx = part.positionX, ty = part.positionY, tz = part.positionZ;
  const out = new Array(local.length);
  for (let i = 0; i < local.length; i++) {
    const p = local[i];
    const r = matApply(rot, [p[0] * sx, p[1] * sy, p[2] * sz]);
    out[i] = [r[0] + tx, r[1] + ty, r[2] + tz];
  }
  return out;
}

/** Resolve a part's fill from the palette, its override and its shade offset. */
function partFill(part, pal, cg) {
  let base = part.color || pal.body;
  if (part.shade) {
    const t = Math.abs(part.shade) / 100;
    base = part.shade > 0 ? mix(base, pal.shade, t) : mix(base, "#ffffff", t * 0.6);
  }
  return grade(base, cg);
}

/**
 * A cheap directional shade, applied as a flat darkening rather than a gradient.
 *
 * Real per-face shading would need the mesh this renderer deliberately does not
 * keep. What it can know is which way a part's centre faces relative to the
 * light, and darkening the whole part by that amount is enough to separate an
 * ear from the head behind it — which is the only job lighting has here.
 */
function lightFactor(part, lighting, vm) {
  if (!lighting.enabled) return 0;
  const az = deg(lighting.azimuth), el = deg(lighting.elevation);
  const L = vNorm([Math.cos(el) * Math.sin(az), Math.sin(el), Math.cos(el) * Math.cos(az)]);
  const c = matApply(vm, [part.positionX, part.positionY, part.positionZ]);
  const n = vNorm([c[0], c[1], c[2] + 1e-3]);
  const d = clamp(vDot(n, L), -1, 1);
  return (-d * 0.5 + 0.5) * (lighting.strength / 100) * 0.55;
}

/**
 * The face plane, pinned to the part that hosts it.
 *
 * The face is flat art placed on a 3D surface, so it needs a 2D frame that
 * moves with that surface. Three points do it: the plane's origin and one unit
 * along each of its axes, projected. The resulting affine matrix is what the
 * eyes and mouth are drawn inside, which is why they squash and slide correctly
 * as the head turns without any of them knowing the head exists.
 *
 * Perspective is not affine, so this is an approximation — but the face occupies
 * a small, near-central patch of a long-focal-length projection, where the
 * error is far below a pixel. Wrapping each feature individually was tried and
 * was both slower and worse: features drifted apart under rotation instead of
 * holding together as one face.
 */
function faceFrame(host, face, project, vm, pose) {
  const rot = matEuler(deg(host.rotationX), deg(host.rotationY), deg(host.rotationZ));
  const halfDepth = (host.depth / 100) * UNIT * 0.5;

  // Where on the host the face sits, in the host's own space, pushed just
  // proud of the surface so it can never be swallowed by the body's own fill.
  const ox = face.offsetX * FACE_SCALE;
  const oy = -face.offsetY * FACE_SCALE;
  const originLocal = [ox, oy, halfDepth * 0.995];

  const u = FACE_SCALE;   // one face unit, in host-local world units
  const fr = deg(face.rotation);
  const cos = Math.cos(fr), sin = Math.sin(fr);
  // Face space is written the way a person reads a screen: +X right, +Y *down*.
  // So V points down in world space. Every face field in the document — mouthY,
  // noseY, the highlight offset — is a downward number, and this one negation
  // is the only place that is reconciled.
  const uLocal = [u * cos, -u * sin, 0];
  const vLocal = [u * sin, -u * cos, 0];

  const toWorld = (p) => {
    const r = matApply(rot, p);
    return [r[0] + host.positionX, r[1] + host.positionY, r[2] + host.positionZ];
  };
  const toView = (p) => matApply(vm, toWorld(p));

  const O = toView(originLocal);
  const U = toView([originLocal[0] + uLocal[0], originLocal[1] + uLocal[1], originLocal[2] + uLocal[2]]);
  const V = toView([originLocal[0] + vLocal[0], originLocal[1] + vLocal[1], originLocal[2] + vLocal[2]]);

  const pO = project(O), pU = project(U), pV = project(V);

  // The surface normal in view space decides whether the face is pointing at
  // the viewer at all. A face on the back of a head must not be drawn through
  // it — and it must fade rather than pop, or a slow turn ends in a flicker.
  const n = matApply(vm, matApply(rot, [0, 0, 1]));
  const facing = vNorm(n)[2];
  const visible = facing > 0.02;
  const fade = clamp((facing - 0.02) / 0.35, 0, 1);

  return {
    visible,
    opacity: fade,
    // SVG matrix(a,b,c,d,e,f): a,b is the image of (1,0); c,d of (0,1).
    matrix: [
      pU[0] - pO[0], pU[1] - pO[1],
      pV[0] - pO[0], pV[1] - pO[1],
      pO[0] + (pose && pose.faceX ? pose.faceX : 0),
      pO[1] + (pose && pose.faceY ? pose.faceY : 0),
    ],
    depth: O[2],
  };
}

/**
 * Face geometry is written in face units and rounded on the way out.
 *
 * `f()` here is `fmt` from the hull, applied to every number that reaches a
 * path string. Without it a 31-unit eye at a fractional roundness emits
 * coordinates like `-0.40500000000000114` — seventeen significant digits of
 * float noise, four times per eye, in a file whose whole point is being small
 * and readable. It also makes the output stable enough to assert on.
 */
const f = fmt;

/** Rounded-rectangle path centred on the origin, in face units. */
function rrect(w, h, r) {
  const hw = w / 2, hh = h / 2;
  const rr = Math.min(r, hw, hh);
  if (rr <= 0.01) return `M ${f(-hw)} ${f(-hh)} H ${f(hw)} V ${f(hh)} H ${f(-hw)} Z`;
  return [
    `M ${f(-hw + rr)} ${f(-hh)}`,
    `H ${f(hw - rr)}`,
    `A ${f(rr)} ${f(rr)} 0 0 1 ${f(hw)} ${f(-hh + rr)}`,
    `V ${f(hh - rr)}`,
    `A ${f(rr)} ${f(rr)} 0 0 1 ${f(hw - rr)} ${f(hh)}`,
    `H ${f(-hw + rr)}`,
    `A ${f(rr)} ${f(rr)} 0 0 1 ${f(-hw)} ${f(hh - rr)}`,
    `V ${f(-hh + rr)}`,
    `A ${f(rr)} ${f(rr)} 0 0 1 ${f(-hw + rr)} ${f(-hh)}`,
    "Z",
  ].join(" ");
}

/** An ellipse as two arcs, so it composes with the other path builders. */
function ellipsePath(w, h) {
  return `M ${f(-w / 2)} 0 a ${f(w / 2)} ${f(h / 2)} 0 1 0 ${f(w)} 0`
    + ` a ${f(w / 2)} ${f(h / 2)} 0 1 0 ${f(-w)} 0 Z`;
}

function eyePath(face, w, h) {
  const r = (Math.min(w, h) / 2) * (face.eyeRoundness / 100);
  switch (face.eyeShape) {
    case "ellipse":
      return ellipsePath(w, h);
    case "leaf":
      return `M 0 ${f(-h / 2)} Q ${f(w / 2)} 0 0 ${f(h / 2)} Q ${f(-w / 2)} 0 0 ${f(-h / 2)} Z`;
    case "line":
      return rrect(w, Math.max(2, h * 0.18), Math.max(1, h * 0.09));
    default:
      return rrect(w, h, r);
  }
}

function mouthPath(face) {
  const w = face.mouthWidth, h = face.mouthHeight, c = face.mouthCurve;
  switch (face.mouthShape) {
    case "line":
      return rrect(w, Math.max(1.5, h * 0.25), Math.max(0.75, h * 0.12));
    case "oval":
      return ellipsePath(w, h);
    case "cat": {
      // Two mirrored arcs — the w-shaped muzzle. Stroked, never filled.
      const q = w / 2;
      return `M ${f(-q)} ${f(-h / 2)} Q ${f(-q / 2)} ${f(h / 2)} 0 ${f(-h / 2)}`
        + ` Q ${f(q / 2)} ${f(h / 2)} ${f(q)} ${f(-h / 2)}`;
    }
    default: {
      // A curve given thickness by sweeping it: the outward arc, then the
      // inward one back. One closed path, so it fills cleanly at any curve —
      // including zero, where it degenerates to a bar rather than to nothing.
      const q = w / 2;
      const k = (c / 100) * h * 2.6;
      const t = Math.max(1.2, h * 0.42);
      return `M ${f(-q)} 0 Q 0 ${f(k)} ${f(q)} 0 Q 0 ${f(k - t)} ${f(-q)} 0 Z`;
    }
  }
}

function nosePath(face) {
  const w = face.noseWidth, h = face.noseHeight;
  switch (face.noseShape) {
    case "oval":
      return ellipsePath(w, h);
    case "line":
      return rrect(Math.max(1.5, w * 0.35), h, Math.max(0.75, w * 0.17));
    case "heart":
      return `M 0 ${f(h / 2)} C ${f(-w)} ${f(-h * 0.1)} ${f(-w * 0.45)} ${f(-h * 0.75)} 0 ${f(-h * 0.2)}`
        + ` C ${f(w * 0.45)} ${f(-h * 0.75)} ${f(w)} ${f(-h * 0.1)} 0 ${f(h / 2)} Z`;
    default:
      return `M ${f(-w / 2)} ${f(-h / 2)} L ${f(w / 2)} ${f(-h / 2)} L 0 ${f(h / 2)} Z`;
  }
}

/**
 * Build everything `svg.js` and `renderer.js` need.
 *
 * Pure: the same document and the same pose produce byte-identical output, on
 * any machine, with no clock and no randomness anywhere in the path. That is
 * what lets a character be cached by document hash, and what lets a test assert
 * on a path string instead of on a screenshot.
 */
export function buildRenderModel(doc, options) {
  const opts = options || {};
  const pose = opts.pose || {};
  const s = doc.scene;
  const pal = palette(s.appearance.paletteId);
  const cg = s.effects.colorGrade;
  const vm = viewMatrix(s.view, pose);

  // Auto-fit. `view.scale` is a zoom *relative to a character that fits the
  // frame*, which is the only definition under which "1" means the same thing
  // to a compact blob and to a rabbit with ears twice its own height. Turning
  // fit off restores the absolute scale, and that is what an imported document
  // from the reference builder gets, so it keeps the crop it was composed with.
  const inset = VIEWBOX / 2 - (s.camera.padding == null ? 10 : s.camera.padding);
  let unitScale = PIXELS_PER_UNIT;
  if (s.camera.fit !== "none") {
    // The worst case is the outermost point rotated all the way toward the
    // camera, where it sits at z = radius and is magnified by `persp(radius)`.
    // Reserving for that is what makes "it never leaves the frame" a guarantee
    // rather than a statement about the angles someone happened to try.
    const radius = boundingRadius(s.entity.parts);
    unitScale = inset / (radius * persp(radius));
  }

  const project = projector(s.view, pose, unitScale);

  const outlineColor = grade(s.effects.outline.color || pal.outline, cg);
  const bodyColor = grade(pal.body, cg);
  const background = s.appearance.background || pal.background;

  const parts = [];
  let allPoints = [];

  for (const part of s.entity.parts) {
    const world = worldPoints(part);
    const flat = new Array(world.length);
    let depth = 0;
    for (let i = 0; i < world.length; i++) {
      const v = matApply(vm, world[i]);
      depth += v[2];
      flat[i] = project(v);
    }
    depth /= world.length || 1;

    const ring = simplify(convexHull(flat));
    if (ring.length < 3) continue;
    allPoints = allPoints.concat(ring);

    let fill = partFill(part, pal, cg);
    const lf = lightFactor(part, s.lighting, vm);
    if (lf > 0) fill = mix(fill, "#000000", lf);

    parts.push({
      id: part.id,
      d: ringToPath(ring),
      fill,
      depth,
      outline: part.outline !== false,
    });
  }

  // Painter's algorithm. Stable within equal depths so a document with two
  // coincident parts draws them in the order it lists them, every time.
  parts.sort((a, b) => a.depth - b.depth);

  const hostPart = s.entity.parts.find((p) => p.faceHost) || s.entity.parts[0];
  const frame = hostPart && s.face.enabled !== false
    ? faceFrame(hostPart, s.face, project, vm, pose)
    : null;

  const faceInk = grade(s.face.color || pal.face, cg);
  // Whatever the palette says, a face has to be visible on the body it is
  // drawn on. Below a 2:1 contrast ratio the eyes stop reading at rail size,
  // so the ink is swapped for one that does.
  const hostFill = parts.length ? parts[parts.length - 1].fill : bodyColor;
  const ink = contrastOk(faceInk, hostFill) ? faceInk : readableInk(hostFill);

  const f = s.face;
  const blink = clamp(pose.blink == null ? 0 : pose.blink, 0, 1);
  const eyeH = f.height * (1 - blink * 0.94);
  const eyeCx = (f.gap + f.width) / 2;
  const eyeDx = (pose.eyeX || 0) * f.width * 0.22;
  const eyeDy = (pose.eyeY || 0) * f.height * 0.12;

  const eyes = [-1, 1].map((sign) => ({
    d: eyePath(f, f.width, Math.max(1.2, eyeH)),
    x: sign * eyeCx + eyeDx,
    y: eyeDy,
    rotation: sign < 0 ? f.leftEyeRotation : f.rightEyeRotation,
  }));

  // A highlight fades with the lid rather than vanishing at some threshold.
  // Partly because it looks right, and partly because the live renderer patches
  // attributes in place: a feature that appears and disappears changes the
  // shape of the DOM, which forces a full rebuild — twice per blink, on every
  // character on the page.
  const highlights = f.eyeHighlight.enabled
    ? eyes.map((e) => ({
      x: e.x + (f.eyeHighlight.offsetX / 100) * f.width,
      y: e.y + (f.eyeHighlight.offsetY / 100) * f.height,
      r: Math.max(0.6, (f.eyeHighlight.size / 100) * Math.min(f.width, f.height) * 0.5),
      fade: Math.max(0, 1 - blink * 1.6),
    }))
    : [];

  const box = bounds2(allPoints);

  return {
    viewBox: VIEWBOX,
    size: s.camera.size,
    name: doc.metadata.name,
    background: {
      style: s.appearance.backgroundStyle,
      color: grade(background, cg),
      shade: grade(mix(background, "#000000", 0.16), cg),
    },
    frame: {
      kind: s.camera.frame,
      shadow: s.camera.showFrameShadow ? s.camera.frameShadow : null,
      padding: s.camera.padding,
    },
    parts,
    outline: s.effects.showOutline && s.effects.outline.width > 0
      ? { color: outlineColor, width: s.effects.outline.width, opacity: s.effects.outline.opacity / 100 }
      : null,
    seams: s.effects.seams !== false,
    shadow: s.effects.showAvatarShadow ? s.effects.avatarShadow : null,
    faceShadow: s.effects.showFaceShadow ? s.effects.faceShadow : null,
    face: frame && frame.visible ? {
      matrix: frame.matrix,
      opacity: frame.opacity,
      scale: FACE_SCALE,
      ink,
      eyes,
      highlights,
      highlightColor: f.eyeHighlight.color,
      highlightOpacity: f.eyeHighlight.opacity / 100,
      mouth: f.mouthEnabled && f.mouthShape !== "none"
        ? { d: mouthPath(f), y: f.mouthY, rotation: f.mouthRotation, stroke: f.mouthShape === "cat" }
        : null,
      nose: f.noseEnabled
        ? { d: nosePath(f), y: f.noseY, rotation: f.noseRotation }
        : null,
    } : null,
    bounds: box,
  };
}

function contrastOk(ink, on) {
  const l = (hexc) => {
    const h = String(hexc).replace("#", "");
    const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
    const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
      const x = v / 255;
      return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const a = l(ink), b = l(on);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) >= 2;
}
