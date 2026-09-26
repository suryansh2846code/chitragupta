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

    // The row's schedule text is written from the legacy columns, which cannot
    // describe "when a GitHub issue changes". Replaced here, from the spec the
    // engine matches against, so the row and the behaviour agree.
    const when = line.querySelector(".ib-when");
    const words = triggerWords(a.trigger);
    if (when && words) when.textContent = words;

    const meta = line.querySelector(".ib-meta");
    if (meta) {
      const bits = [];
      if (a.next_run) bits.push(`Next ${esc(autoWhen(a.next_run))}`);
      if (a.waiting) {
        bits.push(`${a.waiting} waiting for you`);
      } else if (a.last_state) {
        // What happened last time, in the same words the history screen uses.
        // A row that only said when it would next run gave no way to tell
        // "ran fine" from "correctly did nothing" without opening it.
        bits.push(esc(RUN_WORDS[a.last_state] || a.last_state));
      }
      if (bits.length) meta.innerHTML += ` · ${bits.join(" · ")}`;
    }
    const name = line.querySelector(".ib-name");
    if (name && word && a.state !== "paused") {
      name.innerHTML += ` <span class="ib-badge">${esc(word)}</span>`;
    }
    const actions = line.querySelector(".ib-actions");
    if (actions && !actions.querySelector("[data-history-r]")) {
      const button = document.createElement("button");
      // One word, and the count goes in a fixed-width slot beside it. It read
      // "History (12)" on one row and "History" on the next, so Pause, Edit
      // and the delete cross sat at a different x on every line.
      button.className = "tiny ghost ib-hist";
      button.dataset.historyR = a.id;
      button.textContent = "History";
      button.title = a.runs ? `${a.runs} run(s) so far` : "It has not run yet";
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

/* ── the builder: WHEN / ONLY IF / THEN ─────────────────────────────────────
 *
 * A form over the schema that already exists, not a second description of it.
 * Every option comes from `/api/automations/vocabulary`, which reads the
 * trigger and condition registries — so a condition added in Python appears
 * here, and one whose meaning changed cannot keep an old label.
 *
 * Deliberately flat. A list of conditions is an implicit "all of these", which
 * is what a person means by writing two of them, and it is the shape
 * `conditions.evaluate` already treats that way. Nesting is a real thing the
 * engine supports and a real thing a form cannot express without becoming a
 * programming language — so an automation that HAS a nested rule is shown,
 * locked, and left exactly as it was rather than flattened by opening a screen.
 */

//: Filled once per session from the vocabulary. Empty until then, and every
//: reader copes with empty — the modal must open on a machine where that call
//: failed, holding the automation's real values, rather than not opening.
let VOCAB = { triggers: [], conditions: [], fields: [], event_kinds: [],
              sources: [] };

//: The condition rows on screen. The user's edits live here between renders,
//: so adding a fourth row cannot discard what was typed into the third.
let COND_ROWS = [];

//: Set when the automation being edited has a condition this form cannot
//: express. Nothing is sent for conditions while it is true.
let COND_LOCKED = false;

async function loadVocabulary() {
  if (VOCAB.triggers.length) return VOCAB;
  try {
    const body = await api("/api/automations/vocabulary");
    VOCAB = {
      triggers: body.triggers || [], conditions: body.conditions || [],
      fields: body.fields || [], event_kinds: body.event_kinds || [],
      sources: body.sources || [],
    };
  } catch (_) { /* the form still opens, with what the automation already has */ }
  return VOCAB;
}

//: Which conditions a row may be, in the order a person reads them. Groups are
//: filtered out: this form cannot express one.
function conditionChoices() {
  return VOCAB.conditions.filter((c) => !c.group);
}

function conditionSpec(type) {
  return conditionChoices().find((c) => c.type === type) || null;
}

//: The trigger list to draw when the vocabulary call failed. Not a second
//: source of truth — it is what the engine has shipped with since the registry
//: existed, and it exists so a form on an offline machine is a form rather
//: than an empty select. The server's answer always wins.
const FALLBACK_TRIGGERS = [
  { type: "event", label: "When something happens", fields: ["kind", "source"] },
  { type: "schedule", label: "At a time of day", fields: ["at_time", "days"] },
  { type: "interval", label: "Every so often", fields: ["interval_min"] },
  { type: "manual", label: "Only when I ask", fields: [] },
];

/* Fill the WHEN select and the event pickers. Keeps the current value if it is
 * still offered, because re-rendering a form must not silently change it. */
function renderTriggerChoices(selected) {
  const select = $("#rmTrigger");
  if (!select) return;
  const options = VOCAB.triggers.length ? VOCAB.triggers : FALLBACK_TRIGGERS;
  select.innerHTML = options.map((t) =>
    `<option value="${esc(t.type)}"${t.type === selected ? " selected" : ""}>${
      esc(t.label)}</option>`).join("");
  if (selected) select.value = selected;

  const kinds = $("#rmEventKind");
  if (kinds) {
    const current = kinds.value;
    const list = VOCAB.event_kinds.length ? VOCAB.event_kinds : ["email.received"];
    kinds.innerHTML = list.map((k) =>
      `<option value="${esc(k)}"${k === current ? " selected" : ""}>${
        esc(eventKindWords(k))}</option>`).join("");
  }
  const every = $("#rmInterval");
  if (every) every.innerHTML = everyOptions(every.value);
  renderSourceChoices();
}

/* The app list, redrawn whenever the kind of event changes — the two questions
 * are one question asked twice, and leaving Gmail selected under "a calendar
 * event changes" is an automation that can never fire. */
function renderSourceChoices() {
  const apps = $("#rmEventSource");
  const kinds = $("#rmEventKind");
  if (!apps) return;
  const kind = kinds ? kinds.value : "";
  const chosen = apps.value;
  apps.innerHTML = sourceOptions(kind, chosen);
  // Keep the app only if it still makes sense for this kind of event.
  const still = (VOCAB.sources || []).some(
    (a) => a.id === chosen && (!kind || a.kind === kind));
  apps.value = still ? chosen : "";
}

/* The "what to check" menu, as options. Named by the server, so the words a
 * person picks and the path the engine reads stay attached — a form holding
 * its own half of that pairing offers fields no event carries. */
function fieldOptions(selected) {
  const fields = VOCAB.fields.length ? VOCAB.fields
    : [{ path: "event.title", label: "Subject or title" },
       { path: "event.body", label: "The text of it" }];
  const known = fields.some((f) => f.path === selected);
  // A field the automation already uses that this build does not offer is kept
  // and shown as itself, never silently swapped for the first item in the list.
  const all = known || !selected ? fields
    : [...fields, { path: selected, label: selected }];
  return all.map((f) => `<option value="${esc(f.path)}"${
    f.path === selected ? " selected" : ""}>${esc(f.label)}</option>`).join("");
}

//: How often "every so often" can mean. Minutes underneath, because that is
//: what `interval_min` is, but nobody thinks in 1440 of them.
const EVERY_CHOICES = [
  [15, "Every 15 minutes"], [30, "Every 30 minutes"], [60, "Every hour"],
  [120, "Every 2 hours"], [360, "Every 6 hours"], [720, "Every 12 hours"],
  [1440, "Once a day"],
];

/* The interval list, keeping a value this list does not offer.
 * An automation set to 45 minutes must not become 15 because somebody opened
 * it to read the name. */
function everyOptions(selected) {
  const minutes = Number(selected) || 60;
  const known = EVERY_CHOICES.some(([n]) => n === minutes);
  const all = known ? EVERY_CHOICES
    : [...EVERY_CHOICES, [minutes, `Every ${minutes} minutes`]]
      .sort((a, b) => a[0] - b[0]);
  return all.map(([n, label]) => `<option value="${n}"${
    n === minutes ? " selected" : ""}>${esc(label)}</option>`).join("");
}

/* The apps that can cause this kind of event, as options.
 *
 * Empty means any of them, which is the honest default: an automation that
 * cares about email usually cares about email, not about which client it
 * arrived in. */
function sourceOptions(kind, selected) {
  const apps = (VOCAB.sources || []).filter((a) => !kind || a.kind === kind);
  const known = apps.some((a) => a.id === selected);
  const all = known || !selected ? apps
    : [...apps, { id: selected, label: selected }];
  return [`<option value="">Any connected app</option>`].concat(
    all.map((a) => `<option value="${esc(a.id)}"${
      a.id === selected ? " selected" : ""}>${esc(a.label)}</option>`)).join("");
}

//: An event kind in a person's words. Falls back to the kind itself, so a new
//: one is readable rather than absent — "issue.changed" is worse than "An issue
//: changes" and much better than nothing.
const EVENT_WORDS = {
  "email.received": "An email arrives",
  "message.received": "A message arrives",
  "calendar.changed": "A calendar event changes",
  "document.changed": "A document changes",
  "file.changed": "A file changes",
  "note.changed": "A note changes",
  "issue.changed": "An issue changes",
  "repository.changed": "Something changes in a repository",
  "health.recorded": "A health reading is recorded",
};
function eventKindWords(kind) { return EVENT_WORDS[kind] || kind; }

/* What this field's value can be, when the answer is a known list.
 *
 * "Which app it came from" is a choice between the apps this build has, not a
 * sentence — and a text box there is a box somebody types "Gmail" into when the
 * engine is comparing against "gmail". Returns null when the value really is
 * free text, which is most of them.
 */
function valueChoices(field) {
  if (field === "event.source" || field === "event_source" || field === "event.app") {
    return (VOCAB.sources || []).map((a) => [a.id, a.label]);
  }
  if (field === "event_kind") {
    return (VOCAB.event_kinds || []).map((k) => [k, eventKindWords(k)]);
  }
  return null;
}

/* Draw the condition rows from COND_ROWS. */
function renderConditions() {
  const host = $("#rmConditions");
  const hint = $("#rmCondHint");
  const add = $("#rmAddCond");
  if (!host) return;
  if (COND_LOCKED) {
    host.innerHTML = "";
    if (hint) {
      hint.textContent = "This one has a grouped rule that is too complex to"
        + " show here. It still works — it is left exactly as it is.";
    }
    if (add) add.hidden = true;
    return;
  }
  if (add) add.hidden = false;
  if (hint) {
    hint.textContent = COND_ROWS.length
      ? "All of these have to be true for it to run."
      : "Leave this empty and it runs every time.";
  }
  // Three columns and a remove button, the same three on every row whether or
  // not this condition takes a value. Laying them out with `flex: 1 1 28%`
  // meant a row for `is not empty` had two wide boxes where its neighbours had
  // three narrow ones, so nothing in the list lined up with anything.
  host.innerHTML = COND_ROWS.map((row, i) => {
    const spec = conditionSpec(row.type);
    const wantsField = !spec || spec.field !== false;
    const valueKind = spec ? spec.value : "text";
    const placeholder = valueKind === "list" ? "one, another, a third"
      : valueKind === "number" ? "a number"
      : valueKind === "prompt" ? "e.g. is this from a client?"
      : "what to compare it to";
    return `<div class="cond-row">
      ${wantsField ? `<select class="cond-field" data-cond-field="${i}"
        >${fieldOptions(row.field)}</select>`
        : `<span class="cond-whole">The whole thing</span>`}
      <select class="cond-type" data-cond-type="${i}">${
        conditionChoices().map((c) => `<option value="${esc(c.type)}"${
          c.type === row.type ? " selected" : ""}>${esc(c.label)}</option>`)
          .join("")}</select>
      ${valueKind ? valueControl(i, row, valueKind, placeholder)
        : `<span class="cond-none"></span>`}
      <button class="tiny ghost ib-x" data-cond-del="${i}"
        aria-label="Remove this check">${IC.close}</button>
    </div>`;
  }).join("");

  // Every edit is written back to COND_ROWS as it is typed. Reading the DOM
  // only at save time loses whatever a re-render happened to wipe.
  host.querySelectorAll("[data-cond-field]").forEach((select) => {
    const write = () => {
      const row = COND_ROWS[Number(select.dataset.condField)];
      const was = valueChoices(row.field);
      row.field = select.value;
      const now = valueChoices(row.field);
      // A value picked from one list means nothing in another, and a value
      // typed for a free-text field means nothing in a list. Cleared rather
      // than carried across, so the row never shows an answer to a question it
      // is no longer asking.
      if (String(was) !== String(now)) {
        row.value = "";
        renderConditions();
      }
    };
    // Both, because it is a `select` now and the harness drives whichever the
    // app bound — and because a browser fires `change`, not `input`, on one.
    select.onchange = write;
    select.oninput = write;
  });
  host.querySelectorAll("[data-cond-value]").forEach((input) => {
    const write = () => {
      COND_ROWS[Number(input.dataset.condValue)].value = input.value;
    };
    // It is a `select` for some fields and an `input` for others, and a browser
    // fires a different event for each.
    input.oninput = write;
    input.onchange = write;
  });
  host.querySelectorAll("[data-cond-type]").forEach((select) => {
    select.onchange = () => {
      COND_ROWS[Number(select.dataset.condType)].type = select.value;
      renderConditions();          // a different type asks a different question
    };
  });
  host.querySelectorAll("[data-cond-del]").forEach((button) => {
    button.onclick = () => {
      COND_ROWS.splice(Number(button.dataset.condDel), 1);
      renderConditions();
    };
  });
}

/* The value box, or a list where the answers are known.
 *
 * Only for a single value: "is one of" takes several, and a one-line select
 * cannot say that. That one stays a text box with a placeholder showing the
 * shape it wants.
 */
function valueControl(i, row, valueKind, placeholder) {
  const choices = valueKind === "text" ? valueChoices(row.field) : null;
  if (!choices || !choices.length) {
    return `<input class="cond-value" data-cond-value="${i}"
      value="${esc(row.value || "")}"
      ${valueKind === "number" ? 'type="number"' : ""}
      placeholder="${esc(placeholder)}" />`;
  }
  const current = String(row.value || "");
  const known = choices.some(([value]) => value === current);
  const all = known || !current ? choices : [...choices, [current, current]];
  return `<select class="cond-value" data-cond-value="${i}">${
    [`<option value="">Pick one…</option>`].concat(
      all.map(([value, label]) => `<option value="${esc(value)}"${
        value === current ? " selected" : ""}>${esc(label)}</option>`))
      .join("")}</select>`;
}

function addCondition() {
  // Opens on a field and a check that already make sense together, so the row
  // reads as a sentence before anything is typed. An empty field is a row that
  // silently never matches, which `builderConditions` then drops — correct, and
  // invisible, so it is better not to offer it in the first place.
  const first = conditionChoices()[0];
  const fields = VOCAB.fields.length ? VOCAB.fields : [{ path: "event.title" }];
  COND_ROWS.push({ type: first ? first.type : "contains",
                   field: fields[0].path, value: "" });
  renderConditions();
}

/* Open the builder holding what this automation already says.
 *
 * `existing` is the routine row the list already has; the rich trigger and the
 * conditions are fetched, because they live on the automation view of the same
 * row and a routine row does not carry them. A failed fetch leaves the form on
 * the legacy fields rather than on nothing. */
async function openBuilder(existing) {
  await loadVocabulary();
  COND_ROWS = [];
  COND_LOCKED = false;
  let trigger = null;
  let conditions = [];
  if (existing) {
    try {
      const body = await api(`/api/automations/${existing.id}`);
      trigger = body.trigger || null;
      conditions = body.conditions || [];
    } catch (_) { /* legacy fields only */ }
  }
  renderTriggerChoices((trigger && trigger.type) || "event");
  if (trigger) {
    if (trigger.type === "event") {
      if ($("#rmEventKind") && trigger.kind) $("#rmEventKind").value = trigger.kind;
      if ($("#rmEventSource")) $("#rmEventSource").value = trigger.source || "";
    } else if (trigger.type === "schedule") {
      if ($("#rmAtTime") && trigger.at_time) $("#rmAtTime").value = trigger.at_time;
      if ($("#rmDays")) $("#rmDays").value = trigger.days || "";
    } else if (trigger.type === "interval" && $("#rmInterval")) {
      // The options are rebuilt around the stored value before it is selected.
      // A browser drops an assignment to a value the list does not contain, so
      // setting it first silently turned "every 45 minutes" into every hour.
      $("#rmInterval").innerHTML = everyOptions(trigger.interval_min || 60);
      $("#rmInterval").value = String(trigger.interval_min || 60);
    }
  }
  // A group, or a condition this build does not know, means the form cannot
  // round-trip it. Shown and locked rather than quietly rewritten.
  const known = new Set(conditionChoices().map((c) => c.type));
  COND_LOCKED = conditions.some((c) => !c || !known.has(c.type));
  if (!COND_LOCKED) {
    COND_ROWS = conditions.map((c) => ({
      type: c.type,
      field: c.field || "",
      value: Array.isArray(c.value) ? c.value.join(", ")
        : c.question || (c.value === undefined || c.value === null
          ? "" : String(c.value)),
    }));
  }
  renderConditions();
}

/* The trigger spec the engine stores, from what the form says. */
function builderTrigger() {
  const type = ($("#rmTrigger") || {}).value || "event";
  if (type === "event") {
    const spec = { type: "event", kind: ($("#rmEventKind") || {}).value || "" };
    const source = (($("#rmEventSource") || {}).value || "").trim();
    if (source) spec.source = source;
    return spec;
  }
  if (type === "schedule") {
    return { type: "schedule", at_time: ($("#rmAtTime") || {}).value || "",
             days: ($("#rmDays") || {}).value || "" };
  }
  if (type === "interval") {
    return { type: "interval",
             interval_min: parseInt(($("#rmInterval") || {}).value, 10) || 60 };
  }
  return { type: "manual" };
}

/* The conditions the engine stores, or null when they must be left alone.
 *
 * A row with no field, or no value where one is required, is dropped rather
 * than saved half-written: a condition that reads an empty path is one that
 * silently never passes, and the automation then does nothing for a reason
 * nobody can see. */
function builderConditions() {
  if (COND_LOCKED) return null;
  const out = [];
  COND_ROWS.forEach((row) => {
    const spec = conditionSpec(row.type);
    if (!spec) return;
    const value = String(row.value === undefined ? "" : row.value).trim();
    const field = String(row.field || "").trim();
    if (spec.field !== false && !field) return;
    if (spec.value && !value) return;
    const written = { type: row.type };
    if (spec.field !== false) written.field = field;
    if (spec.value === "list") {
      written.value = value.split(",").map((v) => v.trim()).filter(Boolean);
    } else if (spec.value === "number") {
      written.value = Number(value);
    } else if (spec.value === "prompt") {
      written.question = value;
    } else if (spec.value) {
      written.value = value;
    }
    out.push(written);
  });
  return out;
}

//: What the legacy `routines` columns must say for the same automation.
//:
//: Both halves are written, and that is deliberate rather than redundant: the
//: rich spec is what the engine reads, and the legacy columns are the fallback
//: for a row whose `trigger_json` is empty. An event kind with no legacy
//: equivalent becomes `manual`, which does not fire — the same fail-closed
//: choice `model._legacy_trigger` makes, and the opposite of guessing
//: "new_email" for a GitHub push.
function builderLegacy(trigger) {
  if (trigger.type === "schedule") {
    return { trigger: "daily", at_time: trigger.at_time || "08:00",
             days: trigger.days || "", interval_min: 60 };
  }
  if (trigger.type === "interval") {
    return { trigger: "schedule", interval_min: trigger.interval_min || 60,
             at_time: "", days: "" };
  }
  if (trigger.type === "event" && trigger.kind === "email.received") {
    return { trigger: "new_email", interval_min: 60, at_time: "", days: "" };
  }
  return { trigger: "manual", interval_min: 60, at_time: "", days: "" };
}

/* Store the rich half against an automation that now exists.
 *
 * Returns "" on success or a sentence for the user. Two requests rather than
 * one because the routine row and the automation view of it have separate
 * writers, and a new endpoint that wrote both would be a third place deciding
 * what a trigger means. */
async function saveBuilder(automationId) {
  const trigger = builderTrigger();
  const conditions = builderConditions();
  const body = conditions === null ? { trigger } : { trigger, conditions };
  try {
    await api(`/api/automations/${automationId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (_) {
    return "Saved — but when it runs and what it checks could not be stored.";
  }
  return "";
}

/* One automation's trigger, as a sentence. Replaces the row text, which is
 * written from the legacy columns and cannot describe a rich trigger. */
function triggerWords(trigger) {
  const spec = trigger || {};
  if (spec.type === "event") {
    return eventKindWords(spec.kind || "")
      + (spec.source ? ` in ${spec.source}` : "");
  }
  if (spec.type === "schedule") {
    const days = ROUTINE_DAY_WORDS[spec.days || ""]
      || String(spec.days || "").split(",").filter(Boolean).join(", ");
    return `${days || "Every day"} at ${routineClock(spec.at_time)}`;
  }
  if (spec.type === "interval") {
    const mins = Number(spec.interval_min) || 60;
    return mins === 60 ? "Every hour" : `Every ${mins} min`;
  }
  if (spec.type === "manual") return "Only when you ask";
  return "";
}
