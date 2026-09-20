/**
 * What `api()` actually puts on the wire for each body shape.
 *
 * `fetch` stringifies whatever it is handed, so `body: { url }` left as an
 * object goes out as the text "[object Object]" — which every endpoint taking
 * a JSON body answers 422 to. That was live on `/api/open-browser` and
 * invisible, because its only caller had a `catch` that fell back to
 * `window.open`.
 *
 * argv: <a path inside chitragupta/web/>
 */
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];

const makeEl = () => ({
  value: "", hidden: false, disabled: false, className: "", style: {},
  dataset: {}, onclick: null, textContent: "", children: [],
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, setAttribute() {}, getAttribute: () => null,
  focus() {}, remove() {}, closest: () => null, innerHTML: "",
  appendChild(c) { this.children.push(c); return c; },
  querySelector: () => makeEl(), querySelectorAll: () => [],
});

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: () => makeEl(), querySelectorAll: () => [],
  getElementById: () => makeEl(), createElement: () => makeEl(),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = { location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {} };
const store = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };

/** Exactly what reached `fetch`, with no interpretation. */
const seen = [];
globalThis.fetch = async (url, opts = {}) => {
  seen.push({
    url,
    bodyType: typeof opts.body,
    body: opts.body === undefined ? null : String(opts.body),
    contentType: (opts.headers || {})["Content-Type"] || null,
  });
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};

new Function(appSource(path.dirname(APP_JS)) + "\nglobalThis.__api = api;")();

const api = globalThis.__api;

await api("/plain-object", { method: "POST", body: { url: "https://x.test/a" } });
await api("/already-a-string", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ url: "https://x.test/b" }) });
await api("/no-body", { method: "POST" });
await api("/no-options");
await api("/form", { method: "POST", body: new FormData() });
await api("/explicit-type", {
  method: "POST", headers: { "Content-Type": "text/plain" }, body: { a: 1 } });

console.log(JSON.stringify({ seen }, null, 2));
