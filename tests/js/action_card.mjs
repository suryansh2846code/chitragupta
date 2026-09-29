/**
 * Execute app.js's parseActions + actionCard against model-written text and
 * report every string the card wrote into the DOM.
 *
 * An action card is built from an `<action …>` tag the *model* emits, and it is
 * assembled with template strings into `innerHTML`. That makes every attribute
 * in that tag attacker-influenced in the only sense that matters here: the model
 * is summarising the user's own email and documents, so a crafted message can
 * decide what an attribute contains.
 *
 * Reads {text, markdown, catalog} as JSON on stdin and writes
 * {clean, actions, writes, markdown} to stdout.
 *
 * `catalog` seeds `ACTION_CATALOG`, which the app normally fills from
 * `/api/actions/catalog` at boot. Without it every action the branch chain does
 * not name renders from an empty registry — no label, no rows — so a walk over
 * every action would be checking escaping on cards that had drawn nothing.
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];   // a path inside chitragupta/web/
const input = JSON.parse(fs.readFileSync(0, "utf8"));

const writes = [];

const makeEl = (tag = "div") => {
  const e = {
    tagName: tag, _html: "", value: "", hidden: false, disabled: false,
    className: "", title: "", textContent: "", style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => makeEl(), querySelectorAll: () => [],
    addEventListener() {}, appendChild() {}, insertBefore() {},
    setAttribute() {},
    getAttribute: () => null, focus() {}, remove() {}, closest: () => null,
  };
  Object.defineProperty(e, "innerHTML", {
    get() { return this._html; },
    set(v) { this._html = String(v); writes.push(String(v)); },
  });
  return e;
};

// app.js installs a focus-management observer at load; every harness that
// evaluates the file needs one to exist, or the whole script throws before the
// function under test is ever reached.
globalThis.MutationObserver = class {
  observe() {}
  disconnect() {}
  takeRecords() { return []; }
};

globalThis.document = {
  querySelector: () => makeEl(), querySelectorAll: () => [],
  getElementById: () => makeEl(), createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

// The whole workspace, in the order index.html loads it — one file today,
// several once app.js is split. `new Function` compiles a script, so every
// piece has to arrive in one shared scope; see tests/js/_app_source.mjs.
const src = appSource(path.dirname(APP_JS));
new Function(
  src +
  "\nglobalThis.__parseActions = parseActions;" +
  "\nglobalThis.__actionCard = actionCard;" +
  "\nglobalThis.__setCatalog = (c) => { ACTION_CATALOG = c; };" +
  "\nglobalThis.__md = md;"
)();

if (input.catalog) globalThis.__setCatalog(input.catalog);

const { clean, actions } = globalThis.__parseActions(input.text);
for (const a of actions) globalThis.__actionCard(a);

process.stdout.write(JSON.stringify({
  clean,
  actions,
  writes,
  markdown: input.markdown === undefined ? null : globalThis.__md(input.markdown),
}));
