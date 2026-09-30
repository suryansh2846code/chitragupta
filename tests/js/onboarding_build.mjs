/**
 * Drive the onboarding build screen and report WHEN it handed over.
 *
 * The whole value of this block is its timing — that the digest screen does not
 * appear until the first enrichment pass is done and there is something true to
 * put on the cards. No source-level check can see that, so the real block is
 * sliced out of `onboarding.html` and run against a scripted backend with a
 * scripted clock: the 700ms interval is stepped by hand, and `Date.now` is a
 * number this file controls, so a 150-second patience window costs no seconds.
 *
 * argv: <path to onboarding.html>
 * stdin: { ticks, stats, sync, enrich, digest, clickSkipAtTick }
 *   stats/sync/enrich are arrays read by tick index (last entry repeats).
 *   digest is what fetchDigestData resolves with, after `digestDelayTicks`.
 */
import fs from "node:fs";

const PAGE = process.argv[2];
const plan = JSON.parse(fs.readFileSync(0, "utf8"));

const src = fs.readFileSync(PAGE, "utf8");
const a = src.indexOf("// >>> build-progress >>>");
const b = src.indexOf("// <<< build-progress <<<");
if (a < 0 || b < 0) {
  console.error("build-progress markers missing from onboarding.html");
  process.exit(2);
}
const block = src.slice(a, b);

// ── scripted clock ──────────────────────────────────────────────────────────
let clock = 1_000_000;
const TICK = 700;
const at = (arr, i) => arr[Math.min(i, arr.length - 1)];

// ── the smallest DOM the block touches ──────────────────────────────────────
const node = () => ({
  textContent: "", style: {}, hidden: false, disabled: false, onclick: null,
  attrs: {},
  setAttribute(k, v) { this.attrs[k] = String(v); },
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
  removeAttribute(k) { delete this.attrs[k]; },
});
const els = {
  bFill: node(), bPct: node(), bDet: node(), stItems: node(), stEnt: node(),
  bSkip: node(),
  // The bar reports its value to assistive tech as well as to the eye, and the
  // warning row is how a stalled sync reaches the screen at all.
  bBar: node(), bWarn: node(), bWarnText: node(), bRetry: node(),
};
els.bWarn.classList = mkClassList();
const bWhat = node();
const steps = [node(), node(), node()];
for (const s of steps) s.classList = mkClassList();
const stepBuild = node(); stepBuild.classList = mkClassList();

function mkClassList() {
  const set = new Set();
  // Varargs, like the real DOM: `classList.add("on", "building")` is one call
  // adding two classes, and a single-argument fake silently drops the second.
  return { add: (...cs) => cs.forEach((c) => set.add(c)),
           remove: (...cs) => cs.forEach((c) => set.delete(c)),
           contains: (c) => set.has(c), _set: set };
}

const stage = { classList: mkClassList() };
const digestEl = { classList: mkClassList() };

const document = {
  getElementById: (id) => els[id] || null,
  querySelector: (sel) => {
    if (sel === ".bWhat") return bWhat;
    if (sel === ".digest") return digestEl;
    if (sel === '.step[id="stepBuild"]') return stepBuild;
    return null;
  },
  querySelectorAll: () => steps,
};

// Every stage transition in the page goes through `setStage`, which also
// re-inerts the panes — the block under test only ever asks for a state, so the
// harness records which one and nothing more.
const stages = [];
const setStage = (names) => {
  stages.push(names.slice());
  stage.classList.remove("on", "building", "brainready");
  names.forEach((n) => stage.classList.add(n));
};
const focused = [];
const focusPane = (id) => focused.push(id);

// ── the scripted backend ────────────────────────────────────────────────────
let tick = 0;
const calls = [];
const api = (path) => {
  calls.push(path);
  if (path.startsWith("/api/brain/stats")) return Promise.resolve(at(plan.stats, tick));
  if (path.startsWith("/api/sync/status")) return Promise.resolve(at(plan.sync, tick));
  if (path.startsWith("/api/brain/enrich/status")) return Promise.resolve(at(plan.enrich, tick));
  if (path.startsWith("/api/brain/enrich/start")) return Promise.resolve({ running: true });
  return Promise.resolve({});
};

let digestFetches = 0;
const fetchDigestData = () => {
  digestFetches += 1;
  return Promise.resolve(plan.digest === undefined ? null : plan.digest);
};

let filledWith = undefined, filledAtTick = -1;
const fillCards = (d) => { filledWith = d; filledAtTick = tick; };

// ── run ─────────────────────────────────────────────────────────────────────
let loop = null;
const setInterval_ = (fn) => { loop = fn; return 1; };
const clearInterval_ = () => { loop = null; };
// The block hands over inside a setTimeout; run it immediately so the harness
// observes the same end state the browser would half a second later.
const setTimeout_ = (fn) => { fn(); return 1; };

let error = null;
const timeline = [];
try {
  const make = new Function(
    "document", "stage", "api", "ripplePop", "fetchDigestData", "fillCards",
    "digestDone", "localStorage", "Date", "setInterval", "clearInterval", "setTimeout",
    "setStage", "focusPane",
    block + "\nreturn runBuildProgress;");
  const runBuildProgress = make(
    document, stage, api, () => {}, fetchDigestData, fillCards,
    false, { getItem: () => null }, { now: () => clock },
    setInterval_, clearInterval_, setTimeout_,
    setStage, focusPane);

  stage.classList.add("on", "building");
  runBuildProgress(0);

  for (tick = 0; tick < plan.ticks && loop; tick++) {
    if (plan.clickSkipAtTick === tick && els.bSkip.onclick) els.bSkip.onclick();
    await loop();
    timeline.push({
      tick, pct: els.bPct.textContent, what: bWhat.textContent,
      detail: els.bDet.textContent, skipShown: !els.bSkip.hidden,
      skipLabel: els.bSkip.textContent, skipDisabled: els.bSkip.disabled,
      ariaNow: els.bBar.getAttribute("aria-valuenow"),
      warn: els.bWarn.classList.contains("show") ? els.bWarnText.textContent : "",
      handedOver: stage.classList.contains("brainready"),
    });
    clock += TICK;
  }
} catch (e) {
  error = String((e && e.stack) || e);
}

process.stdout.write(JSON.stringify({
  error,
  handedOver: stage.classList.contains("brainready"),
  stillBuilding: stage.classList.contains("building"),
  handedOverAtTick: filledAtTick,
  filledWith: filledWith === undefined ? null : filledWith,
  digestFetches,
  startedEnrichment: calls.some((c) => c.startsWith("/api/brain/enrich/start")),
  stages, focused,
  timeline,
}));
