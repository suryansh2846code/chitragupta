/**
 * The document.
 *
 * A character is a plain JSON object and nothing else. The renderer holds no
 * state the document does not describe, which is what makes a character
 * shareable as a URL, storable as one TEXT column, diffable in review, and
 * re-renderable years later at a size nobody has thought of yet.
 *
 * Three rules hold that property up:
 *
 *   1. **Every value is a number, a string or a boolean.** No functions, no
 *      element references, no colours resolved at write time. A palette is
 *      stored by id, so a document written today picks up a palette fixed
 *      tomorrow instead of freezing yesterday's mistake.
 *   2. **`normalize()` is the only door in.** Nothing downstream of it checks
 *      whether a field exists — a partial document, a document from a newer
 *      version of this package, and a hand-edited document with a string where
 *      a number belongs all come out of it complete and in range. A renderer
 *      that has to guard every read is a renderer that will eventually guard
 *      one wrong.
 *   3. **Unknown keys survive the round trip.** A document written by a later
 *      version, loaded here and saved again keeps the fields this version did
 *      not understand. Dropping them silently would mean an old tab quietly
 *      deleting work done in a new one.
 *
 * The field names are deliberately the long ones (`positionX`, not `x`). They
 * are read by people in a URL and in a database row far more often than they
 * are typed.
 */

import { clamp } from "./math.js";
import { PALETTE_IDS } from "./palettes.js";
import { SHAPES } from "./primitives.js";

export const SCHEMA_ID = "character.scene";
export const VERSION = 1;

/**
 * The scene's own unit. Parts are positioned and sized in hundredths of this,
 * so "width: 100" is one unit wide and the numbers a person sees in the editor
 * read as percentages without any conversion.
 */
export const UNIT = 100;

/**
 * Slider ranges, in one place.
 *
 * The editor builds its controls from this table rather than hardcoding min /
 * max / step beside each control, so a range can never disagree with the clamp
 * that `normalize()` applies to the same field — which is the bug where a
 * slider goes to 200 and the value silently snaps back to 150 on reload.
 */
export const LIMITS = {
  part: {
    positionX: { min: -120, max: 120, step: 1, unit: "" },
    positionY: { min: -120, max: 120, step: 1, unit: "" },
    positionZ: { min: -120, max: 120, step: 1, unit: "" },
    width: { min: 4, max: 200, step: 1, unit: "%" },
    height: { min: 4, max: 200, step: 1, unit: "%" },
    depth: { min: 4, max: 200, step: 1, unit: "%" },
    rotationX: { min: -180, max: 180, step: 1, unit: "°" },
    rotationY: { min: -180, max: 180, step: 1, unit: "°" },
    rotationZ: { min: -180, max: 180, step: 1, unit: "°" },
    round: { min: 0, max: 100, step: 1, unit: "%" },
    taper: { min: 0, max: 100, step: 1, unit: "%" },
    shade: { min: -60, max: 60, step: 1, unit: "%" },
  },
  face: {
    width: { min: 4, max: 80, step: 1, unit: "" },
    height: { min: 4, max: 120, step: 1, unit: "" },
    gap: { min: 0, max: 120, step: 1, unit: "" },
    eyeRoundness: { min: 0, max: 100, step: 1, unit: "%" },
    rotation: { min: -45, max: 45, step: 1, unit: "°" },
    leftEyeRotation: { min: -45, max: 45, step: 1, unit: "°" },
    rightEyeRotation: { min: -45, max: 45, step: 1, unit: "°" },
    offsetX: { min: -80, max: 80, step: 1, unit: "" },
    offsetY: { min: -80, max: 80, step: 1, unit: "" },
    mouthWidth: { min: 4, max: 140, step: 1, unit: "" },
    mouthHeight: { min: 1, max: 80, step: 1, unit: "" },
    mouthY: { min: -80, max: 120, step: 1, unit: "" },
    mouthCurve: { min: -100, max: 100, step: 1, unit: "" },
    mouthRotation: { min: -45, max: 45, step: 1, unit: "°" },
    noseWidth: { min: 2, max: 80, step: 1, unit: "" },
    noseHeight: { min: 2, max: 80, step: 1, unit: "" },
    noseY: { min: -60, max: 100, step: 1, unit: "" },
    noseRotation: { min: -180, max: 180, step: 1, unit: "°" },
  },
  view: {
    yaw: { min: -1.2, max: 1.2, step: 0.01, unit: "" },
    pitch: { min: -1.0, max: 1.0, step: 0.01, unit: "" },
    roll: { min: -0.8, max: 0.8, step: 0.01, unit: "" },
    scale: { min: 0.4, max: 3, step: 0.01, unit: "×" },
    positionX: { min: -150, max: 150, step: 1, unit: "" },
    positionY: { min: -150, max: 150, step: 1, unit: "" },
  },
  effects: {
    outlineWidth: { min: 0, max: 24, step: 0.5, unit: "px" },
    opacity: { min: 0, max: 100, step: 1, unit: "%" },
    distance: { min: 0, max: 80, step: 1, unit: "px" },
    softness: { min: 0, max: 60, step: 1, unit: "px" },
    direction: { min: 0, max: 360, step: 1, unit: "°" },
    brightness: { min: 0.3, max: 2, step: 0.01, unit: "×" },
    saturation: { min: 0, max: 2, step: 0.01, unit: "×" },
    tintAmount: { min: 0, max: 100, step: 1, unit: "%" },
  },
  follow: {
    yawRange: { min: 0, max: 70, step: 1, unit: "°" },
    pitchRange: { min: 0, max: 50, step: 1, unit: "°" },
    eyeShift: { min: 0, max: 100, step: 1, unit: "%" },
    stiffness: { min: 1, max: 30, step: 0.5, unit: "" },
    damping: { min: 0.4, max: 1.5, step: 0.01, unit: "" },
  },
  lighting: {
    azimuth: { min: -180, max: 180, step: 1, unit: "°" },
    elevation: { min: -90, max: 90, step: 1, unit: "°" },
    strength: { min: 0, max: 100, step: 1, unit: "%" },
  },
};

