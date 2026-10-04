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
 * stdin: {agent, tools, connectors, apps?, panel?, toggle?, clickScreen?,
 *          failSave?}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, "utf8"));

const calls = [];
let openedConnectors = 0;
//: Was the profile already closed by the time the screen opened? Recorded at
//: the moment of the navigation, not after it: the bug was that the page
//: changed *behind* a dialog that stayed up, and only the ordering shows it.
let profileWasOpenOnNavigate = null;

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
  const out = { "[data-tool]": [], "[data-bulk]": [],
                "[data-tool-fix]": [], "[data-group]": [], "[data-screen]": [],
                "[data-folder-add]": [], "[data-folder-off]": [],
                "[data-grant]": [], ".at-err": [] };
  // One section standing in for the group a control sits in. Both directions
  // of the "the screen must not argue with itself" rule walk up to it — a
  // bucket switch to reach the rows under it, a row to reach the switches above
  // it — so a harness that returned null here would silently skip the half
  // being tested.
  const section = makeEl("section");
  section._nodes = [];
  for (const m of box.innerHTML.matchAll(/data-tool="([^"]*)"[^>]*data-on="([^"]*)"/g)) {
    const b = makeEl("button");
    b._sel = "[data-tool]";
    b.dataset = { tool: m[1], on: m[2] };
    const err = makeEl("span"); err._sel = ".at-err"; err.hidden = true;
    const row = makeEl("div"); row._nodes = [err];
    b._row = row;
    b.closest = (sel) => (sel === ".at-group" ? section : row);
    section._nodes.push(b);
    out["[data-tool]"].push(b);
  }
  // The whole tag, because the class carries whether a tier is half-granted —
  // a state the screen must not render as a plain "off" — and a harness that
  // dropped the class would test the wrong branch of that.
  for (const m of box.innerHTML.matchAll(
      /<button[^>]*data-bulk="([^"]*)"[^>]*data-on="([^"]*)"[^>]*>([\s\S]*?)<\/button>/g)) {
    const b = makeEl("button");
    b._sel = "[data-bulk]";
    b.dataset = { bulk: m[1], on: m[2] };
    // What it says NOW, so a label left stale by a press shows up as itself.
    b.textContent = m[3].replace(/<[^>]*>/g, "").trim();
    const cls = /class="([^"]*)"/.exec(m[0]);
    if (cls) cls[1].split(/\s+/).filter(Boolean).forEach((c) => b.classList.add(c));
    // The "n of m on" beside the switch is as wrong when stale as the switch.
    const part = makeEl("span"); part._sel = ".at-part"; part.textContent = "";
    const wrap = makeEl("div"); wrap._nodes = [part];
    b._part = part;
    b.closest = (sel) => (sel === ".at-bulk" ? wrap : section);
    section._nodes.push(b);
    out["[data-bulk]"].push(b);
  }
  for (const m of box.innerHTML.matchAll(/data-group="([^"]*)"/g)) {
    const d = makeEl("details");
    d._sel = "[data-group]";
    d.dataset = { group: m[1] };
    // Whether it was drawn open is read off the attribute, so a repaint that
    // forgot the user's disclosure shows up here rather than in a screenshot.
    // A plain substring, not a RegExp: a connector's label is its own and may
    // contain characters a pattern would read as syntax.
    d.open = box.innerHTML.includes(`data-group="${m[1]}" open`);
    out["[data-group]"].push(d);
  }
  for (const m of box.innerHTML.matchAll(/data-screen="([^"]*)"/g)) {
    const b = makeEl("button");
    b._sel = "[data-screen]";
    b.dataset = { screen: m[1] };
    out["[data-screen]"].push(b);
  }
  // The three controls that are not tool switches: the folder a user opens to
  // agents, closing one again, and whether this agent may reach a connector
  // without asking. Each had a working endpoint and no screen at all.
  for (const _ of box.innerHTML.matchAll(/data-folder-add="1"/g)) {
    const b = makeEl("button"); b._sel = "[data-folder-add]";
    out["[data-folder-add]"].push(b);
  }
  for (const m of box.innerHTML.matchAll(/data-folder-off="([^"]*)"/g)) {
    const b = makeEl("button"); b._sel = "[data-folder-off]";
    b.dataset = { folderOff: m[1] };
    out["[data-folder-off]"].push(b);
  }
  for (const m of box.innerHTML.matchAll(/data-grant="([^"]*)"[^>]*data-on="([^"]*)"/g)) {
    const b = makeEl("button"); b._sel = "[data-grant]";
    b.dataset = { grant: m[1], on: m[2] };
    out["[data-grant]"].push(b);
  }
  for (const _ of box.innerHTML.matchAll(/data-tool-fix="1"/g)) {
    const b = makeEl("button"); b._sel = "[data-tool-fix]";
    out["[data-tool-fix]"].push(b);
  }
  return out;
};
let buttons = { "[data-tool]": [], "[data-bulk]": [],
                "[data-tool-fix]": [], "[data-group]": [], "[data-screen]": [],
                "[data-folder-add]": [], "[data-folder-off]": [], "[data-grant]": [] };
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
// `CSS.escape` exists in every browser and in no harness. Without it the
// renderer's own selector building throws — inside a `try`, so the bulk toggle
// silently ran its failure branch and every assertion about the request still
// passed. A harness that does not define what the page defines is a harness
// testing a different program.
globalThis.CSS = { escape: (s) => String(s).replace(/["\\]/g, "\\$&") };
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
  // The other half of three permissions. `renderAgentTools` reads it and
  // `loadAgentTools` fills it, so a harness that renders directly has to set
  // it — otherwise every strip is the "not back yet" branch and the controls
  // under test are never drawn.
  "\nglobalThis.__panel = (p) => { _PANEL = p; };" +
  "\nglobalThis.__agents = (a) => { agents = a; };" +
  // Assigning globalThis.openConnectorsScreen cannot shadow a function
  // DECLARATION in the evaluated scope, so the swap has to happen inside it.
  "\nglobalThis.__spyConnectors = (fn) => { openConnectorsScreen = fn; };"
)();

globalThis.__agents([input.agent]);
// The screen calls openConnectorsScreen for every "fix this" affordance; count
// it rather than opening a panel that does not exist in a harness.
globalThis.__spyConnectors(() => {
  openedConnectors += 1;
  const bg = el("#agentProfile");
  profileWasOpenOnNavigate = bg.hidden === false;
});

const agent = JSON.parse(JSON.stringify(input.agent));
const specs = input.groups || [];
const apps = input.apps || [];
if (input.panel) globalThis.__panel({ forAgent: input.agent.id, ...input.panel });
globalThis.__render(box, { agent, tools: input.tools, connectors: input.connectors, categories: input.categories, specs, apps });
const firstHtml = box.innerHTML;
buttons = parsedButtons();

// Render again so wireAgentToolActions binds onto the button objects the
// first pass produced. Do NOT re-parse afterwards: fresh objects would be
// unbound, which is the harness losing the handlers rather than the app
// failing to set them.
globalThis.__render(box, { agent, tools: input.tools, connectors: input.connectors, categories: input.categories, specs, apps });

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

// The switches ABOVE the row that was pressed. Turning one browser tool on
// inside the disclosure used to leave the switch over it reading a plain "off".
const groupAfter = buttons["[data-bulk]"].map((b) => ({
  bulk: b.dataset.bulk, on: b.dataset.on === "1",
  some: b.classList.contains("is-some"),
  part: (b._part && b._part.textContent) || "",
  label: b.textContent,
}));

// A disclosure the user opened, and whether a repaint shuts it. Opened through
// the handler the renderer bound, which is the mechanism under test.
let reopened = null;
if (input.openGroup) {
  const d = buttons["[data-group]"].find((x) => x.dataset.group === input.openGroup);
  if (d && typeof d.ontoggle === "function") {
    d.open = true;
    d.ontoggle();
    globalThis.__render(box, { agent, tools: input.tools, connectors: input.connectors,
                              categories: input.categories, specs, apps });
    reopened = box.innerHTML.includes(`data-group="${input.openGroup}" open`);
  }
}

// The controls that are not tool switches. Each one is a permission that used
// to have an endpoint nothing called.
let folderAdded = null;
if (input.addFolder) {
  registry.set("#atFolderPath", makeEl("input"));
  registry.get("#atFolderPath").value = input.addFolder;
  const b = buttons["[data-folder-add]"][0];
  if (b && typeof b.onclick === "function") {
    await b.onclick();
    const post = calls.filter((c) => c.path.includes("/folders") && c.method === "POST").pop();
    folderAdded = post ? { path: post.path, body: post.body } : null;
  }
}
let screenClicked = null;
if (input.clickScreen) {
  el("#agentProfile").hidden = false;            // the profile this is a tab in
  const b = buttons["[data-screen]"].find((x) => x.dataset.screen === input.clickScreen);
  if (b && typeof b.onclick === "function") {
    b.onclick();
    screenClicked = { profileStillOpen: profileWasOpenOnNavigate,
                      closedAfter: el("#agentProfile").hidden === true };
  }
}

let grantToggled = null;
if (input.toggleGrant) {
  const b = buttons["[data-grant]"].find((x) => x.dataset.grant === input.toggleGrant);
  if (b && typeof b.onclick === "function") {
    await b.onclick();
    const hit = calls.filter((c) => c.path.includes("/connectors")).pop();
    grantToggled = hit ? { path: hit.path, method: hit.method, body: hit.body } : null;
  }
}

if (input.clickFix && buttons["[data-tool-fix]"].length) {
  const b = buttons["[data-tool-fix]"][0];
  if (typeof b.onclick === "function") b.onclick();
}

process.stdout.write(JSON.stringify({
  html: firstHtml,
  bulked, folderAdded, grantToggled, screenClicked,
  groups: globalThis.__groups(input.tools, input.connectors, input.agent.tools,
                              input.categories, specs, apps)
    .map((g) => ({ name: g.name, kind: g.kind, tools: g.tools.map((t) => t.row.name) })),
  toggled, rowError, openedConnectors, groupAfter, reopened,
  screens: buttons["[data-screen]"].map((b) => b.dataset.screen),
  agentToolsAfter: agent.tools,
  calls,
}));
process.exit(0);
