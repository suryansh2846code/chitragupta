/**
 * Execute the keyboard paths, because a keyboard path that is merely *written*
 * is not a keyboard path.
 *
 * Two things are checked by running the real code from app.js:
 *
 *  1. The agent rail. Agent rows are <div>s built into innerHTML. They carry
 *     role="button", so Enter and Space must select — and the only way to know
 *     is to fire the handler the render attached and see whether the selection
 *     actually moved.
 *
 *  2. Escape on a stack of modals. The handler has to pick the *topmost* open
 *     one and close it by clicking that modal's own close button, so each
 *     modal's existing teardown still runs. Closing the wrong one, or bypassing
 *     the button, both look fine in the source and are wrong on screen.
 *
 * Writes a JSON report to stdout. No assertions live here — the driver owns
 * those, so a failure names the behaviour rather than a line of stub.
 */
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];   // a path inside chitragupta/web/

const focusLog = [];
const clickLog = [];
const winListeners = {};

let activeElement = null;

class El {
  constructor(tag = "div", name = "") {
    this.tagName = tag.toUpperCase();
    this._name = name;
    this.attrs = {};
    this._hidden = false;
    this._html = "";
    this.style = { cssText: "" };
    this.dataset = {};
    this.textContent = "";
    this.placeholder = "";
    this.value = "";
    this.children = [];
    this.classList = {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    };
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); }
  get hidden() { return this._hidden; }
  set hidden(v) {
    const was = this._hidden;
    this._hidden = !!v;
    if (was !== this._hidden) notify(this);
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  getClientRects() { return this._hidden ? [] : [{ width: 10, height: 10 }]; }
  focus() { focusLog.push(this._name); activeElement = this; }
  click() { clickLog.push(this._name); if (this.onclick) this.onclick({ target: this }); }
  addEventListener() {}
  appendChild(c) { this.children.push(c); }
  remove() {}
  closest() { return null; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  get isConnected() { return true; }
}

// ── MutationObserver, enough of one to carry attributeFilter:["hidden"] ─────
const observers = [];
function notify(target) {
  for (const { cb, targets } of observers) {
    if (targets.has(target)) cb([{ target, attributeName: "hidden" }]);
  }
}
globalThis.MutationObserver = class {
  constructor(cb) { this.entry = { cb, targets: new Set() }; observers.push(this.entry); }
  observe(t) { this.entry.targets.add(t); }
  disconnect() { this.entry.targets.clear(); }
};

// ── a modal: background element, a close button in its head ────────────────
function makeModal(name) {
  const bg = new El("div", name);
  bg.hidden = true;
  const close = new El("button", `${name}:close`);
  bg.querySelector = (sel) => (sel.includes("button") || sel.includes("ghost") ? close : null);
  const field = new El("input", `${name}:field`);
  bg.querySelectorAll = () => [close, field];
  bg._close = close;
  bg._field = field;
  return bg;
}
const modalA = makeModal("modalA");
const modalB = makeModal("modalB");
const modals = [modalA, modalB];

// ── the agent rail ─────────────────────────────────────────────────────────
const agentList = new El("div", "agentList");
let agentRows = [];

const byId = {};
const el = (id) => (byId[id] ||= new El("div", id));
byId.agentList = agentList;

globalThis.document = {
  get activeElement() { return activeElement; },
  querySelector: (sel) => (sel.startsWith("#") ? el(sel.slice(1)) : new El("div", sel)),
  querySelectorAll: (sel) => {
    if (sel === ".modal-bg") return modals;
    if (sel === ".agent") return agentRows;
    if (sel === ".snav") return [];
    return [];
  },
  getElementById: (id) => el(id),
  createElement: (t) => new El(t),
  addEventListener() {},
  body: new El("body"),
  documentElement: new El("html"),
};
globalThis.window = {
  location: { pathname: "/", href: "/", reload() {} },
  addEventListener(type, fn) { (winListeners[type] ||= []).push(fn); },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  devicePixelRatio: 1,
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.setTimeout = (fn) => { if (typeof fn === "function") fn(); return 0; };
globalThis.confirm = () => false;

const AGENTS = [
  { id: "lead-1", name: "Atlas", role: "runs your team", custom: false, tools: [] },
  { id: "inbox-2", name: "Inbox", role: "triage & replies", custom: true, tools: [] },
];
const fetchLog = [];
globalThis.fetch = async (url) => {
  fetchLog.push(url);
  const body =
    url === "/api/agents" ? { agents: AGENTS }
    : url.includes("/history") ? { history: [] }
    : {};
  return { ok: true, json: async () => body };
};

// The whole workspace, in the order index.html loads it — one file today,
// several once app.js is split. `new Function` compiles a script, so every
// piece has to arrive in one shared scope; see tests/js/_app_source.mjs.
const src = appSource(path.dirname(APP_JS));
new Function(
  src +
  "\nglobalThis.__loadAgents = loadAgents;" +
  "\nglobalThis.__current = () => current;"
)();

const report = { agentRail: {}, escape: {} };

// ── 1. render the rail, then drive it from the keyboard ────────────────────
await globalThis.__loadAgents();
const html = agentList.innerHTML;
report.agentRail.html = html;
report.agentRail.hasRole = /role="button"/.test(html);
report.agentRail.hasTabindex = /tabindex="0"/.test(html);
report.agentRail.hasAriaLabel = /aria-label="[^"]+"/.test(html);
// The row's trailing control. It was a ✕ that deleted the agent, and only on
// custom ones; it is now the ⋯ that opens the profile, on every agent, with
// delete moved into that profile's danger zone. The assertion is the same one
// either way — a real <button> with a label — because that is the property
// that was worth pinning, not which action happened to be behind it.
report.agentRail.rowControlIsButton = /<button[^>]*class="agent-more"/.test(html);
report.agentRail.rowControlHasLabel = /class="agent-more"[^>]*aria-label="[^"]+"/.test(html);
// Every agent gets one, not just the custom ones. AGENTS below is a mix.
report.agentRail.rowControlCount = (html.match(/class="agent-more"/g) || []).length;

