/**
 * Execute the Create-an-agent modal and report what it drew.
 *
 * This screen shipped unreadable: `.am-body input` was in the shared form-field
 * rule, and a tool row's checkbox IS an input inside `.am-body` — so all
 * thirty-odd of them rendered as full-width padded boxes with the label
 * squeezed down to its first letter. A second rule, `.am-body label`, matched
 * the rows too and uppercased them. Both are CSS, and CSS is not what a
 * harness sees — but the DOM the renderer produces is, and every claim worth
 * making about this modal is a property of that DOM: which group a tool landed
 * in, whether its control is a switch or a reason, and what Create reads back.
 *
 * It now draws the **Agents & tools panel itself** — one renderer, so the
 * the card sentences and the roll-ups cannot land on one screen and
 * not the other. The agent being built is a DRAFT: every switch moves the
 * object on screen and sends nothing, and Create posts what is in it.
 *
 * `workspace.js` loads BEFORE `tools.js`, and this modal calls into it. That
 * works only because nothing calls it until after every script has run — so the
 * modal is opened here for real, in the real load order, rather than reasoned
 * about.
 *
 * argv: <a path inside chitragupta/web/>
 * stdin: {tools, categories, connectors, groups?, apps?, toggle?, bulk?}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, "utf8"));

const calls = [];
let posted = null;

const makeEl = (tag = "div") => ({
  tag, innerHTML: "", className: "", value: "", hidden: true, textContent: "",
  style: {}, dataset: {}, _attrs: {}, onclick: null,
  classList: {
    _on: new Set(),
    add(c) { this._on.add(c); }, remove(c) { this._on.delete(c); },
    toggle(c, v) { if (v) this._on.add(c); else this._on.delete(c); },
    contains(c) { return this._on.has(c); },
  },
  querySelectorAll: () => [], querySelector: () => null,
  setAttribute(k, v) { this._attrs[k] = v; },
  getAttribute(k) { return this._attrs[k] ?? null; },
  addEventListener() {}, appendChild() {}, focus() {}, remove() {}, closest: () => null,
});

const els = {};
const el = (sel) => (els[sel] ||= makeEl());

// The controls the renderer writes into #amTools. Parsed back out of the HTML
// so a click runs the handler the renderer actually bound to it, and re-parsed
// whenever the draft repaints — a draft switch re-renders, and handlers bound
// to the previous nodes are handlers on nothing the user can click.
const box = el("#amTools");
let controls = {};
let parsedFor = null;
const reparse = () => {
  if (parsedFor === box.innerHTML) return controls;
  parsedFor = box.innerHTML;
  const made = (sel, re, fields) => [...box.innerHTML.matchAll(re)].map((m) => {
    const b = makeEl("button");
    b.dataset = {};
    fields.forEach((f, i) => { b.dataset[f] = m[i + 1]; });
    return b;
  });
  controls = {
    "[data-tool]": made("[data-tool]",
      /data-tool="([^"]*)"\s+data-on="([^"]*)"/g, ["tool", "on"]),
    "[data-bulk]": made("[data-bulk]",
      /data-bulk="([^"]*)"\s*\n?\s*data-on="([^"]*)"/g, ["bulk", "on"]),
    "[data-screen]": made("[data-screen]", /data-screen="([^"]*)"/g, ["screen"]),
    "[data-group]": made("[data-group]", /data-group="([^"]*)"/g, ["group"]),
    "[data-tool-fix]": made("[data-tool-fix]", /data-tool-fix="(1)"/g, ["toolFix"]),
  };
  return controls;
};
const parsed = (sel) => reparse()[sel] || [];
box.querySelectorAll = (sel) => parsed(sel);

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: (sel) => el(sel),
  querySelectorAll: (sel) => {
    if (sel === "#amTools [data-tool]") return parsed("[data-tool]");
    if (sel === '#amTools [data-tool][data-on="1"]') {
      return parsed("[data-tool]").filter((b) => b.dataset.on === "1");
    }
    return [];
  },
  getElementById: (id) => el(`#${id}`),
  createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
};
// `CSS.escape` exists in every browser and in no harness. Without it the
// renderer's own selector building throws — inside a `try`, so the bulk toggle
// silently ran its failure branch and every assertion about the request still
// passed. A harness that does not define what the page defines is a harness
// testing a different program.
globalThis.CSS = { escape: (s) => String(s).replace(/["\\]/g, "\\$&") };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.fetch = async (url, opts = {}) => {
  calls.push(url);
  if (String(url).startsWith("/api/agents/tools")) {
    return { ok: true, json: async () => ({
      tools: input.tools, categories: input.categories,
      groups: input.groups || [], apps: input.apps || [] }) };
  }
  if (String(url) === "/api/agents/custom" && opts.method === "POST") {
    posted = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ id: "new" }) };
  }
  return { ok: true, json: async () => ({}) };
};

let error = null;
const report = { error: null, groups: [], html: "", count: "", posted: null, opened: false };
try {
  globalThis.__connectors = input.connectors || [];
  new Function(
    appSource(path.dirname(APP_JS)) +
    // The rail and the turn are not what this is about, and both would fetch.
    "\nloadAgents = async () => {};" +
    "\nselectAgent = async () => {};" +
    "\nCONNECTORS = globalThis.__connectors;" +
    "\nglobalThis.__open = openAgentModal;" +
    "\nglobalThis.__create = () => document.getElementById('amCreate').onclick();"
  ).call(globalThis);

  await globalThis.__open();
  reparse();

  report.opened = el("#agentModal").hidden === false;
  report.html = box.innerHTML;
  report.count = el("#amToolCount").textContent;

  // Read the groups back out of what was drawn, not out of the input.
  for (const m of report.html.matchAll(
    /<h3 class="at-group-nm">([\s\S]*?)<\/h3>([\s\S]*?)(?=<section class="at-group|$)/g)) {
    report.groups.push({
      name: m[1],
      // The per-tool rows only. A tier switch and an "it asks you every time"
      // row both carry `.at-nm` too, and they are not tools — reading every
      // one of them made "Read" and "Change anything" look like skills.
      tools: [...m[2].matchAll(
        /<div class="at-row"[\s\S]*?<span class="at-nm">([\s\S]*?)<\/span>/g)]
        .map((x) => x[1]),
      switches: [...m[2].matchAll(/data-tool="([^"]*)"/g)].map((x) => x[1]),
      blocked: [...m[2].matchAll(/<span class="at-blocked">([\s\S]*?)<\/span>/g)].map((x) => x[1]),
    });
  }

  // Each press runs the handler the renderer bound, and a draft press repaints
  // — so the count and the HTML are re-read afterwards rather than before.
  const press = (sel, key, field) => {
    const b = parsed(sel).find((x) => x.dataset[field] === key);
    if (!b || typeof b.onclick !== "function") return false;
    b.onclick();
    report.html = box.innerHTML;
    report.count = el("#amToolCount").textContent;
    return true;
  };
  if (input.bulk) report.pressedBulk = press("[data-bulk]", input.bulk, "bulk");
  if (input.toggle) report.pressedToggle = press("[data-tool]", input.toggle, "tool");

  el("#amName").value = "Sales";
  el("#amRole").value = "outreach";
  el("#amPrompt").value = "be helpful";
  await globalThis.__create();
  report.posted = posted;
} catch (e) {
  error = String((e && e.stack) || e);
}
report.error = error;
report.calls = calls;
process.stdout.write(JSON.stringify(report));
