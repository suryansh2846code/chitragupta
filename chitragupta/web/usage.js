/**
 * What a turn costs, and how the background enrichment is going.
 *
 * The token meter on the AI model screen, and the enrichment job's progress.
 *
 * **The enrichment job runs on the server, not here.** Starting it from the
 * page and tracking it in a variable meant a refresh killed the work; the page
 * asks `/api/brain/enrich/status` and reconnects to whatever is already
 * running. That is also why progress survives being closed and reopened.
 *
 * Usage is shown because the model runs on the *user's* key or subscription,
 * and spending someone's money without showing them the meter is not a choice
 * they got to make. Providers that cost nothing (`FREE_PROVIDERS`) say so
 * instead of showing a number that would only ever be zero.
 */

// queue drains, showing live progress. General across any connector's content.
let _enriching = false;
function fmtTokens(n) { return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n); }
function fmtEta(sec) { sec = Math.round(sec); if (sec < 60) return sec + "s"; const m = Math.round(sec / 60); return m < 60 ? m + "m" : Math.round(m / 60) + "h"; }
const ENRICH_TIPS = [
  "Entities are pulled from your emails, docs, notes & calendar.",
  "Click any entity in the list to see the facts behind it.",
  "Bounce emails, boilerplate & encoded junk are filtered out.",
  "The model types every entity — person, org, project or tool.",
  "Extraction is simple — a small model (Haiku 4.5, gpt-5.4-mini) is plenty.",
  "Local models (Ollama) or your Claude subscription do this for free.",
  "It runs batch by batch — you can Stop anytime and resume later.",
];
// providers with no per-token API cost (free to enrich with)
const FREE_PROVIDERS = { ollama: 1, "claude-code": 1, subscription: 1, mock: 1 };
// ── model token usage (persisted server-side) ──────────────────────────────
async function updateUsage() {
  const box = $("#usageBox"); if (!box) return;
  let u; try { u = await api("/api/usage"); } catch (e) { return; }
  const a = u.active || {}, tin = a.in || 0, tout = a.out || 0, calls = a.calls || 0;
  const local = u.locality === "local";
  const limit = local ? "local · no API limit"
    : (u.context_window ? `~${Math.round(u.context_window / 1000)}K context/call` : "—");
  box.innerHTML = `
    <div class="usage-row"><span>Active model</span><b>${esc(u.active_provider || "—")}${a.model ? " · " + esc(a.model) : ""}</b></div>
    <div class="usage-row"><span>Tokens used</span><b>${(tin + tout).toLocaleString()}</b></div>
    <div class="usage-row"><span>In / Out</span><b>${tin.toLocaleString()} / ${tout.toLocaleString()}</b></div>
    <div class="usage-row"><span>Calls</span><b>${calls.toLocaleString()}</b></div>
    <div class="usage-row"><span>Limit</span><b>${limit}</b></div>`;
}
{ const rb = $("#usageReset"); if (rb) rb.onclick = async () => {
  try { await api("/api/usage/reset", { method: "POST" }); updateUsage(); } catch (e) {}
}; }

