/**
 * What one agent may use, and where each skill came from.
 *
 * The Agents & tools panel: the built-in tools, the tools a connector the user
 * added exposes to the agent loop, and a switch for each.
 *
 * There used to be a second screen here — a read-only "Tools & skills" drawer
 * off the sidebar. Same endpoint, same grouping, same rows, no switches, and it
 * printed each connector's description in full where this one trims to the
 * first sentence. A strict subset of this panel, reached by a nav item with
 * almost the same name, and neither said the other existed. It is gone; the two
 * rules only its tests covered moved into `test_frontend_agent_tools.py`.
 *
 * **Every skill says where it came from.** A row that does not name its source
 * reads as something Chitragupta invented, when it belongs to a server the user
 * connected and can disconnect. `toolConnector` resolves that provenance, and a
 * connector that cannot answer right now is shown as unreachable rather than
 * dropped from the list — a capability that silently vanishes is
 * indistinguishable from one that never existed.
 *
 * Names come from the connector, so they are escaped like any other text the
 * user's own sources supply.
 */

// ── the tools panel ────────────────────────────────────────────────────────
// A skill either ships with Chitragupta or arrives with something the user
// connected, and they are entitled to know which: a tool that reaches into
// their mail is a different thing from one that searches their brain.
// Provenance rides on each row as `source` plus `connector` — the connector's
// own user-facing label. Rows that predate provenance carry no `source` at all,
// and those are builtins.
//
// The acronym for the protocol a connector speaks NEVER reaches this screen,
// exactly as "vendor CLI" never reaches the sign-in card. The user added
// Linear; the user sees Linear.

// What a person reads for this tool. `name` is an id — for a connector tool it
// is a qualified one we minted — and an id on screen is an internal surfaced.
function toolLabel(t) {
  return ((t && (t.label || t.name)) || "").trim();
}

// A row that grants a whole category rather than naming one connector's tool.
// It belongs in the agent builder, where it is the switch a user flips, and not
// in a list of skills, where the concrete tools are already shown under their
// own connector.
function isCategoryRow(t) { return !!t && t.source === "category"; }

// One readable line about a tool.
//
// A connector's description is written FOR A MODEL by whoever wrote the
// server: Notion's search tool ships four hundred words of instructions about
// query_type and filter nesting. Rendered whole, twenty-seven of those are a
// page of prose where a list of switches should be, and the labels disappear
// into it.
//
// So: the first sentence, capped. Derived from what the API sent rather than
// replaced by anything of ours — the full text is still what the model gets,
// and truncating the display cannot change what the tool does.
function toolBlurb(t) {
  const full = ((t && t.description) || "").trim();
  if (!full) return "";
  // Stop at the first sentence end that is followed by a space — a bare "." is
  // as likely to be inside `{"id":"self"}` or a version number.
  const m = full.match(/^[\s\S]*?[.!?](?=\s)/);
  let out = (m ? m[0] : full).trim();
  if (out.length > 150) out = out.slice(0, 149).replace(/\s+\S*$/, "") + "…";
  return out;
}

// The heading a built-in sits under. The API names it; a tool that arrives
// without one lands in "Other", which is visible enough to get fixed.
function toolCategory(t) { return ((t && t.category) || "").trim() || "Other"; }

// ── what a built-in touches, and what it does to it ───────────────────────
//
// The panel used to arrange itself by `category` — thirteen headings and
// sixty-four switches, every one of them a question about a tool, asked before
// the user had sent the agent a single message.
//
// `group` and `access` come from the API because the decision is not this
// layer's: a tool declares a capability, and `agents/tool_facts.py` derives
// both from it. A consumer that re-derived them would be the name chain this
// file's own comments keep warning about.
function toolGroupKey(t) { return ((t && t.group) || "").trim(); }
function toolAccess(t) { return ((t && t.access) || "").trim(); }

//: What a group can be allowed to do, in the order the tiers escalate.
//:
//: `access` is the server's word and `verb` is the person's — they are not the
//: same word and must not be assumed to be. Writing the bucket keys as the UI
//: labels and comparing them straight against `access` silently matched
//: nothing for two of the four tiers, so "Change" and the irreversible bucket
//: rendered no switch at all while their tools sat in the disclosure below,
//: on. A panel that omits a switch for something an agent can do is worse than
//: the sixty-four it replaced.
//:
//: `outbound` has its own bucket even though no built-in is one today.
//: Folding it into "change" would mean the first tool that reaches a person
//: arrives already covered by a switch somebody set for something else.
const ACCESS_BUCKETS = [
  { key: "read", access: ["read"], verb: "Read",
    blurb: "Look, and report back." },
  { key: "change", access: ["write"], verb: "Change",
    blurb: "Alter something. Each change is yours to undo." },
  { key: "send", access: ["outbound"], verb: "Send to people",
    blurb: "Reaches somebody who is not you." },
  { key: "run", access: ["destructive"], verb: "Irreversible",
    blurb: "Running code, and anything else nothing can undo." },
];

