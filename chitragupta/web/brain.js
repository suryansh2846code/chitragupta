/**
 * The brain panel: what is in it, where it came from, and searching it.
 *
 * Memory and entity counts, the connector list and its sync state, the Google
 * connection card, and the entity/search modals.
 *
 * **Google sources are shown as disconnected until a real sign-in.** The app
 * ships an OAuth client, which makes the connector look configured before the
 * user has consented to anything — `/api/google/status` overrides the bundled
 * client's `ready`, and `renderGoogleCard` renders that answer rather than the
 * connector's own.
 *
 * Everything user-supplied here is escaped before it reaches `innerHTML`:
 * memory text, entity names and connector names all come from the user's own
 * sources, which is to say from whoever emailed them.
 */

async function loadBrain() {
  const s = await api("/api/brain/stats");
  $("#brainStats").innerHTML = `
    <div class="b"><div class="num">${s.total}</div><div class="lbl">memories</div></div>
    <div class="b"><div class="num">${s.graph.entities}</div><div class="lbl">entities</div></div>
    <div class="b"><div class="num">${s.graph.relations}</div><div class="lbl">facts</div></div>`;
  if ($("#ctxMem")) $("#ctxMem").textContent = s.total > 0 ? "On" : "Empty";
  if ($("#ctxEntities")) $("#ctxEntities").textContent = (s.graph.entities || 0).toLocaleString();
  const { entities } = await api("/api/brain/entities?limit=20");
  $("#entities").innerHTML = entities.length ? entities.map((e) =>
    `<div class="ent" data-entity="${e.id}" title="click for facts"><span class="etype ${e.type}">${e.type}</span> ${esc(e.name)} <span class="t">${e.mentions}×</span></div>`).join("")
    : `<div class="ent t">empty — add notes or sync a connector</div>`;
  document.querySelectorAll("[data-entity]").forEach((el) =>
    el.onclick = () => openEntity(el.dataset.entity));
  const { connectors } = await api("/api/connectors");
  CONNECTORS = connectors;
  if ($("#ctxSources")) $("#ctxSources").textContent = connectors.filter((c) => c.ready).length;
  const staleAfterMin = Math.max(120, (SYNC_INTERVAL_MIN || 30) * 4);
  // **What each source is actually doing, from the server.** The row used to
  // work this out itself from `last_sync` and a threshold, which let it say
  // exactly three things: Connected, Stale, off. The server can now say a
  // sign-in has run out, a service is rate-limiting us, a pass is running, or a
  // source is paused — each with a sentence naming what to do. Guessing from a
  // timestamp meant a user whose Gmail sign-in had expired read "Connected ·
  // last synced 12 Sep", which is true and useless.
  //
  // Fetched separately and tolerantly: this endpoint reads state the app
  // already holds and touches no service, but a source list that failed to
  // render because a health call hiccupped would be a worse screen than one
  // with less detail on it.
  let health = [];
  try {
    health = (await api("/api/connectors/health")).connectors || [];
  } catch (_) { health = []; }
  renderConnectors(connectors, staleAfterMin, health);
  loadSyncStatus();
  loadApprovals();
  // The sites agents may read are part of the same answer as the connectors:
  // what this app can reach on the user's behalf. Guarded because the list must
  // never stop the rest of the panel rendering.
  try { loadBrowserSites(); } catch (_) {}
  try { renderGoogleCard(await api("/api/google/status")); } catch (_) {}
}

// ── polished Google connect flow (local backend, Turnstone-grade UX) ────────
function renderGoogleCard(s) {
  const el = $("#googleCard");
  if (!el) return;
  if (!s.client_configured) { el.hidden = true; return; }
  el.hidden = false;
  if (s.connected) {
    const chips = (s.services || []).map((x) => `<span class="gchip">${esc(x)}</span>`).join("");
    el.className = "google-card connected";
    el.innerHTML =
      `<div class="gc-row"><span class="gc-ic">${IC.check}</span>
         <div class="gc-txt"><b>Google connected</b>
           <span class="gc-sub">${esc(s.account || "signed in")}</span></div>
         <button class="tiny ghost" id="gcDisconnect">Disconnect</button></div>
       <div class="gchips">${chips}</div>
       <div class="gc-note">Token stays on your Mac — not sent to any third party.</div>`;
    $("#gcDisconnect").onclick = disconnectGoogle;
  } else {
    el.className = "google-card";
    el.innerHTML =
      `<div class="gc-txt"><b>Connect Google</b>
         <span class="gc-sub">Gmail · Calendar · Drive — read-only</span></div>
       <button class="gsignin" id="gcConnect"><span class="g-logo">G</span>Sign in with Google</button>
       <div class="gc-note">One click. Token stays on your Mac.</div>`;
    $("#gcConnect").onclick = connectGoogle;
  }
}
async function connectGoogle() {
  const el = $("#googleCard");
  el.className = "google-card connecting";
  el.innerHTML =
    `<div class="gc-row"><span class="gc-spin"></span>
       <div class="gc-txt"><b>Waiting for approval…</b>
         <span class="gc-sub">Approve access in the browser window that opened.</span></div></div>
     <button class="tiny ghost" id="gcReopen">Reopen browser sign-in</button>`;
  const open = () => api("/api/google/reconnect", { method: "POST" }).catch(() => {});
  $("#gcReopen").onclick = open;
  await open();
  let tries = 0;
  const poll = setInterval(async () => {
    let g; try { g = await api("/api/google/status"); } catch { return; }
    if (g.connected) { clearInterval(poll); toast("Google connected"); renderGoogleCard(g); loadBrain(); }
    else if (++tries > 60) { clearInterval(poll); renderGoogleCard(g); toast("Didn't finish — try again"); }
  }, 3000);
}
async function disconnectGoogle() {
  if (!confirm("Disconnect Google? You can reconnect anytime.")) return;
  await api("/api/google/disconnect", { method: "POST" });
  toast("Google disconnected");
  renderGoogleCard(await api("/api/google/status")); loadBrain();
}

