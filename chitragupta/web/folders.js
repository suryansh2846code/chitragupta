/* Which folders the open agent may read — set from the composer, and from the
 * agent's own Folders tab.
 *
 * The pill beside the message box used to say "workspace" and open the
 * Connectors screen. That is a different thing and saying so is the point:
 * folders added there are *ingested into the brain*, which every agent and the
 * whole graph share. These are one agent's own — the social-media agent and the
 * tax agent live on the same Mac and have no business reading each other's
 * work, and there was no way to say so.
 *
 * **One endpoint, both directions.** The pill and the profile tab read
 * `GET /api/agents/{id}/folders` and write with `PUT`, the way the autonomy
 * pill and the Persona tab share theirs, so a folder set here and a folder set
 * there cannot disagree.
 *
 * **Three states, not two.** A folder list the user has never touched is
 * *undecided* and the agent follows whatever the machine already had open —
 * otherwise every existing setup would silently lose its files on upgrade. An
 * empty list is the user saying *look at nothing*, which is a thing they asked
 * to be able to say and which has to survive a reload. The server keeps them
 * apart as `null` and `[]`; so does this.
 */

//: What one agent can reach, what it could be given, and what it was actually
//: told — plus **which agent it is about**, which is the field that matters.
//:
//: Two surfaces show this and they are not always on the same agent: the rail's
//: ⋯ opens any agent's profile without selecting it, and creating an agent
//: opens its profile while the chat is still on somebody else. One shared
//: `for` would have let the Folders tab repaint the composer pill with a
//: different agent's folders — a label about somebody else, over the message
//: box you are about to send from.
function foldersState(agentId) {
  return { for: agentId || "", available: [], chosen: null, effective: [] };
}

let fldPill = foldersState("");    // what the composer pill is showing
let fldTab = foldersState("");     // what the profile's Folders tab is showing

function closeFolderMenu() {
  const menu = $("#cmpFolderMenu");
  const pill = $("#cmpFolderPill");
  if (menu) menu.hidden = true;
  if (pill) {
    pill.classList.remove("is-active");
    pill.setAttribute("aria-expanded", "false");
  }
}

/** The last segment of a path — what a person calls a folder. */
function folderName(path) {
  const parts = String(path || "").replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || path;
}

/** The pill's word for a set of folders. Never a count on its own at n=1. */
function folderSummary(paths) {
  if (!paths.length) return "No folders";
  if (paths.length === 1) return folderName(paths[0]);
  return `${paths.length} folders`;
}

function renderFolderPill() {
  const pill = $("#cmpFolderPill");
  const label = $("#cmpFolderLabel");
  if (!pill || !label) return;
  const on = fldPill.effective;
  label.textContent = folderSummary(on);
  // The whole list on the control, because the label can only hold a count and
  // "3 folders" is not an answer to "which three".
  pill.title = on.length
    ? `This agent can read:\n${on.join("\n")}`
    : "This agent reads no files. Pick a folder to give it one.";
  pill.classList.toggle("is-locked", on.length === 0);
}

