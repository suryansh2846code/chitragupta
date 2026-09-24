/**
 * Drive the in-app browser screen and report what it showed and what it sent.
 *
 * The two things worth executing here are invisible to a source-order check:
 *
 *  - a frame has to reach the `<img>` as a data URL, and the address has to
 *    reach the address bar, because a page shown without its address is the one
 *    thing a browser must never do;
 *  - a click on the picture has to arrive at the server as a point on the PAGE.
 *    The image is letterboxed (`object-fit: contain`), so image pixels and page
 *    pixels differ by a scale factor, and getting that wrong puts every click a
 *    few pixels out — which on a dense page is a different control.
 *
 * argv: <webscreen.js>  stdin: {frame: {...}, clicks: [{clientX, clientY}], rect}
 */
import fs from "node:fs";

const SRC = fs.readFileSync(process.argv[2], "utf8");
const input = JSON.parse(fs.readFileSync(0, "utf8"));

const calls = [];
const els = {};

function mk(id) {
  const node = {
    id, value: "", src: "", textContent: "", hidden: false, tabIndex: 0,
    onclick: null, onwheel: null, onkeydown: null,
    getBoundingClientRect: () => input.rect,
    focus() {}, addEventListener() {},
  };
  return node;
}
const el = (sel) => (els[sel] ||= mk(sel));

globalThis.$ = el;
globalThis.document = {
  activeElement: null,
  querySelector: el,
  querySelectorAll: () => [],
  addEventListener() {},
};
globalThis.window = { addEventListener() {} };
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => {};

globalThis.api = async (path, opts) => {
  calls.push({
    path,
    method: (opts && opts.method) || "GET",
    body: opts && opts.body ? JSON.parse(opts.body) : null,
  });
  if (path === "/api/browser/view") return input.frame;
  return { ok: true };
};

// Evaluated the way the page loads it — a plain script, not a module — and the
// entry points handed back, because `new Function` gives the source its own
// scope and its top-level functions never reach `globalThis`.
let error = null;
let mod = {};
try {
  // eslint-disable-next-line no-new-func
  mod = new Function(`${SRC}\n;return {openWebScreen, closeWebScreen};`)();
} catch (e) {
  error = `${e.message}`;
}

const out = { error, frames: [], sent: [] };

if (!error) {
  await mod.openWebScreen();
  out.frames.push({
    src: el("#wsFrame").src.slice(0, 40),
    url: el("#wsUrl").value,
    title: el("#wsTitle").textContent,
    hidden: el("#webScreen").hidden,
  });

  for (const click of input.clicks || []) {
    calls.length = 0;
    await el("#wsFrame").onclick(click);
    out.sent.push(calls.filter((c) => c.method === "POST").map((c) => c.body));
  }

  // Closing must stop the polling, or a panel nobody is looking at goes on
  // screenshotting a page several times a second.
  mod.closeWebScreen();
  out.closedHidden = el("#webScreen").hidden;
}

console.log(JSON.stringify(out));
