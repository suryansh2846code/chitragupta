/**
 * Render model -> SVG markup.
 *
 * A string, built by concatenation, with no DOM anywhere in it — so this runs
 * unchanged in a browser, in Node, in a worker, and in a test that has never
 * heard of a document. `renderer.js` uses the same output for the live preview
 * rather than building elements a second way; one path means the file a user
 * downloads is provably the image they were looking at.
 *
 * Two things this file is careful about:
 *
 * **Ids.** SVG ids are document-global, not scoped to their `<svg>`. Two
 * characters on one page both calling their blur filter `blur` means the second
 * one silently takes the first one's settings. Every id here is prefixed, and
 * the renderer hands each live instance a distinct prefix.
 *
 * **Escaping.** Colours and the character's name reach the markup from user
 * input and from a pasted URL. They are escaped on the way in, not trusted
 * because they "came from a colour picker" — the same document also arrives
 * from `decode()`, where nothing came from a picker at all.
 */

import { fmt } from "./hull.js";
import { deg } from "./math.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/** Escape for an XML attribute value. */
export function xmlAttr(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[c]
  ));
}

/** Escape for XML text content. */
export function xmlText(v) {
  return String(v == null ? "" : v).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

/**
 * Corner radius per frame kind, as a fraction of the square.
 *
 * `squircle` is drawn as a path rather than an `rx`, because a superellipse and
 * a rounded rectangle differ most exactly where an avatar is looked at most —
 * the corner beside the face.
 */
const FRAME_RADIUS = { rounded: 0.1, squircle: 0.22, circle: 0.5, square: 0, none: 0 };

function framePath(kind, size) {
  const r = (FRAME_RADIUS[kind] == null ? FRAME_RADIUS.rounded : FRAME_RADIUS[kind]) * size;
  if (kind === "circle") {
    const c = size / 2;
    return `M 0 ${c} a ${c} ${c} 0 1 0 ${size} 0 a ${c} ${c} 0 1 0 ${-size} 0 Z`;
  }
  if (kind === "squircle") {
    // A superellipse approximated with one cubic per corner. `k` is the classic
    // 0.55 circle constant pulled in, which is what gives the continuous
    // curvature a plain rounded rect does not have.
    const k = r * 0.42;
    return [
      `M 0 ${fmt(r)}`,
      `C 0 ${fmt(k)} ${fmt(k)} 0 ${fmt(r)} 0`,
      `H ${fmt(size - r)}`,
      `C ${fmt(size - k)} 0 ${size} ${fmt(k)} ${size} ${fmt(r)}`,
      `V ${fmt(size - r)}`,
      `C ${size} ${fmt(size - k)} ${fmt(size - k)} ${size} ${fmt(size - r)} ${size}`,
      `H ${fmt(r)}`,
      `C ${fmt(k)} ${size} 0 ${fmt(size - k)} 0 ${fmt(size - r)}`,
      "Z",
    ].join(" ");
  }
  if (!r) return `M 0 0 H ${size} V ${size} H 0 Z`;
  return [
    `M ${fmt(r)} 0`,
    `H ${fmt(size - r)}`,
    `A ${fmt(r)} ${fmt(r)} 0 0 1 ${size} ${fmt(r)}`,
    `V ${fmt(size - r)}`,
    `A ${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(size - r)} ${size}`,
    `H ${fmt(r)}`,
    `A ${fmt(r)} ${fmt(r)} 0 0 1 0 ${fmt(size - r)}`,
    `V ${fmt(r)}`,
    `A ${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(r)} 0`,
    "Z",
  ].join(" ");
}

/** A shadow spec (direction in degrees, distance, softness, opacity) -> dx/dy/blur. */
function shadowOffset(sh) {
  const a = deg(sh.direction);
  return {
    dx: Math.cos(a) * sh.distance,
    dy: Math.sin(a) * sh.distance,
    blur: sh.softness / 2,
    opacity: sh.opacity / 100,
    color: sh.color || "#000000",
  };
}

function dropShadowFilter(id, sh, pad = 40) {
  const o = shadowOffset(sh);
  return `<filter id="${xmlAttr(id)}" x="${-pad}%" y="${-pad}%" width="${100 + pad * 2}%" height="${100 + pad * 2}%" color-interpolation-filters="sRGB">`
    + `<feDropShadow dx="${fmt(o.dx)}" dy="${fmt(o.dy)}" stdDeviation="${fmt(o.blur)}"`
    + ` flood-color="${xmlAttr(o.color)}" flood-opacity="${fmt(o.opacity)}"/></filter>`;
}

function backgroundMarkup(model, size, ids) {
  const bg = model.background;
  if (bg.style === "none") return "";
  if (bg.style === "gradient") {
    return `<rect width="${size}" height="${size}" fill="url(#${xmlAttr(ids.bgGrad)})"/>`;
  }
  if (bg.style === "ring") {
    const c = size / 2;
    return `<rect width="${size}" height="${size}" fill="${xmlAttr(bg.shade)}"/>`
      + `<circle cx="${c}" cy="${c}" r="${fmt(size * 0.38)}" fill="${xmlAttr(bg.color)}"/>`;
  }
  return `<rect width="${size}" height="${size}" fill="${xmlAttr(bg.color)}"/>`;
}

/**
 * The body, drawn back to front.
 *
 * Two modes, and the difference is the whole visual signature of this style.
 *
 * **Seams on** (the default): each part paints its own fill and then its own
 * outline. A nearer part's outline lands on top of a farther part's fill, so
 * the join between an ear and a head is a visible line — the same line the
 * reference draws across a cat's face. The character reads as assembled solids.
 *
 * **Seams off**: every silhouette is painted in the outline colour first, then
 * every fill goes over the top. Only the outermost contour survives, so the
 * character reads as one cut-out shape. Costs a second pass over the parts.
 */
function bodyMarkup(model, hook) {
  const out = model.outline;
  let s = "";
  if (!model.seams && out) {
    for (const p of model.parts) {
      if (!p.outline) continue;
      s += `<path${hook("s")} d="${p.d}" fill="${xmlAttr(out.color)}" stroke="${xmlAttr(out.color)}"`
        + ` stroke-width="${fmt(out.width)}" stroke-linejoin="round" opacity="${fmt(out.opacity)}"/>`;
    }
    for (const p of model.parts) s += `<path${hook("p")} d="${p.d}" fill="${xmlAttr(p.fill)}"/>`;
    return s;
  }
  for (const p of model.parts) {
    const stroke = out && p.outline
      ? ` stroke="${xmlAttr(out.color)}" stroke-width="${fmt(out.width)}" stroke-linejoin="round"`
        + (out.opacity < 1 ? ` stroke-opacity="${fmt(out.opacity)}"` : "")
      : "";
    s += `<path${hook("p")} d="${p.d}" fill="${xmlAttr(p.fill)}"${stroke}/>`;
  }
  return s;
}

function faceMarkup(face, hook) {
  if (!face) return "";
  const m = face.matrix.map(fmt).join(" ");
  let inner = "";

  for (const e of face.eyes) {
    const t = `translate(${fmt(e.x)} ${fmt(e.y)})` + (e.rotation ? ` rotate(${fmt(e.rotation)})` : "");
    inner += `<g${hook("e")} transform="${t}"><path d="${e.d}" fill="${xmlAttr(face.ink)}"/></g>`;
  }
  for (const h of face.highlights) {
    inner += `<circle${hook("h")} cx="${fmt(h.x)}" cy="${fmt(h.y)}" r="${fmt(h.r)}"`
      + ` fill="${xmlAttr(face.highlightColor)}"`
      + ` opacity="${fmt(face.highlightOpacity * (h.fade == null ? 1 : h.fade))}"/>`;
  }
  if (face.nose) {
    const t = `translate(0 ${fmt(face.nose.y)})` + (face.nose.rotation ? ` rotate(${fmt(face.nose.rotation)})` : "");
    inner += `<g${hook("n")} transform="${t}"><path d="${face.nose.d}" fill="${xmlAttr(face.ink)}"/></g>`;
  }
  if (face.mouth) {
    const t = `translate(0 ${fmt(face.mouth.y)})` + (face.mouth.rotation ? ` rotate(${fmt(face.mouth.rotation)})` : "");
    const paint = face.mouth.stroke
      ? `fill="none" stroke="${xmlAttr(face.ink)}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"`
      : `fill="${xmlAttr(face.ink)}"`;
    inner += `<g${hook("m")} transform="${t}"><path d="${face.mouth.d}" ${paint}/></g>`;
  }

  const op = face.opacity < 1 ? ` opacity="${fmt(face.opacity)}"` : "";
  return `<g${hook("f")} transform="matrix(${m})"${op}>${inner}</g>`;
}

/**
 * Model -> the inner markup of an `<svg>`, plus the attributes it needs.
 *
 * Split out from `toSVG` because the live renderer sets attributes once and
 * then only ever replaces `innerHTML`. Rebuilding the root element on every
 * pointer move is what made the first version of the follow-cursor loop drop
 * frames on a page with a dozen characters on it.
 */
export function toSVGBody(model, options) {
  const opts = options || {};
  const prefix = opts.idPrefix || "ch";
  // `data-ch` markers let the live renderer find the nodes it has to patch
  // every frame without walking the tree by position. Off by default: an
  // exported file should not carry another program's bookmarks.
  const hook = opts.hooks ? (kind) => ` data-ch="${kind}"` : () => "";
  const size = model.viewBox;
  const ids = {
    clip: `${prefix}-clip`,
    shadow: `${prefix}-shadow`,
    faceShadow: `${prefix}-fshadow`,
    bgGrad: `${prefix}-bg`,
  };

  let defs = "";
  if (model.frame.kind !== "none") {
    defs += `<clipPath id="${xmlAttr(ids.clip)}"><path d="${framePath(model.frame.kind, size)}"/></clipPath>`;
  }
  if (model.background.style === "gradient") {
    defs += `<linearGradient id="${xmlAttr(ids.bgGrad)}" x1="0" y1="0" x2="0" y2="1">`
      + `<stop offset="0" stop-color="${xmlAttr(model.background.color)}"/>`
      + `<stop offset="1" stop-color="${xmlAttr(model.background.shade)}"/></linearGradient>`;
  }
  if (model.shadow) defs += dropShadowFilter(ids.shadow, model.shadow);
  if (model.faceShadow) defs += dropShadowFilter(ids.faceShadow, model.faceShadow, 60);

  const bodyFilter = model.shadow ? ` filter="url(#${xmlAttr(ids.shadow)})"` : "";
  const faceFilter = model.faceShadow ? ` filter="url(#${xmlAttr(ids.faceShadow)})"` : "";

  // The face sits outside the body's shadow group on purpose: eyes should not
  // cast the body's drop shadow across the body's own face, which is what a
  // single shared filter produces and what reads as a smudge rather than depth.
  const scene = `<g${bodyFilter}>${bodyMarkup(model, hook)}</g>`
    + `<g${faceFilter}>${faceMarkup(model.face, hook)}</g>`;
  const clipped = model.frame.kind === "none"
    ? backgroundMarkup(model, size, ids) + scene
    : `<g clip-path="url(#${xmlAttr(ids.clip)})">${backgroundMarkup(model, size, ids)}${scene}</g>`;

  return {
    defs: defs ? `<defs>${defs}</defs>` : "",
    content: clipped,
    ids,
  };
}

/**
 * Model -> a complete, standalone SVG document.
 *
 * `role="img"` with a `<title>` rather than a bare graphic: a character is a
 * person's or an agent's identity, and a screen reader landing on an unlabelled
 * `<svg>` announces nothing at all.
 */
export function toSVG(model, options) {
  const opts = options || {};
  const px = opts.size || model.size || model.viewBox;
  const { defs, content } = toSVGBody(model, opts);
  const title = opts.title == null ? model.name : opts.title;
  const label = title ? `<title>${xmlText(title)}</title>` : "";
  const decl = opts.standalone === false ? "" : "";
  // `geometricPrecision` asks the renderer not to trade curve accuracy for
  // speed. It matters most at the sizes this is used at: a 34px avatar is a
  // 256-unit drawing scaled down by seven, where the default heuristics are
  // happy to flatten a curve that is still visibly a curve.
  return `${decl}<svg xmlns="${SVG_NS}" viewBox="0 0 ${model.viewBox} ${model.viewBox}"`
    + ` width="${fmt(px)}" height="${fmt(px)}" shape-rendering="geometricPrecision"`
    + ` role="img"${title ? ` aria-label="${xmlAttr(title)}"` : ""}>`
    + `${label}${defs}${content}</svg>`;
}

/** The SVG as a `data:` URL, for `<img src>` and for CSS `background-image`. */
export function toDataURL(model, options) {
  const svg = toSVG(model, options);
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export { SVG_NS };