//: Which bucket a row belongs in, or "" for a tier nobody has named.
//:
//: Unknown is dropped from the SWITCHES and stays visible in the per-tool rows
//: — the opposite way round from failing closed, and deliberately: a switch
//: nobody can describe is one a person cannot consent with, while the row
//: still lets them set it and still says what it does.
function bucketOf(row) {
  const tier = toolAccess(row);
  const found = ACCESS_BUCKETS.find((b) => b.access.includes(tier));
  return found ? found.key : "";
}

function toolConnector(t) {
  // Anything that is not explicitly a builtin came in with a connector —
  // including source kinds that do not exist yet, which is the whole point of
  // asking the question this way round.
  if (!t || !t.source || t.source === "builtin" || isCategoryRow(t)) return "";
  const label = (t.connector || "").trim();
  // A connector that did not name itself still must not be named after its
  // protocol. Vague is survivable; the acronym is not.
  return label || "A connected app";
}

// A connector row exists either because we ship it or because the user added
// it — `custom` apps come from their own form, `mcp` ones from a server they
// chose. Only those two can be "configured but unreachable"; the rest are
// merely not set up yet, which the Sources panel already says in its own words.
function userConfigured(c) { return !!(c && (c.custom || c.mcp)); }

//: A connector the user added that cannot answer right now — as opposed to one
//: we ship that was simply never set up, which is not this screen's business.
function unreachableConnector(c) { return userConfigured(c) && c.ready === false; }


// ── per-agent tools ────────────────────────────────────────────────────────
// The screen that answers "why does my agent not know about Notion?".
//
// It replaced a read-only list of every tool that exists. That answered a
// different question from the one nobody could previously ask — an agent
// silently lacking connector access looked exactly like a connector that was
// still syncing, and no screen distinguished them.
//
// The render is split from its fetch so the real path can be executed in a
// test: an empty panel and a panel that threw are indistinguishable from
// outside, and this file has shipped both.

//: The agent whose tools are on screen. A save names the agent it was for, so
//: a reply arriving after the user has switched cannot repaint the new one.
let AGENT_TOOLS_FOR = "";
//: Saves in flight, by tool name, so a row can show its own progress and a
//: second click cannot race the first.
const _toolSaving = new Set();

//: An agent that does not exist yet — the one the builder is drawing.
//:
//: The builder and this panel are the SAME list, and the only difference is
//: where the answer is kept: a saved agent's switches each PATCH, a draft's
//: live in the object on screen and nothing is sent until Create. A flag
//: rather than a second renderer, because a second renderer is exactly what
//: this screen had — and the builder's copy was two releases behind it. It
//: still arranged itself by the thirteen categories, with no group sentences,
//: no presets and no "allow all": sixty-four switches to set by hand at the
//: one moment the user knows least about what the agent will need. Polish
//: landing on one of two copies is how the copies got that far apart.
function isDraftAgent(a) { return !!(a && a.draft); }

//: The last draft render, so a switch can repaint the screen it is on.
//:
//: A saved agent reads its state back from the server after every press. A
//: draft has no server, so this context IS the state, and the render is the
//: only thing that has to agree with it.
let _draftCtx = null;

function rerenderDraft() {
  if (!_draftCtx) return;
  renderAgentTools(_draftCtx.box, _draftCtx);
  // The builder keeps its own count above the list and reads it off the DOM,
  // so it is refreshed after the DOM is and never before.
  if (typeof updateAgentToolCount === "function") updateAgentToolCount();
}

//: Every switch in a draft, in one place: there is nothing to save, so moving
//: the set and repainting is the whole operation.
function setDraftTools(agent, names, on) {
  const have = new Set(agent.tools || []);
  for (const n of names) { if (!n) continue; if (on) have.add(n); else have.delete(n); }
  agent.tools = [...have];
  // Whatever preset was pressed, this is no longer it — and Create sends the
  // preset by NAME, so a stale one would grant something nobody asked for.
  agent.preset = "";
  rerenderDraft();
}

//: Is this tool in the group that cannot leave this machine? Asked of the
//: specs the API sent, so it is the same answer the group heading gives.
function alwaysOnTool(ctx, row) {
  const spec = ((ctx && ctx.specs) || []).find((s) => s && s.key === toolGroupKey(row));
  return !!(spec && spec.always);
}

