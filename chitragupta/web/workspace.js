/**
 * The things the workspace keeps for you: approvals, tasks, reminders,
 * routines, the folder picker, and first-run.
 *
 * **The approvals queue is the visible half of the unattended-action gate.** An
 * agent reading a new email is reading text a stranger wrote, and that text can
 * contain an instruction aimed at the model. Anything leaving the machine
 * without a permitted recipient waits here for one tap, and `decide()` says
 * what happened either way — an approval that silently does nothing is the same
 * bug as a dead spinner.
 *
 * First-run lives here too: the welcome, naming the lead agent, and the
 * one-time intro. `flashConnectors` exists because pointing at the thing you
 * mean is better than describing where it is.
 */

// What is actually on the list this grant would join. The plural used to be
// hard-coded "people", which is wrong about a repository and wrong about a
// connector tool — and "2 people won't be asked about again" after approving a
// write to `linear` is not a sentence the user can check.
const GRANT_NOUNS = {
  repo_recipient: ["repository", "repositories"],
  connector_tool: ["connector tool", "connector tools"],
};
const grantNoun = (kind, n) =>
  (GRANT_NOUNS[kind] || ["person", "people"])[n === 1 ? 0 : 1];

async function loadApprovals() {
  const box = $("#approvals");
  if (!box) return;
  let rows;
  try {
    rows = (await api("/api/agents/approvals")).approvals || [];
  } catch {
    return;                       // a failed poll must not blank a live list
  }
  if (!rows.length) { box.hidden = true; box.innerHTML = ""; return; }

  box.hidden = false;
  box.innerHTML =
    `<div class="ctx-label">Waiting for you (${rows.length})</div>` +
    rows.map((a) => {
      // Offered only when the server said an allow-list could clear this one.
      // `blocked` comes from `permissions.check()` as data; reading the address
      // out of `reason` instead would be the UI guessing at what an injected
      // `to:` field contained. Empty means no grant could help — an action in
      // NEVER_UNATTENDED — and a button that cannot work must not be shown.
      const blocked = Array.isArray(a.blocked) ? a.blocked : [];
      const who = blocked.length === 1
        ? blocked[0] : `${blocked.length} ${grantNoun(a.kind, blocked.length)}`;
      // Spelled out in the label, never "Always allow this": a standing grant
      // the user cannot read is a tap, not consent. Secondary styling, and
      // second in the row, so approving once stays the easy answer.
      const allow = blocked.length
        ? `<button class="tiny ghost" data-apralw="${esc(a.id)}"
             title="${esc(blocked.join(", "))}">Always allow ${esc(who)}</button>`
        : "";
      return `
      <div class="apr" data-apr="${esc(a.id)}">
        <div class="apr-sum">${esc(a.summary)}</div>
        <div class="apr-why">${esc(a.reason || "")}${
          a.routine_name ? ` · from “${esc(a.routine_name)}”` : ""}</div>
        <div class="apr-btns">
          <button class="tiny" data-aprok="${esc(a.id)}">Approve</button>
          ${allow}
          <button class="tiny ghost" data-aprno="${esc(a.id)}">Dismiss</button>
        </div>
      </div>`;
    }).join("");

  const decide = async (id, verb, path) => {
    const card = box.querySelector(`[data-apr="${id}"]`);
    if (card) card.classList.add("apr-busy");
    try {
      const r = await api(`/api/agents/approvals/${encodeURIComponent(id)}/${path}`,
                          { method: "POST" });
      // Say what happened, including when the action itself failed — an
      // approval that silently does nothing is the same bug as a dead spinner.
      toast(r.ok === false ? (r.error || `couldn't ${verb} that`)
                           : (r.detail || `${verb}d`));
    } catch (e) {
      toast(String(e));
    }
    loadApprovals();
  };

  // Grant first, then run the action that was waiting on it. In that order the
  // approval still happens if the grant fails, and the user is told which part
  // did not work rather than watching one button do two things silently.
  const allowAlways = async (id) => {
    const row = rows.find((r) => r.id === id);
    const who = (row && row.blocked) || [];
    const card = box.querySelector(`[data-apr="${id}"]`);
    if (card) card.classList.add("apr-busy");
    try {
      for (const value of who) {
        await api("/api/agents/permissions", {
          method: "POST", headers: { "Content-Type": "application/json" },
          // The list the SERVER said this action is judged against. Guessing
          // it here wrote `telegram:@dana` onto the email list, which the gate
          // for `message_send` never reads — so the tap did nothing and said
          // it had worked.
          body: JSON.stringify({ value, kind: row.kind || "",
                                 note: "allowed from an approval" }) });
      }
    } catch (e) {
      toast(`Could not save that permission — ${String(e)}`);
      if (card) card.classList.remove("apr-busy");
      return;
    }
    toast(who.length === 1 ? `${who[0]} won't be asked about again`
                           : `${who.length} ${grantNoun(row && row.kind, who.length)}`
                             + ` won't be asked about again`);
    decide(id, "approve", "approve");
  };

  box.querySelectorAll("[data-aprok]").forEach((b) =>
    b.onclick = () => decide(b.dataset.aprok, "approve", "approve"));
  box.querySelectorAll("[data-apralw]").forEach((b) =>
    b.onclick = () => allowAlways(b.dataset.apralw));
  box.querySelectorAll("[data-aprno]").forEach((b) =>
    b.onclick = () => decide(b.dataset.aprno, "dismiss", "reject"));
}


