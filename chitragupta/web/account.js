/* The user's account — making one, signing into it, and adding a second way in.
 *
 * **An account is not a session**, and the screen shows both because they can
 * disagree in a way the user needs to see: an account restored from a backup,
 * with nobody signed in yet. `state.has_account` and `state.signed_in` are
 * separate for that reason.
 *
 * **Every provider the server declared is drawn, not only the usable ones.** A
 * provider that is simply absent is indistinguishable from one nobody thought
 * about; one that is present and unavailable carries its reason. So Apple
 * appears with the `data-soon` lock the rest of the app uses for exactly this,
 * and clicking it toasts *why* rather than doing nothing — `core.js`'s
 * `markSoon` is the mechanism.
 *
 * **No provider is named here.** The list, the labels and the reasons all come
 * down the wire, so adding Microsoft is a client id on the server and nothing
 * in this file — the `providerId === "x"` chain `/CLAUDE.md` forbids on render
 * paths is exactly what that avoids.
 *
 * **It says what it asks for, in the server's words** (`state.asks_for`, and
 * each provider's own `scopes`), so the disclosure cannot drift from the
 * request. Signing in is always optional and the screen says so.
 *
 * Names are `ac*` at the top level: plain scripts share one scope, and a `let`
 * colliding with another file's is a SyntaxError that kills this whole file.
 */

let acState = null;
let acBusy = false;

async function loadAccount() {
  const host = $("#acBody");
  if (!host) return;
  try {
    acState = await api("/api/account/state");
  } catch (e) {
    host.innerHTML = `<div class="set-row"><div class="set-main">`
      + `<div class="set-desc">${esc(String(e))}</div></div></div>`;
    return;
  }
  acRender();
}

function acRender() {
  const host = $("#acBody");
  if (!host || !acState) return;
  const s = acState;
  const user = s.user;
  const account = s.account;

  host.innerHTML = `
    <div class="set-row">
      <div class="set-main">
        <div class="set-label">${acTitle(s)}</div>
        <div class="set-desc">${acLine(s)}</div>
      </div>
      <div class="set-ctl">
        ${user ? `<button id="acSignOut" class="tiny ghost">Sign out</button>` : ""}
        ${acBusy ? `<button id="acCancel" class="tiny ghost">Cancel</button>` : ""}
      </div>
    </div>

    ${!user ? `
      <div class="set-row set-row-block">
        <div class="ac-providers">
          ${(s.providers || []).map(acProviderButton).join("")}
        </div>
        <div class="set-desc" style="margin-top:10px">
          Signing in is optional — everything works without it. It asks for
          ${esc(s.asks_for || "your name and email address")} and nothing
          else: not your mail, not your files, not your calendar. Those are
          separate, under Services below.
        </div>
      </div>` : ""}

    ${user && account ? acLinkedRows(s) : ""}
  `;
  markSoon(host);
  acBind();
}

/* One button per provider the server declared. An unavailable one is drawn
 * `data-soon`, so `core.js` dulls it, puts a lock on it, and answers a click
 * with the reason — rather than it being absent and unexplained. */
function acProviderButton(p) {
  const label = `${p.linked ? "Signed in with" : "Continue with"} ${esc(p.label)}`;
  if (!p.available) {
    return `<button class="tiny ghost ac-provider" data-soon="${esc(p.reason)}"
             >Continue with ${esc(p.label)}</button>`;
  }
  return `<button class="tiny ac-provider" data-provider="${esc(p.id)}"${
    acBusy ? " disabled" : ""}>${acBusy ? "Waiting…" : label}</button>`;
}

/* What is already linked, and what can still be added. Only shown when signed
 * in, because linking requires proving you hold the account first. */
function acLinkedRows(s) {
  const linked = (s.providers || []).filter((p) => p.linked);
  const addable = (s.providers || []).filter((p) => !p.linked);
  return `
    <div class="set-row set-row-block">
      <div class="set-label">Ways to sign in</div>
      <div class="set-desc">
        Any of these reaches this account. Adding one needs you signed in
        first, which is what makes it safe — we never join two accounts
        because their email addresses match.
      </div>
      <ul class="ac-linked">
        ${linked.map((p) => `
          <li>
            <span>${esc(p.label)}</span>
            ${linked.length > 1
              ? `<button class="ts-btn-link ac-unlink" data-provider="${esc(p.id)}"
                  >Remove</button>`
              : `<span class="model-hint">your only way in</span>`}
          </li>`).join("")}
      </ul>
      <div class="bk-row" style="margin-top:10px">
        ${addable.map((p) => p.available
          ? `<button class="tiny ghost ac-link" data-provider="${esc(p.id)}"
              >Add ${esc(p.label)}</button>`
          : `<button class="tiny ghost" data-soon="${esc(p.reason)}"
              >Add ${esc(p.label)}</button>`).join("")}
      </div>
    </div>`;
}

