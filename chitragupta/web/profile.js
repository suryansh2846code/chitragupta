/* The agent profile — everything about one agent, in one popup.
 *
 * Opened by the ⋯ on an agent row or in the chat header. A dialog rather than
 * a screen on purpose: you open it *about* the agent you are talking to, and
 * coming back to the conversation should not be a navigation.
 *
 * **It composes, it does not redraw.** The permission groups come from
 * `renderAgentTools` in `tools.js`, which is also what the agent builder uses
 * — three drawings of "what may this agent do" is three chances for them to
 * disagree, and `web/CLAUDE.md` already carries that warning about the second.
 * The markup lives in `index.html`, not here, because the focus observer and
 * the Escape handler in `app.js` bind to every `.modal-bg` once at load.
 *
 * **Names here are `prof*`, never `ap*`.** `appearance.js` owns `apAgent`,
 * `apDoc`, `apDirty` and `apEditor` at the top level of this same shared
 * scope — these are plain scripts, so redeclaring one of them with `let` is a
 * SyntaxError that kills the whole file, and the symptom is a ⋯ that does
 * nothing rather than an error anybody sees.
 */

//: Which agent is open, which tab, and whether there is unsaved prose in it.
let profAgentId = null;
let profTab = "appearance";   // see PROF_DEFAULT_TAB below
let profDirty = false;

//: The file metadata from `/api/agents/{id}/files`, so the Memory tab can show
//: how full it is without fetching the file to measure it.
let profFiles = {};

//: The tabs, in the order they are shown. **The first one is what the popup
//: opens on** — `PROF_DEFAULT_TAB` is read off this list rather than written
//: out again, so reordering the rail cannot leave the landing tab behind.
//:
//: Appearance leads because it is the one tab that is about *this agent* at a
//: glance rather than about its settings: you press the dots on a face, and
//: the face is what you get. Everything after it is in the order you would
//: reach for it — who it is, what it runs on, what it has learned, what powers
//: it, what it may touch.
const PROF_TABS = [
  { key: "appearance", label: "Appearance", icon: "appearance" },
  { key: "profile", label: "Profile", icon: "account" },
  { key: "persona", label: "Persona", icon: "pencil" },
  { key: "memory", label: "Memory", icon: "brain" },
  { key: "model", label: "Model", icon: "model" },
  { key: "permissions", label: "Permissions", icon: "shield" },
];

const PROF_DEFAULT_TAB = PROF_TABS[0].key;

/**
 * Is there unsaved work anywhere in the open tab?
 *
 * Two different flags, because the avatar editor is `appearance.js`'s and
 * tracks its own `apDirty`. Asking only about `profDirty` would let somebody
 * close the dialog on a half-built character without being asked — the one
 * tab where the work is hardest to redo.
 */
function profileHasUnsaved() {
  if (profDirty) return true;
  return profTab === "appearance" && typeof apDirty !== "undefined" && apDirty;
}

function profAgent() {
  return (agents || []).find((a) => a.id === profAgentId) || null;
}

/** The one opener. The rail, the chat header and anything later all use it. */
function openAgentProfile(id, tab) {
  const bg = $("#agentProfile");
  if (!bg) return;
  if (!id) return;
  profAgentId = id;
  profTab = tab || PROF_DEFAULT_TAB;
  profDirty = false;
  profFiles = {};
  bg.hidden = false;
  renderProfileHead();
  renderProfileTabs();
  renderProfilePane();
  loadProfileFiles();
}

/**
 * Closing is where unsaved work is defended.
 *
 * Escape closes the topmost modal by clicking the button in its `.modal-head`
 * (see `app.js`), so this is the one place the guard has to be — a keydown
 * handler of its own would be a second rule covering one of the two ways out.
 */
function closeAgentProfile() {
  if (profileHasUnsaved() &&
      !confirm("You have unsaved changes to this agent. Close and lose them?")) {
    return;
  }
  const bg = $("#agentProfile");
  if (bg) bg.hidden = true;
  profAgentId = null;
  profDirty = false;
}

