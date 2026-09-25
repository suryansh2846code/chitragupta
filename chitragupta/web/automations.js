/* Automations: what state each one is in, and what a run actually did.
 *
 * The routine list already existed and still renders the rows. This adds the
 * two things a routine never had and an automation needs:
 *
 *   1. **A state a person can scan.** "Paused / Needs you / Running / Active"
 *      — the worst live thing about it, not its last run's verdict. A user
 *      looking down a list wants "this one needs you" to win over "and it also
 *      ran fine on Tuesday".
 *   2. **A run you can open.** The lifecycle, in order, with what was decided
 *      at each step. "Why did it do that" is the question an unattended system
 *      has to be able to answer, and the answer has to survive months.
 *
 * Deliberately no second list screen. An automation *is* a routine with more to
 * say, so it is the same row with more on it — two screens would mean two
 * places to pause something, and the one you use less is the one that drifts.
 */

//: Badge text and class per state. Keyed off what `/api/automations` computes,
//: so the words the API chose and the words the user reads cannot diverge.
const AUTO_STATES = {
  paused: ["Paused", "is-off"],
  waiting_for_approval: ["Needs you", "is-waiting"],
  needs_attention: ["Needs attention", "is-bad"],
  running: ["Running", "is-live"],
  active: ["", ""],
};

//: How a run's final state reads in history. `blocked` is deliberately not
//: "failed": the system correctly declined, nobody needs to fix anything, and
//: a user who sees enough red badges stops reading them.
const RUN_WORDS = {
  pending: "Starting", running: "Working", executing: "Doing it",
  waiting_for_approval: "Waiting for you", verifying: "Checking it landed",
  completed: "Done", retrying: "Retrying", blocked: "Did not apply",
  failed: "Failed", escalated: "Stopped — needs you", cancelled: "Stopped",
};

let AUTOMATIONS = [];

function autoWhen(iso) {
  if (!iso) return "";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  return when.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

/* Decorate the rows the routine list already drew.
 *
 * Additive on purpose: it reads `#routineList` after `loadRoutines()` has
 * filled it and adds to the rows it finds. If this file fails to load, the
 * list still works — which is the property that let it ship alongside the
 * older screen instead of replacing it. */
async function loadAutomationState() {
  try {
    const { automations } = await api("/api/automations");
    AUTOMATIONS = automations || [];
  } catch (_) { return; }

  AUTOMATIONS.forEach((a) => {
    const row = document.querySelector(`[data-toggle-r="${a.id}"]`);
    if (!row) return;
    const line = row.closest(".ib-row");
    if (!line) return;
    const [word, cls] = AUTO_STATES[a.state] || ["", ""];
    if (cls) line.classList.add(cls);

    const meta = line.querySelector(".ib-meta");
    if (meta) {
      const bits = [];
      if (a.next_run) bits.push(`Next ${esc(autoWhen(a.next_run))}`);
      if (a.waiting) bits.push(`${a.waiting} waiting for you`);
      if (bits.length) meta.innerHTML += ` · ${bits.join(" · ")}`;
    }
    const name = line.querySelector(".ib-name");
    if (name && word && a.state !== "paused") {
      name.innerHTML += ` <span class="ib-badge">${esc(word)}</span>`;
    }
    const actions = line.querySelector(".ib-actions");
    if (actions && !actions.querySelector("[data-history-r]")) {
      const button = document.createElement("button");
      button.className = "tiny ghost";
      button.dataset.historyR = a.id;
      button.textContent = a.runs ? `History (${a.runs})` : "History";
      button.onclick = () => automationHistory(a.id);
      actions.insertBefore(button, actions.firstChild);
    }
  });
}

/* One automation's runs, newest first. */
async function automationHistory(id) {
  const panel = $("#automationDetail");
  if (!panel) return;
  panel.hidden = false;
  panel.innerHTML = `<div class="ib-empty">Loading…</div>`;
  let data;
  try {
    data = await api(`/api/automations/${id}`);
  } catch (_) {
    panel.innerHTML = `<div class="ib-empty">Could not load that automation.</div>`;
    return;
  }
  const runs = data.history || [];
  panel.innerHTML = `
    <div class="auto-head">
      <strong>${esc(data.name)}</strong>
      <span class="ib-meta">${esc(data.goal || "")}</span>
      <button class="tiny ghost" id="autoClose">${IC.close}</button>
    </div>
    <div class="auto-runs">${runs.length ? runs.map((r) => `
      <button class="auto-run" data-run="${esc(r.id)}">
        <span class="auto-run-state">${esc(RUN_WORDS[r.state] || r.state)}</span>
        <span class="ib-meta">${esc(autoWhen(r.created_at))} · ${esc(
          (r.outcome || r.reason || "").slice(0, 90))}</span>
      </button>`).join("")
      : `<div class="ib-empty">It has not run yet.</div>`}</div>
    <div id="runDetail"></div>`;
  const close = $("#autoClose");
  if (close) close.onclick = () => { panel.hidden = true; };
  panel.querySelectorAll("[data-run]").forEach((b) => {
    b.onclick = () => runDetail(id, b.dataset.run);
  });
}

/* One run, whole. The lifecycle in order, so the behaviour is understandable
 * without anyone explaining the architecture. */
async function runDetail(automationId, runId) {
  const target = $("#runDetail");
  if (!target) return;
  target.innerHTML = `<div class="ib-empty">Loading…</div>`;
  let data;
  try {
    data = await api(`/api/automations/${automationId}/runs/${runId}`);
  } catch (_) {
    target.innerHTML = `<div class="ib-empty">That run is gone.</div>`;
    return;
  }
  const { run, steps } = data;
  const trigger = run.trigger || {};
  const context = run.context || {};

  //: Named for what happened rather than for the internal step kind — a user
  //: reading their own automation should not have to learn our vocabulary.
  const STEP_WORDS = {
    condition: "Checked the conditions", plan: "Worked out what to do",
    action: "Did", verify: "Confirmed",
  };
  const rows = (steps || []).map((s) => {
    const label = s.kind === "action" || s.kind === "verify"
      ? `${STEP_WORDS[s.kind]} ${esc(s.name)}`
      : esc(STEP_WORDS[s.kind] || s.kind);
    const detail = s.error || (s.result && (s.result.detail || s.result.reason)) || "";
    return `<li class="auto-step is-${esc(s.state)}">
      <span>${label}</span>
      <span class="ib-meta">${esc(String(detail).slice(0, 140))}</span></li>`;
  }).join("");

  const warned = (context.injection_attempts || []).length
    ? `<p class="auto-warn">Something in the content it read tried to give it new
       instructions. It was ignored — but you may want to look at
       ${esc(context.injection_attempts.join(", "))}.</p>`
    : "";

  target.innerHTML = `
    <div class="auto-detail">
      <p class="ib-meta">Started by <strong>${esc(trigger.kind || "?")}</strong>
        ${trigger.subject ? `· ${esc(trigger.subject)}` : ""}</p>
      <p class="ib-meta">${esc(RUN_WORDS[run.state] || run.state)}
        ${run.reason ? `· ${esc(run.reason)}` : ""}</p>
      ${warned}
      <ol class="auto-steps">${rows || "<li>Nothing recorded.</li>"}</ol>
      <p class="ib-meta">Saw ${Number(context.chars || 0)} characters of context
        from ${(context.pieces || []).length} source(s).
        Used ${Number(run.actions_used || 0)} action(s) and
        ${Number(run.model_calls_used || 0)} model call(s).</p>
    </div>`;
}
