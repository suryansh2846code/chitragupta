/**
 * Does the Agent Library open on first entry, and only on first entry?
 *
 * Nothing is pre-added and there is no lead agent, so a new install has NO
 * agents at all — the library is the only way to get one. It used to be opened
 * by the "name your lead agent" card; that card is gone, so this executes the
 * boot path instead.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {onboarded: bool, seen: bool}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const { onboarded, seen } = JSON.parse(fs.readFileSync(0, "utf8"));

const makeEl = () => {
  const node = {
    value: "", hidden: true, disabled: false, className: "", style: {},
    dataset: {}, onclick: null, onkeydown: null, textContent: "", children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => makeEl(), querySelectorAll: () => [],
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
    // `clearSkeletons()` drops `aria-busy` when the rail turns out to be empty,
    // which is exactly the path this harness exercises. A fake element without
    // this method does not fail the assertion under test — it throws before the
    // assertion is ever reached.
    removeAttribute() {},
    focus() {}, select() {}, remove() {}, closest: () => null,
    scrollIntoView() {}, appendChild(c) { this.children.push(c); return c; },
  };
  let html = "";
  Object.defineProperty(node, "innerHTML", {
    get: () => html, set(v) { html = String(v); if (v === "") node.children.length = 0; },
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
  getElementById: (id) => el(`#${id}`), createElement: () => makeEl(),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = { location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {} };
const store = {};
if (onboarded) store.chitragupta_onboarded = "1";
if (seen) store.chitragupta_saw_library = "1";
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
/** Routed by path: a fixture thin enough to throw would abort the very flow
 *  under test, and the abort would look like "the library did not open". */
const FIXTURES = [
  [/\/api\/agents\/lead/, { id: "atlas", name: "Atlas", role: "lead agent" }],
  [/\/api\/brain\/stats/, { total: 0, graph: { entities: 0, relations: 0 } }],
  [/\/api\/brain\/entities/, { entities: [] }],
  [/\/api\/agents\/library/, { categories: [], templates: [] }],
  [/\/api\/agents\/[^/]+\/history/, { history: [] }],
  [/\/api\/agents\/[^/]+\/model/, { provider: null, model: null }],
  [/\/api\/agents/, { agents: [] }],
];
/** Boot touches a dozen endpoints. A fixture thin enough to throw would abort
 *  the very sequence under test, so the default is permissive rather than {}. */
const DEFAULT = {
  agents: [], tools: [], categories: [], templates: [], entities: [],
  tasks: [], reminders: [], routines: [], providers: [], connectors: [],
  history: [], results: [], onboarded: true, total: 0,
  stats: { today: 0, overdue: 0, total: 0 },
  graph: { entities: 0, relations: 0 },
};
globalThis.fetch = async (p) => {
  const url = String(p);
  const hit = FIXTURES.find(([re]) => re.test(url));
  return { ok: true, json: async () => (hit ? { ...DEFAULT, ...hit[1] } : DEFAULT) };
};

// Evaluating the app RUNS its boot sequence — which is the thing under test,
// so it is not called a second time here. Boot is async, hence the settle.
let error = null;
try {
  new Function(appSource(path.dirname(APP_JS)))();
  await new Promise((r) => setTimeout(r, 60));
} catch (e) {
  error = `${e.constructor.name}: ${e.message}`;
}

console.log(JSON.stringify({
  error,
  libraryOpen: el("#libraryScreen").hidden === false,
  sawFlag: store.chitragupta_saw_library || null,
  // `createLead` catches its own failures, so a silent abort looks identical
  // to "the library did not open". These say which happened.
  agentsEmptyState: el("#agentList").innerHTML.includes("Agent Library"),
}, null, 2));

// Boot installs polling intervals that would keep this process alive forever.
process.exit(0);
