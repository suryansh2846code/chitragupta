/**
 * Draw the Inbox message list and press one.
 *
 * The claim is not that the list renders. It is that a long message can be
 * **read** — it used to be clamped with nothing to press, so the text ran out
 * mid-word and there was nowhere else to go for the rest. Pressing a row has to
 * open it and mark it read, and pressing the buttons inside a row must not.
 *
 * The whole page is evaluated in the order `index.html` loads it, because a
 * harness that evaluates one file passes while the real page is broken by
 * something another script declared first.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {messages, unread, script}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const { messages, unread = 0, script = [] } = JSON.parse(fs.readFileSync(0, "utf8"));

const registry = new Map();

/** Rows the app wrote, as objects a test can press. */
function parseRows(html) {
  const out = [];
  const re = /<div class="([^"]*)"[^>]*data-msg="([^"]*)"/g;
  let m;
  while ((m = re.exec(html))) {
    const classes = new Set(m[1].split(/\s+/).filter(Boolean));
    out.push({
      dataset: { msg: m[2] },
      onclick: null,
      classList: {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        contains: (c) => classes.has(c),
        toggle: (c, on) => (on === undefined
          ? (classes.has(c) ? classes.delete(c) : classes.add(c))
          : (on ? classes.add(c) : classes.delete(c))),
      },
      _classes: classes,
    });
  }
  return out;
}

/** Buttons inside the list, by their data attribute. */
function parseButtons(html, attr) {
  const out = [];
  const re = new RegExp(`${attr}="([^"]*)"`, "g");
  let m;
  while ((m = re.exec(html))) {
    out.push({ dataset: { [attr.replace(/^data-/, "").replace(/-([a-z])/g,
      (_, c) => c.toUpperCase())]: m[1] }, onclick: null });
  }
  return out;
}

const makeEl = (tag = "div") => {
  const node = {
    tag, textContent: "", hidden: false, className: "", value: "",
    dataset: {}, onclick: null, children: [], style: {},
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    addEventListener() {}, setAttribute() {}, getAttribute: () => null,
    focus() {}, remove() {}, closest: () => null,
    appendChild(c) { node.children.push(c); return c; },
    insertBefore(c) { node.children.unshift(c); return c; },
    querySelector: () => null,
    querySelectorAll(sel) {
      const attr = (sel.match(/^\[([a-z-]+)\]$/) || [])[1];
      if (!attr) return [];
      if (attr === "data-msg") return (node.rows = node.rows || []);
      return parseButtons(node.innerHTML, attr).map((b) => {
        (node.pressable = node.pressable || []).push(b);
        return b;
      });
    },
    get firstChild() { return node.children[0] || null; },
  };
  let html = "";
  Object.defineProperty(node, "innerHTML", {
    get: () => html,
    set(v) {
      html = String(v);
      node.children.length = 0;
      node.rows = parseRows(html);
      node.pressable = [];
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

const calls = [];
globalThis.fetch = async (url, opts = {}) => {
  calls.push({ url: String(url), method: (opts && opts.method) || "GET" });
  if (String(url).startsWith("/api/messages") && !opts.method) {
    return { ok: true, status: 200, json: async () => ({ messages, unread }) };
  }
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};

new Function(appSource(path.dirname(APP_JS))
  + "\nglobalThis.__m = {loadMessages};")();

function row(id) {
  const found = (el("#messageList").rows || []).find((r) => r.dataset.msg === id);
  if (!found) throw new Error(`no row for ${id}`);
  return found;
}

let error = null;
const opened = {};
try {
  await globalThis.__m.loadMessages();
  for (const step of script) {
    if (step.op === "press") {
      // A press on the row itself: nothing inside it was the target.
      await row(step.id).onclick({ target: { closest: () => null } });
    } else if (step.op === "pressButton") {
      // A press that landed on a button inside the row.
      await row(step.id).onclick({ target: { closest: () => ({}) } });
    }
    opened[step.id] = row(step.id).classList.contains("is-open");
  }
} catch (e) {
  error = `${e && e.constructor && e.constructor.name}: ${e && e.message}`;
}

process.stdout.write(JSON.stringify({
  error,
  calls,
  opened,
  html: el("#messageList").innerHTML,
  unreadBadge: el("#navUnread").hidden ? "" : el("#navUnread").textContent,
  readAllHidden: el("#msgReadAll").hidden,
  states: (el("#messageList").rows || []).map((r) => ({
    id: r.dataset.msg,
    open: r.classList.contains("is-open"),
    unread: r.classList.contains("is-unread"),
  })),
}));
