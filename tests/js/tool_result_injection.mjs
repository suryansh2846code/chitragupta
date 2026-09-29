/**
 * Execute app.js's message and trace rendering against attacker-written text.
 *
 * The server half of this is `tests/test_mcp_tool_result_injection.py`. This is
 * the half that half of the threat actually lands in: an MCP read tool's result
 * is rendered in the browser, and if the renderer that turns `<action …>` into a
 * Confirm card is ever pointed at a tool result instead of the model's reply, a
 * stranger's GitHub issue body becomes a button the user can click.
 *
 * `addTrace` and `addMsg` are executed for real — a source-order or
 * `node --check` assertion sees none of this.
 *
 * Reads {reply, toolResult} as JSON on stdin; writes
 * {actionCards, traceHtml, replyHtml, error}.
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];   // a path inside chitragupta/web/
const input = JSON.parse(fs.readFileSync(0, "utf8"));

// Every element appended to #messages, so we can tell a rendered action card
// from a plain message. `actionCard()` builds an element whose className starts
// with "acard"; a message is "msg assistant".
const appended = [];

const makeEl = (tag = "div") => {
  const e = {
    tag,
    innerHTML: "", textContent: "", className: "", value: "", hidden: false,
    style: {}, dataset: {}, scrollTop: 0, scrollHeight: 100, children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => makeEl(), querySelectorAll: () => [],
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
    focus() {}, remove() {}, closest: () => null,
    appendChild(child) { this.children.push(child); return child; },
    // A card puts its form above its buttons, and `.ac-actions` lives in the
    // card's own markup rather than in `children` — so the reference node is
    // never found here and appending is the honest answer. Without this the
    // card threw, the harness reported an error, and the two anti-vacuity
    // checks caught it: exactly what they are for.
    insertBefore(child, ref) {
      const at = ref ? this.children.indexOf(ref) : -1;
      if (at === -1) this.children.push(child);
      else this.children.splice(at, 0, child);
      return child;
    },
  };
  return e;
};

const messages = makeEl();
// Only #messages records, so we measure what the user would actually see.
messages.appendChild = (child) => { appended.push(child); return child; };
messages.querySelector = () => null;          // no hero-empty to remove

globalThis.MutationObserver = class {
  observe() {} disconnect() {} takeRecords() { return []; }
};

const byId = { messages };
globalThis.document = {
  querySelector: (sel) => (sel === "#messages" ? messages : makeEl()),
  querySelectorAll: () => [],
  getElementById: (id) => byId[id] || makeEl(),
  createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

// The whole workspace, in the order index.html loads it — one file today,
// several once app.js is split. `new Function` compiles a script, so every
// piece has to arrive in one shared scope; see tests/js/_app_source.mjs.
const src = appSource(path.dirname(APP_JS));
let error = null;
try {
  new Function(
    src +
    "\nglobalThis.__addMsg = addMsg;" +
    "\nglobalThis.__addTrace = addTrace;" +
    "\nglobalThis.__parseActions = parseActions;" +
    "\nglobalThis.__setAgents = (a, c) => { agents = a; current = c; };"
  )();
  globalThis.__setAgents([{ id: "research", name: "Research" }], "research");
} catch (e) {
  error = String(e && e.stack ? e.stack : e);
}

/** Count action cards by CLASS, not by position.
 *
 * An earlier version measured `appended.length` before and after the `addMsg`
 * call, which counted only cards created in that window — so a mutation that
 * made `addTrace` render cards was invisible, and the test that exists to catch
 * exactly that passed. Classify every element instead, in both phases.
 */
const cardsIn = (els) => els.filter((e) => (e.className || "").includes("action-card")).length;

let traceHtml = "", replyHtml = "";
let cardsFromTrace = null, cardsFromReply = null;
if (!error) {
  try {
    // 1. The tool result, rendered the way a real turn renders it.
    globalThis.__addTrace([
      { kind: "tool_call", name: "github_get_issue", arguments: { id: 42 } },
      { kind: "tool_result", name: "github_get_issue", result: input.toolResult },
    ]);
    const traceEls = appended.slice();
    traceHtml = traceEls.map((e) => e.innerHTML || "").join("");
    cardsFromTrace = cardsIn(traceEls);

    const afterTrace = appended.length;

    // 2. The model's own reply.
    const el = globalThis.__addMsg("assistant", input.reply);
    replyHtml = el && el.innerHTML ? el.innerHTML : "";
    cardsFromReply = cardsIn(appended.slice(afterTrace));
  } catch (e) {
    error = String(e && e.stack ? e.stack : e);
  }
}

process.stdout.write(JSON.stringify({
  // Cards a stranger's tool result produced. Must always be 0.
  actionCardsFromTrace: cardsFromTrace,
  // Cards the model's own reply produced. Legitimately 1 when it proposes one.
  actionCards: cardsFromReply,
  traceHtml,
  replyHtml,
  parsedFromToolResult: error
    ? null
    : globalThis.__parseActions(input.toolResult).actions.length,
  error,
}));
