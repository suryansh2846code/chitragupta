/**
 * The composer's autonomy pill — opened, switched, and switched back.
 *
 * It edits the same setting as the profile's Persona tab, so what is asserted
 * is the seam: does it read the open agent's level, does picking one send the
 * right PUT, does the label move before the server answers (and come back if
 * the server refuses), and does switching agents re-read it rather than show
 * the level of the agent you just left.
 *
 * argv: <app.js>   stdout: {hidden, loaded, menu, picked, failed, perAgent}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const WEB = path.dirname(APP_JS);

function makeEl(tag = "div") {
  const el = {
    tag, value: "", hidden: false, disabled: false, title: "", children: [],
    _attrs: {}, _html: "", _text: "", id: "", style: {}, dataset: {},
    onclick: null, oninput: null, onchange: null, onkeydown: null,
    _classes: new Set(),
    appendChild(c) { el.children.push(c); return c; },
    append(...n) { for (const x of n) el.children.push(x); },
    insertBefore(c) { el.children.unshift(c); return c; },
    removeChild(c) { el.children = el.children.filter((x) => x !== c); },
    setAttribute(k, v) { el._attrs[k] = String(v); },
    getAttribute(k) { return el._attrs[k] ?? null; },
    removeAttribute(k) { delete el._attrs[k]; },
    addEventListener() {}, removeEventListener() {}, focus() {}, remove() {},
    closest: () => null, querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 10, height: 10 }),
  };
  Object.defineProperty(el, "className", {
    get: () => [...el._classes].join(" "),
    set: (v) => { el._classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
  });
  el.classList = {
    add: (c) => el._classes.add(c),
    remove: (c) => el._classes.delete(c),
    toggle: (c, on) => {
      const want = on === undefined ? !el._classes.has(c) : !!on;
      if (want) el._classes.add(c); else el._classes.delete(c);
    },
    contains: (c) => el._classes.has(c),
  };
  Object.defineProperty(el, "textContent", {
    get: () => el._text || el.children.map((c) => c.textContent).join(""),
    set: (v) => { el.children = []; el._html = ""; el._text = String(v); },
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html,
    set: (v) => { el.children = []; el._text = ""; el._html = String(v); },
  });
  return el;
}

const registry = new Map();
const el = (sel) => {
  if (!registry.has(sel)) registry.set(sel, makeEl());
  return registry.get(sel);
};

const AGENTS = [
  { id: "inbox", name: "Inbox", role: "Triage", custom: false },
  { id: "chotu", name: "Chotu", role: "Odd jobs", custom: true },
];

const LEVELS = [
  { key: "read_only", label: "Read only", blurb: "Looks and reports." },
  { key: "ask_first", label: "Ask before changing", blurb: "Asks first." },
  { key: "on_its_own", label: "Acts on its own", blurb: "Gets on with it." },
];

//: What each agent is on, so switching agents can be seen to re-read.
const STORED = { inbox: "ask_first", chotu: "read_only" };
let failNext = false;

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  createElement: (t) => makeEl(t),
  querySelector: (sel) => el(sel),
  querySelectorAll: () => [],
  getElementById: (id) => el(`#${id}`),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(), head: makeEl(),
  activeElement: null,
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {},
  innerWidth: 1280, innerHeight: 800,
};
Object.defineProperty(globalThis, "navigator", {
  value: { clipboard: { writeText: async () => {} } }, configurable: true,
});
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.confirm = () => true;

const requests = [];
globalThis.fetch = async (url, opts) => {
  const method = (opts && opts.method) || "GET";
  requests.push({ url, method, body: opts && opts.body });
  const json = (v) => ({ ok: true, json: async () => v });
  const m = /\/api\/agents\/([^/]+)\/persona$/.exec(url);
  if (m && method === "GET") {
    return json({
      persona: { traits: [], communication: [], thinking: [],
                 autonomy: STORED[m[1]] || "ask_first", extra: "" },
      vocabulary: { traits: [], communication: [], thinking: [],
                    autonomy: LEVELS,
                    limits: { traits: 4, communication: 3, thinking: 3, extra: 4000 },
                    default_autonomy: "ask_first" },
    });
  }
  if (m && method === "PUT") {
    if (failNext) return { ok: false, json: async () => ({ detail: "nope" }) };
    STORED[m[1]] = JSON.parse(opts.body).autonomy;
    return json({ persona: { autonomy: STORED[m[1]] } });
  }
  return json({});
};

globalThis.__AGENTS = AGENTS;
new Function(`${appSource(WEB)}
  ;agents = globalThis.__AGENTS; current = "inbox";
  ;globalThis.__loadAutonomy = loadAutonomy;
  ;globalThis.__setAutonomy = setAutonomy;
  ;globalThis.__toolsFor = () => AGENT_TOOLS_FOR;
`)();

// The markup ships both of these `hidden`, and the stub does not parse
// attributes — so without this the menu starts "open" and the first click
// closes it, which looks exactly like a pill that does not work.
el("#cmpAutoPill").hidden = true;
el("#cmpAutoMenu").hidden = true;

const out = {};
const tick = () => new Promise((r) => setTimeout(r, 0));
const pill = () => el("#cmpAutoPill");
const menu = () => el("#cmpAutoMenu");

try {
  // 1. Nothing known yet: the pill says nothing rather than guessing.
  out.hidden = {
    beforeAnyAgent: pill().hidden === true,
    // And the markup really does ship it hidden — the line above only proves
    // the fixture, this proves the page.
    inPage: /id="cmpAutoPill"[^>]*hidden/.test(
      fs.readFileSync(path.join(WEB, "index.html"), "utf8")),
  };

  // 2. It reads the open agent's level.
  await globalThis.__loadAutonomy("inbox");
  await tick();
  out.loaded = {
    shown: pill().hidden === false,
    label: el("#cmpAutoLabel")._text,
    rows: menu().children.length,
    onRow: menu().children.findIndex((r) => r.classList.contains("is-on")),
  };

  // 3. The pill opens the menu, and picking a level sends it.
  pill().onclick({ stopPropagation() {} });
  out.menuOpens = menu().hidden === false;
  requests.length = 0;
  await menu().children[2].onclick();
  await tick();
  const put = requests.find((r) => r.method === "PUT");
  out.picked = {
    url: put && put.url, body: put && JSON.parse(put.body),
    label: el("#cmpAutoLabel")._text,
    menuClosed: menu().hidden === true,
    // Read only strips tools, so anything showing this agent's permissions is
    // now stale. This is what makes that panel repaint instead of skipping.
    invalidatedTools: globalThis.__toolsFor() === "",
  };

  // 4. A refused save puts the label back rather than lying about the mode.
  failNext = true;
  await globalThis.__setAutonomy("read_only");
  await tick();
  out.failed = { label: el("#cmpAutoLabel")._text };
  failNext = false;

  // 5. Switching agents re-reads it. Showing the level of the agent you just
  //    left would be a label about somebody else.
  await globalThis.__loadAutonomy("chotu");
  await tick();
  out.perAgent = { chotu: el("#cmpAutoLabel")._text };
  await globalThis.__loadAutonomy("inbox");
  await tick();
  out.perAgent.backToInbox = el("#cmpAutoLabel")._text;
} catch (e) {
  out.error = String((e && e.stack) || e);
}

process.stdout.write(JSON.stringify(out));
