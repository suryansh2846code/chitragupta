/**
 * The agent profile popup — opened, tabbed through, saved, and closed.
 *
 * What this owns is the seam, which is where the bugs will be: does the ⋯ on a
 * row open the dialog, does each tab actually draw, does the Persona box get
 * the file the server sent, does Save send back what is on screen, does the
 * danger zone call the right endpoint for the kind of agent it is looking at,
 * and does closing with unsaved work stop to ask.
 *
 * `renderAgentTools` is not re-tested here — `agent_tools.mjs` owns it. What
 * is asserted is that the Permissions tab hands it the profile's OWN container,
 * because the alternative (drawing into the Settings panel's `#agentToolList`)
 * looks identical in code review and renders nothing in the popup.
 *
 * argv: <app.js>   stdout: {opened, tabs, persona, memory, danger, guard, perms}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const WEB = path.dirname(APP_JS);
const PAGE = fs.readFileSync(path.join(WEB, "index.html"), "utf8");

function makeEl(tag = "div") {
  const el = {
    tag, value: "", hidden: false, disabled: false, title: "", maxLength: 0,
    style: {}, dataset: {}, onclick: null, oninput: null, onchange: null,
    onkeydown: null, children: [], _attrs: {}, className: "", _html: "", _text: "",
    placeholder: "", htmlFor: "", id: "", type: "",
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); },
      toggle(c, on) { if (on === undefined) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); } else if (on) this._set.add(c); else this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    appendChild(child) { el.children.push(child); return child; },
    // Real: `profile.js` builds its footers with `append(a, b, c)`, which is a
    // DOM method a stub that only has `appendChild` would silently lack — the
    // render would throw halfway and the pane would be half-drawn.
    append(...nodes) { for (const n of nodes) el.children.push(n); },
    insertBefore(child) { el.children.unshift(child); return child; },
    removeChild(child) { el.children = el.children.filter((c) => c !== child); },
    setAttribute(k, v) { el._attrs[k] = String(v); },
    getAttribute(k) { return el._attrs[k] ?? null; },
    removeAttribute(k) { delete el._attrs[k]; },
    addEventListener() {}, removeEventListener() {}, focus() {}, remove() {},
    closest: () => null, querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 64, height: 64 }),
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

// Rows carrying the markup `loadAgents` produced, so the ⋯ handler it binds
// lands on something this harness can click.
const agentRows = AGENTS.map((a) => {
  const row = makeEl("div");
  row.dataset.id = a.id;
  return row;
});
const moreButtons = AGENTS.map((a) => {
  const b = makeEl("button");
  b.dataset.profile = a.id;
  return b;
});

globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.document = {
  createElement: (tag) => makeEl(tag),
  querySelector: (sel) => el(sel),
  querySelectorAll: (sel) => (sel === ".agent" ? agentRows
    : sel === "[data-profile]" ? moreButtons
    : sel === ".modal-bg" ? [el("#agentProfile")]
    : []),
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

let confirmAnswer = true;
const confirmsAsked = [];
globalThis.confirm = (q) => { confirmsAsked.push(q); return confirmAnswer; };

const requests = [];
globalThis.fetch = async (url, opts) => {
  const method = (opts && opts.method) || "GET";
  requests.push({ url, method, body: opts && opts.body });
  const json = (v) => ({ ok: true, json: async () => v });
  if (url === "/api/agents") return json({ agents: AGENTS });
  if (url === "/api/agents/avatars") return json({ avatars: {} });
  if (url === "/api/agents/tools") {
    return json({ tools: [], categories: [], groups: [], presets: [] });
  }
  if (/\/files$/.test(url)) {
    return json({ files: [
      { name: "persona.md", exists: false, bytes: 0, limit: 16384 },
      { name: "memory.md", exists: true, bytes: 6100, limit: 6144 },
    ] });
  }
  if (/\/files\/persona\.md$/.test(url)) return json({ text: "Be brief." });
  if (/\/files\/memory\.md$/.test(url)) return json({ text: "- Keep it short" });
  if (/\/model$/.test(url)) return json({ provider: "claude", model: "claude-sonnet-5" });
  if (/\/history$/.test(url)) return json({ history: [] });
  if (/\/cards$/.test(url)) return json({ cards: [], ran: [] });
  return json({});
};

globalThis.__AGENTS = AGENTS;
// The fixture and the exports are installed INSIDE the app's scope: `agents`,
// `current` and `loadAgentTools` are bindings in the evaluated script, so a
// global of the same name is a different variable the app never reads.
new Function(`${appSource(WEB)}
  ;agents = globalThis.__AGENTS; current = "inbox";
  ;globalThis.__loadAgents = loadAgents;
  ;globalThis.__openProfile = openAgentProfile;
  ;globalThis.__setToolLoader = (fn) => { loadAgentTools = fn; };
  ;globalThis.__setAppearanceDirty = (on) => { markAppearanceDirty(on); };
`)();

const out = {};
const tick = () => new Promise((r) => setTimeout(r, 0));
const pane = () => el("#profPane");
const paneText = () => JSON.stringify(pane().children.map((c) => c._html || c._text));
const findBy = (root, pred) => {
  const walk = (n) => {
    if (pred(n)) return n;
    for (const c of n.children || []) { const hit = walk(c); if (hit) return hit; }
    return null;
  };
  return walk(root);
};
const button = (label) => findBy(pane(), (n) => n.tag === "button" && n._text === label);

try {
  // 1. The markup is in the page, not injected — the focus observer and the
  //    Escape handler in app.js bind to .modal-bg once, at load.
  out.markupInPage = /id="agentProfile"[^>]*class="modal-bg"/.test(PAGE);

  // 2. The ⋯ opens it.
  await globalThis.__loadAgents();
  const more = moreButtons[0];
  out.moreIsBound = typeof more.onclick === "function";
  if (more.onclick) more.onclick({ stopPropagation() {} });
  await tick();
  out.opened = {
    shown: el("#agentProfile").hidden === false,
    title: el("#profTitle").textContent,
    tabs: el("#profTabs").children.length,
    firstPaneDrawn: pane().children.length > 0,
  };

  // The avatar editor needs a real browser — native colour inputs, pointer
  // capture, `createElementNS`. `appearance_screen.mjs` makes the same trade
  // and for the same reason: standing it up here would be testing the fake DOM.
  // Installed BEFORE the tab loop below, which renders the Appearance tab too.
  const mounted = [];
  globalThis.Character.mountEditor = (container, opts) => {
    mounted.push({ container, hasDocument: !!opts.document });
    return { destroy() {}, getDocument: () => opts.document, setDocument() {},
             on: () => () => {} };
  };

  // 3. Every tab draws something. A tab that renders an empty pane is the
  //    failure this loop exists for — it looks fine until you click it.
  out.tabs = {};
  for (const tab of ["profile", "persona", "memory", "model", "permissions",
                     "appearance"]) {
    globalThis.__openProfile("inbox", tab);
    await tick(); await tick();
    out.tabs[tab] = pane().children.length;
  }

  // 4. Persona: the file the server sent is in the box, and Save sends it back.
  globalThis.__openProfile("inbox", "persona");
  await tick(); await tick();
  const doc = findBy(pane(), (n) => n.tag === "textarea");
  out.persona = { loaded: doc && doc.value, saveDisabled: button("Save").disabled };
  doc.value = "Answer only in haiku.";
  doc.oninput();
  out.persona.saveEnabledAfterEdit = button("Save").disabled === false;
  requests.length = 0;
  await button("Save").onclick();
  await tick();
  out.persona.saved = requests.find((r) => r.method === "PUT") || null;

  // 5. Memory: "forget all of it" deletes the file rather than saving a blank.
  globalThis.__openProfile("inbox", "memory");
  await tick(); await tick();
  requests.length = 0;
  confirmAnswer = true;
  await button("Forget all of it").onclick();
  await tick();
  out.memory = {
    deleted: requests.find((r) => r.method === "DELETE") || null,
    // The file fixture is 6100 of 6144 bytes — the state a long-running agent
    // reaches, and the only one the meter exists to report.
    meter: el("#profMeter").textContent,
  };

  // 6. The danger zone asks the right question and calls the right endpoint
  //    for the kind of agent. A preset is removed from the roster and keeps
  //    everything; a custom agent is destroyed.
  out.danger = {};
  for (const [id, key] of [["inbox", "preset"], ["chotu", "custom"]]) {
    globalThis.__openProfile(id, "profile");
    await tick(); await tick();
    requests.length = 0; confirmsAsked.length = 0; confirmAnswer = true;
    const btn = button("Remove from team") || button("Delete agent");
    await btn.onclick();
    await tick();
    out.danger[key] = {
      label: btn._text,
      asked: confirmsAsked[0] || "",
      url: (requests.find((r) => r.method === "DELETE") || {}).url || "",
    };
  }

  // 7. Closing with unsaved work stops to ask — and "no" keeps it open.
  globalThis.__openProfile("inbox", "persona");
  await tick(); await tick();
  findBy(pane(), (n) => n.tag === "textarea").oninput();
  confirmAnswer = false; confirmsAsked.length = 0;
  el("#profClose").onclick();
  out.guard = {
    asked: confirmsAsked.length === 1,
    stayedOpen: el("#agentProfile").hidden === false,
  };
  confirmAnswer = true;
  el("#profClose").onclick();
  out.guard.closedWhenAllowed = el("#agentProfile").hidden === true;

  // 8. Appearance mounts the real editor, into the profile's own container,
  //    and the profile's guard sees ITS dirty flag — a second flag, owned by
  //    appearance.js, which the close guard would otherwise not know about.
  mounted.length = 0;
  globalThis.__openProfile("inbox", "appearance");
  await tick(); await tick();
  out.appearance = {
    mounted: mounted.length === 1,
    gotADocument: !!(mounted[0] && mounted[0].hasDocument),
    // Mounted inside the pane, not into the deleted settings panel.
    insidePane: !!(mounted[0] && mounted[0].container.id === "apEditor"),
  };
  // Drag a slider: the editor reports a change, and closing must now ask.
  globalThis.__setAppearanceDirty(true);
  confirmAnswer = false; confirmsAsked.length = 0;
  el("#profClose").onclick();
  out.appearance.guarded = confirmsAsked.length === 1 &&
    el("#agentProfile").hidden === false;
  confirmAnswer = true;
  el("#profClose").onclick();

  // 9. The settings rail no longer offers either of them, and nothing in the
  //    page still declares the panels they used to open.
  out.removed = {
    toolsNav: !/data-msnav="tools"/.test(PAGE),
    appearanceNav: !/data-msnav="appearance"/.test(PAGE),
    toolsPanel: !/data-sp="tools"/.test(PAGE),
    appearancePanel: !/data-sp="appearance"/.test(PAGE),
  };

  // 10. Permissions draws into the profile's own container, never the Settings
  //     panel's — the mistake that renders nothing and reads fine.
  let toolBox = null;
  globalThis.__setToolLoader((id, box) => { toolBox = box; });
  globalThis.__openProfile("inbox", "permissions");
  await tick();
  out.perms = {
    gotABox: !!toolBox,
    isOwnBox: !!toolBox && toolBox !== el("#agentToolList"),
    boxId: toolBox && toolBox.id,
  };
} catch (e) {
  out.error = String((e && e.stack) || e);
}

process.stdout.write(JSON.stringify(out));
