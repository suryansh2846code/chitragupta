/* The version row — what you are running, and whether there is more.
 *
 * Three states, and a build can only ever be in one of them:
 *
 *   1. **No feed configured.** Says the version and stops. No Check button,
 *      because pressing it could only fail — `/CLAUDE.md`: never show a
 *      control that cannot work.
 *   2. **Up to date.** Says so, says when it last looked, offers Check.
 *   3. **An update exists.** Names the version, links to it, and says plainly
 *      that it is a download rather than pretending to install it.
 *
 * **It says what the check sends, in the UI, in words.** The server tells us
 * (`state.sends`) rather than this file listing the fields, so the disclosure
 * cannot drift from the request — the same reason `updates.request_payload`
 * is one function. A privacy-first product that makes a daily request has to
 * be able to point at the sentence describing it.
 *
 * Names here are `up*` at the top level: these are plain scripts sharing one
 * scope, and a `let` that collides with another file's is a SyntaxError that
 * kills this whole file. See `web/CLAUDE.md`.
 */

let upState = null;

async function loadUpdates() {
  const host = $("#upBody");
  if (!host) return;
  try {
    upState = await api("/api/updates/state");
  } catch (e) {
    host.innerHTML = `<div class="set-row"><div class="set-main">`
      + `<div class="set-desc">${esc(String(e))}</div></div></div>`;
    return;
  }
  upRender();
}

function upRender() {
  const host = $("#upBody");
  if (!host || !upState) return;
  const s = upState;
  const update = s.update;

  host.innerHTML = `
    <div class="set-row">
      <div class="set-main">
        <div class="set-label">${update
          ? `Version ${esc(update.version)} is available`
          : "You are up to date"}</div>
        <div class="set-desc">${upStatusLine(s)}</div>
      </div>
      <div class="set-ctl">
        ${update && update.url
          ? `<button id="upGet" class="tiny">Get it</button>` : ""}
        ${s.configured
          ? `<button id="upCheck" class="tiny ghost">Check now</button>` : ""}
      </div>
    </div>

    ${update && update.notes ? `
      <div class="set-row set-row-block">
        <div class="set-desc">${esc(update.notes)}</div>
      </div>` : ""}

    ${s.configured ? `
      <div class="set-row">
        <div class="set-main">
          <div class="set-label">Check for updates automatically</div>
          <div class="set-desc">
            Once a day at most. It sends ${upSends(s)} — nothing else, and
            nothing that identifies you or your Mac.
          </div>
        </div>
        <div class="set-ctl">
          <button id="upToggle" class="tiny ghost"
                  aria-pressed="${s.enabled ? "true" : "false"}">${
            s.enabled ? "On" : "Off"}</button>
        </div>
      </div>` : ""}
  `;
  upBind();
}

/* The line under the heading. Four different things to say, and the order
 * matters: a failure the user can act on comes before a reassurance. */
function upStatusLine(s) {
  if (!s.configured) {
    return `You are running ${esc(s.current)}. This build has no update feed, `
         + `so it will not check for new versions.`;
  }
  if (s.reason) return esc(s.reason);
  if (s.update) {
    return `You are running ${esc(s.current)}. Updating is a download and a `
         + `drag to Applications — the app will not replace itself.`;
  }
  const when = s.checked_at
    ? `Last checked ${upWhen(s.checked_at)}.`
    : "Not checked yet.";
  return `Version ${esc(s.current)}. ${when}`;
}

/* The field names the server told us it sends, as a phrase. Never a list
 * written here — see the file comment. */
function upSends(s) {
  const WORDS = {
    app: "this app's version",
    os: "your macOS version",
    arch: "whether your Mac is Apple silicon or Intel",
  };
  const parts = (s.sends || []).map((f) => WORDS[f] || f);
  if (!parts.length) return "nothing";
  if (parts.length === 1) return parts[0];
  return parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
}

function upWhen(epochSeconds) {
  const ms = Number(epochSeconds) * 1000;
  if (!ms) return "never";
  const mins = Math.round((Date.now() - ms) / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} minutes ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  return new Date(ms).toLocaleDateString();
}

function upBind() {
  const check = $("#upCheck");
  if (check) check.onclick = upCheckNow;

  const toggle = $("#upToggle");
  if (toggle) toggle.onclick = upToggleAuto;

  const get = $("#upGet");
  if (get) {
    get.onclick = async () => {
      const url = upState?.update?.url;
      if (!url) return;
      // Through the API's own guard, which accepts http(s) only — a release
      // URL arrives from a server, so it is not ours to hand to the system
      // opener unchecked.
      try {
        await api("/api/open-browser", { method: "POST", body: { url } });
      } catch (_) {
        toast("Could not open the download page");
      }
    };
  }
}

async function upCheckNow() {
  const button = $("#upCheck");
  if (button) { button.disabled = true; button.textContent = "Checking…"; }
  try {
    upState = await api("/api/updates/check", { method: "POST" });
  } catch (e) {
    toast(String(e));
  } finally {
    // Re-render whatever happened: the button is inside the markup this
    // replaces, so there is nothing to re-enable by hand.
    upRender();
  }
}

async function upToggleAuto() {
  const wanted = !(upState && upState.enabled);
  try {
    upState = await api("/api/updates/settings",
                        { method: "POST", body: { enabled: wanted } });
  } catch (e) {
    toast(String(e));
  }
  upRender();
}
