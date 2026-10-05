/**
 * What happens to a control the page has drawn but the app cannot yet run.
 *
 * The mic and the waveform in the composer were full-strength buttons with no
 * click handler anywhere — the one thing the product rules here are most
 * explicit about, because a button that does nothing reads as "the app is
 * broken" rather than "that part is not built yet". They are `data-soon` now,
 * and `markSoon()` + a capture-phase listener in core.js turn that one
 * attribute into the dulling, the lock badge, the tooltip and the answer to a
 * tap.
 *
 * Source order cannot show any of this: the attribute is in the markup, the
 * behaviour is in a delegated listener, and the two only meet at runtime. So
 * the controls are read out of the real `index.html`, run through the real
 * `markSoon`, and clicked.
 *
 * argv: <a path inside chitragupta/web/>   stdout: one JSON object
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const WEB = path.dirname(APP_JS);

// ── the controls the shipping page actually marks ──────────────────────────
// Read from index.html rather than listed here: a third button added next
// month has to be covered by this test without anyone remembering to edit it.
const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
const marked = [];
for (const m of html.matchAll(/<(\w+)\b([^>]*\bdata-soon\s*=\s*"([^"]*)"[^>]*)>/g)) {
  const [, tag, attrs, soon] = m;
  marked.push({
    tag,
    soon,
    id: (attrs.match(/\bid="([^"]*)"/) || [, ""])[1],
    // A locked control still has to be reachable, so a keyboard user can find
    // out *why* it is dulled instead of tabbing straight past it.
    tabindex: (attrs.match(/\btabindex="([^"]*)"/) || [, null])[1],
  });
}

// ── a DOM small enough to run core.js in, large enough to click in ─────────
const registry = new Map();
const makeEl = (over = {}) => ({
  innerHTML: "", value: "", hidden: false, disabled: false, title: "",
  textContent: "", style: {}, dataset: {}, _attrs: {}, _classes: [],
  classList: {
    _owner: null,
    add(c) { this._owner._classes.push(c); },
    remove() {}, toggle() {}, contains(c) { return this._owner._classes.includes(c); },
  },
  querySelector: () => makeEl(), querySelectorAll: () => [],
  addEventListener() {}, appendChild() {},
  setAttribute(k, v) { this._attrs[k] = v; if (k === "title") this.title = v; },
  getAttribute(k) { return this._attrs[k] ?? null; },
  focus() {}, remove() {}, closest: () => null,
  ...over,
});
const el = (sel) => {
  if (!registry.has(sel)) {
    const e = makeEl();
    e.classList._owner = e;
    registry.set(sel, e);
  }
  return registry.get(sel);
};

// One element per marked control, keyed by id so the assertions can find it.
const soonEls = marked.map((c) => {
  const e = el(`#${c.id}`);
  e.dataset.soon = c.soon;
  e._attrs["data-soon"] = c.soon;
  e.closest = (sel) => (sel === "[data-soon]" ? e : null);
  return e;
});

let docClick = null;                 // the capture-phase listener core.js installs
globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: (s) => el(s),
  // The only selector this harness is asked for; anything else is a render
  // path we are not exercising and an empty list is the honest answer.
  querySelectorAll: (s) => (s === "[data-soon]" ? soonEls : []),
  getElementById: (i) => el(`#${i}`), createElement: () => makeEl(),
  addEventListener(type, fn, capture) { if (type === "click" && capture) docClick = fn; },
  body: makeEl(), documentElement: makeEl(),
};
globalThis.window = { location: { pathname: "/", href: "/" }, addEventListener() {},
                      matchMedia: () => ({ matches: false, addEventListener() {} }), open() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

new Function(
  appSource(WEB) +
  "\nglobalThis.__markSoon = markSoon;" +
  "\nglobalThis.__icons = applyIcons;"
)();

globalThis.__icons();                // which is where markSoon() is called from

const clicked = soonEls.map((e) => {
  const ev = { target: e, _prevented: false, _stopped: false,
               preventDefault() { this._prevented = true; },
               stopPropagation() { this._stopped = true; } };
  if (docClick) docClick(ev);
  return { prevented: ev._prevented, stopped: ev._stopped, toast: el("#toast").textContent };
});

process.stdout.write(JSON.stringify({
  // Nothing marked at all would pass every assertion below vacuously.
  marked,
  listenerInstalled: !!docClick,
  painted: soonEls.map((e) => ({
    classes: e._classes,
    aria: e.getAttribute("aria-disabled"),
    label: e.getAttribute("aria-label"),
    title: e.title,
  })),
  clicked,
}));
process.exit(0);
