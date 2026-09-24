/**
 * Websites agents may read — the visible half of the browser's consent model.
 *
 * It lives on the Connectors screen because that is where a person looks to
 * answer "what can this app reach on my behalf", and a site an agent can read
 * belongs in that answer just as much as a connected mailbox does.
 *
 * **Adding a site is a person's action, and only a person's.** No agent tool
 * reaches these endpoints. An agent that wants a site it does not have can name
 * it and nothing more, which is the only reason the list means anything.
 *
 * The setup button appears only when there is a browser to set up and a way to
 * drive it — `status.drivable` is false in builds that can store the list but
 * cannot yet open a page, and a button that cannot work reads as the app being
 * broken.
 */

let WEB_POLL = null;

async function loadBrowserSites() {
  const box = $("#webSites");
  if (!box) return;
  let s;
  try {
    s = await api("/api/browser/status");
  } catch {
    return;                       // a failed poll must not blank a live list
  }

  renderBrowserSetup(s);

  box.innerHTML = (s.sites || []).length
    ? s.sites.map((site) => `
        <div class="cn-web-row" data-site="${esc(site.host)}">
          <span class="cn-web-host">${esc(site.host)}</span>
          <span class="cn-web-cap">${site.may_act ? "read &amp; change" : "read only"}</span>
          <button class="tiny ghost" data-webact="${esc(site.host)}"
                  data-on="${site.may_act ? "1" : ""}">${
            site.may_act ? "Read only" : "Allow changes"}</button>
          ${site.note && !site.note.startsWith("signed in from")
            ? `<span class="cn-web-note">${esc(site.note)}</span>` : ""}
          <button class="tiny ghost" data-webdel="${esc(site.host)}">Remove</button>
        </div>`).join("")
    // Not an error: this is the correct starting state, and saying so beats an
    // empty box that reads as something having failed to load.
    : `<div class="cn-web-empty">No sites yet. Agents cannot open any page
         until you add one.</div>`;

  // Letting an agent *change* things on a site is a second decision, made after
  // the user has seen reading work — never folded into the press that allowed
  // the site at all. Turning it on does not skip any approval: every click and
  // every keystroke still collects a card. What it decides is whether that card
  // may ever appear for this site.
  box.querySelectorAll("[data-webact]").forEach((b) =>
    b.onclick = async () => {
      const host = b.dataset.webact, turningOn = !b.dataset.on;
      // This press IS the consent, so it has to say what it actually permits.
      // It used to promise a card per click as well, which was true and
      // unusable: one WhatsApp reply is find, click the chat, type, send, and
      // a person saying yes four times for one sentence stops reading by the
      // third. A tap nobody reads is not consent.
      if (turningOn && !confirm(
          `Let agents type and click on ${host}?\n\n`
          + "They can already read it. This lets them fill in its boxes and "
          + "press its buttons while you are here, without asking each time — "
          + "so approve it for a site you would be comfortable watching them "
          + "work in.\n\n"
          + "Automations are never allowed to do this, whatever you set here. "
          + "You can turn it back off at any time.")) return;
      b.disabled = true;
      try {
        await api(`/api/browser/sites/${encodeURIComponent(host)}/acting`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ allowed: turningOn }) });
        toast(turningOn ? `Agents can ask to change things on ${host}`
                        : `${host} is read-only again`);
      } catch (e) {
        toast(`Could not change that — ${String(e)}`);
        b.disabled = false;
        return;
      }
      loadBrowserSites();
    });

  box.querySelectorAll("[data-webdel]").forEach((b) =>
    b.onclick = async () => {
      const host = b.dataset.webdel;
      b.disabled = true;
      try {
        await api(`/api/browser/sites/${encodeURIComponent(host)}`,
                  { method: "DELETE" });
        toast(`Agents can no longer read ${host}`);
      } catch (e) {
        toast(`Could not remove that — ${String(e)}`);
        b.disabled = false;
        return;
      }
      loadBrowserSites();
    });

  renderSiteShelf(s.sites || []);

  const forget = $("#webForget");
  if (forget) forget.hidden = !(s.sites || []).length && !s.installed;

  // Pick a sign-in back up. The state is the server's, so reloading the page
  // mid-sign-in has to find the flow again — otherwise a refresh strands a
  // browser window that nobody can now finish or cancel. This is the one
  // entry point the section has, so it is where that belongs.
  loadConnectState();
}

