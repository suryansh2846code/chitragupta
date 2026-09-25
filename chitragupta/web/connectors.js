/**
 * Adding a source, and setting one up.
 *
 * The per-connector setup help, the custom-app form, the connector catalog
 * browser, and the permission screen shown before an MCP server is added.
 *
 * **Consent to something nobody has been shown is not consent.** A connector's
 * tools are read from the server and displayed *before* it is added
 * (`connectorPermissions`), because "add" is the only moment the user is asked,
 * and a list they never saw is not a decision they made.
 *
 * Setup instructions are per-source prose, not a generic form. Every field a
 * user types lands in the chmod-600 secrets store through
 * `POST /api/connectors/{name}/secret` — never in a file in the repo, and never
 * asked for at a terminal.
 */

// ── what each source looks like, and where it belongs ──────────────────────
// The marks are drawn here rather than fetched: every asset a page pulls from
// a vendor's CDN is a request that says which app the user is running, and the
// whole product promise is that nothing leaves the machine. They are simple
// recognisable shapes in each brand's colour, not pixel copies.
//
// Keyed by connector name from REGISTRY, so a connector without an entry still
// renders — it falls back to a neutral mark and its own group. Adding a
// connector never needs an edit here to keep working.
const CONNECTOR_ICONS = {
  gmail: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#fff" d="M3 6.5A1.5 1.5 0 0 1 4.5 5h15A1.5 1.5 0 0 1 21 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>
    <path fill="#EA4335" d="M3 6.9 12 13l9-6.1v2.3L12 15.4 3 9.2z"/></svg>`,
  gcal: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="4.5" width="18" height="16" rx="2.5" fill="#fff"/>
    <rect x="3" y="4.5" width="18" height="4" rx="2.5" fill="#4285F4"/>
    <text x="12" y="17" font-size="8.5" font-weight="700" text-anchor="middle" fill="#4285F4" font-family="Helvetica,Arial">31</text></svg>`,
  gdrive: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#0F9D58" d="m8.5 3.5 7 0 4.5 8-3.5 0z"/>
    <path fill="#F4B400" d="m20 11.5-3.5 6-7 0 3.5-6z"/>
    <path fill="#4285F4" d="M8.5 3.5 4 11.5l3.5 6 3.5-6z"/></svg>`,
  notion: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="3" width="18" height="18" rx="3.5" fill="#fff"/>
    <path fill="#111" d="M8 8.2h1.9l4 5.6V8.2h1.6v7.6h-1.8l-4.1-5.8v5.8H8z"/></svg>`,
  github: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#e6edf3" d="M12 2.2a9.8 9.8 0 0 0-3.1 19.1c.5.1.7-.2.7-.5v-1.8c-2.7.6-3.3-1.3-3.3-1.3-.5-1.1-1.1-1.4-1.1-1.4-.9-.6.1-.6.1-.6 1 .1 1.5 1 1.5 1 .9 1.5 2.3 1.1 2.9.8.1-.6.3-1.1.6-1.3-2.2-.3-4.5-1.1-4.5-4.9 0-1.1.4-2 1-2.7-.1-.3-.4-1.3.1-2.7 0 0 .8-.3 2.7 1a9.4 9.4 0 0 1 5 0c1.9-1.3 2.7-1 2.7-1 .5 1.4.2 2.4.1 2.7.6.7 1 1.6 1 2.7 0 3.8-2.3 4.6-4.5 4.9.4.3.7.9.7 1.9v2.8c0 .3.2.6.7.5A9.8 9.8 0 0 0 12 2.2"/></svg>`,
  linear: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="3" width="18" height="18" rx="4.5" fill="#5E6AD2"/>
    <path fill="#fff" d="M7 13.4 10.6 17a5.6 5.6 0 0 1-3.6-3.6m-.3-2.1 5.9 5.9q.8-.1 1.5-.4L7.1 9.8q-.3.7-.4 1.5m.9-2.8 7.6 7.6q.5-.4.9-.9L8.5 7.6q-.5.4-.9.9m2-1.4 7.1 7.1A5.7 5.7 0 0 0 9.6 7.1"/></svg>`,
  imessage: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="2.5" y="2.5" width="19" height="19" rx="5" fill="#34C759"/>
    <path fill="#fff" d="M12 6.4c-3.4 0-6.1 2.2-6.1 5s2.7 5 6.1 5q.8 0 1.5-.2l2.7 1.3-.7-2.3c1.6-.9 2.6-2.3 2.6-3.8 0-2.8-2.7-5-6.1-5"/></svg>`,
  apple_mail: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="2.5" y="4.5" width="19" height="15" rx="4" fill="#1F8DFB"/>
    <path fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round" d="m5.5 8.5 6.5 5 6.5-5"/></svg>`,
  apple_calendar: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="4.5" width="18" height="16" rx="3.5" fill="#fff"/>
    <rect x="3" y="4.5" width="18" height="4.5" rx="3.5" fill="#FF3B30"/>
    <text x="12" y="17.5" font-size="8.5" font-weight="600" text-anchor="middle" fill="#1c1c1e" font-family="Helvetica,Arial">17</text></svg>`,
  files: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#54A0FF" d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
  notes: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="4" y="3" width="16" height="18" rx="2.5" fill="#FFD60A"/>
    <path stroke="#8a6d00" stroke-width="1.4" stroke-linecap="round" d="M8 8h8M8 12h8M8 16h5"/></svg>`,
};

//: A neutral mark for anything without one — a custom API app, an MCP server,
//: or a connector added after this file was last touched.
const CONNECTOR_ICON_FALLBACK = `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
  <rect x="3" y="3" width="18" height="18" rx="4.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <path stroke="currentColor" stroke-width="1.5" stroke-linecap="round" d="M8 12h8M12 8v8"/></svg>`;

//: One line saying what the source actually gives the brain.
//:
//: No `group` any more — which group a source belongs to is now `on_device`,
//: answered by the connector itself. It was a map here, four connectors were
//: missing from it, and Slack, Telegram, Apple Health and Google Fit fell
//: through to a heading that said "Custom sources" about four things nobody
//: had customised. A fact the backend knows does not get a second copy here.
const CONNECTOR_META = {
  gmail:          { desc: "Your mail, read-only — threads, senders and what you agreed to." },
  gcal:           { desc: "Meetings, who is in them, and what your week looks like." },
  apple_mail:     { desc: "Mail from the Mail app on this Mac." },
  apple_calendar: { desc: "Events from the Calendar app on this Mac." },
  imessage:       { desc: "Messages on this Mac — who you talk to and about what." },
  notion:         { desc: "Pages and databases from your Notion workspace." },
  gdrive:         { desc: "Documents in your Drive, read-only." },
  files:          { desc: "A folder on this machine, indexed where it sits." },
  notes:          { desc: "Anything you type in yourself — the highest-trust source." },
  github:         { desc: "Issues, pull requests and what you are shipping." },
  linear:         { desc: "Issues, projects and cycles from your Linear workspace." },
  slack:          { desc: "Channels a bot has been invited to, and what was said in them." },
  telegram:       { desc: "Your Telegram conversations, read from this Mac." },
  apple_health:   { desc: "Readings from an Apple Health export — numbers, never memories." },
  google_fit:     { desc: "Activity and body measurements from your Google account." },
};