// ── what actually happened ───────────────────────────────────────────────
/**
 * The record after the fact.
 *
 * The approvals queue above is consent *before* an action runs. This is the
 * other half, and it is the half that makes agreeing to unattended work
 * reasonable: a person who cannot review what their agents did has only ever
 * been asked to trust them.
 *
 * Three things it must say and used to say none of, because nothing read the
 * log back at all:
 *
 * * **Whether it worked**, distinctly from whether it was *confirmed*. "Sent"
 *   is what we asked for; "confirmed 3:42 PM" is what Gmail said happened, and
 *   an action we could not check says the plainer thing rather than claiming a
 *   verification nobody made.
 * * **Where it came from** — a routine acting on its own is a different fact
 *   about your week than a card you tapped.
 * * **Whether it can still be taken back.** Undo lives on the card for the few
 *   seconds after a confirm; this is where it lives afterwards, which is when
 *   a person actually notices the date was wrong.
 */

//: How an action arrived, in the user's terms. "chat" is the ordinary case and
//: says nothing — a label on every row is a label nobody reads.
const ACTION_ORIGIN = {
  routine: "an automation",
  approval: "you approved it",
  scheduled: "scheduled",
};

function actionWhen(stamp) {
  const d = new Date(stamp);
  if (Number.isNaN(d.getTime())) return "";
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

async function loadActionLog() {
  const box = $("#actionLog");
  if (!box) return;
  const empty = $("#actionLogEmpty");
  const count = $("#actionLogCount");

  let rows;
  try {
    rows = (await api("/api/actions/log?limit=40")).entries || [];
  } catch {
    return;                       // a failed poll must not blank a live list
  }
  if (empty) empty.hidden = rows.length > 0;
  if (count) count.textContent = rows.length ? `${rows.length} recent` : "";
  if (!rows.length) { box.innerHTML = ""; return; }

  box.innerHTML = rows.map((e) => {
    // Three states, not two. An undone action is neither a success the user
    // should still see as done nor a failure that needs their attention.
    const state = e.undone ? "undone" : (e.ok ? "ok" : "err");
    // Drawn, never typed — `web/CLAUDE.md`. A dingbat is a colour font the OS
    // picks, so it ignores `currentColor` and cannot take the three states'
    // colours; `IC` is the set.
    const mark = e.undone ? IC.undo : (e.ok ? IC.check : IC.close);
    const stamp = e.verified && e.result && e.result.verified_at
      ? clockTime(e.result.verified_at) : "";
    const where = ACTION_ORIGIN[e.origin] || "";
    const why = [where, actionWhen(e.created_at)].filter(Boolean).join(" · ");
    // Offered only where the server said an inverse exists AND it has not
    // already been used. A button that quietly does nothing is the bug this
    // whole column is meant to prevent.
    const undo = e.reversible && !e.undone
      ? `<button class="tiny ghost" data-undo="${esc(e.id)}">${
           esc(e.result && e.result.undo_label ? e.result.undo_label : "Undo")}</button>`
      : "";
    return `
      <div class="alog-row" data-alog="${esc(e.id)}" data-state="${state}">
        <span class="alog-mark" aria-hidden="true">${mark}</span>
        <div class="alog-body">
          <div class="alog-sum">${esc(e.summary || e.action_type)}</div>
          <div class="alog-why">${esc(why)}${
            stamp ? ` · confirmed ${esc(stamp)}` : ""}${
            e.undone ? " · taken back" : ""}</div>
          ${e.ok ? "" : `<div class="alog-err">${esc(e.detail || "It did not work.")}</div>`}
        </div>
        ${undo}
      </div>`;
  }).join("");

  box.querySelectorAll("[data-undo]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      const was = b.textContent;
      b.textContent = "Undoing…";
      try {
        const out = await api("/api/actions/undo", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ log_id: b.dataset.undo }) });
        toast(out.ok ? (out.detail || "Undone")
                     : (out.error || "That could not be undone."));
        // Reload either way: a refusal usually means the world moved, and the
        // row is now telling the user something that is no longer true.
        loadActionLog(); loadReminders(); loadRoutines();
      } catch (e) {
        b.disabled = false; b.textContent = was;
        toast(String(e));
      }
    };
  });
}