//: What a connected site says about itself.
//:
//: The grant carries `{origin, host, may_read, may_act, note}` and no account,
//: so there is no username to print — and printing one we do not have is worse
//: than printing none. The note is shown when it is a real one; the sign-in
//: flow writes "signed in from Connectors", which is where the connection came
//: from rather than who it is, so that is not an account either.
function siteAccount(grant) {
  const note = String((grant && grant.note) || "").trim();
  if (!note || /^signed in from /i.test(note)) return "";
  return note;
}

/** One card per site: its mark, whether it is connected, and as whom. */
function renderSiteShelf(grants) {
  const box = $("#webShelf");
  if (!box) return;

  const byHost = new Map();
  for (const g of grants) byHost.set(String(g.host || "").toLowerCase(), g);

  // The catalogue first, then anything the user connected that is not in it —
  // a site added by hand belongs on the shelf too, with a generic mark.
  const rows = SITE_CATALOG.map((spec) => ({
    spec, grant: [...byHost.values()].find((g) => siteSpec(g.host) === spec) || null,
  }));
  for (const g of byHost.values()) {
    if (siteSpec(g.host)) continue;
    rows.push({ spec: { id: g.host, label: g.host, host: g.host,
                        url: "https://" + g.host, tint: "var(--muted)",
                        blurb: "", risk: "", icon: IC.connectors }, grant: g });
  }

  box.innerHTML = rows.map(({ spec, grant }) => {
    const on = Boolean(grant);
    const who = on ? siteAccount(grant) : "";
    const line = on
      ? (who ? esc(who) : "Signed in")
      : esc(spec.blurb || spec.host);
    return `
      <div class="site-card${on ? " is-on" : ""}" data-site-id="${esc(spec.id)}">
        <span class="site-mark logo-tile" style="color:${esc(spec.tint)};--brand:${esc(spec.tint)}"><i class="lt-sheen"></i>${spec.icon}</span>
        <div class="site-text">
          <div class="site-nm">${esc(spec.label)}</div>
          <div class="site-sub">${line}</div>
        </div>
        ${on
          ? `<button type="button" class="tiny ghost" data-site-off="${esc(grant.host)}">Disconnect</button>`
          : `<button type="button" class="tiny" data-site-on="${esc(spec.id)}">Connect</button>`}
      </div>`;
  }).join("");

  box.querySelectorAll("[data-site-on]").forEach((b) => b.onclick = () => {
    const spec = SITE_CATALOG.find((x) => x.id === b.dataset.siteOn);
    if (!spec) return;
    // Straight into the flow that already exists, with the address filled in.
    const input = $("#webConnectInput");
    if (input) { input.value = spec.url; renderConnectRisk(); }
    const go = $("#webConnectGo");
    if (go) go.onclick();
  });

  box.querySelectorAll("[data-site-off]").forEach((b) => b.onclick = async () => {
    const host = b.dataset.siteOff;
    // Disconnect ends the SESSION, not just the permission — say so, because a
    // button that signs you out while promising less is a lie about what it did.
    if (!confirm(`Disconnect ${host}? Agents stop reading it and the sign-in `
                 + "is cleared, so you would sign in again next time.")) return;
    b.disabled = true;
    try {
      await api(`/api/browser/sites/${encodeURIComponent(host)}`, { method: "DELETE" });
      toast(`${host} disconnected`);
    } catch (e) {
      toast(`Could not disconnect that — ${String(e)}`);
      b.disabled = false;
      return;
    }
    loadBrowserSites();
  });
}