let SYNC_INTERVAL_MIN = 30;
/**
 * Which sources failed on the last sweep, named.
 *
 * `last_result` has carried the per-connector errors all along and nothing read
 * them, so the panel said "last 12:34" after a pass where every source failed.
 * A timestamp on its own is a claim that it worked, and a user whose brain quietly
 * stopped updating finds out days later — which is the failure the background
 * loop is most prone to (see `scheduler.py`).
 *
 * Keys starting with `_` are the sweep's own bookkeeping (`_cancelled`,
 * `_deduped`, `_graph`), not sources.
 */
function failedSources(result) {
  return Object.entries(result || {})
    .filter(([name, r]) => !name.startsWith("_") && r && r.errors && r.errors.length)
    .map(([name]) => name);
}
async function loadSyncStatus() {
  try {
    const s = await api("/api/sync/status");
    if (s.interval_minutes) SYNC_INTERVAL_MIN = s.interval_minutes;
    const last = s.last_run ? new Date(s.last_run).toLocaleTimeString() : "not yet";
    const failed = failedSources(s.last_result);
    // Named, not counted: "1 source failed" sends the user looking, and the
    // name is already on screen in the connector row that needs attention.
    const trouble = failed.length
      ? ` · ${failed.join(", ")} failed`
      : "";
    const el = $("#syncStatus");
    el.textContent = s.syncing ? "syncing now…"
      : (s.enabled ? `auto every ${s.interval_minutes}m · last ${last}${trouble}`
                   : "auto-sync off");
    el.classList.toggle("sync-trouble", !s.syncing && failed.length > 0);
    $("#syncAll").textContent = s.syncing ? "syncing…" : "sync all";
    $("#syncAll").disabled = !!s.syncing;
  } catch (_) {}
}
$("#syncAll").onclick = async () => {
  const r = await api("/api/sync/now", { method: "POST" });
  if (!r.started) { toast(r.reason || "already syncing"); return; }
  toast("syncing all connectors…");
  const poll = setInterval(async () => {
    const s = await api("/api/sync/status");
    loadSyncStatus();
    if (!s.syncing) { clearInterval(poll); toast("sync complete"); loadBrain(); }
  }, 3000);
};

//: Connectors with a sync in flight right now.
//:
//: The button is disabled too, but a `disabled` attribute is not the guard —
//: the row is re-rendered wholesale by `loadBrain()` on every refresh, and the
//: fresh button arrives enabled. This set survives that, so a sync that is
//: still running cannot be started a second time by clicking a repainted
//: button. A first Gmail pass can run for minutes, which is plenty of time.
const SYNCING = new Set();

//: The one poll watching whatever is syncing, so a second Sync press does not
//: start a second timer against the same job.
let SYNC_POLL = null;

/**
 * The parts of a connector row, looked up the way the browser will.
 *
 * **The selectors here were dead.** They looked for `.conn`, `.conn-sub` and
 * `.dot`; the row renders `.cn-row`, `.cn-sub` and a `.cn-logo` carrying
 * `data-state`. `.conn` survives only as a leftover CSS rule, so the lookup was
 * always null and — because every line used `?.` — all of the feedback silently
 * did nothing. `node --check` passes on that, which is why
 * `tests/js/connector_sync_feedback.mjs` clicks it instead.
 */
function _cnRowParts(name) {
  const row = document.querySelector(`.cn-row[data-conn="${CSS.escape(name)}"]`);
  return {
    row,
    sub: row?.querySelector(".cn-sub") || null,
    logo: row?.querySelector(".cn-logo") || null,
    btn: row?.querySelector(`[data-sync="${CSS.escape(name)}"]`) || null,
    stop: row?.querySelector(`[data-syncstop="${CSS.escape(name)}"]`) || null,
  };
}

/** Show one job's progress on its row. */
function _cnShowJob(job) {
  const parts = _cnRowParts(job.connector);
  if (parts.sub) parts.sub.textContent = job.says;
  if (parts.logo) {
    parts.logo.dataset.state = job.running ? "syncing"
      : job.state === "done" ? "ok" : "off";
  }
  if (parts.btn) parts.btn.disabled = Boolean(job.running);
  // Stop only exists while there is something to stop — "anything the user
  // starts, they can stop", and nothing they did not.
  if (parts.stop) parts.stop.hidden = !job.running;
}