//: The Save button of whichever tab is drawn, held rather than looked up.
//: `$("#profSave")` would be a claim about markup made from a handler — the
//: thing `web/CLAUDE.md` records as silently doing nothing once the markup
//: moves. Every pane that has a Save registers it here as it builds it.
let profSaveEl = null;

function markProfileDirty(on) {
  profDirty = !!on;
  if (profSaveEl) profSaveEl.disabled = !on;
}

function renderProfileHead() {
  const a = profAgent();
  if (!a) return;
  const title = $("#profTitle");
  if (title) title.textContent = a.name;
  const sub = $("#profSub");
  if (sub) sub.textContent = a.role || "";
  const orb = $("#profOrb");
  // Live, not a string: this is one avatar the user is looking straight at,
  // which is exactly what `live` is for — see `web/CLAUDE.md`.
  if (orb) paintAvatar(orb, agentOrbId(a), { live: true, title: a.name });
}

function renderProfileTabs() {
  const box = $("#profTabs");
  if (!box) return;
  box.textContent = "";
  for (const t of PROF_TABS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "profile-tab" + (t.key === profTab ? " is-on" : "");
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(t.key === profTab));
    const ic = document.createElement("span");
    ic.className = "profile-tab-ic";
    ic.setAttribute("aria-hidden", "true");
    ic.innerHTML = IC[t.icon] || "";
    b.appendChild(ic);
    const lb = document.createElement("span");
    lb.textContent = t.label;
    b.appendChild(lb);
    b.onclick = () => {
      if (t.key === profTab) return;
      if (profileHasUnsaved() &&
          !confirm("You have unsaved changes here. Leave them?")) return;
      profDirty = false;
      profTab = t.key;
      renderProfileTabs();
      renderProfilePane();
    };
    box.appendChild(b);
  }
}

function renderProfilePane() {
  const pane = $("#profPane");
  const a = profAgent();
  if (!pane || !a) return;
  pane.textContent = "";
  if (profTab === "profile") return renderProfileIdentity(pane, a);
  if (profTab === "persona") return renderProfilePersona(pane, a);
  if (profTab === "memory") return renderProfileDoc(pane, a, "memory");
  if (profTab === "model") return renderProfileModel(pane, a);
  if (profTab === "permissions") return renderProfilePermissions(pane, a);
  if (profTab === "appearance") return renderProfileAppearance(pane, a);
}

function renderProfileAppearance(pane, a) {
  const head = document.createElement("div");
  head.innerHTML =
    `<h2>What ${esc(a.name)} looks like</h2>
     <p class="ms-sub">Used everywhere this agent appears. Every agent starts
       with a face generated from its own id — change anything and save to make
       it yours.</p>`;
  pane.appendChild(head);

  const box = document.createElement("div");
  box.id = "profAppearance";
  pane.appendChild(box);
  // `appearance.js` builds its own markup in there and wires its own buttons:
  // every function in that file reaches its parts by id, so assembling them
  // here would be a second copy of its contract living outside it.
  if (typeof mountAppearanceFor === "function") mountAppearanceFor(box, a.id);
}

// ── Profile: what it is called, and the way out ────────────────────────────

function renderProfileIdentity(pane, a) {
  const head = document.createElement("div");
  head.innerHTML =
    `<h2>Who this agent is</h2>
     <p class="ms-sub">The name and line you see in the rail. Renaming changes
       nothing else — its memory, its chat and its permissions all stay with it.</p>`;
  pane.appendChild(head);

  const field = (id, label, value, max) => {
    const wrap = document.createElement("div");
    wrap.className = "profile-field";
    const lb = document.createElement("label");
    lb.textContent = label;
    lb.htmlFor = id;
    const input = document.createElement("input");
    input.className = "set-input";
    input.id = id;
    input.value = value || "";
    input.maxLength = max;
    input.oninput = () => markProfileDirty(true);
    wrap.append(lb, input);
    pane.appendChild(wrap);
    return input;
  };
  const nameEl = field("profName", "Name", a.name, 60);
  const roleEl = field("profRole", "What it does", a.role, 140);

  const foot = document.createElement("div");
  foot.className = "profile-foot";
  const save = document.createElement("button");
  save.id = "profSave";
  save.className = "tiny";
  save.textContent = "Save";
  save.disabled = true;
  profSaveEl = save;
  save.onclick = async () => {
    save.disabled = true;
    try {
      await api(`/api/agents/${encodeURIComponent(a.id)}/identity`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nameEl.value, role: roleEl.value }),
      });
    } catch (e) {
      toast(String(e)); save.disabled = false; return;
    }
    profDirty = false;
    toast("Saved");
    await loadAgents();
    renderProfileHead();
    if (profAgentId === current) selectAgent(current);
  };
  const reset = document.createElement("button");
  reset.className = "tiny ghost";
  reset.textContent = "Use the name we ship";
  reset.onclick = async () => {
    try {
      await api(`/api/agents/${encodeURIComponent(a.id)}/identity`,
                { method: "DELETE" });
    } catch (e) { toast(String(e)); return; }
    profDirty = false;
    await loadAgents();
    renderProfileHead();
    renderProfilePane();
    if (profAgentId === current) selectAgent(current);
  };
  foot.append(save, reset);
  pane.appendChild(foot);

  renderProfileDanger(pane, a);
}

