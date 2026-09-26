/**
 * Open the connector Details panel for real, and click every control in it.
 *
 * `node --check` passes on all four of the failures this catches, and three of
 * them have shipped in this file before in one form or another:
 *
 * * a handler bound to markup that was never written into the container;
 * * a `data-` attribute the renderer spells one way and the binder another, so
 *   the button appears and does nothing;
 * * a body sent as a JSON *string*, which `_encodeBody` passes through without
 *   the JSON header, so FastAPI answers 422 and the dialog silently fails —
 *   the exact bug `core.js` records against `/api/open-browser` and that the
 *   Disconnect button shipped with;
 * * a capability rendered as `send:email` rather than as "Send email", which is
 *   an internal on a screen.
 *
 * Reads a scenario as JSON on stdin, writes one JSON result to stdout.
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];   // a path inside chitragupta/web/
const scenario = JSON.parse(fs.readFileSync(0, "utf8"));

const registry = new Map();
const calls = [];
let panelHtml = "";

function makeEl(id = "") {
  const el = {
    id, _html: "", _text: "", value: "", hidden: false, disabled: false,
    title: "", style: {}, dataset: {}, onclick: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, appendChild() {}, setAttribute() {},
    getAttribute: () => null, focus() {}, remove() {}, closest: () => null,
    get innerHTML() { return el._html; },
    // **Published as it is written, not read back afterwards.** The panel
    // binds its handlers in the same call that sets this, so a `panelHtml`
    // captured after `connectorDetails` returns is empty at bind time —
    // `querySelectorAll` answers with nothing, no handler is attached, and the
    // harness reports "no buttons" about a panel that has three.
    set innerHTML(v) {
      el._html = String(v);
      if (el.id === "cnDetail") panelHtml = el._html;
    },
    get textContent() { return el._text; },
    set textContent(v) { el._text = String(v); },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  return el;
}

function elFor(sel) {
  const key = String(sel).replace(/^#/, "");
  if (!registry.has(key)) registry.set(key, makeEl(key));
  return registry.get(key);
}

/**
 * Buttons parsed out of whatever the panel actually wrote.
 *
 * Built from the rendered HTML rather than from a list typed here, because a
 * handler bound to a selector the renderer never emits is precisely the bug
 * this harness exists to catch — and a hardcoded list would bind happily to
 * nothing and report success.
 */
const buttonCache = new Map();

function buttonsIn(html, attribute) {
  // **Memoised, and that is the whole reason this harness can click.** The
  // panel binds `onclick` onto whatever `querySelectorAll` handed it; a fresh
  // array per call throws those handlers away, and every button then reads as
  // "rendered but does nothing" — which is the bug under test, faked by the
  // harness itself. `tests/CLAUDE.md` records two earlier harnesses that were
  // blind in exactly this way.
  const key = `${attribute}:${html.length}`;
  if (buttonCache.has(key)) return buttonCache.get(key);
  const found = [];
  const pattern = new RegExp(
    `<button[^>]*?\\sdata-${attribute}="([^"]*)"([^>]*)>([\\s\\S]*?)<\\/button>`,
    "g");
  for (const match of html.matchAll(pattern)) {
    const el = makeEl();
    el.dataset[attribute.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = match[1];
    for (const attr of match[2].matchAll(/data-([a-z]+)="([^"]*)"/g)) {
      el.dataset[attr[1]] = attr[2];
    }
    el._text = match[3].replace(/<[^>]*>/g, "").trim();
    found.push(el);
  }
  buttonCache.set(key, found);
  return found;
}

globalThis.MutationObserver = class {
  observe() {} disconnect() {} takeRecords() { return []; }
};
globalThis.document = {
  querySelector: (s) => elFor(s),
  // Every binder in `connectorDetails` reaches for its buttons through this,
  // and each is answered from the markup the panel genuinely produced.
  querySelectorAll: (sel) => {
    const match = /^\[data-([a-z-]+)\]$/.exec(String(sel));
    return match ? buttonsIn(panelHtml, match[1]) : [];
  },
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
globalThis.confirm = () => scenario.confirm !== false;
globalThis.alert = () => {};
globalThis.setTimeout = (fn) => fn;

// Every request the panel makes, recorded with the body EXACTLY as it was
// handed over — so a `JSON.stringify` string is visible as a string here
// rather than being indistinguishable from an object after the fact.
globalThis.fetch = async (url, options = {}) => {
  calls.push({ url: String(url), method: options.method || "GET",
               bodyType: typeof options.body,
               body: options.body === undefined ? null : options.body });
  const key = String(url);
  let payload = { ok: true, detail: "done" };
  if (key.includes("/manifest")) payload = scenario.manifest;
  else if (key.includes("/data") && (options.method || "GET") === "GET") {
    payload = scenario.data;
  } else if (key.includes("/health")) payload = scenario.health;
  else if (key.includes("/data")) payload = { ok: true, removed: 3,
                                              detail: "Removed 3 items." };
  return { ok: true, status: 200, statusText: "OK",
           headers: { get: () => "application/json" },
           json: async () => payload, text: async () => JSON.stringify(payload) };
};

const src = appSource(path.dirname(APP_JS));
new Function(src + `
  globalThis.__details = connectorDetails;
  globalThis.__cap = _cnCapability;
  // **Bare assignment, not \`globalThis.x =\`.** These are top-level function
  // declarations inside the \`new Function\` body, so the panel's own calls
  // resolve to the *binding*, not to a global of the same name — setting the
  // global left the real \`loadBrain\` running, which reached for a stats shape
  // this harness does not model and took the whole run down.
  openBrainModal = (title, html) => { globalThis.__title = title; };
  loadBrain = () => { globalThis.__reloaded = true; };
  toast = (m) => { (globalThis.__toasts ||= []).push(String(m)); };
`)();

const result = { ok: true, error: null, calls, toasts: [] };
try {
  // Evaluating the app source runs its own boot requests. Cleared here so
  // `calls` is what the PANEL asked for, and a test about the panel's cost is
  // not measuring the shell's.
  calls.length = 0;
  await globalThis.__details(scenario.name, scenario.label);
  result.html = panelHtml;
  result.title = globalThis.__title;

  result.buttons = {
    pause: buttonsIn(panelHtml, "cnpause").length,
    resync: buttonsIn(panelHtml, "cnresync").length,
    forget: buttonsIn(panelHtml, "cnforget").length,
  };
  result.pauseLabel = (buttonsIn(panelHtml, "cnpause")[0] || {})._text || "";
  // A capability must never reach a screen in its machine spelling.
  result.showsRawCapability = /\b(read|send|create|delete):[a-z_]+\b/.test(panelHtml);
  result.capabilitySentence = globalThis.__cap("send:email");

  if (scenario.click) {
    calls.length = 0;
    const attribute = { pause: "cnpause", resync: "cnresync",
                        forget: "cnforget" }[scenario.click];
    // Fetched exactly the way the panel fetched them, so these ARE the
    // elements it bound onto.
    const buttons = globalThis.document.querySelectorAll(`[data-${attribute}]`);
    result.clicked = buttons.length;
    result.bound = buttons.filter((b) => typeof b.onclick === "function").length;
    for (const button of buttons) await button.onclick?.();
  }
  result.toasts = globalThis.__toasts || [];
  result.reloaded = Boolean(globalThis.__reloaded);
} catch (e) {
  result.ok = false;
  result.error = `${e && e.name}: ${e && e.message}`;
}
process.stdout.write(JSON.stringify(result));