/**
 * Follow whatever is syncing until it finishes.
 *
 * Started on load as well as after a press, which is what makes progress
 * **survive a refresh**: the job lives on the server, so a reloaded page finds
 * it and picks the count back up rather than showing a finished-looking row over
 * a sync that is still running.
 */
async function watchSyncJobs() {
  if (SYNC_POLL) return;
  const tick = async () => {
    let jobs = [];
    try {
      jobs = (await api("/api/connectors/jobs")).jobs || [];
    } catch (_) { return; }        // a hiccup must not end the watch
    let anyRunning = false;
    for (const job of jobs) {
      _cnShowJob(job);
      if (job.running) { anyRunning = true; SYNCING.add(job.connector); }
      else if (SYNCING.delete(job.connector)) {
        // Only announce a job this page was watching. Otherwise a reload
        // re-announces every sync of the last few minutes.
        toast(`${job.label}: ${job.says}`);
        loadBrain();
      }
    }
    if (!anyRunning) { clearInterval(SYNC_POLL); SYNC_POLL = null; }
  };
  SYNC_POLL = setInterval(tick, 1200);
  await tick();
}

async function syncConn(name) {
  if (name === "files") { openPicker(); return; }   // folder picker for local files

  if (SYNCING.has(name)) {
    toast(`${name} is already syncing`);
    return;
  }
  const parts = _cnRowParts(name);
  SYNCING.add(name);
  if (parts.sub) parts.sub.textContent = "Syncing…";
  if (parts.logo) parts.logo.dataset.state = "syncing";
  if (parts.btn) parts.btn.disabled = true;
  if (parts.stop) parts.stop.hidden = false;

  try {
    // **Started, not awaited to completion.** The synchronous route holds one of
    // six shared lane slots for the whole pass, and that is the lane this page
    // loads through — so a first Gmail sync could make the screen that started
    // it slow. `/sync/start` answers at once with a job to follow.
    const started = await api(`/api/connectors/${encodeURIComponent(name)}/sync/start`,
                             { method: "POST", body: { params: {} } });
    if (started?.job) _cnShowJob(started.job);
    watchSyncJobs();
  } catch (e) {
    SYNCING.delete(name);
    // A refusal carries a sentence naming which source holds the slot, so it is
    // shown rather than replaced with "could not sync".
    const said = String(e).replace(/^Error:\s*/, "");
    toast(said);
    if (parts.sub) parts.sub.textContent = said;
    if (parts.logo) parts.logo.dataset.state = "off";
    if (parts.btn) parts.btn.disabled = false;
    if (parts.stop) parts.stop.hidden = true;
  }
}

/** Stop a sync the user started. Cooperative — it lands within one record. */
async function stopSyncConn(name) {
  try {
    await api(`/api/connectors/${encodeURIComponent(name)}/sync/stop`,
              { method: "POST" });
  } catch (e) { toast(String(e)); return; }
  toast("stopping…");
}

// ── brain detail modal (entity facts / memory search) ──────────────────────
function openBrainModal(title, bodyHtml) {
  $("#bmTitle").textContent = title;
  $("#bmBody").innerHTML = bodyHtml;
  $("#brainModal").hidden = false;
}
$("#bmClose").onclick = () => $("#brainModal").hidden = true;
$("#brainModal").onclick = (e) => { if (e.target.id === "brainModal") $("#brainModal").hidden = true; };

async function openEntity(id) {
  const d = await api(`/api/brain/entities/${id}/facts`);
  const e = d.entity;
  const facts = d.facts.length
    ? d.facts.map((f) => `<li>${esc(f)}</li>`).join("")
    : "<li class='t'>no facts recorded</li>";
  openBrainModal(`${e.name}`,
    `<div class="bm-sub"><span class="etype ${e.type}">${e.type}</span> · ${e.mentions} mentions</div>
     ${e.summary ? `<p>${esc(e.summary)}</p>` : ""}
     <ul class="bm-facts">${facts}</ul>`);
}

async function searchBrain(q) {
  const d = await api(`/api/brain/search?q=${encodeURIComponent(q)}`);
  if (!d.memories.length) { openBrainModal(`Search: "${q}"`, `<p class="t">No matches.</p>`); return; }
  const rows = d.memories.map((m) =>
    `<div class="bm-mem">
       <div class="bm-mem-head"><b>${esc(m.title || m.source)}</b>
         <span><span class="bm-score">${m.score}</span>
         <button class="tiny ghost" data-delmem="${m.id}">${IC.close}</button></span></div>
       <div class="bm-mem-body">${esc(m.text)}</div>
     </div>`).join("");
  openBrainModal(`Search: "${q}"`, rows);
  document.querySelectorAll("[data-delmem]").forEach((b) => b.onclick = async () => {
    await api(`/api/brain/memories/${b.dataset.delmem}`, { method: "DELETE" });
    toast("Deleted"); b.closest(".bm-mem").remove(); loadBrain();
  });
}
$("#brainSearch").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.value.trim()) searchBrain(e.target.value.trim());
});
