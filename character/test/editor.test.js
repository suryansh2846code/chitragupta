/**
 * The editor and the built bundle actually load.
 *
 * This file exists because of a bug it would have caught in one second and the
 * rest of the suite could not see at all: a backtick inside a CSS comment in
 * `ui/editor-style.js` closed the stylesheet's template literal, so the module
 * was a syntax error. Every other test imports from `src/` only, so all 33 of
 * them passed — while in a browser the *whole bundle* failed to evaluate, which
 * took out the renderer too and left every avatar on the page a blank square.
 *
 * So: import the editor for real, and evaluate the shipped bundle for real.
 * Neither test asserts anything clever. Their entire job is "this parses and
 * the public names are there", which is the failure mode that hurts most and
 * the one a DOM-free suite is blindest to.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const BUNDLE = join(here, "..", "dist", "character.global.js");

test("the editor module imports without a DOM", async () => {
  // Nothing at module scope may touch `document` — a host that imports the
  // editor at the top of a file and mounts it later, or renders on a server,
  // must not crash on the import alone.
  const mod = await import("../ui/editor.js");
  assert.equal(typeof mod.mountEditor, "function");
  assert.ok(Array.isArray(mod.FACE_PRESETS) && mod.FACE_PRESETS.length > 0);
});

test("the stylesheet is one intact template literal", async () => {
  const { EDITOR_CSS } = await import("../ui/editor-style.js");
  assert.equal(typeof EDITOR_CSS, "string");
  assert.ok(EDITOR_CSS.length > 2000, "the stylesheet looks truncated");
  assert.ok(EDITOR_CSS.includes(".ce-pane"), "a rule went missing");
  // Balanced braces is a cheap proof that no part of it was parsed as code.
  const open = (EDITOR_CSS.match(/{/g) || []).length;
  const close = (EDITOR_CSS.match(/}/g) || []).length;
  assert.equal(open, close, "unbalanced braces in the stylesheet");
});

/**
 * A DOM with just enough surface for the panel to build itself.
 *
 * Not a browser and not trying to be. It exists so `mountEditor` can be *run*
 * rather than merely imported — which is the difference between catching a
 * missing export and catching the temporal-dead-zone `ReferenceError` that
 * shipped here: `buildActions()` read a `const` declared below it, which
 * `node --check` passes, the module import passes, and the browser throws on.
 */
function fakeDOM() {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const matches = (el, sel) => (sel.startsWith(".")
    ? String(el.className || "").split(/\s+/).includes(sel.slice(1))
    : String(el.tagName || "").toLowerCase() === sel.toLowerCase());
  const findAll = (root, sel, out = []) => {
    for (const c of root.children || []) {
      if (c && c.tagName && matches(c, sel)) out.push(c);
      if (c && c.children) findAll(c, sel, out);
    }
    return out;
  };
  const find = (root, sel) => findAll(root, sel)[0] || null;
  const make = (tag, ns) => {
    const el = {
      tagName: String(tag).toUpperCase(), namespaceURI: ns || null,
      children: [], attrs: {}, className: "", value: "", disabled: false, hidden: false,
      innerHTML: "", textContent: "", ownerDocument: null,
      style: { setProperty() {}, removeProperty() {} },
      dataset: {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      get firstChild() { return el.children[0] || null; },
      appendChild(c) { el.children.push(c); return c; },
      append(...cs) { for (const c of cs) if (c) el.children.push(c); },
      insertBefore(c) { el.children.unshift(c); return c; },
      removeChild(c) { el.children = el.children.filter((x) => x !== c); return c; },
      setAttribute(k, v) { el.attrs[k] = String(v); },
      getAttribute(k) { return el.attrs[k] ?? null; },
      removeAttribute(k) { delete el.attrs[k]; },
      addEventListener() {}, removeEventListener() {}, focus() {}, remove() {},
      closest: () => null,
      // A real class-selector search over real children, because the panel's
      // sync closures reach for things they built (`.ce-switch` inside the
      // follow toggle). Returning null there is a crash; returning a fresh stub
      // would pass while proving nothing.
      querySelector: (sel) => find(el, sel),
      querySelectorAll: (sel) => findAll(el, sel),
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
    };
    return el;
  };
  const doc = {
    createElement: (t) => make(t),
    createElementNS: (ns, t) => make(t, ns),
    createTextNode: (t) => ({ nodeValue: String(t) }),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    head: make("head"),
    documentElement: make("html"),
    activeElement: null,
  };
  return { doc, SVG_NS, make };
}

test("the editor mounts, builds every tab, and accepts an edit", async () => {
  const { doc, make } = fakeDOM();
  const previous = globalThis.document;
  globalThis.document = doc;
  try {
    const { mountEditor } = await import("../ui/editor.js");
    const { generateScene } = await import("../src/generate.js");

    const container = make("div");
    const seen = [];
    // `storageKey: null` keeps `localStorage` out of it; everything else runs,
    // including the preview — which is where the bug was, because the actions
    // row is only built when there is a preview to put it under.
    const editor = mountEditor(container, {
      document: generateScene("editor-smoke"),
      storageKey: null,
      onChange: (d) => seen.push(d),
    });

    assert.ok(container.children.length > 0, "the editor mounted nothing");
    assert.equal(typeof editor.getDocument().scene.view.yaw, "number");

    // An edit flows all the way back out, so the sync closures ran too.
    const next = editor.getDocument();
    next.scene.appearance.paletteId = "mint";
    editor.setDocument(next);
    assert.equal(seen.length, 1);
    assert.equal(seen[0].scene.appearance.paletteId, "mint");

    editor.destroy();
  } finally {
    globalThis.document = previous;
  }
});

test("the built bundle evaluates and exposes the public surface", () => {
  if (!existsSync(BUNDLE)) {
    assert.fail("dist/character.global.js is missing — run scripts/build.js");
  }
  const require_ = createRequire(import.meta.url);
  const Character = require_(BUNDLE);

  for (const name of ["createCharacter", "renderToString", "generateScene", "mountEditor",
    "normalize", "encode", "decode", "applyPreset", "PALETTES", "PRESETS"]) {
    assert.ok(Character[name], `the bundle does not export ${name}`);
  }
  // And it works, not just exists.
  const svg = Character.renderToString(Character.generateScene("bundle"), { size: 64 });
  assert.ok(svg.startsWith("<svg") && svg.includes("<path"), "the bundled renderer drew nothing");
});
