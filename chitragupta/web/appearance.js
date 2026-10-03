/**
 * What one agent looks like — the Appearance tab of the agent profile.
 *
 * The editor from `character.js`, bound to the agent whose profile is open.
 * Save writes it to `PUT /api/agents/{id}/avatar`, and "Use the generated one"
 * deletes the override so that agent goes back to the character composed from
 * its id.
 *
 * This was a settings screen with a roster of every agent along the top. The
 * roster existed only to answer "which agent am I editing", and the profile has
 * already answered that — so it went with the screen, and `mountAppearanceFor`
 * is what the profile calls instead. The endpoint work, the dirty tracking and
 * the two buttons are unchanged; only the question of *which agent* moved.
 *
 * **Nothing here knows what a character is.** No shapes, no palettes, no
 * schema. This file moves documents between an editor, an endpoint and the
 * `AGENT_AVATARS` map in `core.js` — the format lives entirely in
 * `character.js`, which is a standalone package with its own tests, and a
 * second opinion about it here is a second opinion to keep current.
 *
 * **Edits are not saved as you make them.** The editor fires on every slider
 * tick; writing each one would be hundreds of requests per drag and an undo
 * history nobody asked for. So changes are held, Save is enabled while they are
 * pending, and leaving with unsaved work says so rather than discarding it
 * silently.
 */

//: The agent currently in the editor, and the document it holds. `dirty` is the
//: difference between "looking at an agent" and "has changes worth keeping".
let apAgent = null;
let apDoc = null;
let apDirty = false;
let apEditor = null;

//: The parts `mountAppearanceFor` built, held rather than looked up again.
//:
//: They used to be markup in `index.html` reached by id, which worked because
//: there was exactly one of each and it was always on the page. Now they are
//: created per pane, and `$("#apEditor")` from here is a claim about markup
//: made from a handler — the shape `web/CLAUDE.md` records for `syncConn`,
//: where every guarded line silently did nothing once the markup moved.
let apBox = null;
let apSaveBtn = null;
let apResetBtn = null;
let apNameEl = null;
let apNoteEl = null;

/**
 * Kept, and now a redirect — the screen it opened no longer exists.
 *
 * It was a settings page with a roster of every agent across the top, which
 * existed only to answer "which agent am I editing". The agent profile has
 * already answered that, so this opens it on the Appearance tab.
 */
function openAppearanceScreen() {
  const id = current || (agents[0] && agents[0].id);
  if (id && typeof openAgentProfile === "function") {
    openAgentProfile(id, "appearance");
  }
}


/**
 * Draw the whole editor for one agent into `container`.
 *
 * This was a settings screen with a roster of every agent along the top and the
 * editor underneath. The roster only existed to answer "which agent am I
 * editing" — a question the agent profile has already answered by the time this
 * is reached, so what is left is the editor and its two buttons.
 *
 * **The markup is built here, not in `profile.js`.** Everything below works on
 * these exact elements, so building them is part of this file's job; a caller
 * assembling them would be a second copy of that arrangement living in a file
 * that does not use it. The ids are kept for the devtools inspector and for
 * styling — nothing in this file looks anything up by them any more.
 */
function mountAppearanceFor(container, agentId) {
  if (!container) return;
  container.textContent = "";

  const name = document.createElement("div");
  name.id = "apEditingName";
  name.className = "ap-editing-name";
  const note = document.createElement("p");
  note.id = "apEditingNote";
  note.className = "ms-sub";
  const box = document.createElement("div");
  box.id = "apEditor";
  box.className = "ap-editor";

  const foot = document.createElement("div");
  foot.className = "profile-foot";
  const save = document.createElement("button");
  save.id = "apSave";
  save.className = "tiny";
  save.textContent = "Save";
  save.hidden = true;
  save.onclick = saveAppearance;
  const reset = document.createElement("button");
  reset.id = "apReset";
  reset.className = "tiny ghost";
  reset.textContent = "Use the generated one";
  reset.hidden = true;
  reset.onclick = resetAppearance;
  foot.append(save, reset);

  container.append(name, note, box, foot);
  apBox = box; apSaveBtn = save; apResetBtn = reset;
  apNameEl = name; apNoteEl = note;

  // A fresh pane is a fresh mount, even for the agent that was open last time:
  // `selectAppearanceAgent` returns early when the id has not changed, which
  // would leave the editor unmounted in a container that was just emptied.
  // Clearing `apDirty` with it is safe because the pane cannot be rebuilt
  // without going through the profile's own unsaved-work guard first.
  apAgent = null;
  apDirty = false;
  selectAppearanceAgent(agentId);
}

