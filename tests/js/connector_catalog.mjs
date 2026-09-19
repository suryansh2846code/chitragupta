/**
 * Execute the connector catalog's real render paths and report what landed.
 *
 * `node --check` validates syntax and nothing else — a temporal-dead-zone
 * ReferenceError passes it and silently empties a drawer, which is exactly how
 * the Models panel broke once. So this calls `loadConnectorCatalog()` and
 * `connectorPermissions()` for real and reads what they wrote.
 *
 * The DOM stub models the two things that have caught bugs here before:
 * reassigning `innerHTML` clears the subtree (so a card written into a
 * container a re-render already replaced is detectable), and every
 * `textContent` write is recorded, because a handler's own catch can wipe the
 * error it just set before any assertion sees it.
 *
 * Reads a scenario as JSON on stdin, writes one JSON result to stdout.
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];   // a path inside chitragupta/web/
const scenario = JSON.parse(fs.readFileSync(0, "utf8"));

const textWrites = [];
const registry = new Map();

//: Recorded, not fatal. A flow that ends by refreshing a panel does it
//: fire-and-forget, so an unanswered request lands as a rejection AFTER the
//: result has been written — which killed the process and truncated stdout
//: into invalid JSON, making a passing flow look like a broken harness.
//: Reported instead, so a real one is still visible.
const unhandled = [];
process.on("unhandledRejection", (reason) => {
  unhandled.push(String(reason && reason.message ? reason.message : reason));
});

function makeEl(id = "") {
  const children = [];
  const el = {
    id,
    _html: "",
    value: "",
    hidden: false,
    disabled: false,
    title: "",
    style: {},
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {},
    appendChild(c) { children.push(c); },
    setAttribute() {},
    getAttribute: () => null,
    focus() {},
    remove() {},
    closest: () => null,
    get innerHTML() { return el._html; },
    set innerHTML(v) {
      // Detachment is modelled deliberately: anything previously written into
      // this container is gone, which is the bug class this harness exists for.
      el._html = String(v);
      children.length = 0;
    },
    get textContent() { return el._text || ""; },
    set textContent(v) {
      el._text = String(v);
      textWrites.push({ id: el.id, text: String(v) });
    },
    querySelectorAll(sel) {
      // Buttons are discovered by data-attribute in the real code; the stub
      // hands back one element per match found in the HTML just written.
      const attr = /\[data-([a-zA-Z]+)\]/.exec(sel);
      if (!attr) return [];
      const re = new RegExp(`data-${attr[1]}="([^"]*)"`, "g");
      const out = [];
      let m;
      while ((m = re.exec(el._html))) {
        const b = makeEl();
        b.dataset[attr[1]] = m[1];
        b.disabled = el._html.includes(`data-${attr[1]}="${m[1]}"\n        disabled`)
          || new RegExp(`data-${attr[1]}="${m[1]}"[^>]*disabled`).test(el._html);
        out.push(b);
      }
      return out;
    },
    querySelector: () => null,
  };
  return el;
}

function elFor(sel) {
  const key = String(sel).replace(/^#/, "");
  if (!registry.has(key)) registry.set(key, makeEl(key));
  return registry.get(key);
}

// app.js installs a focus-management observer at load; every harness that
// evaluates the file needs one to exist, or the whole script throws before the
// function under test is ever reached.
globalThis.MutationObserver = class {
  observe() {}
  disconnect() {}
  takeRecords() { return []; }
};

globalThis.document = {
  querySelector: (s) => elFor(s),
  querySelectorAll: () => [],
  getElementById: (s) => elFor(s),
  createElement: () => makeEl(),
  addEventListener() {},
  body: makeEl(),
  documentElement: makeEl(),
};
globalThis.window = {
  location: { pathname: "/", href: "/" },
  addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.confirm = () => true;
globalThis.setTimeout = (fn) => fn;

// Faked at `fetch`, not at `api()`. `api` is a `const`, but more importantly
// stubbing it would skip its own error handling — which is part of what these
// paths depend on when a request fails.
const calls = [];
//: Bodies as well as paths. A sign-in flow's whole claim is about WHAT it
//: posted at each step, and a path list cannot tell a phone number from a
//: login code.
const posted = [];
//: Some steps need a different answer the second time — "status" is asked
//: again after credentials are saved, and it has moved on by then. A list
//: under a pattern is consumed in order; the last entry repeats.
const consumed = {};
globalThis.fetch = async (path, options = {}) => {
  calls.push(path);
  if (options.body) {
    let body = options.body;
    try { body = JSON.parse(body); } catch { /* left as sent */ }
    posted.push({ path: String(path), body });
  }
  for (const [pattern, answer] of Object.entries(scenario.api || {})) {
    if (!String(path).startsWith(pattern)) continue;
    let reply = answer;
    if (Array.isArray(answer)) {
      const seen = consumed[pattern] || 0;
      reply = answer[Math.min(seen, answer.length - 1)];
      consumed[pattern] = seen + 1;
    }
    if (reply && reply.__throw) throw new Error(reply.__throw);
    return { ok: true, json: async () => reply };
  }
  return { ok: false, statusText: "Not Found",
           json: async () => ({ detail: `no canned answer for ${path}` }) };
};