//: What a preset means for an agent that does not exist yet.
//:
//: The panel hands the server a NAME and renders what comes back. A draft has
//: nothing to hand it to, so each preset row carries `tools` — the built-ins it
//: covers — purely so this screen can show what the button will do. Create
//: still sends the name and the server resolves it again at that moment, so a
//: screen left open while a tool shipped shows yesterday's preview and grants
//: today's definition. That is the right way round.
//:
//: No preset can name a connector tool — they do not exist until the user adds
//: one. So how far a preset reaches into them is DERIVED from the preset
//: itself, never from its key: a key chain here would mean a preset added
//: later silently granting nothing at all.
//:
//:   * one that covers every built-in there is covers the connectors too;
//:   * one that reaches past the always-on group covers the row that grants
//:     what the connectors can READ, which is exactly what that row is;
//:   * one that reaches nothing outside this machine covers neither.
function draftPresetTools(ctx, key) {
  const found = ((ctx && ctx.presets) || []).find((p) => p.key === key);
  if (!found || !Array.isArray(found.tools)) return null;   // nothing to preview
  const covered = new Set(found.tools);
  const rows = (ctx && ctx.tools) || [];
  const builtins = rows.filter((t) => !isCategoryRow(t) && !toolConnector(t));
  const chosen = new Set(builtins.filter((t) => covered.has(t.name)).map((t) => t.name));
  const everything = !!builtins.length && builtins.every((t) => chosen.has(t.name));
  // Without the group specs there is no way to ask whether a tool stays on this
  // machine, so the only preset that reaches out is the one that takes
  // everything. Narrower than intended is survivable here; wider is not.
  const haveSpecs = !!((ctx && ctx.specs) || []).length;
  const reachesOut = everything || (haveSpecs && builtins.some(
    (t) => chosen.has(t.name) && !alwaysOnTool(ctx, t)));
  for (const t of rows) {
    if (isCategoryRow(t)) { if (reachesOut) chosen.add(t.name); continue; }
    if (toolConnector(t) && everything) chosen.add(t.name);
  }
  return [...chosen];
}

//: Which preset the agent is on right now, or "" — so the row says where you
//: ARE and not only where you could go.
//:
//: Compared on the built-ins, because that is what a preset decides: it keeps
//: whatever else the agent holds, so counting a connector tool against it would
//: mean no preset ever matched and the row never lit. Compared against the rows
//: on SCREEN rather than the preset's whole list, so a preset is not ruled out
//: by a tool this build does not have.
function currentPreset(ctx, agent) {
  const have = new Set((agent && agent.tools) || []);
  const builtins = ((ctx && ctx.tools) || []).filter(
    (t) => !isCategoryRow(t) && !toolConnector(t));
  if (!builtins.length) return "";
  for (const p of ((ctx && ctx.presets) || [])) {
    if (!Array.isArray(p.tools)) continue;        // an older server: no preview
    const covered = new Set(p.tools);
    if (builtins.every((t) => covered.has(t.name) === have.has(t.name))) return p.key;
  }
  return "";
}

//: Which groups the user has opened, so a re-render does not shut them.
//:
//: A preset repaints the whole panel and every disclosure anybody had opened
//: closed with it — including the one they were reading in order to decide.
//: Keyed by group name because that is what the user opened; a name that is
//: gone by the next render simply never matches.
const _openGroups = new Set();

//: The one list of settings screens this panel can send somebody to.
//:
//: A group says where the other half of its permission lives — "which sites"
//: is not a tool switch — and the ID it names is resolved HERE, because which
//: screens exist is the frontend's fact and not the API's. An ID nothing
//: recognises draws no button, so naming a new one server-side can never
//: produce a control that goes nowhere.
function screenOpener(id) {
  if (id === "connectors" && typeof openConnectorsScreen === "function") {
    return openConnectorsScreen;
  }
  return null;
}

//: Setting the whole lot at once, which is the common case.
//:
//: Six decisions beats sixty-four and is still six. Most of the time the answer
//: is "this one is mine, let it do everything" or "let it look and nothing
//: else", and a screen that compacts the list and still makes somebody set
//: every switch has done half the job.
//:
//: The buttons carry the server's words, including the part people skim —
//: "Allow everything" says it includes running code, because a control that
//: quietly included that would be the tap-nobody-reads failure at the worst
//: possible scale.
//: "Allow all" for one group — every switch in it, in one press.
//:
//: Per group rather than only a global preset, because the real answer is
//: usually about one of them: let it have websites, leave the Mac alone. The
//: global row sets all four; this sets one.
//:
//: Absent on the always-on group (nothing to allow) and while a connector is
//: unreachable (a control that cannot work is not shown as a control).
function groupAllButton(g, why) {
  // A group with no spec is a CONNECTOR, not a group with nothing to allow —
  // and it is the one that needs this most: a connector's own tools get no
  // tier switches, because a server's tools do not declare one, so without
  // this the user's twenty-five Notion tools were the only wall left on the
  // screen. `always` is the real exemption, and it is asked for by name.
  if (why || (g.spec && g.spec.always)) return "";
  const names = g.tools.map(({ row }) => (row && row.name) || "").filter(Boolean);
  if (!names.length) return "";
  const allOn = g.tools.every(({ on }) => on);
  return `<button type="button" class="tiny at-all"
    data-bulk="${esc(names.join(" "))}" data-on="${allOn ? "1" : ""}"
    >${allOn ? "Turn all off" : "Allow all"}</button>`;
}

