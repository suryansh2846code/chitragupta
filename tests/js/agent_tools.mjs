/**
 * Execute the real per-agent tools screen and report what landed.
 *
 * This is the screen built because an agent silently had no connector access
 * and told the user the connector was still syncing. Everything worth checking
 * about it is a property of the DOM it produces — which group a tool landed
 * in, whether a row is a switch or a reason, what the switch sent — and none
 * of that is visible to a source-order assertion. `node --check` passes on a
 * temporal-dead-zone ReferenceError, which is how a whole panel once rendered
 * blank while every test passed.
 *
 * argv: <a path inside chitragupta/web/>
 * stdin: {agent, tools, connectors, toggle?, failSave?}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, "utf8"));

const calls = [];
let openedConnectors = 0;

const makeEl = (tag = "div") => {
  const el = {
    tag, innerHTML: "", className: "", value: "", hidden: false, disabled: false,
    title: "", textContent: "", style: {}, dataset: {}, _attrs: {}, onclick: null,
    onchange: null, _nodes: [],
    classList: {
      _on: new Set(),
      add(c) { this._on.add(c); }, remove(c) { this._on.delete(c); },
      toggle(c, v) { if (v) this._on.add(c); else this._on.delete(c); },
      contains(c) { return this._on.has(c); },
    },
    // Reassigning innerHTML detaches the subtree — modelling that is the
    // point, because writing into a container a re-render replaced is the bug
    // these harnesses exist for.
    querySelectorAll(sel) { return (this._nodes || []).filter((n) => n._sel === sel); },
    querySelector(sel) { return (this._nodes || []).find((n) => n._sel === sel) || null; },
    closest() { return el._row || null; },
    addEventListener() {}, appendChild() {},
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k] ?? null; },
    focus() {}, remove() {},
  };
  return el;
};

// The box the screen renders into. Its querySelectorAll has to return real
// button objects, because the test clicks one.
const box = makeEl();
const parsedButtons = () => {
  // Parse the rendered HTML for the controls, and hand back live objects whose
  // clicks run the handlers the renderer bound.
  const out = { "[data-tool]": [], "[data-bulk]": [], "[data-tool-fix]": [], ".at-err": [] };
  for (const m of box.innerHTML.matchAll(/data-tool="([^"]*)"[^>]*data-on="([^"]*)"/g)) {
    const b = makeEl("button");
    b._sel = "[data-tool]";
    b.dataset = { tool: m[1], on: m[2] };
    const err = makeEl("span"); err._sel = ".at-err"; err.hidden = true;
    const row = makeEl("div"); row._nodes = [err];
    b._row = row;
    b.closest = () => row;
    out["[data-tool]"].push(b);
  }
  for (const m of box.innerHTML.matchAll(/data-bulk="([^"]*)"[^>]*data-on="([^"]*)"/g)) {
    const b = makeEl("button");
    b._sel = "[data-bulk]";
    b.dataset = { bulk: m[1], on: m[2] };
    // `toggleToolBucket` walks up to the group to bring the per-tool rows
    // with it; a harness that returned null here would silently skip that.
    const section = makeEl("section");
    section._nodes = [];
    section.querySelector = () => null;
    b.closest = () => section;
    out["[data-bulk]"].push(b);
  }
  for (const _ of box.innerHTML.matchAll(/data-tool-fix="1"/g)) {
    const b = makeEl("button"); b._sel = "[data-tool-fix]";
    out["[data-tool-fix]"].push(b);
  }
  return out;
};
let buttons = { "[data-tool]": [], "[data-bulk]": [], "[data-tool-fix]": [] };
box.querySelectorAll = (sel) => buttons[sel] || [];

const registry = new Map();
const el = (sel) => {
  if (sel === "#agentToolList") return box;
  if (!registry.has(sel)) registry.set(sel, makeEl());
  return registry.get(sel);
};

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: (s) => el(s), querySelectorAll: () => [],
  getElementById: (i) => el(`#${i}`), createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = { location: { pathname: "/", href: "/" }, addEventListener() {},
                      matchMedia: () => ({ matches: false, addEventListener() {} }), open() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.fetch = async (p, opts = {}) => {
  calls.push({ path: String(p), method: opts.method || "GET",
               body: opts.body ? JSON.parse(opts.body) : null });
  if (input.failSave && String(p).includes("/tools") && opts.method === "PATCH") {
    return { ok: false, status: 500, json: async () => ({ detail: "nope" }) };
  }
  if (String(p).includes("/tools") && opts.method === "PATCH") {
    return { ok: true, json: async () => ({ id: input.agent.id, name: input.agent.name,
                                            tools: JSON.parse(opts.body).tools }) };
  }
  return { ok: true, json: async () => ({}) };
};

new Function(
  appSource(path.dirname(APP_JS)) +
  "\nglobalThis.__render = renderAgentTools;" +
  "\nglobalThis.__groups = agentToolGroups;" +
  "\nglobalThis.__agents = (a) => { agents = a; };" +
  // Assigning globalThis.openConnectorsScreen cannot shadow a function
  // DECLARATION in the evaluated scope, so the swap has to happen inside it.
  "\nglobalThis.__spyConnectors = (fn) => { openConnectorsScreen = fn; };"
)();

globalThis.__agents([input.agent]);
// The screen calls openConnectorsScreen for every "fix this" affordance; count
// it rather than opening a panel that does not exist in a harness.
globalThis.__spyConnectors(() => { openedConnectors += 1; });

const agent = JSON.parse(JSON.stringify(input.agent));
const specs = input.groups || [];
globalThis.__render(box, { agent, tools: input.tools, connectors: input.connectors, categories: input.categories, specs });
const firstHtml = box.innerHTML;
buttons = parsedButtons();

// Render again so wireAgentToolActions binds onto the button objects the
// first pass produced. Do NOT re-parse afterwards: fresh objects would be
// unbound, which is the harness losing the handlers rather than the app
// failing to set them.
globalThis.__render(box, { agent, tools: input.tools, connectors: input.connectors, categories: input.categories, specs });

let bulked = null;
if (input.bulk) {
  const btn = buttons["[data-bulk]"].find((b) => b.dataset.bulk === input.bulk);
  if (btn && typeof btn.onclick === "function") {
    await btn.onclick();
    const patch = calls.filter((c) => c.method === "PATCH").pop();
    bulked = {
      sent: patch ? patch.body.tools : null,
      patches: calls.filter((c) => c.method === "PATCH").length,
      onAfter: btn.dataset.on === "1",
    };
  }
}

let toggled = null;
let rowError = "";
if (input.toggle) {
  const btn = buttons["[data-tool]"].find((b) => b.dataset.tool === input.toggle);
  if (btn && typeof btn.onclick === "function") {
    await btn.onclick();
    const patch = calls.filter((c) => c.method === "PATCH").pop();
    toggled = {
      sent: patch ? patch.body.tools : null,
      path: patch ? patch.path : null,
      onAfter: btn.dataset.on === "1",
      ariaAfter: btn.getAttribute("aria-checked"),
    };
    const err = btn.closest().querySelector(".at-err");
    rowError = err && err.hidden === false ? err.textContent : "";
  }
}

if (input.clickFix && buttons["[data-tool-fix]"].length) {
  const b = buttons["[data-tool-fix]"][0];
  if (typeof b.onclick === "function") b.onclick();
}

process.stdout.write(JSON.stringify({
  html: firstHtml,
  bulked,
  groups: globalThis.__groups(input.tools, input.connectors, input.agent.tools, input.categories, specs)
    .map((g) => ({ name: g.name, kind: g.kind, tools: g.tools.map((t) => t.row.name) })),
  toggled, rowError, openedConnectors,
  agentToolsAfter: agent.tools,
  calls,
}));
process.exit(0);
