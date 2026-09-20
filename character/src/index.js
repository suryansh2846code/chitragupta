/**
 * character — geometric 3D avatars in SVG.
 *
 * The whole public surface, in one place. Everything below is re-exported from
 * a module that does one job; nothing is defined here, so this file can be read
 * as the API and nothing else.
 *
 *   import { createCharacter, generateScene } from "character";
 *
 *   createCharacter("#avatar", generateScene("agent:inbox"));
 *
 * That is the ninety-percent case: a seed in, a live character out, following
 * the cursor, with nothing stored and nothing configured.
 *
 * The rest of the surface exists for the other ten percent — an editor that
 * needs the limits table, a server that needs the SVG string without a DOM, a
 * host that needs to put a character in a URL.
 */

export { VERSION, SCHEMA_ID, UNIT, LIMITS, EYE_SHAPES, MOUTH_SHAPES, NOSE_SHAPES,
  FRAMES, FITS, BACKGROUND_STYLES, PART_ROLES,
  defaultScene, defaultPart, normalize, migrate, cloneScene } from "./schema.js";

export { PALETTES, PALETTE_IDS, palette, mix, grade, contrast, luminance, readableInk,
  hexToRgb, rgbToHex } from "./palettes.js";

export { SHAPES, SHAPE_LABELS } from "./primitives.js";

export { PRESETS, PRESET_IDS, preset, applyPreset } from "./presets.js";

export { generateScene, randomScene } from "./generate.js";

export { buildRenderModel, viewMatrix, VIEWBOX } from "./project.js";

export { toSVG, toSVGBody, toDataURL } from "./svg.js";

export { createCharacter, renderToString } from "./renderer.js";

// `_setPointer` and `_activeCount` are test hooks, not API. A host that wants
// to drive the pose itself should use `instance.setPose()` instead.
export { FollowController, _setPointer, _activeCount } from "./follow.js";

export { encode, decode, fromURL, toURL } from "./codec.js";

export { toPNGBlob, svgToPNGBlob, downloadSVG, downloadPNG, downloadBlob } from "./png.js";