//: The presets, with the one that is already true marked.
//:
//: Three buttons that look identical whatever the agent holds say where you
//: could go and never where you are — so "Read only" was indistinguishable from
//: "Read only, already applied", and the only way to tell was to count
//: switches, which is the thing this row exists to replace.
function presetRow(ctx, agent) {
  const list = Array.isArray(ctx && ctx.presets) ? ctx.presets : [];
  if (!list.length) return "";          // an older server: the switches remain
  const now = currentPreset(ctx, agent);
  return `<div class="at-presets">`
    + list.map((p) => {
      const on = !!now && p.key === now;
      return `<button type="button" class="at-preset${on ? " is-current" : ""}"
         aria-pressed="${on}"
         data-preset="${esc(p.key)}" title="${esc(p.blurb || "")}">
         <span class="at-preset-nm">${esc(p.label)}</span>
         <span class="at-preset-ds">${esc(p.blurb || "")}</span>
       </button>`;
    }).join("")
    + `</div>`;
}

//: Where the other half of this group's permission is set.
//:
//: "Allow changes on websites" grants the agent nothing on its own: which
//: sites it may touch is a list kept on another screen, and a user who turned
//: every switch here on and was still refused had no way to learn that from
//: this panel. Same for accounts — "may read your mail" means nothing until a
//: mailbox is connected.
//:
//: The group names the screen; this resolves it, and draws nothing for an ID
//: the frontend does not have.
function groupMoreButton(g, draft) {
  const more = g.spec && g.spec.more;
  // Not from the builder: it is a modal, and the screen this opens would come
  // up behind it. The group's own sentence still says the list exists.
  if (draft || !more || !more.screen || !screenOpener(more.screen)) return "";
  return `<button type="button" class="tiny at-more" data-screen="${esc(more.screen)}"
    >${esc(more.label || "Open")}</button>`;
}

//: The sentence under a group heading, saying what the whole group IS.
//:
//: Sixty-four switches had sixty-four descriptions and no answer to "what am I
//: deciding?". A group has one, and it is the server's — this layer renders it
//: and never writes it, for the reason every other label on this screen comes
//: down the wire.
function groupBlurb(g) {
  const text = ((g.spec && g.spec.blurb) || "").trim();
  return text ? `<p class="at-group-ds">${esc(text)}</p>` : "";
}

//: The switches a person actually sets: one per thing this group can do.
//:
//: A master switch over every tool in its bucket, because "may it read my
//: mail" is the decision and `list_mail` versus `read_thread` is not. The
//: per-tool rows survive inside the disclosure below for anyone who wants
//: them — taking them away would be removing control, where the complaint was
//: that control was the ONLY thing on offer.
function groupSwitches(g, why) {
  if (!g.spec) return "";          // an older server, or a group nobody named
  if (g.spec.always) {
    return `<p class="at-always">Always on. Nothing here leaves this machine,
      so it is not something to switch.</p>`;
  }
  const buckets = ACCESS_BUCKETS.map((b) => {
    const inBucket = g.tools.filter(({ row }) => bucketOf(row) === b.key);
    if (!inBucket.length) return "";
    const on = inBucket.every(({ on: isOn }) => isOn);
    const some = !on && inBucket.some(({ on: isOn }) => isOn);
    const names = inBucket.map(({ row }) => (row && row.name) || "").join(" ");
    const control = why
      ? `<span class="at-blocked">${esc(why)}</span>`
      : `<button type="button" role="switch" aria-checked="${on}"
           class="at-toggle${on ? " is-on" : ""}${some ? " is-some" : ""}"
           data-bulk="${esc(names)}" data-on="${on ? "1" : ""}"
           aria-label="${esc(b.verb)} — ${esc(g.name)}"><span class="at-knob"></span></button>`;
    // "3 of 7 on" rather than a half-lit switch with nothing explaining it:
    // a partially-granted group is a real state, usually because somebody
    // used the per-tool rows, and it has to be legible without opening them.
    //
    // The element is always drawn and empty when there is nothing to say, so a
    // switch flipped inside the disclosure can fill it without a re-render.
    // Rendering it only when it has content meant the one case it exists for —
    // a group becoming partial — had nowhere to put the sentence.
    const part = `<span class="at-part">${
      some ? `${inBucket.filter((t) => t.on).length} of ${inBucket.length} on` : ""}</span>`;
    return `<div class="at-bulk" data-bucket="${esc(b.key)}">
      <span class="at-text">
        <span class="at-nm">${esc(b.verb)}</span>
        <span class="at-ds">${esc(b.blurb)}</span>
      </span>${part}${control}</div>`;
  }).join("");
  return buckets ? `<div class="at-bulks">${buckets}</div>` : "";
}

