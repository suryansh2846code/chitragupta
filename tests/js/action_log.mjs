/**
 * Render the action log, then press Undo and record every call.
 *
 * Two claims, and neither is visible from the HTML alone. **Undo appears only
 * where the server said an inverse exists and has not already been used** — a
 * sent email has none, and a button that quietly does nothing is the exact
 * failure this whole column is meant to prevent. And **pressing it must send
 * the log entry's own id**, because the inverse needs the result the service
 * returned, which the row does not otherwise hold.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {entries, undoResult}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const { entries, undoResult } = JSON.parse(fs.readFileSync(0, "utf8"));

/** Buttons the app asks for by selector, discovered from the HTML it wrote.
 *  Returning [] here is how a harness passes while no handler was ever
 *  attached — the mistake `frontend-testing.md` warns about. */
const buttonsFrom = (html, attr, key) => {
  const found = [];
  const re = new RegExp(`${attr}="([^"]*)"`, "g");
  let m;
  while ((m = re.exec(html))) {
    found.push({ dataset: { [key]: m[1] }, disabled: false, onclick: null,
                 textContent: "" });
  }
  return found;
};

const makeEl = (tag = "div") => {
  const node = {
    tag, value: "", hidden: false, disabled: false, className: "", style: {},
    dataset: {}, onclick: null, textContent: "", children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
    focus() {}, remove() {}, closest: () => null,
    appendChild(c) { this.children.push(c); return c; },
    querySelector(sel) {
      this._q = this._q || {};
      if (!this._q[sel]) this._q[sel] = makeEl();
      return this._q[sel];
    },
    querySelectorAll(sel) {
      const attr = (sel.match(/^\[([a-z-]+)\]$/) || [])[1];
      if (!attr) return [];
      const key = attr.replace(/^data-/, "");
      const made = buttonsFrom(this.innerHTML, attr, key);
      // Kept, so the test can press the very button the app wired up rather
      // than a fresh object that never received a handler.
      made.forEach((b) => (this._pressable = this._pressable || []).push(b));
      return made;
    },
  };
  let html = "";
  Object.defineProperty(node, "innerHTML", {
    get: () => html,
    set(v) { html = String(v); if (v === "") node.children.length = 0; },
  });
  return node;
};

const registry = new Map();
const el = (sel) => {
  if (!registry.has(sel)) registry.set(sel, makeEl());
  return registry.get(sel);
};
globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: (s) => el(s), querySelectorAll: () => [],
  getElementById: (id) => el(`#${id}`), createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = { location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {} };
const store = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };

/** Every request the page makes, in order. */
const calls = [];
let logReads = 0;
globalThis.fetch = async (url, opts = {}) => {
  calls.push({ url, method: opts.method || "GET",
               body: opts.body ? JSON.parse(opts.body) : null });
  let payload = { ok: true };
  if (url.includes("/api/actions/log")) {
    // The reload after an undo comes back with the row already marked, which
    // is what the real server would say — and it stops a re-render loop.
    logReads += 1;
    payload = { entries: logReads > 1
      ? entries.map((e) => (e.reversible ? { ...e, undone: true } : e))
      : entries };
  } else if (url.includes("/api/actions/undo")) {
    payload = undoResult || { ok: true, detail: "Undone" };
  }
  return { ok: true, status: 200, json: async () => payload };
};

const toasts = [];
new Function(appSource(path.dirname(APP_JS)) +
  "\nglobalThis.__load = loadActionLog;" +
  "\nglobalThis.__toast = (m) => globalThis.__toasts.push(String(m));" +
  "\ntoast = globalThis.__toast;" +
  // The undo handler refreshes these two afterwards; they would otherwise
  // reach the fake fetch and answer with the wrong shape.
  "\nloadReminders = async () => {};" +
  "\nloadRoutines = async () => {};")();
globalThis.__toasts = toasts;

let error = null;
try {
  await globalThis.__load();
} catch (e) {
  error = `${e.constructor.name}: ${e.message}`;
}

const box = el("#actionLog");
const html = box.innerHTML;

// Press it. The app attached its handler to the objects ITS querySelectorAll
// handed back, so the press has to go through those same objects.
const pressable = (box._pressable || []).filter((b) => b.onclick);
let pressed = null;
if (pressable.length) {
  const button = pressable[0];
  pressed = { id: button.dataset.undo, label: button.textContent };
  try {
    await button.onclick();
  } catch (e) {
    pressed.error = `${e.constructor.name}: ${e.message}`;
  }
}

console.log(JSON.stringify({
  error,
  html,
  text: html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim(),
  //: Rows carry their state as a data attribute, which is what the colour and
  //: the strike-through hang off — asserted rather than the CSS.
  states: [...html.matchAll(/data-state="([a-z]+)"/g)].map((m) => m[1]),
  undoButtons: [...html.matchAll(/data-undo="([^"]*)"/g)].map((m) => m[1]),
  emptyHidden: el("#actionLogEmpty").hidden,
  count: el("#actionLogCount").textContent,
  pressed,
  calls,
  toasts,
}, null, 2));