// ── the standing half of the gate ────────────────────────────────────────
/**
 * Who unattended agents may reach without asking each time.
 *
 * The approvals card is where a grant is normally made, because that is the
 * moment a user learns they want one. This is the other half: every standing
 * grant, reviewable and revocable. A permission the user cannot find is one
 * they cannot take back, and `permissions.py` is explicit that this list is the
 * whole boundary — so it has to be visible.
 */
async function loadAllowList() {
  const box = $("#allowList");
  if (!box) return;
  let rows;
  try {
    rows = (await api("/api/agents/permissions")).permissions || [];
  } catch {
    return;                       // a failed poll must not blank a live list
  }

  box.innerHTML = rows.length
    ? rows.map((p) => `
        <div class="set-row" data-allow="${esc(p.value)}">
          <div class="set-main">
            <div class="set-label">${esc(p.value)}${
              // Which list, when there is more than one it could be on. The
              // same handle can be a person on two apps, and "remove" has to
              // be unambiguous about which permission it takes away.
              p.kind_label ? `<span class="set-tag">${esc(p.kind_label)}</span>` : ""}</div>
            <div class="set-desc">${esc(p.note || "Agents may reach them unattended.")}</div>
          </div>
          <div class="set-ctl">
            <button class="tiny ghost" data-allowdel="${esc(p.value)}"
                    data-allowkind="${esc(p.kind || "")}">Remove</button>
          </div>
        </div>`).join("")
    // Not an error state, and said in the user's terms: the app is working
    // exactly as designed, and every outbound action is waiting for a tap.
    : `<div class="set-row"><div class="set-main">
         <div class="set-desc">Nobody yet — every email or invitation an agent
         sends on its own is waiting for you to approve it.</div>
       </div></div>`;

  box.querySelectorAll("[data-allowdel]").forEach((b) =>
    b.onclick = async () => {
      const value = b.dataset.allowdel;
      b.disabled = true;
      try {
        const kind = b.dataset.allowkind || "";
        await api(`/api/agents/permissions/${encodeURIComponent(value)}`
                  + (kind ? `?kind=${encodeURIComponent(kind)}` : ""),
                  { method: "DELETE" });
        toast(`${value} will be asked about again`);
      } catch (e) {
        toast(`Could not remove that — ${String(e)}`);
        b.disabled = false;
        return;
      }
      loadAllowList();
    });
}