function agentToolGroups(tools, connectors, agentTools, categories, specs) {
  const rows = Array.isArray(tools) ? tools : [];
  const have = new Set(Array.isArray(agentTools) ? agentTools : []);

  // What each built-in group IS, in the API's words. Absent (an older server,
  // or a tool whose group nobody recognises) and the screen falls back to the
  // thirteen categories — worse, and still a working screen.
  const byKey = new Map();
  for (const s of (Array.isArray(specs) ? specs : [])) {
    if (s && s.key) byKey.set(s.key, s);
  }

  const health = new Map();
  for (const c of (Array.isArray(connectors) ? connectors : [])) {
    const label = ((c && c.label) || "").trim();
    if (label) health.set(label.toLowerCase(), c);
  }

  const groups = [];
  const seen = new Map();
  const groupFor = (name, kind, spec) => {
    const key = name.toLowerCase();
    let g = seen.get(key);
    if (!g) {
      g = { name, kind, tools: [], connector: null, spec: spec || null };
      seen.set(key, g); groups.push(g);
    }
    return g;
  };

  for (const t of rows) {
    const name = (t && t.name) || "";
    // Three kinds of group, and each is asked for by a different field:
    //   the category switch  — source === "category"
    //   a connector's tools  — connector
    //   a built-in           — its own category, from the API
    // Thirty-two built-ins under one heading is a wall; under nine short
    // headings it is a list you can choose from.
    // A built-in goes under what it TOUCHES, not under its old heading. The
    // heading survives inside the group, where it is a sub-list rather than a
    // decision.
    const spec = !isCategoryRow(t) && !toolConnector(t)
      ? byKey.get(toolGroupKey(t)) : null;
    const group = isCategoryRow(t)
      ? groupFor("Your connectors", "category")
      : toolConnector(t)
        ? groupFor(toolConnector(t), "connector")
        : spec
          ? groupFor(spec.label, "builtin", spec)
          : groupFor(toolCategory(t), "builtin");
    group.tools.push({ row: t, on: have.has(name) });
  }

  // A connector the user added that cannot answer contributes no tools, so
  // without this it is simply absent — and absent reads as "Chitragupta lost
  // it" rather than "sign in again".
  for (const c of (Array.isArray(connectors) ? connectors : [])) {
    if (!unreachableConnector(c)) continue;
    const label = (c.label || "").trim();
    if (label) groupFor(label, "connector").connector = c;
  }
  for (const g of groups) {
    if (!g.connector) g.connector = health.get(g.name.toLowerCase()) || null;
  }

  // Connectors first: this screen exists because of them. Built-ins after, in
  // the order the API names — not alphabetical, because "Your Mac" belongs
  // last whatever letter it starts with, and that is a judgement the layer
  // that owns the categories already made.
  const builtin = groups.filter((g) => g.kind === "builtin");
  // The API's group order first — it is the layer that decided "Your Mac"
  // comes last, and a consumer sorting them itself would be re-deciding that
  // alphabetically. Categories remain the fallback for a server that predates
  // groups, so the screen degrades rather than scrambles.
  const groupOrder = (Array.isArray(specs) ? specs : []).map((s) => s.label);
  const order = groupOrder.length ? groupOrder
    : (Array.isArray(categories) ? categories : []);
  const rank = (g) => {
    const i = order.indexOf(g.name);
    return i === -1 ? order.length : i;        // unnamed sinks to the bottom
  };
  builtin.sort((a, b) => rank(a) - rank(b));
  return [...groups.filter((g) => g.kind === "category"),
          ...groups.filter((g) => g.kind === "connector"),
          ...builtin];
}

//: Why this row cannot be switched, or "" if it can.
//:
//: The reason is the connector's OWN `reason`, written by the layer that
//: failed, for a person — never a string we compose from a name we happen to
//: recognise. There is deliberately no "this agent is built in" case: presets
//: are editable, so it could never be true, and a branch that cannot fire is
//: the kind that later fires for the wrong reason.
function toolBlockedReason(group) {
  const c = group.connector;
  if (c && c.ready === false) {
    return (c.reason || "").trim() || `${group.name} can't be reached right now.`;
  }
  return "";
}