function renderFolderMenu() {
  const menu = $("#cmpFolderMenu");
  if (!menu) return;
  menu.textContent = "";

  const head = document.createElement("div");
  head.className = "cmp-folder-head";
  head.textContent = "Folders this agent can read";
  menu.appendChild(head);

  const note = document.createElement("div");
  note.className = "cmp-folder-note";
  // Says what this is NOT, because the other folder control in this app does
  // the opposite thing and the two were one word apart.
  note.textContent = "Just this agent. Folders added under Connectors go into "
    + "the shared brain instead.";
  menu.appendChild(note);

  const on = new Set(fldPill.effective);
  for (const path of fldPill.available) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "cmp-folder-row" + (on.has(path) ? " is-on" : "");
    row.setAttribute("role", "menuitemcheckbox");
    row.setAttribute("aria-checked", String(on.has(path)));
    row.dataset.folder = path;
    row.innerHTML =
      `<span class="cmp-folder-tick">${on.has(path) ? IC.check : ""}</span>
       <span class="cmp-folder-text">
         <span class="cmp-folder-name">${esc(folderName(path))}</span>
         <span class="cmp-folder-path">${esc(path)}</span>
       </span>`;
    row.onclick = () => toggleAgentFolder(fldPill, path);
    menu.appendChild(row);
  }

  if (!fldPill.available.length) {
    const empty = document.createElement("div");
    empty.className = "cmp-folder-note";
    empty.textContent = "No folder has been opened to this app yet.";
    menu.appendChild(empty);
  }

  const foot = document.createElement("div");
  foot.className = "cmp-folder-foot";

  const none = document.createElement("button");
  none.type = "button";
  none.className = "cmp-folder-act" + (fldPill.effective.length ? "" : " is-on");
  none.id = "cmpFolderNone";
  none.textContent = "Look at no files";
  none.onclick = () => saveAgentFolders(fldPill, []);
  foot.appendChild(none);

  const add = document.createElement("button");
  add.type = "button";
  add.className = "cmp-folder-act";
  add.id = "cmpFolderAdd";
  add.textContent = "Add a folder…";
  add.onclick = () => pickFolderForAgent(fldPill);
  foot.appendChild(add);

  menu.appendChild(foot);
}

/** Read one agent's folders into `state`, ignoring an answer that arrived late. */
async function fetchFoldersInto(state, agentId) {
  state.for = agentId;
  try {
    const got = await api(`/api/agents/${encodeURIComponent(agentId)}/folders`);
    // The user may have moved on while this was in flight; a list painted over
    // a different agent is a label about somebody else.
    if (state.for !== agentId) return false;
    state.available = got.available || [];
    state.chosen = got.chosen === undefined ? null : got.chosen;
    state.effective = got.folders || [];
  } catch (_) {
    state.available = []; state.chosen = null; state.effective = [];
  }
  return true;
}

/** Read the open agent's folders into the pill. Called on every agent switch. */
async function loadAgentFolders(agentId) {
  const pill = $("#cmpFolderPill");
  if (!agentId) { if (pill) pill.hidden = true; return; }
  const landed = await fetchFoldersInto(fldPill, agentId);
  if (!landed) return;              // a newer switch owns the pill now
  renderFolderPill();
  renderFolderMenu();
  // Shown only once it has something true to say. The markup ships it hidden
  // for the same reason the mode pill does: a control reading "No folders"
  // before anybody has told it anything is a control reporting a guess, and
  // the guess here is the one that reads as "this agent is cut off".
  if (pill) pill.hidden = false;
}

/** Repaint whichever surfaces are showing this agent. */
function renderFoldersFor(agentId) {
  if (fldPill.for === agentId) { renderFolderPill(); renderFolderMenu(); }
  if (fldTab.for === agentId) renderFolderPane();
}

/**
 * Write one agent's folders.
 *
 * Always sends a list, never null: every path here came from a press, and a
 * press that says "these" must not read back as "whatever the machine had".
 *
 * `state` is the surface the press came from, and the other surface is updated
 * only when it is on the same agent — the pill and the Folders tab are often
 * on different ones.
 */
async function saveAgentFolders(state, paths) {
  const agentId = state.for;
  if (!agentId) return;
  const was = { ...state };
  // Moved before the request and put back if it fails: leaving the old label
  // up until the server answers reads as a press that did nothing.
  const paint = (available, chosen, effective) => {
    for (const s of [fldPill, fldTab]) {
      if (s.for !== agentId) continue;
      s.available = available; s.chosen = chosen; s.effective = effective;
    }
    renderFoldersFor(agentId);
  };
  paint(state.available, paths.slice(), paths.slice());
  try {
    const got = await api(`/api/agents/${encodeURIComponent(agentId)}/folders`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folders: paths }),
    });
    // Trust what came back, not what we sent — the server intersects what we
    // asked for with what is actually open.
    paint(got.available || was.available,
          got.chosen === undefined ? null : got.chosen,
          got.folders || []);
  } catch (e) {
    paint(was.available, was.chosen, was.effective);
    toast(String(e));
    return;
  }
  toast(state.effective.length
    ? `Reading ${folderSummary(state.effective)}`
    : "This agent will not look at any files");
}

