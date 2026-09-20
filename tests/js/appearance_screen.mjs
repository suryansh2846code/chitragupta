/**
 * Open Appearance, draw the roster, pick an agent, save it — for real.
 *
 * The avatar renderer has its own suite (`character/test/`, 31 tests) and this
 * harness deliberately does not re-test it. What it tests is the seam, which is
 * where every bug in this feature will actually be: does the settings-rail item
 * route anywhere, does every agent get a face drawn, does picking one hand the
 * editor that agent's document, and does Save send the document that is on
 * screen to the endpoint that stores it.
 *
 * `Character.mountEditor` is stubbed. It is the one piece that needs a real
 * browser DOM — native colour inputs, pointer capture, `createElementNS` — and
 * standing that up here would be testing the fake DOM rather than the app. What
 * it was *given* is recorded instead, because that is the part this file owns.
 *
 * argv: <app.js>   stdout: {opened, roster, picked, saved, error}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const WEB = path.dirname(APP_JS);
const PAGE = fs.readFileSync(path.join(WEB, "index.html"), "utf8");

// ── a DOM with just enough reality ─────────────────────────────────────────
// Children are real, so a render path that builds a roster can be read back by
// counting them. Everything else is a stub that records rather than acts.
function makeEl(tag = "div") {
  const el = {
    tag, value: "", hidden: false, disabled: false, title: "", scrollTop: 0,
    style: {}, dataset: {}, onclick: null, children: [], _attrs: {},
    className: "", _html: "", _text: "",
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); },
      toggle(c, on) { if (on === undefined) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); } else if (on) this._set.add(c); else this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    appendChild(child) { el.children.push(child); return child; },
    insertBefore(child) { el.children.unshift(child); return child; },
    removeChild(child) { el.children = el.children.filter((c) => c !== child); },
    setAttribute(k, v) { el._attrs[k] = String(v); },
    getAttribute(k) { return el._attrs[k] ?? null; },
    removeAttribute(k) { delete el._attrs[k]; },
    addEventListener() {}, removeEventListener() {}, focus() {}, remove() {},
    closest: () => null, querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 64, height: 64 }),
  };
  // Real accessors, because `textContent = ""` is how every render path here
  // empties a container before redrawing it. A plain string property looks like
  // it works and silently keeps the old children, so a roster redrawn twice
  // counts double — and an assertion that "every agent has a card" passes while
  // the screen shows each agent twice.
  Object.defineProperty(el, "textContent", {
    get: () => el._text || el.children.map((c) => c.textContent).join(""),
    set: (v) => { el.children = []; el._html = ""; el._text = String(v); },
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html,
    set: (v) => { el.children = []; el._text = ""; el._html = String(v); },
  });
  return el;
}

const registry = new Map();
const el = (sel) => {
  if (!registry.has(sel)) registry.set(sel, makeEl());
  return registry.get(sel);
};

// Both lists come out of the page, never from a copy here. A settings-rail item
// added to `index.html` without a panel — or with a panel nothing routes to —
// is exactly the bug this file exists to catch, and a hardcoded list would hide
// it by only ever clicking what someone remembered to type.
const MSNAVS = [...PAGE.matchAll(/data-msnav="([a-z-]+)"/g)].map((m) => m[1]);
const SPS = [...PAGE.matchAll(/data-sp="([a-z-]+)"/g)].map((m) => m[1]);
const railItems = MSNAVS.map((msnav) => Object.assign(makeEl("button"), { dataset: { msnav } }));
const panels = SPS.map((sp) => Object.assign(makeEl("div"), { dataset: { sp }, hidden: true }));
const navButtons = [...PAGE.matchAll(/data-nav="([a-z]+)"/g)]
  .map((m) => Object.assign(makeEl("button"), { dataset: { nav: m[1] } }));

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  // No `createElementNS`: this is not a browser, and `paintAvatar` is supposed
  // to notice that and fall back to a static character rather than throwing.
  createElement: (tag) => makeEl(tag),
  querySelector: (sel) => el(sel),
  querySelectorAll: (sel) => (sel === ".ms-nav-item" ? railItems
    : sel === ".sp" ? panels
    : sel === ".snav" ? navButtons
    : sel === ".agent" ? [] : []),
  getElementById: (id) => el(`#${id}`),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(), head: makeEl(),
  activeElement: null,
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {},
  innerWidth: 1280, innerHeight: 800,
};
// `navigator` is a getter-only global in Node, so it is defined rather than
// assigned. The editor's "Copy link" reaches for the clipboard through it.
Object.defineProperty(globalThis, "navigator", {
  value: { clipboard: { writeText: async () => {} } }, configurable: true,
});
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.confirm = () => true;

const AGENTS = [
  { id: "inbox", name: "Inbox", role: "Triage", custom: false },
  { id: "launch", name: "Launch", role: "Shipping", custom: false },
  { id: "research", name: "Research", role: "Digging", custom: true },
];

const requests = [];
globalThis.fetch = async (url, opts) => {
  requests.push({ url, method: (opts && opts.method) || "GET", body: opts && opts.body });
  if (url === "/api/agents") return { ok: true, json: async () => ({ agents: AGENTS }) };
  if (url === "/api/agents/avatars") return { ok: true, json: async () => ({ avatars: {} }) };
  return { ok: true, json: async () => ({}) };
};

// The fixture is installed INSIDE the app's scope, not on `globalThis`.
// `agents` and `current` are `let` bindings inside the evaluated script, so a
// global of the same name is a different variable — one this harness would
// happily set while the roster kept reading the empty one and rendering its
// "no agents yet" message. That is not a hypothetical: it is what this file did
// first, and the only symptom was a roster with one child in it.
globalThis.__AGENTS = AGENTS;
new Function(`${appSource(WEB)}
  ;agents = globalThis.__AGENTS; current = "launch";
`)();

const out = { opened: null, roster: null, picked: null, saved: null, error: null };

try {
  // The editor needs a browser; what it is handed is what this file owns.
  const mounted = [];
  globalThis.Character.mountEditor = (container, opts) => {
    mounted.push({ hasDocument: !!opts.document, name: opts.document && opts.document.metadata.name,
                   storageKey: opts.storageKey });
    return { destroy() {}, getDocument: () => opts.document, setDocument() {}, on: () => () => {} };
  };

  // 1. The settings-rail item routes somewhere.
  const rail = railItems.find((b) => b.dataset.msnav === "appearance");
  if (!rail) throw new Error("index.html has no Appearance item in the settings rail");
  if (typeof rail.onclick !== "function") throw new Error("the Appearance rail item is unbound");
  el("#modelScreen").hidden = true;
  panels.forEach((p) => { p.hidden = true; });
  rail.onclick();
  const shown = panels.filter((p) => p.hidden === false).map((p) => p.dataset.sp);
  out.opened = { screen: el("#modelScreen").hidden === false, panel: shown };

  // 2. Every agent got a card, and every card got a face.
  const roster = el("#apRoster");
  out.roster = {
    cards: roster.children.length,
    // The avatar is drawn into the first child of each card. A card with no
    // `<svg>` is an agent with no face, which is the whole point of the screen.
    withFaces: roster.children.filter((c) => (c.children[0] || {}).innerHTML.includes("<svg")).length,
    names: roster.children.map((c) => (c.children[1] || {}).textContent),
  };

  // 3. It opened on the agent being talked to, not on an empty frame.
  out.picked = { mounts: mounted.length, name: mounted[0] && mounted[0].name,
                 storageKey: mounted[0] && mounted[0].storageKey };

  // 4. Picking another agent hands the editor that agent's document.
  roster.children[0].onclick();
  out.picked.afterClick = mounted[mounted.length - 1].name;

  // 5. Save sends the document on screen to the endpoint that stores it.
  requests.length = 0;
  const save = el("#apSave");
  if (typeof save.onclick !== "function") throw new Error("Save is unbound");
  await save.onclick();
  const put = requests.find((r) => r.method === "PUT");
  out.saved = put ? { url: put.url, hasScene: !!JSON.parse(put.body).scene } : null;
} catch (e) {
  out.error = `${e.constructor.name}: ${e.message}`;
}

process.stdout.write(JSON.stringify(out));
process.exit(0);