// The whole workspace, in the order index.html loads it — one file today,
// several once app.js is split. `new Function` compiles a script, so every
// piece has to arrive in one shared scope; see tests/js/_app_source.mjs.
const src = appSource(path.dirname(APP_JS));
new Function(
  src +
  "\nglobalThis.__browser = connectorBrowser;" +
  "\nglobalThis.__loadCatalog = loadConnectorCatalog;" +
  "\nglobalThis.__permissions = connectorPermissions;" +
  "\nglobalThis.__approvals = loadApprovals;" +
  "\nglobalThis.__setup = connectorHelp;"
)();

const result = { ok: true, calls, posted, modals: [], textWrites, error: null };
try {
  if (scenario.mode === "catalog") {
    globalThis.__browser();
    await globalThis.__loadCatalog();
    const box = elFor("cxList");
    result.catalogHtml = box.innerHTML;
    result.addButtons = box.querySelectorAll("[data-cxadd]").map((b) => ({
      id: b.dataset.cxadd, disabled: !!b.disabled,
    }));
  } else if (scenario.mode === "permissions") {
    await globalThis.__permissions(scenario.entry);
    result.permHtml = elFor("cxPerm").innerHTML;
  } else if (scenario.mode === "setup") {
    // A sign-in is several screens, and the claim is what each one posts. So
    // the flow is driven the way a person drives it: open it, type into the
    // boxes it actually rendered, press its button, repeat.
    await globalThis.__setup(scenario.connector);
    for (const round of scenario.steps || []) {
      for (const [id, value] of Object.entries(round.type || {})) {
        elFor(id).value = value;
      }
      const button = elFor(round.press || "tgGo");
      if (!button.onclick) {
        result.error = `nothing is wired to #${round.press || "tgGo"}`;
        break;
      }
      await button.onclick();
    }
    // The modal BODY, not only its title: an account name, a warning or a
    // set of form fields all live in the html and never in textContent.
    result.modalBody = elFor("bmBody").innerHTML;
    result.say = elFor("tgSay").textContent;
    result.modalHidden = elFor("brainModal").hidden;
  } else if (scenario.mode === "approvals") {
    await globalThis.__approvals();
    const box = elFor("approvals");
    result.approvalsHtml = box.innerHTML;
    result.approvalsHidden = box.hidden;
    result.approveButtons = box.querySelectorAll("[data-aprok]").map(
      (b) => b.dataset.aprok);
  }
} catch (e) {
  result.ok = false;
  result.error = `${e && e.name}: ${e && e.message}`;
}
// The real `openBrainModal` writes the title into #bmTitle, so the recorded
// textContent writes are what prove it opened.
result.modals = textWrites.filter((w) => w.id === "bmTitle").map((w) => w.text);
// One turn of the loop, so a rejection raised by fire-and-forget work the
// flow started is recorded before the result is written rather than after.
await new Promise((done) => setImmediate(done));
result.unhandled = unhandled;
process.stdout.write(JSON.stringify(result));
