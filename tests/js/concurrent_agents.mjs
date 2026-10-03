/**
 * Drive two agents through overlapping turns and report what each one holds.
 *
 * The workspace used to keep one `busy` flag, one AbortController and one turn
 * id for the whole window, which made four agents into a one-lane workspace:
 * `selectAgent` refused outright while a reply was being written ("finishing
 * current reply…"), so the only thing a user could do with an agent that was
 * thinking was wait for it. The server never had that limit.
 *
 * Three claims, and none of them can be made by reading the source — they are
 * all about what happens while two turns are genuinely in flight:
 *
 *  1. switching away from a working agent WORKS, and the composer belongs to
 *     the agent on screen rather than to the last one to start a turn;
 *  2. two agents really do run at once — two requests, neither waiting;
 *  3. a reply that lands while the user is looking elsewhere does NOT get
 *     written into the other agent's transcript. It is flagged instead, and the
 *     stored history is what shows it when the agent is opened.
 *
 * Plus the thing that makes (1) survivable: a half-typed message stays with the
 * agent it was addressed to.
 *
 * argv: <a path inside chitragupta/web/>   stdout: one JSON report
 */
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];

const makeEl = (tag = "div") => {
  const e = {
    tag, className: "", value: "", hidden: false, disabled: false, title: "",
    textContent: "", style: {}, dataset: {}, _attrs: {}, _kids: [],
    scrollTop: 0, scrollHeight: 100, placeholder: "",
    classList: {
      _on: new Set(),
      add(c) { this._on.add(c); }, remove(c) { this._on.delete(c); },
      toggle(c, v) { if (v === undefined ? !this._on.has(c) : v) this._on.add(c); else this._on.delete(c); },
      contains(c) { return this._on.has(c); },
    },
    // Children, by class — `addMsg` removes the empty state with
    // `querySelector(".hero-empty")`, and a stub that answered with a throwaway
    // would leave the greeting sitting above every reply while reporting that
    // it had gone.
    querySelector: (sel) => e._kids.find(
      (k) => String(k.className).split(/\s+/).includes(String(sel).replace(/^\./, ""))) || null,
    querySelectorAll: () => [],
    addEventListener() {}, setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k] ?? null; },
    removeAttribute(k) { delete this._attrs[k]; },
    focus() {}, closest: () => null,
    remove() { const p = e._parent; if (!p) return; p._kids = p._kids.filter((k) => k !== e); },
    appendChild(c) { c._parent = e; e._kids.push(c); return c; },
  };
  // `innerHTML = ""` is how renderHistory empties the thread, so the children
  // have to go with it — otherwise every assertion about "what is on screen"
  // is really about everything that was ever on screen.
  let html = "";
  Object.defineProperty(e, "innerHTML", {
    get: () => html,
    set: (v) => { html = String(v); e._kids = []; },
  });
  return e;
};

const els = {};
const el = (sel) => (els[sel] ||= makeEl());

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  querySelector: (s) => el(s), querySelectorAll: () => [],
  getElementById: (i) => el(`#${i}`), createElement: (t) => makeEl(t),
  addEventListener() {}, body: makeEl(), documentElement: makeEl(),
};
globalThis.window = { location: { pathname: "/", href: "/" }, addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }), open() {} };
const store = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.AbortController = class { constructor() { this.signal = { aborted: false }; }
  abort() { this.signal.aborted = true; } };

const AGENTS = [
  { id: "alpha", name: "Alpha", role: "research", tools: [] },
  { id: "beta", name: "Beta", role: "inbox", tools: [] },
];
//: Server-side history, so switching back reads the reply from where it is
//: really stored rather than from anything the window kept.
const HISTORY = { alpha: [], beta: [] };
//: One gate per agent: the turn hangs until the test lets it answer, which is
//: the only way two of them are ever in flight at the same moment.
const gates = {};
const chatPaths = [];
//: Agents whose transcript fetch hangs until released.
//:
//: Not a contrivance: `/api/agents/{id}/connectors` measures 4.5s on a real
//: machine and `/api/connectors` 9.8s, so a few switches fill the browser's
//: six-per-host connection pool and the transcript queues behind them. A
//: fixture that always answered instantly agreed with a composer that was only
//: repainted at the end — and in a browser that composer stayed locked by the
//: agent the user had just left.
const historyGates = {};