function toggleAgentFolder(state, path) {
  const on = new Set(state.effective);
  if (on.has(path)) on.delete(path); else on.add(path);
  // Kept in the order the machine lists them, so the menu does not reshuffle
  // under the finger that is pressing it.
  saveAgentFolders(state, state.available.filter((p) => on.has(p)));
}

// ── the one folder browser ─────────────────────────────────────────────────
//
// There is one modal and one set of handlers, used for both jobs that need a
// folder: giving one to an agent, and ingesting one into the brain. They are
// genuinely different outcomes, which is why each passes its own title, its
// own button and its own action — but the walking of the disk is the same act
// and a second copy of it would drift from this one.

let fldBrowsePath = null;
let fldBrowseDone = null;

async function browseFolders(path) {
  const got = await api("/api/fs/browse" + (path ? `?path=${encodeURIComponent(path)}` : ""));
  fldBrowsePath = got.path;
  const modal = $("#picker");
  if (modal) modal.hidden = false;
  const where = $("#pickPath");
  if (where) where.textContent = got.path;
  const info = $("#pickInfo");
  if (info) {
    info.textContent = got.ingestible_here
      ? `${got.ingestible_here} readable file(s) directly here`
      : "no text files directly here (subfolders may still have them)";
  }
  let rows = "";
  if (got.parent) rows += `<div class="pick-row up" data-go="${esc(got.parent)}">..</div>`;
  rows += (got.dirs || []).map((name) =>
    `<div class="pick-row" data-go="${esc(got.path.replace(/\/$/, "") + "/" + name)}">${esc(name)}</div>`
  ).join("");
  const list = $("#pickList");
  if (list) {
    list.innerHTML = rows || `<div class="pick-row up">(no subfolders)</div>`;
    list.querySelectorAll("[data-go]").forEach((el) => {
      el.onclick = () => browseFolders(el.dataset.go);
    });
  }
}

/**
 * Open the browser, and do `onPick(path)` with whatever the user lands on.
 *
 * `title` and `cta` are the caller's, because the two callers are doing
 * different things to the folder and a button reading "Use this folder" over a
 * flow that ingests a thousand files into the shared brain would be the card
 * describing something other than what its button runs.
 */
function chooseFolder({ title, cta, onPick }) {
  fldBrowseDone = onPick;
  const heading = $("#pickTitle");
  if (heading) heading.textContent = title;
  const button = $("#pickConfirm");
  if (button) button.textContent = cta;
  browseFolders();
}

function closeFolderBrowser() {
  const modal = $("#picker");
  if (modal) modal.hidden = true;
  fldBrowseDone = null;
}

function pickFolderForAgent(state) {
  closeFolderMenu();
  chooseFolder({
    title: "Pick a folder for this agent",
    cta: "Give it this folder",
    onPick: (path) => {
      // Added to what it already has rather than replacing it: the press said
      // "and this one too", and nobody means "and nothing else" by it.
      const held = state.effective;
      saveAgentFolders(state, held.includes(path) ? held : [...held, path]);
    },
  });
}

// ── the same setting, drawn in the agent's profile ─────────────────────────
//
// `profile.js` composes and this draws, the way Permissions is drawn by
// `tools.js` and Appearance by `appearance.js`. Same endpoint and the same
// three answers as the pill — a second copy here would be two settings wearing
// one name, which is exactly what the pill and the Persona tab avoid.
//
// Its own `for`, though. The rail's ⋯ opens any agent's profile without
// selecting it, and creating an agent opens its profile while the chat is
// still on somebody else, so this tab and that pill are routinely about two
// different agents.

