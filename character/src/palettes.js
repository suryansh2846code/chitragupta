/**
 * Colour is a palette, never a free-for-all.
 *
 * A character is four colours that have to work together — body, the outline
 * around it, the face marks on top of it, and the card behind it. Exposed as
 * four independent colour pickers, almost every combination a person lands on
 * is unreadable: a mid-grey face on a mid-grey body, or an outline that
 * disappears into the background. So the editor offers palettes first, and only
 * then lets individual colours be overridden.
 *
 * Every palette here holds face-on-body contrast well above the point where the
 * eyes stop reading at 28px, which is the smallest anything draws one.
 */

export const PALETTES = [
  { id: "coral",   name: "Coral",   body: "#dd8468", shade: "#c06a50", outline: "#ffffff", face: "#241512", background: "#ffc1a9" },
  { id: "cloud",   name: "Cloud",   body: "#e4e4e6", shade: "#c5c6ca", outline: "#26374d", face: "#101317", background: "#74a5d8" },
  { id: "bone",    name: "Bone",    body: "#eae7dd", shade: "#cfcabb", outline: "#1a1a1a", face: "#111111", background: "#1c1c1e" },
  { id: "clay",    name: "Clay",    body: "#d7d2c4", shade: "#b8b2a1", outline: "#3b2a1c", face: "#26211a", background: "#e08b3e" },
  { id: "honey",   name: "Honey",   body: "#e3a45c", shade: "#c8873f", outline: "#3a2412", face: "#2a1a0c", background: "#e7c98a" },
  { id: "moss",    name: "Moss",    body: "#7fb08a", shade: "#5f9070", outline: "#1d2f22", face: "#16241a", background: "#cfe3cd" },
  { id: "indigo",  name: "Indigo",  body: "#8fa2e6", shade: "#6d7fc6", outline: "#1b2044", face: "#141834", background: "#dfe3ff" },
  { id: "plum",    name: "Plum",    body: "#b98ad0", shade: "#9a6cb2", outline: "#2c1738", face: "#21112b", background: "#ecd9f4" },
  { id: "slate",   name: "Slate",   body: "#9aa4b2", shade: "#7b8593", outline: "#1b1f27", face: "#12151b", background: "#2b313b" },
  { id: "ember",   name: "Ember",   body: "#e2695c", shade: "#c14b41", outline: "#2c0f0c", face: "#210b09", background: "#2a1512" },
  { id: "mint",    name: "Mint",    body: "#7fd8c0", shade: "#5cb8a0", outline: "#11332c", face: "#0d2721", background: "#e3f7f1" },
  { id: "sand",    name: "Sand",    body: "#dcc9a8", shade: "#bfa985", outline: "#3a2f1e", face: "#2a2216", background: "#6c7f5e" },
  { id: "ink",     name: "Ink",     body: "#3a3f4b", shade: "#2a2e38", outline: "#e8eaf0", face: "#e8eaf0", background: "#14161b" },
  { id: "blossom", name: "Blossom", body: "#f0a7b4", shade: "#d08592", outline: "#3a1a22", face: "#2b1017", background: "#fbe4e8" },
  { id: "sky",     name: "Sky",     body: "#8ec9e8", shade: "#6ba8c9", outline: "#123243", face: "#0d2634", background: "#e2f2fa" },
  { id: "gold",    name: "Gold",    body: "#d8b25f", shade: "#b89341", outline: "#33260d", face: "#241b08", background: "#191510" },
];

const BY_ID = new Map(PALETTES.map((p) => [p.id, p]));

export const PALETTE_IDS = PALETTES.map((p) => p.id);

export function palette(id) {
  return BY_ID.get(id) || PALETTES[0];
}

export function hexToRgb(hex) {
  let h = String(hex == null ? "" : hex).replace("#", "").trim();
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  if (h.length !== 6) return [0, 0, 0];
  const n = parseInt(h, 16);
  if (!Number.isFinite(n)) return [0, 0, 0];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rgbToHex(r, g, b) {
  const c = (v) => {
    const n = Math.max(0, Math.min(255, Math.round(v)));
    return n.toString(16).padStart(2, "0");
  };
  return `#${c(r)}${c(g)}${c(b)}`;
}

/** Mix two hex colours. `t` of 0 is `a`, 1 is `b`. */
export function mix(a, b, t) {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  return rgbToHex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t);
}

/**
 * Apply brightness / saturation / tint to a hex colour.
 *
 * This is the colour-grade pass, and it runs here rather than as an SVG filter
 * on purpose: an exported SVG that leans on `feColorMatrix` renders differently
 * in Figma, in Safari's own thumbnailer, and in anything that rasterises
 * without filter support. Baking the grade into the fill values means the file
 * a user downloads is the image they were looking at.
 */
export function grade(hex, g) {
  if (!g) return hex;
  const { brightness = 1, saturation = 1, tintAmount = 0, tintR = 0, tintG = 0, tintB = 0 } = g;
  if (brightness === 1 && saturation === 1 && !tintAmount) return hex;
  let [r, gg, b] = hexToRgb(hex);
  const lum = 0.2126 * r + 0.7152 * gg + 0.0722 * b;
  r = lum + (r - lum) * saturation;
  gg = lum + (gg - lum) * saturation;
  b = lum + (b - lum) * saturation;
  r *= brightness; gg *= brightness; b *= brightness;
  if (tintAmount) {
    const t = tintAmount / 100;
    r += (tintR - r) * t;
    gg += (tintG - gg) * t;
    b += (tintB - b) * t;
  }
  return rgbToHex(r, gg, b);
}

/**
 * Relative luminance, for deciding whether a mark on this colour should be the
 * dark one or the light one. The formula the WCAG contrast ratio uses.
 */
export function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a, b) {
  const l1 = luminance(a), l2 = luminance(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

/**
 * The readable ink for marks drawn on `bg`.
 *
 * Used as the last line of defence for face features: whatever palette or
 * custom body colour a person lands on, eyes that vanish into the head are a
 * character with no face, which reads as a failed render rather than a choice.
 */
export function readableInk(bg, dark = "#14161b", light = "#f6f7fa") {
  return contrast(bg, dark) >= contrast(bg, light) ? dark : light;
}