{
  const add = $("#allowAdd"), input = $("#allowInput");
  const grant = async () => {
    const value = (input && input.value || "").trim();
    if (!value) return;
    if (add) add.disabled = true;
    try {
      await api("/api/agents/permissions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value, note: "" }) });
      if (input) input.value = "";
      toast(`${value} won't be asked about again`);
    } catch (e) {
      toast(`Could not allow that — ${String(e)}`);
    }
    if (add) add.disabled = false;
    loadAllowList();
  };
  if (add) add.onclick = grant;
  if (input) input.onkeydown = (e) => { if (e.key === "Enter") grant(); };
}


// ── tasks ────────────────────────────────────────────────────────────────
function dueLabel(iso) {
  if (!iso) return { text: "", cls: "" };
  const today = new Date().toISOString().slice(0, 10);
  const d = new Date(iso + "T00:00:00");
  const nice = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  if (iso < today) return { text: "overdue · " + nice, cls: "over" };
  if (iso === today) return { text: "today", cls: "today" };
  return { text: nice, cls: "" };
}

async function loadTasks() {
  const d = await api("/api/tasks");
  const n = d.stats.today, o = d.stats.overdue;
  $("#taskCount").textContent =
    d.tasks.length ? `· ${n} today${o ? ", " + o + " overdue" : ""}` : "";
  if (!d.tasks.length) {
    $("#taskList").innerHTML = `<div class="tasks-empty">No open tasks. Add one, or ask an agent.</div>`;
    return;
  }
  $("#taskList").innerHTML = d.tasks.map((t) => {
    const due = dueLabel(t.due);
    // Where it came from. A task made out of a thread is worth more than the
    // same sentence typed by hand precisely because it can go back — without
    // this the user reads "send Rahul the revised figures" in three weeks and
    // searches their inbox anyway, which is the work they asked to be rid of.
    // Shown only when there is somewhere to go: never a control that cannot
    // work.
    const from = t.source === "email" && t.source_ref
      ? `<span class="task-src" data-thread="${esc(t.source_ref)}"
               title="Open the email this came from">${IC.external} email</span>`
      : "";
    return `<div class="task">
      <span class="check" data-done="${t.id}">${IC.check}</span>
      <div class="body">
        <div class="ttl">${esc(t.title)}</div>
        ${due.text || from
          ? `<div class="due ${due.cls}">${due.text}${
              due.text && from ? " · " : ""}${from}</div>`
          : ""}
      </div>
      <span class="del" data-del-task="${t.id}">${IC.close}</span>
    </div>`;
  }).join("");
  document.querySelectorAll("[data-thread]").forEach((el) => el.onclick = async () => {
    try {
      await api("/api/open-browser", { method: "POST", body: {
        url: `https://mail.google.com/mail/#all/${el.dataset.thread}` } });
    } catch (e) {
      toast(`Could not open that email — ${String(e)}`);
    }
  });
  document.querySelectorAll("[data-done]").forEach((el) => el.onclick = async () => {
    await api(`/api/tasks/${el.dataset.done}/complete`, { method: "POST" });
    toast("Task done"); loadTasks();
  });
  document.querySelectorAll("[data-del-task]").forEach((el) => el.onclick = async () => {
    await api(`/api/tasks/${el.dataset.delTask}`, { method: "DELETE" });
    loadTasks();
  });
}

let REMINDERS = [];
let _editingReminder = null;     // null = creating

async function loadReminders() {
  try {
    const { reminders } = await api("/api/reminders");
    REMINDERS = reminders;
    $("#reminderList").innerHTML = reminders.length ? reminders.map((r) => {
      const when = new Date(r.fire_at).toLocaleString(undefined,
        { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
      // Two different things share this list. A reminder is text with a time and
      // can be reworded; a queued ACTION is an email or an event an agent is
      // about to send, and a half-edited one is worse than one you cancel and
      // ask for again — so it offers cancel, not edit.
      const isAction = r.kind === "action";
      const edit = isAction ? ""
        : `<button class="tiny ghost" data-edit-rem="${r.id}">Edit</button>`;
      return `<div class="ib-row">
        <span class="ib-state" data-on="${isAction ? "queued" : "1"}" aria-hidden="true"></span>
        <span class="ib-text">
          <span class="ib-name">${esc(r.label || r.message)}${isAction ? `<span class="ib-badge">Queued action</span>` : ""}</span>
          <span class="ib-meta">${esc(when)}${r.agent_id ? " · " + esc(r.agent_id) : ""}</span>
        </span>
        <span class="ib-actions">${edit}
          <button class="tiny ghost ib-x" data-del-rem="${r.id}" aria-label="${isAction ? "Cancel" : "Delete"}">${IC.close}</button>
        </span></div>`;
    }).join("") : `<div class="ib-empty">Nothing coming up.</div>`;
    document.querySelectorAll("[data-del-rem]").forEach((b) => b.onclick = async () => {
      await api(`/api/reminders/${b.dataset.delRem}`, { method: "DELETE" });
      toast("Removed"); loadReminders();
    });
    document.querySelectorAll("[data-edit-rem]").forEach((b) => b.onclick = () =>
      reminderForm(REMINDERS.find((x) => x.id === b.dataset.editRem)));
  } catch (_) {}
}

//: `<input type="datetime-local">` speaks local wall time with no zone, which
//: is exactly what a person means by "9am" — so convert by hand rather than
//: through toISOString(), which would shift it to UTC and move the reminder.
function _toLocalInput(iso) {
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function reminderForm(existing) {
  _editingReminder = existing || null;
  const when = existing ? new Date(existing.fire_at) : new Date(Date.now() + 60 * 60 * 1000);
  $("#remMessage").value = existing ? (existing.label || existing.message || "") : "";
  $("#remWhen").value = _toLocalInput(when);
  $("#remTitle").textContent = existing ? "Edit reminder" : "New reminder";
  $("#remSave").textContent = existing ? "Save" : "Create";
  $("#remHint").textContent = "";
  $("#reminderModal").hidden = false;
}
{
  const nb = $("#newReminderBtn"); if (nb) nb.onclick = () => reminderForm(null);
  const cl = $("#remClose"); if (cl) cl.onclick = () => { $("#reminderModal").hidden = true; };
  const bg = $("#reminderModal");
  if (bg) bg.onclick = (e) => { if (e.target.id === "reminderModal") bg.hidden = true; };
  const save = $("#remSave");
  if (save) save.onclick = async () => {
    const message = $("#remMessage").value.trim();
    const local = $("#remWhen").value;
    if (!message) { $("#remHint").textContent = "Say what it should remind you about."; return; }
    if (!local) { $("#remHint").textContent = "Pick a date and time."; return; }
    // The local value carries no zone. Stamping it with the browser's offset is
    // what keeps "9am" meaning 9am here rather than 9am UTC.
    const fire_at = new Date(local).toString() === "Invalid Date" ? null : _isoWithOffset(new Date(local));
    if (!fire_at) { $("#remHint").textContent = "That date does not look right."; return; }
    const editing = _editingReminder;
    try {
      if (editing) {
        await api(`/api/reminders/${editing.id}`, { method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, fire_at }) });
      } else {
        await api("/api/reminders", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, fire_at }) });
      }
    } catch (e) { $("#remHint").textContent = "Could not save that reminder."; return; }
    $("#reminderModal").hidden = true;
    _editingReminder = null;
    toast(editing ? "Reminder updated" : "Reminder set");
    loadReminders();
  };
}
function _isoWithOffset(d) {
  const p = (n) => String(n).padStart(2, "0");
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const a = Math.abs(off);
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
    + `T${p(d.getHours())}:${p(d.getMinutes())}:00${sign}${p(Math.floor(a / 60))}:${p(a % 60)}`;
}