/** The one-time download, and what to say while there isn't one. */
function renderBrowserSetup(s) {
  const btn = $("#webSetup"), state = $("#webSetupState");
  if (!btn || !state) return;

  // Nothing to offer in a build that cannot drive a browser. The list still
  // works — it is stored either way — so the section is useful before the
  // download exists, and silent about a button that would do nothing.
  if (!s.drivable) {
    btn.hidden = true;
    state.hidden = false;
    state.textContent = s.installed
      ? "Browsing is set up. This version can remember which sites you allow, but cannot open pages yet."
      : "This version can remember which sites you allow. Opening pages is coming.";
    stopBrowserPoll();
    return;
  }

  if (s.state === "running") {
    btn.hidden = true;
    state.hidden = false;
    state.textContent = `${s.message || "Setting up…"} ${s.percent || 0}%`;
    startBrowserPoll();
    return;
  }

  stopBrowserPoll();
  if (s.state === "error") {
    btn.hidden = false;
    btn.textContent = "Try again";
    state.hidden = false;
    state.textContent = s.message || "Could not set up the browser.";
    return;
  }
  if (s.installed) {
    btn.hidden = true;
    state.hidden = true;
    return;
  }
  btn.hidden = false;
  btn.textContent = "Set up browsing";
  state.hidden = false;
  // Said before it starts, because 150 MB on a slow connection is a surprise
  // worth not having.
  state.textContent = `One-time download, about ${s.approx_mb || 150} MB.`;
}

function startBrowserPoll() {
  if (WEB_POLL) return;
  WEB_POLL = setInterval(loadBrowserSites, 1500);
}
function stopBrowserPoll() {
  if (!WEB_POLL) return;
  clearInterval(WEB_POLL);
  WEB_POLL = null;
}

{
  const add = $("#webAdd"), input = $("#webInput"), err = $("#webErr");
  const show = (message) => {
    if (!err) return;
    err.hidden = !message;
    err.textContent = message || "";
  };

  const allow = async () => {
    const url = (input && input.value || "").trim();
    if (!url) return;
    if (add) add.disabled = true;
    show("");
    try {
      await api("/api/browser/sites", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, note: "" }) });
      if (input) input.value = "";
      toast(`Agents can now read ${url}`);
    } catch (e) {
      // The server's sentence, not a generic one: "only https addresses can be
      // used" tells somebody what to change, and a 400 does not.
      show(String(e).replace(/^Error:\s*/, ""));
    }
    if (add) add.disabled = false;
    loadBrowserSites();
  };

  if (add) add.onclick = allow;
  if (input) input.onkeydown = (e) => { if (e.key === "Enter") allow(); };

  const setup = $("#webSetup");
  if (setup) setup.onclick = async () => {
    setup.disabled = true;
    try { await api("/api/browser/install", { method: "POST" }); }
    catch (e) { toast(`Could not start setup — ${String(e)}`); }
    setup.disabled = false;
    loadBrowserSites();
  };

  const forget = $("#webForget");
  if (forget) forget.onclick = async () => {
    if (!confirm("Forget every website sign-in made inside this app? Agents "
                 + "will need you to sign in again before they can read those "
                 + "sites.")) return;
    try {
      await api("/api/browser/forget-everything", { method: "POST" });
      toast("Every sign-in forgotten");
    } catch (e) { toast(`Could not do that — ${String(e)}`); }
    loadBrowserSites();
  };
}


// ── connecting a site: signing in once, in a window you can watch ──────────
//
// The state lives on the SERVER (`browser/signin.py`), which is what makes this
// survive a refresh: reload mid-sign-in and the panel picks the flow back up
// rather than stranding a browser window nobody can now finish or cancel.
//
// Three states, and the middle one is the one that is easy to get wrong:
//
//   idle              → an address and a Connect button
//   connecting        → "the window is open", Done, and Cancel
//   still_signing_in  → NOT a failure. It is our guess that you are still on
//                       the login page, and pressing Done again overrules it.
//
// That last one matters more than it looks. The check is a heuristic over the
// address the browser landed on; it will be wrong on some site, and a user who
// cannot overrule it is locked out of an account that is already theirs.
let CONNECT_POLL = null;

