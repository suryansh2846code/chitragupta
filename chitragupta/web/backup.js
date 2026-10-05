/* Backup and recovery — the screen.
 *
 * Four things happen here, and the order they are allowed to happen in is the
 * whole design:
 *
 *   1. Say what a backup will and will not contain, BEFORE asking for anything.
 *   2. Take a passphrase, make the backup, show progress, allow Stop.
 *   3. Show the recovery code exactly once, and refuse to move on until the
 *      user confirms they kept it.
 *   4. Restore: inspect a file (no secret), then ask for one, then reconnect.
 *
 * **The recovery code dialog cannot be re-opened, and that is deliberate.** The
 * server generates it, hands it over once and stores only its wrap, so there is
 * no endpoint that could show it again — see `archive/identity.py`. A user who
 * dismisses it has one door to their backups instead of two, so the screen
 * keeps a standing warning until `/api/backup/recovery-code/saved` is called.
 * Nagging is the correct behaviour here: the alternative is a silent downgrade
 * the user discovers on the worst day they will ever have with this app.
 *
 * **Progress is polled, never pushed.** The job lives server-side so a reload
 * finds it still running (`archive/job.py`), which means this file must be able
 * to *join* a job in progress rather than only start one — `bkSync` is written
 * for the reload case first and the start case second.
 *
 * Names here are `bk*` at the top level. These are plain scripts sharing one
 * scope, so a `let` that collides with another file's is a SyntaxError that
 * kills this whole file, and the symptom is a dead screen rather than an error
 * anyone sees — `web/CLAUDE.md` and `profile.js` both carry this warning.
 */

let bkPoll = null;          // the interval handle while a job runs
let bkState = null;         // last /api/backup/state
let bkPicked = null;        // the file the restore flow is inspecting

/* The `finished` stamp of the job whose outcome has already been reported.
 *
 * Without it this screen spins forever: `bkSync` finishes a backup, calls
 * `bkLoad` to pick up the new file, and `bkLoad` ends by calling `bkSync` to
 * join any running job — which sees the same finished job and calls `bkLoad`
 * again. Mutual recursion with a network request on each turn, so the symptom
 * in a browser is not a hang but a tight loop hammering the server. A
 * `tests/js/` harness is what caught it; `node --check` cannot see it and
 * neither can reading either function on its own. */
let bkSeen = 0;

const BK_PHRASE_MIN = 8;

/* ── reading the current situation ──────────────────────────────────────── */

async function openBackupScreen() {
  const m = $("#modelScreen");
  if (!m) return;
  m.hidden = false;
  showSettingsPanel("backup");
  await bkLoad();
}

async function bkLoad() {
  try {
    bkState = await api("/api/backup/state");
  } catch (e) {
    bkRender({ error: String(e) });
    return;
  }
  bkRender();
  // A job may already be running — this screen was perhaps just reopened, or
  // the page reloaded mid-backup. Joining is the common case, not the odd one.
  await bkSync();
}

/* ── the screen ─────────────────────────────────────────────────────────── */

