/**
 * Render a connector confirmation card and report what a person would read.
 *
 * Two things went wrong on a real machine and this executes both. The card
 * printed the tool's id and every argument raw — `notion-update-page`, a UUID,
 * `properties {}` — which tells nobody what they are approving. And `esc()`
 * threw on a non-string, so the TypeError replaced the message it was escaping:
 * a successful action rendered as a red crash.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {action, result}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const { action, plan, reply, result, edits, catalog, undoResult } =
  JSON.parse(fs.readFileSync(0, "utf8"));

const makeEl = (tag = "div") => {
  const node = {
    tag, value: "", hidden: false, disabled: false, className: "", style: {},
    dataset: {}, onclick: null, textContent: "", children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelectorAll: () => [],
    addEventListener() {},
    // Recorded, not discarded: the generic field editor labels its boxes with
    // `aria-label`, and a test addressing them by position would pin the
    // registry's field order as well as the behaviour it means to check.
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    focus() {}, remove() {}, closest: () => null,
    parent: null,
    appendChild(c) { c.parent = this; this.children.push(c); return c; },
    // Without this the Undo button's success path threw `replaceWith is not a
    // function`, was swallowed by its own error handler, and the harness
    // reported the button still sitting there — a green test over a path that
    // had never run. Real browsers have it; the fake DOM did not.
    replaceWith(node) {
      const parent = this.parent;
      if (!parent) return;
      const at = parent.children.indexOf(this);
      if (at === -1) return;
      node.parent = parent;
      parent.children.splice(at, 1, node);
    },
    querySelector(sel) {
      this._q = this._q || {};
      if (!this._q[sel]) this._q[sel] = makeEl();
      return this._q[sel];
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
// What the confirm actually POSTed. The claim an editable card makes is about
// this and nothing else, and it is invisible in the rendered HTML.
//
// Only requests that CARRY a body, and only the first: a successful confirm
// calls `loadReminders()` and `loadRoutines()` straight afterwards, and those
// have no body — so "last call wins" recorded null over the thing under test.
let sent = null;
//: Undo POSTs to a different endpoint, and its body is not the thing the
//: confirm claim is about — recorded separately so one does not hide the other.
let undoSent = null;
globalThis.fetch = async (url, options) => {
  const body = options && options.body;
  const isUndo = String(url || "").includes("/api/actions/undo");
  if (body && isUndo) {
    try { undoSent = JSON.parse(body); } catch { undoSent = "unparseable"; }
    return { ok: true, json: async () => (undoResult || { ok: true, detail: "Undone" }) };
  }
  if (body && sent === null) {
    try { sent = JSON.parse(body); } catch { sent = "unparseable"; }
  }
  return { ok: true, json: async () => result };
};

new Function(appSource(path.dirname(APP_JS)) +
  "\nglobalThis.__card = actionCard;" +
  "\nglobalThis.__plan = planCard;" +
  "\nglobalThis.__parsePlans = parsePlans;" +
  "\nglobalThis.__esc = esc;" +
  // `ACTION_CATALOG` is a `let` inside this scope and is normally filled by
  // `loadActionCatalog()` at boot. The harness does not boot, so a card would
  // render with no editable fields and no Undo — the two things most worth
  // testing — unless the test can seed it.
  "\nglobalThis.__setCatalog = (c) => { ACTION_CATALOG = c; };")();

if (catalog) globalThis.__setCatalog(catalog);

// `esc` is the escaper every innerHTML path in the app goes through, so a
// throw here does not lose one message — it blanks whatever was being drawn.
// Exercised directly, because a caller that stringifies first would hide it.
const escaped = {};
for (const [name, value] of [["object", {}], ["array", []], ["number", 7],
                             ["bool", true], ["null", null],
                             ["nested", { a: { b: 1 } }]]) {
  try {
    escaped[name] = { ok: true, out: globalThis.__esc(value) };
  } catch (e) {
    escaped[name] = { ok: false, out: `${e.constructor.name}: ${e.message}` };
  }
}

/** Every element under `node` with this class. The fake DOM has no selectors,
 *  and an editable card holds its inputs as real children — so the test walks
 *  what was actually appended rather than trusting a query that returns []. */
