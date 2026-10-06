/**
 * Render the version row for real and press its buttons.
 *
 * The failures this catches are the ones `node --check` cannot: a Check button
 * drawn on a build with no feed (so pressing it can only fail), a disclosure
 * sentence that stops matching what the request actually sends, and a toggle
 * that reports the state it wanted rather than the state the server confirmed.
 *
 * Same shape as `backup_screen.mjs`: ids resolve out of the markup the renderer
 * produced, and `fetch` is faked rather than `api`, so the real request
 * encoding and the real rejection path are both exercised.
 *
 * Reads a scenario as JSON on stdin, writes one JSON result to stdout.
 */
import fs from "node:fs";

import { appSource } from "./_app_source.mjs";

const WEB_DIR = process.argv[2];
const scenario = JSON.parse(fs.readFileSync(0, "utf8"));

const requests = [];
const toasts = [];
const registry = new Map();

const ID_IN_HTML = /\bid="([^"]+)"/g;

function makeEl(id = "") {
  const node = {
    id, _html: "", _text: "",
    value: "", hidden: false, disabled: false, title: "",
    style: {}, dataset: {}, onclick: null, oninput: null,
    _attrs: {},
    _classes: new Set(),
    classList: {
      add: (c) => node._classes.add(c),
      remove: (c) => node._classes.delete(c),
      toggle: (c, on) => (on ? node._classes.add(c) : node._classes.delete(c)),
      contains: (c) => node._classes.has(c),
    },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    setAttribute(k, v) { node._attrs[k] = String(v); },
    removeAttribute(k) { delete node._attrs[k]; },
    getAttribute: (k) => (k in node._attrs ? node._attrs[k] : null),
    focus() {}, remove() {}, closest: () => null, insertBefore() {},
    get innerHTML() { return node._html; },
    set innerHTML(v) {
      node._html = String(v);
      for (const [, found] of node._html.matchAll(ID_IN_HTML)) {
        if (!registry.has(`#${found}`)) registry.set(`#${found}`, makeEl(found));
      }
    },
    get textContent() { return node._text; },
    set textContent(v) { node._text = String(v); },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  return node;
}

const el = (sel) => {
  if (!registry.has(sel)) registry.set(sel, makeEl(sel.replace(/^#/, "")));
  return registry.get(sel);
};

globalThis.MutationObserver = class {
  observe() {} disconnect() {} takeRecords() { return []; }
};
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.document = {
  querySelector: (s) => el(s),
  querySelectorAll: () => [],
  getElementById: (i) => el(`#${i}`),
  createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {},
  innerWidth: 1440, innerHeight: 900,
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.Character = { scene: () => ({}), mount: () => ({}), svg: () => "" };

/* The scenario's `answers` are consumed in order, which is how "press Check
 * and the answer changes" is exercised — but **only for `/api/updates/*`**.
 *
 * The first version advanced on every request, and `app.js`'s own boot code
 * calls `/api/brain/enrich/status` and `/api/onboarded` as the source is
 * evaluated. Those ate the first two answers, so the row rendered the fixture
 * meant for the second step and a correct toggle looked like it sent the wrong
 * value. A queue shared with unrelated traffic is not a queue.
 */
let step = 0;
const answers = scenario.answers || [];

globalThis.fetch = async (url, opts = {}) => {
  const p = String(url);
  requests.push({
    path: p,
    method: opts.method || "GET",
    body: opts.body ? JSON.parse(opts.body) : null,
  });
  if (!p.startsWith("/api/updates/")) {
    return { ok: true, json: async () => ({}) };
  }
  if (scenario.fail_on && p.includes(scenario.fail_on)) {
    return { ok: false, status: 500, statusText: "boom",
             json: async () => ({ detail: "the server said no" }) };
  }
  const answer = answers[Math.min(step++, answers.length - 1)] || {};
  return { ok: true, json: async () => answer };
};

const src = appSource(WEB_DIR);
const api = new Function(`${src}
  return { loadUpdates, upRender, upCheckNow, upToggleAuto,
           setToast: (fn) => { toast = fn; } };`)();
api.setToast((m) => toasts.push(String(m)));

const result = { requests, toasts, steps: [] };

/* `aria-pressed` is written in the renderer's template string, not through
 * `setAttribute`, so a fake element's `getAttribute` cannot see it — the first
 * version of this harness read `null` for every state and two tests failed
 * against correct code. Read it out of the markup the renderer produced, which
 * is the only place it exists. */
function attrOf(html, id, attr) {
  const tag = new RegExp(`<[^>]*\\bid="${id}"[^>]*>`).exec(String(html));
  if (!tag) return null;
  const found = new RegExp(`\\b${attr}="([^"]*)"`).exec(tag[0]);
  return found ? found[1] : null;
}

function snapshot(label) {
  const body = el("#upBody")._html;
  result.steps.push({
    label,
    ids: [...new Set([...body.matchAll(ID_IN_HTML)].map((m) => m[1]))],
    html: body,
    toggle_pressed: attrOf(body, "upToggle", "aria-pressed"),
  });
}

async function main() {
  await api.loadUpdates();
  snapshot("loaded");

  for (const act of scenario.actions || []) {
    switch (act) {
      case "press_check": await api.upCheckNow(); break;
      case "press_toggle": await api.upToggleAuto(); break;
      case "press_get": {
        const get = registry.get("#upGet");
        result.get_existed = !!(get && get.onclick);
        if (get && get.onclick) await get.onclick();
        break;
      }
      default: throw new Error(`unknown action ${act}`);
    }
    snapshot(act);
  }
  process.stdout.write(JSON.stringify(result));
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({
    harness_error: String((e && e.stack) || e), requests, steps: result.steps,
  }));
  process.exit(1);
});