//: The two kinds of source, which is the difference a person can act on.
//:
//: Grouped by **where the data is**, not by topic. Topic was the old split
//: (mail / chat / docs / code) and it answered a question nobody was asking:
//: every row already says what it gives you. What no row said is whether
//: anything leaves this Mac, which is the entire product promise and the one
//: thing a person deciding whether to connect something actually wants.
const CONNECTOR_GROUPS = [
  { id: "device", title: "On this Mac",
    sub: "Read straight off this machine. No account is involved and nothing leaves it." },
  { id: "account", title: "Your accounts",
    sub: "You sign in with the service itself, and Chitragupta talks to it from this Mac. Nothing is routed through us." },
];

//: How a source is reached, as a word on the row.
//:
//: This names the mechanism, which `/CLAUDE.md` would normally call an
//: internal — the original note here said MCP is "an implementation detail
//: they never need". That was reversed deliberately: the person running this
//: asked to see which sources are ours and which are the vendor's own server,
//: because it decides who to chase when one misbehaves. The *sections* stay
//: jargon-free; only the tag names the route.
const CONNECTOR_KINDS = {
  builtin: { label: "Built-in", title: "Written into Chitragupta. We maintain it." },
  mcp:     { label: "MCP", title: "The service's own server, speaking the Model Context Protocol. The vendor maintains it and owns the schema." },
  custom:  { label: "Custom", title: "An API you pointed Chitragupta at yourself." },
};

//: The colour each mark already wears, lifted from its own artwork above, so a
//: tile's glow is that product's colour and not one house colour applied to
//: eleven different logos. Where the mark is monochrome (Notion, GitHub) the
//: value is the ink it is drawn in. Anything missing falls through to --star
//: via `.logo-tile`, which is the right answer for a custom app or an MCP
//: server: we do not know its colour, so we do not invent one.
const CONNECTOR_TINT = {
  gmail: "#EA4335", gcal: "#4285F4", gdrive: "#0F9D58",
  notion: "#ffffff", github: "#e6edf3", linear: "#5E6AD2",
  imessage: "#34C759", apple_mail: "#1F8DFB", apple_calendar: "#FF3B30",
  files: "#54A0FF", notes: "#FFD60A",
};
function connectorIcon(name) {
  return CONNECTOR_ICONS[name] || CONNECTOR_ICON_FALLBACK;
}

//: A stable colour for a source we have no mark for.
//:
//: Derived from the id, the way `character.js` composes an agent's face from
//: its id — so a catalog of two dozen reads as two dozen objects rather than
//: one grey shape repeated, and a service added next year gets its own colour
//: without anybody picking one. Never fetched: an image pulled from a vendor's
//: CDN is a request that tells them which app the user is running, which is
//: the whole reason the marks above are drawn by hand.
function connectorHue(id) {
  let h = 0;
  for (let i = 0; i < String(id).length; i++) h = (h * 31 + String(id).charCodeAt(i)) % 360;
  return h;
}

//: The name a mark is looked up by.
//:
//: An MCP connector is `mcp:notion` and a custom app is `custom:<id>`, so a
//: straight lookup missed every one of them — Notion sat in the list wearing
//: the blank fallback while its mark was right there under `notion`. The
//: route is not part of the brand.
function markKey(name) {
  const at = String(name).indexOf(":");
  return at === -1 ? String(name) : String(name).slice(at + 1);
}

//: The mark for a source: ours if we drew one, a monogram if not.
//:
//: A monogram rather than the neutral plus-in-a-box, because twenty identical
//: fallbacks in a row is a list you cannot scan. It also does not pretend to
//: be somebody's logo, which a rough hand-drawn approximation would.
function connectorMark(id, name) {
  if (CONNECTOR_ICONS[id]) return CONNECTOR_ICONS[id];
  const letter = String(name || id).trim().charAt(0).toUpperCase() || "?";
  return `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <text x="12" y="16.5" font-size="12" font-weight="700" text-anchor="middle"
      fill="currentColor" font-family="var(--sans), Helvetica, Arial"
      >${esc(letter)}</text></svg>`;
}
function connectorTint(name) {
  return CONNECTOR_TINT[name] || "";
}
function connectorMeta(name) {
  return CONNECTOR_META[name] || { desc: "" };
}
//: Which section a row sits in, from the row itself rather than a lookup.
function connectorGroup(c) {
  return c.on_device ? "device" : "account";
}

// ── the connector list ─────────────────────────────────────────────────────
// Grouped, with each source's own mark, because a flat list of eleven names in
// one column is a list you read rather than a page you scan. The rows keep the
// same data-sync / data-setup / data-editapp / data-delapp / data-delmcp hooks
// the old list had, so every existing handler still finds its button.
let _cnRows = [];           // {name, label, group, ready, html} — for filtering
let _cnFilter = "all";

// The desktop app exposes `open_privacy_settings` on the pywebview bridge. It
// takes no arguments on purpose: the URL it opens is a constant in
// `connectors/permissions.py`, because `/api/open-browser` refuses custom
// schemes and widening that guard to reach a Settings pane is not a trade worth
// making. See chitragupta/connectors/permissions.py.
function canOpenPrivacySettings() {
  return !!window.pywebview?.api?.open_privacy_settings;
}

async function openPrivacySettings() {
  try {
    await window.pywebview.api.open_privacy_settings();
  } catch (_) {
    toast("Could not open System Settings. Open it yourself and go to " +
          "Privacy & Security → Full Disk Access, then turn on Chitragupta.");
  }
}