/**
 * The two ways an agent leaves, which are not the same act.
 *
 * **Retire** takes it off the rail and keeps everything — its memory, its
 * persona, its conversation — and the Agent Library brings it back.
 * **Delete** destroys it and cannot be undone.
 *
 * A preset has had both since the roster existed: leaving the roster *is*
 * retiring, and the template is always in the Library to add again. A custom
 * agent had only the destructive one, so "I am not using this right now" and
 * "erase everything it learned" were one button — and the one a person reaches
 * for first is the one that cannot be taken back.
 *
 * Two rows, never one button with a modifier: the whole difference between
 * them is what survives, and that has to be readable before anything is
 * pressed.
 */
function renderProfileDanger(pane, a) {
  const box = document.createElement("div");
  box.className = "profile-danger";
  const custom = !!a.custom;
  box.innerHTML = `<h2>Putting it away</h2>`;

  const leave = async (btn, url, method, ask, said) => {
    if (!confirm(ask)) return;
    btn.disabled = true;
    // A turn still running for an agent that is about to leave has nowhere to
    // put its answer, and would go on spending the user's key writing it.
    try { await stopTurn(a.id); } catch (_) { /* it may not be running */ }
    LANDED.delete(a.id);
    delete DRAFTS[a.id];
    try {
      await api(url, { method });
    } catch (e) { toast(String(e)); btn.disabled = false; return; }
    if (current === a.id) current = null;
    profDirty = false;
    closeAgentProfile();
    toast(said);
    loadAgents();
  };

  const row = (title, desc, label, danger, run) => {
    const wrap = document.createElement("div");
    wrap.className = "set-row";
    const main = document.createElement("div");
    main.className = "set-main";
    main.innerHTML = `<div class="set-label">${esc(title)}</div>
                      <div class="set-desc">${esc(desc)}</div>`;
    const ctl = document.createElement("div");
    ctl.className = "set-ctl";
    const btn = document.createElement("button");
    btn.className = "tiny ghost" + (danger ? " is-danger" : "");
    btn.textContent = label;
    btn.onclick = () => run(btn);
    ctl.appendChild(btn);
    wrap.append(main, ctl);
    box.appendChild(wrap);
  };

  row("Retire it",
      custom
        ? "Takes it off the rail. Its memory, its persona and its conversation are all kept, and you can bring it back from the Agent Library."
        : "Takes it out of your team. Everything it learned is kept, and you can add it again from the Agent Library.",
      "Retire", false,
      (btn) => leave(
        btn,
        custom ? `/api/agents/custom/${encodeURIComponent(a.id)}/retire`
               : `/api/agents/roster/${encodeURIComponent(a.id)}`,
        custom ? "POST" : "DELETE",
        `Retire ${a.name}? Nothing it learned is deleted — the Agent Library can bring it back.`,
        "Retired — the Agent Library can bring it back"));

  // Both kinds can be deleted; what "deleted" leaves behind is what differs.
  // An agent we ship has no row to destroy and its template is in the Library
  // whatever happens — so deleting it erases everything it accumulated and it
  // comes back NEW. One the user built has a row, and deleting it is final.
  row("Delete it",
      custom
        ? "Destroys it, along with its persona, everything it has learned, and your conversation. This cannot be undone, and it will not be in the Library."
        : "Erases your conversation, everything it learned, the persona you chose and the face you gave it, and takes it off the team. You can add it again from the Library — as a new agent, with none of that.",
      custom ? "Delete permanently" : "Delete", true,
      (btn) => leave(
        btn,
        custom ? `/api/agents/custom/${encodeURIComponent(a.id)}`
               : `/api/agents/${encodeURIComponent(a.id)}/reset`,
        custom ? "DELETE" : "POST",
        custom
          ? `Permanently delete ${a.name}? Its memory and conversation go with it, and this cannot be undone.`
          : `Delete ${a.name}? Everything it learned, its persona and your conversation are erased. Adding it again gives you a new one.`,
        custom ? "Agent deleted" : "Deleted — the Library has a fresh one"));

  pane.appendChild(box);
}

