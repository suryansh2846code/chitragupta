/**
 * The workspace shell: state, chrome, the agent rail, the drawers, the keyboard
 * shortcuts, and boot.
 *
 * This file was the whole frontend — 3,581 lines. It is the last piece now, and
 * everything it used to hold sits beside it, loaded by `index.html` in this
 * order, all in one shared global scope:
 *
 *   core.js          $ api esc md toast, the orb palette, the icon set
 *   providers.js     the catalog and its state, sign-in, the provider cards
 *   models.js        the model picker, agent bindings, loadProviders()
 *   chat.js          sending a turn, and everything that renders one
 *   brain.js         the brain panel, sync, Google, entity and search modals
 *   connectors.js    adding a source, and setting one up
 *   workspace.js     approvals, tasks, reminders, routines, first-run
 *   brain-screen.js  the full-screen canvas view
 *   usage.js         the token meter and enrichment progress
 *   tools.js         the Tools & skills panel
 *   app.js           this file
 *
 * **Plain scripts, not modules.** The test harnesses evaluate the frontend with
 * `new Function`, which compiles a script and cannot process `import`, and
 * several of them reach into this scope to install a fixture. The order above
 * is declared once, in `index.html`, and both the browser and the harnesses
 * read it from there — see docs/development/frontend-testing.md.
 *
 * Load order is the dependency graph. `core.js` goes first because `esc` and
 * `md` have ~119 call sites, and a `const` read before its definition is a
 * temporal dead-zone ReferenceError that `node --check` passes and that has
 * blanked a screen here twice.
 */
let current = null;
let agents = [];
let CONNECTORS = [];

function applyIcons() {
  document.querySelectorAll(".snav").forEach((b) => {
    const el = b.querySelector(".snav-ic"); if (!el) return;
    const key = b.id === "helpBtn" ? "help"
      : b.dataset.nav === "sources" ? "connectors"
      : b.dataset.nav;
    el.innerHTML = IC[key] || "";
  });
  document.querySelectorAll(".ms-nav-item").forEach((b) => {
    const el = b.querySelector(".ms-nav-ic"); if (!el) return;
    el.innerHTML = IC[b.dataset.msnav] || "";
  });
  const set = (id, name) => { const e = $(id); if (e) e.innerHTML = IC[name]; };
  set("#attachBtn", "attach"); set("#micBtn", "mic"); set("#send", "arrowUp");
}

// collapse / expand the sidebar (persisted)
function setCollapsed(on) {
  const app = document.querySelector(".app"); if (!app) return;
  app.classList.toggle("collapsed", on);
  const b = $("#collapseBtn");
  if (b) {
    b.textContent = on ? "›" : "‹";
    b.title = on ? "Expand sidebar" : "Collapse sidebar";
    b.setAttribute("aria-label", b.title);
    b.setAttribute("aria-expanded", on ? "false" : "true");
  }
  try { localStorage.setItem("ls_collapsed", on ? "1" : ""); } catch (_) {}
}
{
  const b = $("#collapseBtn");
  if (b) b.onclick = () => setCollapsed(!document.querySelector(".app").classList.contains("collapsed"));
  setCollapsed(localStorage.getItem("ls_collapsed") === "1");
}
function agentOrbId(a) { return a ? a.id : ""; }
function agentDesc(a) {
  if (!a) return "";
  return "Helps you with " + (a.role || "your work") + ".";
}


// Skeletons (index.html) are normally cleared by whatever renders over them —
// `loadAgents()` and `renderHistory()` both assign innerHTML. Two paths never
// reach a render and would shimmer for ever: a first run with no agents, where
// nothing is selected so no history is ever fetched, and a boot that throws.
// Both call this, so "still loading" can never be what a stuck screen says.
function clearSkeletons() {
  const box = $("#messages");
  if (box) {
    box.querySelectorAll(".sk-thread").forEach((n) => n.remove());
    box.removeAttribute("aria-busy");
  }
  const rail = $("#agentList");
  if (rail) {
    rail.querySelectorAll(".sk-agent").forEach((n) => n.remove());
    rail.removeAttribute("aria-busy");
  }
  // Back to the placeholder the header shipped with, not to blank.
  const nm = $("#agentName");
  if (nm && nm.querySelector(".sk")) nm.textContent = "—";
  const rl = $("#agentRole");
  if (rl && rl.querySelector(".sk")) rl.textContent = "";
}