function _cnRowHtml(c, staleAfterMin) {
  const meta = connectorMeta(c.name);
  const ls = c.state?.last_sync ? new Date(c.state.last_sync) : null;
  const ageMin = ls ? (Date.now() - ls.getTime()) / 60000 : null;
  const stale = c.ready && ageMin !== null && ageMin > staleAfterMin;
  const last = ls ? ls.toLocaleDateString() : "";
  const state = !c.ready ? "off" : stale ? "stale" : "ok";
  // What the row says about itself: connected sources report their freshness,
  // unconnected ones say what they would give you if you connected them.
  // A connector with no listing tool is connected and useful — your agents can
  // ask it things — it just has nothing to pull in ahead of time. Saying
  // "not synced yet" about one would promise a sync that is never coming.
  const onDemand = c.mcp && c.ready && c.can_sync === false;
  // A `fix` means the server knows exactly what is wrong and that the user can
  // clear it. That reason outranks the catalogue blurb: "Reads your local
  // iMessages" is true and useless when macOS is the thing standing in the way.
  const blocked = !c.ready && !!c.fix;
  // A retired source says so. It keeps syncing and keeps everything it has
  // already put in the brain — what it no longer does is grow, because the
  // vendor's own server is the route now. Without this line the user reads a
  // connector that quietly stopped gaining features as one that is broken,
  // and has no idea there is somewhere to move to.
  const superseded = c.ready && c.superseded_by;
  const status = !c.ready ? (blocked ? c.reason : (meta.desc || c.reason || "Not connected"))
    : superseded ? `Connected · ${c.label}'s own server replaces this — disconnect to move across`
    : onDemand ? "Connected · answers your agents on demand"
    : !last ? "Connected · not synced yet"
    : stale ? `Connected · last synced ${last}` : `Connected · synced ${last}`;
  const badge = !c.ready ? ""
    : `<span class="cn-badge ${stale ? "is-stale" : ""}">${stale ? "Stale" : "Connected"}</span>`;
  // How it is reached, said on the row. `title` carries the difference for
  // anyone who wants it, so the tag itself can stay one word.
  const k = CONNECTOR_KINDS[c.kind] || CONNECTOR_KINDS.builtin;
  const kind = `<span class="cn-kind is-${esc(c.kind || "builtin")}" title="${esc(k.title)}">${esc(k.label)}</span>`;

  const sync = c.ready && !onDemand
    ? `<button class="tiny ghost" data-sync="${esc(c.name)}">Sync</button>` : "";
  // Opening a System Settings pane needs the native bridge, which exists only
  // in the desktop app — in a browser tab there is nothing behind the button,
  // and a control that cannot work is worse than no control. The sentence in
  // `status` already says where to go by hand, so the browser loses nothing.
  const fixBtn = blocked && c.fix === "full_disk_access" && canOpenPrivacySettings()
    ? `<button class="tiny" data-fda="${esc(c.name)}">Open Settings</button>` : "";
  const setup = c.custom
    ? `<button class="tiny ghost" data-editapp="${esc(c.name)}">Edit</button>`
    : (c.ready || fixBtn ? "" : `<button class="tiny" data-setup="${esc(c.name)}">Connect</button>`);
  // **A way out.** A token-backed connector had none: once it was ready the
  // row offered Sync and nothing else, so a source could be connected and
  // never unconnected — and a retirement the user cannot act on is a
  // retirement in name only. Telegram and Google already had their own; this
  // is the same control for everything that authenticates with a key.
  const disconnect = c.can_disconnect
    ? `<button class="tiny ghost" data-cnoff="${esc(c.name)}" data-cnlabel="${esc(c.label)}">Disconnect</button>`
    : "";
  const del = c.custom
    ? `<button class="tiny ghost cn-x" data-delapp="${esc(c.name)}" title="Remove" aria-label="Remove ${esc(c.label)}">${IC.close}</button>`
    : c.mcp ? `<button class="tiny ghost" data-cntools="${esc(c.name)}" data-cnlabel="${esc(c.label)}" title="What this connector can do">Permissions</button>
               <button class="tiny ghost cn-x" data-delmcp="${esc(c.name)}" title="Remove" aria-label="Remove ${esc(c.label)}">${IC.close}</button>` : "";

  return `<div class="cn-row" data-conn="${esc(c.name)}">
    <span class="cn-logo logo-tile" data-state="${state}" style="--brand:${
      connectorTint(markKey(c.name)) || `hsl(${connectorHue(c.name)} 62% 68%)`
    }"><i class="lt-sheen"></i>${connectorMark(markKey(c.name), c.label)}</span>
    <span class="cn-text">
      <span class="cn-name">${esc(c.label)}${kind}${badge}</span>
      <span class="cn-sub">${esc(status)}</span>
    </span>
    <span class="cn-actions">${sync}${fixBtn}${setup}${disconnect}${del}</span>
  </div>`;
}

// Bound after every render, not once at load: the list is replaced wholesale
// on each filter keystroke, so a handler attached to the previous nodes is
// attached to nothing the user can click.
function bindConnectorRowActions() {
  document.querySelectorAll("[data-sync]").forEach((b) => b.onclick = () => syncConn(b.dataset.sync));
  document.querySelectorAll("[data-setup]").forEach((b) => b.onclick = () => connectorHelp(b.dataset.setup));
  document.querySelectorAll("[data-fda]").forEach((b) => b.onclick = () => openPrivacySettings());
  document.querySelectorAll("[data-editapp]").forEach((b) => b.onclick = () =>
    customAppForm(CONNECTORS.find((x) => x.name === b.dataset.editapp)?.config));
  document.querySelectorAll("[data-delapp]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.delapp.split(":")[1];
    if (!confirm("Remove this custom app? (synced records stay in the brain.)")) return;
    await api(`/api/custom-apps/${id}`, { method: "DELETE" });
    toast("custom app removed"); loadBrain();
  });
  document.querySelectorAll("[data-cnoff]").forEach((b) => b.onclick = async () => {
    // Says what it does and what it does not. The token goes; everything the
    // connector already put in the brain stays, the same promise removing an
    // MCP connector makes two handlers below.
    if (!confirm(`Disconnect ${b.dataset.cnlabel}? The saved key is forgotten. `
                 + "What it already synced stays in your brain.")) return;
    // The body goes as an OBJECT. `_encodeBody` only adds the JSON header for
    // an object — a `JSON.stringify` string passes through untouched, the
    // browser labels it text/plain, and FastAPI answers 422. That is exactly
    // the bug core.js records against /api/open-browser, and this button shipped
    // with it: the dialog appeared, OK did nothing, and nothing said why.
    try {
      await api(`/api/connectors/${encodeURIComponent(b.dataset.cnoff)}/secret`,
                { method: "POST", body: { value: "" } });
    } catch (e) {
      // And it is caught, which is the other half of that bug — the 422 was
      // invisible because the only caller swallowed it.
      toast(`Could not disconnect ${b.dataset.cnlabel}. ${String(e)}`);
      return;
    }
    toast(`${b.dataset.cnlabel} disconnected`); loadBrain();
  });
  document.querySelectorAll("[data-cntools]").forEach((b) => b.onclick = () =>
    connectorTools(b.dataset.cntools, b.dataset.cnlabel));
  document.querySelectorAll("[data-delmcp]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.delmcp.split(":")[1];
    // Say what removing does and does not do. Silently keeping the memories
    // would be a surprise; silently deleting them would be worse.
    if (!confirm("Remove this connector? (what it already synced stays in your brain.)")) return;
    await api(`/api/connectors/mcp/${encodeURIComponent(id)}`, { method: "DELETE" });
    toast("connector removed"); loadBrain();
  });
}

function renderConnectors(connectors, staleAfterMin) {
  const box = $("#connectors"); if (!box) return;
  // The catalog is part of this screen now, so it loads with it. Fired rather
  // than awaited — what you already have must render immediately, and a slow
  // catalog must never be what holds it up — and caught, because a section
  // that fails must not take the sources you already have down with it.
  Promise.resolve().then(loadConnectorCatalog).catch(() => {
    const avail = $("#cxList");
    if (avail) avail.textContent = "Could not load the connector list.";
  });
  _cnRows = connectors.map((c) => ({
    name: c.name, label: c.label, ready: Boolean(c.ready),
    group: connectorGroup(c),
    html: _cnRowHtml(c, staleAfterMin),
  }));
  renderConnectorFilters();
  applyConnectorFilter();
}

