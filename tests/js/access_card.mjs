/**
 * Render the access panel from a real `<action>` tag, then press a switch.
 *
 * The thing being replaced was an agent writing *"Settings → Agents & tools →
 * Health & Fitness → let it browse the web, plus allow changes on amazon.in.
 * Say 'go' when it's on."* — so every claim worth making here is about what the
 * chat ends up holding instead, and none of it is visible from the source:
 *
 *  - the panel draws a switch per thing asked for, labelled with the server's
 *    sentence rather than the `group:level` pair;
 *  - there is NO Confirm. Each switch is the decision, which is the only shape
 *    that can express "allow two of these three";
 *  - a press grants exactly the key it was drawn for, and repaints from what
 *    the server read back — never from what the client hoped it sent;
 *  - the agent's own reason survives from the tag's body onto the card;
 *  - a failed grant says so on the row that failed.
 *
 * argv: <a path inside chitragupta/web/>   stdin: {tag, rows, afterGrant, fail}
 */
import fs from "node:fs";
import path from "node:path";

import { appSource } from "./_app_source.mjs";

const APP_JS = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, "utf8"));

const posted = [];

const makeEl = (tag = "div") => {
  const e = {
    tag, className: "", value: "", hidden: false, disabled: false, title: "",
    textContent: "", style: {}, dataset: {}, _attrs: {}, _kids: [], onclick: null,
    scrollTop: 0, scrollHeight: 100,
    classList: {
      _on: new Set(),
      add(c) { this._on.add(c); }, remove(c) { this._on.delete(c); },
      toggle(c, v) { if (v) this._on.add(c); else this._on.delete(c); },
      contains(c) { return this._on.has(c); },
    },
    addEventListener() {}, focus() {}, remove() {},
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k] ?? null; },
    removeAttribute(k) { delete this._attrs[k]; },
    closest: () => null,
    appendChild(c) { e._kids.push(c); return c; },
    insertBefore(c) { e._kids.push(c); return c; },
  };
  let html = "";
  Object.defineProperty(e, "innerHTML", {
    get: () => html, set: (v) => { html = String(v); },
  });
  // Two things the panel does to its own DOM, and the harness has to do both
  // for real or it is testing a different component.
  //
  // It writes its rows into a `.acc-rows` box it looked up on the card — so a
  // class selector has to come back as a persistent element, the same one every
  // time, or the card writes into a throwaway and nothing is ever on screen.
  //
  // Then it re-finds the controls it just wrote and binds handlers to them. So
  // the buttons are parsed back out of the markup, the way `create_agent.mjs`
  // does it: a press runs the handler the renderer actually bound, and a
  // repaint re-binds rather than leaving handlers on nodes nobody can click.
  e._parts = {};
  // **The same button object every time, until the markup changes.** The panel
  // writes its rows, then looks the controls up again to bind handlers to them;
  // a stub that minted a fresh object per lookup would hand the test a button
  // the renderer had never seen, with no handler on it. Re-minting on a repaint
  // is the other half: handlers bound to the previous rows are handlers on
  // nodes nobody can click, which is a real bug worth being able to catch.
  let found = {}, foundFor = null;
  e.querySelectorAll = (sel) => {
    const attr = /\[data-([a-z-]+)\]/.exec(sel);
    if (!attr) return [];
    if (foundFor !== html) { found = {}; foundFor = html; }
    if (found[sel]) return found[sel];
    const camel = attr[1].replace(/-([a-z])/g, (m, c) => c.toUpperCase());
    const re = new RegExp(`data-${attr[1]}="([^"]*)"`, "g");
    found[sel] = [...html.matchAll(re)].map((m) => {
      const b = makeEl("button");
      b.dataset[camel] = m[1];
      b.closest = () => ({ querySelector: () => b._err || (b._err = makeEl()) });
      return b;
    });
    return found[sel];
  };
  e.querySelector = (sel) => {
    const cls = /^\.([a-z-]+)$/.exec(sel);
    if (cls) {
      if (!html.includes(`class="${cls[1]}"`)
          && !html.includes(`class="${cls[1]} `)
          && !html.includes(` ${cls[1]}"`)) return null;
      return (e._parts[cls[1]] ||= makeEl());
    }
    return e.querySelectorAll(sel)[0] || null;
  };
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
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 0;

