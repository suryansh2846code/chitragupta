/**
 * The composer's folder pill — opened, ticked, unticked, emptied and browsed.
 *
 * The pill it replaced said "workspace" and opened the Connectors screen,
 * which is a different thing: those folders are read into the brain every
 * agent shares. This one is the agent's own, so what is asserted is the seam —
 * whose folders it shows, what it sends, that several can be on at once, that
 * "look at no files" is a thing that survives, and that the browse modal hands
 * a chosen folder to the agent rather than to the ingest flow.
 *
 * argv: <app.js>   stdout: {loaded, menu, ticked, unticked, none, browse,
 *                           perAgent, failed, pane}
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
    isConnected: true,
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

//: Every folder open to the app — the boundary. Per-agent scopes narrow it.
const AVAILABLE = ["/Users/x/notes", "/Users/x/work/tax", "/Users/x/photos"];

//: `null` is undecided and `[]` is "look at nothing" — the whole feature is
//: that those are different answers, so the fake keeps them apart too.
const STORED = { inbox: ["/Users/x/notes"], chotu: null };
const DEFAULT_SCOPE = ["/Users/x/notes", "/Users/x/work/tax"];
let failNext = false;

const effective = (id) =>
  (STORED[id] === null ? DEFAULT_SCOPE : STORED[id]).filter((p) => AVAILABLE.includes(p));

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

  const m = /\/api\/agents\/([^/]+)\/folders$/.exec(url);
  if (m && method === "GET") {
    return json({ agent: m[1], available: AVAILABLE,
                  chosen: STORED[m[1]], folders: effective(m[1]) });
  }
  if (m && method === "PUT") {
    if (failNext) return { ok: false, json: async () => ({ detail: "nope" }) };
    STORED[m[1]] = JSON.parse(opts.body).folders;
    return json({ agent: m[1], available: AVAILABLE,
                  chosen: STORED[m[1]], folders: effective(m[1]) });
  }
  if (url.startsWith("/api/fs/browse")) {
    const asked = /path=([^&]+)/.exec(url);
    const here = asked ? decodeURIComponent(asked[1]) : "/Users/x";
    return json({ path: here, parent: "/Users", home: "/Users/x",
                  dirs: ["photos"], ingestible_here: 4 });
  }
  return json({});
};

globalThis.__AGENTS = AGENTS;
new Function(`${appSource(WEB)}
  ;agents = globalThis.__AGENTS; current = "inbox";
  ;globalThis.__loadAgentFolders = loadAgentFolders;
  ;globalThis.__mountAgentFoldersIn = mountAgentFoldersIn;
  ;globalThis.__pickFolderForAgent = pickFolderForAgent;
  ;globalThis.__openPicker = openPicker;
`)();

// The markup ships both hidden and the stub does not parse attributes, so
// without this the menu starts "open" and the first click closes it — which
// looks exactly like a pill that does not work.
el("#cmpFolderMenu").hidden = true;
el("#picker").hidden = true;
el("#cmpFolderPill").hidden = true;

const out = {};
const tick = () => new Promise((r) => setTimeout(r, 0));
const pill = () => el("#cmpFolderPill");
const menu = () => el("#cmpFolderMenu");
const rows = () => menu().children.filter((c) => c.dataset && c.dataset.folder);
const foot = () => menu().children.find((c) => c.classList.contains("cmp-folder-foot"));
const act = (text) => foot().children.find((b) => b._text === text);

try {
  // 1. Nothing known yet: the pill says nothing rather than guessing, and the
  //    guess here is the one that reads as "this agent is cut off".
  out.hidden = {
    beforeAnyAgent: pill().hidden === true,
    inPage: /id="cmpFolderPill"[^>]*hidden/.test(
      fs.readFileSync(path.join(WEB, "index.html"), "utf8")),
  };

  // 2. It reads the open agent's folders, and the label names them.
  await globalThis.__loadAgentFolders("inbox");
  await tick();
  out.loaded = {
    shown: pill().hidden === false,
    label: el("#cmpFolderLabel")._text,
    title: pill().title,
  };

  // 2. The menu lists every folder open to the app, ticking this agent's.
  pill().onclick({ stopPropagation() {} });
  out.menu = {
    opens: menu().hidden === false,
    rows: rows().length,
    on: rows().filter((r) => r.classList.contains("is-on"))
              .map((r) => r.dataset.folder),
    // What it is NOT, said on the menu — the other folder control in this app
    // does the opposite thing and the two were one word apart.
    saysItIsJustThisAgent: menu().children.some(
      (c) => (c._text || "").includes("shared brain")),
  };

  // 3. A second folder can be ticked. More than one at a time is the point.
  requests.length = 0;
  await rows()[1].onclick();
  await tick();
  let put = requests.find((r) => r.method === "PUT");
  out.ticked = {
    url: put && put.url,
    body: put && JSON.parse(put.body),
    label: el("#cmpFolderLabel")._text,
    menuStaysOpen: menu().hidden === false,
  };

  // 4. And untickable again.
  requests.length = 0;
  await rows()[0].onclick();
  await tick();
  put = requests.find((r) => r.method === "PUT");
  out.unticked = {
    body: put && JSON.parse(put.body),
    label: el("#cmpFolderLabel")._text,
  };

  // 5. "Look at no files" sends an EMPTY LIST, never null. Null would read
  //    back as undecided and the agent would quietly carry on reading.
  requests.length = 0;
  await act("Look at no files").onclick();
  await tick();
  put = requests.find((r) => r.method === "PUT");
  out.none = {
    body: put && JSON.parse(put.body),
    sentAList: put && Array.isArray(JSON.parse(put.body).folders),
    label: el("#cmpFolderLabel")._text,
  };

  // 6. Browsing for a new one adds it to what the agent already has rather
  //    than replacing it, and goes to the agent — not to the ingest flow.
  //    The press said "and this one too"; nobody means "and nothing else".
  STORED.inbox = ["/Users/x/notes"];
  await globalThis.__loadAgentFolders("inbox");
  await tick();
  await act("Add a folder…").onclick();
  await tick();
  const beforeConfirm = {
    modalOpen: el("#picker").hidden === false,
    title: el("#pickTitle")._text,
    cta: el("#pickConfirm")._text,
  };
  requests.length = 0;
  el("#pickConfirm").onclick();
  await tick();
  put = requests.find((r) => r.method === "PUT");
  out.browse = {
    ...beforeConfirm,
    body: put && JSON.parse(put.body),
    url: put && put.url,
    modalClosed: el("#picker").hidden === true,
  };

  // 7. The SAME modal, opened for ingesting, says what it is going to do.
  //    One browser, two actions, and the button never describes the wrong one.
  await globalThis.__openPicker();
  await tick();
  out.ingest = {
    title: el("#pickTitle")._text,
    cta: el("#pickConfirm")._text,
  };
  el("#pickClose").onclick();

  // 8. Switching agents re-reads it. An agent that was never asked follows
  //    whatever was already open, which is not the same as having nothing.
  await globalThis.__loadAgentFolders("chotu");
  await tick();
  out.perAgent = {
    chotu: el("#cmpFolderLabel")._text,
    chotuIsUndecided: STORED.chotu === null,
  };

  // 9. A refused save puts the label back rather than lying about what the
  //    agent can read.
  failNext = true;
  await globalThis.__loadAgentFolders("inbox");
  await tick();
  const before = el("#cmpFolderLabel")._text;
  pill().onclick({ stopPropagation() {} });
  await rows()[2].onclick();
  await tick();
  out.failed = { before, after: el("#cmpFolderLabel")._text };
  failNext = false;

  // 10. The profile tab is the same setting drawn in a second place, and it
  //     says which of the three answers is current.
  await globalThis.__loadAgentFolders("inbox");
  await tick();
  const pillBefore = el("#cmpFolderLabel")._text;
  const box = makeEl();
  await globalThis.__mountAgentFoldersIn(box, "chotu");
  await tick();
  const state = box.children.find((c) => c.classList.contains("fld-state"));
  out.pane = {
    rows: (box.children.find((c) => c.classList.contains("fld-list")) || { children: [] })
            .children.length,
    state: state && state._text,
    hasAdd: box.children.some((c) =>
      c.children && c.children.some((b) => b.id === "profFolderAdd")),
    // The rail's ⋯ opens ANY agent's profile without selecting it, so the tab
    // and the pill are routinely about two different agents. One shared "which
    // agent" would have repainted the composer with somebody else's folders.
    pillUntouched: el("#cmpFolderLabel")._text === pillBefore,
    pillWas: pillBefore,
  };

  // 11. And a press in the tab writes the TAB's agent, not the chat's.
  requests.length = 0;
  const tabRows = box.children.find((c) => c.classList.contains("fld-list")).children;
  await tabRows[2].onclick();
  await tick();
  put = requests.find((r) => r.method === "PUT");
  out.paneSave = {
    url: put && put.url,
    pillStillUntouched: el("#cmpFolderLabel")._text === pillBefore,
  };

  // 12. And the pill really is in the page, with the menu beside it.
  const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
  out.markup = {
    pill: html.includes('id="cmpFolderPill"'),
    menu: /id="cmpFolderMenu"[^>]*hidden/.test(html),
    // The dead one is gone rather than left behind to be wired by mistake.
    oldPillGone: !html.includes("cmpWorkspacePill"),
  };
} catch (e) {
  out.error = String((e && e.stack) || e);
}

process.stdout.write(JSON.stringify(out));