//: Where the tab body was mounted, so a save can repaint it without the
//: profile having to know. Held rather than re-queried: `#profFolders` is
//: built per pane, and a selector in a handler is a claim about markup that
//: goes stale.
let fldPaneBox = null;

function renderFolderPane() {
  const box = fldPaneBox;
  if (!box || !box.isConnected) { fldPaneBox = null; return; }
  box.textContent = "";

  const list = document.createElement("div");
  list.className = "fld-list";
  const on = new Set(fldTab.effective);

  for (const path of fldTab.available) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "fld-row" + (on.has(path) ? " is-on" : "");
    row.setAttribute("role", "checkbox");
    row.setAttribute("aria-checked", String(on.has(path)));
    row.dataset.folder = path;
    row.innerHTML =
      `<span class="fld-tick">${on.has(path) ? IC.check : ""}</span>
       <span class="fld-text">
         <span class="fld-name">${esc(folderName(path))}</span>
         <span class="fld-path">${esc(path)}</span>
       </span>`;
    row.onclick = () => toggleAgentFolder(fldTab, path);
    list.appendChild(row);
  }
  box.appendChild(list);

  if (!fldTab.available.length) {
    const empty = document.createElement("p");
    empty.className = "ms-sub";
    empty.textContent = "No folder has been opened to this app yet. "
      + "Add one and this agent gets it.";
    box.appendChild(empty);
  }

  const said = document.createElement("p");
  said.className = "fld-state";
  // Says what is true now, in the words of the three answers the server keeps
  // apart: nothing chosen, nothing at all, or these. A count cannot tell the
  // first two apart, which is why this is a sentence.
  said.textContent = fldTab.effective.length
    ? `Reading ${fldTab.effective.length === 1 ? "one folder" : fldTab.effective.length + " folders"}.`
    : (fldTab.chosen === null
        ? "Nothing chosen yet — this agent follows whatever was already open."
        : "This agent opens no files at all.");
  box.appendChild(said);

  const acts = document.createElement("div");
  acts.className = "fld-acts";

  const add = document.createElement("button");
  add.id = "profFolderAdd";
  add.textContent = "Add a folder…";
  add.onclick = () => pickFolderForAgent(fldTab);
  acts.appendChild(add);

  const none = document.createElement("button");
  none.id = "profFolderNone";
  none.className = "ghost";
  none.textContent = "Look at no files";
  none.disabled = fldTab.effective.length === 0;
  none.onclick = () => saveAgentFolders(fldTab, []);
  acts.appendChild(none);

  box.appendChild(acts);
}

async function mountAgentFoldersIn(box, agentId) {
  fldPaneBox = box;
  box.textContent = "";
  const loading = document.createElement("p");
  loading.className = "ms-sub";
  loading.textContent = "Loading…";
  box.appendChild(loading);
  await fetchFoldersInto(fldTab, agentId);
  renderFolderPane();
}

{
  const pill = $("#cmpFolderPill");
  if (pill) {
    pill.onclick = (e) => {
      e.stopPropagation();
      const menu = $("#cmpFolderMenu");
      if (!menu) return;
      const open = !menu.hidden;
      if (!open) renderFolderMenu();
      menu.hidden = open;
      pill.classList.toggle("is-active", !open);
      pill.setAttribute("aria-expanded", String(!open));
    };
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest || !e.target.closest(".cmp-folder-wrap")) closeFolderMenu();
  });
  window.addEventListener("keydown", (e) => {
    const menu = $("#cmpFolderMenu");
    if (e.key === "Escape" && menu && !menu.hidden) {
      e.stopPropagation();
      closeFolderMenu();
    }
  });

  const close = $("#pickClose");
  if (close) close.onclick = closeFolderBrowser;
  const confirm = $("#pickConfirm");
  if (confirm) {
    confirm.onclick = () => {
      const path = fldBrowsePath;
      const done = fldBrowseDone;
      closeFolderBrowser();
      if (path && done) done(path);
    };
  }
}