// Enrichment runs SERVER-SIDE (a background job) — the UI just starts/stops it and
// polls status, so a refresh reconnects to the running job instead of killing it.
let _enrichPoll = null, _enrichTip = 0;
function renderEnrich(s) {
  const eb = $("#enrichBtn"), panel = $("#enrichPanel"), bar = panel && panel.querySelector(".ep-bar");
  const fill = $("#epFill"), state = $("#epState"), pctEl = $("#epPct"),
        meta = $("#epMeta"), foundEl = $("#epFound"), tipEl = $("#epTip");
  if (panel) panel.hidden = false;
  const LOCAL = { ollama: 1, "claude-code": 1, subscription: 1, mock: 1 };
  const total = (s.processed || 0) + (s.remaining || 0);
  const pct = total ? Math.min(100, Math.round(s.processed / total * 100)) : (s.running ? 0 : 100);
  if (fill) fill.style.width = pct + "%"; if (pctEl) pctEl.textContent = pct + "%";
  if (eb) eb.style.setProperty("--p", pct + "%");
  if (s.running) {
    if (eb) { eb.classList.add("running"); eb.textContent = `Stop · ${pct}%`; }
    if (bar) bar.classList.add("working");
    if (state) { state.textContent = `Enriching with ${s.provider || "AI"}`; state.classList.remove("done"); }
    if (tipEl) tipEl.textContent = "Tip — " + ENRICH_TIPS[_enrichTip++ % ENRICH_TIPS.length];
  } else {
    if (eb) { eb.classList.remove("running"); eb.style.setProperty("--p", "0%"); eb.textContent = "Enrich with AI"; }
    if (bar) bar.classList.remove("working");
    if (state) {
      state.textContent = (s.remaining === 0 && s.processed > 0) ? `Fully enriched · +${s.entities} entities`
        : s.processed > 0 ? `Stopped · +${s.entities} entities · +${s.facts} facts` : "Ready to enrich";
      state.classList.add("done");
    }
  }
  const rate = s.elapsed > 0 ? s.processed / s.elapsed : 0;
  const eta = s.running && s.remaining > 0 && rate > 0 ? ` · ~${fmtEta(s.remaining / rate)} left` : "";
  const tok = LOCAL[s.provider] ? "local · free"
    : (s.tokens ? `${fmtTokens(s.tokens_in)} in · ${fmtTokens(s.tokens_out)} out${s.estimated ? " (est)" : ""}` : "");
  const capNote = s.cap ? ` · recent ${s.cap}/bulk source` : "";
  if (meta) meta.innerHTML =
    `${(s.processed || 0).toLocaleString()} of ${total.toLocaleString()} memories${capNote} · +${s.entities || 0} entities · +${s.facts || 0} facts${eta}` + (tok ? `<br>${tok}` : "");
  if (foundEl && s.found && s.found.length)
    foundEl.innerHTML = s.found.map((f) => `<span class="ep-chip ${esc(f.type || "")}">${esc(f.name)}</span>`).join("");
}
function pollEnrich() {
  clearInterval(_enrichPoll);
  const tick = async () => {
    let s; try { s = await api("/api/brain/enrich/status"); } catch (e) { return; }
    renderEnrich(s); loadBrain(); _bsRefresh(); updateUsage();
    if (!s.running) { clearInterval(_enrichPoll); _enrichPoll = null; }
  };
  tick(); _enrichPoll = setInterval(tick, 2000);
}
{ const eb = $("#enrichBtn"); if (eb) eb.onclick = async () => {
  if (eb.classList.contains("running")) {
    eb.textContent = "Stopping…";
    try { await api("/api/brain/enrich/stop", { method: "POST" }); } catch (e) {}
    return;
  }
  // Warn before spending real tokens on a big queue with a metered cloud model.
  const em = enrichModel();
  const prov = em.provider;
  if (!FREE_PROVIDERS[prov]) {
    let remaining = 0;
    try { remaining = (await api("/api/brain/enrich/status")).remaining || 0; } catch (_) {}
    if (remaining > 150) {
      const estTok = remaining * 500;   // rough: ~0.5k tokens per memory
      const ok = confirm(
        `Enrich ~${remaining.toLocaleString()} memories with “${prov}” (a metered cloud model)?\n\n`
        + `This can use a LOT of tokens — very roughly ~${fmtTokens(estTok)} — and may cost money.\n\n`
        + `Extraction is a simple task, so this is a better fit for a FREE model:\n`
        + `  • a local model (Ollama), or your Claude subscription/CLI — free\n`
        + `  • or a small cheap model (Haiku 4.5, gpt-5.4-mini)\n\n`
        + `You can Stop anytime. Continue with ${prov}?`);
      if (!ok) return;
    }
  }
  eb.classList.add("running"); eb.textContent = "Starting…"; eb.style.setProperty("--p", "3%");
  if ($("#enrichPanel")) $("#enrichPanel").hidden = false;
  if ($("#epTip")) $("#epTip").textContent = "Tip — " + ENRICH_TIPS[0];
  try {
    await api("/api/brain/enrich/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: em.provider, model: em.model }) });
  } catch (e) {
    const st = $("#epState"); if (st) { st.textContent = "Couldn't start — check Model."; st.classList.add("done"); }
    eb.classList.remove("running"); eb.textContent = "Enrich with AI"; return;
  }
  pollEnrich();
}; }
// resume on load: if the server job is running, reconnect the UI
(async () => { try { const s = await api("/api/brain/enrich/status"); if (s.running) { pollEnrich(); } } catch (_) {} })();
$("#bsSync").onclick = async () => {
  try { const r = await api("/api/sync/now", { method: "POST" });
    toast(r.started ? "syncing your sources…" : (r.reason || "already syncing"));
    _bsRefresh(); updateBrainStatus();
  } catch (e) { toast(errText(e)); }
};
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#brainScreen").hidden) closeBrainScreen(); });
