/**
 * Build the Websites shelf and report what each card said.
 *
 * The claim a card makes is "you are connected to this, as this account" — and
 * the account is the part that can be wrong in a way nobody notices, because a
 * grant carries no username at all. So the cards are built for real and read
 * back, rather than trusted to a source-order check.
 *
 * Loads `connectors.js` alongside it, because the page does and because the
 * brand marks a hand-added site wears live there — testing this file alone
 * would test the fallback and call it the feature.
 *
 * argv: <browser.js> <connectors.js>   stdin: {grants: [...]}
 */
import fs from "node:fs";
const MARKS = process.argv[3] ? fs.readFileSync(process.argv[3], "utf8") : "";
const SRC = fs.readFileSync(process.argv[2], "utf8");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const els = {}; const mkEl = (id) => ({ id, innerHTML: "", value: "", onclick: null,
  disabled: false, querySelectorAll: () => [], hidden: false, textContent: "",
  classList: { add(){}, remove(){}, toggle(){}, contains: () => false } });
const el = (id) => (els[id] ||= mkEl(id));
const ctx = { $: (s) => el(String(s).replace(/^#/, "")), esc: (t) => String(t),
  IC: { connectors: "[generic]" }, api: async () => ({}), toast: () => {},
  setInterval: () => 1, clearInterval: () => {}, confirm: () => true,
  renderConnectRisk: () => {}, loadBrowserSites: () => {} };
// The whole file: the renderer and the catalogue sit either side of the setup
// code, and the top-level blocks only attach handlers to elements the fake
// already answers for.
// Only the marks half of connectors.js — the rest of that file attaches
// handlers to elements this fake does not model.
const MARK_SRC = MARKS
  ? MARKS.slice(MARKS.indexOf("const CONNECTOR_ICONS"),
                MARKS.indexOf("// \u2500\u2500 the connector list"))
  : "";
const run = new Function(...Object.keys(ctx),
  MARK_SRC + "\n" + SRC
  + "\nreturn { renderSiteShelf, siteSpec, siteAccount, SITE_CATALOG, siteSpecFor };");
const api = run(...Object.values(ctx));
api.renderSiteShelf(input.grants || []);
const html = el("webShelf").innerHTML.replace(/<svg[\s\S]*?<\/svg>/g, "[mark]").replace(/\s+/g, " ");
process.stdout.write(JSON.stringify({
  catalogue: api.SITE_CATALOG.map((x) => x.label),
  cards: [...html.matchAll(/<div class="site-nm">(.*?)<\/div> <div class="site-sub">(.*?)<\/div>/g)]
    .map((m) => [m[1], m[2]]),
  on: [...html.matchAll(/data-site-off="(.*?)"/g)].map((m) => m[1]),
  off: [...html.matchAll(/data-site-on="(.*?)"/g)].map((m) => m[1]),
  marks: (html.match(/\[mark\]/g) || []).length,
  caps: [...html.matchAll(/<span class="site-cap[^"]*">(.*?)<\/span>/g)].map((m) => m[1]),
  // Every control that would take a site away. Two of them for one site is
  // the duplication this screen had, and nothing was watching for it.
  removers: [...html.matchAll(/data-(site-off|webdel)="(.*?)"/g)].map((m) => m[2]),
  toggles: [...html.matchAll(/data-webact="(.*?)"/g)].map((m) => m[1]),
  // What a hand-added host would wear, so "it gets its own logo" is checked
  // rather than hoped for.
  byHand: Object.fromEntries((input.probe || []).map((h) => {
    const spec = api.siteSpecFor(h);
    return [h, { tint: spec.tint, generic: spec.icon === "[generic]",
                 monogram: /<text/.test(spec.icon) }];
  })),
}));
