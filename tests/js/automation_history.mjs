/**
 * Render an automation's run history and open one run.
 *
 * Two claims, and neither is visible from reading the HTML. **A run that
 * correctly declined must not read as a failure** — `blocked` is the system
 * working, and a user who sees enough red badges stops reading them. And **an
 * injection attempt must be surfaced to the user**, not only defused silently:
 * the fence makes the attack fail, this is what makes it visible.
 *
 * The whole page is evaluated, exactly what the browser loads, because a
 * harness that evaluates one file in isolation passes while the real page is
 * broken by something another script declared first.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {automation, run, steps}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const { automation, run, steps } = JSON.parse(fs.readFileSync(0, "utf8"));

const makeEl = (tag = "div") => {
  const node = {
    tag, textContent: "", hidden: false, className: "", value: "",
    dataset: {}, onclick: null, children: [], style: {},
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); }, toggle() {},
    },
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
    focus() {}, remove() {}, closest: () => null,
    appendChild(child) { node.children.push(child); return child; },
    insertBefore(child) { node.children.unshift(child); return child; },
    querySelector: (sel) => el(`${tag}>${sel}`),
    /** Buttons the app wrote, read back out of its own HTML — so a press
     *  reaches the object the app actually wired a handler onto. Returning []
     *  here is how a harness passes while nothing was ever bound. */
    querySelectorAll(sel) {
      const attr = (sel.match(/^\[([a-z-]+)\]$/) || [])[1];
      if (!attr) return [];
      const key = attr.replace(/^data-/, "")
        .replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      const out = [];
      const re = new RegExp(`${attr}="([^"]*)"`, "g");
      let m;
      while ((m = re.exec(node.innerHTML))) {
        const button = { dataset: { [key]: m[1] }, onclick: null,
                         textContent: "", className: "" };
        out.push(button);
        (node.pressable = node.pressable || []).push(button);
      }
      return out;
    },
    get firstChild() { return node.children[0] || null; },
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

globalThis.MutationObserver = class {
  observe() {} disconnect() {} takeRecords() { return []; }
};
globalThis.document = {
  querySelector: (s) => el(s), querySelectorAll: () => [],
  getElementById: (id) => el(`#${id}`), createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {},
};
const store = {};
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; },
};
globalThis.sessionStorage = { getItem: () => null, setItem() {} };

/** Every request the page made, in order. */
const calls = [];
globalThis.fetch = async (url, opts = {}) => {
  calls.push(String(url));
  let payload = {};
  if (/\/runs\//.test(url)) payload = { run, steps };
  else if (/\/api\/automations\/[^/]+$/.test(url)) payload = automation;
  else if (String(url).endsWith("/api/automations")) {
    payload = { automations: [automation] };
  }
  return { ok: true, status: 200, json: async () => payload };
};

new Function(appSource(path.dirname(APP_JS))
  + "\nglobalThis.__history = automationHistory;"
  + "\nglobalThis.__runDetail = runDetail;")();

let error = null;
try {
  await globalThis.__history(automation.id);
  await globalThis.__runDetail(automation.id, run.id);
} catch (e) {
  error = `${e && e.constructor && e.constructor.name}: ${e && e.message}`;
}

process.stdout.write(JSON.stringify({
  error,
  calls,
  historyHtml: el("#automationDetail").innerHTML,
  historyHidden: el("#automationDetail").hidden,
  runHtml: el("#runDetail").innerHTML,
}));
