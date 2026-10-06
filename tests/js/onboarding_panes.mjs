/**
 * Run the onboarding's pane switching and report WHAT IS REACHABLE on each
 * screen.
 *
 * The five screens are layered, not switched: each is `position:absolute;
 * inset:0` and is hidden with `opacity:0; pointer-events:none`. That hides a
 * screen from the eyes and from the mouse and from nothing else — `Tab` still
 * walks every control on every screen. On the finale, five of seven tab stops
 * were invisible, and one of them was "Build my brain", which erases the brain.
 *
 * No source-level check can see that, and neither can a screenshot. So the real
 * block is sliced out of `onboarding.html` and run against the smallest DOM it
 * touches, and this file reports the tab order the way a keyboard sees it.
 *
 * argv: <path to onboarding.html>
 * stdin: { stages: ["on","building"], inertSupported: true,
 *          accountDone: true }   // false = the account screen, which is first
 */
import fs from "node:fs";

const PAGE = process.argv[2];
const plan = JSON.parse(fs.readFileSync(0, "utf8") || "{}");

const src = fs.readFileSync(PAGE, "utf8");
const a = src.indexOf("// >>> pane-switching >>>");
const b = src.indexOf("// <<< pane-switching <<<");
if (a < 0 || b < 0) {
  console.error("pane-switching markers missing from onboarding.html");
  process.exit(2);
}
const block = src.slice(a, b);

function mkClassList(el) {
  const set = new Set();
  return {
    add: (...cs) => cs.forEach((c) => set.add(c)),
    remove: (...cs) => cs.forEach((c) => set.delete(c)),
    contains: (c) => set.has(c),
    toggle: (c, on) => { if (on) set.add(c); else set.delete(c); return set.has(c); },
    _set: set,
  };
}

/** An element that records the two things that decide reachability. */
function el(id, { focusable = false } = {}) {
  const e = {
    id, inert: false, focusable,
    style: {}, attrs: {},
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    hasAttribute(k) { return k in this.attrs; },
  };
  e.classList = mkClassList(e);
  return e;
}

// The panes, and the controls the tab order actually lands on. `owner` is the
// pane whose reachability governs the control, exactly as the browser applies
// `inert` down a subtree.
const nodes = {
  paneAccount: el("paneAccount"),
  paneHero: el("paneHero"),
  paneConnect: el("paneConnect"),
  paneBuild: el("paneBuild"),
  paneDigest: el("paneDigest"),
  continue: el("continue"),
  creq: el("creq"),
  skipTop: el("skipTop"),
  stepConnect: el("stepConnect"),
  stepBuild: el("stepBuild"),
  stepBrain: el("stepBrain"),
};

//: What a keyboard would reach, and which pane governs each one. The two that
//: matter most: `buildBtn` erases the brain, and `toBrain` finishes onboarding.
const CONTROLS = [
  // The account screen's sign-in buttons are rendered into `#acoButtons` by
  // `acoRender`, which this harness does not run — but reachability is governed
  // by the pane either way, exactly as the browser applies `inert` down a
  // subtree.
  { name: "acoButtons", owner: "paneAccount" },
  { name: "buildBtn", owner: "paneHero" },
  { name: "sourceTile", owner: "paneConnect" },
  { name: "continue", owner: "continue" },
  { name: "cancel", owner: "paneBuild" },
  { name: "bSkip", owner: "paneBuild" },
  { name: "toBrain", owner: "paneDigest" },
  { name: "skipTop", owner: "skipTop" },
];

const INERT_SUPPORTED = plan.inertSupported !== false;
// `inert` is a property on the prototype in every browser that implements it.
globalThis.HTMLElement = function HTMLElement() {};
if (INERT_SUPPORTED) HTMLElement.prototype.inert = false;

const stage = { classList: mkClassList() };
const document = { getElementById: (id) => nodes[id] || null };
const redrawn = [];

let error = null;
let result = null;
try {
  const make = new Function(
    "document", "stage", "redraw", "HTMLElement",
    // `passAccount` reaches `accountDone`, which is a `var` inside the sliced
    // block. Harness-side on purpose: the page has no reason to expose it, and
    // a setter added to production code for a test is a seam nobody asked for.
    block + "\nreturn { syncPanes: syncPanes, setStage: setStage, setLive: setLive,"
          + " passAccount: function(v){ accountDone = (v !== false); } };");
  const api = make(document, stage, () => redrawn.push(1), HTMLElement);

  // Default true, so every existing scenario still describes the screens AFTER
  // the account step — which is what they were written about.
  api.passAccount(plan.accountDone !== false);
  api.setStage(plan.stages || []);

  const hidden = (n) => (INERT_SUPPORTED ? n.inert === true : n.style.visibility === "hidden");
  result = {
    // The tab order as a keyboard sees it: a control inside an inert (or
    // visibility:hidden) subtree is simply not there.
    reachable: CONTROLS.filter((c) => !hidden(nodes[c.owner])).map((c) => c.name),
    // And the same question for assistive tech, which reads `aria-hidden`.
    exposed: Object.keys(nodes).filter((k) => nodes[k].getAttribute("aria-hidden") !== "true"),
    current: ["stepConnect", "stepBuild", "stepBrain"]
      .filter((s) => nodes[s].getAttribute("aria-current") === "step"),
    active: ["stepConnect", "stepBuild", "stepBrain"]
      .filter((s) => nodes[s].classList.contains("act")),
    done: ["stepConnect", "stepBuild", "stepBrain"]
      .filter((s) => nodes[s].classList.contains("done")),
    stageClasses: [...stage.classList._set].sort(),
    redrew: redrawn.length > 0,
  };
} catch (e) {
  error = String((e && e.stack) || e);
}

process.stdout.write(JSON.stringify({ error, ...(result || {}) }));
