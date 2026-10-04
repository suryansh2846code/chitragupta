/**
 * What one agent may use — one card per app, two switches on each.
 *
 * **The axis changed, and that is the whole of this screen's history.**
 *
 * It began as sixty-four switches under thirteen headings: a question about
 * every tool, asked before the user had sent the agent a single message. That
 * became four cards by *reach* — what stays on this machine, what touches an
 * account, what touches a website, what touches your files — which is the right
 * question for the **gate** and the wrong one for a person. It put Gmail, the
 * calendar, Telegram, Notion and Linear on one card called "Your connected
 * accounts", so a user who wanted to say *read GitHub, leave my mail alone* had
 * no control that said it.
 *
 * People think in apps. So the card is the app: Gmail, The browser, Your Mac,
 * GitHub. On each one, **Read** and the one word that app's changes actually
 * are — Change for a web page, Write for a file, Send for mail. Between the
 * switch and the next row is the roll-up: what that switch covers, in the names
 * a person reads. The per-tool list survives in the disclosure underneath for
 * anyone who wants it; taking it away would be removing control, where the
 * complaint was that control was the only thing on offer.
 *
 * **The second switch is never one generic word.** `CLAUDE.md` forbids folding
 * `outbound` or `destructive` into "change": a tap given for one must never
 * silently cover the other. So running code is its own switch on the one card
 * that has it, reaching a person is its own switch wherever one appears, and
 * the wording comes down the wire from `tool_facts.App` rather than being
 * guessed here.
 *
 * **Some apps change things without a tool.** Sending mail, adding an event,
 * running somebody else's verb in GitHub — those are *actions*, and every one
 * comes to the user as a card they confirm. There is no switch to draw, so the
 * row says that instead. An app showing only "Read" reads as an app that cannot
 * do anything else, which is false, and is what sends people hunting through
 * settings for a control that does not exist.
 *
 * **Nothing on this screen may be a control that does nothing.** Three
 * permissions used to have a working endpoint and no UI at all — the folders
 * agents may reach, which connectors an agent may use without asking, and the
 * people it may write to unattended. "Your Mac → Read" granted a tool whose
 * only possible answer was *"No folder has been opened to agents yet"*, and no
 * screen anywhere opened one. They are all on the card they belong to now.
 *
 * There used to be a second screen as well — a read-only "Tools & skills"
 * drawer off the sidebar. Same endpoint, same grouping, same rows, no switches,
 * and it printed each connector's description in full where this one trims to
 * the first sentence. A strict subset of this panel, reached by a nav item with
 * almost the same name, and neither said the other existed. It is gone; the two
 * rules only its tests covered moved into `test_frontend_agent_tools.py`.
 *
 * Every skill still says where it came from, and a connector that cannot answer
 * is shown as unreachable rather than dropped: a capability that silently
 * vanishes is indistinguishable from one that never existed. Names come from
 * the connector, so they are escaped like any other text the user's own sources
 * supply, and the acronym for the protocol a connector speaks never reaches
 * this screen — the user added Linear; the user sees Linear.
 */

// What a person reads for this tool. `name` is an id — for a connector tool it
// is a qualified one we minted — and an id on screen is an internal surfaced.
function toolLabel(t) {
  return ((t && (t.label || t.name)) || "").trim();
}

// A row that grants a whole category rather than naming one connector's tool.
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

// The heading a built-in sits under, inside its card. The API names it; a tool
// that arrives without one lands in "Other", which is visible enough to get
// fixed.
function toolCategory(t) { return ((t && t.category) || "").trim() || "Other"; }

// ── what a built-in touches, and what it does to it ───────────────────────
//
// `app` and `access` come from the API because the decision is not this
// layer's: a tool declares a capability, and `agents/tool_facts.py` derives
// both from it. A consumer that re-derived them would be the name chain this
// file's own comments keep warning about. `group` is still sent and is still
// the GATE's axis — `request_permission` and the agent's own prompt reason in
// it — so it is read only as the fallback for a server that predates `app`.
function toolAppKey(t) { return ((t && t.app) || "").trim(); }
function toolGroupKey(t) { return ((t && t.group) || "").trim(); }
function toolAccess(t) { return ((t && t.access) || "").trim(); }

