/**
 * The conversation: sending a turn, and everything that renders one.
 *
 * Agent selection and the empty state, the message list and tool trace, action
 * cards, the thinking indicator, and the two ways a turn runs — streamed, or in
 * one piece.
 *
 * **Streaming reassembles SSE frames across chunk boundaries.** One network
 * chunk is not one frame. A reader that assumes it does passes every hand test
 * and drops tokens against a real model, so `streamTurn` buffers and splits on
 * the frame delimiter rather than on whatever arrives.
 *
 * **Action cards are rendered from text a stranger may have written.** The
 * model's reply can quote an email, so everything here goes through `esc()`
 * before any markup — `md()` in core.js escapes first for the same reason.
 * Confirming an action is a deliberate tap; the agent never sends one unasked.
 *
 * One turn at a time for the whole workspace (`busy`), with an AbortController
 * so the user can stop it. Anything the user starts, they can stop.
 */

//: What every card in this conversation already settled as, by key. Filled
//: before the history is drawn, so a card that was answered comes back answered
//: rather than offering its buttons again.
let CARD_STATE = {};

//: What this agent has actually done, newest first, from the action log.
//:
//: `CARD_STATE` only knows about cards answered since it existed, which on a
//: real machine was none of them: the table held nothing while the log held
//: every approval the user was looking at. A card confirmed before any of this
//: shipped has to recognise ITSELF, and the only thing a stored message and a
//: log row share is the parameters.
//:
//: Entries are claimed as they are matched — two identical proposals in one
//: conversation are two cards, and one action must not settle both.
let CARD_RAN = [];

//: How many cards with each key have been drawn so far in this render. Two
//: identical proposals in one conversation are told apart by their position,
//: which is the only thing that distinguishes them for a person reading it too.
let CARD_SEEN = {};

async function selectAgent(id) {
  if (busy) { toast("finishing current reply…"); return; }
  current = id;
  const a = agents.find((x) => x.id === id) || { name: "—", role: "", tools: [] };
  const oid = agentOrbId(a);
  $("#agentName").textContent = a.name;
  $("#agentRole").textContent = a.role;
  // The chat header is the one avatar on screen the whole time someone is
  // working, so it is live — it follows the pointer. The context card's is not:
  // it sits behind a panel that is usually closed, and a character nobody can
  // see should not be costing frames.
  const chOrb = $("#chOrb"); if (chOrb) paintAvatar(chOrb, oid, { live: true, title: a.name });
  const ctxOrb = $("#ctxOrb"); if (ctxOrb) paintAvatar(ctxOrb, oid, { size: 96, title: a.name });
  if ($("#ctxAgentName")) $("#ctxAgentName").textContent = a.name;
  if ($("#ctxAgentRole")) $("#ctxAgentRole").textContent = a.role;
  if ($("#ctxAgentDesc")) $("#ctxAgentDesc").textContent = agentDesc(a);
  $("#input").placeholder = "Message " + a.name + "…";
  document.querySelectorAll(".agent").forEach((el) => el.classList.toggle("active", el.dataset.id === id));
  loadAgents();   // refresh the active dot in the rail
  updateAgentModelChip(id);
  const chip = $("#agentModelChip");
  if (chip && !chip._wired) {
    chip._wired = 1;
    chip.onclick = () => { if (current) openAgentModelModal(current); };
  }
  // Which connectors this agent could be handed, for the `@` picker. Per
  // agent, because the labels are the same but who may use them is not.
  attachedConnectors = [];
  renderConnectorChips();
  loadConnectorNames();
  const { history } = await api(`/api/agents/${id}/history`);
  // Before the history, not after: a card reads its own state as it is built,
  // and fetching this second would draw every card as pending and then have to
  // repaint them.
  try {
    const answered = await api(`/api/agents/${id}/cards`);
    CARD_STATE = answered.cards || {};
    CARD_RAN = (answered.ran || []).map((e) => ({ ...e, claimed: false }));
  } catch (_) { CARD_STATE = {}; CARD_RAN = []; }
  renderHistory(history);
}

function heroEmpty() {
  const a = agents.find((x) => x.id === current) || { name: "your agent" };
  const hr = new Date().getHours();
  const greet = hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening";
  const div = document.createElement("div");
  div.className = "hero-empty";
  div.innerHTML = `
    <div class="he-top">
      <span class="orb orb-xl"></span>
      <div>
        <h1 class="he-hi">${greet}.</h1>
        <p class="he-sub">Your second brain, always on your side. Ask ${esc(a.name)} anything — it already knows your world.</p>
      </div>
    </div>
    <div class="he-cards">
      <button class="he-card" data-q="Catch me up — what's new since yesterday?"><span class="hc-ic">${IC.message}</span><b>Catch me up</b><span>What's new since yesterday?</span></button>
      <button class="he-card" data-q="What should I focus on today? Show my open tasks."><span class="hc-ic">${IC.tasks}</span><b>Show my tasks</b><span>What should I focus on today?</span></button>
      <button class="he-card" data-fill="Find "><span class="hc-ic">${IC.search}</span><b>Find something</b><span>Search across my apps &amp; notes.</span></button>
      <button class="he-card" data-q="Help me plan my day and week."><span class="hc-ic">${IC.spark}</span><b>Help me plan</b><span>Plan my day / week.</span></button>
    </div>`;
  // The greeting is the first thing on an empty conversation and the largest
  // the character is ever drawn, so this one is live too.
  paintAvatar(div.querySelector(".orb-xl"), agentOrbId(a), { live: true, title: a.name });
  div.querySelectorAll(".he-card").forEach((c) => c.onclick = () => {
    if (c.dataset.fill) { $("#input").value = c.dataset.fill; $("#input").focus(); autoGrow(); }
    else send(c.dataset.q);
  });
  maybeEnrichTip(div);
  return div;
}

// Gentle, dismissible nudge: if memories are waiting to be enriched, tell the user
// enrichment sharpens answers and let them start it in one click. Hidden once the
// queue is drained or the user dismisses it.
async function maybeEnrichTip(div) {
  if (localStorage.getItem("chitragupta_enrich_tip_off")) return;
  let cfg; try { cfg = await api("/api/brain/enrich/config"); } catch (_) { return; }
  const rem = cfg.remaining || 0;
  if (rem < 20) return;
  const tip = document.createElement("div");
  tip.className = "he-tip";
  tip.innerHTML = `<span class="het-ic">${IC.spark}</span>
    <span class="het-tx"><b>Sharpen your brain.</b> Enrich <b>${rem.toLocaleString()}</b> memories
    into people, projects &amp; facts for more precise answers — runs locally &amp; free.</span>
    <button class="het-go">Enrich</button><button class="het-x" title="Dismiss">${IC.close}</button>`;
  tip.querySelector(".het-go").onclick = () => { openBrainScreen(); setTimeout(() => { const b = $("#enrichBtn"); if (b && !b.classList.contains("running")) b.click(); }, 300); };
  tip.querySelector(".het-x").onclick = () => { localStorage.setItem("chitragupta_enrich_tip_off", "1"); tip.remove(); };
  div.appendChild(tip);
}

function renderHistory(history) {
  const box = $("#messages");
  box.innerHTML = "";              // also takes the boot skeleton with it
  box.removeAttribute("aria-busy");
  // A fresh count for a fresh render: the keys depend on how many cards with
  // the same shape have been drawn, and carrying the tally over would give the
  // same card a different key the second time the chat was opened.
  CARD_SEEN = {};
  // Same reason: a second render must be able to claim the same entries again,
  // or reopening a chat twice draws every card pending the second time.
  for (const entry of CARD_RAN) entry.claimed = false;
  const msgs = history.filter((m) => m.role === "user" || m.role === "assistant");
  if (!msgs.length) { box.appendChild(heroEmpty()); return; }
  for (const m of msgs) addMsg(m.role, m.content);   // so history shows action cards too
  box.scrollTop = box.scrollHeight;
}

/** Plans first, then whatever actions were left loose.
 *
 * A model may wrap several proposals in `<plan rationale="…">`. Seventeen
 * emails is seventeen decisions and one judgement, and seventeen cards ask a
 * person to make that judgement seventeen times — the second one is already
 * being made without reading.
 *
 * Plans are lifted out before `parseActions` runs, so the actions inside one
 * are claimed by their plan and never also rendered as loose cards. Mirrors
 * `actions.parse_plans` on the server, and like it, a plan with no usable
 * steps is dropped rather than shown as an empty card.
 */
function parsePlans(text) {
  const plans = [];
  const rest = String(text || "").replace(
    /<plan(\s+[^>]*?)?>([\s\S]*?)<\/plan>/gi, (m, attrs, inner) => {
      const { actions } = parseActions(inner);
      if (!actions.length) return "";
      let rationale = "";
      const found = /rationale="([^"]*)"/i.exec(attrs || "");
      if (found) rationale = found[1].trim();
      plans.push({ rationale, steps: actions });
      return "";
    });
  const { clean, actions } = parseActions(rest);
  return { clean, plans, actions };
}

function parseActions(text) {
  const actions = [];
  const clean = text.replace(/<action\s+([^>]*?)>([\s\S]*?)<\/action>/gi, (m, attrs, inner) => {
    const a = { params: {} };
    let mm; const re = /(\w+)="([^"]*)"/g;
    while ((mm = re.exec(attrs))) { if (mm[1] === "type") a.type = mm[2]; else a.params[mm[1]] = mm[2]; }
    if (a.type === "send_email") a.params.body = inner.trim();
    else if (a.type === "create_event") a.params.description = inner.trim();
    else if (a.type === "set_reminder") a.params.message = inner.trim();
    else if (a.type === "create_routine") a.params.instruction = inner.trim();
    else if (a.type === "message_send") a.params.text = inner.trim();
    else if (a.type === "mcp_action") {
      // A connector tool takes an object, and attributes are flat strings — so
      // the arguments are the body, as JSON. Mirrors `actions.parse_actions`.
      a.params.server_id = a.params.server || a.params.server_id || "";
      delete a.params.server;
      try {
        let body = inner.trim();
        if (body.startsWith("```")) body = body.replace(/^```[a-z]*\n?/i, "").replace(/```$/, "").trim();
        a.params.arguments = body ? JSON.parse(body) : {};
      } catch {
        // Malformed JSON is dropped, never guessed at — the same rule the
        // server applies, so the card and the execution agree about what
        // exists. Returning here leaves the tag stripped and no card shown.
        return "";
      }
    }
    else if (a.type === "log_workout") {
      // A session is a list of blocks, which does not fit in flat attributes.
      // Same body-as-JSON rule as mcp_action and mail_triage, parsed the same
      // way on both sides so the card and the execution agree about what exists.
      try {
        let body = inner.trim();
        if (body.startsWith("```")) body = body.replace(/^```[a-z]*\n?/i, "").replace(/```$/, "").trim();
        const parsed = body ? JSON.parse(body) : null;
        const blocks = Array.isArray(parsed) ? parsed : (parsed && parsed.blocks);
        if (!Array.isArray(blocks) || !blocks.length) return "";
        a.params.blocks = blocks;
      } catch { return ""; }
    }
    else if (a.type === "mail_triage") {
      // A list of emails does not fit in flat attributes either, so the body is
      // JSON — `{items: [...]}` or the bare list. Mirrors `actions._items`.
      try {
        let body = inner.trim();
        if (body.startsWith("```")) body = body.replace(/^```[a-z]*\n?/i, "").replace(/```$/, "").trim();
        const parsed = body ? JSON.parse(body) : null;
        const items = Array.isArray(parsed) ? parsed : (parsed && parsed.items);
        if (!Array.isArray(items) || !items.length) return "";
        a.params.items = items;
      } catch { return ""; }
    }
    if (a.type) actions.push(a);
    return "";   // strip the tag from the visible text
  });
  return { clean: clean.trim(), actions };
}