//: The sites on the shelf — mark, where signing in starts, and the warning if
//: there is one. One row per site, because a logo that lives apart from the
//: address that opens it is two things to keep in step.
//:
//: The warnings are the contract's, not invented here:
//: docs/development/connected-sites.md — "say the risk in the UI, once, before
//: the user connects one of the bottom four. Not buried in a doc."
//:
//: This is editorial copy — judgements about how each company treats
//: automation — and it belongs beside a site catalogue in the backend the day
//: one exists. Until then it is here, said once, rather than in a document
//: nobody opens.
const SITE_CATALOG = [
  {
    id: "linkedin", label: "LinkedIn", host: "linkedin.com",
    url: "https://www.linkedin.com/login", tint: "#3f7fe0",
    blurb: "Your feed, messages and connections.",
    risk: "LinkedIn watches for automation. Reading your own feed at human pace "
      + "is not scraping, but accounts have been restricted for less — connect "
      + "it only if you accept that risk.",
    icon: `<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><path d="M4.98 3.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM3 9h4v12H3V9zm6.5 0h3.8v1.7h.05c.53-.95 1.83-1.95 3.77-1.95 4.03 0 4.78 2.5 4.78 5.76V21h-4v-5.6c0-1.34-.02-3.06-1.9-3.06-1.9 0-2.2 1.45-2.2 2.96V21h-4V9z"/></svg>`,
  },
  {
    id: "whatsapp", label: "WhatsApp", host: "web.whatsapp.com",
    url: "https://web.whatsapp.com", tint: "#5fcf8e",
    blurb: "Your chats, through WhatsApp Web.",
    risk: "This signs in through WhatsApp Web, the same as linking a device. "
      + "Safer than a reimplemented protocol, but not risk-free.",
    icon: `<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><path d="M12 2a10 10 0 0 0-8.6 15.05L2 22l5.1-1.33A10 10 0 1 0 12 2zm0 2a8 8 0 1 1-4.1 14.86l-.3-.18-2.6.68.7-2.53-.2-.32A8 8 0 0 1 12 4zm-3.2 4.3c-.16 0-.42.06-.64.3-.22.24-.84.82-.84 2s.86 2.32.98 2.48c.12.16 1.68 2.68 4.14 3.65 2.05.8 2.47.64 2.91.6.44-.04 1.43-.58 1.63-1.15.2-.56.2-1.05.14-1.15-.06-.1-.22-.16-.46-.28-.24-.12-1.43-.7-1.65-.78-.22-.08-.38-.12-.54.12-.16.24-.62.78-.76.94-.14.16-.28.18-.52.06-.24-.12-1.02-.38-1.94-1.2-.72-.64-1.2-1.43-1.34-1.67-.14-.24-.02-.37.1-.49.11-.11.24-.28.36-.42.12-.14.16-.24.24-.4.08-.16.04-.3-.02-.42-.06-.12-.54-1.3-.74-1.78-.19-.46-.39-.4-.54-.41h-.01z"/></svg>`,
  },
  {
    id: "x", label: "X", host: "x.com",
    url: "https://x.com/login", tint: "#dfe7f2",
    blurb: "Your timeline and messages.",
    risk: "X watches for automation and restricts accounts that look automated.",
    icon: `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M18.24 2.25h3.31l-7.23 8.26 8.5 11.24H16.17l-5.21-6.82L4.99 21.75H1.68l7.73-8.84L1.25 2.25H8.08l4.71 6.23zm-1.16 17.52h1.83L7.01 4.13H5.05z"/></svg>`,
  },
  {
    id: "discord", label: "Discord", host: "discord.com",
    url: "https://discord.com/login", tint: "#b498f0",
    blurb: "Your servers and direct messages.",
    risk: "Discord's rules do not allow automating a user account. A real "
      + "browser session is less clearly against them, not clearly within them.",
    icon: `<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><path d="M19.3 5.4A16.8 16.8 0 0 0 15.1 4l-.3.6a12.6 12.6 0 0 1 3.7 1.9 17.8 17.8 0 0 0-12.9 0A12.7 12.7 0 0 1 9.2 4.6L8.9 4a16.9 16.9 0 0 0-4.2 1.4C2 9.6 1.3 13.6 1.7 17.6a17 17 0 0 0 5.1 2.6l1-1.7c-.9-.3-1.7-.7-2.4-1.2l.5-.4a12.1 12.1 0 0 0 10.2 0l.5.4c-.7.5-1.5.9-2.4 1.2l1 1.7a17 17 0 0 0 5.1-2.6c.5-4.6-.6-8.6-2-12.2zM8.7 15.2c-1 0-1.8-.9-1.8-2s.8-2 1.8-2 1.8.9 1.8 2-.8 2-1.8 2zm6.6 0c-1 0-1.8-.9-1.8-2s.8-2 1.8-2 1.8.9 1.8 2-.8 2-1.8 2z"/></svg>`,
  },
  {
    id: "reddit", label: "Reddit", host: "reddit.com",
    url: "https://www.reddit.com/login", tint: "#e0a45e",
    blurb: "Your feed and saved posts.",
    // No warning: Reddit does not treat a signed-in reader the way the four
    // above do. A direct Reddit connection is the better path and is planned;
    // this works today, which is why it is here.
    risk: "",
    icon: `<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><path d="M22 12a2.1 2.1 0 0 0-3.56-1.5 10.4 10.4 0 0 0-5.4-1.7l.92-4.33 3.01.64a1.8 1.8 0 1 0 .2-1.02l-3.7-.78a.5.5 0 0 0-.6.39l-1.06 5.09a10.4 10.4 0 0 0-5.35 1.7A2.1 2.1 0 1 0 4 15.62a4.1 4.1 0 0 0-.05.63c0 3.2 3.61 5.8 8.06 5.8s8.06-2.6 8.06-5.8a4 4 0 0 0-.05-.62A2.1 2.1 0 0 0 22 12zM8.4 13.5a1.5 1.5 0 1 1 1.5 1.5 1.5 1.5 0 0 1-1.5-1.5zm7.7 4.35a5.3 5.3 0 0 1-4.09 1.3 5.3 5.3 0 0 1-4.09-1.3.44.44 0 0 1 .62-.62 4.5 4.5 0 0 0 3.47 1.04 4.5 4.5 0 0 0 3.47-1.04.44.44 0 1 1 .62.62zm-.2-2.85a1.5 1.5 0 1 1 1.5-1.5 1.5 1.5 0 0 1-1.5 1.5z"/></svg>`,
  },
];