function bkRender(extra) {
  const host = $("#bkBody");
  if (!host) return;
  if (extra && extra.error) {
    host.innerHTML = `<p class="ms-sub" style="color:var(--danger)">${esc(extra.error)}</p>`;
    return;
  }
  const s = bkState || {};
  const withheld = s.withheld || {};
  const backups = s.backups || [];

  host.innerHTML = `
    ${bkWarningHtml(s)}

    <section class="ms-sec">
      <h2 class="ms-h2">Make a backup</h2>
      <p class="ms-sub">
        Everything your agents know — their personas, what they may do, your
        automations, tasks, reminders and measurements — in one encrypted file.
        It is unreadable without your passphrase, including by us.
      </p>

      <label class="ctx-label" for="bkPhrase">Passphrase</label>
      <input id="bkPhrase" class="ctx-input" type="password" autocomplete="new-password"
             placeholder="At least ${BK_PHRASE_MIN} characters" />
      <p id="bkPhraseHint" class="model-hint"></p>

      <label class="ctx-label" for="bkPath">Save to</label>
      <input id="bkPath" class="ctx-input" type="text" spellcheck="false"
             placeholder="${esc(s.default_dir || "")}" />
      <p class="model-hint">Leave empty to use the folder above.</p>

      <div class="bk-row" style="margin-top:14px">
        <button id="bkStart" class="tiny">Back up now</button>
        <button id="bkStop" class="tiny ghost" hidden>Stop</button>
      </div>

      <div id="bkProgress" class="bk-progress" hidden>
        <div class="bk-bar"><div id="bkBarFill" class="bk-bar-fill"></div></div>
        <p id="bkPhase" class="model-hint"></p>
      </div>
      <p id="bkOutcome" class="model-hint"></p>
    </section>

    <section class="ms-sec">
      <h2 class="ms-h2">What a backup never contains</h2>
      <p class="ms-sub">
        Your sign-ins to other services stay on this Mac. If a backup were ever
        stolen it could not be used to read your mail or your messages — so
        after restoring on a new Mac you reconnect each one, with a tap.
      </p>
      <ul class="bk-list">
        ${Object.entries(withheld).map(
          ([name, why]) => `<li><code>${esc(name)}</code> — ${esc(why)}</li>`).join("")}
      </ul>
    </section>

    <section class="ms-sec">
      <h2 class="ms-h2">Restore</h2>
      <p class="ms-sub">
        Putting a backup back replaces what is on this Mac. Anything it replaces
        is renamed rather than deleted, so a restore you did not mean is
        undoable.
      </p>
      <label class="ctx-label" for="bkRestorePath">Backup file</label>
      <input id="bkRestorePath" class="ctx-input" type="text" spellcheck="false"
             placeholder="Path to a .cgarch file" />
      <div class="bk-row" style="margin-top:10px">
        <button id="bkInspect" class="tiny ghost">Have a look first</button>
      </div>
      <div id="bkInspected" class="bk-inspected" hidden></div>
    </section>

    ${backups.length ? `
    <section class="ms-sec">
      <h2 class="ms-h2">Backups on this Mac</h2>
      <ul class="bk-list">
        ${backups.map((b) => `
          <li>
            <code>${esc(b.name)}</code>
            <span class="model-hint"> ${bkSize(b.bytes)} · ${bkWhen(b.modified)}</span>
            <button class="ts-btn-link bk-use" data-path="${esc(b.path)}">Restore this</button>
          </li>`).join("")}
      </ul>
    </section>` : ""}
  `;
  bkBind();
}

function bkWarningHtml(s) {
  if (!s.set_up || s.recovery_code_saved) return "";
  // No "show it again" control, because there is no endpoint that could serve
  // one — the code is never stored (`archive/identity.py`). Offering a button
  // that cannot work would be worse than admitting it.
  return `
    <div class="bk-warn" role="alert">
      <strong>Your recovery code has not been saved.</strong>
      It was shown once and cannot be shown again. It is the only way into your
      backups if you forget your passphrase — so if you still have it, confirm
      below. If you do not, make sure your passphrase is written down
      somewhere safe: it is now the only way in.
      <div class="bk-row" style="margin-top:10px">
        <button id="bkSaved" class="tiny ghost">I have saved it</button>
      </div>
    </div>`;
}

function bkBind() {
  const start = $("#bkStart");
  if (start) start.onclick = bkStart;
  const stop = $("#bkStop");
  if (stop) stop.onclick = bkStopJob;
  const inspect = $("#bkInspect");
  if (inspect) inspect.onclick = () => bkInspect($("#bkRestorePath").value);
  const saved = $("#bkSaved");
  if (saved) saved.onclick = bkMarkSaved;

  const phrase = $("#bkPhrase");
  if (phrase) phrase.oninput = bkCheckPhrase;

  document.querySelectorAll(".bk-use").forEach((b) => {
    b.onclick = () => {
      const field = $("#bkRestorePath");
      if (field) field.value = b.dataset.path;
      bkInspect(b.dataset.path);
    };
  });
}

function bkCheckPhrase() {
  const field = $("#bkPhrase");
  const hint = $("#bkPhraseHint");
  if (!field || !hint) return true;
  const value = field.value || "";
  if (!value) { hint.textContent = ""; hint.style.color = ""; return false; }
  const ok = value.length >= BK_PHRASE_MIN;
  // Says what to do, not what is wrong: "too short" leaves the user guessing
  // by how much.
  hint.textContent = ok
    ? "Keep this somewhere you will still have it in a year."
    : `${BK_PHRASE_MIN - value.length} more character(s) needed.`;
  hint.style.color = ok ? "" : "var(--danger)";
  return ok;
}

/* ── making a backup ────────────────────────────────────────────────────── */