// ── attaching a connector to one message ─────────────────────────────────
// An agent must ask before it reaches a connector. `@` is the way to answer
// that question in advance: attach one to THIS message and the agent may use
// it for this turn only, without a card and without a standing grant.
//
// The chips are rendered before sending, deliberately. A permission the user
// cannot see at the moment they grant it is not a permission they granted.

let attachedConnectors = [];      // ids for this message
let CONNECTOR_LABELS = {};        // id → what a person reads
let connectorPickerIndex = -1;

async function loadConnectorNames() {
  if (!current) return;
  try {
    const d = await api(`/api/agents/${encodeURIComponent(current)}/connectors`);
    CONNECTOR_LABELS = d.labels || {};
  } catch (_) { CONNECTOR_LABELS = {}; }
}

function renderConnectorChips() {
  const box = $("#cmpConnectors");
  if (!box) return;
  box.hidden = attachedConnectors.length === 0;
  box.innerHTML = attachedConnectors.map((id) =>
    `<span class="cmp-chip">${esc(CONNECTOR_LABELS[id] || id)}` +
    `<button type="button" data-drop-connector="${esc(id)}" aria-label="Remove">${IC.close}</button></span>`).join("");
  box.querySelectorAll("[data-drop-connector]").forEach((b) => {
    b.onclick = () => {
      attachedConnectors = attachedConnectors.filter((x) => x !== b.dataset.dropConnector);
      renderConnectorChips();
    };
  });
}

/** What the user is part-way through typing after an `@`, or null. */
function connectorQuery(value, caret) {
  const upto = value.slice(0, caret);
  const at = upto.lastIndexOf("@");
  if (at < 0) return null;
  // Only when `@` starts a word — an email address must not open the picker.
  if (at > 0 && !/\s/.test(upto[at - 1])) return null;
  const typed = upto.slice(at + 1);
  if (/\s/.test(typed)) return null;
  return { at, typed: typed.toLowerCase() };
}

function closeConnectorPicker() {
  const p = $("#cmpPicker");
  if (p) { p.hidden = true; p.innerHTML = ""; }
  connectorPickerIndex = -1;
}

// Named for what it picks, not for what it is. `workspace.js` also has an
// `openPicker` — for folders — and it loads later, so the short name silently
// overwrote this one. Twelve scripts share one scope; a generic name in it is
// a collision waiting for the next file.
function openConnectorPicker(matches, q) {
  const p = $("#cmpPicker");
  if (!p) return;
  if (!matches.length) return closeConnectorPicker();
  connectorPickerIndex = 0;
  p.hidden = false;
  p.innerHTML = matches.map(([id, label], i) =>
    `<button type="button" role="option" class="cmp-opt${i === 0 ? " on" : ""}"
       data-pick="${esc(id)}">${esc(label)}<span>add to this message</span></button>`).join("");
  p.querySelectorAll("[data-pick]").forEach((b) => {
    b.onclick = () => chooseConnectorOption(b.dataset.pick, q);
  });
}

function chooseConnectorOption(id, q) {
  const input = $("#input");
  if (!attachedConnectors.includes(id)) attachedConnectors.push(id);
  // Take the half-typed @mention back out: the chip is the record now, and
  // leaving the text would send the agent a word it has to ignore.
  const value = input.value;
  input.value = value.slice(0, q.at) + value.slice(q.at + 1 + q.typed.length);
  closeConnectorPicker();
  renderConnectorChips();
  input.focus();
  autoGrow();
}

function updateConnectorPicker() {
  const input = $("#input");
  if (!input) return;
  const q = connectorQuery(input.value, input.selectionStart ?? input.value.length);
  if (!q) return closeConnectorPicker();
  const matches = Object.entries(CONNECTOR_LABELS)
    .filter(([id, label]) =>
      !attachedConnectors.includes(id) &&
      (id.includes(q.typed) || String(label).toLowerCase().includes(q.typed)))
    .slice(0, 6);
  openConnectorPicker(matches, q);
}

// ── a connector action, in words ─────────────────────────────────────────
// The card used to print the tool's id and every argument raw:
//
//     Action  notion-update-page
//     page_id 3bddf1be-bce9-80d7-a826-c2042f47f837
//     properties {}
//     content_updates []
//
// None of which tells a person what they are about to approve. These turn it
// into a sentence, and drop the arguments that carry no information — while
// keeping every argument that does, because a confirmation the user cannot
// actually read is not a confirmation.

/** "notion-update-page" + "Notion" → "Update page in Notion". */
//: The server's own description of every action — fields, risk tier, whether
//: it can be undone. Fetched once from `/api/actions/catalog`.
//:
//: This used to be `EDITABLE = { log_workout: true }`: a hand-kept list of
//: which cards the user was allowed to correct, and it said no to `send_email`.
//: The argument for opt-in was about *where a card is worth showing at all* —
//: which is a real question, and a different one. Once a card exists, the user
//: is being asked to approve what is on it, and a card you cannot fix a typo
//: in is a card that sends you back to the agent to re-ask for the same email
//: with one word changed.
//:
//: So every scalar field the registry declares is editable, and the registry is
//: the only place that is decided. Structured values (`items`, `blocks`,
//: `arguments`) are skipped by the generic editor and keep their own — a text
//: box containing JSON is not a correction anybody can make safely.
//:
//: What executes is what is on the card at the moment Confirm is pressed —
//: never what the model originally proposed. That is the point, and it is why
//: the fields are read at click time rather than copied back into `p`.
let ACTION_CATALOG = {};

async function loadActionCatalog() {
  try {
    ACTION_CATALOG = (await api("/api/actions/catalog")).actions || {};
  } catch {
    // A card still renders and still confirms without the catalog; it just
    // cannot offer the extras. Never a reason to fail the conversation.
    ACTION_CATALOG = {};
  }
}

//: Fields whose value is structured, or which the user must not retype. The
//: agent id is bookkeeping, not content.
const NOT_TYPEABLE = new Set(["items", "blocks", "arguments", "agent_id",
                              "loop_id", "thread_id",
                              // An opaque Google id. Nobody can correct one by
                              // reading it, and a typo here moves a different
                              // meeting — the failure the recipe forbids
                              // guessing for in the first place.
                              "event_id",
                              // A file path typed into a box is a path the
                              // folder grants have to re-check anyway, and a
                              // half-typed one is an attachment that silently
                              // vanishes. Changing what is attached means
                              // asking the agent.
                              "attach", "attachments"]);

//: A one-line input for a short field, a textarea for the long ones. The body
//: of an email is the field most worth fixing and the one least suited to a
//: 5em box.
const LONG_FIELDS = new Set(["body", "text", "description", "instruction", "note"]);

//: What kind of thing a card is, in one word.
//:
//: A conversation fills up with cards that all look alike, and a settled one
//: has lost its buttons — so there is *less* left to recognise it by, not more.
//: The chip is the fast way to tell an automation from an email while
//: scrolling. A word, never the action id: `create_routine` is ours.
//:
//: By family rather than per action, because "Email" is the useful answer for
//: both sending one and drafting one. Anything not named here falls back to
//: "Action", so a new action is plain rather than blank.
const CARD_KIND = {
  create_routine: "Automation",
  send_email: "Email", create_draft: "Email",
  mail_triage: "Inbox",
  create_event: "Calendar", update_event: "Calendar", cancel_event: "Calendar",
  set_reminder: "Reminder",
  create_task: "Task", create_followup: "Task",
  message_send: "Message",
  notify: "Notification",
  log_workout: "Training",
  drive_create_doc: "Document", drive_share: "Document",
  mcp_action: "Connector",
};

function cardKind(type) {
  return CARD_KIND[type] || "Action";
}

//: What the three tiers say on a card. The words are the user's, not the
//: enum's: "green" means nothing to a person, "this reaches nobody" does.
const RISK_NOTE = {
  green: "Reaches nobody — nothing leaves your machine.",
  amber: "This leaves your machine.",
  red: "",       // the action's own `always_ask_because` is more specific
};

/** Several actions, one judgement, one button.
 *
 * The card shows what the agent understood (`rationale`), every step it means
 * to take in the order it will take them, and the tier of the worst one — a
 * plan is as risky as its worst step, and nine green archives beside one amber
 * send is a card that sends an email.
 *
 * Steps are **not** individually editable here. A card that lets you correct
 * one of seventeen is a card you have to read seventeen times, which is the
 * thing this replaces; correcting one means asking the agent, and the single
 * action card is where correcting belongs.
 */