// ── Persona: chosen, not written ───────────────────────────────────────────
//
// This was a blank box asking somebody to compose a system prompt, which is a
// box most people close again. It is the same `persona.md` underneath — the
// choices are rendered into it server-side by `agents/persona.py`, so nothing
// about how an agent reads its instructions changed.
//
// **The vocabulary comes down the wire.** A copy of the trait and style lists
// here would be a second copy to keep current, and the one that drifts is the
// one somebody is choosing from.

//: What the server said, held so a save can send the whole picture and a
//: re-render can redraw it without another round trip.
let profPersona = null;
let profVocab = null;

async function renderProfilePersona(pane, a) {
  const head = document.createElement("div");
  head.innerHTML =
    `<h2>How ${esc(a.name)} works</h2>
     <p class="ms-sub">Pick what fits. This is added to what the agent already
       does — it does not replace its instructions, which live in
       <code>persona.md</code> in its own folder.</p>`;
  pane.appendChild(head);

  const body = document.createElement("div");
  body.id = "profPersonaBody";
  body.innerHTML = `<p class="ms-sub">Loading…</p>`;
  pane.appendChild(body);

  const id = a.id;
  try {
    const got = await api(`/api/agents/${encodeURIComponent(id)}/persona`);
    if (profAgentId !== id || profTab !== "persona") return;
    profPersona = got.persona;
    profVocab = got.vocabulary;
  } catch (e) {
    body.innerHTML = `<p class="ms-sub">Could not open this agent's persona.</p>`;
    toast(String(e));
    return;
  }
  drawPersonaBody(body, a);
}

/** One group of chips. Multi-select, capped, and the cap is shown not enforced
 *  silently — a chip that refuses to turn on with no explanation reads as a
 *  broken control. */
function personaChips(box, { title, hint, options, chosen, cap, onChange }) {
  const sec = document.createElement("section");
  sec.className = "pp-sec";
  const h = document.createElement("div");
  h.className = "pp-head";
  h.innerHTML = `<div class="pp-title">${esc(title)}</div>
                 <div class="pp-hint">${esc(hint)}</div>`;
  sec.appendChild(h);

  const row = document.createElement("div");
  row.className = "pp-chips";
  for (const opt of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pp-chip" + (chosen.includes(opt) ? " is-on" : "");
    b.setAttribute("aria-pressed", String(chosen.includes(opt)));
    b.textContent = opt;
    b.onclick = () => {
      const at = chosen.indexOf(opt);
      if (at >= 0) chosen.splice(at, 1);
      else if (chosen.length >= cap) {
        toast(`Pick at most ${cap} — more than that stops describing anything.`);
        return;
      } else chosen.push(opt);
      b.classList.toggle("is-on", at < 0);
      b.setAttribute("aria-pressed", String(at < 0));
      onChange();
    };
    row.appendChild(b);
  }
  sec.appendChild(row);
  box.appendChild(sec);
}