function renderConnectorFilters() {
  const box = $("#cnFilters"); if (!box) return;
  const total = _cnRows.length;
  const connected = _cnRows.filter((r) => r.ready).length;
  // Only groups that actually have a source are offered. A filter that can
  // only ever return nothing is a control that cannot work.
  const present = CONNECTOR_GROUPS.filter((g) => _cnRows.some((r) => r.group === g.id));
  const chips = [
    { id: "all", label: `All`, n: total },
    { id: "connected", label: `Connected`, n: connected },
    ...present.map((g) => ({ id: g.id, label: g.title, n: _cnRows.filter((r) => r.group === g.id).length })),
  ];
  box.innerHTML = chips.map((c) =>
    `<button type="button" role="tab" class="cn-chip${c.id === _cnFilter ? " is-on" : ""}" data-cnf="${c.id}"
      aria-selected="${c.id === _cnFilter}">${esc(c.label)} <span class="cn-chip-n">${c.n}</span></button>`).join("");
  box.querySelectorAll("[data-cnf]").forEach((b) => b.onclick = () => {
    _cnFilter = b.dataset.cnf;
    renderConnectorFilters();
    applyConnectorFilter();
  });
}

function applyConnectorFilter() {
  const box = $("#connectors"); if (!box) return;
  const q = (($("#cnSearch") || {}).value || "").trim().toLowerCase();
  const match = (r) => {
    if (q && !r.label.toLowerCase().includes(q) && !r.name.toLowerCase().includes(q)) return false;
    if (_cnFilter === "all") return true;
    if (_cnFilter === "connected") return r.ready;
    return r.group === _cnFilter;
  };
  const shown = _cnRows.filter(match);
  const sections = CONNECTOR_GROUPS.map((g) => {
    const rows = shown.filter((r) => r.group === g.id);
    if (!rows.length) return "";
    const conn = rows.filter((r) => r.ready).length;
    return `<section class="cn-group">
      <div class="cn-group-head">
        <div><h2 class="cn-group-title">${esc(g.title)}</h2><p class="cn-group-sub">${esc(g.sub)}</p></div>
        <span class="cn-group-count">${conn} of ${rows.length} connected</span>
      </div>
      <div class="cn-card">${rows.map((r) => r.html).join("")}</div>
    </section>`;
  }).join("");
  box.innerHTML = sections;
  const empty = $("#cnEmpty"); if (empty) empty.hidden = shown.length > 0;
  // The handlers are rebound here rather than delegated, because this markup is
  // replaced wholesale on every filter keystroke — a listener bound to a node
  // that a re-render has already detached is the bug these harnesses exist for.
  bindConnectorRowActions();
}

{
  const inp = $("#cnSearch");
  if (inp) inp.addEventListener("input", () => applyConnectorFilter());
}

const CONNECTOR_HELP = {
  gmail: `<p>Read-only access to your Gmail.</p><ol>
    <li>In <b>Google Cloud Console</b> → APIs & Services → Credentials, create an
        <b>OAuth client ID</b> of type <b>Desktop app</b>.</li>
    <li>Download the <code>client_secret.json</code>.</li>
    <li>Set <code>GOOGLE_CLIENT_SECRETS</code> to its path, or drop it at
        <code>~/Library/Chitragupta/google_client_secret.json</code>.</li>
    <li>Run a sync — a browser opens once to authorize (read-only).</li></ol>`,
  gdrive: `<p>Read-only access to your Google Drive (Docs, text, PDFs).</p>
    <p>Uses the <b>same Google OAuth Desktop client</b> as Gmail — set it up once
    (see the Gmail setup) and Drive works too.</p>`,
  gcal: `<p>Read-only access to your Google Calendar events.</p>
    <p>Uses the <b>same Google OAuth Desktop client</b> as Gmail — set it up once
    (see the Gmail setup). Then re-authorize once so Calendar scope is granted.</p>`,
  apple_mail: `<p>Reads mail straight off your Mac — <b>no Google sign-in</b>.
    Works if you have your account in the <b>Mail app</b>.</p><ol>
    <li>Add your email account in <b>Mail</b> (if not already).</li>
    <li><b>System Settings → Privacy & Security → Full Disk Access</b> → add your
        terminal / Chitragupta → enable.</li>
    <li>Restart Chitragupta, then click sync.</li></ol>`,
  apple_calendar: `<p>Reads events off your Mac — <b>no sign-in</b>. Works with any
    calendar in the <b>Calendar app</b>.</p><ol>
    <li>Enable <b>Full Disk Access</b> for your terminal / Chitragupta.</li>
    <li>Restart Chitragupta, then click sync.</li></ol>`,
  imessage: `<p>Reads your local iMessages (fully on-device, no cloud).</p><ol>
    <li>Open <b>System Settings → Privacy & Security → Full Disk Access</b>.</li>
    <li>Add your <b>Terminal</b> (or whatever runs Chitragupta) and enable it.</li>
    <li>Restart Chitragupta, then click sync.</li></ol>
    <p class="t">macOS only. Chitragupta only reads, never sends.</p>`,
  notion: `Read-only access to the Notion pages you share with an integration.`,
  linear: `Read-only access to your Linear issues (status, priority, team).`,
  github: `Read-only access to the GitHub issues & PRs you're involved in.`,
};
// ── Telegram ─────────────────────────────────────────────────────────────
//
// The one connector whose backend shipped complete and unreachable. Six
// endpoints and the whole Telethon flow existed; nothing in the frontend said
// the word "telegram" except a label constant. So `message_send` was an action
// the Inbox agent is taught and structurally could not take — the exact thing
// `/CLAUDE.md` forbids: never show a control that cannot work.
//
// Signing in takes up to four steps and the server owns which one you are on.
// `GET /api/telegram/status` is asked first and after anything that might have
// moved, rather than the modal keeping its own idea: a wizard that tracks its
// own position is a wizard that shows you step 2 after step 2 already
// succeeded in another window.

//: What each step of the sign-in asks for. The server decides which one is
//: current; this only says how each looks.
const TG_STEPS = {
  credentials: {
    title: "Connect Telegram",
    blurb: `<p>Telegram needs its own app credentials — Chitragupta cannot
      ship one, because an API ID identifies the app to Telegram and a shared
      one would be every user's traffic under a single name.</p>
      <ol>
        <li>Open <b>my.telegram.org</b> and sign in with your phone.</li>
        <li>Choose <b>API development tools</b> and fill the short form
            (any app name will do).</li>
        <li>Copy the <b>api_id</b> and <b>api_hash</b> it gives you.</li>
      </ol>
      <p style="margin:6px 0 12px"><a href="https://my.telegram.org/apps"
         target="_blank" rel="noopener">Open my.telegram.org →</a></p>`,
    fields: [["tgApiId", "api_id", "text", "1234567"],
             ["tgApiHash", "api_hash", "password", "your api_hash"]],
    button: "Save",
  },
  phone: {
    title: "Sign in to Telegram",
    blurb: `<p>Telegram will send a login code to this number, in the Telegram
      app itself.</p>`,
    fields: [["tgPhone", "Phone number, with country code", "tel", "+44…"]],
    button: "Send me a code",
  },
  code: {
    title: "Enter the code",
    blurb: `<p>Telegram has sent a code to your phone — check the Telegram app
      rather than your texts.</p>`,
    fields: [["tgCode", "Login code", "text", "12345"]],
    button: "Sign in",
  },
  password: {
    title: "Two-factor password",
    blurb: `<p>This account has a Telegram password (two-step verification).
      It never leaves your Mac.</p>`,
    fields: [["tgPassword", "Telegram password", "password", ""]],
    button: "Finish",
  },
};