function planCard(plan) {
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  // Grouped by what each step does, because the same verb repeated eleven
  // times is one line to a person and eleven to a list.
  const counts = new Map();
  for (const s of steps) {
    const label = (ACTION_CATALOG[s.type] && ACTION_CATALOG[s.type].label)
      || String(s.type || "").replace(/_/g, " ");
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  const worst = steps.reduce((acc, s) => {
    const r = (ACTION_CATALOG[s.type] || {}).risk;
    if (r === "red" || acc === "red") return "red";
    if (r === "amber" || acc === "amber") return "amber";
    return acc || "green";
  }, "green");
  const note = steps
    .map((s) => (ACTION_CATALOG[s.type] || {}).always_ask_because)
    .find(Boolean) || RISK_NOTE[worst] || "";

  const listed = steps.slice(0, PLAN_NAMED_MAX).map((s) =>
    `<div class="pl-step">${esc(actionSummary(s))}</div>`).join("");
  const rest = steps.length - Math.min(steps.length, PLAN_NAMED_MAX);
  // The counts only earn their space when the list below is truncated. On a
  // plan short enough to show whole they repeat it — and worse, they repeat it
  // badly: "Inbox changes once" over a step that is fourteen emails.
  const rows = rest > 0 ? [...counts].map(([label, n]) =>
    `<div class="ac-row"><b>${esc(label)}</b> ${n === 1 ? "once" : `${n} times`}</div>`
  ).join("") : "";

  const key = planKey(plan);
  const el = document.createElement("div");
  el.className = "action-card";
  el.dataset.card = key;
  // Its own kind, not the steps'. A plan is the card whose one button does the
  // most, and that is the thing worth recognising at a glance.
  el.dataset.kind = "Plan";
  el.dataset.risk = worst;
  // The middle of the card, held as a fragment because the settled version
  // rebuilds around exactly the same body. Not wrapped in an element of its
  // own: an extra div here changes which margins collapse against which.
  const body = `
    ${plan.rationale ? `<div class="ac-row muted pl-why">${esc(plan.rationale)}</div>` : ""}
    ${rows}
    <div class="pl-steps">${listed}${
      rest > 0 ? `<div class="pl-step muted">and ${rest} more</div>` : ""}</div>`;
  el.innerHTML = `<div class="ac-head">${
      steps.length} action${steps.length === 1 ? "" : "s"} ready<span class="ac-kind">Plan</span><span class="ac-tag">needs your confirmation</span></div>
    ${body}
    ${note ? `<div class="ac-row muted ac-risk">${esc(note)}</div>` : ""}
    <div class="ac-actions"><button class="ac-confirm">Approve &amp; do ${
      steps.length === 1 ? "it" : "all"}</button>
    <button class="ac-cancel ghost">Cancel</button></div>
    <div class="ac-result"></div>`;

  // Already answered on a previous visit. This card ran every step at once, so
  // coming back with its button intact is not one duplicate — it is a second
  // copy of the whole plan.
  const settled = settledState(key, steps);
  if (settled) {
    return settledCard(el, settled, {
      title: `${steps.length} action${steps.length === 1 ? "" : "s"}`,
      body });
  }

  el.querySelector(".ac-cancel").onclick = () => {
    markAnswered(el, "cancelled");
    rememberCard(key, "cancelled");
  };
  el.querySelector(".ac-confirm").onclick = async () => {
    const btns = el.querySelector(".ac-actions");
    btns.innerHTML = "<span class='muted'>Working…</span>";
    const rr = el.querySelector(".ac-result");
    try {
      const r = await api("/api/actions/execute-plan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ steps, agent_id: current }) });
      // Every step is reported, including the ones that never started. "Six of
      // nine" does not tell anybody WHICH three did not happen, and a plan that
      // half-ran is exactly when a person needs to know.
      rr.innerHTML =
        `<div class="${r.ok ? "ac-ok" : "ac-err"}">${
          r.ok ? IC.check : IC.close} ${esc(r.detail || "")}</div>`
        + (r.steps || []).map((s) => {
            const ok = s.result && s.result.ok;
            return `<div class="pl-done" data-state="${ok ? "ok" : "err"}">${
              ok ? IC.check : IC.close} ${esc(s.summary || s.type)}${
              ok ? "" : ` — ${esc((s.result && s.result.error) || "failed")}`}</div>`;
          }).join("")
        + (r.skipped || []).map((s) =>
            `<div class="pl-done" data-state="skip">${esc(s.summary || s.type)} — not started</div>`
          ).join("");
      const note2 = (r.agent_note || "").trim();
      if (note2) rr.innerHTML += `<div class="ac-note">${md(note2)}</div>`;
      if ((r.undoable || []).length) rr.appendChild(undoAllButton(r.undoable));
      // Answered whichever way it went. A plan that half-ran is answered as
      // failed on purpose: the steps that did run must not be offered again.
      markAnswered(el, r.ok ? "done" : "failed");
      rememberCard(key, r.ok ? "done" : "failed",
                   { detail: r.detail || "" });
      loadReminders(); loadRoutines(); loadActionLog();
    } catch (e) {
      rr.innerHTML = `<span class="ac-err">${esc(resultLine(e) || "That did not go through.")}</span>`;
    }
  };
  return el;
}

//: Beyond this the card gives a count instead of a list nobody reads to the
//: end — the same limit and the same reason as the mail card's.
const PLAN_NAMED_MAX = 6;

/** "Archive 9 emails, Mark read 5 emails" — the decision, grouped by verb.
 *
 *  Lives here rather than inside the triage card because the plan card needs
 *  the same sentence, and "9 inbox changes" on the one card that is supposed
 *  to make the whole job readable is the version of this that fails. Mirrors
 *  `mail_triage.summarise` on the server; both exist because the card has to
 *  read the same as the log.
 *
 *  **Plain text, and every label in it was written by the model** — from a JSON
 *  body, where a quote and a `>` both survive. Escaped where it is rendered,
 *  never here; see `actionCard`.
 */
function triageSummary(items) {
  const counts = new Map();
  for (const it of (items || [])) {
    const name = MAIL_VERBS[it && it.do] || "Change";
    const key = it && it.label ? `${name} as “${it.label}”` : name;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const parts = [...counts].map(([k, n]) => `${k} ${n} email${n === 1 ? "" : "s"}`);
  return parts.join(", ") || "Change your inbox";
}

/** The files a message would carry, by name — never by path.
 *
 *  The path says where it is on disk, which the user already knows and which
 *  is long enough to push the subject off the card. The name is what they are
 *  checking: that it is the right document. Mirrors `approvals._attachment_names`.
 */
function attachmentNames(p) {
  let named = (p && (p.attach || p.attachments)) || [];
  if (typeof named === "string") named = named.split(",");
  const names = named.map((x) => String(x).trim().split("/").pop())
    .filter(Boolean);
  if (!names.length) return "";
  if (names.length <= 2) return names.join(" and ");
  return `${names[0]} and ${names.length - 1} more files`;
}

/** One plan step in the user's terms. Falls back to the type rather than
 *  showing raw params, which is a card asking to be trusted rather than read. */
function actionSummary(step) {
  const p = step.params || {};
  switch (step.type) {
    case "create_draft": return `Draft “${p.subject || "(no subject)"}” to ${p.to || "nobody yet"}`;
    case "send_email": return `Email “${p.subject || "(no subject)"}” to ${p.to || "someone"}`;
    case "create_event": return `Event “${p.title || "untitled"}” on ${p.start || "a date"}`;
    case "update_event": return p.start
      ? `Move a meeting to ${p.start}` : "Change a meeting";
    case "cancel_event": return "Cancel a meeting";
    case "set_reminder": return `Reminder: ${p.message || ""}`;
    case "message_send": return `Message ${p.chat || p.to || "someone"} on ${
      MESSAGING_APPS[(p.app || "").toLowerCase()] || p.app || "an app"}`;
    case "create_routine": return `Automation “${p.name || "untitled"}” — ${
      proposedWhen(p)}`;
    case "mail_triage": return triageSummary(p.items);
    case "mcp_action": return humanAction(p.tool, p.connector || p.server_id);
    default: return String(step.type || "an action").replace(/_/g, " ");
  }
}

/** Take back as much of a plan as can be taken back.
 *
 *  Labelled by how many, never "Undo" alone: some of a plan is undoable and
 *  some is not, and a button that says "Undo" over a sent email is promising
 *  something it cannot do.
 */
function undoAllButton(logIds) {
  const b = document.createElement("button");
  b.className = "tiny ac-undo";
  b.textContent = logIds.length === 1
    ? "Undo that" : `Undo ${logIds.length} of them`;
  b.onclick = async () => {
    b.disabled = true;
    const was = b.textContent;
    b.textContent = "Undoing…";
    try {
      const out = await api("/api/actions/undo", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ log_ids: logIds }) });
      if (out.ok) {
        b.replaceWith(Object.assign(document.createElement("span"),
          { className: "muted ac-undone", textContent: " · " + (out.detail || "Undone") }));
        loadReminders(); loadRoutines(); loadActionLog();
      } else {
        b.disabled = false; b.textContent = was;
        toast(out.detail || out.error || "None of that could be undone.");
      }
    } catch (e) {
      b.disabled = false; b.textContent = was;
      toast(resultLine(e) || "That could not be undone.");
    }
  };
  return b;
}

/** "3:42 PM" from an ISO stamp, or "" if it is not one. */
function clockTime(stamp) {
  if (!stamp) return "";
  const d = new Date(stamp);
  return Number.isNaN(d.getTime())
    ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** The Undo button an action earns by having a real inverse.
 *
 * Offered only when the server said `reversible`, which it says only when the
 * registry declares an `undo` for that action AND the action actually
 * succeeded. A button that quietly does nothing is worse than no button, so
 * this one is never rendered on a guess — a sent email has no undo and does
 * not pretend to.
 */
function undoButton(result) {
  const b = document.createElement("button");
  b.className = "tiny ac-undo";
  b.textContent = result.undo_label || "Undo";
  b.onclick = async () => {
    b.disabled = true;
    const was = b.textContent;
    b.textContent = "Undoing…";
    try {
      const out = await api("/api/actions/undo", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ log_id: result.log_id }) });
      if (out.ok) {
        b.replaceWith(Object.assign(document.createElement("span"),
          { className: "muted ac-undone", textContent: " · " + (out.detail || "Undone") }));
        loadReminders(); loadRoutines(); loadActionLog();
      } else {
        b.disabled = false; b.textContent = was;
        toast(out.error || "That could not be undone.");
      }
    } catch (e) {
      b.disabled = false; b.textContent = was;
      toast(resultLine(e) || "That could not be undone.");
    }
  };
  return b;
}

/** Does this field mean anything for the values in front of us?
 *
 * "Create automation" declares At, Days and Interval min, and a new-email
 * trigger reads none of them — so the card drew three empty boxes on the one
 * screen whose job is letting somebody check what is about to happen. An empty
 * box is not neutral; it reads as something they forgot to fill in.
 *
 * The rule comes from the registry, because the handler is what decides which
 * fields it reads and a copy kept here would drift from it. A field already
 * carrying a value is always shown: whatever it says, hiding something that
 * would be saved is worse than showing something that will not be. */
function relevantField(spec, name, p) {
  // Shown only when it carries something. The flag that turns sharing into
  // publishing has to be on the card when it is set and must not be an empty
  // box on every other share. See `ActionSpec.only_when_set`.
  if (Array.isArray(spec.only_when_set) && spec.only_when_set.includes(name)
      && !hasValue(p[name])) return false;
  const rule = spec.depends_on && spec.depends_on[name];
  if (!rule) return true;
  const [decider, values] = rule;
  if (p[name] !== undefined && p[name] !== null && p[name] !== "") return true;
  return (values || []).includes(String(p[decider] === undefined ? "" : p[decider]));
}

/** Real inputs for every scalar field the registry declares, in its order. */
function actionFields(type, p) {
  const spec = ACTION_CATALOG[type];
  if (!spec || !Array.isArray(spec.fields)) return null;
  const usable = spec.fields.filter(
    (f) => !NOT_TYPEABLE.has(f) && relevantField(spec, f, p)
      && (p[f] === undefined || p[f] === null
        || typeof p[f] === "string" || typeof p[f] === "number"));
  if (!usable.length) return null;

  const wrap = document.createElement("div");
  wrap.className = "ac-edit";
  const inputs = {};
  for (const name of usable) {
    const row = document.createElement("label");
    row.className = "ac-edit-row";
    const tag = document.createElement("span");
    tag.className = "ac-edit-label";
    tag.textContent = humanKey(name);
    const box = document.createElement(LONG_FIELDS.has(name) ? "textarea" : "input");
    box.className = "ac-field ac-field-wide";
    box.value = p[name] === null || p[name] === undefined ? "" : String(p[name]);
    if (box.tagName === "TEXTAREA") box.rows = Math.min(10, Math.max(3,
      String(box.value).split("\n").length + 1));
    box.setAttribute("aria-label", humanKey(name));
    inputs[name] = box;
    row.appendChild(tag); row.appendChild(box);
    wrap.appendChild(row);
  }
  //: The boxes themselves, so a caller can watch them without re-finding them
  //: by class — a selector in a handler is a claim about markup and it goes
  //: stale.
  wrap.boxes = Object.values(inputs);
  //: Read at click time, never copied back — an edit the user made and a
  //: confirm that ignored it would be the worst possible version of this.
  wrap.readFields = () => {
    const out = {};
    for (const [name, box] of Object.entries(inputs)) out[name] = box.value;
    return out;
  };
  return wrap;
}

//: One editable field. Kept as an element in a closure rather than found again
//: with a selector: a card that reads the DOM back to itself can be handed a
//: stale node, and a confirm that silently falls back to the proposed value is
//: exactly the bug this feature exists to prevent.
function field(value, { width = "5em", type = "text", label = "" } = {}) {
  const input = document.createElement("input");
  input.className = "ac-field";
  input.type = type;
  input.value = value === null || value === undefined ? "" : String(value);
  input.style.width = width;
  if (label) input.setAttribute("aria-label", label);
  return input;
}