/** The autonomy choice: one card per level, the whole card clickable. */
function personaAutonomy(box, chosen, onChange) {
  const sec = document.createElement("section");
  sec.className = "pp-sec";
  sec.innerHTML = `<div class="pp-head">
      <div class="pp-title">How much it decides on its own</div>
      <div class="pp-hint">What it may use is the Permissions tab. This is how
        far it goes with what it has.</div>
    </div>`;
  const row = document.createElement("div");
  row.className = "pp-levels";
  // The cards are held, not re-found with `row.querySelectorAll(".pp-level")`
  // from inside the handler. These three are built right here, so searching
  // the DOM for them again is the selector-as-a-claim-about-markup failure
  // `web/CLAUDE.md` records — and it is not hypothetical: with the lookup,
  // choosing a second level left the first one lit as well.
  const cards = [];
  for (const level of profVocab.autonomy) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "pp-level" + (level.key === chosen.value ? " is-on" : "");
    card.setAttribute("role", "radio");
    card.setAttribute("aria-checked", String(level.key === chosen.value));
    card.innerHTML = `<span class="pp-level-name">${esc(level.label)}</span>
                      <span class="pp-level-blurb">${esc(level.blurb)}</span>`;
    card.onclick = () => {
      chosen.value = level.key;
      for (const other of cards) {
        other.classList.remove("is-on");
        other.setAttribute("aria-checked", "false");
      }
      card.classList.add("is-on");
      card.setAttribute("aria-checked", "true");
      onChange();
    };
    cards.push(card);
    row.appendChild(card);
  }
  sec.appendChild(row);
  box.appendChild(sec);
}

function drawPersonaBody(body, a) {
  body.textContent = "";
  const v = profVocab;
  const p = profPersona;
  const traits = [...(p.traits || [])];
  const comm = [...(p.communication || [])];
  const think = [...(p.thinking || [])];
  const level = { value: p.autonomy || v.default_autonomy };
  const dirty = () => markProfileDirty(true);

  personaAutonomy(body, level, dirty);
  personaChips(body, {
    title: "How it comes across", hint: `Up to ${v.limits.traits}.`,
    options: v.traits, chosen: traits, cap: v.limits.traits, onChange: dirty });
  personaChips(body, {
    title: "How it says things", hint: `Up to ${v.limits.communication}.`,
    options: v.communication, chosen: comm, cap: v.limits.communication,
    onChange: dirty });
  personaChips(body, {
    title: "How it works a problem", hint: `Up to ${v.limits.thinking}.`,
    options: v.thinking, chosen: think, cap: v.limits.thinking,
    onChange: dirty });

  const sec = document.createElement("section");
  sec.className = "pp-sec";
  sec.innerHTML = `<div class="pp-head">
      <div class="pp-title">Anything else</div>
      <div class="pp-hint">In your own words. This is kept exactly as you write
        it, and it is the part the agent is told outranks the choices above.</div>
    </div>`;
  const area = document.createElement("textarea");
  area.className = "profile-doc pp-extra";
  area.id = "profPersonaExtra";
  area.setAttribute("aria-label", "Anything else");
  area.placeholder =
    "e.g. Always check my calendar before proposing a time, and never reply to recruiters.";
  area.value = p.extra || "";
  area.oninput = dirty;
  sec.appendChild(area);
  body.appendChild(sec);

  const foot = document.createElement("div");
  foot.className = "profile-foot";
  const save = document.createElement("button");
  save.className = "tiny";
  save.textContent = "Save";
  save.disabled = true;
  profSaveEl = save;
  save.onclick = async () => {
    save.disabled = true;
    try {
      const got = await api(`/api/agents/${encodeURIComponent(a.id)}/persona`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ traits, communication: comm, thinking: think,
                               autonomy: level.value, extra: area.value }),
      });
      profPersona = got.persona;
    } catch (e) {
      toast(String(e)); save.disabled = false; return;
    }
    profDirty = false;
    toast("Saved");
    // Read only takes tools away and leaving it puts them back, so the
    // Permissions tab is now showing something other than what is true.
    if (typeof loadAgentTools === "function") AGENT_TOOLS_FOR = "";
  };
  foot.appendChild(save);
  body.appendChild(foot);
  markProfileDirty(false);
}

// ── Memory: the file it writes itself ──────────────────────────────────────

