/**
 * Render the task list, then click the "from email" link and record the call.
 *
 * Two things have to be true and neither is visible from the source. The link
 * must appear only for a task that actually has a thread — otherwise it is a
 * control that cannot work, which `CLAUDE.md` names as the failure that reads
 * as the app being broken. And clicking it must ask the backend to open the
 * thread the task stored, not a URL built from something else on the row.
 *
 * `loadTasks()` attaches its handlers via `document.querySelectorAll`, not the
 * container's, so the fake document rebuilds them from the HTML the list just
 * wrote — otherwise this harness passes with no handler ever attached, which
 * is the exact blindness `frontend-testing.md` warns about.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {tasks}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const { tasks } = JSON.parse(fs.readFileSync(0, "utf8"));

/** Elements the app asks for by selector, discovered from the HTML it wrote. */
const elementsFrom = (html, attr, key) => {
  const found = [];
  const re = new RegExp(`${attr}="([^"]*)"`, "g");
  let m;
  while ((m = re.exec(html))) {
    found.push({ dataset: { [key]: m[1] }, disabled: false, onclick: null });
  }
  return found;
};

const camel = (attr) =>
  attr.replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());

const makeEl = (tag = "div") => {
  const node = {
    tag, value: "", hidden: false, disabled: false, className: "", style: {},
    dataset: {}, onclick: null, onkeydown: null, textContent: "", children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
    focus() {}, remove() {}, closest: () => null,
    appendChild(c) { this.children.push(c); return c; },
    querySelector(sel) {
      this._q = this._q || {};
      if (!this._q[sel]) this._q[sel] = makeEl();
      return this._q[sel];
    },
    querySelectorAll() { return []; },
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

/** Every handler the page attached, kept so the test can press one. */
const attached = [];

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: (s) => el(s),
  // The real one selects out of the document, which at this point holds what
  // `#taskList` just wrote. Scanning that HTML is the same discovery.
  querySelectorAll: (sel) => {
    const attr = (sel.match(/^\[([a-z-]+)\]$/) || [])[1];
    if (!attr) return [];
    const made = elementsFrom(el("#taskList").innerHTML, attr, camel(attr));
    made.forEach((node) => attached.push({ sel, node }));
    return made;
  },
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
globalThis.fetch = async (url, opts = {}) => {
  calls.push({ url, method: opts.method || "GET",
               body: opts.body ? JSON.parse(opts.body) : null });
  let payload = { ok: true };
  if (url.includes("/api/tasks") && (opts.method || "GET") === "GET") {
    payload = { tasks, stats: { today: 1, overdue: 0, open: tasks.length } };
  }
  return { ok: true, status: 200, json: async () => payload };
};

const toasts = [];
new Function(appSource(path.dirname(APP_JS)) +
  "\nglobalThis.__load = loadTasks;" +
  "\nglobalThis.__toast = (m) => globalThis.__toasts.push(String(m));" +
  "\ntoast = globalThis.__toast;")();
globalThis.__toasts = toasts;

let error = null;
try {
  await globalThis.__load();
} catch (e) {
  error = `${e.constructor.name}: ${e.message}`;
}

const html = el("#taskList").innerHTML;

// Press the origin link, if the page offered one.
let pressed = null;
const link = attached.find((a) => a.sel === "[data-thread]");
if (link) {
  calls.length = 0;
  try {
    await link.node.onclick();
    pressed = "ok";
  } catch (e) {
    pressed = `THREW: ${e.constructor.name}: ${e.message}`;
  }
}

console.log(JSON.stringify({
  error,
  html,
  sourceLinks: attached.filter((a) => a.sel === "[data-thread]").length,
  pressed,
  calls,
  toasts,
}, null, 2));