function siteSpec(host) {
  const h = String(host || "").toLowerCase().replace(/^www\./, "");
  return SITE_CATALOG.find((x) => h === x.host || h.endsWith("." + x.host)
    || x.host.endsWith("." + h)) || null;
}

function connectRisk(value) {
  const host = String(value || "").trim().toLowerCase()
    .replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^www\./, "");
  const parts = host.split(".");
  // Walk up to the registered domain, so m.linkedin.com and www.x.com both
  // match the row that warns about them.
  for (let i = 0; i < parts.length - 1; i++) {
    const spec = siteSpec(parts.slice(i).join("."));
    if (spec && spec.risk) return spec.risk;
  }
  return "";
}

function renderConnectRisk() {
  const box = $("#webConnectRisk"), input = $("#webConnectInput");
  if (!box) return;
  const why = connectRisk(input && input.value);
  box.hidden = !why;
  box.textContent = why;
}

/** Draw whichever of the three states the server says we are in. */
function renderConnect(st) {
  const idle = $("#webConnectIdle"), live = $("#webConnectLive");
  const msg = $("#webConnectMsg"), done = $("#webConnectDone");
  if (!idle || !live) return;

  if (!st || !st.connecting) {
    idle.hidden = false;
    live.hidden = true;
    stopConnectPoll();
    return;
  }

  idle.hidden = true;
  live.hidden = false;
  live.classList.toggle("is-waiting", Boolean(st.still_signing_in));
  // The server's sentence when it has one — it knows whether this is the first
  // ask or the "that still looks like a login page" one, and writing our own
  // here would mean two places deciding what the user is being told.
  if (msg) {
    msg.textContent = st.note || (st.host
      ? `A browser window is open at ${st.host}. Sign in there — it is a separate `
        + `window, not part of this app — then come back and press Done.`
      : "A browser window is open. Sign in there, then press Done.");
  }
  // Second press means "I really am in", and says so rather than looking like
  // the same button failing twice.
  //
  // Except when the identity provider is the one refusing. That is not our
  // guess being wrong, so there is nothing to overrule and the second press
  // would be turned down like the first — "Done anyway" there promises an
  // override that cannot happen. The button stays "Done", and it starts
  // working the moment they sign in the way the note above it describes.
  if (done) {
    done.textContent = st.still_signing_in && !st.sso_refused
      ? "Done anyway" : "Done";
  }
  startConnectPoll();
}