async function bkStart() {
  if (!bkCheckPhrase()) { $("#bkPhrase").focus(); return; }
  const passphrase = $("#bkPhrase").value;
  const path = ($("#bkPath").value || "").trim();
  bkSay("");
  try {
    const started = await api("/api/backup/start", {
      method: "POST",
      body: { passphrase, path: path || null },
    });
    // Clear it immediately: there is no reason for a passphrase to sit in a
    // DOM node for the rest of the session.
    $("#bkPhrase").value = "";
    bkCheckPhrase();
    if (started.recovery_code) bkShowCode(started.recovery_code);
    bkWatch();
  } catch (e) {
    bkSay(String(e), true);
  }
}

async function bkStopJob() {
  try { await api("/api/backup/stop", { method: "POST" }); } catch (_) {}
}

/* ── progress ───────────────────────────────────────────────────────────── */

function bkWatch() {
  if (bkPoll) return;
  bkPoll = setInterval(bkSync, 700);
  bkSync();
}

async function bkSync() {
  let s;
  try {
    s = await api("/api/backup/status");
  } catch (_) {
    return;                       // a transient failure must not kill the loop
  }
  const running = !!s.running;
  const box = $("#bkProgress");
  const stop = $("#bkStop");
  const start = $("#bkStart");
  if (box) box.hidden = !running;
  if (stop) stop.hidden = !running;
  if (start) start.disabled = running;

  if (running) {
    const p = s.progress || {};
    const fill = $("#bkBarFill");
    if (fill) {
      // A phase with no countable total reports 0 — show it as an
      // indeterminate stripe rather than a bar frozen at zero, which reads as
      // stuck.
      const known = (p.total || 0) > 0;
      fill.classList.toggle("is-indeterminate", !known);
      fill.style.width = known ? `${Math.round((p.fraction || 0) * 100)}%` : "100%";
    }
    const phase = $("#bkPhase");
    if (phase) {
      phase.textContent = [p.phase, p.detail].filter(Boolean).join(" — ")
        + ((p.total || 0) > 0 ? ` (${p.done}/${p.total})` : "");
    }
    return;
  }

  if (bkPoll) { clearInterval(bkPoll); bkPoll = null; }

  // Report an outcome once. A reload mid-job still gets it — `bkSeen` starts
  // at 0 and the server keeps the result until the next job starts — but
  // re-entering `bkSync` for the same finished job does nothing.
  if (!s.finished || s.finished === bkSeen) return;
  bkSeen = s.finished;

  if (s.cancelled) {
    bkSay("Stopped. Nothing was written.");
  } else if (s.error) {
    bkSay(s.error, true);
  } else if (s.result && s.kind === "backup") {
    bkSay(`Backed up ${s.result.summary} to ${s.result.path}`);
    bkLoad();                                  // refresh the list of backups
  } else if (s.result && s.kind === "restore") {
    bkAfterRestore(s.result);
  }
}

function bkSay(text, bad) {
  const el = $("#bkOutcome");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = bad ? "var(--danger)" : "";
}

/* ── the recovery code, shown once ──────────────────────────────────────── */

function bkShowCode(code) {
  const dlg = $("#bkCodeModal");
  const out = $("#bkCodeValue");
  if (!dlg || !out) { alert(`Your recovery code:\n\n${code}`); return; }
  out.textContent = code;
  dlg.hidden = false;

  const copy = $("#bkCodeCopy");
  if (copy) {
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(code);
        toast("Recovery code copied");
      } catch (_) {
        toast("Could not copy — select the code and copy it by hand");
      }
    };
  }
  const done = $("#bkCodeDone");
  if (done) {
    done.onclick = async () => {
      await bkMarkSaved();
      dlg.hidden = true;
      // Overwrite the node: the code should not stay in the DOM once the
      // dialog is closed.
      out.textContent = "";
    };
  }
}

async function bkMarkSaved() {
  try {
    await api("/api/backup/recovery-code/saved", { method: "POST" });
    bkState = await api("/api/backup/state");
    bkRender();
  } catch (e) {
    toast(String(e));
  }
}

/* ── restoring ──────────────────────────────────────────────────────────── */