async function loadAgents() {
  const d = await api("/api/agents");
  agents = d.agents;
  $("#agentList").removeAttribute("aria-busy");
  if (!agents.length) {
    // Nothing ships pre-added, so an empty rail is a real first run — not an
    // error. It has to lead somewhere rather than just being blank.
    $("#agentList").innerHTML =
      `<div class="agents-empty">
         <p>No agents yet.</p>
         <button type="button" id="emptyToLibrary" class="tiny">Browse the Agent Library</button>
       </div>`;
    const go = $("#emptyToLibrary");
    if (go) go.onclick = () => { if (typeof openLibrary === "function") openLibrary(); };
    clearSkeletons();   // no agent to select, so no history is coming
    return;
  }
  $("#agentList").innerHTML = agents.map((a) => {
    return `
    <div class="agent ${a.id === current ? "active" : ""}" data-id="${a.id}"
         role="button" tabindex="0" aria-pressed="${a.id === current}"
         aria-label="${esc(a.name)} — ${esc(a.role)}">
      <span class="orb"></span>
      <div class="a-meta">
        <div class="n">${esc(a.name)}</div>
        <div class="r">${esc(a.role)}</div>
      </div>
      ${a.custom ? `<button type="button" class="del-agent" data-del-agent="${a.id}" aria-label="Delete agent ${esc(a.name)}">${IC.close}</button>`
        : (a.id === current ? `<span class="dot"></span>` : "")}
    </div>`; }).join("");
  document.querySelectorAll(".agent").forEach((el) => {
    // Mounted after the markup, never inside it: a character is a live instance
    // with a pointer subscription and an observer, and an `innerHTML` template
    // can only produce a string. Painting here also means the rail's own
    // template stays readable — the alternative was an SVG document inlined
    // into the middle of it.
    const orb = el.querySelector(".orb");
    const agent = agents.find((a) => a.id === el.dataset.id);
    if (orb) paintAvatar(orb, agentOrbId(agent), { live: true, title: agent ? agent.name : "" });

    el.onclick = (e) => {
      if (e.target.closest("[data-del-agent]")) return;   // handled below
      selectAgent(el.dataset.id);
    };
    // role="button" is a promise that Enter and Space work. Keep it.
    el.onkeydown = (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      if (e.target.closest("[data-del-agent]")) return;
      e.preventDefault();
      selectAgent(el.dataset.id);
    };
  });
  document.querySelectorAll("[data-del-agent]").forEach((el) => el.onclick = async (e) => {
    e.stopPropagation();
    if (!confirm("Delete this agent?")) return;
    await api(`/api/agents/custom/${el.dataset.delAgent}`, { method: "DELETE" });
    if (current === el.dataset.delAgent) current = null;
    toast("Agent deleted"); loadAgents();
  });
  if (!current && agents.length) selectAgent(agents[0].id);
}


// ── the left nav ────────────────────────────────────────────────────────────
// Every destination is a screen now. The slide-over drawer is gone: `tasks`
// moved into Inbox, beside the other three kinds of pending work, and `tools`
// was a read-only copy of the Agents & tools panel — same endpoint, same
// grouping, no switches, and the untruncated descriptions that panel already
// fixed. Those two were the drawer's only panels.
function openDrawer(name) {
  if (name === "model" || name === "settings") return openModelScreen();
  if (name === "sources") return openConnectorsScreen();
  if (name === "tools") return openToolsScreen();
  if (name === "inbox" || name === "tasks") return openInboxScreen();
  if (name === "brain") return openBrainScreen();
  if (name === "library") return openLibrary();
}
document.querySelectorAll(".snav").forEach((b) => b.onclick = () => {
  if (b.id === "helpBtn") { window.location.href = "/onboarding?replay=1"; return; }  // re-experience onboarding (won't wipe)
  openDrawer(b.dataset.nav);
});
{ const nr = $("#newAgentRow"); if (nr) nr.onclick = () => $("#newAgentBtn").click(); }
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#modelScreen") && !$("#modelScreen").hidden) closeModelScreen(); });

// Cmd/Ctrl+R → refresh the workspace in place (picks up new code — assets are
// served no-cache). The desktop app runs in a webview where the browser's reload
// shortcut isn't wired, so we bind it ourselves. Note: it reloads "/", NOT the
// onboarding — a reload here should never restart onboarding.
window.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && (e.key === "r" || e.key === "R" || e.code === "KeyR")) {
    e.preventDefault();
    window.location.reload();
  }
}, true);

