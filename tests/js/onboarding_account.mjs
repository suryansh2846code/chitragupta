/**
 * Run the onboarding's first screen and report whether it can be got past.
 *
 * Making an account is step one, with no skip — so the question this harness
 * exists to answer is the dangerous one: **can it trap somebody?** A gate whose
 * key does not exist is not a strict app, it is a bricked one, and there are
 * three ways a user could arrive without a key:
 *
 *   1. the build has no OAuth client configured at all;
 *   2. `/api/account/state` cannot be reached;
 *   3. the sign-in was started and never finished.
 *
 * The first two must pass the user through. The third must leave the screen
 * usable rather than a dead spinner. None of that is visible to a grep, and a
 * source-order assertion would pass on every one of them.
 *
 * The real block is sliced out of `onboarding.html`, so a rename or a lost
 * branch fails here exactly as it would in a browser. `accountDone` is a `var`
 * inside that block and so invisible from out here, which is why the slice has
 * its assignment rewritten onto a global the harness can read — the page keeps
 * no test seam of its own.
 *
 * argv: <path to onboarding.html>
 * stdin: { state, begin, finish, open: {...} | {"__fail": "why"},
 *          press: "google" | null }
 */
import fs from "node:fs";

const PAGE = process.argv[2];
const plan = JSON.parse(fs.readFileSync(0, "utf8") || "{}");

const src = fs.readFileSync(PAGE, "utf8");
const a = src.indexOf("// >>> account-step >>>");
const b = src.indexOf("// <<< account-step <<<");
if (a < 0 || b < 0) {
  console.error("account-step markers missing from onboarding.html");
  process.exit(2);
}
const block = src.slice(a, b);

const calls = [];
const suppressions = [];
let synced = 0;
let focused = null;

/** The two elements the block writes into. */
function el(id) {
  return {
    id, _html: "", _text: "",
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    /** The buttons the renderer drew, found the way `acoRender` finds them. */
    querySelectorAll(sel) {
      if (!sel.startsWith(".")) return [];
      const wanted = sel.slice(1);
      const out = [];
      for (const [tag] of this._html.matchAll(/<button[^>]*>/gi)) {
        if (!new RegExp(`class="[^"]*\\b${wanted}\\b`).test(tag)) continue;
        const provider = /\bdata-provider="([^"]*)"/.exec(tag);
        out.push({
          getAttribute: (k) => (k === "data-provider"
            ? (provider ? provider[1] : null) : null),
          addEventListener() {},
        });
      }
      return out;
    },
  };
}

const nodes = { acoButtons: el("acoButtons"), acoNote: el("acoNote") };

function answer(key, fallback) {
  const given = plan[key];
  if (given === undefined) return fallback;
  if (given && given.__fail) {
    const why = typeof given.__fail === "string" ? given.__fail : "unreachable";
    return Promise.reject(new Error(why));
  }
  return given;
}

const api = async (path, opts) => {
  calls.push({
    path,
    method: (opts && opts.method) || "GET",
    body: opts && opts.body ? JSON.parse(opts.body) : null,
  });
  if (path === "/api/account/state") return answer("state", {});
  if (path === "/api/account/signin/begin") {
    return answer("begin", { url: "https://accounts.google.com/x", port: 1 });
  }
  if (path === "/api/account/signin/finish") return answer("finish", {});
  if (path === "/api/open-browser") return answer("open", {});
  return {};
};

const esc = (s) => String(s == null ? "" : s)
  .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let error = null;
let result = null;
try {
  if (!/\baccountDone\s*=\s*true\b/.test(block)) {
    throw new Error("the account step no longer sets accountDone — this harness "
                    + "reads that assignment to know the gate opened");
  }
  globalThis.__done = false;

  const bridge = new Function(
    "document", "api", "esc", "suppressed", "syncPanes", "focusPane",
    block.replace(/\baccountDone\s*=\s*true\b/g, "globalThis.__done = true")
      + "\nreturn { load: acoLoad, signIn: acoSignIn, render: acoRender };",
  )(
    { getElementById: (id) => nodes[id] || null },
    api,
    esc,
    (what, e) => suppressions.push(`${what}: ${e && e.message}`),
    () => { synced += 1; },
    (id) => { focused = id; },
  );

  await bridge.load();
  if (plan.press) await bridge.signIn(plan.press);

  const html = nodes.acoButtons._html;
  result = {
    passed: !!globalThis.__done,
    calls,
    suppressions,
    synced,
    focused,
    note: nodes.acoNote._text,
    // One entry per button the screen drew, live or locked.
    buttons: [...html.matchAll(/<button[^>]*>/gi)].map(([tag]) => ({
      provider: (/\bdata-provider="([^"]*)"/.exec(tag) || [])[1] || null,
      locked: /\bdisabled\b/.test(tag),
      title: (/\btitle="([^"]*)"/.exec(tag) || [])[1] || null,
    })),
  };
} catch (e) {
  error = String((e && e.stack) || e);
}

process.stdout.write(JSON.stringify(
  error ? { harness_error: error, calls } : result));
