/**
 * SVG -> PNG, in the browser, without a canvas-tainting surprise.
 *
 * Rasterising an SVG through `<img>` and `drawImage` is the only approach that
 * does not need a rendering library shipped alongside it. The two traps:
 *
 *   **Tainting.** A canvas that has drawn an SVG referencing anything external
 *   cannot be read back — `toBlob` throws a SecurityError. The renderer emits
 *   nothing external (no fonts, no images, no `xlink:href`), so the canvas
 *   stays clean. Anything added later that loads a resource breaks PNG export
 *   without breaking the preview, which is a quietly nasty way to find out.
 *
 *   **Missing dimensions.** An SVG with only a `viewBox` and no width/height
 *   rasterises at an implementation-defined size, and the answers differ across
 *   browsers. Export always sets both explicitly.
 */

import { buildRenderModel } from "./project.js";
import { toSVG } from "./svg.js";
import { normalize } from "./schema.js";

/**
 * Rasterise an SVG string to a PNG blob.
 *
 * `size` is the pixel size of the square. A blob URL rather than a data URL for
 * the source image: Safari refuses to load large `data:` URLs into an `<img>`,
 * and a 1024px character with a long path list clears that limit.
 */
export function svgToPNGBlob(svgString, size) {
  return new Promise((resolve, reject) => {
    if (typeof document === "undefined" || typeof Image === "undefined") {
      reject(new Error("PNG export needs a browser"));
      return;
    }
    const px = Math.max(1, Math.round(size || 256));
    const blob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const img = new Image();

    const done = (fn, arg) => { URL.revokeObjectURL(url); fn(arg); };

    img.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = px;
        canvas.height = px;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, px, px);
        canvas.toBlob((out) => {
          if (out) done(resolve, out);
          else done(reject, new Error("PNG export failed"));
        }, "image/png");
      } catch (err) {
        done(reject, err);
      }
    };
    img.onerror = () => done(reject, new Error("PNG export failed"));
    img.src = url;
  });
}

/** A character document straight to a PNG blob at `size` pixels. */
export function toPNGBlob(doc, size) {
  const normalised = normalize(doc);
  const px = size || normalised.scene.camera.size;
  const svg = toSVG(buildRenderModel(normalised), { size: px, idPrefix: "x" });
  return svgToPNGBlob(svg, px);
}

/**
 * Hand a file to the user.
 *
 * Deliberately a function rather than something the caller is left to write:
 * the revoke-after-a-tick dance is the part everyone forgets, and forgetting it
 * leaks the whole blob for the life of the tab — which for a page where a
 * person exports twenty variations is not a rounding error.
 */
export function downloadBlob(blob, filename) {
  if (typeof document === "undefined") return;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Download a character as `.svg`. */
export function downloadSVG(doc, filename, size) {
  const normalised = normalize(doc);
  const svg = toSVG(buildRenderModel(normalised), { size: size || normalised.scene.camera.size, idPrefix: "x" });
  downloadBlob(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }), filename || `${safeName(normalised)}.svg`);
}

/** Download a character as `.png` at `size` pixels. */
export function downloadPNG(doc, size, filename) {
  const normalised = normalize(doc);
  const px = size || 512;
  return toPNGBlob(normalised, px).then((blob) => {
    downloadBlob(blob, filename || `${safeName(normalised)}-${px}.png`);
    return blob;
  });
}

function safeName(doc) {
  const n = String((doc.metadata && doc.metadata.name) || "character")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return n || "character";
}