globalThis.fetch = async (url, opts = {}) => {
  const p = String(url);
  const json = (v) => ({ ok: true, json: async () => v });
  if (p === "/api/agents") return json({ agents: AGENTS });
  let m = /^\/api\/agents\/([^/]+)\/history$/.exec(p);
  if (m) {
    if (historyGates[m[1]] === "hold") {
      await new Promise((resolve) => { historyGates[m[1]] = resolve; });
    }
    return json({ history: HISTORY[m[1]] || [] });
  }
  m = /^\/api\/agents\/([^/]+)\/cards$/.exec(p);
  if (m) return json({ cards: {}, ran: [] });
  // No streaming body, so the turn falls through to the plain endpoint — which
  // is the one held open here.
  if (/\/chat\/stream$/.test(p)) return { ok: false, body: null };
  m = /^\/api\/agents\/([^/]+)\/chat$/.exec(p);
  if (m) {
    const who = m[1];
    chatPaths.push(who);
    // **The question is stored on the way IN, like the real one.**
    // `runtime.run_turn` appends it before it calls the model — that is what
    // makes a turn that died still show what was asked. A fixture that stored
    // the pair at the end agreed with whatever the client did, and hid the
    // client drawing the question a second time on top of it.
    HISTORY[who] = [{ role: "user", content: JSON.parse(opts.body).message }];
    const reply = await new Promise((resolve) => { gates[who] = resolve; });
    HISTORY[who] = HISTORY[who].concat([{ role: "assistant", content: reply }]);
    return json({ agent_id: who, reply, trace: [] });
  }
  return json({});
};

new Function(
  appSource(path.dirname(APP_JS)) +
  // Not what this is about, and each would fetch or mount a live character.
  "\npaintAvatar = () => {};" +
  "\nupdateAgentModelChip = async () => {};" +
  "\nloadConnectorNames = async () => {};" +
  "\nloadBrain = async () => {};  loadTasks = async () => {};" +
  "\nloadReminders = async () => {}; toast = () => {};" +
  "\nglobalThis.__select = selectAgent; globalThis.__send = send;" +
  "\nglobalThis.__load = loadAgents;" +
  "\nglobalThis.__state = () => ({ current, turns: Object.keys(TURNS).sort()," +
  "  landed: [...LANDED].sort(), draft: $('#input').value });"
).call(globalThis);

const settle = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
/** Everything on screen, as text — a reply in the wrong chat shows up here. */
const onScreen = () => el("#messages")._kids
  .map((k) => `${k.className}|${k.innerHTML}|${k.textContent}|${k.dataset.raw || ""}`)
  .join("\n");

const report = { error: null, steps: [] };
const step = (name, extra) => report.steps.push({
  name, ...globalThis.__state(), inputDisabled: el("#input").disabled,
  screen: onScreen(), ...extra });

try {
  await globalThis.__load();
  await globalThis.__select("alpha");

  globalThis.__send("what did Alpha find?");       // not awaited: it hangs
  await settle();
  step("alpha-working");

  el("#input").value = "half-written note to Alpha";
  await globalThis.__select("beta");               // the switch that used to be refused
  await settle();
  step("switched-to-beta");
  el("#input").value = "half-written note to Beta";

  // Back to an agent that is STILL thinking. Its answer is not in the stored
  // history yet, so the indicator has to come off the turn's own record — and
  // the question, which the server stored on the way in, must not be drawn
  // again on top of it.
  await globalThis.__select("alpha");
  await settle();
  step("back-on-working-alpha");

  // Now an idle agent whose transcript has not arrived. Being selected, having
  // a usable composer and having your draft back are facts about which agent is
  // on screen — none of them is a fact about the network.
  historyGates.beta = "hold";
  globalThis.__select("beta");                     // not awaited: it is stuck
  await settle();
  step("beta-selected-history-pending");
  historyGates.beta("go");
  await settle();

  globalThis.__send("and Beta?");
  await settle();
  step("both-working", { chatPaths: chatPaths.slice() });

  gates.alpha("ALPHA ANSWER");                     // lands while Beta is on screen
  await settle();
  step("alpha-landed-offscreen");

  await globalThis.__select("alpha");
  await settle();
  step("back-on-alpha");

  gates.beta("BETA ANSWER");
  await settle();
  step("beta-landed-offscreen");
} catch (e) {
  report.error = String((e && e.stack) || e);
}
process.stdout.write(JSON.stringify(report));
process.exit(0);