async function addTask() {
  const title = $("#taskInput").value.trim();
  if (!title) return;
  await api("/api/tasks", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  $("#taskInput").value = "";
  toast("Task added"); loadTasks();
}
$("#taskAdd").onclick = addTask;
$("#taskInput").addEventListener("keydown", (e) => { if (e.key === "Enter") addTask(); });

// ── folder picker ──────────────────────────────────────────────────────────
let pickPath = null;
async function openPicker(path) {
  const d = await api("/api/fs/browse" + (path ? `?path=${encodeURIComponent(path)}` : ""));
  pickPath = d.path;
  $("#picker").hidden = false;
  $("#pickPath").textContent = d.path;
  $("#pickInfo").textContent = d.ingestible_here
    ? `${d.ingestible_here} ingestible file(s) directly here` : "no text files directly here (subfolders may still have them)";
  let rows = "";
  if (d.parent) rows += `<div class="pick-row up" data-go="${esc(d.parent)}">⤴  ..</div>`;
  rows += d.dirs.map((name) => `<div class="pick-row" data-go="${esc(d.path.replace(/\/$/, "") + "/" + name)}">${esc(name)}</div>`).join("");
  $("#pickList").innerHTML = rows || `<div class="pick-row up">(no subfolders)</div>`;
  document.querySelectorAll("#pickList [data-go]").forEach((el) => el.onclick = () => openPicker(el.dataset.go));
}
$("#pickClose").onclick = () => $("#picker").hidden = true;
$("#pickIngest").onclick = async () => {
  $("#picker").hidden = true;
  toast(`ingesting ${pickPath.split("/").pop()}…`);
  try {
    const r = await api(`/api/connectors/files/sync`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params: { path: pickPath } }) });
    toast(r.errors?.length ? `files: ${r.errors[0]}` : `files: +${r.added} added from ${r.detail}`);
    loadBrain();
  } catch (e) { toast(String(e)); }
};