const findAll = (node, className, found = []) => {
  for (const child of node.children || []) {
    if (child.className === className) found.push(child);
    findAll(child, className, found);
  }
  return found;
};

/** Everything a person would actually read on the card.
 *
 *  `innerHTML` only holds what was set as a string — anything built with
 *  `createElement` and appended is invisible to it, which is a blind spot the
 *  moment a card has real inputs in it. This walks what was appended too.
 */
const visibleText = (node) => {
  let out = String(node.innerHTML || "").replace(/<[^>]*>/g, " ");
  out += " " + String(node.textContent || "");
  if (node.value) out += " " + node.value;
  for (const child of node.children || []) out += " " + visibleText(child);
  return out;
};

// `reply` exercises the split — which cards a whole model reply produces, and
// that an action inside a plan is never ALSO rendered as a loose card.
let parsed = null;
if (reply !== undefined) {
  const out = globalThis.__parsePlans(reply);
  parsed = {
    clean: out.clean,
    plans: out.plans.map((p) => ({ rationale: p.rationale,
                                   steps: p.steps.map((s) => s.type) })),
    loose: out.actions.map((a) => a.type),
  };
}

let error = null, card = null;
try {
  if (plan) card = globalThis.__plan(plan);
  else if (action) card = globalThis.__card(action);
} catch (e) {
  error = `${e.constructor.name}: ${e.message}`;
}

// Let the caller correct the card the way a person would, before confirming.
// This is the whole feature: what executes must be what is on screen NOW.
let fields = [];
if (card && edits) {
  fields = findAll(card, "ac-field");
  for (const [index, value] of Object.entries(edits.set || {})) {
    if (fields[index]) fields[index].value = String(value);
  }
  for (const index of edits.drop || []) {
    const drops = findAll(card, "ac-drop ghost");
    if (drops[index] && drops[index].onclick) drops[index].onclick();
  }
  // The generic editor's boxes carry a second class, so they are a separate
  // list — addressed by field NAME rather than by position, because the order
  // is the registry's and a test pinning an index would pin that too.
  const wide = findAll(card, "ac-field ac-field-wide");
  for (const [name, value] of Object.entries(edits.setField || {})) {
    const box = wide.find((b) => (b.attrs || {})["aria-label"] === name);
    if (box) box.value = String(value);
  }
}

let confirmed = null;
if (card) {
  try {
    await card.querySelector(".ac-confirm").onclick();
    confirmed = card.querySelector(".ac-result").innerHTML;
  } catch (e) {
    confirmed = `THREW: ${e.constructor.name}: ${e.message}`;
  }
}

// Undo lives on the result, so it only exists after a confirm. Clicking it is
// the only way to know the button is wired to anything — a rendered button that
// does nothing is the exact failure it is supposed to prevent.
const undoBtn = card
  ? findAll(card.querySelector(".ac-result"), "tiny ac-undo")[0] || null : null;
// Read BEFORE the click. The handler rewrites the label to "Undoing…" and then
// replaces the node outright, so reading it afterwards reports the machinery
// rather than the word the user was offered.
const undoLabel = undoBtn ? undoBtn.textContent : null;
let afterUndo = null;
if (undoBtn && undoBtn.onclick) {
  try {
    await undoBtn.onclick();
    afterUndo = visibleText(card.querySelector(".ac-result")).replace(/\s+/g, " ").trim();
  } catch (e) {
    afterUndo = `THREW: ${e.constructor.name}: ${e.message}`;
  }
}

console.log(JSON.stringify({
  error,
  parsed,
  html: card ? card.innerHTML : "",
  text: card ? visibleText(card).replace(/\s+/g, " ").trim() : "",
  afterConfirm: confirmed,
  // What the confirm actually POSTed. The claim an editable card makes is
  // about this and nothing else.
  sent,
  fieldCount: card ? findAll(card, "ac-field").length : 0,
  //: The generic editor's boxes, by the field name each one carries.
  editableFields: card
    ? findAll(card, "ac-field ac-field-wide").map((b) => (b.attrs || {})["aria-label"])
    : [],
  risk: card ? card.dataset.risk : null,
  hasUndo: Boolean(undoBtn),
  undoLabel,
  undoSent,
  afterUndo,
  escaped,
}, null, 2));