async function loadConnectState() {
  try { renderConnect(await api("/api/browser/connect")); }
  catch (_) { /* a failed poll must not tear down a live sign-in */ }
}

function startConnectPoll() {
  if (CONNECT_POLL) return;
  CONNECT_POLL = setInterval(loadConnectState, 2000);
}
function stopConnectPoll() {
  if (!CONNECT_POLL) return;
  clearInterval(CONNECT_POLL);
  CONNECT_POLL = null;
}

{
  const err = $("#webConnectErr");
  const show = (m) => { if (err) { err.hidden = !m; err.textContent = m || ""; } };

  const input = $("#webConnectInput");
  if (input) input.oninput = renderConnectRisk;

  const go = $("#webConnectGo");
  if (go) go.onclick = async () => {
    const url = (input && input.value || "").trim();
    if (!url) return;
    go.disabled = true; show("");
    try {
      const r = await api("/api/browser/connect", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }) });
      // The endpoint answers `{ok: false, error}` rather than raising, so a
      // refusal has to be read out of the body — awaiting it and assuming
      // success is how "already signing in to X" became a silent no-op.
      if (r && r.ok === false) show(r.error || "Could not open that site.");
      else if (input) input.value = "";
    } catch (e) {
      show(String(e).replace(/^Error:\s*/, ""));
    }
    go.disabled = false;
    renderConnectRisk();
    loadConnectState();
  };

  const done = $("#webConnectDone");
  if (done) done.onclick = async () => {
    done.disabled = true; show("");
    // `force` is the second press. The first asks the server to check; the
    // second says the user knows better, which is the whole point of the flag.
    const force = done.textContent.trim() === "Done anyway";
    try {
      const r = await api("/api/browser/connect/finish", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force }) });
      if (r && r.ok) {
        toast(r.detail || `${r.host || "That site"} is connected`);
        show("");
      } else if (r && r.still_signing_in) {
        show("");                 // the live message says it; a red line as well is shouting
      } else {
        show((r && r.error) || "Could not finish connecting.");
      }
    } catch (e) {
      show(String(e).replace(/^Error:\s*/, ""));
    }
    done.disabled = false;
    await loadConnectState();
    loadBrowserSites();
  };

  const cancel = $("#webConnectCancel");
  if (cancel) cancel.onclick = async () => {
    cancel.disabled = true;
    try {
      const r = await api("/api/browser/connect/cancel", { method: "POST" });
      if (r && r.detail) toast(r.detail);
    } catch (e) { toast(`Could not stop that — ${String(e)}`); }
    cancel.disabled = false;
    show("");
    loadConnectState();
  };
}