/** Literal text between two fields. */
function sep(text) {
  const span = document.createElement("span");
  span.className = "ac-sep";
  span.textContent = text;
  return span;
}

/** The blocks of a workout card, as rows of inputs the user can correct. */
function workoutFields(blocks) {
  const wrap = document.createElement("div");
  wrap.className = "ac-edit";
  const rows = [];

  for (const b of blocks) {
    const row = document.createElement("div");
    row.className = "ac-row ac-editrow";
    const cells = {
      exercise: field(b.exercise, { width: "10em", label: "Exercise" }),
      sets: field(b.sets, { width: "3.5em", type: "number", label: "Sets" }),
      reps: field(b.reps, { width: "3.5em", type: "number", label: "Reps" }),
      weight: field(b.weight, { width: "5em", type: "number", label: "Weight in kg" }),
      rpe: field(b.rpe, { width: "3.5em", type: "number", label: "How hard, 1-10" }),
    };
    const drop = document.createElement("button");
    drop.className = "ac-drop ghost";
    drop.textContent = "✕";
    drop.setAttribute("aria-label", "Remove this exercise");

    const entry = { cells, row, dropped: false };
    drop.onclick = () => {
      entry.dropped = !entry.dropped;
      row.style.opacity = entry.dropped ? "0.4" : "1";
      for (const cell of Object.values(cells)) cell.disabled = entry.dropped;
    };

    // Separators are spans, not text nodes. Twenty hand-built fake DOMs stand
    // behind `tests/js/`, and every DOM API this file reaches for is one each
    // of them has to implement — so it reaches for as few as it can.
    row.appendChild(cells.exercise);
    row.appendChild(sep(" "));
    row.appendChild(cells.sets);
    row.appendChild(sep(" × "));
    row.appendChild(cells.reps);
    row.appendChild(sep(" @ "));
    row.appendChild(cells.weight);
    row.appendChild(sep(" kg   RPE "));
    row.appendChild(cells.rpe);
    row.appendChild(drop);
    wrap.appendChild(row);
    rows.push(entry);
  }

  const hint = document.createElement("div");
  hint.className = "ac-row muted";
  hint.textContent = "Correct anything that is wrong before you confirm. "
    + "Weight in kg; leave it 0 for bodyweight.";
  wrap.appendChild(hint);

  // Reads at call time, so what is stored is what is on screen right now.
  wrap.readBlocks = () => rows.filter((r) => !r.dropped).map((r) => ({
    exercise: r.cells.exercise.value.trim(),
    sets: Number(r.cells.sets.value),
    reps: Number(r.cells.reps.value),
    weight: Number(r.cells.weight.value),
    rpe: r.cells.rpe.value === "" ? null : Number(r.cells.rpe.value),
  }));
  return wrap;
}

//: App id -> the name a person reads. The server has the same mapping from the
//: connectors' own labels; these are the words on the card, and the ids are
//: what crosses the wire.
const MESSAGING_APPS = { telegram: "Telegram", slack: "Slack" };

//: What each triage verb is called on screen. The server has the same table in
//: `chitragupta/mail_triage.py`; these are the words a person reads, and the ids
//: that cross the wire are the keys — never the other way round.
const MAIL_VERBS = {
  archive: "Archive", mark_read: "Mark read", mark_unread: "Mark unread",
  star: "Star", unstar: "Unstar", label: "Label",
};
//: Beyond this the card gives a count instead of a list nobody reads to the end.
const MAIL_NAMED_MAX = 6;

/** "notion-update-page" + "Notion" → "Update page in Notion", as plain text.
 *
 *  Both arguments are model-written. This deliberately does no escaping: it
 *  returns words, and the card's head is where words become HTML. */
function humanAction(tool, connector) {
  // The tag may carry the connector's id rather than its label ("notion"), and
  // "Update page in notion" reads as a typo. Title-case a bare slug; leave a
  // real label ("Google Drive") exactly as its owner spelled it.
  const named = String(connector || "").trim();
  const where = /^[a-z0-9_-]+$/.test(named)
    ? named.replace(/[-_]+/g, " ").replace(/\b./g, (c) => c.toUpperCase())
    : named;
  let raw = String(tool || "").trim();
  if (!raw) return where ? `Run an action in ${where}` : "Run an action";
  // Strip a leading connector slug ("notion-", "linear_") only when it really
  // is the connector's name — a tool genuinely called "search" must not lose it.
  const head = raw.split(/[-_:.]/)[0].toLowerCase();
  if (where && head === where.toLowerCase().replace(/\s+/g, "")) {
    raw = raw.slice(head.length + 1);
  }
  const words = raw.replace(/([a-z0-9])([A-Z])/g, "$1 $2")
                   .replace(/[-_:.]+/g, " ").trim();
  if (!words) return where ? `Run an action in ${where}` : "Run an action";
  const sentence = words.charAt(0).toUpperCase() + words.slice(1);
  return where ? `${sentence} in ${where}` : sentence;
}

/** An argument name as a person would say it. */
function humanKey(k) {
  return String(k || "").replace(/[-_]+/g, " ")
    .replace(/\bids?\b/gi, (m) => (m.toLowerCase() === "id" ? "ID" : "IDs"))
    .replace(/^./, (c) => c.toUpperCase());
}

/** An argument value, or "" when it carries nothing worth showing. */
function humanValue(v) {
  if (v === true) return "yes";
  if (v === false) return "no";
  if (v === null || v === undefined || v === "") return "";
  if (Array.isArray(v)) return v.length ? `${v.length} item${v.length === 1 ? "" : "s"}` : "";
  if (typeof v === "object") {
    const keys = Object.keys(v);
    return keys.length ? keys.map(humanKey).join(", ") : "";
  }
  return String(v);
}

/** Whatever a connector answered with, as one readable line. */
function resultLine(detail) {
  if (detail === null || detail === undefined || detail === "") return "Done";
  if (typeof detail === "string") return detail;
  // An Error is an object whose `.name` is "Error", so without this it read as
  // a named result and rendered "Done — Error" — over the top of whatever the
  // server had actually explained. Every `catch (e) { resultLine(e) }` in the
  // app was hitting it, the action card's Confirm included.
  if (detail instanceof Error || (detail.message && detail.stack)) {
    return String(detail.message) || "That did not work.";
  }
  if (Array.isArray(detail)) return `Done — ${detail.length} item${detail.length === 1 ? "" : "s"}`;
  if (typeof detail === "object") {
    const named = detail.title || detail.name || detail.url || detail.id;
    return named ? `Done — ${named}` : "Done";
  }
  return String(detail);
}

/** The id this card will have on every render, including the next reload.
 *
 * Built from what the card *is* rather than from where it is stored, because a
 * message has no id the frontend can see — the history is role, content and a
 * timestamp. The action and its parameters are in the message text, so the same
 * message produces the same key every time it is drawn.
 */
function cardKey(a) {
  return keyed(`${a.type}|${shapeOf(a.params)}`);
}

/** The same, for a plan — which is the card that most needed it.
 *
 * A single action card offering to do something twice is one duplicate. A plan
 * card's button runs **every** step, so the same mistake is a duplicate of the
 * whole plan: two more automations, or two more emails.
 *
 * Keyed off the steps rather than the rationale, because the rationale is prose
 * the model rewrites and the steps are what the button actually runs.
 */
function planKey(plan) {
  const steps = (Array.isArray(plan.steps) ? plan.steps : [])
    .map((s) => `${s.type}|${shapeOf(s.params)}`).join(";");
  return keyed(`plan|${steps}`);
}

/** An action's parameters, flattened to something stable and comparable. */
function shapeOf(params) {
  const p = params || {};
  return Object.keys(p).sort()
    .map((k) => `${k}=${typeof p[k] === "object" ? JSON.stringify(p[k]) : p[k]}`)
    .join("&");
}

//: What each settled state is called on the card. The words are the user's:
//: "failed" is ours, and a person reading their own conversation wants to know
//: it did not work, not what the enum is called.
const SETTLED_WORD = { done: "done", cancelled: "cancelled",
                       failed: "didn\u2019t work" };

/** The fields this action cannot run without that the card has not got.
 *
 * `ActionSpec.required` is the handler's own list, published in the catalog —
 * so this is one rule for every action rather than a check about `agent`
 * bolted onto the automation branch. A message with no chat and a reminder
 * with no text are the same fault.
 */
/** The input elements of a generic editor, or none. */
function findFields(editor) {
  return Array.isArray(editor && editor.boxes) ? editor.boxes : [];
}

//: Is there anything in this field?
//:
//: `[]` and `{}` stringify to something non-empty, which would read as filled —
//: `items` and `blocks` are both lists, and an emptied workout card is exactly
//: the case that has to count as missing.
function hasValue(v) {
  if (v === undefined || v === null) return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v).length > 0;
  return String(v).trim() !== "";
}

function missingFields(type, values) {
  const need = (ACTION_CATALOG[type] || {}).required;
  if (!Array.isArray(need)) return [];
  // A nested list is "at least one of these" — `drive_share` needs an address
  // or the anyone-with-the-link flag, and `create_followup` takes what it is
  // about or who it is waiting on. A flat list could only ever be stricter than
  // the handler, which hides a button over something that would have worked.
  return need.filter((f) => (Array.isArray(f)
    ? !f.some((alt) => hasValue(values[alt]))
    : !hasValue(values[f])));
}

/** What to call a gap on the card — "Subject", or "Email or Anyone". */
function missingName(f) {
  return Array.isArray(f) ? f.map(humanKey).join(" or ") : humanKey(f);
}

/** Draw a card that has already been acted on: what it was, and what happened.
 *
 * **One renderer for every settled state and every kind of card.** `done` and
 * `cancelled` went through a settled path while `failed` was patched into the
 * pending one — so a failure kept the amber Confirm button, the whole editable
 * form and a tag reading "needs your confirmation", directly above the sentence
 * saying why it had already been attempted. Two paths meant the third state was
 * always going to drift from the other two.
 *
 * No buttons and no boxes, whatever happened. The boxes exist to correct a
 * proposal, and over a card that has already run they invite an edit that goes
 * nowhere; the button is the duplicate-action hazard this whole mechanism was
 * built to remove. Trying again means asking the agent, which is the one path
 * that produces a fresh proposal rather than replaying an old one.
 */
/** Mark a card answered *in this session*, so live and reloaded agree.
 *
 * A card is fully redrawn from `settledCard` on the next visit. Until then the
 * element on screen is the one the user is looking at, and it kept its original
 * tag: after a failed confirm it read "needs your confirmation" over the
 * sentence saying why it had already been attempted — the same contradiction
 * the reloaded card had, arriving by a different route.
 *
 * The buttons are already gone by this point (the confirm replaced them), so
 * this is the tag and the attribute the styling hangs off.
 */
function markAnswered(el, state) {
  el.dataset.settled = state;
  const tag = el.querySelector(".ac-tag");
  if (tag) tag.textContent = SETTLED_WORD[state] || state;
  // The slot the buttons were in. It still holds "Working…" from the confirm,
  // which is stale the moment the answer arrives. Owned entirely here so the
  // three handlers cannot each write something slightly different into it.
  const actions = el.querySelector(".ac-actions");
  if (actions) {
    actions.innerHTML = state === "cancelled"
      ? "<span class='muted'>Cancelled</span>" : "";
  }
}