$("#composer").onsubmit = (e) => {
  e.preventDefault();
  if (busy) { stopTurn(); return; }   // Stop — the server hears about it
  const v = $("#input").value.trim();
  if ((v || attachments.length) && current) {
    $("#input").value = ""; autoGrow(); send(v);
  }
};
$("#input").addEventListener("input", () => { autoGrow(); updateConnectorPicker(); });
$("#input").addEventListener("blur", () => setTimeout(closeConnectorPicker, 120));
$("#input").addEventListener("keydown", (e) => {
  const picker = $("#cmpPicker");
  const open = picker && !picker.hidden;
  if (open && (e.key === "Enter" || e.key === "Tab")) {
    // While the picker is up, Enter chooses rather than sends — otherwise the
    // half-typed @mention goes to the agent as a word it has to ignore.
    e.preventDefault();
    const chosen = picker.querySelector(".cmp-opt.on") || picker.querySelector(".cmp-opt");
    if (chosen) chosen.click();
    return;
  }
  if (open && e.key === "Escape") { e.preventDefault(); closeConnectorPicker(); return; }
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#clearBtn").onclick = async () => { await api(`/api/agents/${current}/clear`, { method: "POST" }); selectAgent(current); toast("chat cleared"); };
$("#ingestBtn").onclick = async () => {
  const t = $("#ingestText").value.trim(); if (!t) return;
  await api("/api/brain/ingest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t }) });
  $("#ingestText").value = ""; toast("added to brain"); loadBrain();
};

// ── automations / routines ─────────────────────────────────────────────────
//: When a routine runs, in the words a person would use. Mirrors
//: `routines.describe_schedule` — both exist because the row and the approval
//: card have to say the same thing, and a row that says "Every 1440 min" over
//: a routine the user set for 8am is the row lying about their own automation.
const ROUTINE_DAY_WORDS = {
  "": "Every day",
  "mon,tue,wed,thu,fri": "Weekdays",
  "sat,sun": "Weekends",
};

function routineClock(at) {
  const [h, m] = String(at || "").split(":").map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return at || "";
  const suffix = h < 12 ? "AM" : "PM";
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${suffix}`;
}

function routineWhen(r) {
  if (r.trigger === "new_email") return "When new email arrives";
  if (r.trigger === "daily" && r.at_time) {
    const days = ROUTINE_DAY_WORDS[r.days || ""]
      || String(r.days).split(",").filter(Boolean)
        .map((d) => d.charAt(0).toUpperCase() + d.slice(1)).join(", ");
    return `${days} at ${routineClock(r.at_time)}`;
  }
  const mins = Number(r.interval_min) || 60;
  return mins === 60 ? "Every hour" : `Every ${mins} min`;
}

async function loadRoutines() {
  try {
    const { routines } = await api("/api/routines");
    ROUTINES = routines;
    $("#routineList").innerHTML = routines.length ? routines.map((r) => {
      const trig = routineWhen(r);
      // An automation the user never watches run is one they cannot trust, so
      // the row says when it last ran and whether that run went anywhere.
      const ran = r.last_run
        ? `Last run ${new Date(r.last_run).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
        : "Not run yet";
      return `<div class="ib-row${r.enabled ? "" : " is-off"}">
        <span class="ib-state" data-on="${r.enabled ? 1 : 0}" aria-hidden="true"></span>
        <span class="ib-text">
          <span class="ib-name">${esc(r.name)}${r.enabled ? "" : `<span class="ib-badge">Paused</span>`}</span>
          <span class="ib-meta">${esc(trig)} · ${esc(r.agent_id)} · ${esc(ran)}</span>
        </span>
        <span class="ib-actions">
          <button class="tiny ghost" data-toggle-r="${r.id}" data-on="${r.enabled}">${r.enabled ? "Pause" : "Resume"}</button>
          <button class="tiny ghost" data-edit-r="${r.id}">Edit</button>
          <button class="tiny ghost ib-x" data-del-r="${r.id}" aria-label="Delete ${esc(r.name)}">${IC.close}</button>
        </span></div>`;
    }).join("") : `<div class="ib-empty">No automations yet. One is an instruction plus when to run it.</div>`;
    document.querySelectorAll("[data-edit-r]").forEach((b) => b.onclick = () =>
      routineForm(ROUTINES.find((x) => x.id === b.dataset.editR)));
    document.querySelectorAll("[data-toggle-r]").forEach((b) => b.onclick = async () => {
      await api(`/api/routines/${b.dataset.toggleR}/toggle?on=${b.dataset.on !== "1"}`, { method: "POST" });
      loadRoutines();
    });
    document.querySelectorAll("[data-del-r]").forEach((b) => b.onclick = async () => {
      const r = ROUTINES.find((x) => x.id === b.dataset.delR);
      // Deleting an automation stops future work; it does not undo past work.
      if (!confirm(`Delete "${r ? r.name : "this automation"}"? It stops running from now on — anything it already did stays.`)) return;
      await api(`/api/routines/${b.dataset.delR}`, { method: "DELETE" }); toast("Automation removed"); loadRoutines();
    });
  } catch (_) {}
}
let ROUTINES = [];
let _editingRoutine = null;      // null = creating

// One form for both, because "new" and "edit" differ only in what it opens
// holding and where it saves. Two forms drift, and the one you edit less is
// the one that ends up missing a field.
async function routineForm(existing) {
  _editingRoutine = existing || null;
  const { agents } = await api("/api/agents");
  $("#rmAgent").innerHTML = agents.map((a) =>
    `<option value="${esc(a.id)}"${existing && a.id === existing.agent_id ? " selected" : ""}>${esc(a.name)}</option>`).join("");
  $("#rmName").value = existing ? existing.name : "";
  $("#rmInstruction").value = existing ? existing.instruction : "";
  $("#rmInterval").value = existing ? String(existing.interval_min) : "60";
  $("#rmTrigger").value = existing ? existing.trigger : "new_email";
  $("#rmAtTime").value = (existing && existing.at_time) || "08:00";
  $("#rmDays").value = (existing && existing.days) || "";
  routineTriggerFields();
  const save = $("#rmCreate"); if (save) save.textContent = existing ? "Save" : "Create";
  const title = $("#rmTitle"); if (title) title.textContent = existing ? "Edit automation" : "New automation";
  $("#routineModal").hidden = false;
}

//: Show only the question this trigger actually asks. A box that means a
//: different thing depending on a dropdown above it is how a routine gets set
//: to something nobody chose.
function routineTriggerFields() {
  const kind = $("#rmTrigger").value;
  $("#rmIntervalWrap").hidden = kind !== "schedule";
  $("#rmDailyWrap").hidden = kind !== "daily";
}

$("#newRoutineBtn").onclick = () => routineForm(null);
$("#rmTrigger").onchange = routineTriggerFields;
$("#rmClose").onclick = () => $("#routineModal").hidden = true;
$("#rmCreate").onclick = async () => {
  const name = $("#rmName").value.trim(), instruction = $("#rmInstruction").value.trim();
  if (!name || !instruction) { toast("Name & instruction required"); return; }
  const body = JSON.stringify({ name, agent_id: $("#rmAgent").value, trigger: $("#rmTrigger").value,
    instruction, interval_min: parseInt($("#rmInterval").value) || 60,
    at_time: $("#rmAtTime").value || "", days: $("#rmDays").value || "" });
  const editing = _editingRoutine;
  try {
    if (editing) await api(`/api/routines/${editing.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body });
    else await api("/api/routines", { method: "POST", headers: { "Content-Type": "application/json" }, body });
  } catch (e) { toast("Could not save that automation"); return; }
  $("#routineModal").hidden = true;
  _editingRoutine = null;
  toast(editing ? "Automation updated" : "Automation created");
  loadRoutines();
};

// ── create custom agent ─────────────────────────────────────────────────

//: What a new agent starts with.
//:
//: The category row is checked, and that is a bug fix rather than a
//: preference: every preset ships with it, and an agent built in the app did
//: not — so an agent the user made was born unable to see any connector they
//: had added, with no screen that said so. A connector somebody deliberately
//: connected is one they want their agents to use.
//:
//: Asked of the ROW, not of a list of names, so the category is recognised by
//: what the API says it is. The row only exists when there is a connector
//: behind it, so this cannot check a box that grants nothing.
const NEW_AGENT_TOOLS = ["search_brain", "remember", "web_search"];
function newAgentDefault(t) {
  return isCategoryRow(t) || NEW_AGENT_TOOLS.includes((t && t.name) || "");
}

//: Thirty-two tools in one flat list of pills is a wall, so this borrows the
//: grouping the Agents & tools panel already uses — connectors first, then the
//: built-ins under the headings the API names. Creating an agent and editing
//: one afterwards then look like the same screen, because they are the same
//: list; the only difference is that nothing is saved until Create.
async function openAgentModal() {
  const box = $("#amTools");
  $("#amName").value = ""; $("#amRole").value = ""; $("#amPrompt").value = "";
  $("#agentModal").hidden = false;
  box.innerHTML = `<p class="am-tools-loading">Loading what it could use…</p>`;

  let tools = [], categories = [];
  try { ({ tools, categories } = await api("/api/agents/tools")); }
  catch (e) { box.innerHTML = `<p class="am-tools-loading">Couldn't load the tool list.</p>`; return; }

  // `on` here is the DEFAULT for a new agent, not something already saved.
  const preset = tools.filter(newAgentDefault).map((t) => t.name);
  const groups = agentToolGroups(tools, CONNECTORS, preset, categories);

  box.innerHTML = groups.map((g) => {
    const why = toolBlockedReason(g);
    const rows = g.tools.map(({ row, on }) => {
      const name = (row && row.name) || "";
      // Never a control that cannot work: a blocked group says why instead.
      const control = why
        ? `<span class="am-blocked">${esc(why)}</span>`
        : `<button type="button" role="switch" aria-checked="${on}"
             class="am-toggle${on ? " is-on" : ""}" data-tool="${esc(name)}"
             data-on="${on ? "1" : ""}"><span class="am-knob"></span></button>`;
      return `<div class="am-row">
        <span class="am-text">
          <span class="am-nm">${esc(toolLabel(row))}</span>
          <span class="am-ds">${esc(toolBlurb(row))}</span>
        </span>${control}</div>`;
    }).join("");
    return `<section class="am-group${why ? " is-blocked" : ""}">
      <h4 class="am-group-nm">${esc(g.name)}</h4>
      <div class="am-card">${rows}</div>
    </section>`;
  }).join("");

  // A switch is the user's action; it flips on click and is read back off the
  // DOM at Create, so nothing here talks to the server.
  box.querySelectorAll("[data-tool]").forEach((b) => b.onclick = () => {
    const on = b.dataset.on !== "1";
    b.dataset.on = on ? "1" : "";
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-checked", String(on));
    updateAgentToolCount();
  });
  updateAgentToolCount();
}

//: What this agent will be able to do, said before it exists. Without it the
//: only way to know is to count switches.
function updateAgentToolCount() {
  const el = $("#amToolCount"); if (!el) return;
  const all = document.querySelectorAll("#amTools [data-tool]");
  const on = document.querySelectorAll('#amTools [data-tool][data-on="1"]');
  el.textContent = all.length ? `${on.length} of ${all.length} on` : "";
}

$("#newAgentBtn").onclick = openAgentModal;
$("#amClose").onclick = () => $("#agentModal").hidden = true;
$("#amCreate").onclick = async () => {
  const name = $("#amName").value.trim();
  if (!name) { toast("Name required"); return; }
  const chosen = [...document.querySelectorAll('#amTools [data-tool][data-on="1"]')]
    .map((b) => b.dataset.tool);
  const a = await api("/api/agents/custom", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, role: $("#amRole").value.trim(),
      system_prompt: $("#amPrompt").value.trim(), tools: chosen }) });
  $("#agentModal").hidden = true; toast("Agent created");
  await loadAgents(); selectAgent(a.id);
};

