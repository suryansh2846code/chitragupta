/**
 * Execute the real mark/tint resolution from `connectors.js` and report it.
 *
 * Not a re-implementation: the colour floor is arithmetic, and a Python copy
 * of it would agree with itself forever while the shipped one drifted. Reads
 * a list of ids on stdin, writes `{id: {tint, kind}}` on stdout.
 */
import fs from "fs";

const src = fs.readFileSync(process.argv[2], "utf8");

function objectAfter(name) {
  const i = src.indexOf(name);
  if (i === -1) throw new Error(`${name} is gone from connectors.js`);
  let depth = 0;
  const open = src.indexOf("{", i);
  for (let k = open; k < src.length; k++) {
    if (src[k] === "{") depth++;
    if (src[k] === "}" && !--depth) return src.slice(open, k + 1);
  }
  throw new Error(`${name} is unbalanced`);
}

function functionThrough(startMarker, endMarker) {
  const a = src.indexOf(startMarker);
  const b = src.indexOf("\n}", src.indexOf(endMarker)) + 2;
  return src.slice(a, b);
}

const BRAND_MARKS = eval("(" + objectAfter("const BRAND_MARKS =") + ")");
const CONNECTOR_ICONS = eval("(" + objectAfter("const CONNECTOR_ICONS =") + ")");
const CONNECTOR_TINT = eval("(" + objectAfter("const CONNECTOR_TINT =") + ")");

const connectorHue = new Function(
  `${functionThrough("function connectorHue", "function connectorHue")}\nreturn connectorHue;`)();
const markTint = new Function("BRAND_MARKS", "CONNECTOR_TINT", "connectorHue",
  `const connectorTint=(n)=>CONNECTOR_TINT[n]||"";
   ${functionThrough("const MARK_MIN_LUMA", "function markTint")}
   return markTint;`)(BRAND_MARKS, CONNECTOR_TINT, connectorHue);

function luma(css) {
  let rgb;
  const hex = /^#?([0-9a-f]{6})$/i.exec(String(css).trim());
  if (hex) {
    const n = parseInt(hex[1], 16);
    rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  } else {
    const m = /rgb\((\d+),\s*(\d+),\s*(\d+)\)/.exec(css);
    if (!m) return null;                       // an hsl() fallback; bright by construction
    rgb = [+m[1], +m[2], +m[3]];
  }
  const [r, g, b] = rgb.map((v) => v / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

const ids = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};
for (const id of ids) {
  out[id] = {
    tint: markTint(id),
    luma: luma(markTint(id)),
    kind: BRAND_MARKS[id] ? "vendor" : CONNECTOR_ICONS[id] ? "drawn" : "monogram",
  };
}
process.stdout.write(JSON.stringify(out));
