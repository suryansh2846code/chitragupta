/**
 * Drive the "sign in to a site" flow and report what the panel showed.
 *
 * The whole value of this screen is its three states, and the middle one is the
 * one that is easy to get wrong: `still_signing_in` is not a failure, it is our
 * guess that the user is still on the login page — and pressing Done a second
 * time has to overrule it with `force: true`. A user who cannot overrule a
 * heuristic is locked out of an account that is already theirs.
 *
 * None of that is visible to a source-order assertion: what matters is which
 * block is hidden, what the button says, and what the second press puts on the
 * wire. So the real functions are sliced out of browser.js and run.
 *
 * argv: <path to browser.js>   stdin: {states: [...], press: [...]}
 *   `states` are successive `GET /api/browser/connect` bodies.
 *   `press` is a list of "go" | "done" | "cancel".
 */
import fs from "node:fs";

const SRC = fs.readFileSync(process.argv[2], "utf8");
const input = JSON.parse(fs.readFileSync(0, "utf8"));

const calls = [];
let stateIdx = 0;

const el = (id) => (els[id] ||= mk(id));
const els = {};
function mk(id) {
  const set = new Set();
  return {
    id, value: "", textContent: "", hidden: false, disabled: false, oninput: null,
    onclick: null, onkeydown: null,
    classList: { add: (c) => set.add(c), remove: (c) => set.delete(c),
                 toggle: (c, v) => (v ? set.add(c) : set.delete(c)),
                 contains: (c) => set.has(c) },
    _classes: set,
    focus() {}, scrollIntoView() {},
  };
}

const ctx = {
  $: (sel) => el(String(sel).replace(/^#/, "")),
  api: async (path, opts = {}) => {
    calls.push({ path, method: opts.method || "GET",
                 body: opts.body ? JSON.parse(opts.body) : null });
    if (path === "/api/browser/connect" && (opts.method || "GET") === "GET") {
      const s = input.states[Math.min(stateIdx, input.states.length - 1)];
      stateIdx += 1;
      return s;
    }
    // An array means successive answers to the same path — the refusal, then
    // what happens after the user has pressed the thing that fixes it. One
    // fixed reply cannot express "and now it works", which is the half of a
    // one-press route out that is worth testing.
    const reply = (input.replies || {})[path];
    if (Array.isArray(reply)) return reply.length > 1 ? reply.shift() : reply[0];
    return reply ?? { ok: true };
  },
  toast: (m) => calls.push({ toast: m }),
  setInterval: () => 1,
  clearInterval: () => {},
  loadBrowserSites: async () => {},
};

// Everything from the connect section down — the flow and the block that wires
// it, in the order the browser evaluates them.
const block = SRC.slice(SRC.indexOf("// ── connecting a site"));
// The fix button starts hidden in the markup, and the harness's fake elements
// start visible. A test that found it already on screen would be testing the
// fake rather than the code that reveals it.
el("webConnectWindow").hidden = true;
const run = new Function(...Object.keys(ctx),
  block + "\nreturn { renderConnect, connectRisk, renderConnectRisk, loadConnectState, els: null };");
const api = run(...Object.values(ctx));

const snap = () => ({
  idleHidden: el("webConnectIdle").hidden,
  liveHidden: el("webConnectLive").hidden,
  waiting: el("webConnectLive")._classes.has("is-waiting"),
  message: el("webConnectMsg").textContent,
  doneLabel: el("webConnectDone").textContent,
  error: el("webConnectErr").hidden ? "" : el("webConnectErr").textContent,
  fixHidden: el("webConnectWindow").hidden,
  risk: el("webConnectRisk").hidden ? "" : el("webConnectRisk").textContent,
});

const report = { error: null, frames: [], calls: [], risk: {} };
try {
  for (const name of ["linkedin.com", "https://www.linkedin.com/feed",
                      "m.x.com", "news.bbc.co.uk", "example.com"]) {
    report.risk[name] = Boolean(api.connectRisk(name));
  }

  for (const step of input.press || []) {
    if (step === "risk") {
      el("webConnectInput").value = input.riskValue || "";
      api.renderConnectRisk();
    } else if (step === "poll") {
      await api.loadConnectState();
    } else {
      // "go" reads the address out of the field, and an empty field is a
      // deliberate no-op — so a test that means to press Connect has to type
      // first, exactly as a person would.
      if (step === "go") el("webConnectInput").value = input.typed ?? "example.com";   // ?? so "" is honoured
      const btn = { go: "webConnectGo", done: "webConnectDone",
                    cancel: "webConnectCancel", fix: "webConnectWindow" }[step];
      if (typeof el(btn).onclick === "function") await el(btn).onclick();
    }
    report.frames.push({ step, ...snap() });
  }
} catch (e) {
  report.error = String((e && e.stack) || e);
}
report.calls = calls;
process.stdout.write(JSON.stringify(report));