//: The tiers a card can draw a switch for, in the order they escalate.
//:
//: `access` is the server's word and `verb` is the person's — they are not the
//: same word and must not be assumed to be. Writing the bucket keys as the UI
//: labels and comparing them straight against `access` silently matched
//: nothing for two of the four tiers, so "Change" and the irreversible bucket
//: rendered no switch at all while their tools sat in the disclosure below,
//: on. A panel that omits a switch for something an agent can do is worse than
//: the sixty-four it replaced.
//:
//: `verb` takes the card's own word where the card has one — "Change" on a web
//: page, "Write" on a file — because one generic word across every card is a
//: switch that was set for one app and granted another. `outbound` keeps a
//: bucket of its own even though no built-in is one today: folding it into
//: "change" would mean the first tool that reaches a person arrives already
//: covered by a switch somebody set for something else.
const ACCESS_BUCKETS = [
  { key: "read", access: ["read"], word: () => "Read",
    blurb: "Look, and report back." },
  { key: "change", access: ["write"], word: (c) => c.writeLabel || "Change",
    blurb: "Alter something. Each change is yours to undo." },
  { key: "send", access: ["outbound"], word: () => "Send to people",
    blurb: "Reaches somebody who is not you." },
  { key: "run", access: ["destructive"], word: (c) => c.runLabel || "Irreversible",
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

//: The id permission is decided against, as opposed to the name a person
//: reads. A user can rename a connector; a rename must not change who may use
//: what, so the grant switch sends this and never the label.
function toolConnectorId(t) { return ((t && t.connector_id) || "").trim(); }

// A connector row exists either because we ship it or because the user added
// it — `custom` apps come from their own form, `mcp` ones from a server they
// chose. Only those two can be "configured but unreachable"; the rest are
// merely not set up yet, which the Sources panel already says in its own words.
function userConfigured(c) { return !!(c && (c.custom || c.mcp)); }

//: A connector the user added that cannot answer right now — as opposed to one
//: we ship that was simply never set up, which is not this screen's business.
function unreachableConnector(c) { return userConfigured(c) && c.ready === false; }


// ── per-agent tools ────────────────────────────────────────────────────────
//
// **The render is split from its fetch so the real path can be executed in a
// test.** An empty panel and a panel that threw are indistinguishable from
// outside, and this file has shipped both — `node --check` passes on the
// temporal-dead-zone `ReferenceError` that once blanked the whole screen.

//: The agent whose tools are on screen. A save names the agent it was for, so
//: a reply arriving after the user has switched cannot repaint the new one.
let AGENT_TOOLS_FOR = "";
//: Saves in flight, by tool name, so a row can show its own progress and a
//: second click cannot race the first.
const _toolSaving = new Set();

//: The other half of three permissions, fetched beside the tool list.
//:
//: None of these is a tool, and each is the thing that decides whether the
//: tools above it can reach anything at all: which sites the browser may open,
//: which folders exist on disk for an agent to read, whether this agent may use
//: a connector without asking first, and which standing grants already let an
//: action run unattended. They were endpoints with no screen, which is the same
//: dead end as a switch with no endpoint and harder to spot.
//:
//: `forAgent` is stamped so a reply arriving after the user has switched agents
//: cannot be drawn under the new one's name.
let _PANEL = { forAgent: "", sites: null, folders: null, grants: null,
               reach: null };

//: An agent that does not exist yet — the one the builder is drawing.
//:
//: The builder and this panel are the SAME list, and the only difference is
//: where the answer is kept: a saved agent's switches each PATCH, a draft's
//: live in the object on screen and nothing is sent until Create. A flag
//: rather than a second renderer, because a second renderer is exactly what
//: this screen had — and the builder's copy was two releases behind it. Polish
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
  rerenderDraft();
}

//: Which cards the user has opened, so a re-render does not shut them.
//:
//: The panel repaints when the sites, folders and grants land, and every
//: disclosure anybody had opened closed with it — including the one they were
//: reading in order to decide. Keyed by card name because that is what the user
//: opened; a name that is gone by the next render simply never matches.
const _openGroups = new Set();

//: Leaving this panel means leaving the profile it is a tab inside.
//:
//: Every button here that opens another screen used to navigate **behind** the
//: profile: the page changed, the modal stayed up, and the user was looking at
//: a dialog over a screen they had just been sent to. One of the five closed
//: first — the one that was written last — and the other four did not, which is
//: a fix applied per case rather than to the shape. So nothing navigates except
//: through here.
//:
//: And it returns whether it actually left. `closeAgentProfile` asks before
//: discarding unsaved changes, so "close then go" navigated anyway when the
//: user said no — leaving them exactly where the bug above put them, having
//: just declined to go.
function leavePanel() {
  if (typeof closeAgentProfile !== "function") return true;   // not in a profile
  closeAgentProfile();
  const bg = document.querySelector("#agentProfile");
  return !bg || bg.hidden !== false;
}

function goElsewhere(run) {
  return () => { if (leavePanel()) run(); };
}

//: The one list of settings screens this panel can send somebody to.
//:
//: A card says where the other half of its permission lives — "which sites" is
//: not a tool switch — and the ID it names is resolved HERE, because which
//: screens exist is the frontend's fact and not the API's. An ID nothing
//: recognises draws no button, so naming a new one server-side can never
//: produce a control that goes nowhere.
function screenOpener(id) {
  if (id === "connectors" && typeof openConnectorsScreen === "function") {
    return goElsewhere(openConnectorsScreen);
  }
  if (id === "allowlist") {
    // The one permission here that is global rather than per agent, so it
    // could not come into the agent profile with the rest — it followed the
    // approvals queue onto the Actions screen. Nothing on a card links here
    // any more (the grants are listed in the row itself, and a new one is
    // added in the row too); this stays for anything that still names it.
    return goElsewhere(() => {
      if (typeof openActionsScreen === "function") openActionsScreen();
      // After the panel has been shown: the element has no box until then, and
      // `scrollIntoView` on a hidden one does nothing at all.
      setTimeout(() => {
        const el = document.querySelector("#allowList");
        if (el && typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }, 0);
    });
  }
  return null;
}

// ── the cards ──────────────────────────────────────────────────────────────

//: What one switch covers, in the names a person reads.
//:
//: The "summarise all the permissions in between" half of this screen. A switch
//: labelled "Read" over a collapsed list says nothing about whether reading
//: includes scrolling, going back, or waiting for a page — and the user
//: deciding has to open the disclosure to find out, which is the wall this
//: layout replaced. Six names, then a count: enough to recognise the shape of
//: the grant, short enough to stay one line.
function coveredBy(items) {
  const names = items.map(({ row }) => toolLabel(row)).filter(Boolean);
  if (!names.length) return "";
  if (names.length <= 6) return names.join(" · ");
  return `${names.slice(0, 6).join(" · ")} · +${names.length - 6} more`;
}

//: One tier of one card: the word, what it covers, and the switch.
//:
//: One function for all four tiers rather than a branch per tier, because the
//: thing that differs between them is a word and a list, and a copy per tier is
//: how "Change" ended up with no switch while its tools sat on underneath.
function tierRow(card, bucket, items, why) {
  const on = items.every(({ on: isOn }) => isOn);
  const some = !on && items.some(({ on: isOn }) => isOn);
  const names = items.map(({ row }) => (row && row.name) || "").join(" ");
  const word = bucket.word(card);
  const control = why
    ? `<span class="at-blocked">${esc(why)}</span>`
    : `<button type="button" role="switch" aria-checked="${on}"
         class="at-toggle${on ? " is-on" : ""}${some ? " is-some" : ""}"
         data-bulk="${esc(names)}" data-on="${on ? "1" : ""}"
         aria-label="${esc(word)} — ${esc(card.name)}"><span class="at-knob"></span></button>`;
  // "3 of 7 on" rather than a half-lit switch with nothing explaining it: a
  // partially-granted tier is a real state, usually because somebody used the
  // per-tool rows, and it has to be legible without opening them.
  //
  // The element is always drawn and empty when there is nothing to say, so a
  // switch flipped inside the disclosure can fill it without a re-render.
  // Rendering it only when it has content meant the one case it exists for —
  // a tier becoming partial — had nowhere to put the sentence.
  const part = `<span class="at-part">${
    some ? `${items.filter((t) => t.on).length} of ${items.length} on` : ""}</span>`;
  return `<div class="at-bulk" data-bucket="${esc(bucket.key)}">
    <span class="at-text">
      <span class="at-nm">${esc(word)}</span>
      <span class="at-ds">${esc(coveredBy(items))}</span>
      <span class="at-sub">${esc(bucket.blurb)}</span>
    </span>${part}${control}</div>`;
}

//: The row for a change that is an ACTION rather than a tool.
//:
//: Sending mail, adding an event, running somebody else's verb — none of them
//: is a tool, so there is no switch to draw and nothing this screen could
//: toggle. Saying nothing is what it used to do, and an app showing only "Read"
//: reads as an app that cannot do anything else. It can; it asks first.
//:
//: **And it says what the standing grants actually change, which is nothing
//: you will see in a conversation.** `approvals.run_or_queue` is "the seam
//: every *unattended* action goes through — interactive chat does not come
//: this way", so the list only decides what an automation may do with nobody
//: watching. The row used to read "always comes to you as a card you confirm"
//: and then offer a box that undoes it: both sentences true, of different
//: situations, and neither saying which.
//:
//: **Read-only, and empty draws nothing.** A grant is made from the approval
//: it would have cleared — the queue offers it there with the exact value the
//: gate reads, at the moment somebody learns they want one. A box here asked
//: people to predict that, and wrote to the same global list from a second
//: place. "Nothing runs on its own yet" over an empty row was a sentence every
//: user read once, on a screen about something else.
function askRow(card, draft) {
  const ask = card.ask;
  if (!ask || !ask.label) return "";
  const held = reachFor(card);
  // `null` is "not asked yet" and `[]` is "none" — and here they draw the
  // same thing, because there is nothing worth saying about an empty list on
  // a screen that cannot change it.
  const grants = Array.isArray(held) && held.length
    ? `<span class="at-reach">
         <span class="at-reach-lb">Runs unattended for, across every agent:</span>
         ${held.map((g) => `<span class="at-chip">${esc(g.label || g.value)}
           ${draft ? "" : `<button type="button" class="at-chip-x"
             data-reach-off="${esc(g.value)}" data-reach-kind="${esc(g.kind || "")}"
             aria-label="Ask again before reaching ${esc(g.label || g.value)}"
             >×</button>`}</span>`).join("")}
       </span>`
    : "";
  return `<div class="at-bulk is-ask">
    <span class="at-text">
      <span class="at-nm">${esc(ask.label)}</span>
      <span class="at-ds">${esc(ask.blurb || "")}</span>
      ${grants}
    </span></div>`;
}

//: The standing grants that belong to ONE card, out of the list that holds all
//: of them. `null` until the answer is back.
//:
//: A connector's grants are matched on the **server id** its keys start with,
//: never on the label: a user can rename a connector, and a rename must not
//: change which permissions are shown as belonging to it.
function reachFor(card) {
  const all = _PANEL.reach;
  if (!Array.isArray(all)) return null;
  if (card.kind === "connector") {
    const id = (card.connectorId || "").toLowerCase();
    if (!id) return [];
    return all.filter((g) => g.kind === "connector_tool"
      && String(g.value || "").toLowerCase().startsWith(`${id}:`));
  }
  const kind = (card.ask && card.ask.kind) || "";
  return kind ? all.filter((g) => g.kind === kind) : [];
}

//: The live line under a card: the other half of its permission, as it stands.
//:
//: Reading is allowed on three sites; no folder is open at all; this agent has
//: to ask before it may use Notion. Each is a fact the switches above cannot
//: express and the user cannot act on without it — and each used to be
//: invisible, which is why "I turned it on and it still says no" was the most
//: common thing this screen produced.
function cardStrip(card, ctx) {
  const draft = isDraftAgent(ctx.agent);
  // The grant comes FIRST where there is one: "may this agent reach the
  // browser at all" is upstream of "which sites", and a card that led with the
  // sites answered the second question while the first one was still no.
  const grant = grantStrip(card, draft);
  if (card.key === "browser") return grant + sitesStrip(draft);
  if (card.key === "mac") return foldersStrip(draft);
  return grant;
}

//: The connector this card is gated on, whether it is one of the user's own
//: servers or one of ours.
//:
//: **Built-in cards have one too, and missing that is what kept the browser
//: broken.** `browse_open` is in `connector_grants.FIRST_PARTY_TOOLS`, so
//: reaching the browser is a permission of its own — stored per agent, and
//: exempted by exactly one template. This function used to answer only for
//: `kind === "connector"`, so Gmail, the calendar and the browser had no
//: control anywhere in the app, their switches read as on, and every agent but
//: Chief of Staff was refused.
function grantConnectorId(card) {
  if (card.kind === "connector") return card.connectorId || "";
  return (card.grant && card.grant.connector) || "";
}

function sitesStrip(draft) {
  const sites = _PANEL.sites;
  // Anything that is not a list is "not back yet", including a key nobody set.
  // `[]` and "we have not asked" mean different things to every strip — empty
  // is a fact worth stating, and a panel that claimed "no site is allowed"
  // before asking would be wrong for the first few hundred milliseconds of
  // every open.
  if (!Array.isArray(sites)) return "";
  if (!sites.length) {
    return `<p class="at-strip is-warn">No site is allowed yet, so nothing here
      can open a page. ${draft ? "You can allow one under Connectors."
        : `<button type="button" class="link" data-screen="connectors"
             >Choose which sites</button>`}</p>`;
  }
  const shown = sites.slice(0, 3).map((s) => s.host || s.origin || "").filter(Boolean);
  const more = sites.length > shown.length ? ` · +${sites.length - shown.length} more` : "";
  return `<p class="at-strip">${sites.length} site${sites.length === 1 ? "" : "s"} allowed
    — ${esc(shown.join(" · "))}${esc(more)}</p>`;
}

//: Where a folder is opened to agents — on the card, not on another screen.
//:
//: `tool_facts.App("mac")` deliberately names no `more_screen`: there was no
//: screen to name. The endpoint has existed since the file tools shipped and
//: nothing in the app called it, so every agent granted "Your Mac → Read" got a
//: tool that could only ever answer *"No folder has been opened to agents
//: yet"*. A control in the card beats a button that sends somebody somewhere.
function foldersStrip(draft) {
  const folders = _PANEL.folders;
  if (!Array.isArray(folders)) return "";        // see `sitesStrip`
  const rows = folders.map((p) => `
    <span class="at-chip">${esc(p)}
      ${draft ? "" : `<button type="button" class="at-chip-x" data-folder-off="${esc(p)}"
        aria-label="Close ${esc(p)} to agents">×</button>`}</span>`).join("");
  const add = draft ? "" : `
    <span class="at-folder-add">
      <input id="atFolderPath" class="set-input" type="text" autocomplete="off"
             placeholder="~/Documents/work" aria-label="A folder to open to agents" />
      <button type="button" class="tiny" data-folder-add="1">Open</button>
    </span>
    <span class="at-err" id="atFolderErr" hidden></span>`;
  if (!folders.length) {
    return `<div class="at-strip is-warn">
      <p>No folder is open to agents yet, so nothing here can reach anything on
      this Mac. Opening one is the consent — there are no default grants.</p>
      ${add}</div>`;
  }
  return `<div class="at-strip"><p>Agents can work in:</p>
    <div class="at-chips">${rows}</div>${add}</div>`;
}

//: Whether this agent may use this connector without asking, per agent.
//:
//: A separate permission from the switches above it, and deliberately a
//: separate control: the switches decide whether the agent can see the
//: connector's tools at all, and this decides whether reaching the account
//: behind them interrupts the user first. Collapsing the two would be one tap
//: granting two different things.
function grantStrip(card, draft) {
  const grants = _PANEL.grants;
  const connector = grantConnectorId(card);
  if (draft || !grants || !connector) return "";
  if (grants.unrestricted) {
    return `<p class="at-strip">This agent is allowed to reach every connected
      app without asking. That comes with the agent, not from this screen.</p>`;
  }
  const allowed = (grants.allowed || []).includes(connector);
  // The connector's own name where there is one, and the card's heading when
  // it is not set up yet — `first_party_labels` only names what is configured,
  // and the control must not vanish exactly when somebody needs to find it.
  //
  // The name goes at the START of the sentence and never into the button. A
  // heading is written to sit above a card ("The browser"), so mid-sentence it
  // is a stray capital and "Allow The browser" is not a label anybody writes.
  const named = (card.grant && card.grant.label) || card.name;
  // **Not "asks you" — it refuses.** An agent with no grant does not get a
  // card it can wait on: the tool comes back refused and the turn says so.
  // Wording it as a question is what let five attempts read as a switch that
  // had not taken.
  // **Blocked has to look blocked.** As a sentence with a link in it this read
  // as a footnote under two switches that both said "on", which is the exact
  // picture that sent somebody to the switches five times. A headline, the
  // consequence under it, and a real button — and the switches above are dimmed
  // by `is-gated` on the card, because a live-looking switch over a refusal is
  // the screen arguing with itself.
  if (allowed) {
    // One element, not a bold and a text node: `at-reach-txt` is a column
    // flex, so an anonymous text node beside a `<b>` becomes a second item and
    // the sentence breaks after the name.
    return `<div class="at-reach-state is-on">
      <span class="at-reach-txt"><span><b>${esc(named)}</b> — this agent may
        reach it without asking.</span></span>
      <button type="button" class="tiny ghost" data-grant="${esc(connector)}"
        data-on="1">Make it ask again</button>
    </div>`;
  }
  return `<div class="at-reach-state is-off">
    <span class="at-reach-txt">
      <b class="at-reach-hd">This agent cannot use ${esc(named)} yet</b>
      <span>The switches above are set, and every one of them is refused until
      you allow this. It is a second permission and it is kept per agent.</span>
    </span>
    <button type="button" class="tiny at-reach-go" data-grant="${esc(connector)}"
      data-on="">Allow ${esc(named)}</button>
  </div>`;
}

//: Where the other half of this card's permission is set.
//:
//: "Allow changes on websites" grants the agent nothing on its own: which sites
//: it may touch is a list kept on another screen, and a user who turned every
//: switch here on and was still refused had no way to learn that from this
//: panel. The card names the screen; this resolves it, and draws nothing for an
//: ID the frontend does not have.
function cardMoreButton(card, draft) {
  const more = card.more;
  // Not from the builder: it is a modal, and the screen this opens would come
  // up behind it. The card's own sentence still says the list exists.
  if (draft || !more || !more.screen || !screenOpener(more.screen)) return "";
  return `<button type="button" class="tiny at-more" data-screen="${esc(more.screen)}"
    >${esc(more.label || "Open")}</button>`;
}

//: Every switch on one card. Empty for the always-on one, which has nothing to
//: decide: an agent that cannot read its own memory is not a lesser agent, it
//: is a broken one.
function cardSwitches(card, why, draft) {
  if (card.always) {
    // Not a second copy of the card's own sentence: the blurb above already
    // says nothing here leaves the machine, and a line repeating it is a line
    // nobody reads twice. This one says what the STATE is.
    return `<p class="at-always">Always on — granted when the agent is created,
      and not something to switch.</p>`;
  }
  // The ask row is INSIDE the same card as the switches, not a box under it.
  // "Read" and "Send" are two rows of one control — Gmail's permissions — and
  // drawing the second in a container of its own made it read as a separate,
  // unrelated thing that happened to be nearby.
  const rows = ACCESS_BUCKETS.map((b) => {
    const items = card.tools.filter(({ row }) => bucketOf(row) === b.key);
    return items.length ? tierRow(card, b, items, why) : "";
  }).join("") + (why ? "" : askRow(card, draft));
  return rows ? `<div class="at-bulks">${rows}</div>` : "";
}

// ── building the cards ─────────────────────────────────────────────────────

//: One card per app, built from what the API said each tool touches.
//:
//: Three kinds, and each is asked for by a different field:
//:   a connector's own tools  — `connector`
//:   the standing connector grant — `source === "category"`
//:   a built-in               — its `app`, from `tool_facts.permission_apps`
//:
//: Connectors come first: this screen exists because of them. The built-in
//: cards follow in the API's order — it is the layer that decided "Your Mac"
//: comes last, and a consumer sorting them itself would be re-deciding that
//: alphabetically.
//:
//: `apps` absent — an older server — falls back to the gate's `groups`, and
//: then to the thirteen categories. Worse each time, and still a working
//: screen: a panel that renders nothing because one field is missing is the
//: failure mode this whole file's tests exist for.
function agentToolApps(tools, connectors, agentTools, apps, specs, categories) {
  const rows = Array.isArray(tools) ? tools : [];
  const have = new Set(Array.isArray(agentTools) ? agentTools : []);
  const appSpecs = Array.isArray(apps) ? apps : [];
  const groupSpecs = Array.isArray(specs) ? specs : [];

  const byApp = new Map(appSpecs.filter((a) => a && a.key).map((a) => [a.key, a]));
  const byGroup = new Map(groupSpecs.filter((g) => g && g.key).map((g) => [g.key, g]));

  const health = new Map();
  for (const c of (Array.isArray(connectors) ? connectors : [])) {
    const label = ((c && c.label) || "").trim();
    if (label) health.set(label.toLowerCase(), c);
  }

  const cards = [];
  const seen = new Map();
  const cardFor = (key, name, kind, spec) => {
    const id = `${kind}:${key.toLowerCase()}`;
    let card = seen.get(id);
    if (!card) {
      card = {
        key, name, kind, tools: [], connector: null, connectorId: "",
        blurb: (spec && spec.blurb) || "",
        grant: (spec && spec.grant) || null,
        always: !!(spec && spec.always),
        writeLabel: (spec && spec.write_label) || "",
        runLabel: (spec && spec.run_label) || "",
        ask: (spec && spec.ask) || null,
        more: (spec && spec.more) || null,
      };
      seen.set(id, card); cards.push(card);
    }
    return card;
  };

  for (const t of rows) {
    const name = (t && t.name) || "";
    let card;
    if (isCategoryRow(t)) {
      card = cardFor("connected-apps", "Your connected apps", "category");
      card.blurb = card.blurb || toolBlurb(t);
    } else if (toolConnector(t)) {
      card = cardFor(toolConnector(t), toolConnector(t), "connector");
      card.connectorId = card.connectorId || toolConnectorId(t);
    } else {
      // The app axis first, the gate's axis as the fallback, the old heading
      // last. Each step down loses a sentence and keeps a working screen.
      const appSpec = byApp.get(toolAppKey(t));
      const groupSpec = byGroup.get(toolGroupKey(t));
      card = appSpec
        ? cardFor(appSpec.key, appSpec.label, "builtin", appSpec)
        : groupSpec
          ? cardFor(groupSpec.key, groupSpec.label, "builtin", groupSpec)
          : cardFor(toolCategory(t), toolCategory(t), "builtin");
    }
    card.tools.push({ row: t, on: have.has(name) });
  }

  // A connector the user added that cannot answer contributes no tools, so
  // without this it is simply absent — and absent reads as "Chitragupta lost
  // it" rather than "sign in again".
  for (const c of (Array.isArray(connectors) ? connectors : [])) {
    if (!unreachableConnector(c)) continue;
    const label = (c.label || "").trim();
    if (label) cardFor(label, label, "connector").connector = c;
  }
  for (const card of cards) {
    if (card.kind !== "connector") continue;
    if (!card.connector) card.connector = health.get(card.name.toLowerCase()) || null;
    // Changing anything in a connected app runs through one action, and that
    // action always comes back as a card. Written here rather than declared
    // server-side because the app's NAME is in the sentence, and the server
    // does not know which connectors this user has.
    card.ask = card.ask || {
      label: "Change anything",
      blurb: `Creating, editing or running anything in ${card.name} comes to `
           + "you as a card you confirm. Allow one afterwards and the next "
           + "identical one can run on its own.",
    };
  }

  const order = appSpecs.length ? appSpecs.map((a) => a.label)
    : groupSpecs.length ? groupSpecs.map((g) => g.label)
      : (Array.isArray(categories) ? categories : []);
  const rank = (card) => {
    const i = order.indexOf(card.name);
    return i === -1 ? order.length : i;        // unnamed sinks to the bottom
  };
  const builtin = cards.filter((c) => c.kind === "builtin").sort((a, b) => rank(a) - rank(b));
  return [...cards.filter((c) => c.kind === "category"),
          ...cards.filter((c) => c.kind === "connector"),
          ...builtin];
}

//: Kept so a consumer written against the old name still resolves. The shape
//: it returns is the card list above; the panel is the only caller.
function agentToolGroups(tools, connectors, agentTools, categories, specs, apps) {
  return agentToolApps(tools, connectors, agentTools, apps, specs, categories);
}

//: Why this card cannot be switched, or "" if it can.
//:
//: The reason is the connector's OWN `reason`, written by the layer that
//: failed, for a person — never a string we compose from a name we happen to
//: recognise. There is deliberately no "this agent is built in" case: presets
//: are editable, so it could never be true, and a branch that cannot fire is
//: the kind that later fires for the wrong reason.
function toolBlockedReason(card) {
  const c = card.connector;
  if (c && c.ready === false) {
    return (c.reason || "").trim() || `${card.name} can't be reached right now.`;
  }
  return "";
}

function renderAgentTools(boxEl, { agent, tools, connectors, categories, specs, apps }) {
  if (!boxEl) return;
  const ctx = { box: boxEl, agent, tools, connectors, categories, specs, apps };
  // A draft keeps its own state, so the context it was drawn from is the only
  // thing a switch can read back — kept here rather than passed through every
  // handler, because the handlers are shared with the saved-agent path and
  // that one reads the server instead.
  if (isDraftAgent(agent)) _draftCtx = ctx;
  const cards = agentToolApps(tools, connectors, agent && agent.tools, apps, specs, categories);
  const usable = cards.reduce((n, c) =>
    n + (toolBlockedReason(c) ? 0 : c.tools.filter((t) => t.on).length), 0);

  if (!cards.length) {
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
  const total = cards.reduce((n, c) => n + c.tools.length, 0);
  // The builder prints the same count in its own header, directly above this
  // list, and two elements saying one number is how they come to disagree. The
  // panel has no such header, so it keeps the sentence.
  const summary = draft ? ""
    : `<p class="at-summary">${who} can use <b>${usable}</b> of ${total} tools.</p>`;

  boxEl.innerHTML = summary + cards.map((card) => {
    const why = toolBlockedReason(card);
    const rows = card.tools.map(({ row, on }) => {
      const name = (row && row.name) || "";
      const busy = _toolSaving.has(name);
      // A control that cannot work is not shown as a control: the row carries
      // the reason instead, and the place that fixes it.
      //
      // The always-on card draws none either, and that is the same rule rather
      // than a second one. Its heading says "not something to switch" — over a
      // column of switches, which is the screen arguing with itself, and the
      // switches were the half that was wrong: `tool_facts.ALWAYS` is granted
      // at creation and deliberately never offered as a toggle.
      const control = why
        ? `<span class="at-blocked">${esc(why)}</span>`
        : card.always
          ? ""
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
    const fix = why && card.connector && !draft
      ? `<button type="button" class="tiny at-fix" data-tool-fix="1">Open Connectors</button>` : "";
    // A connector that cannot answer contributes NO tools, and a disclosure
    // reading "Show all 0 tools" over a second copy of the reason already on
    // the heading is two useless lines each. Four unreachable connectors made
    // that the whole first screen. One line: the name and what to do.
    const detail = card.tools.length ? `
      <details class="at-detail" data-group="${esc(card.name)}"${
          _openGroups.has(card.name) ? " open" : ""}>
        <summary>${card.tools.length === 1 ? "Show the one tool"
          : `Show all ${card.tools.length} tools`}</summary>
        <div class="at-card">${rows}</div>
      </details>` : "";
    // A card whose grant is missing has live-looking switches over a refusal.
    // Dimming them is not cosmetic: it is the only thing on screen that says
    // the switch is not the answer.
    const gated = !isDraftAgent(agent) && _PANEL.grants && grantConnectorId(card)
      && !(_PANEL.grants.unrestricted
           || (_PANEL.grants.allowed || []).includes(grantConnectorId(card)));
    return `<section class="at-group${why ? " is-blocked" : ""}${
        card.tools.length ? "" : " is-empty"}${card.always ? " is-always" : ""}${
        gated ? " is-gated" : ""}">
      <div class="at-group-head">
        <h3 class="at-group-nm">${esc(card.name)}</h3>
        ${why ? `<span class="at-group-why">${esc(why)}</span>${fix}` : ""}
        ${cardMoreButton(card, draft)}
      </div>
      ${card.blurb ? `<p class="at-group-ds">${esc(card.blurb)}</p>` : ""}
      ${cardSwitches(card, why, draft)}
      ${cardStrip(card, ctx)}
      ${detail}
    </section>`;
  }).join("");
  wireAgentToolActions(boxEl, agent);
}

function wireAgentToolActions(boxEl, agent) {
  // Rebound after every render: the list is replaced wholesale, so a handler
  // on the previous nodes is a handler on nothing the user can click.
  boxEl.querySelectorAll("[data-tool-fix]").forEach((b) => {
    b.onclick = goElsewhere(() => openConnectorsScreen());
  });
  boxEl.querySelectorAll("[data-open-library]").forEach((b) => {
    b.onclick = goElsewhere(() => {
      if (typeof openLibrary === "function") openLibrary();
    });
  });
  boxEl.querySelectorAll("[data-tool]").forEach((b) => {
    b.onclick = () => toggleAgentTool(agent, b);
  });
  boxEl.querySelectorAll("[data-bulk]").forEach((b) => {
    b.onclick = () => toggleToolBucket(agent, b);
  });
  // Where the other half of a permission is set. The card named the screen;
  // `screenOpener` is what decides there is one to open.
  boxEl.querySelectorAll("[data-screen]").forEach((b) => {
    const open = screenOpener(b.dataset.screen);
    if (open) b.onclick = () => open();
  });
  boxEl.querySelectorAll("[data-folder-add]").forEach((b) => {
    b.onclick = () => openFolderToAgents(agent, b);
  });
  boxEl.querySelectorAll("[data-folder-off]").forEach((b) => {
    b.onclick = () => closeFolderToAgents(agent, b);
  });
  boxEl.querySelectorAll("[data-grant]").forEach((b) => {
    b.onclick = () => toggleConnectorGrant(agent, b);
  });
  boxEl.querySelectorAll("[data-reach-off]").forEach((b) => {
    b.onclick = () => askAgainBefore(b);
  });
  // Remember which disclosures are open. Every state that lands repaints the
  // panel, and without this it shuts every list the user had opened to decide
  // with.
  boxEl.querySelectorAll("[data-group]").forEach((d) => {
    d.ontoggle = () => {
      const key = d.dataset.group || "";
      if (!key) return;
      if (d.open) _openGroups.add(key); else _openGroups.delete(key);
    };
  });
}

//: Bring a card's switches with a change made inside it.
//:
//: The other direction was handled from the start — a tier switch patches the
//: rows under it — and this one was not, so turning one browser tool on inside
//: the disclosure left the switch above it reading a plain "off" over a tool
//: the agent could now use. Same rule, other way round: the screen must never
//: argue with itself.
//:
//: Patched in place rather than re-rendered, for the reason the tier switch
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

//: One switch, every tool in a tier. The whole point of the compaction.
//:
//: A separate function from `toggleAgentTool` rather than a loop over it,
//: because N tools must be **one** PATCH: looping would fire seven requests
//: that each send the whole list, and whichever replied last would win — so
//: turning a tier on could land as a tier half on, depending on the network.
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
    // The card's OTHER switches: a tier press changes whether the whole card is
    // on, and a stale switch beside a live one is the same lie in a different
    // place.
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
    // The switches ABOVE this row are about the card it is in, and one of them
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

// ── the other half of three permissions ────────────────────────────────────

//: Open a folder to agents. The grant IS the consent — there are no defaults.
//:
//: The server validates and answers with a sentence written for a person
//: ("Pick a folder inside your home directory rather than the whole of it"), so
//: that sentence is shown in the row rather than replaced with something of
//: ours about paths. A toast would be gone by the time somebody looked at the
//: box they typed in.
async function openFolderToAgents(agent, btn) {
  const input = document.querySelector("#atFolderPath");
  const err = document.querySelector("#atFolderErr");
  const path = ((input && input.value) || "").trim();
  if (err) { err.hidden = true; err.textContent = ""; }
  if (!path) { if (input && input.focus) input.focus(); return; }
  btn.disabled = true;
  try {
    const out = await api("/api/agents/folders", { method: "POST", body: { path } });
    _PANEL.folders = out.folders || _PANEL.folders || [];
    if (input) input.value = "";
    toast("Agents can work in that folder");
    redrawAgentTools();
  } catch (e) {
    if (err) { err.textContent = String(e); err.hidden = false; }
    btn.disabled = false;
  }
}

async function closeFolderToAgents(agent, btn) {
  const path = btn.dataset.folderOff || "";
  if (!path) return;
  btn.disabled = true;
  try {
    const out = await api(
      `/api/agents/folders?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    _PANEL.folders = out.folders || [];
    toast("Agents can no longer reach that folder");
    redrawAgentTools();
  } catch (e) {
    toast("Couldn't close that folder — try again.");
    btn.disabled = false;
  }
}

//: Whether this agent may reach this connector without asking first.
//:
//: Stored per `(agent, connector)` and keyed by the connector's ID, never its
//: label: a user can rename a connector, and a rename must not change who may
//: use what.
async function toggleConnectorGrant(agent, btn) {
  const connector = btn.dataset.grant || "";
  if (!agent || !agent.id || !connector) return;
  const wasOn = btn.dataset.on === "1";
  btn.disabled = true;
  const base = `/api/agents/${encodeURIComponent(agent.id)}/connectors`;
  try {
    if (wasOn) {
      await api(`${base}/${encodeURIComponent(connector)}`, { method: "DELETE" });
    } else {
      await api(base, { method: "POST", body: { connector, scope: "always" } });
    }
    const allowed = new Set((_PANEL.grants && _PANEL.grants.allowed) || []);
    if (wasOn) allowed.delete(connector); else allowed.add(connector);
    _PANEL.grants = { ..._PANEL.grants, allowed: [...allowed] };
    redrawAgentTools();
  } catch (e) {
    toast("Couldn't change that — try again.");
    btn.disabled = false;
  }
}

//: Take back one standing grant, from the card it belongs to.
//:
//: The only control a standing permission needs here. Making one is deliberately
//: not offered: a grant is given from the approval card that was asking, in the
//: moment somebody is reading what it would do — which is the whole reason
//: `permissions` keeps them off this screen.
async function askAgainBefore(btn) {
  const value = btn.dataset.reachOff || "";
  const kind = btn.dataset.reachKind || "";
  if (!value) return;
  btn.disabled = true;
  try {
    await api(`/api/agents/permissions/${encodeURIComponent(value)}`
              + (kind ? `?kind=${encodeURIComponent(kind)}` : ""),
              { method: "DELETE" });
    _PANEL.reach = (_PANEL.reach || []).filter(
      (g) => !(g.value === value && (g.kind || "") === kind));
    toast("That will be asked about again");
    redrawAgentTools();
  } catch (e) {
    toast("Couldn't change that — try again.");
    btn.disabled = false;
  }
}

// ── loading ────────────────────────────────────────────────────────────────

//: The last context the panel drew from, so a permission that changed outside
//: the tool list can repaint without re-fetching the catalog.
let _panelCtx = null;

function redrawAgentTools() {
  if (!_panelCtx || !_panelCtx.box) return;
  renderAgentTools(_panelCtx.box, _panelCtx);
}

//: The three permissions that are not tools, fetched beside the tool list.
//:
//: Never awaited into the render: `/api/agents/{id}/connectors` probes every
//: configured server to answer honestly, so blocking the panel on it would be
//: the 2.47s stall `api/concurrency.py` exists to stop. The panel draws, these
//: land, and the cards fill in.
async function loadPanelPermissions(agentId) {
  _PANEL = { forAgent: agentId, sites: null, folders: null, grants: null,
             reach: null };
  const settled = await Promise.allSettled([
    api("/api/browser/sites"),
    api("/api/agents/folders"),
    agentId ? api(`/api/agents/${encodeURIComponent(agentId)}/connectors`) : null,
    api("/api/agents/permissions"),
  ]);
  if (_PANEL.forAgent !== agentId) return;     // the user switched while we waited
  const [sites, folders, grants, reach] = settled;
  // `[]` and `null` mean different things to every strip: empty is a fact worth
  // stating ("no folder is open yet"), and a failed fetch is not something to
  // state at all.
  if (sites.status === "fulfilled") _PANEL.sites = sites.value.sites || [];
  if (folders.status === "fulfilled") _PANEL.folders = folders.value.folders || [];
  if (grants.status === "fulfilled" && grants.value) _PANEL.grants = grants.value;
  if (reach.status === "fulfilled") _PANEL.reach = reach.value.permissions || [];
  redrawAgentTools();
}

/**
 * @param boxEl where to draw. There is no settings panel to default to any
 *   more — this screen is the agent profile's Permissions tab — but the
 *   fallback stays for the agent builder and for anything still calling with
 *   one argument. Parameterised rather than copied: the cards, the switch
 *   wording and the blocked reasons are exactly what must not exist twice.
 *   It lands in `_panelCtx`, so `redrawAgentTools` follows it.
 */
async function loadAgentTools(agentId, boxEl) {
  const box = boxEl || $("#agentToolList"); if (!box) return;
  const id = agentId || AGENT_TOOLS_FOR || current;
  // Repainting a full panel with "Loading…" is a flash of nothing in the middle
  // of somebody reading it. The placeholder is for an empty panel, or for a
  // switch to a DIFFERENT agent — where leaving the old one's switches up would
  // be showing one agent's permissions under another agent's name.
  const same = AGENT_TOOLS_FOR === id && !!box.innerHTML.trim();
  AGENT_TOOLS_FOR = id;
  if (!same) box.innerHTML = `<div class="at-empty">Loading…</div>`;
  let tools = [], categories = [], specs = [], apps = [];
  try {
    const got = await api("/api/agents/tools");
    ({ tools, categories } = got);
    specs = got.groups || [];
    apps = got.apps || [];
  }
  catch (e) { box.innerHTML = `<div class="at-empty">Couldn't load the tool list.</div>`; return; }
  if (AGENT_TOOLS_FOR !== id) return;      // the user switched while we waited

  // **"Pick an agent." over a picker with no options.** Two different states
  // were collapsed into one dead end: a roster that is genuinely empty, which
  // is a first run and has somewhere to go, and an id that no longer resolves
  // — a deleted agent, or the panel opened before the roster landed — which
  // should fall back rather than refuse. Neither was something a person could
  // act on, and the picker beside it had nothing in it to pick.
  const roster = Array.isArray(agents) ? agents : [];
  if (!roster.length) {
    box.innerHTML = `<div class="at-empty">
      <p>No agents yet, so there is nothing to permit.</p>
      <p class="at-empty-next">Add one from the Agent Library and its switches
      appear here.</p>
      <button type="button" class="tiny" data-open-library="1">Browse the Agent Library</button>
    </div>`;
    wireAgentToolActions(box);
    return;
  }
  const agent = roster.find((a) => a.id === id) || roster[0];
  AGENT_TOOLS_FOR = agent.id;
  // The agent dropdown that used to be repainted here went with the settings
  // panel it sat on: this screen is reached from an agent now, so there is no
  // "which agent?" left to ask.

  _panelCtx = { box, agent, tools, connectors: CONNECTORS, categories, specs, apps };
  renderAgentTools(box, _panelCtx);
  loadPanelPermissions(agent.id);
  if (!CONNECTORS.length) {
    // /api/connectors starts every added server to answer honestly, so it is
    // far too slow to block a panel on. Render what we know, then sharpen.
    try {
      const { connectors } = await api("/api/connectors");
      CONNECTORS = connectors;
      if (AGENT_TOOLS_FOR === id) {
        _panelCtx = { ..._panelCtx, connectors };
        renderAgentTools(box, _panelCtx);
      }
    } catch (_) { /* the tools are on screen; health is a bonus */ }
  }
}

// `renderAgentToolPicker` lived here: a dropdown asking which agent the panel
// was about. It went with the panel. The agent profile is opened *from* an
// agent, so the question no longer exists — which is the point of moving it.