function tgFields(step) {
  return step.fields.map(([id, label, type, placeholder]) => `
    <label class="t" style="display:block;margin:10px 0 4px">${esc(label)}</label>
    <input id="${id}" type="${type}" autocomplete="off" spellcheck="false"
           placeholder="${esc(placeholder)}"
           style="width:100%;padding:8px 10px;border:1px solid var(--line);
                  border-radius:8px;background:var(--bg);color:var(--text)" />`
  ).join("");
}

/** Open the Telegram modal at whichever step the server says we are on. */
async function telegramSetup(at = "") {
  let state = {};
  try {
    state = await api("/api/telegram/status");
  } catch {
    // The probe shells out to Telethon and can be slow or absent. A modal that
    // refuses to open teaches nobody anything; start at the beginning instead.
    state = { configured: false, authorized: false };
  }

  if (state.authorized) {
    openBrainModal("Telegram", `
      <p>Connected as <b>${esc(state.account || "your account")}</b>.</p>
      <p class="t">Your agents can read your chats, and send a message when you
         confirm one. The session lives on this Mac only.</p>
      <button id="tgOut" class="tiny ghost" style="margin-top:12px">Disconnect</button>`);
    $("#tgOut").onclick = async () => {
      $("#tgOut").disabled = true;
      try {
        const out = await api("/api/telegram/disconnect", { method: "POST" });
        toast(out.detail || "Telegram disconnected");
        $("#brainModal").hidden = true;
        loadBrain();
      } catch (e) { $("#tgOut").disabled = false; toast(String(e)); }
    };
    return;
  }

  // `at` lets a step move the flow on without re-asking; otherwise the server's
  // own answer decides, which is what makes a second window harmless.
  const which = at || (state.configured ? "phone" : "credentials");
  const step = TG_STEPS[which];
  openBrainModal(step.title, `${step.blurb}${tgFields(step)}
    <div style="display:flex;gap:8px;margin-top:12px;align-items:center">
      <button id="tgGo" class="tiny">${esc(step.button)}</button>
      ${which === "credentials" ? "" :
        `<button id="tgBack" class="tiny ghost">Start again</button>`}
      <span id="tgSay" class="t"></span>
    </div>
    <p class="t" style="margin-top:10px">Everything here is stored on this Mac
       only — never uploaded.</p>`);

  const first = $(`#${step.fields[0][0]}`);
  if (first) first.focus();
  const say = (words) => { const el = $("#tgSay"); if (el) el.textContent = words; };
  const back = $("#tgBack");
  if (back) back.onclick = () => telegramSetup("credentials");

  const go = $("#tgGo");
  go.onclick = async () => {
    const value = (id) => ($(`#${id}`)?.value || "").trim();
    go.disabled = true;
    say("…");
    try {
      let out;
      if (which === "credentials") {
        out = await api("/api/telegram/credentials", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ api_id: value("tgApiId"),
                                 api_hash: value("tgApiHash") }) });
        if (out.ok === false) throw new Error(out.error || "Telegram refused that");
        return telegramSetup("phone");
      }
      if (which === "phone") {
        out = await api("/api/telegram/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phone: value("tgPhone") }) });
        if (!out.ok) throw new Error(out.error || "Telegram refused that");
        if (out.already) { toast("Already signed in"); return telegramSetup(); }
        return telegramSetup("code");
      }
      const path = which === "code" ? "code" : "password";
      out = await api(`/api/telegram/${path}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(which === "code"
          ? { code: value("tgCode") } : { password: value("tgPassword") }) });
      if (out.needs_password) {
        // Two-factor is on. Not a failure — the next step, and saying so is the
        // difference between a wizard and a dead end.
        return telegramSetup("password");
      }
      if (!out.ok) throw new Error(out.error || "Telegram refused that");
      toast(out.detail || "Telegram connected");
      $("#brainModal").hidden = true;
      loadBrain();
    } catch (e) {
      go.disabled = false;
      say(resultLine(e) || "That did not work.");
    }
  };
  for (const [id] of step.fields) {
    const box = $(`#${id}`);
    if (box) box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") go.click();
    });
  }
}

function connectorHelp(name) {
  // Telegram is a sign-in, not a pasted key, so it does not fit the
  // single-secret modal below.
  if (name === "telegram") return telegramSetup();
  const c = CONNECTORS.find((x) => x.name === name);
  const f = c?.secret_field;
  if (f) {
    // Connectors that authenticate with a single pasted token: show steps +
    // an in-app field (no .env editing, no restart needed).
    const steps = (f.steps || []).map((s) => `<li>${s}</li>`).join("");
    const link = f.help_url
      ? `<p style="margin:6px 0 12px"><a href="${f.help_url}" target="_blank" rel="noopener">Open ${c.label} to get your key →</a></p>` : "";
    openBrainModal(`Connect ${c.label}`,
      `<p>${CONNECTOR_HELP[name] || ""}</p>
       ${steps ? `<ol>${steps}</ol>` : ""}${link}
       <label class="t" style="display:block;margin-bottom:4px">${esc(f.label)}</label>
       <div style="display:flex;gap:8px">
         <input id="secretInput" type="password" autocomplete="off" spellcheck="false"
                placeholder="${esc(f.placeholder || "")}"
                style="flex:1;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font-family:monospace" />
         <button id="secretSave" class="tiny">Save</button>
       </div>
       <p class="t" style="margin-top:8px">Stored locally on your Mac only
         (<code>~/Library/Chitragupta/secrets.json</code>) — never uploaded.</p>`);
    const input = $("#secretInput");
    input.focus();
    $("#secretSave").onclick = async () => {
      const value = input.value.trim();
      if (!value) { toast("paste your key first"); return; }
      $("#secretSave").disabled = true; $("#secretSave").textContent = "Saving…";
      try {
        const r = await api(`/api/connectors/${name}/secret`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }) });
        if (r.ready) {
          toast(`${c.label} connected — syncing…`);
          $("#brainModal").hidden = true;
          await syncConn(name);
        } else {
          toast(r.reason || "saved, but not ready yet");
        }
        loadBrain();
      } catch (e) { toast(String(e)); }
      finally { $("#secretSave").disabled = false; $("#secretSave").textContent = "Save"; }
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#secretSave").click(); });
    return;
  }
  openBrainModal(`Set up ${name}`, (CONNECTOR_HELP[name] || "<p>No setup needed.</p>")
    + `<p class="t" style="margin-top:10px">Add the value to your <code>.env</code> and restart Chitragupta.</p>`);
}

