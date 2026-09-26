/**
 * Press Sync on a connector row for real, and report what the row did.
 *
 * This harness exists because of a bug `node --check` cannot see and a
 * source-order assertion cannot see either: `syncConn` reached for `.conn`,
 * `.conn-sub` and `.dot`, while the row renders `.cn-row`, `.cn-sub` and a
 * `.cn-logo` carrying `data-state`. `.conn` survived only as a leftover CSS
 * rule, so the lookup returned null and — because every line used `?.` — all
 * of the feedback silently did nothing. Pressing Sync looked like pressing
 * nothing until a toast arrived, and a second click started a second sync.
 *
 * So the row markup is rendered by the real `_cnRowHtml`, and `syncConn` is
 * then made to find its elements *through that markup*. A selector that the
 * renderer does not emit finds nothing here, exactly as in the browser.
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

function makeEl(id = "") {
  const el = {
    id, _html: "", _text: "", value: "", hidden: false, disabled: false,
    title: "", style: {}, dataset: {}, onclick: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, appendChild() {}, setAttribute() {},
    getAttribute: () => null, focus() {}, remove() {}, closest: () => null,
    get innerHTML() { return el._html; },
    set innerHTML(v) { el._html = String(v); },
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
 * The row, built out of the markup `_cnRowHtml` genuinely produced.
 *
 * Its children are discovered by **looking for the classes in that HTML**, not
 * by being listed here — a hardcoded list would happily answer a selector the
 * renderer never emits, which is the whole bug.
 */
function rowFrom(html, name) {
  if (!new RegExp(`class="cn-row" data-conn="${name}"`).test(html)) return null;
  const children = new Map();
  for (const cls of ["cn-sub", "cn-logo", "cn-name"]) {
    if (!new RegExp(`class="${cls}[ "]`).test(html)) continue;
    children.set(`.${cls}`, makeEl(cls));
  }
  const syncBtn = new RegExp(`data-sync="${name}"`).test(html)
    ? makeEl("syncBtn") : null;
  const stopBtn = new RegExp(`data-syncstop="${name}"`).test(html)
    ? Object.assign(makeEl("stopBtn"), { hidden: true }) : null;
  const row = makeEl("row");
  row.querySelector = (sel) => {
    // Longest-prefix first: `[data-syncstop=` also starts with `[data-sync`.
    if (sel.startsWith("[data-syncstop")) return stopBtn;
    if (sel.startsWith("[data-sync")) return syncBtn;
    return children.get(sel) || null;
  };
  row._children = children;
  row._syncBtn = syncBtn;
  if (stopBtn) {
    // Bound the way `bindConnectorRowActions` binds it. The harness does not run
    // that function — it renders one row rather than the list — so the wiring is
    // reproduced here rather than assumed.
    stopBtn.dataset.syncstop = name;
    stopBtn.onclick = () => globalThis.__stop(name);
  }
  row._stopBtn = stopBtn;
  return row;
}

let theRow = null;