function selectAppearanceAgent(id) {
  if (!id) return;
  if (apDirty && id !== apAgent && !confirm("Discard the unsaved changes to this avatar?")) return;

  apAgent = id;
  apDirty = false;
  apDoc = JSON.parse(JSON.stringify(agentScene(id)));

  const a = agents.find((x) => x.id === id);
  if (apNameEl) apNameEl.textContent = a ? a.name : id;
  renderAppearanceNote();

  mountAppearanceEditor();
  markAppearanceDirty(false);
}

/**
 * Say which of the two states this agent's avatar is in.
 *
 * Called on select *and* after saving. Only setting it on select left the note
 * saying "change anything and save to make it yours" on an avatar the person
 * had just made theirs — the one moment they are looking for confirmation that
 * the save landed.
 */
function renderAppearanceNote() {
  if (!apNoteEl) return;
  apNoteEl.textContent = AGENT_AVATARS.has(apAgent)
    ? "You made this one. It is used everywhere this agent appears."
    : "Generated from this agent. Change anything and save to make it yours.";
}

function mountAppearanceEditor() {
  const box = apBox;
  if (!box) return;
  if (typeof Character === "undefined" || !Character.mountEditor) {
    box.textContent = "The avatar editor could not be loaded.";
    return;
  }
  if (apEditor) { apEditor.destroy(); apEditor = null; }
  box.textContent = "";
  apEditor = Character.mountEditor(box, {
    document: apDoc,
    // Saved presets are the user's own scratch shelf and belong to them, not to
    // one agent — so they are shared across every agent in this workspace.
    storageKey: "chitragupta_character_presets",
    onChange: (next) => {
      apDoc = next;
      markAppearanceDirty(true);
    },
  });
}

function markAppearanceDirty(on) {
  apDirty = on;
  const save = apSaveBtn;
  if (save) { save.hidden = !apAgent; save.disabled = !on; }
  const reset = apResetBtn;
  // Only offered when there is something to go back from. A control that
  // cannot do anything is worse than one that is not there.
  if (reset) reset.hidden = !apAgent || !AGENT_AVATARS.has(apAgent);
}

async function saveAppearance() {
  if (!apAgent || !apDoc) return;
  const btn = apSaveBtn;
  if (btn) btn.disabled = true;
  try {
    await api(`/api/agents/${apAgent}/avatar`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene: apDoc }),
    });
    AGENT_AVATARS.set(apAgent, apDoc);
    markAppearanceDirty(false);
    renderAppearanceNote();
    repaintAgentAvatars();
    toast("Avatar saved");
  } catch (e) {
    // The server's message is written for a person to read; showing ours
    // instead would throw away the only part that says what to do.
    toast(typeof e === "string" ? e : "Couldn't save that avatar");
    if (btn) btn.disabled = false;
  }
}

async function resetAppearance() {
  if (!apAgent) return;
  try {
    await api(`/api/agents/${apAgent}/avatar`, { method: "DELETE" });
    AGENT_AVATARS.delete(apAgent);
    selectAppearanceAgent(apAgent);       // reloads the generated character
    repaintAgentAvatars();
    toast("Back to the generated avatar");
  } catch (_) {
    toast("Couldn't reset that avatar");
  }
}

/**
 * Repaint every avatar on screen after a save.
 *
 * The rail, the chat header and the roster all drew the old character, and a
 * saved avatar that only appears after a reload reads as a save that did not
 * work. `loadAgents()` redraws the rail; the chat header and the Library are
 * repainted directly because they are not part of that render.
 */
function repaintAgentAvatars() {
  try { loadAgents(); } catch (_) {}
  const chOrb = $("#chOrb");
  if (chOrb && current) paintAvatar(chOrb, current, { live: true });
  const ctxOrb = $("#ctxOrb");
  if (ctxOrb && current) paintAvatar(ctxOrb, current, { size: 96 });
  // And the one in the profile's own header, which is the avatar the person is
  // looking straight at when they press Save.
  const profOrb = $("#profOrb");
  if (profOrb && apAgent) paintAvatar(profOrb, apAgent, { live: true });
}
