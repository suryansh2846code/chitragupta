/**
 * A character from a seed.
 *
 * The point of this file is that nobody should ever see an empty avatar slot.
 * Hand it an agent id, a username, an email, and it returns a complete,
 * distinct, deliberately-composed character — the same one, every time, on
 * every machine, with nothing stored anywhere.
 *
 * Deterministic all the way down. `Math.random` appears exactly once in this
 * package, in `randomScene()`, where the user pressed a button labelled
 * "Surprise me" and is asking for it. Everywhere else the generator is seeded
 * from the string it was given, because an identity that changes on refresh is
 * not an identity.
 *
 * The variation is deliberately narrow in the places that carry recognition —
 * body family and palette — and wide in the places that carry personality, like
 * eye shape and the angle of a glance. Two agents should be told apart at 28px
 * across a rail, which is a much harder constraint than "look different".
 */

import { rng, pick, pickInt, hashString } from "./math.js";
import { PALETTES } from "./palettes.js";
import { PRESETS, applyPreset } from "./presets.js";
import { defaultScene, normalize, EYE_SHAPES } from "./schema.js";

/**
 * Preset families, weighted.
 *
 * `blob` and `pill` appear more than once because a roster where every agent is
 * an animal reads as a sticker pack. The plain bodies are the baseline that
 * makes the cat and the fox feel like a choice.
 */
const BODY_POOL = [
  "blob", "blob", "pill", "cloud", "cat", "bear",
  "rabbit", "ghost", "bot", "fox", "owl", "panda", "stack", "blob",
];

/**
 * Compose a character for `seed`.
 *
 * `options.palette` pins the palette (for a host with its own brand colours),
 * `options.preset` pins the body, and `options.name` sets the document name —
 * everything else is derived. Pinning one axis and letting the rest vary is the
 * usual case: one product's agents all in its own palette, still individually
 * recognisable.
 */
export function generateScene(seed, options) {
  const opts = options || {};
  const h = hashString(seed == null ? "" : seed);
  const next = rng(h);

  const presetId = opts.preset || BODY_POOL[h % BODY_POOL.length];
  const pal = opts.palette || pick(next, PALETTES).id;

  let doc = defaultScene();
  doc.metadata.name = opts.name || String(seed || "Character").slice(0, 80);
  doc.scene.appearance.paletteId = pal;
  doc = applyPreset(doc, presetId);
  if (opts.name) doc.metadata.name = opts.name;
  else if (seed) doc.metadata.name = String(seed).slice(0, 80);

  const f = doc.scene.face;

  // Eye shape is the single strongest cue at small sizes, so it varies first
  // and widest — but a preset that chose its own eyes (a cat's, a fox's) keeps
  // them, because those are part of what makes it that creature.
  const presetFace = PRESETS.find((p) => p.id === presetId);
  if (!presetFace || !presetFace.face || !presetFace.face.eyeShape) {
    f.eyeShape = pick(next, EYE_SHAPES);
  }
  f.width = Math.round(f.width * (0.82 + next() * 0.4));
  f.height = Math.round(f.height * (0.8 + next() * 0.45));
  f.gap = Math.round(f.gap * (0.84 + next() * 0.36));
  f.eyeRoundness = pickInt(next, 55, 100);

  // A tilt in the eyes is the cheapest expression there is. Kept small and
  // mirrored, so it reads as a mood rather than as a misaligned render.
  if (next() < 0.45) {
    const tilt = pickInt(next, 4, 13);
    f.leftEyeRotation = -tilt;
    f.rightEyeRotation = tilt;
  }
  if (next() < 0.3) {
    f.eyeHighlight.enabled = true;
    f.eyeHighlight.size = pickInt(next, 18, 32);
  }
  if (!f.mouthEnabled && next() < 0.35) {
    f.mouthEnabled = true;
    f.mouthShape = pick(next, ["curve", "line", "cat"]);
    f.mouthWidth = pickInt(next, 32, 60);
    f.mouthCurve = pickInt(next, 10, 70);
    f.mouthY = pickInt(next, 40, 62);
  }

  // A character looking slightly off-axis is alive; one looking straight down
  // the barrel is a passport photo. The range stays inside what the follow
  // loop can turn back through, so it never looks stuck.
  const v = doc.scene.view;
  v.yaw = round2((next() - 0.5) * 0.34);
  v.pitch = round2(0.16 + next() * 0.24);
  v.roll = round2((next() - 0.5) * 0.06);
  v.scale = round2(0.97 + next() * 0.1);

  doc.scene.camera.frame = opts.frame || "rounded";
  doc.scene.appearance.backgroundStyle = next() < 0.22 ? "gradient" : "solid";

  return normalize(doc);
}

/** "Surprise me": a character nobody has seen, including us. */
export function randomScene(options) {
  const seed = `${Math.random()}-${Math.random()}`;
  const doc = generateScene(seed, Object.assign({ name: "Character" }, options));
  doc.metadata.name = (options && options.name) || "Character";
  return doc;
}

function round2(n) {
  return Math.round(n * 100) / 100;
}