//: Only `memory.md` now. `persona.md` is still a file and still the thing the
//: agent reads, but it is no longer edited as one — the Persona tab writes it
//: from choices, so a second spec for it here would be a screen nothing draws.
const PROF_DOCS = {
  memory: {
    file: "memory.md",
    title: "What it has learned",
    sub: `This agent writes here itself, from your conversations — how you want
          its work done. Facts about you go to your brain instead, where every
          agent can use them. Anything you delete here it will not learn again.`,
    empty: "Nothing learned yet. It fills in as you work together.",
    reset: "Forget all of it",
    cleared: "Forgotten",
  },
};

async function loadProfileFiles() {
  const id = profAgentId;
  try {
    const got = await api(`/api/agents/${encodeURIComponent(id)}/files`);
    if (profAgentId !== id) return;         // they moved on while we waited
    profFiles = {};
    for (const f of got.files || []) profFiles[f.name] = f;
  } catch (_) { /* the pane shows the file itself; this is only the meter */ }
  if (profTab === "memory") renderProfileMeter();
}

function renderProfileMeter() {
  const el = $("#profMeter");
  if (!el) return;
  const spec = PROF_DOCS[profTab];
  const meta = profFiles[spec.file];
  if (!meta || !meta.exists) { el.textContent = ""; return; }
  const kb = (n) => (n / 1024).toFixed(1);
  const full = meta.limit && meta.bytes >= meta.limit - 256;
  el.classList.toggle("is-full", !!full);
  // Only shown once there is something to measure. A "0.0 of 6.0 KB" on an
  // empty file is a number nobody asked for about a thing that is not a
  // problem — the meter exists for the one case where it IS one.
  el.textContent = full
    ? `Full (${kb(meta.bytes)} KB). Trim it, or this agent cannot learn anything new.`
    : `${kb(meta.bytes)} of ${kb(meta.limit)} KB`;
}

async function renderProfileDoc(pane, a, kind) {
  const spec = PROF_DOCS[kind];
  const head = document.createElement("div");
  head.innerHTML = `<h2>${esc(spec.title)}</h2><p class="ms-sub">${spec.sub}</p>`;
  pane.appendChild(head);

  const area = document.createElement("textarea");
  area.className = "profile-doc";
  area.id = "profDoc";
  area.setAttribute("aria-label", spec.title);
  area.placeholder = spec.empty;
  area.value = "";
  area.oninput = () => markProfileDirty(true);
  pane.appendChild(area);

  const foot = document.createElement("div");
  foot.className = "profile-foot";
  const save = document.createElement("button");
  save.id = "profSave";
  save.className = "tiny";
  save.textContent = "Save";
  save.disabled = true;
  profSaveEl = save;
  const reset = document.createElement("button");
  reset.className = "tiny ghost";
  reset.textContent = spec.reset;
  const meter = document.createElement("span");
  meter.id = "profMeter";
  meter.className = "profile-meter muted";
  foot.append(save, reset, meter);
  pane.appendChild(foot);

  const url = `/api/agents/${encodeURIComponent(a.id)}/files/${spec.file}`;
  const id = a.id;
  try {
    const got = await api(url);
    if (profAgentId !== id || profTab !== kind) return;
    area.value = got.text || "";
  } catch (e) {
    toast(`Could not open ${spec.file} — ${e}`);
  }
  markProfileDirty(false);
  renderProfileMeter();

  save.onclick = async () => {
    save.disabled = true;
    try {
      await api(url, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: area.value }),
      });
    } catch (e) {
      // The server refuses a file past its cap with a sentence written to be
      // read by the person who pressed this button. Show that, not a code.
      toast(String(e)); save.disabled = false; return;
    }
    profDirty = false;
    toast("Saved");
    loadProfileFiles();
  };
  reset.onclick = async () => {
    if (!confirm(kind === "memory"
      ? `Forget everything ${a.name} has learned about this work?`
      : `Put ${a.name} back on the instructions it ships with?`)) return;
    try {
      await api(url, { method: "DELETE" });
    } catch (e) { toast(String(e)); return; }
    area.value = "";
    profDirty = false;
    markProfileDirty(false);
    toast(spec.cleared);
    loadProfileFiles();
  };
}