function settledCard(el, settled, { chrome, title, body }) {
  const state = settled.state;
  el.dataset.settled = state;
  const said = esc(resultLine(settled.detail) || "");
  // `title` is TEXT and is escaped here; `chrome` and `body` are markup this
  // file built and are not. See the note on `actionCard`'s own head.
  el.innerHTML = `${chrome || ""}
    <div class="ac-head">${esc(title)}</div>
    <span class="ac-kind">${esc(el.dataset.kind || "")}</span>
    <span class="ac-tag">${SETTLED_WORD[state] || state}</span>
    ${body}
    <div class="ac-result">${
      state === "done"
        ? `<span class="ac-ok">${IC.check} ${said}`
          + (settled.verified_at
            ? `<span class="ac-verified"> \u00b7 confirmed ${
                esc(clockTime(settled.verified_at))}</span>` : "")
          + `</span>`
        : state === "failed"
          ? `<span class="ac-err">${IC.close} ${said || "It did not work"}</span>`
          : `<span class="muted">Cancelled</span>`}</div>`;
  return el;
}

/** What this card settled as, from either source — or null if it is still a
 *  question.
 *
 *  The recorded answer wins. It is exact, and it is the only place a
 *  **cancellation** can live: a card nobody ran leaves no trace in the log, so
 *  letting the log override this would bring back a card the user said no to,
 *  claiming it had run.
 */
function settledState(key, steps) {
  const recorded = CARD_STATE[key];
  if (recorded) return recorded;
  const matched = steps.map((step) => claimRan(step));
  // A plan is settled only when EVERY step is accounted for. Half a plan is
  // exactly when the user needs the button back, and drawing it done would
  // hide that a step never happened.
  if (matched.some((m) => !m)) {
    for (const m of matched) { if (m) m.claimed = false; }
    return null;
  }
  if (!matched.length) return null;
  const failed = matched.find((m) => !m.ok);
  const last = matched[matched.length - 1];
  return {
    state: failed ? "failed" : "done",
    detail: (failed || last).detail || "",
    verified_at: failed ? "" : (last.verified_at || ""),
    log_id: last.log_id || "",
    reversible: !!last.reversible,
  };
}

/** The oldest unclaimed thing this agent did that matches one step, claimed.
 *
 *  Containment, not equality: the confirm adds `agent_id` on the way through,
 *  so what was logged is always a superset of what the card proposed. Oldest
 *  first, because cards are drawn in the order they were proposed.
 *
 *  **Compared on the fields that NAME the action**, when the registry says
 *  which those are. The boxes on a card are editable on purpose — what runs is
 *  what is on the card when Confirm is pressed, never what was proposed — so a
 *  card whose agent box was corrected logged a run it could never match, and
 *  sat pending forever underneath the result of itself. An action that declares
 *  no identity is still judged on every field, which can only fail to settle a
 *  card rather than settle the wrong one.
 */
function claimRan(step) {
  const spec = ACTION_CATALOG[step.type] || {};
  const names = Array.isArray(spec.identity) ? spec.identity : [];
  const all = step.params || {};
  const want = names.length
    ? Object.fromEntries(names.filter((k) => all[k] !== undefined)
        .map((k) => [k, all[k]]))
    : all;
  // An identity naming nothing this card carries would match every action of
  // the type. Fall back to the strict comparison rather than guess.
  const compare = Object.keys(want).length ? want : all;
  for (let i = CARD_RAN.length - 1; i >= 0; i--) {
    const entry = CARD_RAN[i];
    if (entry.claimed || entry.type !== step.type) continue;
    const had = entry.params || {};
    const same = Object.keys(compare).every((k) =>
      String(had[k] === undefined ? "\u0000" : had[k]) === String(compare[k]));
    if (!same) continue;
    entry.claimed = true;
    return entry;
  }
  return null;
}

/** `base` plus how many cards of that shape have been drawn in this render. */
function keyed(base) {
  const n = (CARD_SEEN[base] = (CARD_SEEN[base] || 0) + 1);
  return `${base}#${n}`;
}

/** Tell the server what a card settled as, so a reload agrees with the screen.
 *
 * Failures are swallowed on purpose: the action already happened, and a card
 * that will look pending again tomorrow is a smaller problem than an error box
 * over something that worked. */
