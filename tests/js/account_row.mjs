/**
 * Render the "You" row on the Account screen and press its buttons.
 *
 * What this catches that `node --check` cannot: a Sign in button drawn on a
 * build with no OAuth client, a privacy sentence that stops matching the scopes
 * the server actually requests, and a sign-in that leaves the button stuck on
 * "Waiting for Google…" when the browser never comes back.
 *
 * Same shape as `backup_screen.mjs` and `updates_row.mjs`: ids resolve out of
 * the markup the renderer produced, and `fetch` is faked rather than `api`.
 *
 * Reads a scenario as JSON on stdin, writes one JSON result to stdout.
 */
import fs from "node:fs";

import { appSource } from "./_app_source.mjs";

const WEB_DIR = process.argv[2];
const scenario = JSON.parse(fs.readFileSync(0, "utf8"));

const requests = [];
const toasts = [];
const confirms = [];
const registry = new Map();

const ID_IN_HTML = /\bid="([^"]+)"/g;

function makeEl(id = "") {
  const node = {
    id, _html: "", _text: "",
    value: "", hidden: false, disabled: false, title: "",
    style: {}, dataset: {}, onclick: null,
    _classes: new Set(),
    classList: {
      add: (c) => node._classes.add(c),
      remove: (c) => node._classes.delete(c),
      toggle: (c, on) => (on ? node._classes.add(c) : node._classes.delete(c)),
      contains: (c) => node._classes.has(c),
    },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    setAttribute() {}, removeAttribute() {}, getAttribute: () => null,
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
    querySelectorAll: (sel) => nodesMatching(node._html, sel),
  };
  return node;
}

const TAG_WITH_CLASS = /<[a-z][^>]*\bclass="([^"]*)"[^>]*>/gi;

/* A fake element per `class="… wanted …"` tag, carrying its `data-*`. The row
 * draws one button per provider now, so the handlers are bound by class rather
 * than by id and the harness has to find them the same way the browser does. */
function nodesMatching(html, sel) {
  if (!sel || !sel.startsWith(".")) return [];
  const wanted = sel.slice(1);
  const out = [];
  let index = 0;
  for (const [tag, classes] of String(html).matchAll(TAG_WITH_CLASS)) {
    if (!classes.split(/\s+/).includes(wanted)) continue;
    const node = makeEl(`${wanted}-${index++}`);
    for (const [, name, value] of tag.matchAll(/\bdata-([a-z-]+)="([^"]*)"/g)) {
      node.dataset[name.replace(/-([a-z])/g, (_m, c) => c.toUpperCase())] = value;
    }
    out.push(node);
  }
  return out;
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
globalThis.confirm = (m) => {
  confirms.push(String(m));
  return scenario.confirm !== false;
};
globalThis.Character = { scene: () => ({}), mount: () => ({}), svg: () => "" };

/* Answers are per path, and the `/state` queue advances so "sign in, then the
 * state has changed" can be driven. Scoped to `/api/account/` because `app.js`
 * makes its own requests as the source is evaluated — a queue shared with
 * unrelated traffic is not a queue, which `updates_row.mjs` learnt the hard
 * way. */
const queues = scenario.answers || {};
const used = {};

globalThis.fetch = async (url, opts = {}) => {
  const p = String(url);
  requests.push({
    path: p,
    method: opts.method || "GET",
    body: opts.body ? JSON.parse(opts.body) : null,
  });
  if (!p.startsWith("/api/account/") && p !== "/api/open-browser") {
    return { ok: true, json: async () => ({}) };
  }
  if (scenario.fail_on && p.includes(scenario.fail_on)) {
    return { ok: false, status: 400, statusText: "no",
             json: async () => ({ detail: scenario.fail_detail || "refused" }) };
  }
  const queue = queues[p];
  if (Array.isArray(queue)) {
    const at = used[p] || 0;
    used[p] = at + 1;
    return { ok: true, json: async () => queue[Math.min(at, queue.length - 1)] };
  }
  return { ok: true, json: async () => (queue === undefined ? {} : queue) };
};

const src = appSource(WEB_DIR);
const api = new Function(`${src}
  return { loadAccount, acRender, acSignIn, acSignOut, acCancel,
           acUnlink,
           busy: () => acBusy,
           setToast: (fn) => { toast = fn; } };`)();
api.setToast((m) => toasts.push(String(m)));

const result = { requests, toasts, confirms, steps: [] };

/* Which provider buttons were drawn, and in what state. Read out of the
 * markup, because that is the only place `data-provider` and `data-soon`
 * exist — a fake element's `getAttribute` sees only `setAttribute` calls. */
function providerButtons(html) {
  const out = [];
  for (const [tag] of String(html).matchAll(/<button[^>]*>/gi)) {
    const provider = /\bdata-provider="([^"]*)"/.exec(tag);
    const soon = /\bdata-soon="([^"]*)"/.exec(tag);
    if (!provider && !soon) continue;
    out.push({
      provider: provider ? provider[1] : null,
      soon: soon ? soon[1] : null,
      locked: !!soon,
    });
  }
  return out;
}

function snapshot(label) {
  const body = el("#acBody")._html;
  result.steps.push({
    label,
    ids: [...new Set([...body.matchAll(ID_IN_HTML)].map((m) => m[1]))],
    html: body,
    buttons: providerButtons(body),
    busy: api.busy(),
  });
}

async function main() {
  await api.loadAccount();
  snapshot("loaded");

  for (const act of scenario.actions || []) {
    switch (act) {
      case "press_signin":
        await api.acSignIn(scenario.provider || "google");
        break;
      case "press_link":
        await api.acSignIn(scenario.provider || "microsoft", true);
        break;
      case "press_unlink":
        await api.acUnlink(scenario.provider || "microsoft");
        break;
      case "press_signout": await api.acSignOut(); break;
      case "press_cancel": await api.acCancel(); break;
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
