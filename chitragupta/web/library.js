/* Agent Library — what Chitragupta offers, and who the user has taken on.
 *
 * Four agents used to be *the* agents: all present, none chosen. This screen is
 * the other half of `agents/library.py` — a shelf per kind of work, a card per
 * specialist, and Add / In your roster as the only two states a card can be
 * in — a state, not a toggle. Taking an agent off the team is a decision made
 * in its own profile, beside the permanent one it has to be told apart from.
 *
 * Two things on a card are not decoration and must not be dropped:
 *
 *   • what it needs and has not got — the card says "connect Gmail first"
 *     rather than adding an agent that will quietly do nothing;
 *   • what it can do to the machine — an agent that runs code or touches files
 *     says so BEFORE it is added, because adding it is the consent.
 *
 * A function declaration for `openLibrary`, not a const arrow: `app.js` calls it
 * from a nav handler defined above this file in load order, and a temporal-dead-
 * zone ReferenceError there is exactly what blanked the model drawer once.
 */

let LIB = { categories: [], templates: [] };
let libCat = "All";
let libQuery = "";

/* Source names as a person says them, not as we key them. */
const SOURCE_LABELS = {
  gmail: "Gmail", gcal: "Google Calendar", gdrive: "Google Drive",
  notion: "Notion", github: "GitHub", linear: "Linear", notes: "Notes",
  imessage: "Messages", apple_mail: "Apple Mail",
  apple_calendar: "Apple Calendar", files: "Folders you choose",
};
const label = (s) => SOURCE_LABELS[s] || s;

function openLibrary() {
  const screen = $("#libraryScreen");
  if (!screen) return;
  screen.hidden = false;
  loadLibrary();
}

function closeLibrary() {
  const screen = $("#libraryScreen");
  if (screen) screen.hidden = true;
}

async function loadLibrary() {
  try {
    LIB = await api("/api/agents/library");
  } catch (e) {
    toast("Could not open the library: " + e);
    return;
  }
  renderLibCats();
  renderLibrary();
}

function renderLibCats() {
  const box = $("#libCats");
  if (!box) return;
  const used = ["All", ...LIB.categories.filter(
    (c) => LIB.templates.some((t) => t.category === c))];
  box.innerHTML = "";
  for (const name of used) {
    const b = document.createElement("button");
    b.className = "lib-cat" + (name === libCat ? " is-on" : "");
    b.textContent = name;
    b.onclick = () => { libCat = name; renderLibCats(); renderLibrary(); };
    box.appendChild(b);
  }
}

function libMatches(t) {
  if (libCat !== "All" && t.category !== libCat) return false;
  if (!libQuery) return true;
  const hay = `${t.name} ${t.role} ${t.description} ${t.category}`.toLowerCase();
  return hay.includes(libQuery);
}

/**
 * The agents the user built and then retired.
 *
 * Not templates — nothing can be built *from* one — so they are their own
 * shelf rather than cards mixed into the catalogue, and the button restores
 * the agent that was put away rather than making a fresh copy of it. That
 * distinction is the whole reason retiring is not deleting: the memory, the
 * persona and the conversation are still there and come back with it.
 */
function renderRetired() {
  const sec = $("#libRetiredSec");
  const box = $("#libRetired");
  if (!sec || !box) return;
  const rows = LIB.retired || [];
  sec.hidden = rows.length === 0;
  box.textContent = "";
  for (const r of rows) {
    const card = document.createElement("div");
    card.className = "lib-retired-card";

    const face = document.createElement("span");
    face.className = "lib-retired-orb";
    card.appendChild(face);
    paintAvatar(face, r.id, { size: 40, title: r.name });

    const meta = document.createElement("div");
    meta.className = "lib-retired-meta";
    meta.innerHTML = `<div class="lib-retired-name">${esc(r.name)}</div>
                      <div class="lib-retired-role">${esc(r.role || "")}</div>`;
    card.appendChild(meta);

    const btn = document.createElement("button");
    btn.className = "tiny";
    btn.textContent = "Bring it back";
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        await api(`/api/agents/custom/${encodeURIComponent(r.id)}/restore`,
                  { method: "POST" });
      } catch (e) { toast(errText(e)); btn.disabled = false; return; }
      toast(`${r.name} is back on your team`);
      await loadLibrary();
      loadAgents();
    };
    card.appendChild(btn);
    box.appendChild(card);
  }
}

