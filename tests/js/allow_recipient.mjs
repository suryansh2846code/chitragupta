/**
 * Render the approvals queue, then press "Always allow" and record every call.
 *
 * Two things have to be true and neither is visible from the HTML alone. The
 * button must appear only when the server said an allow-list could clear the
 * action — `blocked` non-empty — because a button that cannot work reads as the
 * app being broken. And pressing it must grant *the addresses the server named*,
 * not something recovered from the sentence next to them, then run the action
 * that was waiting. So the handler is invoked and the requests are captured.
 *
 * The row also has to SHOW what it is asking about. It carried one summary line
 * while the whole proposal sat in `params` on the same response, so the user
 * approved an email without being shown what it said — see
 * `test_frontend_approval_fidelity.py`. `catalog` seeds `ACTION_CATALOG`, which
 * the app fills at boot; without it `actionFace` has no fields to draw from and
 * every row degrades to exactly the line this is here to replace.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {rows, catalog}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const { rows, catalog } = JSON.parse(fs.readFileSync(0, "utf8"));

/** Buttons the app asks for by selector, discovered from the HTML it wrote. */
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
    // The real thing selects out of what `innerHTML` just wrote, so the stub
    // does too. Returning [] here is how a harness passes while no handler was
    // ever attached — the mistake `frontend-testing.md` warns about.
    querySelectorAll(sel) {
      const attr = (sel.match(/^\[([a-z-]+)\]$/) || [])[1];
      if (!attr) return [];
      const key = attr.replace(/^data-/, "");
      return buttonsFrom(this.innerHTML, attr, key);
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
globalThis.fetch = async (url, opts = {}) => {
  calls.push({ url, method: opts.method || "GET",
               body: opts.body ? JSON.parse(opts.body) : null });
  let payload = { ok: true };
  if (url.includes("/api/agents/approvals") && (opts.method || "GET") === "GET") {
    // Second and later polls come back empty, so `loadApprovals()` re-running
    // after a decision cannot loop forever.
    payload = { approvals: calls.filter((c) => c.url.includes("approvals")
                                          && c.method === "GET").length > 1
                  ? [] : rows };
  } else if (url.includes("/api/agents/permissions")) {
    payload = { kind: "email_recipient", value: "x" };
  }
  return { ok: true, status: 200, json: async () => payload };
};

const toasts = [];
new Function(appSource(path.dirname(APP_JS)) +
  "\nglobalThis.__load = loadApprovals;" +
  "\nglobalThis.__setCatalog = (c) => { ACTION_CATALOG = c; };" +
  "\nglobalThis.__toast = (m) => globalThis.__toasts.push(String(m));" +
  "\ntoast = globalThis.__toast;")();
globalThis.__toasts = toasts;
if (catalog) globalThis.__setCatalog(catalog);

let error = null;
try {
  await globalThis.__load();
} catch (e) {
  error = `${e.constructor.name}: ${e.message}`;
}

const box = el("#approvals");
const html = box.innerHTML;

// Press it. `querySelectorAll` rebuilds the buttons from the HTML, so this is
// the same discovery the app did — but the app attached its handler to *its*
// objects, so the click has to go through a fresh load with a captured handler.
let pressed = null;
const allowButtons = box.querySelectorAll("[data-apralw]");
if (allowButtons.length) {
  // Re-attach by re-running the loader against a box whose querySelectorAll
  // hands back objects we keep a reference to.
  const keep = [];
  box.querySelectorAll = function (sel) {
    const attr = (sel.match(/^\[([a-z-]+)\]$/) || [])[1];
    if (!attr) return [];
    const key = attr.replace(/^data-/, "");
    const made = buttonsFrom(this.innerHTML, attr, key);
    made.forEach((b) => keep.push({ sel, b }));
    return made;
  };
  calls.length = 0;
  await globalThis.__load();
  const target = keep.find((k) => k.sel === "[data-apralw]");
  if (target) {
    try {
      await target.b.onclick();
      pressed = "ok";
    } catch (e) {
      pressed = `THREW: ${e.constructor.name}: ${e.message}`;
    }
  }
}

console.log(JSON.stringify({
  error,
  html,
  allowButtons: allowButtons.length,
  pressed,
  calls,
  toasts,
}, null, 2));
