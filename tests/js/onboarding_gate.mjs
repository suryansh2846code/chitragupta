/**
 * Run the onboarding's Continue gate and report what it lets through.
 *
 * What this gate accepts is the difference between a first run that works and
 * one that dead-ends, and the two ways it used to be wrong were both invisible
 * to a reader of the source:
 *
 *   1. `selected["gmail"]` — a requirement of nothing. The digest files an
 *      entity by what it IS, never by where it came from, so a brain of Notion
 *      pages works as well as one of email. What the check actually did was
 *      dead-end every user who does not have Gmail.
 *   2. `localStorage.getItem("chitragupta_provider")` — the presence of a
 *      string. Picking a cloud provider and leaving the key blank wrote that
 *      string, lit the button, and three minutes later the finale said
 *      "Connect an AI model" to somebody who just had.
 *
 * argv: <path to onboarding.html>
 * stdin: { selected: {...}, llmReady: bool, provider: "anthropic"|null }
 */
import fs from "node:fs";

const PAGE = process.argv[2];
const plan = JSON.parse(fs.readFileSync(0, "utf8") || "{}");

const src = fs.readFileSync(PAGE, "utf8");
const a = src.indexOf("// >>> connect-gate >>>");
const b = src.indexOf("// <<< connect-gate <<<");
if (a < 0 || b < 0) {
  console.error("connect-gate markers missing from onboarding.html");
  process.exit(2);
}
const block = src.slice(a, b);

const selected = plan.selected || {};
const selCount = () => Object.values(selected).filter(Boolean).length;
const localStorage = { getItem: (k) => (k === "chitragupta_provider" ? (plan.provider ?? null) : null) };

let error = null, result = null;
try {
  const make = new Function(
    "selCount", "localStorage",
    block + "\nreturn { set: (v) => { llmReady = v; }, canContinue, gateHint, llmName };");
  const api = make(selCount, localStorage);
  api.set(!!plan.llmReady);
  result = { canContinue: api.canContinue(), hint: api.gateHint(), llmName: api.llmName() };
} catch (e) {
  error = String((e && e.stack) || e);
}

process.stdout.write(JSON.stringify({ error, ...(result || {}) }));
