/**
 * The automation builder, driven: open it, type into it, read what it would save.
 *
 * The claim worth this much machinery is not that the form renders. It is that
 * **what the form sends is the schema the engine already reads** — a list of
 * flat conditions, with a number where a number is meant and a list where a
 * list is meant — and that the options in it came from the server rather than
 * from a copy kept here. A form that quietly invents its own shape is a second
 * automation schema, which is the one thing this was not allowed to become.
 *
 * The whole page is evaluated, in the order `index.html` loads it, because a
 * harness that evaluates one file passes while the real page is broken by
 * something another script declared first.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {vocabulary, automation, script}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
/** Everything the app told the user, in order. */
globalThis.__toasts = [];

const { vocabulary, automation, script, failPatch } =
  JSON.parse(fs.readFileSync(0, "utf8"));

/** Every element the app asked for, so the harness can read one back. */
const registry = new Map();

/** Children the app wrote into innerHTML, as objects a test can type into. */
function parseChildren(html) {
  const out = [];
  // One entry per tag carrying a `data-cond-*` attribute: that is what the
  // builder binds its handlers to, and binding to something the app did not
  // write is how a harness passes while nothing works.
  const re = /<(input|select|button)\b([^>]*?)>/g;
  let m;
  while ((m = re.exec(html))) {
    const attrs = m[2];
    const data = {};
    for (const [, name, value] of attrs.matchAll(/data-([a-z-]+)="([^"]*)"/g)) {
      data[name.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    if (!Object.keys(data).length) continue;
    const value = (attrs.match(/ value="([^"]*)"/) || [])[1] || "";
    out.push({ tag: m[1], dataset: data, value, attrs,
               oninput: null, onchange: null, onclick: null });
  }
  return out;
}

const makeEl = (tag = "div") => {
  const node = {
    tag, textContent: "", hidden: false, className: "", value: "",
    dataset: {}, onclick: null, oninput: null, onchange: null,
    children: [], style: {},
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
    focus() {}, remove() {}, closest: () => null,
    appendChild(child) { node.children.push(child); return child; },
    insertBefore(child) { node.children.unshift(child); return child; },
    querySelector: () => null,
    /** Read back out of the app's own HTML, so a handler is bound to the thing
     *  the app wrote. Returning [] here is how a harness passes while nothing
     *  was ever wired. */
    querySelectorAll(sel) {
      const attr = (sel.match(/^\[([a-z-]+)\]$/) || [])[1];
      if (!attr) return [];
      const key = attr.replace(/^data-/, "")
        .replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      node.live = node.live || [];
      const out = node.live.filter((c) => key in c.dataset);
      return out;
    },
    get firstChild() { return node.children[0] || null; },
  };
  let html = "";
  Object.defineProperty(node, "innerHTML", {
    get: () => html,
    set(v) {
      html = String(v);
      node.children.length = 0;
      // Re-parsed on every write, exactly like a browser: a row the app just
      // re-rendered is a new object, and a handler bound to the old one is gone.
      node.live = parseChildren(html);
    },
  });
  return node;
};

const el = (sel) => {
  if (!registry.has(sel)) registry.set(sel, makeEl());
  return registry.get(sel);
};

globalThis.MutationObserver = class {
  observe() {} disconnect() {} takeRecords() { return []; }
};
globalThis.document = {
  querySelector: (s) => el(s), querySelectorAll: () => [],
  getElementById: (id) => el(`#${id}`), createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {},
};
const store = {};
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; },
};
globalThis.sessionStorage = { getItem: () => null, setItem() {} };

/** Every request, with its body, so a PATCH can be inspected. */
const calls = [];
globalThis.fetch = async (url, opts = {}) => {
  calls.push({ url: String(url), method: (opts && opts.method) || "GET",
               body: opts && opts.body ? JSON.parse(opts.body) : null });
  const method = (opts && opts.method) || "GET";
  if (failPatch && method === "PATCH") {
    // What `api()` does with a non-2xx, so the app's own error path runs.
    return { ok: false, status: 500, json: async () => ({ detail: "nope" }) };
  }
  let payload = {};
  if (String(url).endsWith("/api/automations/vocabulary")) payload = vocabulary;
  else if (String(url) === "/api/routines" && method === "POST") {
    payload = { id: "new-1", name: "Created" };
  } else if (/\/api\/automations\/[^/]+$/.test(url)) payload = automation;
  return { ok: true, status: 200, json: async () => payload };
};