async function rememberCard(key, state, extra) {
  if (!key || !current) return;
  try {
    await api(`/api/agents/${current}/cards/${encodeURIComponent(key)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state, ...(extra || {}) }),
    });
  } catch (_) { /* see above */ }
}

/** When a *proposed* automation will run, in the words a person would use.
 *
 * This line used to be written inline and understood two triggers out of
 * three: `schedule` said "every 60 min" and everything else said "on every new
 * email". So a Sunday-morning automation was presented to the user as running
 * on every new email — the card describing something other than what its
 * button runs, which is the one thing a card may never do.
 *
 * `routineWhen` (workspace.js) already says this for a *stored* automation and
 * its comment already claimed the card used it. Now the card does, so there is
 * one set of words rather than two that drift. It is read at render time, long
 * after every script has loaded, which is what makes the backwards reference
 * safe — see docs/development/frontend-testing.md.
 */
function proposedWhen(p) {
  const at = String(p.at || p.at_time || "").trim();
  let trigger = String(p.trigger || "new_email");
  if (!["new_email", "schedule", "daily"].includes(trigger)) trigger = "new_email";
  // **The same rule the server applies** (`actions._create_routine`): a time of
  // day wins over whatever trigger the model reached for first. Models pick the
  // trigger they were shown first and then attach `at="8am"` to it, and a card
  // that reads the trigger literally promises an interval the server will not
  // create.
  if (at && trigger !== "new_email") trigger = "daily";
  return routineWhen({
    trigger, at_time: at, days: p.days || "",
    // Model-written: forced to a number rather than trusted.
    interval_min: Number.parseInt(p.interval_min, 10) || 60,
  });
}

/** What an action looks like to a person: its title, its rows, its tier.
 *
 * **One place, because two surfaces ask the same question.** This was the
 * inside of `actionCard` and nothing else could reach it, so the approvals
 * queue — the one place an *unattended* agent asks permission — had nothing to
 * draw with and showed a single summary line. The card you tap in your own
 * conversation showed the whole email; the card a routine raises after reading
 * a stranger's email showed its subject. The weaker surface was guarding the
 * riskier path, and the fix is not to give it a renderer of its own.
 *
 * `title` and `verb` are TEXT, `rows` is MARKUP, and `note` is the tier in the
 * user's words. See `actionCard` for why that division is load-bearing.
 *
 * Callers: `actionCard` here, and `loadApprovals` in `workspace.js` — read at
 * render time, long after every script has loaded, which is what makes the
 * cross-file call safe. Same shape as `proposedWhen` reaching `routineWhen` in
 * the other direction; see docs/development/frontend-testing.md.
 */
function actionFace(type, params) {
  const p = params || {};
  let title, rows, verb = "send";
  const at = p.at || p.when;
  if (type === "send_email" || type === "create_draft") {
    // One branch, because the two differ in a word and a verb. The word is the
    // whole difference and it has to be the loudest thing on the card: a draft
    // sits in the user's own folder, and an email has gone.
    const drafting = type === "create_draft";
    title = drafting ? "Save a draft" : "Send email";
    verb = drafting ? "save" : (at ? "schedule" : "send");
    const files = attachmentNames(p);
    rows = `<div class="ac-row"><b>To</b> ${esc(p.to || (drafting ? "(nobody yet)" : ""))}</div>
       ${p.cc ? `<div class="ac-row"><b>Cc</b> ${esc(p.cc)}</div>` : ""}
       <div class="ac-row"><b>Subject</b> ${esc(p.subject || "")}</div>
       ${p.thread_id ? `<div class="ac-row muted">Goes into the existing conversation.</div>` : ""}
       ${at && !drafting ? `<div class="ac-row"><b>Send at</b> ${esc(at)}</div>` : ""}
       ${files ? `<div class="ac-row"><b>Attached</b> ${esc(files)}</div>` : ""}
       <div class="ac-body">${esc(p.body || "")}</div>`;
  } else if (type === "update_event" || type === "cancel_event") {
    // What is CHANGING, never "an event was changed". This card emails every
    // attendee, so the thing being approved has to be readable as the thing
    // that will land in their inbox.
    const off = type === "cancel_event";
    title = off ? "Cancel this meeting" : "Move this meeting";
    verb = off ? "cancel" : "move";
    rows = (off
      ? `<div class="ac-row muted">It is called off and everybody in it is told.</div>`
      : `${p.start ? `<div class="ac-row"><b>New time</b> ${esc(p.start)}${
            p.end ? " → " + esc(p.end) : " (same length)"}</div>` : ""}
         ${p.title ? `<div class="ac-row"><b>New name</b> ${esc(p.title)}</div>` : ""}
         ${p.location ? `<div class="ac-row"><b>Where</b> ${esc(p.location)}</div>` : ""}
         ${p.attendees ? `<div class="ac-row"><b>Who</b> ${esc(String(p.attendees))}</div>` : ""}
         <div class="ac-row muted">Everybody in it gets the update.</div>`);
  } else if (type === "set_reminder") {
    title = "Set reminder"; verb = "set";
    rows = `<div class="ac-row"><b>Remind</b> ${esc(p.message || "")}</div>
       <div class="ac-row"><b>When</b> ${esc(p.at || p.when || "")}</div>`;
  } else if (type === "create_routine") {
    title = "Create automation"; verb = "create";
    rows = `<div class="ac-row"><b>Name</b> ${esc(p.name || "Automation")}</div>
       <div class="ac-row"><b>Runs</b> ${esc(proposedWhen(p))}${
         p.agent || p.agent_id ? ` · ${esc(p.agent || p.agent_id)}` : ""}</div>
       <div class="ac-body">${esc(p.instruction || "")}</div>`;
  } else if (type === "mcp_action") {
    // Previously this fell through to the calendar branch, so a connector
    // action would have been presented as "Create calendar event" — a card
    // describing something other than what the button runs.
    const where = p.connector || p.server_id || "a connector";
    title = humanAction(p.tool, where);
    verb = "run";
    const args = p.arguments && typeof p.arguments === "object" ? p.arguments : {};
    // Only the arguments that say something. `properties {}` and
    // `content_updates []` are the tool's own empty defaults, and printing them
    // asks the user to read noise before approving.
    const shown = Object.keys(args).map((k) => {
      const v = humanValue(args[k]);
      if (!v) return "";
      return `<div class="ac-row"><b>${esc(humanKey(k))}</b> ${esc(v.slice(0, 300))}</div>`;
    }).filter(Boolean).join("");
    rows = shown || `<div class="ac-row muted">No details to fill in.</div>`;
  } else if (type === "log_workout") {
    // Chat only: there is no training screen and there is not going to be one.
    // The user talks, this appears, they fix what is wrong, they confirm.
    const blocks = Array.isArray(p.blocks) ? p.blocks : [];
    const volume = blocks.reduce(
      (sum, b) => sum + (Number(b.sets) || 0) * (Number(b.reps) || 0) * (Number(b.weight) || 0), 0);
    title = blocks.length === 1 ? "Log this set" : `Log this session`;
    verb = "log";
    rows = volume
      ? `<div class="ac-row muted">${volume.toLocaleString()} kg of work, as it stands.</div>`
      : "";
  } else if (type === "message_send") {
    // The app is named, because it is half the decision — the same handle can
    // be two different people on two different apps.
    const where = MESSAGING_APPS[(p.app || "").toLowerCase()] || p.app || "a messaging app";
    // Not escaped here. The head escapes it, and escaping twice turns an app
    // called "AT&T" into "AT&amp;amp;T" on the card.
    title = `Send a message on ${where}`; verb = "send";
    rows = `<div class="ac-row"><b>To</b> ${esc(p.chat || p.to || "someone")}</div>
       <div class="ac-body">${esc(p.text || "")}</div>`;
  } else if (type === "mail_triage") {
    // Plain verbs and real subjects. The user is approving a change to their
    // own inbox, so the card has to read like one — never a message id, never
    // a Gmail label name, and never twelve separate cards for twelve emails.
    const items = Array.isArray(p.items) ? p.items : [];
    title = triageSummary(items);
    verb = "apply";
    const named = items.slice(0, MAIL_NAMED_MAX);
    rows = named.map((it) => {
      const what = MAIL_VERBS[it && it.do] || "Change";
      const suffix = it && it.label ? ` as “${it.label}”` : "";
      return `<div class="ac-row"><b>${esc(what + suffix)}</b> ${esc((it && it.subject) || "(no subject)")}</div>`;
    }).join("");
    const rest = items.length - named.length;
    rows = rows + (rest > 0 ? `<div class="ac-row muted">and ${rest} more</div>` : "")
      + `<div class="ac-row muted">Nothing is deleted — archiving takes an email out of your inbox and keeps it.</div>`;
  } else if (type === "create_event") {
    title = "Create calendar event"; verb = "create";
    rows = `<div class="ac-row"><b>Title</b> ${esc(p.title || "")}</div>
       <div class="ac-row"><b>When</b> ${esc(p.start || "")}${p.end ? " → " + esc(p.end) : ""}</div>
       ${p.description ? `<div class="ac-body">${esc(p.description)}</div>` : ""}`;
  } else {
    // **Anything this chain does not name renders from the REGISTRY.**
    //
    // This branch used to be `create_event`'s, with no condition on it — so
    // every action the chain had not been taught about was presented as
    // "Create calendar event". A card described something other than what its
    // button ran, which is the one thing a card may never do.
    //
    // It has happened twice. `mcp_action` hit it and was fixed by adding a
    // branch above; eight actions added later — the Notion, Linear and Drive
    // writes, and `create_task` — hit the same trap, and one of them showed a
    // user a Notion write titled "Create calendar event". A special case per
    // action is not a fix, it is a queue of the next occurrence.
    //
    // The registry already publishes a label and the fields for every action
    // and the frontend was ignoring both. So an unknown action now says its
    // own name and shows its own values, and the worst a future action can do
    // is look plain.
    title = ACTION_CATALOG[type] && ACTION_CATALOG[type].label
      ? ACTION_CATALOG[type].label : "Confirm this action";
    verb = "do it";
    const shown = (ACTION_CATALOG[type] && ACTION_CATALOG[type].fields) || [];
    rows = shown.filter((f) => p[f] !== undefined && p[f] !== "")
      .map((f) => {
        const value = typeof p[f] === "object"
          ? JSON.stringify(p[f]) : String(p[f]);
        // The long one reads as the body, the way every other card's does.
        return value.length > 80
          ? `<div class="ac-row"><b>${esc(humanKey(f))}</b></div>
             <div class="ac-body">${esc(value)}</div>`
          : `<div class="ac-row"><b>${esc(humanKey(f))}</b> ${esc(value)}</div>`;
      }).join("");
  }
  const spec = ACTION_CATALOG[type] || {};
  // The tier, in the user's words. A red action says why it always asks — that
  // sentence is per action and comes from the registry, because "this always
  // needs your approval" told about the wrong thing teaches nobody anything.
  const note = spec.always_ask_because || RISK_NOTE[spec.risk] || "";
  return { title, rows, verb, note,
           kind: cardKind(type), risk: spec.risk || "" };
}

/** One proposal, as a card the user can read, correct and confirm.
 *
 * **`title` and `verb` are TEXT; `rows` is MARKUP.** The head escapes the
 * first two and interpolates the third as it stands, so a branch in
 * `actionFace` writes plain words and never its own `esc()`.
 *
 * That division is the fix for a real hole rather than a tidiness rule. The
 * head used to interpolate `title` raw, which was safe only for as long as
 * every branch remembered — nine wrote constants, one pre-escaped, and the two
 * that assembled a title out of what the *model* wrote did not. So
 * `<action type="mcp_action" tool="<img src=x onerror=…>">` put a live tag in
 * the head of a card, in the origin that can POST `/api/actions/execute`; and
 * one card escaped the same string correctly in a row two lines lower. The
 * model is summarising a stranger's email, so "the model wrote it" is not a
 * reason to trust it — it is the reason not to.
 *
 * Escaping once, at the one place the value becomes HTML, is what makes the
 * next branch safe without its author having to know any of this.
 */
function actionCard(a) {
  const p = a.params;
  // Before the editors and the handlers, all of which close over it.
  const key = cardKey(a);
  // What this action reads as, from the one place that decides. The approvals
  // queue draws the same face from the same function.
  const { title, rows, verb, note, kind, risk } = actionFace(a.type, p);
  const el = document.createElement("div");
  el.className = "action-card";
  // The key it will have on every render, including the next reload.
  el.dataset.card = key;
  el.dataset.kind = kind;
  // The tier as an attribute, so the card can be styled and tested by what it
  // actually is rather than by reading its prose.
  el.dataset.risk = risk;
  // A window, not a notice. The chrome is decorative and says so to screen
  // readers — the three dots are the shape of "an application is asking you
  // something", and nothing is announced by them that the title does not say.
  el.innerHTML = `<div class="ac-chrome" aria-hidden="true">
      <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>
    </div>
    <div class="ac-head">${esc(title)}</div>
    <span class="ac-kind">${esc(kind)}</span>
    <span class="ac-tag">needs your confirmation</span>
    ${rows}
    ${note ? `<div class="ac-row muted ac-risk">${esc(note)}</div>` : ""}
    <div class="ac-row ac-missing" hidden></div>
    <div class="ac-actions"><button class="ac-confirm">Confirm & ${esc(verb)}</button>
    <button class="ac-cancel ghost">Cancel</button></div>
    <div class="ac-result"></div>`;
  // Already answered, on a previous visit — so the card is drawn as what it
  // became, not as a question. Before the editors, because a settled card has
  // nothing to edit: the values are history, and a box around a historical
  // value invites a correction that goes nowhere.
  const settled = settledState(key, [a]);
  if (settled) {
    return settledCard(el, settled, {
      chrome: `<div class="ac-chrome" aria-hidden="true">
        <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>
      </div>`,
      title, body: rows });
  }

  // Editable types grow real inputs. Held here, not looked up again later:
  // the values that execute are read off these elements at click time.
  //
  // Two editors, because two kinds of field. `workoutFields` understands a
  // list of blocks; `actionFields` covers every scalar the registry declares.
  // An action can have both — a session has blocks *and* a note.
  let editor = null;
  if (a.type === "log_workout") {
    editor = workoutFields(Array.isArray(p.blocks) ? p.blocks : []);
    el.appendChild(editor);
  }
  const fields = actionFields(a.type, p);
  if (fields) el.appendChild(fields);

  //: A card missing something the action cannot run without does not offer to
  //: act — an automation with an empty Agent box showed "Confirm & create"
  //: over a press that could only ever return "say which agent".
  //:
  //: It asks rather than blocks: the boxes are on the same card, so the button
  //: comes back the moment the gap is filled. Re-checked on every keystroke
  //: against what is on the card NOW, which is the same thing the confirm
  //: reads — never what was proposed.
  const watchGaps = () => {
    const now = fields && fields.readFields
      ? { ...p, ...fields.readFields() } : p;
    const gaps = missingFields(a.type, now);
    el.dataset.blocked = gaps.length ? "1" : "";
    const say = el.querySelector(".ac-missing");
    if (say) {
      say.textContent = gaps.length
        ? `Needs ${gaps.map(missingName).join(" and ")} before this can run.`
        : "";
      say.hidden = !gaps.length;
    }
    const go = el.querySelector(".ac-confirm");
    if (go) {
      go.disabled = gaps.length > 0;
      go.hidden = gaps.length > 0;
    }
  };
  if (fields) {
    for (const box of findFields(fields)) {
      box.addEventListener("input", watchGaps);
    }
  }
  watchGaps();

  el.querySelector(".ac-cancel").onclick = () => {
    markAnswered(el, "cancelled");
    rememberCard(key, "cancelled");
  };
  // What is on the card now, not what was proposed. An edit the user made and
  // a confirm that ignored it would be the worst possible version of this.
  const editedParams = () => {
    const base = { ...p, agent_id: current };
    if (fields && fields.readFields) Object.assign(base, fields.readFields());
    if (editor && editor.readBlocks) base.blocks = editor.readBlocks();
    return base;
  };
  el.querySelector(".ac-confirm").onclick = async () => {
    const btns = el.querySelector(".ac-actions"); btns.innerHTML = "<span class='muted'>Working…</span>";
    try {
      const r = await api("/api/actions/execute", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: a.type, params: editedParams() }) });
      const rr = el.querySelector(".ac-result");
      // The agent is told what its own proposal did, and when it failed it gets
      // one turn to answer for it. That answer is the useful part of a failed
      // card — it is where "Notion cannot delete pages, do it there" comes from.
      const note = (r.agent_note || "").trim();
      if (r.ok) {
        // Rung 5 on the card: "sent" is what we asked for, "confirmed 3:42 PM"
        // is what the service says happened. Only shown when it was really
        // checked — an unverified action says the plainer thing rather than
        // claiming a confirmation nobody made.
        const stamp = r.verified ? clockTime(r.verified_at) : "";
        rr.innerHTML = `<span class="ac-ok">${IC.check} ${esc(resultLine(r.detail))}`
          + (stamp ? `<span class="ac-verified"> · confirmed ${esc(stamp)}</span>` : "")
          + `</span>`;
        if (r.reversible && r.log_id) rr.appendChild(undoButton(r));
        markAnswered(el, "done");
        rememberCard(key, "done", {
          detail: r.detail || "", log_id: r.log_id || "",
          verified_at: r.verified ? (r.verified_at || "") : "",
          reversible: !!r.reversible });
        loadReminders(); loadRoutines(); loadActionLog(); return;
      }
      rr.innerHTML = `<span class="ac-err">${esc(r.error || "Failed")}</span>`
        + (note ? `<div class="ac-note">${md(note)}</div>` : "");
      // Answered, whichever way it went. It was attempted, so it never goes
      // back to being a proposal — trying again means asking the agent, which
      // produces a fresh card rather than replaying this one.
      markAnswered(el, "failed");
      rememberCard(key, "failed", { detail: r.error || "Failed" });
      if (r.reauth) {
        const b = document.createElement("button");
        b.className = "tiny"; b.textContent = "Reconnect Google"; b.style.marginTop = "8px";
        b.onclick = async () => { const x = await api("/api/google/reconnect", { method: "POST" }); toast(x.detail || "Opening browser…"); };
        rr.appendChild(document.createElement("br")); rr.appendChild(b);
      }
    } catch (e) {
      // A rejected request carries FastAPI's `detail`, which for a validation
      // error is a LIST of objects — so this must not assume a string either.
      el.querySelector(".ac-result").innerHTML =
        `<span class="ac-err">${esc(resultLine(e) || "That did not go through.")}</span>`;
    }
  };
  return el;
}

// ── image attachments ──────────────────────────────────────────────────────
// #attachBtn had an icon and no click handler — a control that could not work,
// which is the one thing the product rules here are most explicit about. It
// picks files now, and the same three checks guard every way in: the button,
// a paste, and a drop.
//
// The vision check happens HERE, before the request, because we already know
// the answer: the catalog says whether the chosen model can see. Sending it
// anyway would bill the user for a failure we predicted. The server repeats
// the check — the client is a convenience, never the guard.
const IMG_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const IMG_MAX_BYTES = 5 * 1024 * 1024;     // decoded; the wire cap is larger
const IMG_MAX = 4;
let attachments = [];                       // {name, dataUrl, size}
let sentImages = [];                        // the set belonging to the turn in flight

function modelSeesImages() {
  // The agent's binding, for the same reason the turn uses it: this decides
  // whether to refuse an image BEFORE spending, and refusing against a stale
  // localStorage pair means refusing on behalf of a model that is not the one
  // about to answer.
  const b = (typeof AGENT_MODEL_BINDING !== "undefined" && AGENT_MODEL_BINDING) || null;
  const pid = (b && (b.configured_provider || b.provider)) || "";
  const mid = (b && b.configured_model) || "";
  const prov = (MODEL_CATALOG || []).find((p) => p.id === pid);
  if (!prov) return { ok: true };           // unknown is not "no"
  // No model chosen means Auto, which resolves to the best one available —
  // refusing there would refuse a model that can probably see.
  if (!mid) return { ok: true };
  const m = (prov.models || []).find((x) => (x.id || x.name) === mid);
  if (!m) return { ok: true };
  if (m.vision) return { ok: true };
  const alts = (prov.models || []).filter((x) => x.vision && !x.locked)
    .slice(0, 3).map((x) => x.name || x.id);
  return {
    ok: false,
    why: `${m.name || mid} can't read images.` +
         (alts.length ? ` Try ${alts.join(", ")}.`
                      : " Pick a model marked vision in Model settings."),
  };
}

function renderAttachments() {
  const box = $("#cmpAttachments");
  if (!box) return;
  box.hidden = attachments.length === 0;
  box.innerHTML = attachments.map((a, i) => `
    <div class="cmp-att" title="${esc(a.name || "image")}">
      <img src="${a.dataUrl}" alt="${esc(a.name || "attached image")}" />
      <button type="button" class="cmp-att-x" data-i="${i}" aria-label="Remove ${esc(a.name || "image")}">${IC.close}</button>
    </div>`).join("");
  box.querySelectorAll(".cmp-att-x").forEach((b) => {
    b.onclick = () => { attachments.splice(+b.dataset.i, 1); renderAttachments(); };
  });
}

function addImageFiles(files) {
  const list = Array.from(files || []).filter((f) => f && f.type.startsWith("image/"));
  if (!list.length) return;

  const seeing = modelSeesImages();
  if (!seeing.ok) { toast(seeing.why); return; }

  for (const f of list) {
    if (attachments.length >= IMG_MAX) { toast(`Up to ${IMG_MAX} images at a time.`); break; }
    if (!IMG_TYPES.includes(f.type)) {
      toast(`${(f.type.split("/")[1] || f.type).toUpperCase()} isn't supported — use PNG, JPEG, GIF or WebP.`);
      continue;
    }
    if (f.size > IMG_MAX_BYTES) {
      toast(`"${f.name || "That image"}" is too large — images need to be under ${IMG_MAX_BYTES / (1024 * 1024)}MB.`);
      continue;
    }
    const reader = new FileReader();
    reader.onload = () => {
      attachments.push({ name: f.name || "", dataUrl: String(reader.result), size: f.size });
      renderAttachments();
    };
    reader.onerror = () => toast(`Couldn't read "${f.name || "that image"}".`);
    reader.readAsDataURL(f);
  }
}

{
  const btn = $("#attachBtn"), file = $("#cmpFile");
  if (btn && file) {
    btn.onclick = () => file.click();
    file.onchange = () => { addImageFiles(file.files); file.value = ""; };
  }
  // Paste: a screenshot in the clipboard is the most common way an image gets
  // into a chat, and it arrives as a file on the paste event, not as text.
  const input = $("#input");
  if (input) input.addEventListener("paste", (e) => {
    const items = (e.clipboardData && e.clipboardData.files) || [];
    if (items.length) { e.preventDefault(); addImageFiles(items); }
  });
  // Drop anywhere on the conversation, not just on the button.
  const zone = document.querySelector(".chat");
  if (zone) {
    const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
    ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => {
      if (!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files"))) return;
      stop(e); zone.classList.add("drop-target");
    }));
    ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => {
      if (t === "drop") { stop(e); addImageFiles(e.dataTransfer.files); }
      zone.classList.remove("drop-target");
    }));
  }
}