// Re-render against real row stubs so the handlers the render attaches land on
// something we can fire. Row 1 is "inbox-2" — pressing Enter must select it.
agentRows = AGENTS.map((a) => {
  const row = new El("div", `agent:${a.id}`);
  row.dataset.id = a.id;
  return row;
});
await globalThis.__loadAgents();
const row = agentRows[1];
report.agentRail.keydownAttached = typeof row.onkeydown === "function";
let defaultPrevented = false;
if (row.onkeydown) {
  row.onkeydown({
    key: "Enter",
    preventDefault() { defaultPrevented = true; },
    target: { closest: () => null },
  });
}
report.agentRail.enterPreventedDefault = defaultPrevented;
report.agentRail.selectedAfterEnter = globalThis.__current();

// Space must work too — role="button" promises both.
agentRows[0].onkeydown?.({
  key: " ", preventDefault() {}, target: { closest: () => null },
});
report.agentRail.selectedAfterSpace = globalThis.__current();

// A keystroke on the delete button must NOT select the row behind it.
const before = globalThis.__current();
agentRows[1].onkeydown?.({
  key: "Enter", preventDefault() {}, target: { closest: () => ({}) },
});
report.agentRail.deleteKeyDidNotSelect = globalThis.__current() === before;

// ── 2. Escape, against a stack of two modals ───────────────────────────────
const esc = () => {
  for (const fn of winListeners.keydown || []) {
    fn({ key: "Escape", preventDefault() {}, stopPropagation() {} });
  }
};

clickLog.length = 0; focusLog.length = 0;
esc();
report.escape.closedWithNoneOpen = clickLog.slice();

// open A, then B on top of it
const opener = new El("button", "theOpener");
activeElement = opener;
modalA.hidden = false;
report.escape.focusWentIntoA = focusLog.slice();

modalB.hidden = false;
clickLog.length = 0;
esc();
report.escape.clickedForTopmost = clickLog.slice();

// with B still notionally open, closing it should hand focus back
focusLog.length = 0;
modalB.hidden = true;
report.escape.focusRestoredAfterClose = focusLog.slice();

clickLog.length = 0;
esc();
report.escape.clickedForRemaining = clickLog.slice();

report.fetchLog = fetchLog;
process.stdout.write(JSON.stringify(report, null, 2));
