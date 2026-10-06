/**
 * Render the Backup screen for real, press its buttons, and report what happened.
 *
 * `node --check` passes on every mistake this harness exists to catch: a
 * handler bound to an id the renderer never emitted, a progress bar pinned at
 * zero because its phase has no total, a passphrase left sitting in a DOM node
 * after the request, a "Stopped" outcome painted in the red of a failure.
 *
 * **Ids are registered from the markup the renderer produced**, never declared
 * here — the same rule as `connector_sync_feedback.mjs`. `$("#bkStart")`
 * resolves only if `bkRender` actually wrote `id="bkStart"`, so a handler bound
 * to a renamed element finds nothing here exactly as in a browser.
 *
 * **`fetch` is faked, not `api`.** Replacing `api()` would skip the real
 * request encoding and the real rejection path, and the rejection path is where
 * two of these assertions live.
 *
 * Reads a scenario as JSON on stdin, writes one JSON result to stdout.
 */
import fs from "node:fs";

import { appSource } from "./_app_source.mjs";

const WEB_DIR = process.argv[2];
const scenario = JSON.parse(fs.readFileSync(0, "utf8"));

const requests = [];
const toasts = [];
const alerts = [];
const clipboard = [];
const picks = [];
const registry = new Map();

const ID_IN_HTML = /\bid="([^"]+)"/g;
const TAG_WITH_CLASS = /<[a-z][^>]*\bclass="([^"]*)"[^>]*>/gi;

function makeEl(id = "") {
  const node = {
    id, _html: "", _text: "",
    value: "", hidden: false, disabled: false, title: "",
    style: {}, dataset: {}, onclick: null, oninput: null,
    _classes: new Set(),
    classList: {
      add: (c) => node._classes.add(c),
      remove: (c) => node._classes.delete(c),
      toggle: (c, on) => (on ? node._classes.add(c) : node._classes.delete(c)),
      contains: (c) => node._classes.has(c),
    },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    setAttribute() {}, removeAttribute() {}, getAttribute: () => null,
    focus() {}, remove() {}, closest: () => null, insertBefore() {},
    get innerHTML() { return node._html; },
    set innerHTML(v) {
      node._html = String(v);
      // Registering ids from the produced markup is what makes a stale
      // selector fail here the way it fails in a browser.
      for (const [, found] of node._html.matchAll(ID_IN_HTML)) {
        if (!registry.has(`#${found}`)) registry.set(`#${found}`, makeEl(found));
      }
    },
    get textContent() { return node._text; },
    set textContent(v) { node._text = String(v); },
    querySelector: () => null,
    querySelectorAll: (sel) => nodesMatching(node._html, sel),
  };
  return node;
}

/** A fake element per `class="… wanted …"` tag in some markup, with its data-*. */
function nodesMatching(html, sel) {
  if (!sel || !sel.startsWith(".")) return [];
  const wanted = sel.slice(1);
  const out = [];
  let index = 0;
  for (const [tag, classes] of String(html).matchAll(TAG_WITH_CLASS)) {
    if (!classes.split(/\s+/).includes(wanted)) continue;
    const node = makeEl(`${wanted}-${index++}`);
    for (const [, name, value] of tag.matchAll(/\bdata-([a-z-]+)="([^"]*)"/g)) {
      node.dataset[name.replace(/-([a-z])/g, (_m, c) => c.toUpperCase())] = value;
    }
    out.push(node);
  }
  return out;
}