(async () => {
  try {
    if (await maybeOnboard()) return;   // redirecting to onboarding — stop here
    applyIcons();
    await loadProviders();
    // Awaited, and before `loadAgents()`: the rail paints an avatar per agent,
    // and a custom one that arrives after that paint is a visible flicker of the
    // wrong face on every launch. It is one small request and it cannot fail in
    // a way that blocks — `loadAgentAvatars` swallows its own errors, because an
    // agent with no override still has its generated character. That is also
    // why it sits inside the try but needs no catch of its own.
    await loadAgentAvatars();
    // loadAgents() gets its own catch: it is not awaited, so a rejection here
    // would never reach the handler below, and the rail it was going to fill is
    // the one still showing skeletons.
    loadAgents().catch((e) => {
      clearSkeletons();
      toast(typeof e === "string" ? e : "Could not load your agents");
    });
    loadBrain(); loadTasks(); loadReminders(); loadRoutines();
    updateBrainStatus();
    // No lead agent and nothing pre-added, so a new install has no agents at
    // all — the library is how you get one, and it opens itself once.
    libraryOnFirstRun();
    // poll the brain status often while it's building, and keep time-based panels fresh
    setInterval(updateBrainStatus, 5000);
    setInterval(() => { loadReminders(); loadRoutines(); }, 45000);
  } catch (e) {
    // The backend is unreachable or answered badly. Whatever the screen shows
    // now, it must not be a shimmer — a placeholder that never resolves reads
    // as a hang, and hides the fact that there is something to report.
    clearSkeletons();
    toast(typeof e === "string" ? e : "Could not reach the backend");
    throw e;
  }
})();


// ── dialogs: escape closes them, and focus goes in and comes back ──────────
// Every overlay here is opened by clearing .hidden on its background element,
// in about fifteen different places. Rather than edit fifteen call sites (and
// miss the sixteenth), watch the attribute itself: an overlay that becomes
// visible takes focus, and whatever opened it gets focus back on the way out.
// Escape closes the topmost one by clicking its own close button, so each
// modal's existing teardown still runs instead of being bypassed.
// #welcome is deliberately excluded: its only exit is "Skip for now", which
// writes a preference, and Escape must not quietly make that choice.
{
  const FOCUSABLE = [
    'button:not([disabled])', 'a[href]', 'input:not([type="hidden"])',
    'select', 'textarea', '[tabindex]:not([tabindex="-1"])',
  ].join(",");
  // .modal-bg is position:fixed, so offsetParent is always null — visibility
  // has to be read from the attribute and from whether it has a box at all.
  const shown = (el) => el && !el.hidden;
  const boxed = (el) => el.getClientRects().length > 0;
  const openers = new WeakMap();
  const modals = () => [...document.querySelectorAll(".modal-bg")].filter(shown);

  function focusInto(el) {
    const all = [...el.querySelectorAll(FOCUSABLE)].filter(boxed);
    // Prefer the first field over the first focusable. In DOM order the first
    // focusable is the ✕ in the header, so "Create an agent" would open with
    // focus on Close — technically focused, practically useless.
    const field = all.find((n) =>
      /^(INPUT|TEXTAREA|SELECT)$/.test(n.tagName) && n.type !== "hidden");
    const target = field || all[0];
    if (target) { target.focus({ preventScroll: true }); return; }
    el.setAttribute("tabindex", "-1");
    el.focus({ preventScroll: true });
  }

  const watch = new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.attributeName !== "hidden") continue;
      const el = m.target;
      if (shown(el)) {
        openers.set(el, document.activeElement);
        focusInto(el);
      } else {
        const back = openers.get(el);
        openers.delete(el);
        if (back && back.isConnected && boxed(back)) back.focus({ preventScroll: true });
      }
    }
  });
  document.querySelectorAll(".modal-bg").forEach((el) =>
    watch.observe(el, { attributes: true, attributeFilter: ["hidden"] }));

  window.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = modals();
    const top = open[open.length - 1];   // last in the DOM is the one on top
    if (!top) return;
    e.preventDefault(); e.stopPropagation();
    const close = top.querySelector(".modal-head button, .modal-head .ghost");
    if (close) close.click(); else top.hidden = true;
  });
}