function renderAgentTools(boxEl, { agent, tools, connectors, categories, specs, presets }) {
  if (!boxEl) return;
  const ctx = { box: boxEl, agent, tools, connectors, categories, specs, presets };
  // A draft keeps its own state, so the context it was drawn from is the only
  // thing a switch can read back — kept here rather than passed through every
  // handler, because the handlers are shared with the saved-agent path and
  // that one reads the server instead.
  if (isDraftAgent(agent)) _draftCtx = ctx;
  const groups = agentToolGroups(tools, connectors, agent && agent.tools, categories, specs);
  const usable = groups.reduce((n, g) =>
    n + (toolBlockedReason(g) ? 0 : g.tools.filter((t) => t.on).length), 0);

  if (!groups.length) {
    // Never a blank panel: say what it can still do, and offer the first thing
    // worth adding.
    boxEl.innerHTML = `<div class="at-empty">
      <p><b>${esc((agent && agent.name) || "This agent")}</b> has no tools yet. It can still
      answer from what it already knows about you — your brain is read on every
      question, whether or not any tool is switched on.</p>
      <p class="at-empty-next">The first one worth adding is a connector, so it can
      read something of yours.</p>
      <button type="button" class="tiny" data-tool-fix="1">Open Connectors</button>
    </div>`;
    wireAgentToolActions(boxEl);
    return;
  }

  const draft = isDraftAgent(agent);
  const who = esc((agent && agent.name) || "This agent");
  const total = groups.reduce((n, g) => n + g.tools.length, 0);
  // The builder prints the same count in its own header, directly above this
  // list, and two elements saying one number is how they come to disagree. The
  // panel has no such header, so it keeps the sentence.
  const summary = draft ? ""
    : `<p class="at-summary">${who} can use <b>${usable}</b> of ${total} tools.</p>`;

  boxEl.innerHTML = presetRow(ctx, agent) + summary
    + groups.map((g) => {
    const why = toolBlockedReason(g);
    const rows = g.tools.map(({ row, on }) => {
      const name = (row && row.name) || "";
      const busy = _toolSaving.has(name);
      // A control that cannot work is not shown as a control: the row carries
      // the reason instead, and the place that fixes it.
      const control = why
        ? `<span class="at-blocked">${esc(why)}</span>`
        : `<button type="button" role="switch" aria-checked="${on}"
             class="at-toggle${on ? " is-on" : ""}${busy ? " is-busy" : ""}"
             data-tool="${esc(name)}" data-on="${on ? "1" : ""}"
             aria-label="${esc(toolLabel(row))}"><span class="at-knob"></span></button>`;
      return `<div class="at-row" data-row="${esc(name)}">
        <span class="at-text">
          <span class="at-nm">${esc(toolLabel(row))}</span>
          <span class="at-ds">${esc(toolBlurb(row))}</span>
          <span class="at-err" hidden></span>
        </span>
        ${control}</div>`;
    }).join("");
    // Where it is fixed — but never from the builder, which is a modal: the
    // Connectors screen would open BEHIND it, so the button reads as doing
    // nothing, and closing the modal to reach it would throw away a half-typed
    // form. The reason already names the route in words, which is the part that
    // works from either screen.
    const fix = why && g.connector && !draft
      ? `<button type="button" class="tiny at-fix" data-tool-fix="1">Open Connectors</button>` : "";
    // A connector that cannot answer contributes NO tools, and a disclosure
    // reading "Show all 0 tools" over a second copy of the reason already on
    // the heading is two useless lines each. Four unreachable connectors made
    // that the whole first screen. One line: the name and what to do.
    const detail = g.tools.length ? `
      <details class="at-detail" data-group="${esc(g.name)}"${
          _openGroups.has(g.name) ? " open" : ""}>
        <summary>${g.tools.length === 1 ? "Show the one tool"
          : `Show all ${g.tools.length} tools`}</summary>
        <div class="at-card">${rows}</div>
      </details>` : "";
    return `<section class="at-group${why ? " is-blocked" : ""}${
        g.tools.length ? "" : " is-empty"}${
        g.spec && g.spec.always ? " is-always" : ""}">
      <div class="at-group-head">
        <h3 class="at-group-nm">${esc(g.name)}</h3>
        ${why ? `<span class="at-group-why">${esc(why)}</span>${fix}` : ""}
        ${groupMoreButton(g, draft)}
        ${groupAllButton(g, why)}
      </div>
      ${groupBlurb(g)}
      ${groupSwitches(g, why)}
      ${detail}
    </section>`;
  }).join("");
  wireAgentToolActions(boxEl, agent);
}

function wireAgentToolActions(boxEl, agent) {
  // Rebound after every render: the list is replaced wholesale, so a handler
  // on the previous nodes is a handler on nothing the user can click.
  boxEl.querySelectorAll("[data-tool-fix]").forEach((b) => {
    b.onclick = () => openConnectorsScreen();
  });
  boxEl.querySelectorAll("[data-tool]").forEach((b) => {
    b.onclick = () => toggleAgentTool(agent, b);
  });
  boxEl.querySelectorAll("[data-bulk]").forEach((b) => {
    b.onclick = () => toggleToolBucket(agent, b);
  });
  boxEl.querySelectorAll("[data-preset]").forEach((b) => {
    b.onclick = () => applyPreset(agent, b);
  });
  // Where the other half of a permission is set. The group named the screen;
  // `screenOpener` is what decides there is one to open.
  boxEl.querySelectorAll("[data-screen]").forEach((b) => {
    const open = screenOpener(b.dataset.screen);
    if (open) b.onclick = () => open();
  });
  // Remember which disclosures are open. A preset repaints the panel, and
  // without this it shuts every list the user had opened to decide with.
  boxEl.querySelectorAll("[data-group]").forEach((d) => {
    d.ontoggle = () => {
      const key = d.dataset.group || "";
      if (!key) return;
      if (d.open) _openGroups.add(key); else _openGroups.delete(key);
    };
  });
}