// ── custom API app: connect any REST app, no code ──────────────────────────
function customAppForm(app) {
  app = app || {};
  const row = (label, id, val, ph) =>
    `<label class="t" style="display:block;margin:8px 0 3px">${label}</label>
     <input id="${id}" value="${esc(val || "")}" placeholder="${esc(ph || "")}" spellcheck="false"
       style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)" />`;
  const at = app.auth_type || "none";
  const opt = (v, t) => `<option value="${v}"${at === v ? " selected" : ""}>${t}</option>`;
  openBrainModal(app.id ? `Edit ${app.name}` : "Connect a custom app",
    `<p class="t">Point Chitragupta at any REST API that returns JSON. It fetches the
       endpoint and adds each record to your brain. Stays on your Mac.</p>
     ${row("App name", "ca_name", app.name, "My CRM")}
     ${row("Base URL", "ca_base", app.base_url, "https://api.myapp.com/v1")}
     ${row("Endpoint", "ca_ep", app.endpoint, "/contacts")}
     <label class="t" style="display:block;margin:8px 0 3px">Auth</label>
     <select id="ca_auth" style="width:100%;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)">
       ${opt("none", "None")}${opt("bearer", "Bearer token")}${opt("header", "Custom header")}${opt("query", "Query parameter")}</select>
     ${row("Header / param name (for custom header or query)", "ca_authname", app.auth_name, "X-API-Key")}
     ${row("Token (leave blank to keep current)", "ca_token", "", "•••••••• stored locally, chmod 600")}
     <hr style="border:none;border-top:1px solid var(--line);margin:12px 0">
     <p class="t">Map the JSON (dot-paths, e.g. <code>data.results</code>):</p>
     ${row("Items path — where the list lives", "ca_items", app.items_path, "data.results")}
     ${row("Title field", "ca_title", app.title_field, "name")}
     ${row("Body field", "ca_body", app.body_field, "notes")}
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="ca_save" class="tiny">${app.id ? "Save changes" : "Save & sync"}</button></div>`);
  $("#ca_name").focus();
  $("#ca_save").onclick = async () => {
    const payload = {
      id: app.id || null,
      name: $("#ca_name").value.trim() || "Custom app",
      base_url: $("#ca_base").value.trim(),
      endpoint: $("#ca_ep").value.trim(),
      auth_type: $("#ca_auth").value,
      auth_name: $("#ca_authname").value.trim(),
      items_path: $("#ca_items").value.trim(),
      title_field: $("#ca_title").value.trim(),
      body_field: $("#ca_body").value.trim(),
    };
    const tok = $("#ca_token").value.trim();
    if (tok) payload.token = tok;
    if (!payload.base_url) { toast("base URL is required"); return; }
    $("#ca_save").disabled = true; $("#ca_save").textContent = "Saving…";
    try {
      const r = await api("/api/custom-apps", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload) });
      $("#brainModal").hidden = true;
      toast("custom app saved — syncing…");
      await syncConn(r.name);
      loadBrain();
    } catch (e) { toast(String(e)); $("#ca_save").disabled = false; $("#ca_save").textContent = "Save"; }
  };
}
$("#addCustomApp").onclick = () => customAppForm();

// ── the connector catalog ────────────────────────────────────────────────
// Everything here is a "connector" to the user. Several are backed by MCP
// servers, which is an implementation detail they never need — the same way
// signing in to Claude never mentions a vendor CLI.