new Function(appSource(path.dirname(APP_JS))
  + "\nglobalThis.__b = {openBuilder, addCondition, builderTrigger,"
  + " builderConditions, builderLegacy, saveBuilder, triggerWords,"
  + " renderConditions, renderReadback};"
  // Replaced after the app defined it, so the real save handler is the one
  // that runs — a harness that reimplements the handler tests itself.
  + "\ntoast = (m) => globalThis.__toasts.push(String(m));")();

/** Choose a value the way a person does: set it, then let the page react.
 *
 *  A browser fires `change` on a select, and the app hangs real work off that —
 *  the app list is redrawn from the kind of event chosen beside it. A harness
 *  that only assigned `.value` would test a page nobody had touched.
 */
function pick(selector, value) {
  const node = el(selector);
  node.value = value;
  if (typeof node.onchange === "function") node.onchange();
}

/** Type into the row the builder drew. */
function type(index, what, value) {
  const rows = el("#rmConditions").live || [];
  const key = what === "field" ? "condField"
    : what === "value" ? "condValue" : "condType";
  const target = rows.find((r) => r.dataset[key] === String(index));
  if (!target) throw new Error(`no ${what} input on row ${index}`);
  target.value = value;
  if (what === "type") { if (target.onchange) target.onchange(); }
  else if (target.oninput) target.oninput();
  return target;
}

let error = null;
const out = {};
try {
  // The script is a list of steps, so one harness covers every case rather
  // than one harness per assertion.
  for (const step of script) {
    if (step.op === "open") await globalThis.__b.openBuilder(step.existing || null);
    else if (step.op === "add") globalThis.__b.addCondition();
    else if (step.op === "type") type(step.row, step.what, step.value);
    else if (step.op === "trigger") pick("#rmTrigger", step.value);
    else if (step.op === "set") pick(step.sel, step.value);
    else if (step.op === "check") {
      const node = el(step.sel);
      node.checked = step.value !== false;
      if (typeof node.onchange === "function") node.onchange();
    }
    else if (step.op === "save") out.saved = await globalThis.__b.saveBuilder(step.id);
    else if (step.op === "create") {
      // The real handler, reached through the element the app bound it to.
      el("#rmName").value = step.name || "A new automation";
      el("#rmInstruction").value = step.instruction || "Do the thing.";
      el("#rmAgent").value = "personal";
      const press = el("#rmCreate").onclick;
      if (typeof press !== "function") throw new Error("#rmCreate has no handler");
      await press();
    }
  }
  if (typeof globalThis.__b.renderReadback === "function") {
    globalThis.__b.renderReadback();
  }
  out.trigger = globalThis.__b.builderTrigger();
  out.conditions = globalThis.__b.builderConditions();
  out.legacy = globalThis.__b.builderLegacy(out.trigger);
  out.words = globalThis.__b.triggerWords(out.trigger);
} catch (e) {
  error = `${e && e.constructor && e.constructor.name}: ${e && e.message}`;
}

process.stdout.write(JSON.stringify({
  error,
  calls,
  toasts: globalThis.__toasts,
  modalHidden: el("#routineModal").hidden,
  ...out,
  triggerHtml: el("#rmTrigger").innerHTML,
  conditionsHtml: el("#rmConditions").innerHTML,
  hint: el("#rmCondHint").textContent,
  triggerHint: el("#rmTriggerHint").textContent,
  readback: el("#rmReadback").innerHTML,
  zoneHtml: el("#rmZone").innerHTML,
  syncNote: el("#rmSyncNote").hidden ? "" : el("#rmSyncNote").textContent,
  showing: {
    event: !el("#rmEventWrap").hidden,
    schedule: !el("#rmDailyWrap").hidden,
    interval: !el("#rmIntervalWrap").hidden,
  },
  addHidden: el("#rmAddCond").hidden,
  sourceHtml: el("#rmEventSource").innerHTML,
  everyHtml: el("#rmInterval").innerHTML,
  eventKindHtml: el("#rmEventKind").innerHTML,
}));