export const EYE_SHAPES = ["rounded", "ellipse", "leaf", "line"];
export const MOUTH_SHAPES = ["curve", "line", "oval", "cat", "none"];
export const NOSE_SHAPES = ["inverted-triangle", "oval", "line", "heart"];
export const FRAMES = ["rounded", "squircle", "circle", "square", "none"];
export const FITS = ["contain", "none"];
export const BACKGROUND_STYLES = ["solid", "gradient", "ring", "none"];
export const PART_ROLES = ["body", "ear", "snout", "limb", "accessory"];

/** A part with every field present. Callers override what they care about. */
export function defaultPart(over) {
  return Object.assign({
    id: "part",
    role: "body",
    shape: "sphere",
    color: null,        // null means "take the palette's body colour"
    shade: 0,           // percent toward the palette's shade colour, +/-
    positionX: 0,
    positionY: 0,
    positionZ: 0,
    width: 100,
    height: 100,
    depth: 100,
    rotationX: 0,
    rotationY: 0,
    rotationZ: 0,
    round: 30,
    taper: 55,
    faceHost: false,
    outline: true,
  }, over || {});
}

/**
 * The document a brand-new character starts from: one soft body, two eyes, no
 * mouth, no nose. It is deliberately the plainest thing that still has a face —
 * a first screen with a fully-featured animal on it reads as "here is someone
 * else's character", not as "here is yours to build".
 */