const el = (sel) => {
  if (!registry.has(sel)) registry.set(sel, makeEl(sel.replace(/^#/, "")));
  return registry.get(sel);
};

globalThis.MutationObserver = class {
  observe() {} disconnect() {} takeRecords() { return []; }
};
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.document = {
  querySelector: (s) => el(s),
  // `showSettingsPanel` sweeps `.sp` and `.ms-nav-item`; `bkBind` sweeps the
  // rendered body for `.bk-use`.
  querySelectorAll: (s) => ((s === ".sp" || s === ".ms-nav-item")
    ? [] : nodesMatching(el("#bkBody")._html, s)),
  getElementById: (i) => el(`#${i}`),
  createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = {
  location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {},
  innerWidth: 1440, innerHeight: 900,
};
// Node 26 defines `navigator` as a getter-only global, so it is redefined
// rather than assigned.
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    clipboard: {
      writeText: async (t) => {
        if (scenario.clipboard_fails) throw new Error("denied");
        clipboard.push(String(t));
      },
    },
  },
});
// The pywebview bridge, present only when the scenario says so — which is what
// the desktop app looks like and `chitragupta serve` does not.
if (scenario.bridge) {
  globalThis.window.pywebview = {
    api: {
      pick_backup_file: async () => {
        picks.push("open");
        if (scenario.bridge_throws) throw new Error("panel failed");
        return scenario.bridge.open ?? "";
      },
      pick_backup_destination: async (suggested) => {
        picks.push(`save:${suggested}`);
        if (scenario.bridge_throws) throw new Error("panel failed");
        return scenario.bridge.save ?? "";
      },
    },
  };
}
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.alert = (m) => alerts.push(String(m));
globalThis.confirm = () => scenario.confirm !== false;
globalThis.Character = { scene: () => ({}), mount: () => ({}), svg: () => "" };

const responses = scenario.responses || {};

globalThis.fetch = async (url, opts = {}) => {
  const p = String(url);
  requests.push({
    path: p,
    method: opts.method || "GET",
    body: opts.body ? JSON.parse(opts.body) : null,
  });
  const hit = responses[p];
  if (hit && hit.__status) {
    return { ok: false, status: hit.__status,
             statusText: "error",
             json: async () => ({ detail: hit.__detail || "refused" }) };
  }
  return { ok: true, json: async () => (hit === undefined ? {} : hit) };
};

const src = appSource(WEB_DIR);
// The returned setters reach into the app's own scope, which only same-scope
// code can do — see `_app_source.mjs` on why this is a classic script.
const api = new Function(`${src}
  return { bkRender, bkSync, bkStart, bkInspect, bkCheckPhrase, bkShowCode,
           setState: (s) => { bkState = s; },
           setToast: (fn) => { toast = fn; },
           setPanel: (fn) => { showSettingsPanel = fn; } };`)();

api.setToast((m) => toasts.push(String(m)));
api.setPanel(() => {});

const result = { requests, toasts, alerts, clipboard, picks, steps: [] };

function snapshot(label) {
  const body = el("#bkBody")._html;
  const fill = registry.get("#bkBarFill");
  result.steps.push({
    label,
    ids: [...new Set([...body.matchAll(ID_IN_HTML)].map((m) => m[1]))],
    has_warning: body.includes("bk-warn"),
    code_tags: (body.match(/<code>/g) || []).length,
    progress_hidden: el("#bkProgress").hidden,
    stop_hidden: el("#bkStop").hidden,
    start_disabled: el("#bkStart").disabled,
    phase_text: el("#bkPhase")._text,
    outcome_text: el("#bkOutcome")._text,
    outcome_colour: el("#bkOutcome").style.color || "",
    bar_width: fill ? (fill.style.width || "") : "",
    bar_indeterminate: fill ? fill._classes.has("is-indeterminate") : false,
    phrase_field: el("#bkPhrase").value,
    restore_path_field: el("#bkRestorePath").value,
    save_path_field: el("#bkPath").value,
    hint_text: el("#bkPhraseHint")._text,
    hint_colour: el("#bkPhraseHint").style.color || "",
    code_modal_hidden: el("#bkCodeModal").hidden,
    code_shown: el("#bkCodeValue")._text,
    inspected_html: el("#bkInspected")._html,
  });
}

async function press(sel) {
  const node = registry.get(sel);
  result[`${sel}_existed`] = !!(node && node.onclick);
  if (node && node.onclick) await node.onclick();
}

async function main() {
  api.setState(scenario.state || {});
  api.bkRender();
  snapshot("rendered");

  for (const act of scenario.actions || []) {
    switch (act) {
      case "type_short_passphrase":
        el("#bkPhrase").value = "abc";
        api.bkCheckPhrase();
        break;
      case "type_passphrase":
        el("#bkPhrase").value = "a strong passphrase";
        api.bkCheckPhrase();
        break;
      case "press_backup": await api.bkStart(); break;
      case "sync": await api.bkSync(); break;
      case "press_inspect":
        await api.bkInspect(scenario.inspect_path || "/tmp/x.cgarch");
        break;
      case "press_restore": await press("#bkRestoreGo"); break;
      case "press_code_done": await press("#bkCodeDone"); break;
      case "press_code_copy": await press("#bkCodeCopy"); break;
      case "press_saved": await press("#bkSaved"); break;
      case "press_pick_open": await press("#bkPickOpen"); break;
      case "press_pick_save": await press("#bkPickSave"); break;
      default: throw new Error(`unknown action ${act}`);
    }
    snapshot(act);
  }
  process.stdout.write(JSON.stringify(result));
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({
    harness_error: String((e && e.stack) || e),
    requests, steps: result.steps,
  }));
  process.exit(1);
});
