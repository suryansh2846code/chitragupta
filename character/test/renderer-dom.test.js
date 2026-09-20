/**
 * A change to the document has to reach the screen.
 *
 * Obvious, and it was broken. The live renderer has a fast path that writes
 * attributes onto existing nodes instead of rebuilding the subtree, which is
 * what keeps a rail of characters at 60fps while they follow the cursor. But
 * that path only writes what a *pose* can change — and it was running for every
 * update, including edits. So choosing a different mouth in the editor updated
 * the document, re-projected the character, and then wrote only the parts and
 * the eyes: the old mouth stayed on screen. Every control "worked" and nothing
 * moved.
 *
 * These tests drive `createCharacter` against a fake DOM and assert on the
 * markup it actually produced. They are deliberately about *visible output*
 * rather than about which internal path ran, because the bug was invisible to
 * anything that asked the renderer what it thought it had done.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { createCharacter } from "../src/renderer.js";
import { normalize, defaultScene } from "../src/schema.js";
import { MOUTH_SHAPES, NOSE_SHAPES } from "../src/schema.js";

const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, ns) {
  const node = {
    tagName: String(tag).toUpperCase(), namespaceURI: ns || null,
    children: [], attrs: {}, innerHTML: "", textContent: "", style: {},
    appendChild(c) { node.children.push(c); return c; },
    insertBefore(c) { node.children.unshift(c); return c; },
    removeChild(c) { node.children = node.children.filter((x) => x !== c); },
    setAttribute(k, v) { node.attrs[k] = String(v); },
    getAttribute(k) { return node.attrs[k] ?? null; },
    removeAttribute(k) { delete node.attrs[k]; },
    addEventListener() {}, removeEventListener() {},
    // The renderer looks up its patch targets here. Returning nothing is
    // honest for a DOM that does not parse `innerHTML` — and it means these
    // tests can only pass if the *markup* is right, never because a patch
    // happened to write the attribute they check.
    querySelectorAll: () => [],
  };
  return node;
}

function withDOM(fn) {
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (t) => el(t),
    createElementNS: (ns, t) => el(t, ns),
    querySelector: () => null,
    addEventListener() {},
    head: el("head"), documentElement: el("html"),
  };
  try { return fn(); } finally { globalThis.document = previous; }
}

function mount(doc) {
  const host = el("div");
  const instance = createCharacter(host, doc);
  return { instance, html: () => instance.el.innerHTML };
}

test("changing the mouth shape changes what is drawn", () => {
  withDOM(() => {
    const doc = normalize(defaultScene());
    doc.scene.face.mouthEnabled = true;
    doc.scene.face.mouthShape = "curve";
    const { instance, html } = mount(doc);

    const seen = new Set([html()]);
    for (const shape of MOUTH_SHAPES.filter((m) => m !== "none")) {
      const next = instance.document;
      next.scene.face.mouthShape = shape;
      instance.setDocument(next);
      const markup = html();
      assert.ok(markup.length > 0, `${shape}: nothing was drawn`);
      seen.add(markup);
    }
    // Four mouth shapes, four different drawings.
    assert.ok(seen.size >= 4, `only ${seen.size} distinct renders across every mouth shape`);
    instance.destroy();
  });
});

test("turning the mouth on and off changes what is drawn", () => {
  withDOM(() => {
    const doc = normalize(defaultScene());
    doc.scene.face.mouthEnabled = false;
    const { instance, html } = mount(doc);
    const without = html();

    const on = instance.document;
    on.scene.face.mouthEnabled = true;
    on.scene.face.mouthShape = "curve";
    instance.setDocument(on);
    assert.notEqual(html(), without, "switching the mouth on drew nothing new");

    instance.setDocument(doc);
    assert.equal(html(), without, "switching the mouth off did not remove it");
    instance.destroy();
  });
});

test("changing the nose shape changes what is drawn", () => {
  withDOM(() => {
    const doc = normalize(defaultScene());
    doc.scene.face.noseEnabled = true;
    const { instance, html } = mount(doc);

    const seen = new Set();
    for (const shape of NOSE_SHAPES) {
      const next = instance.document;
      next.scene.face.noseShape = shape;
      instance.setDocument(next);
      seen.add(html());
    }
    assert.equal(seen.size, NOSE_SHAPES.length, `${NOSE_SHAPES.length} nose shapes gave ${seen.size} renders`);
    instance.destroy();
  });
});

test("changing the palette changes what is drawn", () => {
  withDOM(() => {
    const doc = normalize(defaultScene());
    const { instance, html } = mount(doc);
    const before = html();

    const next = instance.document;
    next.scene.appearance.paletteId = "mint";
    instance.setDocument(next);
    assert.notEqual(html(), before, "the palette change never reached the markup");
    instance.destroy();
  });
});

test("a pose can be applied without a document edit", () => {
  withDOM(() => {
    const { instance } = mount(normalize(defaultScene()));
    // The patch path finds no nodes in this DOM, so it falls back to a rebuild
    // — what matters is that it does not throw and still produces a drawing.
    assert.doesNotThrow(() => instance.setPose({ yaw: 0.3, pitch: -0.2 }));
    assert.ok(instance.el.innerHTML.includes("<path"));
    instance.destroy();
  });
});
