/**
 * The built-in characters.
 *
 * Each preset is a *partial* document: the parts and the face fields that make
 * it that creature, and nothing else. Palette, camera, effects and view come
 * from the defaults, or from whatever the person already had — which is what
 * makes "try another body" a body change rather than a reset of everything they
 * had already tuned.
 *
 * Every preset is built from the same convex primitives the editor exposes.
 * There is no hidden shape vocabulary here that a person cannot reach from the
 * parts panel — a preset is a starting point, never a special case.
 *
 * On the geometry: ears and other satellites sit at a negative Z so the head
 * paints over them. That is what produces the seam where they meet, and the
 * seam is the whole reason the result reads as stacked solids rather than as a
 * flat sticker.
 */

import { defaultPart } from "./schema.js";

const part = defaultPart;

export const PRESETS = [
  {
    id: "blob",
    name: "Blob",
    parts: [part({ id: "body", shape: "rounded-box", round: 62, width: 100, height: 96, depth: 92, faceHost: true })],
    face: { eyeShape: "rounded", eyeRoundness: 100, width: 28, height: 64, gap: 41 },
  },
  {
    id: "pill",
    name: "Pill",
    parts: [part({ id: "body", shape: "capsule", width: 82, height: 116, depth: 82, faceHost: true })],
    face: { eyeShape: "rounded", eyeRoundness: 100, width: 24, height: 56, gap: 38, offsetY: -6 },
  },
  {
    id: "cloud",
    name: "Cloud",
    parts: [
      part({ id: "lobe-l", shape: "sphere", width: 62, height: 62, depth: 58, positionX: -34, positionY: -10, positionZ: -14 }),
      part({ id: "lobe-r", shape: "sphere", width: 58, height: 58, depth: 56, positionX: 36, positionY: -4, positionZ: -16 }),
      part({ id: "puff", shape: "sphere", width: 54, height: 54, depth: 52, positionX: 6, positionY: 36, positionZ: -20 }),
      part({ id: "body", shape: "rounded-box", round: 78, width: 96, height: 76, depth: 74, positionY: -6, faceHost: true }),
    ],
    face: { eyeShape: "rounded", eyeRoundness: 100, width: 26, height: 58, gap: 36, offsetY: 8 },
  },
  {
    id: "cat",
    name: "Cat",
    parts: [
      part({ id: "ear-l", shape: "cone", width: 38, height: 44, depth: 28, positionX: -34, positionY: 46, positionZ: -34, rotationZ: 12 }),
      part({ id: "ear-r", shape: "cone", width: 38, height: 44, depth: 28, positionX: 34, positionY: 46, positionZ: -34, rotationZ: -12 }),
      part({ id: "body", shape: "rounded-box", round: 84, width: 104, height: 96, depth: 92, faceHost: true }),
    ],
    face: {
      eyeShape: "ellipse", width: 18, height: 42, gap: 40,
      noseEnabled: true, noseShape: "oval", noseWidth: 9, noseHeight: 7, noseY: 34,
    },
  },
  {
    id: "bear",
    name: "Bear",
    parts: [
      part({ id: "ear-l", shape: "sphere", width: 36, height: 36, depth: 30, positionX: -38, positionY: 42, positionZ: -34, shade: 22 }),
      part({ id: "ear-r", shape: "sphere", width: 36, height: 36, depth: 30, positionX: 38, positionY: 42, positionZ: -34, shade: 22 }),
      part({ id: "body", shape: "rounded-box", round: 90, width: 100, height: 92, depth: 90, faceHost: true }),
      part({ id: "muzzle", shape: "sphere", width: 44, height: 34, depth: 30, positionY: -18, positionZ: 34, shade: -30, outline: false }),
    ],
    face: {
      eyeShape: "ellipse", width: 17, height: 22, gap: 44, offsetY: -14,
      noseEnabled: true, noseShape: "inverted-triangle", noseWidth: 14, noseHeight: 10, noseY: 40,
    },
  },
  {
    id: "rabbit",
    name: "Rabbit",
    parts: [
      part({ id: "ear-l", shape: "capsule", width: 24, height: 86, depth: 20, positionX: -26, positionY: 62, positionZ: -30, rotationZ: 13 }),
      part({ id: "ear-r", shape: "capsule", width: 24, height: 86, depth: 20, positionX: 26, positionY: 62, positionZ: -30, rotationZ: -13 }),
      part({ id: "body", shape: "rounded-box", round: 86, width: 96, height: 92, depth: 88, faceHost: true }),
    ],
    face: {
      eyeShape: "rounded", eyeRoundness: 100, width: 20, height: 46, gap: 40, rotation: 0,
      leftEyeRotation: -12, rightEyeRotation: 12,
      noseEnabled: true, noseShape: "heart", noseWidth: 13, noseHeight: 10, noseY: 40,
    },
  },
  {
    id: "panda",
    name: "Panda",
    parts: [
      part({ id: "ear-l", shape: "sphere", width: 34, height: 34, depth: 28, positionX: -40, positionY: 44, positionZ: -34, color: "#26282e" }),
      part({ id: "ear-r", shape: "sphere", width: 34, height: 34, depth: 28, positionX: 40, positionY: 44, positionZ: -34, color: "#26282e" }),
      part({ id: "body", shape: "rounded-box", round: 88, width: 102, height: 94, depth: 90, faceHost: true }),
      part({ id: "patch-l", shape: "ellipsoid", width: 30, height: 34, depth: 20, positionX: -21, positionY: 6, positionZ: 34, color: "#26282e", rotationZ: 12, outline: false }),
      part({ id: "patch-r", shape: "ellipsoid", width: 30, height: 34, depth: 20, positionX: 21, positionY: 6, positionZ: 34, color: "#26282e", rotationZ: -12, outline: false }),
    ],
    face: {
      color: "#f6f7fa", eyeShape: "ellipse", width: 12, height: 14, gap: 54, offsetY: -6,
      noseEnabled: true, noseShape: "oval", noseWidth: 11, noseHeight: 8, noseY: 42,
    },
  },
  {
    id: "ghost",
    name: "Ghost",
    parts: [part({ id: "body", shape: "teardrop", width: 102, height: 118, depth: 96, rotationZ: 180, faceHost: true })],
    face: { eyeShape: "rounded", eyeRoundness: 100, width: 22, height: 50, gap: 40, offsetY: -18 },
  },
  {
    id: "bot",
    name: "Bot",
    parts: [
      part({ id: "antenna", shape: "cylinder", width: 8, height: 42, depth: 8, positionY: 60, positionZ: -30, shade: 30 }),
      part({ id: "bulb", shape: "sphere", width: 20, height: 20, depth: 20, positionY: 84, positionZ: -36, shade: -40 }),
      part({ id: "body", shape: "rounded-box", round: 26, width: 104, height: 86, depth: 84, faceHost: true }),
      part({ id: "ear-l", shape: "cylinder", width: 14, height: 30, depth: 14, positionX: -56, rotationZ: 90, shade: 30 }),
      part({ id: "ear-r", shape: "cylinder", width: 14, height: 30, depth: 14, positionX: 56, rotationZ: 90, shade: 30 }),
    ],
    face: {
      eyeShape: "rounded", eyeRoundness: 40, width: 22, height: 22, gap: 44,
      mouthEnabled: true, mouthShape: "line", mouthWidth: 46, mouthHeight: 8, mouthY: 44,
    },
  },
  {
    id: "fox",
    name: "Fox",
    parts: [
      part({ id: "ear-l", shape: "pyramid", width: 34, height: 46, depth: 22, positionX: -36, positionY: 48, positionZ: -34, rotationZ: 16 }),
      part({ id: "ear-r", shape: "pyramid", width: 34, height: 46, depth: 22, positionX: 36, positionY: 48, positionZ: -34, rotationZ: -16 }),
      part({ id: "body", shape: "rounded-box", round: 74, width: 100, height: 88, depth: 86, faceHost: true }),
      part({ id: "snout", shape: "sphere", width: 40, height: 28, depth: 26, positionY: -22, positionZ: 32, shade: -34, outline: false }),
    ],
    face: {
      eyeShape: "leaf", width: 22, height: 26, gap: 42, offsetY: -12,
      leftEyeRotation: -8, rightEyeRotation: 8,
      noseEnabled: true, noseShape: "inverted-triangle", noseWidth: 12, noseHeight: 9, noseY: 42,
    },
  },
  {
    id: "owl",
    name: "Owl",
    parts: [
      part({ id: "tuft-l", shape: "wedge", width: 26, height: 34, depth: 20, positionX: -34, positionY: 48, positionZ: -30, rotationZ: -8 }),
      part({ id: "tuft-r", shape: "wedge", width: 26, height: 34, depth: 20, positionX: 34, positionY: 48, positionZ: -30, rotationZ: 8, rotationY: 180 }),
      part({ id: "body", shape: "ellipsoid", width: 104, height: 96, depth: 92, faceHost: true }),
      part({ id: "disc-l", shape: "ellipsoid", width: 40, height: 44, depth: 18, positionX: -22, positionY: 8, positionZ: 32, shade: -26, outline: false }),
      part({ id: "disc-r", shape: "ellipsoid", width: 40, height: 44, depth: 18, positionX: 22, positionY: 8, positionZ: 32, shade: -26, outline: false }),
    ],
    face: {
      eyeShape: "ellipse", width: 20, height: 20, gap: 52, offsetY: -8,
      noseEnabled: true, noseShape: "inverted-triangle", noseWidth: 12, noseHeight: 16, noseY: 26,
    },
  },
  {
    id: "stack",
    name: "Stack",
    parts: [
      part({ id: "base", shape: "frustum", taper: 72, width: 96, height: 48, depth: 92, positionY: -34 }),
      part({ id: "body", shape: "rounded-box", round: 46, width: 88, height: 62, depth: 82, positionY: 14, shade: -18, faceHost: true }),
      part({ id: "cap", shape: "dome", width: 60, height: 26, depth: 56, positionY: 52, shade: 26 }),
    ],
    face: { eyeShape: "rounded", eyeRoundness: 100, width: 20, height: 34, gap: 38, offsetY: 4 },
  },
];

const BY_ID = new Map(PRESETS.map((p) => [p.id, p]));
export const PRESET_IDS = PRESETS.map((p) => p.id);

export function preset(id) {
  return BY_ID.get(id) || null;
}

/**
 * Apply a preset on top of an existing document.
 *
 * Body and face only. A person who has spent a minute on their palette, their
 * outline weight and their camera angle and then clicks "Cat" is asking for a
 * cat — not for everything else they chose to be thrown away. The one thing
 * that does get replaced wholesale is the part list, because merging two
 * skeletons produces a creature nobody asked for.
 */
export function applyPreset(doc, id) {
  const p = preset(id);
  if (!p) return doc;
  const next = JSON.parse(JSON.stringify(doc));
  next.scene.entity.preset = p.id;
  next.scene.entity.parts = JSON.parse(JSON.stringify(p.parts));
  next.scene.face = Object.assign({}, next.scene.face, p.face);
  if (next.metadata.name === "Character" || BY_ID.has(slug(next.metadata.name))) {
    next.metadata.name = p.name;
  }
  return next;
}

function slug(s) {
  return String(s || "").trim().toLowerCase();
}