export function defaultScene() {
  return {
    schema: SCHEMA_ID,
    version: VERSION,
    metadata: { name: "Character" },
    scene: {
      appearance: {
        paletteId: "coral",
        backgroundStyle: "solid",
        background: null,   // null means "take the palette's background"
      },
      camera: {
        size: 256,
        frame: "rounded",
        // "contain" keeps the whole character in frame whatever it is built
        // from and whichever way it is turned; "none" is the absolute scale,
        // where composing a deliberate crop is the caller's job.
        fit: "contain",
        padding: 10,
        showFrameShadow: true,
        frameShadow: { direction: 90, distance: 12, opacity: 22, softness: 24, color: "#000000" },
      },
      entity: {
        preset: "custom",
        parts: [defaultPart({ id: "body", role: "body", shape: "rounded-box", round: 62, faceHost: true })],
      },
      face: {
        // A character with no face at all is a legitimate design — a mark, a
        // token, a shape. It is also what the editor's shape thumbnails need,
        // and inventing a way to hide the face per-thumbnail instead of putting
        // it in the document is how a preview stops matching what it previews.
        enabled: true,
        color: null,
        offsetX: 0,
        offsetY: 0,
        rotation: 0,
        width: 28,
        height: 64,
        gap: 41,
        eyeShape: "rounded",
        eyeRoundness: 100,
        leftEyeRotation: 0,
        rightEyeRotation: 0,
        eyeHighlight: { enabled: false, color: "#ffffff", offsetX: -18, offsetY: -20, opacity: 92, size: 24 },
        mouthEnabled: false,
        mouthShape: "curve",
        mouthWidth: 52,
        mouthHeight: 12,
        mouthY: 52,
        mouthCurve: 45,
        mouthRotation: 0,
        noseEnabled: false,
        noseShape: "inverted-triangle",
        noseWidth: 10,
        noseHeight: 18,
        noseY: 22,
        noseRotation: 0,
      },
      effects: {
        showOutline: true,
        outline: { color: null, opacity: 80, width: 4 },
        showAvatarShadow: true,
        avatarShadow: { color: "#000000", direction: 45, distance: 12, opacity: 24, softness: 16 },
        showFaceShadow: false,
        faceShadow: { color: "#000000", direction: 50, distance: 4, opacity: 28, softness: 0 },
        seams: true,
        colorGrade: { brightness: 1, saturation: 1, tintAmount: 0, tintR: 0, tintG: 0, tintB: 0 },
      },
      lighting: { enabled: false, azimuth: -35, elevation: 40, strength: 40 },
      follow: {
        enabled: true,
        scope: "element",
        yawRange: 26,
        pitchRange: 16,
        eyeShift: 35,
        stiffness: 9,
        damping: 0.85,
        blink: true,
        respectReducedMotion: true,
      },
      // `scale` is a zoom relative to the auto-fit, so 1 means "as large as fits".
      view: { yaw: 0.08, pitch: 0.3, roll: 0.01, scale: 1, positionX: 0, positionY: 0 },
      decals: [],
    },
  };
}

// ── normalisation ──────────────────────────────────────────────────────────