async function bkInspect(rawPath) {
  const path = (rawPath || "").trim();
  const box = $("#bkInspected");
  if (!box) return;
  if (!path) { toast("Choose a backup file first"); return; }
  box.hidden = false;
  box.innerHTML = `<p class="model-hint">Reading…</p>`;
  let seen;
  try {
    seen = await api("/api/backup/inspect", { method: "POST", body: { path } });
  } catch (e) {
    box.innerHTML = `<p class="model-hint" style="color:var(--danger)">${esc(String(e))}</p>`;
    return;
  }
  bkPicked = seen;

  const items = (seen.manifest || []).length;
  const reconnect = seen.reconnect_needed || [];
  const byCode = (seen.unlock_methods || []).includes("recovery");

  box.innerHTML = `
    <p class="ms-sub">
      Made ${esc(bkWhenIso(seen.created_at))} by Chitragupta ${esc(seen.app_version || "?")}
      — ${items} item(s), ${bkSize(seen.bytes)} before encryption.
    </p>
    ${reconnect.length ? `
      <p class="ms-sub">After restoring you will reconnect:
        ${reconnect.map((n) => `<code>${esc(n)}</code>`).join(", ")}.</p>` : ""}

    <label class="ctx-label" for="bkRestorePhrase">Passphrase</label>
    <input id="bkRestorePhrase" class="ctx-input" type="password"
           autocomplete="current-password" placeholder="The passphrase for this backup" />
    ${byCode ? `
      <label class="ctx-label" for="bkRestoreCode">…or your recovery code</label>
      <input id="bkRestoreCode" class="ctx-input" type="text" spellcheck="false"
             placeholder="XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX" />` : ""}

    <div class="bk-row" style="margin-top:14px">
      <button id="bkRestoreGo" class="tiny">Restore this backup</button>
    </div>
  `;
  const go = $("#bkRestoreGo");
  if (go) go.onclick = () => bkRestore(seen.path);
}

async function bkRestore(path) {
  const passphrase = ($("#bkRestorePhrase") || {}).value || "";
  const code = ($("#bkRestoreCode") || {}).value || "";
  if (!passphrase && !code) { toast("Enter the passphrase or the recovery code"); return; }
  if (!confirm("Replace what is on this Mac with this backup?\n\n"
             + "Anything replaced is renamed rather than deleted, so this can "
             + "be undone.")) return;
  bkSay("");
  try {
    await api("/api/backup/restore", {
      method: "POST",
      body: {
        path,
        passphrase: passphrase || null,
        recovery_code: code || null,
        replace_existing: true,
      },
    });
    bkWatch();
  } catch (e) {
    bkSay(String(e), true);
  }
}

async function bkAfterRestore(result) {
  bkSay("Restored. Bringing your brain up to date…");
  try {
    await api("/api/backup/finish-restore", { method: "POST" });
  } catch (_) {
    // A migration that could not run is not a failed restore — the files are
    // in place. Say the honest thing rather than reporting a disaster.
    bkSay("Restored, but the brain could not be re-checked. Restart the app.", true);
    return;
  }
  const reconnect = result.reconnect_needed || [];
  const replaced = result.replaced || [];
  const box = $("#bkInspected");
  if (box) {
    box.hidden = false;
    box.innerHTML = `
      <h3 class="ms-h2">Restored</h3>
      <p class="ms-sub">Your agents, memories, permissions and measurements are back.</p>
      ${reconnect.length ? `
        <p class="ms-sub"><strong>Reconnect these, one tap each:</strong></p>
        <ul class="bk-list">
          ${reconnect.map((n) => `<li><code>${esc(n)}</code> — ${esc(
            (result.withheld || {})[n] || "needs signing in again")}</li>`).join("")}
        </ul>
        <div class="bk-row" style="margin-top:10px">
          <button class="tiny ghost bk-goto" data-panel="connectors">Open Connectors</button>
          <button class="tiny ghost bk-goto" data-panel="account">Open Account</button>
        </div>` : ""}
      ${replaced.length ? `
        <p class="model-hint">Replaced, and kept beside the new files with
        <code>${esc(result.replaced_suffix || ".replaced")}</code> on the end:
        ${replaced.map((n) => esc(n)).join(", ")}</p>` : ""}
    `;
    // Bound, not inlined: `app.js` is evaluated with `new Function` by the test
    // harnesses, and an inline handler is a second place this screen's
    // behaviour would live.
    box.querySelectorAll(".bk-goto").forEach((b) => {
      b.onclick = () => showSettingsPanel(b.dataset.panel);
    });
  }
  bkSay("");
}

/* ── small formatting helpers ──────────────────────────────────────────── */

function bkSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function bkWhen(epochSeconds) {
  const ms = Number(epochSeconds) * 1000;
  if (!ms) return "";
  return new Date(ms).toLocaleString();
}

function bkWhenIso(iso) {
  if (!iso) return "at an unknown time";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}