function acTitle(s) {
  if (s.user) return esc(s.user.name || s.user.email || "Signed in");
  if (s.has_account) return "Signed out";
  return "No account yet";
}

function acLine(s) {
  if (s.user) {
    const when = s.user.signed_in_at
      ? ` Signed in ${acWhen(s.user.signed_in_at)}.` : "";
    return `${esc(s.user.email || "")}.${when} One Mac per licence — this `
         + `machine is registered.`;
  }
  if (s.has_account) {
    // The account survived a sign-out or came back with a restore. Saying so
    // is the difference between "sign in again" and "start over".
    return `This Mac has an account${s.account && s.account.email
      ? ` (${esc(s.account.email)})` : ""}. Sign in to use it again — your `
      + `brain, agents and connections are untouched either way.`;
  }
  return "Chitragupta works fully without an account. Making one is for "
       + "keeping your backups together across machines later.";
}

function acWhen(epochSeconds) {
  const ms = Number(epochSeconds) * 1000;
  if (!ms) return "";
  const days = Math.floor((Date.now() - ms) / 86400000);
  if (days < 1) return "today";
  if (days === 1) return "yesterday";
  return new Date(ms).toLocaleDateString();
}

function acBind() {
  const host = $("#acBody");
  (host ? host.querySelectorAll(".ac-provider") : []).forEach((b) => {
    if (!b.dataset.provider) return;        // the locked ones answer via markSoon
    b.onclick = () => acSignIn(b.dataset.provider);
  });
  (host ? host.querySelectorAll(".ac-link") : []).forEach((b) => {
    b.onclick = () => acSignIn(b.dataset.provider, true);
  });
  (host ? host.querySelectorAll(".ac-unlink") : []).forEach((b) => {
    b.onclick = () => acUnlink(b.dataset.provider);
  });

  const outBtn = $("#acSignOut");
  if (outBtn) outBtn.onclick = acSignOut;
  const cancel = $("#acCancel");
  if (cancel) cancel.onclick = acCancel;
}

async function acSignIn(provider, link) {
  if (acBusy) return;
  let started;
  try {
    started = await api("/api/account/signin/begin", {
      method: "POST", body: { provider: provider, link: !!link },
    });
  } catch (e) {
    toast(String(e));
    return;
  }

  acBusy = true;
  acRender();

  // Through the API's own guard, which accepts http(s) only.
  try {
    await api("/api/open-browser", { method: "POST", body: { url: started.url } });
  } catch (_) {
    toast("Could not open your browser — copy the link from the log");
  }

  // `finish` blocks until the browser comes back, up to three minutes. The
  // button says what it is waiting for rather than spinning with no end state.
  try {
    acState = await api("/api/account/signin/finish", { method: "POST" });
    toast(link ? "Added" : "Signed in");
  } catch (e) {
    toast(String(e));
    try { acState = await api("/api/account/state"); } catch (_) {}
  } finally {
    acBusy = false;
    acRender();
  }
}

async function acCancel() {
  try { await api("/api/account/signin/cancel", { method: "POST" }); } catch (_) {}
  acBusy = false;
  try { acState = await api("/api/account/state"); } catch (_) {}
  acRender();
}

async function acUnlink(provider) {
  if (!confirm(`Remove ${provider} as a way of signing in?\n\n`
             + "Your account and everything in it stays. You will sign in "
             + "with one of the others instead.")) return;
  try {
    acState = await api("/api/account/unlink",
                        { method: "POST", body: { provider } });
  } catch (e) {
    toast(String(e));
  }
  acRender();
}

async function acSignOut() {
  if (!confirm("Sign out of Chitragupta?\n\n"
             + "Your brain, your agents and your connected services all stay "
             + "exactly as they are.")) return;
  try {
    acState = await api("/api/account/signout", { method: "POST" });
  } catch (e) {
    toast(String(e));
  }
  acRender();
}