const num = (v, def, lo, hi) => {
  const n = typeof v === "number" ? v : parseFloat(v);
  if (!Number.isFinite(n)) return def;
  return lo == null ? n : clamp(n, lo, hi);
};
const bool = (v, def) => (typeof v === "boolean" ? v : def);
const str = (v, def) => (typeof v === "string" && v ? v : def);
const oneOf = (v, list, def) => (list.indexOf(v) >= 0 ? v : def);
const hex = (v, def) => (typeof v === "string" && /^#[0-9a-fA-F]{3,8}$/.test(v.trim()) ? v.trim() : def);
/** A colour that is allowed to be absent, meaning "inherit from the palette". */
const hexOrNull = (v) => (v == null ? null : hex(v, null));

function lim(group, key) {
  const t = LIMITS[group] && LIMITS[group][key];
  return t || { min: null, max: null };
}

function normShadow(src, def) {
  const s = src || {};
  return {
    color: hex(s.color, def.color),
    direction: num(s.direction, def.direction, 0, 360),
    distance: num(s.distance, def.distance, 0, 200),
    opacity: num(s.opacity, def.opacity, 0, 100),
    softness: num(s.softness, def.softness, 0, 200),
  };
}

function normPart(src, i, defaults) {
  const s = src && typeof src === "object" ? src : {};
  const d = defaults || defaultPart();
  const L = LIMITS.part;
  const p = Object.assign({}, s, {
    id: str(s.id, `part-${i + 1}`),
    role: oneOf(s.role, PART_ROLES, d.role),
    shape: oneOf(s.shape, SHAPES, d.shape),
    color: hexOrNull(s.color),
    shade: num(s.shade, d.shade, L.shade.min, L.shade.max),
    positionX: num(s.positionX, d.positionX, L.positionX.min, L.positionX.max),
    positionY: num(s.positionY, d.positionY, L.positionY.min, L.positionY.max),
    positionZ: num(s.positionZ, d.positionZ, L.positionZ.min, L.positionZ.max),
    width: num(s.width, d.width, L.width.min, L.width.max),
    height: num(s.height, d.height, L.height.min, L.height.max),
    depth: num(s.depth, d.depth, L.depth.min, L.depth.max),
    rotationX: num(s.rotationX, d.rotationX, L.rotationX.min, L.rotationX.max),
    rotationY: num(s.rotationY, d.rotationY, L.rotationY.min, L.rotationY.max),
    rotationZ: num(s.rotationZ, d.rotationZ, L.rotationZ.min, L.rotationZ.max),
    round: num(s.round, d.round, L.round.min, L.round.max),
    taper: num(s.taper, d.taper, L.taper.min, L.taper.max),
    faceHost: bool(s.faceHost, false),
    outline: bool(s.outline, true),
  });
  return p;
}

/**
 * Bring any object into a complete, in-range document.
 *
 * Total by design: it never throws and never returns null, because the two
 * things that call it are "a URL someone pasted" and "a row read back from
 * disk", and both of those must degrade to a visible character rather than to
 * an error surface. A document so broken that nothing survives comes out as
 * the default one.
 */
export function normalize(input) {
  const base = defaultScene();
  const raw = migrate(input);
  if (!raw || typeof raw !== "object") return base;

  const src = raw.scene && typeof raw.scene === "object" ? raw.scene : {};
  const b = base.scene;

  const appearance = Object.assign({}, src.appearance, {
    paletteId: oneOf(src.appearance && src.appearance.paletteId, PALETTE_IDS, b.appearance.paletteId),
    backgroundStyle: oneOf(src.appearance && src.appearance.backgroundStyle, BACKGROUND_STYLES, b.appearance.backgroundStyle),
    background: hexOrNull(src.appearance && src.appearance.background),
  });

  const cam = src.camera || {};
  const camera = Object.assign({}, cam, {
    size: num(cam.size, b.camera.size, 16, 4096),
    frame: oneOf(cam.frame, FRAMES, b.camera.frame),
    fit: oneOf(cam.fit, FITS, b.camera.fit),
    showFrameShadow: bool(cam.showFrameShadow, b.camera.showFrameShadow),
    frameShadow: normShadow(cam.frameShadow, b.camera.frameShadow),
    padding: num(cam.padding, b.camera.padding, 0, 40),
  });

  const ent = src.entity || {};
  let parts = Array.isArray(ent.parts) ? ent.parts : [];
  // An empty part list is a real document that would render as nothing at all.
  // The character that comes back is the default body, so a blank scene is a
  // starting point rather than an empty frame the user has to diagnose.
  if (!parts.length) parts = b.entity.parts;
  parts = parts.slice(0, 24).map((p, i) => normPart(p, i));
  // Exactly one part carries the face. Two hosts would draw two faces; zero
  // would draw none, and "my character lost its eyes" is the single most
  // alarming way this can fail.
  const hostIdx = parts.findIndex((p) => p.faceHost);
  parts.forEach((p, i) => { p.faceHost = i === (hostIdx >= 0 ? hostIdx : 0); });

  const f = src.face || {};
  const FL = LIMITS.face;
  const face = Object.assign({}, f, {
    enabled: bool(f.enabled, b.face.enabled),
    color: hexOrNull(f.color),
    offsetX: num(f.offsetX, b.face.offsetX, FL.offsetX.min, FL.offsetX.max),
    offsetY: num(f.offsetY, b.face.offsetY, FL.offsetY.min, FL.offsetY.max),
    rotation: num(f.rotation, b.face.rotation, FL.rotation.min, FL.rotation.max),
    width: num(f.width, b.face.width, FL.width.min, FL.width.max),
    height: num(f.height, b.face.height, FL.height.min, FL.height.max),
    gap: num(f.gap, b.face.gap, FL.gap.min, FL.gap.max),
    eyeShape: oneOf(f.eyeShape, EYE_SHAPES, b.face.eyeShape),
    eyeRoundness: num(f.eyeRoundness, b.face.eyeRoundness, 0, 100),
    leftEyeRotation: num(f.leftEyeRotation, b.face.leftEyeRotation, FL.leftEyeRotation.min, FL.leftEyeRotation.max),
    rightEyeRotation: num(f.rightEyeRotation, b.face.rightEyeRotation, FL.rightEyeRotation.min, FL.rightEyeRotation.max),
    eyeHighlight: {
      enabled: bool(f.eyeHighlight && f.eyeHighlight.enabled, b.face.eyeHighlight.enabled),
      color: hex(f.eyeHighlight && f.eyeHighlight.color, b.face.eyeHighlight.color),
      offsetX: num(f.eyeHighlight && f.eyeHighlight.offsetX, b.face.eyeHighlight.offsetX, -100, 100),
      offsetY: num(f.eyeHighlight && f.eyeHighlight.offsetY, b.face.eyeHighlight.offsetY, -100, 100),
      opacity: num(f.eyeHighlight && f.eyeHighlight.opacity, b.face.eyeHighlight.opacity, 0, 100),
      size: num(f.eyeHighlight && f.eyeHighlight.size, b.face.eyeHighlight.size, 0, 100),
    },
    mouthEnabled: bool(f.mouthEnabled, b.face.mouthEnabled),
    mouthShape: oneOf(f.mouthShape, MOUTH_SHAPES, b.face.mouthShape),
    mouthWidth: num(f.mouthWidth, b.face.mouthWidth, FL.mouthWidth.min, FL.mouthWidth.max),
    mouthHeight: num(f.mouthHeight, b.face.mouthHeight, FL.mouthHeight.min, FL.mouthHeight.max),
    mouthY: num(f.mouthY, b.face.mouthY, FL.mouthY.min, FL.mouthY.max),
    mouthCurve: num(f.mouthCurve, b.face.mouthCurve, FL.mouthCurve.min, FL.mouthCurve.max),
    mouthRotation: num(f.mouthRotation, b.face.mouthRotation, FL.mouthRotation.min, FL.mouthRotation.max),
    noseEnabled: bool(f.noseEnabled, b.face.noseEnabled),
    noseShape: oneOf(f.noseShape, NOSE_SHAPES, b.face.noseShape),
    noseWidth: num(f.noseWidth, b.face.noseWidth, FL.noseWidth.min, FL.noseWidth.max),
    noseHeight: num(f.noseHeight, b.face.noseHeight, FL.noseHeight.min, FL.noseHeight.max),
    noseY: num(f.noseY, b.face.noseY, FL.noseY.min, FL.noseY.max),
    noseRotation: num(f.noseRotation, b.face.noseRotation, FL.noseRotation.min, FL.noseRotation.max),
  });

  const e = src.effects || {};
  const effects = Object.assign({}, e, {
    showOutline: bool(e.showOutline, b.effects.showOutline),
    outline: {
      color: hexOrNull(e.outline && e.outline.color),
      opacity: num(e.outline && e.outline.opacity, b.effects.outline.opacity, 0, 100),
      width: num(e.outline && e.outline.width, b.effects.outline.width, 0, 24),
    },
    showAvatarShadow: bool(e.showAvatarShadow, b.effects.showAvatarShadow),
    avatarShadow: normShadow(e.avatarShadow, b.effects.avatarShadow),
    showFaceShadow: bool(e.showFaceShadow, b.effects.showFaceShadow),
    faceShadow: normShadow(e.faceShadow, b.effects.faceShadow),
    seams: bool(e.seams, b.effects.seams),
    colorGrade: {
      brightness: num(e.colorGrade && e.colorGrade.brightness, 1, 0.2, 3),
      saturation: num(e.colorGrade && e.colorGrade.saturation, 1, 0, 3),
      tintAmount: num(e.colorGrade && e.colorGrade.tintAmount, 0, 0, 100),
      tintR: num(e.colorGrade && e.colorGrade.tintR, 0, 0, 255),
      tintG: num(e.colorGrade && e.colorGrade.tintG, 0, 0, 255),
      tintB: num(e.colorGrade && e.colorGrade.tintB, 0, 0, 255),
    },
  });

  const li = src.lighting || {};
  const lighting = {
    enabled: bool(li.enabled, b.lighting.enabled),
    azimuth: num(li.azimuth, b.lighting.azimuth, -180, 180),
    elevation: num(li.elevation, b.lighting.elevation, -90, 90),
    strength: num(li.strength, b.lighting.strength, 0, 100),
  };

  const fo = src.follow || {};
  const FOL = LIMITS.follow;
  const follow = {
    enabled: bool(fo.enabled, b.follow.enabled),
    scope: oneOf(fo.scope, ["element", "window"], b.follow.scope),
    yawRange: num(fo.yawRange, b.follow.yawRange, FOL.yawRange.min, FOL.yawRange.max),
    pitchRange: num(fo.pitchRange, b.follow.pitchRange, FOL.pitchRange.min, FOL.pitchRange.max),
    eyeShift: num(fo.eyeShift, b.follow.eyeShift, FOL.eyeShift.min, FOL.eyeShift.max),
    stiffness: num(fo.stiffness, b.follow.stiffness, FOL.stiffness.min, FOL.stiffness.max),
    damping: num(fo.damping, b.follow.damping, FOL.damping.min, FOL.damping.max),
    blink: bool(fo.blink, b.follow.blink),
    respectReducedMotion: bool(fo.respectReducedMotion, true),
  };

  const v = src.view || {};
  const VL = LIMITS.view;
  const view = {
    yaw: num(v.yaw, b.view.yaw, VL.yaw.min, VL.yaw.max),
    pitch: num(v.pitch, b.view.pitch, VL.pitch.min, VL.pitch.max),
    roll: num(v.roll, b.view.roll, VL.roll.min, VL.roll.max),
    scale: num(v.scale, b.view.scale, VL.scale.min, VL.scale.max),
    positionX: num(v.positionX, b.view.positionX, VL.positionX.min, VL.positionX.max),
    positionY: num(v.positionY, b.view.positionY, VL.positionY.min, VL.positionY.max),
  };

  return {
    schema: SCHEMA_ID,
    version: VERSION,
    metadata: { name: str(raw.metadata && raw.metadata.name, "Character").slice(0, 80) },
    scene: Object.assign({}, src, {
      appearance, camera, entity: Object.assign({}, ent, { preset: str(ent.preset, "custom"), parts }),
      face, effects, lighting, follow, view,
      decals: Array.isArray(src.decals) ? src.decals.slice(0, 24) : [],
    }),
  };
}

/**
 * Documents from other producers, brought to this schema.
 *
 * Right now that means `oneworks.avatar` v1, the format the reference builder
 * puts in its share links. Most of its field names are already ours — the
 * shapes of `face`, `effects`, `camera` and `view` were kept deliberately
 * identical so importing one is mostly a matter of inventing the parts it never
 * stored, which it expressed as `appearance.bodyShape` instead.
 *
 * Migration is additive and never destructive: an unrecognised schema is passed
 * through untouched and `normalize()` salvages whatever of it lines up.
 */
export function migrate(input) {
  if (!input || typeof input !== "object") return input;
  if (input.schema !== "oneworks.avatar") return input;

  const src = input.scene || {};
  const out = JSON.parse(JSON.stringify(input));
  out.schema = SCHEMA_ID;
  out.version = VERSION;

  const parts = Array.isArray(src.entity && src.entity.parts) ? src.entity.parts : [];
  if (!parts.length) {
    const shape = BODY_SHAPE_IMPORT[(src.appearance && src.appearance.bodyShape) || ""] || "rounded-box";
    out.scene.entity = Object.assign({}, src.entity, {
      parts: [defaultPart({ id: "body", role: "body", shape, round: 62, faceHost: true })],
    });
  }
  // That format has no auto-fit and composes its crop by hand, so honouring its
  // `view.scale` literally is the only way an imported link looks like the
  // picture the person copied it from.
  out.scene.camera = Object.assign({}, src.camera, { fit: "none" });

  // That format stores the card colour on the camera; here it belongs to the
  // background, because the camera should be able to change without repainting.
  if (src.camera && src.camera.background && !(src.appearance && src.appearance.background)) {
    out.scene.appearance = Object.assign({}, src.appearance, { background: src.camera.background });
  }
  return out;
}

const BODY_SHAPE_IMPORT = {
  capsule: "capsule",
  sphere: "sphere",
  blob: "rounded-box",
  cube: "box",
  egg: "teardrop",
  drop: "teardrop",
};

/** A deep copy, so a caller mutating what it got back cannot reach our state. */
export function cloneScene(doc) {
  return JSON.parse(JSON.stringify(doc));
}