let grants = 0;
globalThis.fetch = async (url, opts = {}) => {
  const p = String(url);
  const body = opts.body ? JSON.parse(opts.body) : null;
  if (/\/access\/grant$/.test(p)) {
    posted.push({ path: p, body });
    grants += 1;
    if (input.fail) {
      return { ok: false, status: 400,
               json: async () => ({ detail: input.fail }) };
    }
    return { ok: true, json: async () => ({ ok: true, detail: "Done.",
                                            rows: input.afterGrant }) };
  }
  if (/\/access$/.test(p)) {
    posted.push({ path: p, body });
    return { ok: true, json: async () => ({ rows: input.rows }) };
  }
  return { ok: true, json: async () => ({}) };
};

const report = { error: null };
try {
  new Function(
    appSource(path.dirname(APP_JS)) +
    "\nglobalThis.__parse = parsePlans;" +
    "\nglobalThis.__card = actionCard;" +
    "\nagents = [{ id: 'health', name: 'Health & Fitness', role: 'r' }];" +
    "\ncurrent = 'health';" +
    "\ntoast = () => {};"
  ).call(globalThis);

  // From the tag the model writes, through the real parser, into the real card.
  const { clean, actions } = globalThis.__parse(input.tag);
  report.clean = clean.trim();
  report.parsed = actions.map((a) => ({ type: a.type, params: a.params }));
  const card = globalThis.__card(actions[0]);
  await new Promise((r) => setTimeout(r, 0));     // the state fetch settles

  report.cardClass = card.className;
  report.risk = card.dataset.risk;
  report.head = /<div class="ac-head">([\s\S]*?)<\/div>/.exec(card.innerHTML)?.[1] || "";
  report.why = /<div class="ac-body acc-why">([\s\S]*?)<\/div>/.exec(card.innerHTML)?.[1] || "";
  report.hasConfirm = card.innerHTML.includes("ac-confirm");
  // The rows are drawn into the panel's own box, not into the card's markup —
  // so that is what a person is looking at, and that is what is read back.
  const box = card.querySelector(".acc-rows");
  report.html = box.innerHTML;
  const all = (re) => [...box.innerHTML.matchAll(re)].map((m) => m[1]);
  report.switches = all(/data-grant="([^"]*)"/g);
  report.opens = all(/data-opens="([^"]*)"/g);
  report.titles = all(/<b>([\s\S]*?)<\/b>/g);
  report.onRows = all(/data-row="([^"]*)"[^>]*>(?=[\s\S]*?acc-on)/g);
  report.onCount = (box.innerHTML.match(/class="acc-on"/g) || []).length;

  // Press the switch the test named, through the handler the renderer bound.
  if (input.press) {
    const found = box.querySelectorAll("[data-grant]")
      .find((b) => b.dataset.grant === input.press);
    report.pressed = !!found;
    if (found) {
      await found.onclick();
      await new Promise((r) => setTimeout(r, 0));
      report.afterSwitches = [...box.innerHTML.matchAll(/data-grant="([^"]*)"/g)]
        .map((m) => m[1]);
      report.afterOn = (box.innerHTML.match(/class="acc-on"/g) || []).length;
      report.rowError = found._err ? found._err.textContent : "";
      report.stillDisabled = found.disabled;
    }
  }
  report.posted = posted;
  report.grants = grants;
} catch (e) {
  report.error = String((e && e.stack) || e);
}
process.stdout.write(JSON.stringify(report));
process.exit(0);