//: Bring a group's switches with a change made inside it.
//:
//: The other direction was handled from the start — a bucket switch patches the
//: rows under it — and this one was not, so turning one browser tool on inside
//: the disclosure left the switch above it reading a plain "off" over a tool
//: the agent could now use. Same rule, other way round: the screen must never
//: argue with itself.
//:
//: Patched in place rather than re-rendered, for the reason the bucket switch
//: already is: a re-render closes the disclosure the user is working in.
function syncGroupSwitches(section, agent) {
  if (!section || typeof section.querySelectorAll !== "function") return;
  const have = new Set((agent && agent.tools) || []);
  section.querySelectorAll("[data-bulk]").forEach((sw) => {
    const names = (sw.dataset.bulk || "").split(" ").filter(Boolean);
    if (!names.length) return;
    const on = names.filter((n) => have.has(n));
    const all = on.length === names.length;
    sw.dataset.on = all ? "1" : "";
    // "Allow all" is a button and not a switch, so it says the state in words
    // rather than in a knob position.
    if (sw.classList && sw.classList.contains("at-all")) {
      sw.textContent = all ? "Turn all off" : "Allow all";
      return;
    }
    sw.setAttribute("aria-checked", String(all));
    sw.classList.toggle("is-on", all);
    sw.classList.toggle("is-some", !all && on.length > 0);
    const bulk = typeof sw.closest === "function" ? sw.closest(".at-bulk") : null;
    const part = bulk && typeof bulk.querySelector === "function"
      ? bulk.querySelector(".at-part") : null;
    // Just as wrong when stale as the switch beside it.
    if (part) part.textContent = all || !on.length ? "" : `${on.length} of ${names.length} on`;
  });
}

//: One switch, every tool in a bucket. The whole point of the compaction.
//:
//: A separate function from `toggleAgentTool` rather than a loop over it,
//: because N tools must be **one** PATCH: looping would fire seven requests
//: that each send the whole list, and whichever replied last would win — so
//: turning a group on could land as a group half on, depending on the network.
//: Hand a preset NAME to the server, never a list of tools.
//:
//: "Allow everything" has to mean everything *now*. A screen open while a tool
//: shipped would otherwise send its own stale idea of the word and quietly
//: withhold the new one — the failure `agents/grants.py` already records for
//: connectors, where the option did not exist to tick at build time and
//: nothing ever told anyone to go back.
async function applyPreset(agent, btn) {
  const key = btn.dataset.preset;
  if (!agent || !key || btn.classList.contains("is-busy")) return;
  // A draft has nothing to PATCH. The preview moves the switches so the button
  // visibly does something; the NAME is what Create sends, and the server
  // resolves it there — so this screen never becomes the authority on the word
  // "everything".
  if (isDraftAgent(agent)) {
    const next = draftPresetTools(_draftCtx, key);
    if (!next) return;              // a server that sent no preview for it
    agent.tools = next;
    agent.preset = key;
    rerenderDraft();
    return;
  }
  btn.classList.add("is-busy");
  try {
    const saved = await api(`/api/agents/${encodeURIComponent(agent.id)}/tools`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset: key }),
    });
    agent.tools = saved.tools || agent.tools;
    const inList = agents.find((a) => a.id === agent.id);
    if (inList) inList.tools = agent.tools;
    // A preset moves every switch on the screen, so this one IS a re-render —
    // unlike a single bucket, where patching the rows in place keeps the
    // disclosures the user opened.
    loadAgentTools(agent.id);
  } catch (e) {
    toast("Couldn't save that — try again.");
  } finally {
    btn.classList.remove("is-busy");
  }
}

async function toggleToolBucket(agent, btn) {
  const names = (btn.dataset.bulk || "").split(" ").filter(Boolean);
  if (!agent || !names.length) return;
  const wasOn = btn.dataset.on === "1";
  const forAgent = agent.id;
  const next = !wasOn;

  if (isDraftAgent(agent)) { setDraftTools(agent, names, next); return; }

  btn.dataset.on = next ? "1" : "";
  btn.setAttribute("aria-checked", String(next));
  btn.classList.toggle("is-on", next);
  btn.classList.remove("is-some");        // it is all-or-nothing after a press
  btn.classList.add("is-busy");

  const tools = new Set(agent.tools || []);
  for (const n of names) { if (next) tools.add(n); else tools.delete(n); }

  try {
    const saved = await api(`/api/agents/${encodeURIComponent(forAgent)}/tools`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tools: [...tools] }),
    });
    agent.tools = saved.tools || [...tools];
    const inList = agents.find((a) => a.id === forAgent);
    if (inList) inList.tools = agent.tools;
    // Bring the per-tool rows with it. They are inside the disclosure below
    // this switch, and a row showing the opposite of the switch above it is
    // the screen arguing with itself. Updated in place rather than by a
    // re-render, because a re-render would close every disclosure the user
    // had opened.
    const section = btn.closest(".at-group");
    for (const n of names) {
      const row = section && section.querySelector(`[data-tool="${CSS.escape(n)}"]`);
      if (!row) continue;
      row.dataset.on = next ? "1" : "";
      row.setAttribute("aria-checked", String(next));
      row.classList.toggle("is-on", next);
    }
    // The group's OTHER switches, and its "Allow all": a bucket press changes
    // whether the whole group is on, and a stale button beside a live one is
    // the same lie in a different place.
    syncGroupSwitches(section, agent);
  } catch (e) {
    btn.dataset.on = wasOn ? "1" : "";
    btn.setAttribute("aria-checked", String(wasOn));
    btn.classList.toggle("is-on", wasOn);
    toast("Couldn't save that — try again.");
  } finally {
    btn.classList.remove("is-busy");
  }
}