// ── onboarding ───────────────────────────────────────────────────────────
function openOnboard() { $("#onboard").hidden = false; }
function closeOnboard() {
  $("#onboard").hidden = true;
  localStorage.setItem("chitragupta_onboarded", "1");
}
function flashConnectors() {
  const el = $("#connectors");
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600);
}
$("#obSkip").onclick = closeOnboard;
$("#obDone").onclick = closeOnboard;
$("#obGoogle").onclick = () => { closeOnboard(); flashConnectors(); connectGoogle(); };
$("#obLocal").onclick = () => { closeOnboard(); flashConnectors();
  toast("Pick Files, Apple Mail, Calendar or iMessage below → click setup"); };
$("#obApps").onclick = () => { closeOnboard(); flashConnectors();
  toast("Notion · Linear · GitHub — or + Connect a custom app"); };
$("#obFact").onclick = () => { closeOnboard(); $("#ingestText").focus();
  $("#ingestText").scrollIntoView({ behavior: "smooth" }); };
// #helpBtn is gone: "Replay onboarding" moved into the Settings rail, which
// navigates to /onboarding?replay=1. This binding had in fact been dead for a
// while — app.js loads after this file and overwrote the handler with that same
// redirect — but `$("#helpBtn")` is unguarded, so once the button left the
// markup this line threw during script evaluation and took every binding below
// it down with it. Hence: removed, not left to fail quietly.