//: A plan limit, and when it lifts. The provider says "resets 12am
//: (Asia/Calcutta)", which is wrong for a reader in another timezone and wrong
//: for anyone reading it tomorrow — so the backend resolves it to an instant
//: and the card counts down to that.
function parseLimit(text) {
  const m = /<limit\s+until="([^"]*)">([\s\S]*?)<\/limit>/i.exec(text || "");
  if (!m) return null;
  const until = Date.parse(m[1]);
  return { until: Number.isNaN(until) ? 0 : until, message: m[2].trim(),
           rest: (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim() };
}

function untilLabel(ms) {
  if (ms <= 0) return "";
  const s = Math.ceil(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h >= 1) return `${h}h ${m}m`;
  if (m >= 1) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

//: The card the transcript shows instead of the provider's sentence. It ticks,
//: because a static time asks the reader to do the arithmetic, and it arms its
//: own retry rather than leaving them to type "retry" into the chat — which is
//: what the last version taught one user to do.
function limitCard(limit) {
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `<div class="limit-card">
      <div class="limit-head">
        <span class="limit-ic">${IC.clock}</span>
        <span class="limit-msg">${esc(limit.message)}</span>
      </div>
      <div class="limit-foot">
        <span class="limit-countdown"></span>
        <button type="button" class="limit-retry" disabled>Try again</button>
      </div>
    </div>`;
  const out = el.querySelector(".limit-countdown");
  const btn = el.querySelector(".limit-retry");

  const tick = () => {
    const left = limit.until - Date.now();
    if (!limit.until || left <= 0) {
      out.textContent = "You can try again now.";
      btn.disabled = false;
      clearInterval(iv);
      return;
    }
    out.textContent = `Available again in ${untilLabel(left)}`;
    btn.disabled = true;
  };
  // Every second under a minute, every ten after — a card that repaints once a
  // second for three hours is a card nobody asked to animate.
  const iv = setInterval(tick, (limit.until - Date.now()) > 60000 ? 10000 : 1000);
  tick();
  btn.onclick = () => { if (!btn.disabled) resend(); };
  return el;
}

//: Send the last thing the user asked, again. The limit card's retry is the
//: only caller: re-typing a question you already asked is not a retry.
function resend() {
  const asks = [...document.querySelectorAll("#messages .msg.user")];
  const last = asks[asks.length - 1];
  const text = ((last && (last.dataset.raw ?? last.textContent)) || "").trim();
  if (text) send(text);
}

function addMsg(role, text, images) {
  const box = $("#messages");
  const he = box.querySelector(".hero-empty"); if (he) he.remove();
  if (role === "assistant") {
    const limit = parseLimit(text);
    if (limit) {
      box.appendChild(limitCard(limit));
      box.scrollTop = box.scrollHeight;
      return;
    }
    const { clean, plans, actions } = parsePlans(text);
    const el = document.createElement("div");
    el.className = "msg assistant";
    // No avatar on each turn. Which agent is answering is already said by the
    // chat header and the rail; repeating it beside every message spent a
    // 34px column on it and pushed the prose off the column's left edge.
    // The MARKDOWN is what gets copied, not the rendered text: pasting a
    // reply into a note or an issue should keep its lists and its code, and
    // innerText would flatten all of it.
    el.innerHTML = `<div class="a-body">${md(clean)}</div>
      <div class="msg-tools"><button type="button" class="msg-copy" title="Copy reply"
        aria-label="Copy reply">${IC.copy}<span>Copy</span></button></div>`;
    const copyBtn = el.querySelector(".msg-copy");
    if (copyBtn) copyBtn.onclick = async () => {
      const ok = await copyToClipboard(clean);
      if (!ok) { toast("Could not copy that"); return; }
      // Confirm on the button itself. A toast says "something happened";
      // the button saying it says "this is the thing that happened".
      copyBtn.innerHTML = `${IC.tick}<span>Copied</span>`;
      copyBtn.classList.add("is-done");
      setTimeout(() => {
        copyBtn.innerHTML = `${IC.copy}<span>Copy</span>`;
        copyBtn.classList.remove("is-done");
      }, 1400);
    };
    box.appendChild(el);
    // Plans first: they are the agent's answer to what was asked, and a loose
    // action beside one is usually an afterthought.
    for (const p of plans) box.appendChild(planCard(p));
    for (const a of actions) box.appendChild(actionCard(a));
    box.scrollTop = 1e9; return el;
  }
  const el = document.createElement("div");
  el.className = "msg " + role;
  // The question, verbatim. `textContent` would also pick up an attachment's
  // alt text, and the limit card's retry has to resend what was asked.
  if (role === "user") el.dataset.raw = text || "";
  if (images && images.length) {
    // textContent for the words, built nodes for the pictures: the message is
    // user input, so it must never be interpolated into innerHTML.
    const strip = document.createElement("div");
    strip.className = "msg-images";
    for (const a of images) {
      const im = document.createElement("img");
      im.src = a.dataUrl; im.alt = a.name || "attached image";
      strip.appendChild(im);
    }
    el.appendChild(strip);
    if (text) {
      const t = document.createElement("div");
      t.textContent = text;
      el.appendChild(t);
    }
  } else {
    el.textContent = text;
  }
  box.appendChild(el); box.scrollTop = 1e9; return el;
}
//: A session that lapsed, pulled out of the tool results.
//:
//: `browser/session.py` writes this sentence when a granted site lands on its
//: own login page, and it is the ONE place the wording lives — matched here
//: rather than re-composed, so the two cannot drift into saying different
//: things. No match renders nothing, so a change upstream costs a card, never
//: a broken screen.
//:
//: It must not go in the fold. The trace is collapsed by default and this is
//: not trace detail — it is the user's account having logged itself out, and
//: an answer that says "I could not find anything" reads as the app being
//: broken until somebody tells them otherwise.
function lapsedSites(steps) {
  const out = [];
  for (const s of steps || []) {
    if (s.kind !== "tool_result") continue;
    // Non-greedy up to the sentence's full stop, not to the first dot — a host
    // HAS dots in it, and `[^\s.]+` turned linkedin.com into "linkedin".
    const m = /You are signed out of (\S+?)\.(?:\s|$)/.exec(String(s.result || ""));
    if (m && !out.includes(m[1])) out.push(m[1]);
  }
  return out;
}

function reconnectCard(host) {
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `<div class="signout-card">
      <div class="signout-head">
        <span class="signout-ic">${IC.lock}</span>
        <span class="signout-msg">You are signed out of <b>${esc(host)}</b>.
          Agents can still reach it — the permission is fine, the login ended.</span>
      </div>
      <button type="button" class="signout-go">Reconnect ${esc(host)}</button>
    </div>`;
  el.querySelector(".signout-go").onclick = () => {
    // Straight to the control that fixes it, with the address already filled
    // in. "Go to Connectors and find it" is a instruction, not a fix.
    if (typeof openConnectorsScreen === "function") openConnectorsScreen();
    const input = $("#webConnectInput");
    if (input) {
      input.value = host;
      if (typeof renderConnectRisk === "function") renderConnectRisk();
      input.focus();
      input.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };
  return el;
}

function addTrace(steps) {
  // Before the fold, and whether or not there is a trace worth folding.
  for (const host of lapsedSites(steps)) {
    $("#messages").appendChild(reconnectCard(host));
  }
  if (!steps.length) return;
  const calls = steps.filter((s) => s.kind === "tool_call");
  if (!calls.length) return;
  // Folded away by default. What an agent actually ran is worth being able to
  // check — it is the difference between trusting the answer and taking it on
  // faith — but it is not the answer, and a screenful of raw tool arguments
  // between two replies buries the thing the user came for.
  //
  // <details> rather than a button and a class: it is open/closed state the
  // browser already owns, it is keyboard-operable for free, and it cannot get
  // out of step with a re-render the way a toggle flag can.
  const el = document.createElement("details");
  el.className = "trace";
  // Name the tools in the summary, so the fold still says what happened.
  const names = [...new Set(calls.map((c) => c.name))];
  const shown = names.slice(0, 3).join(", ") + (names.length > 3 ? `, +${names.length - 3} more` : "");
  el.innerHTML = `<summary class="trace-sum">
      <span class="trace-chev" aria-hidden="true"></span>
      <span>Show thinking</span>
      <span class="trace-n">${calls.length} step${calls.length === 1 ? "" : "s"} · ${esc(shown)}</span>
    </summary>
    <div class="trace-body">` + calls.map((s) => {
    const res = (steps.find((r) => r.kind === "tool_result" && r.name === s.name) || {}).result || "";
    return `<div class="step"><span class="tname">${esc(s.name)}</span>(${esc(JSON.stringify(s.arguments))})<span class="res">${esc(res.slice(0, 160))}</span></div>`;
  }).join("") + `</div>`;
  $("#messages").appendChild(el); $("#messages").scrollTop = 1e9;
}

let busy = false;               // one turn at a time per the whole workspace
let controller = null;          // AbortController for the in-flight turn
let turnId = null;              // the name this turn answers to, for Stop

// Stop the running turn. The server is told first and the fetch is left alone,
// because the turn keeps whatever it had already written and sends it back —
// aborting here would throw that away and, worse, leave the model calls running
// on the user's own key with the screen saying the work had ended.
async function stopTurn() {
  if (!turnId) { if (controller) controller.abort(); return; }
  const id = turnId;
  try {
    await api(`/api/agents/turns/${encodeURIComponent(id)}/stop`, { method: "POST" });
  } catch (_) {
    if (controller) controller.abort();   // could not reach it; end it locally
  }
}

function setBusy(on) {
  busy = on;
  $("#input").disabled = on;
  const b = $("#send");
  // Swap the ICON. This wrote textContent, which did two things at once: it
  // crammed the word "Stop" into a 34px circle, and — because textContent
  // replaces the element's children — it destroyed the arrow SVG that
  // applyIcons had put there, so the send button was the word "Send" for the
  // rest of the session. The label the assistive tech reads is set alongside,
  // since a glyph on its own says nothing to a screen reader.
  b.innerHTML = IC[on ? "stop" : "arrowUp"] || "";
  b.title = on ? "Stop" : "Send";
  b.setAttribute("aria-label", on ? "Stop generating" : "Send message");
  b.classList.toggle("stopbtn", on);
  if (!on) { $("#input").focus(); autoGrow(); }
}

// Phrases + rough per-provider time estimates for the reply indicator.
const THINK_PHRASES = [
  "Recalling what I know about you…",
  "Searching your brain…",
  "Pulling in the right context…",
  "Connecting the dots…",
  "Composing a reply…",
];
const THINK_EST = { "claude-code": 18, ollama: 12, subscription: 15,
  anthropic: 8, openai: 8, openrouter: 9, mock: 1 };

function makeThinking(provider) {
  const est = THINK_EST[provider] || 12;
  const el = addMsg("assistant", "");
  el.classList.add("thinking");
  el.innerHTML =
    `<div class="think-row"><span class="think-dot"></span>
       <span class="think-msg">Thinking…</span><span class="think-time"></span></div>
     <div class="think-bar"><div class="think-fill"></div></div>`;
  const msgEl = el.querySelector(".think-msg");
  const timeEl = el.querySelector(".think-time");
  const fill = el.querySelector(".think-fill");
  const t0 = performance.now();
  let pi = -1;
  const tick = () => {
    const elapsed = (performance.now() - t0) / 1000;
    // asymptotic progress: approaches ~97% but never completes until the reply lands
    fill.style.width = Math.min(97, 100 * (1 - Math.exp(-elapsed / est))).toFixed(1) + "%";
    const remain = est - elapsed;
    timeEl.textContent = remain > 0.5 ? `~${Math.ceil(remain)}s` : "almost there…";
    const want = Math.min(THINK_PHRASES.length - 1, Math.floor(elapsed / 2.5));
    if (want !== pi) { pi = want; msgEl.textContent = THINK_PHRASES[pi]; }
  };
  tick();
  let timer = setInterval(tick, 150);
  let body = null;
  // Once anything real arrives, stop guessing. The phrases and the countdown
  // exist only to fill silence, and there is no longer any silence to fill.
  const stopGuessing = () => {
    if (timer) { clearInterval(timer); timer = null; }
    if (fill) fill.style.width = "100%";
    if (timeEl) timeEl.textContent = "";
  };
  return {
    el,
    note: (label) => { stopGuessing(); if (msgEl) msgEl.textContent = label; },
    preview: (textSoFar) => {
      stopGuessing();
      if (!body) {
        body = document.createElement("div");
        body.className = "think-preview";
        el.appendChild(body);
      }
      body.textContent = textSoFar;
      const wrap = $("#messages");
      if (wrap) wrap.scrollTop = wrap.scrollHeight;
    },
    done: () => { if (timer) clearInterval(timer); el.remove(); },
  };
}

async function send(text) {
  if (busy) return;             // guard: ignore sends while a turn is running
  setBusy(true);
  controller = new AbortController();
  // Named before the request leaves, so Stop works from the first frame the
  // button is visible rather than from whenever the server gets around to us.
  turnId = (crypto.randomUUID && crypto.randomUUID())
    || `t-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  // Detach the attachments the moment the turn starts: the user can type the
  // next message while this one runs, and anything still in the tray then
  // belongs to THAT message, not this one.
  sentImages = attachments;
  attachments = [];
  renderAttachments();
  addMsg("user", text, sentImages);
  const curAgent = agents.find((x) => x.id === current);
  const thinkProv = (curAgent && curAgent.model_provider) || $("#provider").value;
  const think = makeThinking(thinkProv);
  try {
    const res = await streamTurn(text, think);
    think.done();
    addTrace(res.trace || []);
    addMsg("assistant", res.reply);
    loadBrain();
    loadTasks();       // an agent may have added/completed a task this turn
    loadReminders();   // …or set a reminder
  } catch (e) {
    think.done();
    if (controller && controller.signal.aborted) addMsg("assistant", "Stopped.");
    else addMsg("assistant", String(e));
  }
  finally {
    controller = null; turnId = null;
    // The grant belonged to the message that has now been sent.
    attachedConnectors = [];
    renderConnectorChips();
    setBusy(false);
  }
}

// Run one turn over Server-Sent Events, showing the reply as it is written and
// naming each tool as it runs. Falls back to the plain endpoint if streaming is
// unavailable for any reason — a user whose stream broke wants an answer, not a
// second kind of error.
// `chosenProvider()` lived here and is gone. Its history is worth keeping,
// because it is the same bug twice and the second fix has to not be the third.
//
// It first read `$("#provider").value`, a hidden <select> empty until
// loadProviders() fills it — ~10s cold. For those ten seconds the request
// carried nothing, the server fell back to settings.model_provider (`mock` on
// a fresh install), and the offline model answered while the composer showed a
// real one. The fix was to read localStorage instead, which the picker writes
// synchronously.
//
// That made the CLIENT authoritative, and `run_turn` treats a provider on the
// request as an override beating `agent.model_provider` — so when the stored
// value went stale, it beat a perfectly good agent binding on every turn and
// the same symptom came back with the causes reversed.
//
// Both versions failed the same way: the value on screen and the value in the
// request came from different places. There is one place now — the agent
// binding, which the picker writes, both labels render, and the server
// resolves — and the request names no provider at all.

async function streamTurn(text, think) {
  const body = JSON.stringify({
    // NO provider/model. The agent's own binding decides, server-side.
    //
    // These used to be sent from localStorage, and `run_turn` treats a provider
    // on the request as an OVERRIDE that beats `agent.model_provider`. So a
    // stale global — `chitragupta_provider` had been left on "mock" — silently
    // won every turn, while the chip and the composer pill went on rendering
    // the agent binding they read from /api/agents/{id}/model. The screen said
    // "Claude Opus 5" and the reply came back from the offline mock, with
    // nothing anywhere to show which one was real.
    //
    // The binding is already the source of truth: the composer pill POSTs to
    // /api/agents/{id}/model when you pick, both labels render what that
    // returns, and run_turn resolves it when the request stays quiet. Omitting
    // these is what makes the thing shown and the thing used the same thing.
    // An agent with no binding falls to settings.model_provider — which is
    // exactly what the "Auto" label means.
    message: text,
    effort: localStorage.getItem("chitragupta_effort") || undefined,
    turn_id: turnId || undefined,
    // Granted for this turn only. The server never stores these.
    connectors: attachedConnectors.slice(),
    images: sentImages.map((a) => ({ data_url: a.dataUrl, name: a.name })),
  });
  let resp;
  try {
    resp = await fetch(`/api/agents/${current}/chat/stream`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body, signal: controller.signal,
    });
  } catch (e) {
    if (controller.signal.aborted) throw e;
    resp = null;
  }
  if (!resp || !resp.ok || !resp.body) return plainTurn(body);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "", preview = "", result = null, failure = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; a chunk can split one.
    const frames = buffer.split("\n\n");
    buffer = frames.pop();
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      let ev; try { ev = JSON.parse(line.slice(5)); } catch (_) { continue; }
      if (ev.type === "token") {
        preview += ev.text;
        think.preview(preview);
      } else if (ev.type === "tool_call") {
        think.note(TOOL_LABELS[ev.name] || ev.name.replace(/_/g, " "));
      } else if (ev.type === "plan") {
        const next = (ev.steps || []).find((s) => !s.done);
        if (next) think.note(next.text);
      } else if (ev.type === "done") {
        result = ev.result;
      } else if (ev.type === "error") {
        failure = ev.message;
      }
    }
  }
  if (result) return result;
  if (failure) throw failure;
  return plainTurn(body);        // stream ended with nothing usable
}

async function plainTurn(body) {
  return api(`/api/agents/${current}/chat`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body, signal: controller.signal,
  });
}

// What each tool is doing, in the user's words rather than ours.
const TOOL_LABELS = {
  search_brain: "Searching your brain…", remember: "Saving that…",
  list_entities: "Looking at who and what you work with…",
  web_search: "Searching the web…", gmail_search: "Reading your mail…",
  add_task: "Adding a task…", list_tasks: "Checking your tasks…",
  complete_task: "Ticking that off…", ask_agent: "Asking another agent…",
  update_plan: "Planning…", create_open_loop: "Noting a loose end…",
  list_open_loops: "Checking loose ends…", complete_open_loop: "Closing that off…",
};


function autoGrow() {
  const t = $("#input"); if (!t) return;
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 140) + "px";
}