async function toggleAgentTool(agent, btn) {
  const name = btn.dataset.tool;
  if (!agent || !name || _toolSaving.has(name)) return;
  const wasOn = btn.dataset.on === "1";
  const forAgent = agent.id;

  if (isDraftAgent(agent)) { setDraftTools(agent, [name], !wasOn); return; }

  // Move the switch first. It is the user's action; making them wait for a
  // round trip to see their own click land is what makes a toggle feel broken.
  const next = !wasOn;
  btn.dataset.on = next ? "1" : "";
  btn.setAttribute("aria-checked", String(next));
  btn.classList.toggle("is-on", next);
  btn.classList.add("is-busy");
  _toolSaving.add(name);

  const row = btn.closest(".at-row");
  const err = row && row.querySelector(".at-err");
  if (err) { err.hidden = true; err.textContent = ""; }

  const tools = new Set(agent.tools || []);
  if (next) tools.add(name); else tools.delete(name);

  try {
    const saved = await api(`/api/agents/${encodeURIComponent(forAgent)}/tools`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tools: [...tools] }),
    });
    // Trust what came back, not what we sent.
    agent.tools = saved.tools || [...tools];
    const inList = agents.find((a) => a.id === forAgent);
    if (inList) inList.tools = agent.tools;
    // The switches ABOVE this row are about the group it is in, and one of them
    // now covers a tool it does not say it covers.
    syncGroupSwitches(typeof btn.closest === "function" ? btn.closest(".at-group") : null,
                      agent);
  } catch (e) {
    // Put it back, and say so in the row. A toast would be gone by the time
    // the user looked at the switch that lied.
    btn.dataset.on = wasOn ? "1" : "";
    btn.setAttribute("aria-checked", String(wasOn));
    btn.classList.toggle("is-on", wasOn);
    if (err) { err.textContent = "Couldn't save that — try again."; err.hidden = false; }
  } finally {
    _toolSaving.delete(name);
    btn.classList.remove("is-busy");
  }
}

async function loadAgentTools(agentId) {
  const box = $("#agentToolList"); if (!box) return;
  const id = agentId || AGENT_TOOLS_FOR || current;
  // Repainting a full panel with "Loading…" is a flash of nothing in the middle
  // of somebody reading it, and a preset press reloads deliberately. The
  // placeholder is for an empty panel, or for a switch to a DIFFERENT agent —
  // where leaving the old one's switches up would be showing one agent's
  // permissions under another agent's name.
  const same = AGENT_TOOLS_FOR === id && !!box.innerHTML.trim();
  AGENT_TOOLS_FOR = id;
  if (!same) box.innerHTML = `<div class="at-empty">Loading…</div>`;
  let tools = [], categories = [], specs = [], presets = [];
  try {
    const got = await api("/api/agents/tools");
    ({ tools, categories } = got);
    specs = got.groups || [];
    presets = got.presets || [];
  }
  catch (e) { box.innerHTML = `<div class="at-empty">Couldn't load the tool list.</div>`; return; }
  if (AGENT_TOOLS_FOR !== id) return;      // the user switched while we waited

  const agent = (agents || []).find((a) => a.id === id);
  if (!agent) { box.innerHTML = `<div class="at-empty">Pick an agent.</div>`; return; }

  renderAgentTools(box, { agent, tools, connectors: CONNECTORS, categories, specs, presets });
  if (!CONNECTORS.length) {
    // /api/connectors starts every added server to answer honestly, so it is
    // far too slow to block a panel on. Render what we know, then sharpen.
    try {
      const { connectors } = await api("/api/connectors");
      CONNECTORS = connectors;
      if (AGENT_TOOLS_FOR === id) renderAgentTools(box, { agent, tools, connectors, categories, specs, presets });
    } catch (_) { /* the tools are on screen; health is a bonus */ }
  }
}

function renderAgentToolPicker() {
  const sel = $("#agentToolPicker"); if (!sel) return;
  const id = AGENT_TOOLS_FOR || current;
  sel.innerHTML = (agents || []).map((a) =>
    `<option value="${esc(a.id)}"${a.id === id ? " selected" : ""}>${esc(a.name)}</option>`).join("");
  sel.onchange = () => loadAgentTools(sel.value);
}