// The catalog lives ON the Connectors page now, not in a modal. This stays as
// the way back from the screens that drill into it — a permissions sheet, a
// setup form — and it closes whatever is open and returns you to the list,
// which is what "Back" meant when the list was a modal too.
function connectorBrowser() {
  const modal = $("#brainModal");
  if (modal) modal.hidden = true;
  loadConnectorCatalog();
  const box = $("#cnAvailable");
  if (box && box.scrollIntoView) box.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadConnectorCatalog() {
  const box = $("#cxList");
  if (!box) return;
  let data;
  try {
    data = await api("/api/connectors/catalog");
  } catch (e) {
    box.textContent = "Could not load the connector list. " + String(e);
    return;
  }
  // **A shape guard, not a nicety.** This list used to live in a modal, where
  // a throw took down something the user had deliberately opened. It is part
  // of the Connectors screen now, so the same throw would take down the
  // sources they already have — the half of the screen that must always
  // render. A reply missing `available` is treated as an empty catalog and
  // said out loud, never as a reason for the page to stop.
  data = data || {};
  if (!Array.isArray(data.available)) data.available = [];
  if (!Array.isArray(data.blocked)) data.blocked = [];

  // Same mark, same tile, same size as the sources above it — one screen, one
  // kind of row. A catalog entry carries no state dot: there is nothing
  // connected to report yet, and a dot that always means "off" is noise.
  const card = (c) => `
    <div class="cx-row" data-cx="${esc(c.id)}">
      <span class="cn-logo logo-tile" style="--brand:${
        connectorTint(c.id) || `hsl(${connectorHue(c.id)} 62% 68%)`
      }"><i class="lt-sheen"></i>${connectorMark(c.id, c.name)}</span>
      <span class="cx-text">
        <span class="conn-name">${esc(c.name)}</span>
        <span class="conn-sub">${c.added ? "already added"
          : esc(c.notes || (c.first_party ? "Official connector" : "Community connector"))}</span>
      </span>
      <button class="tiny${c.added ? " ghost" : ""}" data-cxadd="${esc(c.id)}"
        ${c.added ? "disabled" : ""}>${c.added ? "added" : "Add"}</button>
    </div>`;

  // The catalog is what we have vetted, and will never be all of it. Offering
  // the escape hatch here rather than hiding it in settings is the difference
  // between "these nine" and "anything you have".
  const own = `
    <div class="cx-row">
      <span>
        <span class="conn-name">Something else</span>
        <span class="conn-sub">Point Chitragupta at a server you already have.</span>
      </span>
      <button class="tiny ghost" id="cxOwn">Add your own</button>
    </div>`;

  // Grouped, because two dozen connectors in one list is a wall and the same
  // two dozen on six shelves is a decision. The order comes from the server —
  // the catalog knows what belongs where, and a second list here would
  // eventually disagree with it.
  const order = data.categories && data.categories.length
    ? data.categories
    : [...new Set(data.available.map((c) => c.category).filter(Boolean))];
  const shelved = order
    .map((cat) => [cat, data.available.filter((c) => c.category === cat)])
    .filter(([, items]) => items.length);
  // Anything the server grouped under a name we were not given still has to
  // appear. A connector that exists and is invisible is worse than an ugly
  // heading.
  const placed = new Set(shelved.flatMap(([, items]) => items.map((c) => c.id)));
  const rest = data.available.filter((c) => !placed.has(c.id));
  if (rest.length) shelved.push(["Other", rest]);

  const shelf = ([cat, items]) =>
    `<div class="cx-head">${esc(cat)}</div>${items.map(card).join("")}`;

  // **Sources nobody can offer are no longer listed here.** They used to be —
  // the argument was that a grid silently lacking LinkedIn teaches the user
  // this app is missing a feature, when the truth is that no app can offer it.
  // That argument held while this was a modal you opened to go shopping. On
  // the page it is three permanently dead rows at the bottom of a live list,
  // and the longest explanation on the screen belongs to the thing you cannot
  // have. The refusal is not lost: `add_from_catalog` still answers with
  // `BLOCKED`'s own sentence if one is ever asked for by id, which is where it
  // is actually useful — at the moment somebody tries.
  box.innerHTML = `${shelved.map(shelf).join("")}${own}`;

  box.querySelectorAll("[data-cxadd]").forEach((b) => {
    if (!b.disabled) b.onclick = () => connectorPermissions(b.dataset.cxadd);
  });
  $("#cxOwn").onclick = customServerForm;
}

// Consent to something nobody has been shown is not consent, so what a
// connector can do is read from the server and displayed before it is added.
//
// Three shapes, because the sources genuinely differ:
//   · the vendor signs you in  → a Connect button, then their own page
//   · the vendor wants a key   → a masked field
//   · a local server           → whatever it needs positionally, e.g. a folder
// A connector that needs nothing is probed up front and its tools listed.

function fieldRow(f, prefix) {
  const masked = f.kind === "secret";
  const hint = f.kind === "path" ? ' placeholder="~/Documents"' : "";
  return `
    <label class="t" style="display:block;margin:10px 0 3px">${esc(f.label)}</label>
    <div class="t" style="opacity:.7;margin-bottom:4px">${esc(f.help)}</div>
    <input id="${prefix}_${esc(f.name)}" spellcheck="false"${hint}
      type="${masked ? "password" : "text"}"
      style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)" />`;
}

function toolList(items, empty) {
  return items.length
    ? `<ul style="margin:4px 0 0 16px;padding:0">${
        items.map((t) => `<li><code>${esc(t)}</code></li>`).join("")}</ul>`
    : `<div class="t" style="opacity:.7;margin-top:4px">${esc(empty)}</div>`;
}

async function connectorPermissions(entryId) {
  openBrainModal("Add a connector",
    `<div id="cxPerm" class="t">Checking what this connector can do…</div>`);
  let info;
  try {
    info = await api(`/api/connectors/catalog/${encodeURIComponent(entryId)}/permissions`);
  } catch (e) {
    $("#cxPerm").textContent = "Could not check this connector. " + String(e);
    return;
  }

  if (!info.available) {
    $("#cxPerm").innerHTML =
      `<p class="t">${esc(info.reason || "This connector cannot be added.")}</p>
       <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
         <button id="cxBack" class="tiny ghost">Back</button></div>`;
    $("#cxBack").onclick = connectorBrowser;
    return;
  }

  const env = (info.needs_env || []).map((f) => fieldRow(f, "cxenv")).join("");
  const args = (info.needs_args || []).map((f) => fieldRow(f, "cxarg")).join("");

  // Nothing is known about a server behind someone else's sign-in until the
  // user has signed in. Promising a tool list we do not have would be a guess;
  // saying what happens next is not.
  const preview = info.needs_auth
    ? `<p class="t">You'll be sent to <b>${esc(info.name)}</b> to sign in.
         Chitragupta never sees your password, and the permissions you grant are
         shown on their page.</p>
       <p class="t" style="margin-top:8px;opacity:.8">Once connected, anything
         that <b>changes</b> something in ${esc(info.name)} always asks you first.</p>`
    : (info.reads.length || info.writes.length)
      ? `<p class="t"><b>${esc(info.name)}</b> would be able to:</p>
         <div style="margin-top:8px"><b class="t">Read</b>${toolList(info.reads, "nothing")}</div>
         <div style="margin-top:8px"><b class="t">Change</b>${
           toolList(info.writes, "nothing — this connector is read-only")}</div>
         ${info.writes.length ? `<p class="t" style="margin-top:8px;opacity:.8">
           Anything that changes something always asks you first.</p>` : ""}
         ${info.can_sync ? "" : `<p class="t" style="margin-top:8px">
           This one answers questions but cannot list its records, so it is
           searched on demand rather than synced.</p>`}`
      : `<p class="t">${esc(info.notes || "")}</p>
         <p class="t" style="margin-top:8px;opacity:.8">You'll see exactly what
           it can read and change as soon as it's connected.</p>`;

  $("#cxPerm").innerHTML =
    preview +
    ((env || args) ? `<hr style="border:none;border-top:1px solid var(--line);margin:12px 0">${args}${env}` : "") +
    `<div id="cxErr" class="t" style="color:var(--bad);margin-top:8px" hidden></div>
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="cxBack" class="tiny ghost">Back</button>
       <button id="cxGo" class="tiny">${info.needs_auth ? "Connect" : "Add connector"}</button></div>`;

  $("#cxBack").onclick = connectorBrowser;
  $("#cxGo").onclick = async () => {
    const body = { env: {}, args: {} };
    (info.needs_env || []).forEach((f) => {
      const v = $(`#cxenv_${f.name}`);
      if (v && v.value.trim()) body.env[f.name] = v.value.trim();
    });
    (info.needs_args || []).forEach((f) => {
      const v = $(`#cxarg_${f.name}`);
      if (v && v.value.trim()) body.args[f.name] = v.value.trim();
    });
    const go = $("#cxGo"), err = $("#cxErr");
    go.disabled = true; go.textContent = "Checking…"; err.hidden = true;
    try {
      const r = await api(`/api/connectors/catalog/${encodeURIComponent(entryId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      if (!r.ok) {
        // Say why here, where they are looking — a failed add that closes the
        // dialog and shows nothing is the shape of "the app is broken".
        err.textContent = r.error; err.hidden = false;
        go.disabled = false; go.textContent = info.needs_auth ? "Connect" : "Add connector";
        return;
      }
      if (r.signing_in) { awaitSignIn(r.server_id, r.label); return; }
      $("#brainModal").hidden = true;
      toast(`${r.label} connected — syncing…`);
      await syncConn(r.name);
      loadBrain();
    } catch (e) {
      err.textContent = String(e); err.hidden = false;
      go.disabled = false; go.textContent = info.needs_auth ? "Connect" : "Add connector";
    }
  };
}

// ── waiting on the vendor's own sign-in ──────────────────────────────────
// The browser is somewhere else now, so this has to survive the user tabbing
// away and coming back — the status lives on the server, not in this closure.
// And anything they start, they can stop: Cancel really ends the flow.

async function awaitSignIn(serverId, label) {
  openBrainModal(`Connect ${label}`,
    `<p class="t">A browser window is opening. Sign in to <b>${esc(label)}</b>
       and approve the permissions you want to give it.</p>
     <p class="t" id="cxAuthState" style="margin-top:10px;opacity:.75">Waiting for you to finish…</p>
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="cxAuthCancel" class="tiny ghost">Cancel</button></div>`);

  let stopped = false;
  $("#cxAuthCancel").onclick = async () => {
    stopped = true;
    await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}/auth`,
              { method: "DELETE" }).catch(() => {});
    await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}`,
              { method: "DELETE" }).catch(() => {});
    $("#brainModal").hidden = true;
    toast(`${label} was not connected`);
    loadBrain();
  };

  for (let i = 0; i < 150 && !stopped; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    if (stopped) return;
    let s;
    try {
      s = await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}/auth`);
    } catch { continue; }
    if (s.status === "connected") {
      $("#brainModal").hidden = true;
      toast(`${label} connected`);
      loadBrain();
      return;
    }
    if (s.status === "failed" || s.status === "cancelled") {
      const state = $("#cxAuthState");
      if (state) { state.textContent = s.reason || "That didn't complete."; state.style.color = "var(--bad)"; }
      return;
    }
  }
  const state = $("#cxAuthState");
  if (state && !stopped) state.textContent = "Still waiting — you can close this and try again.";
}