globalThis.MutationObserver = class {
  observe() {} disconnect() {} takeRecords() { return []; }
};
globalThis.document = {
  querySelector: (sel) => {
    // The one selector under test. Anything else falls through to a stub, so a
    // *wrong* row selector lands here and gets null — as it does in the browser.
    if (String(sel).startsWith(".cn-row[data-conn=")) return theRow;
    if (String(sel).startsWith(".conn[data-conn=")) return null;
    return elFor(sel);
  },
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
globalThis.CSS = { escape: (s) => String(s) };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.confirm = () => true;
globalThis.setTimeout = (fn) => fn;
//: The poll, driven by the harness rather than by a clock. `setInterval` in a
//: test is either flaky or slow; ticking it is neither, and it is the only way
//: to look at the row *between* two polls.
const ticks = [];
globalThis.setInterval = (fn) => { ticks.push(fn); return ticks.length; };
globalThis.clearInterval = () => {};

let released = false;

function running(s) {
  return { id: "j1", connector: s.connector.name, label: s.connector.label,
           state: "running", running: true, done: s.done ?? 0,
           total: s.total ?? 0, doing: "", percent: null, added: 0, skipped: 0,
           errors: [], detail: "",
           says: s.total ? `Syncing… ${s.done ?? 0} of ${s.total}` : "Syncing…" };
}

function finished(s) {
  const r = s.syncResult || { added: 3, skipped: 1, errors: [] };
  const failed = (r.errors || []).length > 0;
  return { id: "j1", connector: s.connector.name, label: s.connector.label,
           state: r.cancelled ? "cancelled" : failed ? "failed" : "done",
           running: false, done: s.done ?? r.added ?? 0, total: s.total ?? 0,
           doing: "", percent: null, added: r.added ?? 0,
           skipped: r.skipped ?? 0, errors: r.errors || [],
           detail: r.detail || "",
           says: r.cancelled ? "Stopped"
               : failed ? r.errors[0]
               : r.detail || `${r.added ?? 0} new, ${r.skipped ?? 0} already had` };
}

globalThis.fetch = async (url, options = {}) => {
  calls.push({ url: String(url), method: options.method || "GET",
               bodyType: typeof options.body,
               hasJsonHeader: Boolean(
                 options.headers &&
                 String(options.headers["Content-Type"] || "").includes("json")) });
  let payload = { ok: true };
  const key = String(url);
  if (key.includes("/sync/start")) {
    if (scenario.busy) {
      // A 409 from the server: another source holds the slot.
      return { ok: false, status: 409, statusText: "Conflict",
               headers: { get: () => "application/json" },
               json: async () => ({ detail: scenario.busy }),
               text: async () => JSON.stringify({ detail: scenario.busy }) };
    }
    payload = { ok: true, job: running(scenario) };
  } else if (key.includes("/api/connectors/jobs")) {
    // Held so the test can observe the row *mid-sync*, which is the state the
    // dead selectors made unobservable. After the hold, the job has finished.
    if (scenario.hold && !released) {
      payload = { jobs: [running(scenario)] };
    } else {
      payload = { jobs: [finished(scenario)] };
    }
  } else if (key.includes("/sync/stop")) {
    payload = { stopped: true };
  }
  return { ok: true, status: 200, statusText: "OK",
           headers: { get: () => "application/json" },
           json: async () => payload, text: async () => JSON.stringify(payload) };
};

const src = appSource(path.dirname(APP_JS));
new Function(src + `
  globalThis.__row = _cnRowHtml;
  globalThis.__when = _cnWhen;
  globalThis.__sync = syncConn;
  globalThis.__watch = watchSyncJobs;
  globalThis.__stop = stopSyncConn;
  // Replaced by assignment, not via globalThis: these are top-level function
  // declarations, so the caller resolves the binding and not a global of the
  // same name.
  loadBrain = async () => { globalThis.__reloaded = true; };
  toast = (m) => { (globalThis.__toasts ||= []).push(String(m)); };
  openPicker = () => { globalThis.__picker = true; };
`)();

const result = { ok: true, error: null, calls };
try {
  const html = globalThis.__row(scenario.connector, 1440, scenario.health || []);
  result.html = html;
  // The row's own wording for a moment, asked directly as well as read out of
  // the markup — a date-only format is the bug, and it is easiest to see here.
  result.when = Object.fromEntries(
    Object.entries(scenario.when || {}).map(([k, iso]) =>
      [k, globalThis.__when(new Date(iso))]));
  theRow = rowFrom(html, scenario.connector.name);
  result.rowFound = theRow !== null;

  if (scenario.rendered_only) {
    // A page that never pressed Sync — what a refresh looks like. The watch has
    // to find the running job by itself.
    await globalThis.__watch();
  } else {
    await globalThis.__sync(scenario.connector.name);
  }

  if (scenario.hold) {
    // Mid-sync: what does the row say, and is the button shut?
    result.midSub = theRow?._children.get(".cn-sub")?.textContent ?? null;
    result.midLogoState = theRow?._children.get(".cn-logo")?.dataset.state ?? null;
    result.midButtonDisabled = theRow?._syncBtn?.disabled ?? null;
    result.midStopVisible = theRow?._stopBtn
      ? theRow._stopBtn.hidden === false : null;
    // A second press while the first is still running must not start another.
    await globalThis.__sync(scenario.connector.name);
    result.callsDuringHold =
      calls.filter((c) => c.url.includes("/sync/start")).length;
    if (scenario.click_stop) await theRow?._stopBtn?.onclick?.();
    // Now let the job finish and run one more poll.
    released = true;
  }
  for (const tick of ticks) await tick();

  result.finalSub = theRow?._children.get(".cn-sub")?.textContent ?? null;
  result.finalButtonDisabled = theRow?._syncBtn?.disabled ?? null;
  result.finalStopVisible = theRow?._stopBtn
    ? theRow._stopBtn.hidden === false : null;

  result.syncCalls = calls.filter((c) => c.url.includes("/sync/start")).length;
  result.toasts = globalThis.__toasts || [];
  result.reloaded = Boolean(globalThis.__reloaded);
  result.picker = Boolean(globalThis.__picker);
} catch (e) {
  result.ok = false;
  result.error = `${e && e.name}: ${e && e.message}`;
}
process.stdout.write(JSON.stringify(result));
