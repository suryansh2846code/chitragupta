/**
 * Run every rejection shape `api()` can produce through `errText`.
 *
 * `api()` rejects with `e.detail || r.statusText`, and ~45 call sites used to
 * put that value on screen with `String(e)`. Three of its shapes are not
 * English — a `SyntaxError` when the body was not JSON, FastAPI's 422 `detail`
 * array (which stringifies to `[object Object]`), and a bare
 * `TypeError: Failed to fetch`. None of them is something a user can act on,
 * and "never surface an internal" is a rule in `/CLAUDE.md`.
 *
 * Source order cannot show this: every branch returns *a* string. So each
 * shape is actually passed in and the output read back.
 *
 * argv: <a path inside chitragupta/web/>   stdout: one JSON object
 */
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];

const makeEl = () => ({
  innerHTML: "", value: "", hidden: false, disabled: false, title: "",
  textContent: "", style: {}, dataset: {}, _attrs: {},
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  querySelector: () => makeEl(), querySelectorAll: () => [],
  addEventListener() {}, appendChild() {},
  setAttribute(k, v) { this._attrs[k] = v; },
  getAttribute(k) { return this._attrs[k] ?? null; },
  focus() {}, remove() {}, closest: () => null,
});
globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: () => makeEl(), querySelectorAll: () => [],
  getElementById: () => makeEl(), createElement: () => makeEl(),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = { location: { pathname: "/", href: "/" }, addEventListener() {},
                      matchMedia: () => ({ matches: false, addEventListener() {} }), open() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

new Function(appSource(path.dirname(APP_JS)) + "\nglobalThis.__errText = errText;")();

const E = globalThis.__errText;

// The shapes, named for what actually produces each one.
const badJson = new SyntaxError("Unexpected token '<', \"<html>\" is not valid JSON");
const offline = new TypeError("Failed to fetch");

process.stdout.write(JSON.stringify({
  // the common case: FastAPI's `detail` is already a sentence for a person
  plainDetail: E("That folder is outside your home directory."),
  // the three that were reaching the screen as internals
  notJson: E(badJson),
  serverGone: E(offline),
  validation: E([{ loc: ["body", "url"], msg: "field required", type: "value_error" }]),
  // a bare object: never `[object Object]`
  bareObject: E({ some: "internal", shape: 1 }),
  objectWithDetail: E({ detail: "The key was rejected." }),
  // the empties
  nullish: E(null),
  undef: E(undefined),
  emptyString: E("   "),
  // a caller-supplied fallback still wins over the generic one
  custom: E(null, "Could not load your agents."),
  // an ordinary Error keeps its message
  ordinary: E(new Error("Ollama is not running.")),
}));
process.exit(0);
