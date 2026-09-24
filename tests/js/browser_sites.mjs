/**
 * Render the "Websites agents can read" panel, then use it.
 *
 * Executes the render and both handlers — adding a site and removing one —
 * because the render alone would pass with no handler attached at all, which is
 * the mistake `frontend-testing.md` was written about.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {status, addError}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const { status, addError } = JSON.parse(fs.readFileSync(0, "utf8"));

const buttonsFrom = (html, attr, key) => {
  const found = [];
  const re = new RegExp(`${attr}="([^"]*)"`, "g");
  let m;
  while ((m = re.exec(html))) {
    found.push({ dataset: { [key]: m[1] }, disabled: false, onclick: null });
  }
  return found;
};

const makeEl = (tag = "div") => {
  const node = {
    tag, value: "", hidden: false, disabled: false, className: "", style: {},
    dataset: {}, onclick: null, onkeydown: null, children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
    focus() {}, remove() {}, closest: () => null,
    appendChild(c) { this.children.push(c); return c; },
    querySelector() { return makeEl(); },
    querySelectorAll(sel) {
      const attr = (sel.match(/^\[([a-z-]+)\]$/) || [])[1];
      if (!attr) return [];
      const key = attr.replace(/^data-/, "");
      const made = buttonsFrom(this.innerHTML, attr, key);
      made.forEach((b) => kept.push({ sel, b }));
      return made;
    },
  };
  let html = "", text = "";
  Object.defineProperty(node, "innerHTML", {
    get: () => html, set(v) { html = String(v); },
  });
  Object.defineProperty(node, "textContent", {
    get: () => text, set(v) { text = String(v); },
  });
  return node;
};

const kept = [];
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
globalThis.sessionStorage = { getItem: () => null, setItem: () => {} };
globalThis.confirm = () => true;
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => {};

const calls = [];
globalThis.fetch = async (url, opts = {}) => {
  const method = opts.method || "GET";
  calls.push({ url, method, body: opts.body ? JSON.parse(opts.body) : null });
  if (addError && url === "/api/browser/sites" && method === "POST") {
    return { ok: false, status: 400, json: async () => ({ detail: addError }) };
  }
  return { ok: true, status: 200, json: async () => status };
};

const toasts = [];
new Function(appSource(path.dirname(APP_JS)) +
  "\nglobalThis.__load = loadBrowserSites;" +
  "\ntoast = (m) => globalThis.__toasts.push(String(m));")();
globalThis.__toasts = toasts;

let error = null;
try {
  await globalThis.__load();
} catch (e) {
  error = `${e.constructor.name}: ${e.message}`;
}

const list = el("#webSites"), setup = el("#webSetup"), state = el("#webSetupState");
const rendered = {
  list: list.innerHTML,
  setupHidden: setup.hidden,
  setupText: setup.textContent,
  stateHidden: state.hidden,
  stateText: state.textContent,
};

// Remove the first site, if one rendered a button.
let removed = null;
const del = kept.find((k) => k.sel === "[data-webdel]");
if (del) {
  calls.length = 0;
  try { await del.b.onclick(); removed = "ok"; }
  catch (e) { removed = `THREW: ${e.message}`; }
}

// Turn "agents may change things here" on, through the row's own button.
let acted = null;
let actCalls = [];
const act = kept.find((k) => k.sel === "[data-webact]");
if (act) {
  const mark = calls.length;
  try { await act.b.onclick(); acted = "ok"; }
  catch (e) { acted = `THREW: ${e.message}`; }
  actCalls = calls.slice(mark);
}

// Add a site through the input and button the page wired at load.
const before = calls.length;
el("#webInput").value = "payroll.example.com";
let added = null;
if (typeof el("#webAdd").onclick === "function") {
  try { await el("#webAdd").onclick(); added = "ok"; }
  catch (e) { added = `THREW: ${e.message}`; }
}

console.log(JSON.stringify({
  error, rendered, removed, added, acted, actCalls,
  addCalls: calls.slice(before),
  removeCalls: del ? calls.slice(0, before) : [],
  errText: el("#webErr").textContent,
  errHidden: el("#webErr").hidden,
  inputAfter: el("#webInput").value,
  toasts,
}, null, 2));