// ── brain export / import (you own your data) ──────────────────────────────
$("#brainExport").onclick = async () => {
  try {
    const r = await fetch("/api/brain/export");
    if (!r.ok) throw new Error("export failed");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `chitragupta-brain-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
    toast("Brain exported");
  } catch (e) { toast(String(e)); }
};
$("#brainImport").onclick = () => $("#brainImportFile").click();
$("#brainImportFile").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    const r = await api("/api/brain/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data) });
    toast(`Imported ${r.added} memories${r.skipped ? ` · ${r.skipped} already had` : ""}`);
    loadBrain();
  } catch (err) { toast("Import failed — is it a Chitragupta backup?"); }
  finally { e.target.value = ""; }
};

async function maybeOnboard() {
  // Onboarded state lives on the SERVER (survives the desktop app's per-launch
  // port, which resets localStorage). Only a genuine first run goes to onboarding.
  try {
    const s = await api("/api/onboarded");
    if (s.onboarded) return false;
  } catch (_) {}
  if (localStorage.getItem("chitragupta_onboarded")) return false;   // legacy fallback
  window.location.href = "/onboarding";
  return true;
}

//: Shown once, the first time somebody reaches the workspace with onboarding
//: behind them. Nothing is pre-added and there is no lead agent any more, so a
//: new install has NO agents at all — the library is not a nicety here, it is
//: the only way to get one.
const SAW_LIBRARY = "chitragupta_saw_library";

/** Open the Agent Library the first time, and never again on its own. */
function libraryOnFirstRun() {
  if (!localStorage.getItem("chitragupta_onboarded")) return;
  if (localStorage.getItem(SAW_LIBRARY)) return;
  localStorage.setItem(SAW_LIBRARY, "1");
  // A function declaration in library.js, which loads before this file — but
  // guarded anyway, because a missing screen must not break first entry.
  if (typeof openLibrary === "function") openLibrary();
}

// ── live brain-building status ─────────────────────────────────────────────
// Sync started in onboarding keeps running here; this pill shows how the brain
// is filling in real time. Click to kick a fresh sync.
let _wasSyncing = false;
async function updateBrainStatus() {
  const el = $("#brainStatus");
  if (!el) return;
  try {
    const [s, st] = await Promise.all([
      api("/api/sync/status"), api("/api/brain/stats"),
    ]);
    const mem = (st.total || 0).toLocaleString();
    const ent = (st.graph?.entities || 0).toLocaleString();
    if (s.syncing) {
      el.classList.add("syncing");
      el.innerHTML = `<span class="bs-dot"></span>Building your brain… <b>${mem}</b> memories`;
    } else {
      el.classList.remove("syncing");
      el.innerHTML = `<span class="bs-dot"></span>Brain ready · <b>${mem}</b> memories · <b>${ent}</b> entities`;
      if (_wasSyncing) loadBrain();   // refresh panels when a sync just finished
    }
    _wasSyncing = s.syncing;
  } catch (_) {}
}
// clicking the header status (or the sidebar Brain nav) opens the full brain screen
{ const el = $("#brainStatus"); if (el) el.onclick = () => openBrainScreen(); }

// ── full-screen "Your Brain" view: live neural viz + real building status ────
