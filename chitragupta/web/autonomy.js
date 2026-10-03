/* How much the open agent decides on its own — set from the composer.
 *
 * The same setting as the agent profile's Persona tab, reached from where you
 * are when it matters: about to ask for something, deciding whether you want
 * to be asked back. **One endpoint and one vocabulary** — both this and the
 * profile read the levels from `GET /api/agents/{id}/persona` and write with
 * `PUT`, so a mode set here and a mode set there cannot disagree about what
 * the words mean or which one is on.
 *
 * It is per agent, like the setting it edits. Switching agents re-reads it,
 * because "read only" belongs to the agent you set it on and showing it over a
 * different one would be a label about somebody else.
 */

//: The levels as the server describes them, and what the open agent is on.
//: Fetched per agent rather than cached across them — this is three rows of
//: JSON, and a stale level on the wrong agent is the one failure that matters.
let autoLevels = [];
let autoCurrent = "";
let autoFor = "";

//: One icon per level, in the order they escalate. `IC` has no icon for "this
//: agent decides for itself", so the three are borrowed to read as a scale:
//: locked, watched, let go.
const AUTO_IC = { read_only: "lock", ask_first: "shield", on_its_own: "bolt" };

function closeAutonomyMenu() {
  const menu = $("#cmpAutoMenu");
  const pill = $("#cmpAutoPill");
  if (menu) menu.hidden = true;
  if (pill) pill.classList.remove("is-active");
}

/** Draw the pill for whatever is current, or hide it when we know nothing. */
function renderAutonomyPill() {
  const pill = $("#cmpAutoPill");
  if (!pill) return;
  const level = autoLevels.find((l) => l.key === autoCurrent);
  if (!level) { pill.hidden = true; return; }
  pill.hidden = false;
  const ic = $("#cmpAutoIc");
  if (ic) ic.innerHTML = IC[AUTO_IC[level.key]] || "";
  const label = $("#cmpAutoLabel");
  if (label) label.textContent = level.label;
  // The blurb is the whole explanation of what the agent will now do without
  // asking, so it goes on the control rather than only in the menu.
  pill.title = `${level.label} — ${level.blurb}`;
  pill.classList.toggle("is-free", level.key === "on_its_own");
  pill.classList.toggle("is-locked", level.key === "read_only");
}

function renderAutonomyMenu() {
  const menu = $("#cmpAutoMenu");
  if (!menu) return;
  menu.textContent = "";
  for (const level of autoLevels) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "cmp-auto-row" + (level.key === autoCurrent ? " is-on" : "");
    row.setAttribute("role", "menuitemradio");
    row.setAttribute("aria-checked", String(level.key === autoCurrent));
    row.innerHTML =
      `<span class="cmp-auto-name">${esc(level.label)}</span>
       <span class="cmp-auto-blurb">${esc(level.blurb)}</span>`;
    row.onclick = () => setAutonomy(level.key);
    menu.appendChild(row);
  }
}

/** Read the open agent's level. Called on every agent switch. */
async function loadAutonomy(agentId) {
  const pill = $("#cmpAutoPill");
  if (!agentId) { if (pill) pill.hidden = true; return; }
  autoFor = agentId;
  try {
    const got = await api(`/api/agents/${encodeURIComponent(agentId)}/persona`);
    // The user may have moved on while this was in flight; a level painted
    // over a different agent is a label about somebody else.
    if (autoFor !== agentId) return;
    autoLevels = (got.vocabulary && got.vocabulary.autonomy) || [];
    autoCurrent = (got.persona && got.persona.autonomy)
      || (got.vocabulary && got.vocabulary.default_autonomy) || "";
  } catch (_) {
    // A pill that cannot say which mode the agent is in should not be on
    // screen guessing. Hidden is honest; "Mode" is not.
    autoLevels = []; autoCurrent = "";
  }
  renderAutonomyPill();
  renderAutonomyMenu();
}

async function setAutonomy(key) {
  const agentId = autoFor;
  if (!agentId || key === autoCurrent) { closeAutonomyMenu(); return; }
  const was = autoCurrent;
  // Moved before the request and put back if it fails: the menu closes on the
  // press, so leaving the old label up until the server answers reads as a
  // press that did nothing.
  autoCurrent = key;
  renderAutonomyPill();
  closeAutonomyMenu();
  try {
    await api(`/api/agents/${encodeURIComponent(agentId)}/persona`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ autonomy: key }),
    });
  } catch (e) {
    autoCurrent = was;
    renderAutonomyPill();
    toast(String(e));
    return;
  }
  const level = autoLevels.find((l) => l.key === key);
  toast(level ? `${level.label} — ${level.blurb}` : "Saved");
  renderAutonomyMenu();
  // Read only takes the changing tools away and leaving it puts them back, so
  // anything showing this agent's permissions is now showing something other
  // than what is true. `AGENT_TOOLS_FOR` is what makes the panel repaint
  // rather than skip as "same agent".
  if (typeof AGENT_TOOLS_FOR !== "undefined") AGENT_TOOLS_FOR = "";
  // And the profile, if it happens to be open on this agent.
  if (typeof profAgentId !== "undefined" && profAgentId === agentId
      && typeof renderProfilePane === "function") {
    renderProfilePane();
  }
}

{
  const pill = $("#cmpAutoPill");
  if (pill) {
    pill.onclick = (e) => {
      e.stopPropagation();
      const menu = $("#cmpAutoMenu");
      if (!menu) return;
      const open = !menu.hidden;
      menu.hidden = open;
      pill.classList.toggle("is-active", !open);
    };
  }
  // Click away and Escape both close it, the way the model picker does.
  document.addEventListener("click", (e) => {
    if (!e.target.closest || !e.target.closest(".cmp-auto-wrap")) {
      closeAutonomyMenu();
    }
  });
  window.addEventListener("keydown", (e) => {
    const menu = $("#cmpAutoMenu");
    if (e.key === "Escape" && menu && !menu.hidden) {
      e.stopPropagation();
      closeAutonomyMenu();
    }
  });
}