// ── a server we do not list ──────────────────────────────────────────────
// The catalog covers what we have vetted, which will never be all of it.

function customServerForm() {
  const input = (id, label, help, ph) => `
    <label class="t" style="display:block;margin:10px 0 3px">${label}</label>
    ${help ? `<div class="t" style="opacity:.7;margin-bottom:4px">${help}</div>` : ""}
    <input id="${id}" spellcheck="false" placeholder="${ph || ""}"
      style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)" />`;

  openBrainModal("Add your own connector",
    `<p class="t">Point Chitragupta at a server you already have. It is started
       and checked before it is saved, so a broken one is never added.</p>
     ${input("csName", "Name", "What you want to call it here.", "My tracker")}
     <label class="t" style="display:block;margin:10px 0 3px">Kind</label>
     <select id="csKind" style="width:100%;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)">
       <option value="http">A web address (the service runs it)</option>
       <option value="stdio">A program on this Mac</option>
     </select>
     <div id="csHttp">${input("csUrl", "Address", "", "https://mcp.example.com/mcp")}</div>
     <div id="csStdio" hidden>
       ${input("csCmd", "Command", "The program to run.", "npx")}
       ${input("csArgs", "Arguments", "Separated by spaces.", "-y some-mcp-server@1.0.0")}
     </div>
     <div id="csErr" class="t" style="color:var(--bad);margin-top:8px" hidden></div>
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="csBack" class="tiny ghost">Back</button>
       <button id="csGo" class="tiny">Add connector</button></div>`);

  const sync = () => {
    const http = $("#csKind").value === "http";
    $("#csHttp").hidden = !http; $("#csStdio").hidden = http;
  };
  $("#csKind").onchange = sync; sync();
  $("#csBack").onclick = connectorBrowser;
  $("#csGo").onclick = async () => {
    const name = ($("#csName").value || "").trim();
    const kind = $("#csKind").value;
    const err = $("#csErr"), go = $("#csGo");
    if (!name) { err.textContent = "Give it a name."; err.hidden = false; return; }
    const body = {
      id: name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""),
      name, transport: kind,
      url: kind === "http" ? ($("#csUrl").value || "").trim() : "",
      command: kind === "stdio" ? ($("#csCmd").value || "").trim() : "",
      args: kind === "stdio"
        ? ($("#csArgs").value || "").trim().split(/\s+/).filter(Boolean) : [],
    };
    go.disabled = true; go.textContent = "Checking…"; err.hidden = true;
    try {
      const r = await api("/api/connectors/mcp", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      if (!r.ok) {
        err.textContent = r.error; err.hidden = false;
        go.disabled = false; go.textContent = "Add connector"; return;
      }
      if (r.signing_in) { awaitSignIn(r.server_id, r.label); return; }
      $("#brainModal").hidden = true;
      toast(`${r.label} connected`);
      loadBrain();
    } catch (e) {
      err.textContent = String(e); err.hidden = false;
      go.disabled = false; go.textContent = "Add connector";
    }
  };
}

// ── what a connector is allowed to use ───────────────────────────────────
// Least privilege the user cannot set is a claim, not a control. This is the
// half that was missing: the tools were shown before adding and could never
// be changed afterwards.

async function connectorTools(name, label) {
  const serverId = name.split(":")[1];
  openBrainModal(`What ${label} can do`,
    `<div id="cxTools" class="t">Asking ${esc(label)}…</div>`);
  let info;
  try {
    info = await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}/tools`);
  } catch (e) {
    $("#cxTools").textContent = "Could not reach this connector. " + String(e);
    return;
  }
  if (!info.ok) { $("#cxTools").textContent = info.error || "Could not reach it."; return; }

  const allowed = new Set(info.allowed_tools || []);
  const everything = allowed.size === 0;
  const row = (t) => `
    <label class="cx-tool" style="display:flex;gap:9px;align-items:flex-start;padding:6px 0">
      <input type="checkbox" data-tool="${esc(t.name)}"
        ${everything || allowed.has(t.name) ? "checked" : ""} style="margin-top:3px" />
      <span>
        <code>${esc(t.name)}</code>
        ${t.writes ? `<span class="conn-sub" style="color:var(--bad)">changes things — always asks you</span>`
                   : `<span class="conn-sub">${esc(t.description || "reads only")}</span>`}
      </span>
    </label>`;

  $("#cxTools").innerHTML =
    `<p class="t">Turn off anything you'd rather ${esc(label)} could not touch.
       Unchecked tools are not offered to your agents at all.</p>
     <div style="margin-top:10px">${info.tools.map(row).join("")}</div>
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="ctCancel" class="tiny ghost">Cancel</button>
       <button id="ctSave" class="tiny">Save</button></div>`;

  $("#ctCancel").onclick = () => $("#brainModal").hidden = true;
  $("#ctSave").onclick = async () => {
    const boxes = [...document.querySelectorAll("#cxTools [data-tool]")];
    const on = boxes.filter((b) => b.checked).map((b) => b.dataset.tool);
    // All of them checked means "no restriction", which is stored as an empty
    // list — otherwise a tool added by a server update would arrive disabled.
    const body = { allowed_tools: on.length === boxes.length ? [] : on };
    await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    $("#brainModal").hidden = true;
    toast(`${label} updated`);
    loadBrain();
  };
}

// The button is gone — the list it opened is on the page. Guarded rather than
// deleted outright because `app.js` is evaluated whole by the test harnesses,
// and a null here throws before anything under test is reached.
if ($("#addConnector")) $("#addConnector").onclick = () => connectorBrowser();

// ── waiting for approval ─────────────────────────────────────────────────
// An action an unattended agent wanted to take, held until the user decides.
// The queue, the notification and the endpoints existed before this; what did
// not was anywhere to look, which made a desktop notification the only trace a
// request ever happened. A queue nobody can see is not an approval system.