// ── Model ──────────────────────────────────────────────────────────────────

async function renderProfileModel(pane, a) {
  const head = document.createElement("div");
  head.innerHTML =
    `<h2>What powers this agent</h2>
     <p class="ms-sub">Its thinking, its recall and its tools all run on this.
       Leave it on the workspace default and it follows whatever you pick there.</p>`;
  pane.appendChild(head);

  const provWrap = document.createElement("div");
  provWrap.className = "profile-field";
  provWrap.innerHTML = `<label for="profProv">Provider</label>`;
  const prov = document.createElement("select");
  prov.className = "set-select";
  prov.id = "profProv";
  provWrap.appendChild(prov);
  pane.appendChild(provWrap);

  const modWrap = document.createElement("div");
  modWrap.className = "profile-field";
  modWrap.innerHTML = `<label for="profModel">Model</label>`;
  const mod = document.createElement("select");
  mod.className = "set-select";
  mod.id = "profModel";
  modWrap.appendChild(mod);
  pane.appendChild(modWrap);

  const catalog = (typeof MODEL_CATALOG !== "undefined" && MODEL_CATALOG) || [];
  // "Whatever the workspace is set to" is a real answer and the default one,
  // so it is an option rather than an empty select somebody has to interpret.
  prov.innerHTML = `<option value="">The workspace default</option>` +
    catalog.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}${
      p.ready === false ? " — not set up" : ""}</option>`).join("");

  const fillModels = (pid) => {
    const entry = catalog.find((p) => p.id === pid);
    mod.innerHTML = `<option value="">Let it choose</option>` +
      ((entry && entry.models) || []).map((m) =>
        `<option value="${esc(m.id)}">${esc(m.name || m.id)}</option>`).join("");
    mod.disabled = !pid;
  };

  let bound = {};
  try { bound = await api(`/api/agents/${encodeURIComponent(a.id)}/model`); }
  catch (_) { bound = {}; }
  if (profAgentId !== a.id || profTab !== "model") return;
  prov.value = bound.provider || "";
  fillModels(prov.value);
  if (bound.model) mod.value = bound.model;

  prov.onchange = () => { fillModels(prov.value); markProfileDirty(true); };
  mod.onchange = () => markProfileDirty(true);

  const foot = document.createElement("div");
  foot.className = "profile-foot";
  const save = document.createElement("button");
  save.id = "profSave";
  save.className = "tiny";
  save.textContent = "Save";
  save.disabled = true;
  profSaveEl = save;
  save.onclick = async () => {
    save.disabled = true;
    const url = `/api/agents/${encodeURIComponent(a.id)}/model`;
    try {
      if (!prov.value) await api(url, { method: "DELETE" });
      else {
        await api(url, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider: prov.value, model: mod.value || null }),
        });
      }
    } catch (e) { toast(String(e)); save.disabled = false; return; }
    profDirty = false;
    toast("Saved");
    if (typeof updateAgentModelChip === "function" && profAgentId === current) {
      updateAgentModelChip(current);
    }
  };
  foot.appendChild(save);
  pane.appendChild(foot);
}

// ── Permissions ────────────────────────────────────────────────────────────

function renderProfilePermissions(pane, a) {
  const head = document.createElement("div");
  head.innerHTML =
    `<h2>What ${esc(a.name)} may use</h2>
     <p class="ms-sub">Changes apply from its next question — nothing to restart.</p>`;
  pane.appendChild(head);

  const box = document.createElement("div");
  box.className = "at-list";
  box.id = "profToolList";
  pane.appendChild(box);
  // The Agents & tools renderer, drawing into this pane. Not a copy of it:
  // the grouping, the presets and the blocked-reason wording are exactly what
  // must not exist twice.
  if (typeof loadAgentTools === "function") loadAgentTools(a.id, box);
}

// ── wiring ─────────────────────────────────────────────────────────────────

{
  const close = $("#profClose");
  if (close) close.onclick = closeAgentProfile;
  const chMore = $("#chMore");
  if (chMore) {
    chMore.innerHTML = IC.dots;
    chMore.onclick = () => { if (current) openAgentProfile(current); };
  }
}