function renderLibrary() {
  renderRetired();
  const grid = $("#libGrid");
  if (!grid) return;
  const shown = LIB.templates.filter(libMatches);

  const count = $("#libCount");
  if (count) {
    count.textContent = `${LIB.templates.length} agent template` +
      (LIB.templates.length === 1 ? "" : "s");
  }
  const empty = $("#libEmpty");
  if (empty) empty.hidden = shown.length > 0;

  grid.innerHTML = "";
  for (const t of shown) grid.appendChild(libCard(t));
}

function libCard(t) {
  const el = document.createElement("article");
  el.className = "lib-card" + (t.in_roster ? " is-mine" : "");

  const head = document.createElement("div");
  head.className = "lib-card-head";
  const orb = document.createElement("span");
  orb.className = "lib-orb";
  // The same character the agent rail draws (paintAvatar, core.js), so a card
  // and a sidebar row read as the same agent rather than two things sharing a
  // name. Static: the Library is a grid of dozens, and dozens of live instances
  // would all be following the pointer behind a screen you are scrolling.
  head.appendChild(orb);
  paintAvatar(orb, t.id, { size: 48, title: t.name });

  const titles = document.createElement("div");
  titles.innerHTML =
    `<div class="lib-cat-tag">${esc(t.category)}</div>` +
    `<div class="lib-name">${esc(t.name)}</div>`;
  head.appendChild(titles);
  el.appendChild(head);

  const desc = document.createElement("p");
  desc.className = "lib-desc";
  desc.textContent = t.description;
  el.appendChild(desc);

  if (t.works_with.length) {
    const works = document.createElement("div");
    works.className = "lib-works";
    works.textContent = "Works with " +
      t.works_with.map(label).join(" · ");
    el.appendChild(works);
  }

  // What it can do to the machine, said before it is added.
  const powers = [];
  if (t.runs_code) powers.push("runs code on your Mac");
  if (t.touches_files) powers.push("reads and writes in folders you choose");
  if (t.forgets_facts) powers.push("can retract things from your brain");
  if (powers.length) {
    const p = document.createElement("div");
    p.className = "lib-powers";
    p.textContent = powers.join(" · ");
    el.appendChild(p);
  }

  const foot = document.createElement("div");
  foot.className = "lib-foot";

  if (t.missing && t.missing.length) {
    // Never offer a control that cannot work: say what is missing instead.
    const warn = document.createElement("span");
    warn.className = "lib-missing";
    // "Connect Folders you choose first" is not a sentence anyone would say.
    // A folder is opened, not connected, and the two read differently.
    warn.textContent = t.missing.every((m) => m === "files")
      ? "Open a folder for it first"
      : "Connect " + t.missing.filter((m) => m !== "files").map(label).join(" and ")
        + " first";
    foot.appendChild(warn);
  }

  // **The Library adds; it never takes away.** This was a toggle reading
  // "In your roster · Remove", which did the same thing the agent profile
  // calls Retire — one act, two names, two screens, and only one of them said
  // what was kept. Taking an agent off the team is now a decision made in its
  // own profile, next to the other one it has to be told apart from.
  if (t.in_roster) {
    const state = document.createElement("span");
    state.className = "lib-state";
    state.textContent = "In your roster";
    foot.appendChild(state);
  } else {
    const btn = document.createElement("button");
    btn.className = "lib-btn";
    btn.textContent = "Add agent →";
    btn.onclick = () => addToRoster(t, btn);
    foot.appendChild(btn);
  }
  el.appendChild(foot);
  return el;
}

async function addToRoster(t, btn) {
  btn.disabled = true;
  btn.textContent = "Adding…";
  try {
    await api("/api/agents/roster", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: t.id }),
    });
    toast(`${t.name} added to your agents`);
    await loadLibrary();
    if (typeof loadAgents === "function") await loadAgents();
  } catch (e) {
    toast(errText(e));
    btn.textContent = "Add agent →";
  } finally {
    btn.disabled = false;
  }
}

{
  const close = $("#libClose");
  if (close) close.onclick = closeLibrary;
  const search = $("#libSearch");
  if (search) {
    search.addEventListener("input", () => {
      libQuery = search.value.trim().toLowerCase();
      renderLibrary();
    });
  }
  window.addEventListener("keydown", (e) => {
    const screen = $("#libraryScreen");
    if (e.key === "Escape" && screen && !screen.hidden) closeLibrary();
  });
}
