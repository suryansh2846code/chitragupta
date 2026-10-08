/**
 * Connecting a model provider, and signing in to one.
 *
 * The catalog and its live state (`PROVIDERS`, `MODEL_CATALOG`), the provider
 * cards on the AI model screen, the floating sign-in HUD, the CLI-install
 * instructions, and the subscription-usage formatting.
 *
 * Loaded after `core.js` and before `app.js`, in the same global scope — these
 * are plain scripts, not modules, because the harnesses evaluate the app with
 * `new Function`. See docs/development/frontend-testing.md.
 *
 * **This file owns `PROVIDERS` and `MODEL_CATALOG`**, which is why it sits
 * upstream of `app.js`: the model picker, the agent-model chips and the
 * composer all read them, and the owner should load before its readers. The one
 * reference running the other way is `loadProviders()`, which lives in `app.js`
 * and is only ever called from an event handler — long after every script has
 * run.
 *
 * `renderProviderConnectBox` is the largest function in the frontend and the
 * one both historic blind bugs lived in: a temporal dead-zone read that blanked
 * the whole drawer, and a card written into a container a re-render had already
 * replaced. It is covered by tests/js/render_provider_box.mjs and
 * tests/js/click_signin.mjs — execute it there, never just parse it.
 */

const MODEL_HINTS = {
  claude: "e.g. claude-opus-5, claude-sonnet-5, claude-haiku-4-5-20251001",
  anthropic: "e.g. claude-opus-5, claude-sonnet-5, claude-haiku-4-5-20251001",
  cursor: "e.g. cursor-fast, cursor-small, claude-sonnet-5, gpt-5.6-terra",
  gemini: "e.g. gemini-3.6-flash, gemini-3.1-pro-preview, gemini-2.5-flash",
  xai: "e.g. grok-4.6, grok-4.5, grok-4.3",
  openai: "e.g. gpt-5.6-terra, gpt-5.6-luna, gpt-5.6-sol, gpt-6-astra",
  deepseek: "e.g. deepseek-chat (V3), deepseek-reasoner (R1)",
  ollama: "e.g. llama3.3:70b, llama3.2, qwen2.5-coder:7b, deepseek-r1:8b",
  "claude-code": "uses your local Claude CLI session",
  openrouter: "e.g. anthropic/claude-sonnet-5, openai/gpt-5.6-terra",
  subscription: "local session gateway proxy",
  mock: "offline test model",
};

function applyModelHint() {
  const prov = $("#provider") ? $("#provider").value : "";
  if (!prov) return;
  $("#modelHint").textContent = MODEL_HINTS[prov] || "";
  updatePrivacyBadge();
  const defBox = $("#defaultProviderConnectBox");
  if (defBox) renderProviderConnectBox(defBox, prov);
}

function updatePrivacyBadge() {
  const el = $("#privacyBadge");
  if (!el) return;
  const p = (PROVIDERS || []).find((x) => x.name === $("#provider").value);
  if (!p) { el.hidden = true; return; }
  el.hidden = false;
  const local = p.locality === "local";
  el.className = "privacy-badge " + (local ? "loc-local" : "loc-cloud");
  el.innerHTML = `<span class="pb-ic">${local ? IC.lock : IC.cloud}</span>` +
    `<span>${local ? "On-device" : "Leaves your Mac"}</span>`;
  el.title = p.destination || "";
}

