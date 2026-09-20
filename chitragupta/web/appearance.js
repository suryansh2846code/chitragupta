/**
 * The Appearance screen — what each agent looks like.
 *
 * A roster of every agent drawn as its own character, and below it the editor
 * from `character.js` bound to whichever one is selected. Picking an agent
 * swaps the document in the editor; Save writes it to
 * `PUT /api/agents/{id}/avatar`, and "Use the generated one" deletes the
 * override so that agent goes back to the character composed from its id.
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

function openAppearanceScreen() {
  const m = $("#modelScreen");
  if (!m) return;
  m.hidden = false;
  showSettingsPanel("appearance");
  renderAppearanceRoster();
  // Opening straight onto the agent you are talking to, rather than onto an
  // empty frame that asks you to pick one first.
  if (!apAgent) selectAppearanceAgent(current || (agents[0] && agents[0].id));
}

/**
 * The roster.
 *
 * Static characters, not live ones: this is a row of every agent, and thirty
 * springs integrating behind a settings screen is a lot of work to make
 * thumbnails wobble. The one in the editor below follows the cursor, which is
 * where a person is actually looking.
 */
function renderAppearanceRoster() {
  const box = $("#apRoster");
  if (!box) return;
  box.textContent = "";
  if (!agents.length) {
    const empty = document.createElement("p");
    empty.className = "ms-sub";
    empty.textContent = "Add an agent from the Library and it will appear here.";
    box.appendChild(empty);
    return;
  }
  for (const a of agents) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "ap-card" + (a.id === apAgent ? " is-on" : "");
    card.setAttribute("aria-pressed", String(a.id === apAgent));

    const slot = document.createElement("span");
    slot.className = "ap-card-orb";
    card.appendChild(slot);
    paintAvatar(slot, a.id, { size: 72, title: a.name });

    const name = document.createElement("span");
    name.className = "ap-card-name";
    name.textContent = a.name;
    card.appendChild(name);

    if (AGENT_AVATARS.has(a.id)) {
      const tag = document.createElement("span");
      tag.className = "ap-card-tag";
      tag.textContent = "Custom";
      card.appendChild(tag);
    }

    card.onclick = () => selectAppearanceAgent(a.id);
    box.appendChild(card);
  }
}

function selectAppearanceAgent(id) {
  if (!id) return;
  if (apDirty && id !== apAgent && !confirm("Discard the unsaved changes to this avatar?")) return;

  apAgent = id;
  apDirty = false;
  apDoc = JSON.parse(JSON.stringify(agentScene(id)));

  const a = agents.find((x) => x.id === id);
  const nameEl = $("#apEditingName");
  if (nameEl) nameEl.textContent = a ? a.name : id;
  renderAppearanceNote();

  mountAppearanceEditor();
  markAppearanceDirty(false);
  renderAppearanceRoster();
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
  const noteEl = $("#apEditingNote");
  if (!noteEl) return;
  noteEl.textContent = AGENT_AVATARS.has(apAgent)
    ? "You made this one. It is used everywhere this agent appears."
    : "Generated from this agent. Change anything and save to make it yours.";
}

function mountAppearanceEditor() {
  const box = $("#apEditor");
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
  const save = $("#apSave");
  if (save) { save.hidden = !apAgent; save.disabled = !on; }
  const reset = $("#apReset");
  // Only offered when there is something to go back from. A control that
  // cannot do anything is worse than one that is not there.
  if (reset) reset.hidden = !apAgent || !AGENT_AVATARS.has(apAgent);
}

async function saveAppearance() {
  if (!apAgent || !apDoc) return;
  const btn = $("#apSave");
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
  renderAppearanceRoster();
}

{
  const save = $("#apSave");
  if (save) save.onclick = saveAppearance;
  const reset = $("#apReset");
  if (reset) reset.onclick = resetAppearance;
}
