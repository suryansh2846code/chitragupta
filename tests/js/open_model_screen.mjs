/**
 * Click the left-nav items for real and report which surface opened.
 *
 * "AI model" used to be one of four panels inside the slide-over drawer. That
 * is now a full-window screen of its own. Four call sites reach it, so the
 * drawer is gone entirely now — `tasks` moved into Inbox and `tools` became the
 * Agents & tools panel — so EVERY left-nav item has to land on a screen. The
 * routing is still one delegation inside openDrawer() rather than
 * one — and a delegation that matches too greedily would silently send Tasks,
 * Connectors and Tools to the model screen too. Source order cannot see that;
 * clicking can.
 *
 * argv: <app.js>   stdout: {opened: {...}, error}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];   // a path inside chitragupta/web/

// One element per selector, so what app.js mutates is what we read back.
const registry = new Map();
const makeEl = (tag = "div") => ({
  tag, value: "", hidden: true, disabled: false, title: "", scrollTop: 0,
  style: {}, dataset: {}, onclick: null, innerHTML: "", textContent: "",
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  querySelector: () => makeEl(), querySelectorAll: () => [],
  addEventListener() {}, appendChild() {}, setAttribute() {},
  getAttribute: () => null, focus() {}, remove() {}, closest: () => null,
});
const el = (sel) => {
  if (!registry.has(sel)) registry.set(sel, makeEl());
  return registry.get(sel);
};

// The nav buttons app.js binds at load, one per data-nav value — READ OUT OF
// index.html rather than typed here. A hardcoded copy of this list is how the
// harness kept believing in a "tasks" drawer for a release after it moved.
const PAGE = fs.readFileSync(path.join(path.dirname(APP_JS), "index.html"), "utf8");
const NAVS = [...PAGE.matchAll(/data-nav="([a-z]+)"/g)].map((m) => m[1]);
const navButtons = NAVS.map((nav) => Object.assign(makeEl("button"), { dataset: { nav } }));

// Model and Connectors share one shell, so "did the screen open" is only half
// the question — the other half is which panel it opened on. Both panels have
// to exist for showSettingsPanel() to have anything to hide.
const panels = ["inbox", "actions", "connectors", "model", "tools"].map((sp) =>
  Object.assign(makeEl("div"), { dataset: { sp }, hidden: true }));
const railItems = ["inbox", "brain", "connectors", "model"].map((msnav) =>
  Object.assign(makeEl("button"), { dataset: { msnav } }));

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: (sel) => el(sel),
  querySelectorAll: (sel) => (sel === ".snav" ? navButtons
    : sel === ".sp" ? panels
    : sel === ".ms-nav-item" ? railItems : []),
  getElementById: (id) => el(`#${id}`),
  createElement: () => makeEl(),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = { location: { pathname: "/", href: "/" }, addEventListener() {},
                      matchMedia: () => ({ matches: false, addEventListener() {} }), open() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};

new Function(appSource(path.dirname(APP_JS)))();

// The screen starts closed, whatever load-time code did to it.
el("#modelScreen").hidden = true;

const opened = {};
let error = null;
try {
  // Every nav item the page declares, so a new one cannot be added without a
  // harness that already clicks it. Brain and Library raise their own screens.
  for (const nav of NAVS.filter((n) => n !== "brain" && n !== "library")) {
    el("#modelScreen").hidden = true;
    panels.forEach((p) => { p.hidden = true; });
    const btn = navButtons.find((b) => b.dataset.nav === nav);
    if (typeof btn.onclick !== "function") { opened[nav] = "unbound"; continue; }
    try { btn.onclick(); } catch (e) { opened[nav] = `threw: ${e.message}`; continue; }
    const shown = panels.filter((p) => p.hidden === false).map((p) => p.dataset.sp);
    opened[nav] = {
      modelScreen: el("#modelScreen").hidden === false,
      panel: shown.length === 1 ? shown[0] : shown,   // an array means ambiguous
    };
  }
  // …and the screen closes again.
  el("#modelScreen").hidden = false;
  const close = el("#msClose");
  if (typeof close.onclick === "function") close.onclick();
  opened.closeButtonWorks = el("#modelScreen").hidden === true;
} catch (e) {
  error = `${e.constructor.name}: ${e.message}`;
}

process.stdout.write(JSON.stringify({ opened, error }));
process.exit(0);