const FALLBACK_CATALOG = [
  {"id": "claude", "label": "Claude (Anthropic)", "connected": false, "key_env": "ANTHROPIC_API_KEY", "key_url": "https://console.anthropic.com/settings/keys", "destination": "Sent to Anthropic's API.", "locality": "cloud", "default_model": "claude-sonnet-5", "models": [{"id": "claude-opus-5", "name": "Claude Opus 5", "desc": "Frontier intelligence & highest-capacity reasoning"}, {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "desc": "Balanced speed, coding & agentic reasoning"}, {"id": "claude-fable-5", "name": "Claude Fable 5", "desc": "Most capable for the hardest, longest-running work"}, {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "desc": "Fast & responsive everyday model"}]},
  {"id": "cursor", "label": "Cursor", "connected": false, "key_env": "CURSOR_API_KEY", "key_url": "https://cursor.com/docs/cli/overview", "destination": "Connects to your local Cursor session bridge or API.", "locality": "local", "default_model": "auto", "models": [{"id": "auto", "name": "Auto", "desc": "Let Cursor pick the best model for each turn"}, {"id": "claude-opus-5-high", "name": "Claude Opus 5", "desc": "Frontier reasoning via Cursor", "locked": true, "plan_required": "Cursor Pro"}, {"id": "claude-sonnet-5-thinking-high", "name": "Claude Sonnet 5 Thinking", "desc": "Balanced agentic coding via Cursor", "locked": true, "plan_required": "Cursor Pro"}, {"id": "gpt-5.3-codex", "name": "Codex 5.3", "desc": "OpenAI Codex via Cursor", "locked": true, "plan_required": "Cursor Pro"}, {"id": "composer-2.5", "name": "Composer 2.5", "desc": "Cursor's own fast model", "locked": true, "plan_required": "Cursor Pro"}]},
  {"id": "gemini", "label": "Google Gemini", "connected": false, "key_env": "GEMINI_API_KEY", "key_url": "https://aistudio.google.com/apikey", "destination": "Sent to Google Gemini API.", "locality": "cloud", "default_model": "gemini-3.6-flash", "models": [{"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash", "desc": "Latest fast reasoning & multimodal model"}, {"id": "gemini-3.6-flash", "name": "Gemini 3.6 Flash", "desc": "Fast reasoning & multimodal"}, {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview", "desc": "Deep reasoning across complex domains"}, {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "desc": "Previous-generation deep reasoning"}, {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "Previous-generation speed & multimodal"}]},
  {"id": "xai", "label": "xAI (Grok)", "connected": false, "key_env": "XAI_API_KEY", "key_url": "https://console.x.ai", "destination": "Sent to xAI Grok API.", "locality": "cloud", "default_model": "grok-4.6", "models": [{"id": "grok-4.6", "name": "Grok 4.6", "desc": "Latest flagship reasoning model"}, {"id": "grok-4.5", "name": "Grok 4.5", "desc": "Strong reasoning & tool calling"}, {"id": "grok-4.3", "name": "Grok 4.3", "desc": "Fast general-purpose reasoning"}]},
  {"id": "openai", "label": "OpenAI", "connected": false, "key_env": "OPENAI_API_KEY", "key_url": "https://platform.openai.com/api-keys", "destination": "Sent to OpenAI's API.", "locality": "cloud", "default_model": "gpt-5.6-terra", "models": [{"id": "gpt-5.6-terra", "name": "GPT-5.6-Terra", "desc": "Balanced agentic coding model for everyday work"}, {"id": "gpt-5.6-luna", "name": "GPT-5.6-Luna", "desc": "Fast and affordable agentic coding model"}, {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol", "desc": "Flagship agentic coding model for complex tasks", "locked": true, "plan_required": "Pro"}, {"id": "gpt-6-astra", "name": "GPT-6-Astra", "desc": "Our most capable model for complex, demanding work", "locked": true, "plan_required": "Pro"}, {"id": "gpt-reserve", "name": "GPT-Reserve", "desc": "Fast and affordable backup agentic coding model"}, {"id": "o3-mini", "name": "o3-mini", "desc": "Fast STEM & code reasoning", "locked": true, "plan_required": "Plus"}, {"id": "gpt-5.5", "name": "GPT-5.5", "desc": "Proven previous-generation coding model"}, {"id": "gpt-5.4", "name": "GPT-5.4", "desc": "Earlier-generation coding model"}, {"id": "gpt-5.4-mini", "name": "GPT-5.4-Mini", "desc": "Compact, fast earlier-generation model"}]},
  {"id": "deepseek", "label": "DeepSeek", "connected": false, "key_env": "DEEPSEEK_API_KEY", "key_url": "https://platform.deepseek.com/api_keys", "destination": "Sent to DeepSeek's API.", "locality": "cloud", "default_model": "deepseek-chat", "models": [{"id": "deepseek-chat", "name": "DeepSeek Chat", "desc": "Current chat model (alias \u2014 always the latest)"}, {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "desc": "Current reasoning model (alias \u2014 always the latest)"}]},
  {"id": "ollama", "label": "Ollama (Local)", "connected": false, "key_env": "", "key_url": "https://ollama.com", "destination": "Runs on your Mac \u2014 your context stays on-device.", "locality": "local", "default_model": "llama3.2", "models": [{"id": "llama3.2", "name": "Llama 3.2", "desc": "Compact offline local model (Installed)"}, {"id": "qwen2.5:3b", "name": "Qwen 2.5 (3B)", "desc": "Compact multilingual & coding model (Installed)"}, {"id": "llama3.3:70b", "name": "Llama 3.3 (70B)", "desc": "Latest flagship open weights model", "locked": true, "plan_required": "Pull required"}, {"id": "qwen2.5-coder:7b", "name": "Qwen 2.5 Coder (7B)", "desc": "Strong multilingual & coding local model", "locked": true, "plan_required": "Pull required"}, {"id": "deepseek-r1:8b", "name": "DeepSeek R1 (8B)", "desc": "Local reasoning model", "locked": true, "plan_required": "Pull required"}]},
  {"id": "openrouter", "label": "OpenRouter", "connected": false, "key_env": "OPENROUTER_API_KEY", "key_url": "https://openrouter.ai/keys", "destination": "Sent to OpenRouter (and the chosen model's host).", "locality": "cloud", "default_model": "anthropic/claude-sonnet-5", "models": [{"id": "anthropic/claude-sonnet-5", "name": "Claude Sonnet 5", "desc": "Via OpenRouter"}, {"id": "anthropic/claude-opus-5", "name": "Claude Opus 5", "desc": "Via OpenRouter"}, {"id": "openai/gpt-5.6-terra", "name": "GPT-5.6-Terra", "desc": "Via OpenRouter"}, {"id": "deepseek/deepseek-r1", "name": "DeepSeek R1", "desc": "Via OpenRouter"}, {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B", "desc": "Via OpenRouter"}]},
  {"id": "claude-code", "label": "Claude Code CLI", "connected": false, "key_env": "", "key_url": "https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview", "destination": "Sent to Anthropic through the Claude CLI.", "locality": "cloud", "default_model": "claude-code", "models": [{"id": "claude-code", "name": "Claude Code Session", "desc": "Let the Claude CLI pick its active model"}, {"id": "claude-opus-5", "name": "Claude Opus 5", "desc": "Frontier intelligence & deep reasoning (CLI session)"}, {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "desc": "Balanced agentic coding (CLI session)"}, {"id": "claude-fable-5", "name": "Claude Fable 5", "desc": "Most capable for the hardest, longest-running work"}, {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "desc": "Fast & responsive everyday model"}]}
];

let PROVIDERS = [];
let MODEL_CATALOG = FALLBACK_CATALOG;

const BRAND_ICONS = {
  openai: `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M22.28 9.82a5.98 5.98 0 0 0-.51-4.91 6.05 6.05 0 0 0-6.51-2.9 6.06 6.06 0 0 0-10.28 2.17 5.98 5.98 0 0 0-4 2.9 6.05 6.05 0 0 0 .74 7.1 5.98 5.98 0 0 0 .51 4.91 6.05 6.05 0 0 0 6.51 2.9 6.06 6.06 0 0 0 10.28-2.17 5.99 5.99 0 0 0 4-2.9 6.05 6.05 0 0 0-.74-7.1zm-9.02 12.61a4.48 4.48 0 0 1-2.88-1.04l.14-.08 4.78-2.76a.8.8 0 0 0 .39-.68v-6.74l2.02 1.17a.07.07 0 0 1 .04.05v5.58a4.5 4.5 0 0 1-4.49 4.5zm-9.66-4.13a4.47 4.47 0 0 1-.53-3.01l.14.08 4.78 2.76a.77.77 0 0 0 .78 0l5.84-3.37v2.33a.08.08 0 0 1-.03.06L9.74 19.95a4.5 4.5 0 0 1-6.14-1.65zM2.34 7.9a4.48 4.48 0 0 1 2.37-1.98v5.69a.77.77 0 0 0 .38.67l5.82 3.36-2.02 1.17a.08.08 0 0 1-.07 0L3.99 14.02A4.5 4.5 0 0 1 2.34 7.9zm16.1 3.85l-5.84-3.37 2.02-1.16a.08.08 0 0 1 .07 0l4.83 2.79a4.5 4.5 0 0 1-.68 8.1v-5.67a.79.79 0 0 0-.4-.69zm2.01-3.02l-.14-.09-4.78-2.78a.78.78 0 0 0-.78 0L9.41 9.23V6.9a.07.07 0 0 1 .03-.06l4.83-2.79a4.5 4.5 0 0 1 6.68 4.66zM8.31 12.86l-2.02-1.16a.08.08 0 0 1-.04-.06V6.07a4.5 4.5 0 0 1 7.38-3.45l-.14.08-4.78 2.76a.8.8 0 0 0-.4.68v6.72zm1.14-2.07l2.55-1.47 2.55 1.47v2.94l-2.55 1.47-2.55-1.47z"/></svg>`,
  claude: `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a1 1 0 0 1 1 1v2.5a1 1 0 0 1-2 0V3a1 1 0 0 1 1-1zm0 15.5a1 1 0 0 1 1 1V21a1 1 0 0 1-2 0v-2.5a1 1 0 0 1 1-1zm8-6.5a1 1 0 0 1 1 1 1 1 0 0 1-1 1h-2.5a1 1 0 0 1 0-2H20zM6.5 12a1 1 0 0 1 0 2H4a1 1 0 0 1 0-2h2.5zm11.16-6.25a1 1 0 0 1 1.42 0 1 1 0 0 1 0 1.42l-1.77 1.76a1 1 0 0 1-1.41-1.41l1.76-1.77zm-11.31 11.32a1 1 0 0 1 1.41 0 1 1 0 0 1 0 1.41l-1.77 1.77a1 1 0 0 1-1.41-1.41l1.77-1.77zm12.73 0a1 1 0 0 1 0 1.41l-1.77 1.77a1 1 0 0 1-1.41-1.41l1.77-1.77a1 1 0 0 1 1.41 0zM6.35 7.17a1 1 0 0 1 0-1.42l1.77-1.76a1 1 0 1 1 1.41 1.41L7.76 7.17a1 1 0 0 1-1.41 0z"/></svg>`,
  cursor: `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1.75l9.5 5.5v11.5L12 24.25 2.5 18.75V7.25L12 1.75zm0 2.3L4.5 8.38l7.5 4.33 7.5-4.33L12 4.05zm8 6.13l-7 4.04v7.73l7-4.04v-7.73zm-9 11.77v-7.73l-7-4.04v7.73l7 4.04z"/></svg>`,
  xai: `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M18.24 2.25h3.31l-7.23 8.26 8.5 11.24H16.17l-5.21-6.82L4.99 21.75H1.68l7.73-8.84L1.25 2.25H8.08l4.71 6.23zm-1.16 17.52h1.83L7.08 4.13H5.12z"/></svg>`,
  gemini: `<svg width="15" height="15" viewBox="0 0 24 24"><path fill="#4285F4" d="M23.75 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.82-2.4 3.68v3.05h3.88c2.27-2.09 3.66-5.17 3.66-9.17z"/><path fill="#34A853" d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.1-6.72-4.93H1.25v3.15C3.26 21.36 7.33 24 12 24z"/><path fill="#FBBC05" d="M5.28 14.27c-.25-.72-.38-1.49-.38-2.27s.13-1.55.38-2.27V6.58H1.25C.45 8.18 0 9.98 0 12s.45 3.82 1.25 5.42l4.03-3.15z"/><path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.33 0 3.26 2.64 1.25 6.58l4.03 3.15c.95-2.83 3.6-4.98 6.72-4.98z"/></svg>`,
};

let activeWaitingHud = null;

// Report which sign-in branch ran, so "the card showed up in the wrong place"
// is answerable. Fire-and-forget: this must never affect the sign-in itself.
function noteSigninBranch(providerId, branch, detail = "") {
  try {
    api("/api/hud/note", { method: "POST",
      body: { provider: providerId, branch, detail: String(detail) } });
  } catch (_) {}
}

async function raiseFloatingSigninCard(providerId, brandName, authUrl) {
  // Only the desktop app has a window to create; in a browser tab this is
  // absent and the in-app card handles the wait instead.
  const api = window.pywebview && window.pywebview.api;
  // No trace on this branch. It is the ordinary answer in a browser tab, not a
  // fault, and it fired on every sign-in — the two `console.warn`s below stay,
  // because those are a bridge that exists and did not work.
  if (!api || typeof api.open_signin_hud !== "function") return false;
  try {
    const ok = Boolean(await api.open_signin_hud(providerId, brandName, authUrl));
    // Silence here is what made this hard to diagnose: the bridge existed but
    // the window never appeared, and nothing said so.
    if (!ok) console.warn("[chitragupta] native sign-in window could not be shown");
    return ok;
  } catch (e) {
    console.warn("[chitragupta] native sign-in window failed:", e);
    return false;
  }
}

function showWaitingHud({ brandName, authUrl, providerId, requiresCode = false, onCancel, onConnected }) {
  if (activeWaitingHud) {
    activeWaitingHud.dismiss();
  }

  const hud = document.createElement("div");
  hud.className = "ts-floating-hud";
  hud.innerHTML = `
    <div class="ts-hud-header">
      <div class="ts-hud-status">
        <span class="ts-hud-icon">${IC.spark}</span>
        <span>Waiting to connect</span>
      </div>
      <button type="button" class="ts-hud-close" title="Dismiss">${IC.close}</button>
    </div>
    <div class="ts-hud-title">Connect ${esc(brandName)} in your browser</div>
    <div class="ts-hud-body">Sign in and approve access there. Chitragupta will update when the connection is ready.</div>
    ${authUrl ? `<button type="button" class="ts-hud-btn"><span>${IC.external}</span> Open browser sign in</button>` : ""}
    ${requiresCode ? `
      <div class="ts-hud-code-form" style="margin-top:8px;display:flex;gap:6px">
        <input type="text" class="ts-hud-code-input" placeholder="Paste code#state here" style="flex:1;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.15);border-radius:7px;padding:6px 10px;font-size:11px;color:#fff;font-family:var(--mono)">
        <button type="button" class="ts-hud-code-submit tiny primary" style="padding:6px 12px;font-size:11px">Connect</button>
      </div>
    ` : ""}
  `;

  document.body.appendChild(hud);

  const closeBtn = hud.querySelector(".ts-hud-close");
  const openBtn = hud.querySelector(".ts-hud-btn");
  const codeInput = hud.querySelector(".ts-hud-code-input");
  const codeSubmit = hud.querySelector(".ts-hud-code-submit");

  if (openBtn && authUrl) {
    openBtn.onclick = async () => {
      try {
        await api("/api/open-browser", { method: "POST", body: { url: authUrl } });
      } catch (_) {
        window.open(authUrl, "_blank");
      }
    };
  }

  if (codeInput && codeSubmit) {
    codeSubmit.onclick = async () => {
      const code = codeInput.value.trim();
      if (!code) return;
      codeSubmit.disabled = true;
      codeSubmit.innerText = "Connecting…";
      try {
        const res = await api(`/api/providers/${providerId}/auth/code`, {
          method: "POST",
          body: { code }
        });
        toast(`${res.message || 'Signed in'}`);
        await api(`/api/providers/${providerId}/refresh`, { method: "POST" });
        dismiss();
        if (onConnected) onConnected();
      } catch (err) {
        toast(errText(err));
        codeSubmit.disabled = false;
        codeSubmit.innerText = "Connect";
      }
    };
  }

  let pollTimer = null;
  const dismiss = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    hud.style.opacity = "0";
    hud.style.transform = "translateY(-10px) scale(0.95)";
    hud.style.transition = "all 0.2s ease";
    setTimeout(() => {
      if (hud.parentNode) hud.parentNode.removeChild(hud);
    }, 220);
    if (activeWaitingHud && activeWaitingHud.el === hud) {
      activeWaitingHud = null;
    }
    if (onCancel) onCancel();
  };

  closeBtn.onclick = dismiss;

  let attempts = 0;
  // A browser sign-in with an account switch, a password and 2FA can take a
  // while. Giving up after ~2.5 minutes left users staring at a card that
  // never updated, so wait ~10 and then say so rather than vanishing.
  const MAX_ATTEMPTS = 300;   // x2s = ~10 minutes
  const finish = (email) => {
    clearInterval(pollTimer);
    pollTimer = null;
    dismiss();
    toast(`Connected ${brandName}${email ? ` (${email})` : ""}`);
    if (onConnected) onConnected();
  };

  pollTimer = setInterval(async () => {
    attempts++;
    if (attempts > MAX_ATTEMPTS) {
      clearInterval(pollTimer);
      pollTimer = null;
      dismiss();
      toast(`Still waiting on ${brandName}. Finish in your browser, then press Refresh.`);
      if (onCancel) onCancel();
      return;
    }
    try {
      // Poll ONLY the cheap status endpoint. /refresh re-runs discovery and can
      // take seconds per provider; calling it every tick queued requests faster
      // than the server could finish them and froze the whole app.
      const st = await api(`/api/providers/${providerId}/auth/status`).catch(() => ({}));
      if (st.status === "success") {
        await api(`/api/providers/${providerId}/refresh`, { method: "POST" }).catch(() => {});
        finish(st.email);
      } else if (st.status === "error" && st.error) {
        clearInterval(pollTimer);
        pollTimer = null;
        dismiss();
        toast(`${brandName} sign-in failed: ${st.error}`);
        if (onCancel) onCancel();
      }
    } catch (_) {}
  }, 2000);

  activeWaitingHud = { el: hud, dismiss };
  return activeWaitingHud;
}

function formatSubscriptionUsageUpdated(updatedAt, now) {
  if (!updatedAt) return "Updated just now";
  const nowMs = now || Date.now();
  const ageMinutes = Math.max(0, Math.floor((nowMs - updatedAt) / 60000));
  if (ageMinutes < 1) return "Updated just now";
  if (ageMinutes < 60) return `Updated ${ageMinutes}m ago`;
  const ageHours = Math.floor(ageMinutes / 60);
  if (ageHours < 24) return `Updated ${ageHours}h ago`;
  return `Updated ${Math.floor(ageHours / 24)}d ago`;
}

function formatSubscriptionUsageReset(resetsAt, now) {
  if (!resetsAt) return "";
  const nowMs = now || Date.now();
  const resetMs = Number(resetsAt) < 1e11 ? Number(resetsAt) * 1000 : Number(resetsAt);
  const remainingMinutes = Math.ceil((resetMs - nowMs) / 60000);
  if (remainingMinutes <= 0) return "resetting now";
  if (remainingMinutes < 60) return `resets in ${remainingMinutes}m`;
  const hours = Math.floor(remainingMinutes / 60);
  const minutes = remainingMinutes % 60;
  if (hours < 24) return `resets in ${hours}h${minutes ? ` ${minutes}m` : ""}`;
  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  return `resets in ${days}d${remainingHours ? ` ${remainingHours}h` : ""}`;
}

function subscriptionUsageRemainingPercent(usedPercent) {
  return Math.max(0, Math.min(100, Math.round(100 - (Number(usedPercent) || 0))));
}

function showCliInstructions(containerEl, res, brandName, providerId, onRefresh) {
  // Cursor, Grok and Claude Code sign in through their own CLI. If we can fetch
  // that CLI ourselves, offer one button — a product shouldn't ask someone to
  // open a terminal. Otherwise fall back to the exact command.
  if (res.cli_installable) {
    containerEl.innerHTML = `
      <div class="ts-card">
        <div class="ts-card-left">
          <span class="ts-card-title">Set up ${esc(brandName)}</span>
          <span class="ts-card-sub">${esc((res.detail || "").replace(/`/g, ""))}</span>
        </div>
        <div class="ts-cli-progress" hidden>
          <div class="ts-progress-track"><div class="ts-progress-fill" style="width:0%"></div></div>
          <span class="ts-cli-progress-label">Starting…</span>
        </div>
        <div class="ts-action-group" style="margin-top:10px">
          <button type="button" class="tiny primary ts-cli-install">Install &amp; sign in</button>
          ${res.auth_url ? `<a class="pc-link" href="${esc(res.auth_url)}" target="_blank" rel="noopener">Docs ${IC.external}</a>` : ""}
        </div>
      </div>`;

    const btn = containerEl.querySelector(".ts-cli-install");
    const box = containerEl.querySelector(".ts-cli-progress");
    const bar = containerEl.querySelector(".ts-progress-fill");
    const label = containerEl.querySelector(".ts-cli-progress-label");
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = "Installing…";
      box.hidden = false;
      try {
        await api(`/api/providers/${providerId}/cli/install`, { method: "POST" });
        const done = await pollCliInstall(providerId, (st) => {
          bar.style.width = `${st.percent || 0}%`;
          label.textContent = st.message || "Working…";
        });
        if (done.state === "error") {
          label.textContent = done.error || "Install failed.";
          btn.disabled = false;
          btn.textContent = "Try again";
          return;
        }
        label.textContent = "Installed — opening sign-in…";
        onRefresh(true);            // re-run sign-in now that the CLI exists
      } catch (e) {
        label.textContent = `Could not install it. ${errText(e)}`;
        btn.disabled = false;
        btn.textContent = "Try again";
      }
    };
    return;
  }

  const cmds = (res.detail || "").match(/`([^`]+)`/g) || [];
  const commands = cmds.map((c) => c.replace(/`/g, ""));
  containerEl.innerHTML = `
    <div class="ts-card">
      <div class="ts-card-left">
        <span class="ts-card-title">${esc(brandName)} signs in from your terminal</span>
        <span class="ts-card-sub">${esc((res.detail || "").replace(/`/g, ""))}</span>
      </div>
      ${commands.length ? `<div class="ts-cli-cmds">${commands.map((c) =>
        `<code class="ts-cli-cmd" data-cmd="${esc(c)}" title="Click to copy">${esc(c)}</code>`
      ).join("")}</div>` : ""}
      <div class="ts-action-group" style="margin-top:10px">
        <button type="button" class="tiny primary ts-cli-recheck">I've signed in — check again</button>
        ${res.auth_url ? `<a class="pc-link" href="${esc(res.auth_url)}" target="_blank" rel="noopener">Docs ${IC.external}</a>` : ""}
      </div>
    </div>`;
  containerEl.querySelectorAll(".ts-cli-cmd").forEach((el) => {
    el.onclick = () => {
      navigator.clipboard?.writeText(el.dataset.cmd || "");
      toast("Copied");
    };
  });
  const recheck = containerEl.querySelector(".ts-cli-recheck");
  if (recheck) recheck.onclick = async () => {
    recheck.disabled = true;
    try { await loadProviders(); } finally { onRefresh(false); }
  };
}

async function pollCliInstall(providerId, onTick) {
  for (let i = 0; i < 600; i++) {
    const st = await api(`/api/providers/${providerId}/cli`).catch(() => ({ state: "error", error: "lost connection" }));
    onTick(st);
    if (st.state === "done" || st.state === "error") return st;
    await new Promise((r) => setTimeout(r, 500));
  }
  return { state: "error", error: "Install timed out." };
}

function renderProviderConnectBox(boxEl, providerId, options = {}) {
  if (!boxEl) return;
  const p = (MODEL_CATALOG || []).find((c) => c.id === providerId)
         || (PROVIDERS || []).find((x) => x.name === providerId)
         || { id: providerId, label: providerId, ready: false };

  const conn = p.connection || {};
  const caps = p.capabilities || {};
  const detected = p.detected_account || {};
  const isDisconnected = (conn.connection_status === "DISCONNECTED");
  const isReady = Boolean(p.ready && !isDisconnected);

  // An account and an API key are independent credentials — badge and
  // disconnect each from its own state, never from provider-level readiness.
  const creds = p.credentials || {};
  const apiKeyCred = creds.api_key || {};
  const hasApiKey = Boolean(apiKeyCred.connected);
  // Some providers have no interactive sign-in at all (ProviderCapabilities
  // .api_key_only) — never offer them an account card or a sign-in button.
  const apiKeyOnly = Boolean(caps.api_key_only);
  const accountEmail = (!isDisconnected && (conn.email || (conn.connection_status === "ACCOUNT_CONNECTED" && detected.email) || (p.account_meta && p.account_meta.email))) || "";
  const hasActiveAccount = Boolean(!apiKeyOnly && !isDisconnected && isReady && (accountEmail || conn.auth_method === "account" || conn.connection_status === "ACCOUNT_CONNECTED"));
  const isFoundOnComputer = Boolean(!apiKeyOnly && !hasActiveAccount && detected.found_on_computer && detected.email);
  const keyEnv = p.key_env || (caps.key_env || (providerId === "openai" ? "OPENAI_API_KEY" : (providerId === "anthropic" || providerId === "claude" ? "ANTHROPIC_API_KEY" : (providerId === "gemini" ? "GEMINI_API_KEY" : (providerId === "xai" ? "XAI_API_KEY" : (providerId === "deepseek" ? "DEEPSEEK_API_KEY" : (providerId === "openrouter" ? "OPENROUTER_API_KEY" : (providerId === "cursor" ? "CURSOR_API_KEY" : ""))))))));
  const keyUrl = p.key_url || (caps.official_auth_url || "");
  const models = p.models || [];

  // Branded copy for the providers that have an icon; everything else falls
  // back to its own label rather than a generic "Account".
  const providerLabel = p.label || caps.display_name || providerId;
  let brandName = providerLabel;
  let signinBtnName = `Sign in with ${providerLabel}`;
  let brandIcon = "";
  if (providerId === "openai") {
    brandName = "ChatGPT";
    signinBtnName = "Sign in with ChatGPT";
    brandIcon = BRAND_ICONS.openai;
  } else if (providerId === "claude" || providerId === "anthropic") {
    brandName = "Claude";
    signinBtnName = "Sign in with Claude";
    brandIcon = BRAND_ICONS.claude;
  } else if (providerId === "cursor") {
    brandName = "Cursor";
    signinBtnName = "Sign in with Cursor";
    brandIcon = BRAND_ICONS.cursor;
  } else if (providerId === "xai") {
    brandName = "Grok";
    signinBtnName = "Sign in with Grok";
    brandIcon = BRAND_ICONS.xai;
  }

  // 1. Detected / Active Account Card
  let activeCardHtml = "";
  if (!apiKeyOnly && (hasActiveAccount || isFoundOnComputer)) {
    // Derived, not a per-provider chain: a provider missing from that chain
    // (claude-code) fell through to "Connected Account / Using this account"
    // while it was merely detected on the machine. Detection is not consent.
    const plan = detected.plan || p.plan || conn.plan || "";
    const cardTitle = isFoundOnComputer
      ? `${plan || brandName} found`
      : (plan || `${brandName} connected`);
    const badgeText = isFoundOnComputer ? "Found on this computer" : "Using this account";
    const badgeClass = isFoundOnComputer ? "found" : "using";

    const subText = isFoundOnComputer
      ? (providerId === "gemini"
          ? `${esc(detected.email || accountEmail)} · Google Workspace connected · Enter GEMINI_API_KEY below for model inference`
          : `${esc(detected.email || accountEmail)} · Found on this computer`)
      : `${esc(accountEmail || "API Key Active")} · Added to Chitragupta`;

    const descText = providerId === "openai"
      ? "Your ChatGPT plan includes a limited set of models. Chitragupta automatically uses the best model available with your plan."
      : (providerId === "gemini" && isFoundOnComputer
          ? "Google Workspace account is connected for Mail and Calendar. Enter a GEMINI_API_KEY below to enable Gemini models."
          : "Added to Chitragupta. Other apps keep their own sign-in.");

    let usageHtml = "";
    const usage = detected.usage || p.usage || conn.usage;
    if (providerId === "openai" && hasActiveAccount && usage && usage.state !== "unavailable") {
      const windows = usage.windows && usage.windows.length ? usage.windows : [
        { id: "primary", label: "30-day limit", usedPercent: 5, resetsAt: Date.now() + 2588800000 }
      ];
      const updatedLabel = formatSubscriptionUsageUpdated(usage.updatedAt, Date.now());
      usageHtml = `
        <div class="ts-usage-section" role="group" aria-label="Usage limits">
          <div class="ts-usage-head">
            <span class="ts-usage-title">Usage limits</span>
            <span class="ts-usage-updated">${esc(updatedLabel)}</span>
          </div>
          ${windows.map((w) => {
            const remainingPercent = subscriptionUsageRemainingPercent(w.usedPercent);
            const resetLabel = w.resetsAt ? formatSubscriptionUsageReset(w.resetsAt, Date.now()) : "";
            return `
              <div class="ts-usage-window">
                <div class="ts-usage-row">
                  <span>${esc(w.label || "30-day limit")}</span>
                  <span class="ts-usage-remaining">${remainingPercent}% remaining${resetLabel ? ` · ${esc(resetLabel)}` : ""}</span>
                </div>
                <div class="ts-progress-track">
                  <div class="ts-progress-fill" style="width:${remainingPercent}%"></div>
                </div>
              </div>
            `;
          }).join("")}
        </div>
      `;
    }

    let actionBtnHtml = "";
    if (isFoundOnComputer) {
      if (providerId === "gemini") {
        actionBtnHtml = `
          <button type="button" class="tiny ts-btn-signin" onclick="document.getElementById('apiKeyInput')?.focus()" style="background:rgba(234,179,8,0.12);border-color:rgba(234,179,8,0.3);color:#eab308;cursor:pointer">Enter API Key</button>
          <button type="button" class="ts-btn-link ts-refresh-btn">Refresh</button>
          <button type="button" class="ts-btn-link ts-disconnect-btn" data-scope="account" style="color:var(--danger)" title="Sign out of this account">${IC.close}</button>
        `;
      } else {
        actionBtnHtml = `
          <button type="button" class="tiny primary ts-continue-btn" style="padding:5px 12px">Continue</button>
          <button type="button" class="ts-btn-link ts-refresh-btn">Refresh</button>
          <button type="button" class="ts-btn-link ts-disconnect-btn" data-scope="account" style="color:var(--danger)" title="Sign out of this account">${IC.close}</button>
        `;
      }
    } else if (providerId === "openai") {
      // In Turnstone/real UI, top card has no action buttons on the right
      actionBtnHtml = "";
    } else {
      actionBtnHtml = `
        <button type="button" class="tiny ts-btn-signin" style="background:rgba(16,185,129,0.12);border-color:rgba(16,185,129,0.3);color:#34d399;cursor:default">Connected</button>
        <button type="button" class="ts-btn-link ts-refresh-btn">Refresh</button>
        <button type="button" class="ts-btn-link ts-disconnect-btn" data-scope="account" style="color:var(--danger)" title="Sign out of this account">${IC.close}</button>
      `;
    }

    activeCardHtml = `
      <div class="ts-card active-account">
        <div class="ts-card-row">
          <div class="ts-card-left">
            <div class="ts-card-title-wrap">
              <span class="ts-card-title">${esc(cardTitle)}</span>
              <span class="ts-badge ${badgeClass}">${esc(badgeText)}</span>
            </div>
            <div class="ts-card-sub" style="font-weight:500;color:var(--text)">${subText}</div>
            <div class="ts-card-desc">${descText}</div>
          </div>
          ${actionBtnHtml ? `<div class="ts-action-group">${actionBtnHtml}</div>` : ""}
        </div>
        ${usageHtml}
      </div>
    `;
  }

  // 2. Account Sign-in / Different Account Card
  let signinCardHtml = "";
  if (caps.has_interactive_signin) {
    const signinTitle = `${brandName} account for Chitragupta`;
    const signinSub = `Sign in again or use a different account without changing other apps.`;

    signinCardHtml = `
      <div class="ts-card ts-signin-container">
        <div class="ts-card-row">
          <div class="ts-card-left">
            <span class="ts-card-title">${esc(signinTitle)}</span>
            <span class="ts-card-sub">${esc(signinSub)}</span>
          </div>
          <div class="ts-action-group">
            <button type="button" class="ts-btn-signin ts-signin-btn">
              ${brandIcon}
              <span>${esc(signinBtnName)}</span>
            </button>
            <button type="button" class="ts-btn-link ts-refresh-btn">Refresh</button>
            ${hasActiveAccount ? `<button type="button" class="ts-btn-link ts-disconnect-btn" data-scope="account" style="color:var(--muted)" title="Sign out of this account">${IC.close}</button>` : ""}
          </div>
        </div>
      </div>
    `;
  }

  // 3. API Key Card
  let apiKeyCardHtml = "";
  if (keyEnv) {
    apiKeyCardHtml = `
      <div class="ts-card">
        <div class="ts-card-row">
          <div class="ts-card-left">
            <div class="ts-card-title-wrap">
              <span class="ts-card-title">${esc(p.label || providerId)} API key</span>
              ${hasApiKey ? `<span class="ts-badge using">Connected</span>` : ""}
            </div>
            <span class="ts-card-sub">Used for ${esc(p.label || providerId)} runs.${keyUrl ? ` <a href="${keyUrl}" target="_blank" rel="noopener" class="pc-link" style="margin-left:4px">Get key ${IC.external}</a>` : ""}</span>
          </div>
          <div class="ts-action-group">
            <button type="button" class="tiny ghost ts-toggle-key-btn">${hasApiKey ? 'Update API key' : 'Add API key'}</button>
            ${hasApiKey ? `<button type="button" class="ts-btn-link ts-disconnect-btn" data-scope="api_key" style="color:var(--danger)" title="Remove this API key">${IC.close}</button>` : ""}
          </div>
        </div>
        <div class="ts-key-collapse" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
          <input type="password" class="pc-key-input" placeholder="Paste ${esc(keyEnv)}…" autocomplete="off" />
          <button type="button" class="tiny primary pc-connect-btn">Save</button>
          <button type="button" class="tiny ghost pc-test-btn">Test</button>
        </div>
      </div>
    `;
  }

  // 4. Models Card
  //
  // Locked models stay visible — hiding them would leave a user wondering
  // where a model they have heard of went — but they must LOOK locked and say
  // why. This list rendered every model identically, so a ChatGPT Free account
  // saw GPT-5.6-Terra and GPT-6-Astra presented exactly like the models it can
  // actually run. The list also scrolls inside 140px, and the locked ones came
  // first, so the three usable models were below the fold: the panel answered
  // "what can I run?" with a list of things the account cannot run.
  let modelsCardHtml = "";
  if (models && models.length) {
    const usable = models.filter((m) => !m.locked);
    const locked = models.filter((m) => m.locked);
    const ordered = usable.concat(locked);
    const SHOWN = 8;
    const hiddenCount = Math.max(0, ordered.length - SHOWN);
    modelsCardHtml = `
      <div style="margin-top:4px">
        <div class="pc-models-list">
          ${ordered.slice(0, SHOWN).map((m) => {
            const isReasoning = Boolean(m.reasoning);
            const hasTools = m.tool_calling !== false;
            const hasVision = Boolean(m.vision);
            const isLocked = Boolean(m.locked);
            const why = m.plan_required || "Not on your plan";
            const tip = isLocked
              ? `${m.name || m.id} — ${why}`
              : (m.desc || m.id);
            return `
              <div class="pc-model-pill ${isLocked ? "is-locked" : ""}" title="${esc(tip)}">
                ${isLocked ? `<span class="pc-lock" aria-hidden="true">${IC.lock}</span>` : ""}
                <span>${esc(m.name || m.id)}</span>
                ${isLocked ? `<span class="pc-tag locked">${esc(why)}</span>` : ""}
                ${isReasoning ? `<span class="pc-tag reasoning">r1/o1</span>` : ""}
                ${hasTools ? `<span class="pc-tag tools">tools</span>` : ""}
                ${hasVision ? `<span class="pc-tag vision">vision</span>` : ""}
              </div>
            `;
          }).join("")}
          ${hiddenCount ? `<div class="pc-model-pill pc-model-more">+${hiddenCount} more</div>` : ""}
        </div>
      </div>
    `;
  }

  // ── a local server is not an account ──────────────────────────────────────
  // Ollama has no key, no sign-in and nothing to disconnect, so every card
  // above renders empty for it — which is what left its box with no way to
  // connect at all while each of its models said "Connect in Models" to
  // somebody already standing in Models.
  //
  // Asked of the capabilities, not of the id: anything that runs locally,
  // discovers its own models and has no credential to collect gets this card.
  const isLocalServer = Boolean(
    p.locality === "local" &&
    !caps.api_key_supported && !caps.oauth_supported &&
    !caps.browser_login_supported && !caps.device_login_supported &&
    caps.model_discovery_supported);

  let localCardHtml = "";
  if (isLocalServer) {
    const installed = models.filter((m) => !m.locked).length;
    // "Detection is not consent" does not apply to a thing with no credential:
    // there is nothing to consent to. Reachable IS connected here, and the only
    // honest action is to look again.
    const line = isReady
      ? `${installed} model${installed === 1 ? "" : "s"} installed and ready to run.`
      : esc(p.reason || "Not running. Start Ollama, then check again.");
    localCardHtml = `
      <div class="ts-card local-server">
        <div class="ts-card-row">
          <div class="ts-card-left">
            <div class="ts-card-title">${esc(providerLabel)}</div>
            <div class="ts-card-sub">${line}</div>
          </div>
          <div class="ts-card-actions">
            ${isReady
              ? `<span class="pc-badge ready">Running</span>`
              : `<span class="pc-badge local">Not running</span>`}
            <button type="button" class="ts-btn-link ts-refresh-btn">Check again</button>
            ${!isReady && keyUrl
              ? `<a class="pc-link" href="${esc(keyUrl)}" target="_blank" rel="noopener">Get Ollama ${IC.external}</a>`
              : ""}
          </div>
        </div>
        ${isReady ? "" : `<p class="ts-card-hint">Nothing is sent anywhere — these run on this Mac.
          Install Ollama and run a model once, and it appears here.</p>`}
      </div>`;
  }

  boxEl.innerHTML = `
    ${localCardHtml}
    ${activeCardHtml}
    ${signinCardHtml}
    ${apiKeyCardHtml}
    ${modelsCardHtml}
    <div class="pc-feedback" style="display:none;margin-top:4px;padding:4px 6px"></div>
  `;

  const feedbackEl = boxEl.querySelector(".pc-feedback");
  const setFeedback = (msg, isErr = false) => {
    if (!feedbackEl) return;
    feedbackEl.style.display = "block";
    feedbackEl.style.color = isErr ? "var(--danger)" : "var(--ok)";
    feedbackEl.textContent = msg;
  };

  // Toggle API key form
  const toggleKeyBtn = boxEl.querySelector(".ts-toggle-key-btn");
  const keyCollapse = boxEl.querySelector(".ts-key-collapse");
  if (toggleKeyBtn && keyCollapse) {
    toggleKeyBtn.onclick = () => {
      const isClosed = keyCollapse.style.display === "none";
      keyCollapse.style.display = isClosed ? "flex" : "none";
      if (isClosed) {
        const inp = keyCollapse.querySelector(".pc-key-input");
        if (inp) inp.focus();
      }
    };
  }

  // Connect local account (e.g. Claude / Cursor found on this computer)
  const continueBtn = boxEl.querySelector(".ts-continue-btn");
  if (continueBtn) {
    continueBtn.onclick = async () => {
      continueBtn.disabled = true;
      setFeedback("Connecting local account…");
      try {
        const res = await api(`/api/providers/${providerId}/connect-local`, { method: "POST" });
        toast(`Connected ${p.label || providerId}`);
        await loadProviders();
        if (options.onConnect) options.onConnect();
      } catch (e) {
        setFeedback(`Could not connect. ${errText(e)}`, true);
      } finally {
        continueBtn.disabled = false;
      }
    };
  }

  // Sign in flow with live waiting card and reactive polling
  const signinBtn = boxEl.querySelector(".ts-signin-btn");
  const signinContainer = boxEl.querySelector(".ts-signin-container");
  if (signinBtn && signinContainer) {
    signinBtn.onclick = async () => {
      signinBtn.disabled = true;
      const originalHtml = signinContainer.innerHTML;
      const restore = () => {
        signinContainer.innerHTML = originalHtml;
        signinBtn.disabled = false;
        renderProviderConnectBox(boxEl, providerId, options);
      };

      signinContainer.innerHTML = `
        <div class="ts-waiting-card">
          <div class="ts-waiting-body">
            <div class="ts-waiting-title">Finish signing in in your browser</div>
            <div class="ts-waiting-sub">Chitragupta will continue when you're done.</div>
          </div>
          <button type="button" class="ts-btn-link ts-cancel-poll-btn">Cancel</button>
        </div>
      `;

      let hud = null;
      const cancelBtn = signinContainer.querySelector(".ts-cancel-poll-btn");
      const stopPolling = () => {
        if (hud) {
          hud.dismiss();
          hud = null;
        }
        signinContainer.innerHTML = originalHtml;
        renderProviderConnectBox(boxEl, providerId, options);
      };

      if (cancelBtn) cancelBtn.onclick = async () => {
        // Abandon the sign-in for real: stop the CLI's login process and put
        // the floating card away, not just hide our own spinner.
        cancelBtn.disabled = true;
        try {
          await api(`/api/providers/${providerId}/auth/cancel`, { method: "POST" });
        } catch (_) {}
        try { window.pywebview?.api?.close_signin_hud?.(); } catch (_) {}
        toast(`Cancelled ${brandName} sign-in`);
        stopPolling();
      };

      try {
        const res = await api(`/api/providers/${providerId}/auth/start`, { method: "POST" });

        if (res.connected) {
          noteSigninBranch(providerId, "already-connected", res.detail || "");
          toast(`${res.detail || 'Connected'}`);
          stopPolling();
          await loadProviders();
          if (options.onConnect) options.onConnect();
          return;
        }

        // Not every provider has a browser flow. Say what the backend said
        // instead of spinning on a sign-in that will never arrive.
        if (res.started === false) {
          signinBtn.disabled = false;
          if (res.cli_required) {
            noteSigninBranch(providerId, "cli-required", res.detail || "");
            // Write into the LIVE container. Calling restore() first would
            // re-render the whole box and detach signinContainer, so the card
            // would be built into an orphaned node and never appear.
            showCliInstructions(signinContainer, res, brandName, providerId,
              async (retry) => {
                await loadProviders();
                renderProviderConnectBox(boxEl, providerId, options);
                // The CLI now exists, so the same button can start the real
                // browser sign-in without a second click.
                if (retry) boxEl.querySelector(".ts-signin-btn")?.click();
              });
          } else {
            noteSigninBranch(providerId, "no-browser-signin", res.detail || "");
            restore();
            toast(res.detail || `${brandName} has no browser sign-in.`);
            if (res.api_key_only) boxEl.querySelector(".ts-toggle-key-btn")?.click();
          }
          return;
        }

        // In the desktop app a floating card also follows the user to the
        // browser. The in-app row stays either way, so the Models panel still
        // shows what is happening and offers Cancel.
        // Declare BEFORE any read: `if (!floating)` above this line threw a
        // temporal-dead-zone ReferenceError, which the catch below turned into
        // "Sign in error" and tore the whole card down the moment the browser
        // opened. `node --check` does not catch TDZ.
        const floating = await raiseFloatingSigninCard(
          providerId, brandName, res.auth_url || "");

        noteSigninBranch(providerId, floating ? "floating-card" : "in-app-card");

        if (!floating) toast(`Opening ${brandName} in browser…`);

        // Open browser tab if the backend hasn't already (e.g. no CLI available)
        if (res.auth_url && !res.browser_opened) {
          try {
            await api("/api/open-browser", { method: "POST", body: { url: res.auth_url } });
          } catch (_) {
            window.open(res.auth_url, "_blank");
          }
        }

        // The in-app HUD is a stand-in for the native card; skip it when the
        // real one is on screen.
        hud = floating ? null : showWaitingHud({
          brandName,
          authUrl: res.auth_url || "",
          providerId,
          requiresCode: res.requires_code || false,
          onCancel: () => {
            signinContainer.innerHTML = originalHtml;
            renderProviderConnectBox(boxEl, providerId, options);
          },
          onConnected: async () => {
            await loadProviders();
            if (options.onConnect) options.onConnect();
          }
        });

      } catch (e) {
        stopPolling();
        setFeedback(`Could not sign in. ${errText(e)}`, true);
      }
    };
  }

  // Refresh
  boxEl.querySelectorAll(".ts-refresh-btn").forEach((btn) => {
    btn.onclick = async () => {
      btn.disabled = true;
      setFeedback("Refreshing connection & models…");
      try {
        const res = await api(`/api/providers/${providerId}/refresh`, { method: "POST" });
        setFeedback(res.ready ? `Ready — ${res.models?.length || 0} models` : `Status: ${res.reason || 'not ready'}`, !res.ready);
        toast(`Refreshed ${p.label || providerId}`);
        await loadProviders();
      } catch (e) {
        setFeedback(`Could not refresh that. ${errText(e)}`, true);
      } finally {
        btn.disabled = false;
      }
    };
  });

  // Disconnect
  const disconnectBtns = boxEl.querySelectorAll(".ts-disconnect-btn");
  disconnectBtns.forEach((disconnectBtn) => {
    disconnectBtn.onclick = async () => {
      disconnectBtn.disabled = true;
      setFeedback("Disconnecting…");
      try {
        const scope = disconnectBtn.dataset.scope || "all";
        await api(`/api/providers/${providerId}/disconnect?scope=${scope}`, { method: "POST" });
        toast(scope === "api_key" ? `Removed ${p.label || providerId} API key`
            : scope === "account" ? `Signed out of ${p.label || providerId}`
            : `Disconnected ${p.label || providerId}`);
        await loadProviders();
        if (options.onConnect) options.onConnect();
      } catch (e) {
        setFeedback(`Could not disconnect. ${errText(e)}`, true);
      } finally {
        disconnectBtn.disabled = false;
      }
    };
  });

  // Connect via API key
  const connectBtn = boxEl.querySelector(".pc-connect-btn");
  const inputEl = boxEl.querySelector(".pc-key-input");
  if (connectBtn && inputEl) {
    connectBtn.onclick = async () => {
      const keyVal = inputEl.value.trim();
      if (!keyVal) { setFeedback("Please paste an API key first", true); return; }
      connectBtn.disabled = true;
      setFeedback("Connecting & verifying…");
      try {
        const res = await api(`/api/providers/${providerId}/key`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: keyVal }),
        });
        toast(res.ready ? `Connected ${p.label || providerId}` : `Saved key for ${p.label || providerId}`);
        inputEl.value = "";
        await loadProviders();
        if (options.onConnect) options.onConnect();
      } catch (e) {
        setFeedback(`Failed to connect: ${errText(e)}`, true);
      } finally {
        connectBtn.disabled = false;
      }
    };
  }

  // Test connection
  const testBtn = boxEl.querySelector(".pc-test-btn");
  if (testBtn && inputEl) {
    testBtn.onclick = async () => {
      testBtn.disabled = true;
      setFeedback("Testing connection…");
      try {
        const res = await api(`/api/providers/${providerId}/test`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: inputEl.value.trim() || "" }),
        });
        setFeedback(res.message, !res.ok);
      } catch (e) {
        setFeedback(`That key did not work. ${errText(e)}`, true);
      } finally {
        testBtn.disabled = false;
      }
    };
  }
}
