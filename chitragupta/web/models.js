/**
 * Choosing which model answers.
 *
 * The composer's provider/model picker and its flyouts, the per-agent model
 * chip and matrix, the provider cards on the AI model screen, and the enrichment
 * model. `loadProviders()` lives here too: it is what fills `PROVIDERS` and
 * `MODEL_CATALOG`, which `providers.js` declares.
 *
 * Loaded after `providers.js` and before `app.js`, in the same global scope —
 * plain scripts, not modules; see docs/development/frontend-testing.md.
 *
 * **A selection is written to three places at once** (`setActiveModel`). The
 * composer used to save only the agent's binding and then send the value of a
 * hidden select on the AI model screen that nobody had updated — so the pill said
 * one provider and the turn ran on another. Three readers and one writer is
 * exactly how they drifted.
 *
 * Derive what a card shows from the provider's capabilities, never from a
 * `providerId === "x"` chain.
 */

const COMPOSER_PROVIDERS = [
  { id: "claude", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "openrouter", label: "OpenRouter" },
  { id: "deepseek", label: "Free open-source models" },
  { id: "ollama", label: "Ollama" },
  { id: "xai", label: "xAI" },
  { id: "cursor", label: "Cursor" },
  { id: "gemini", label: "Google Gemini" },
  { id: "claude-code", label: "Claude Code CLI" },
  { id: "subscription", label: "Subscription Gateway" },
];

let activePickerProvider = "cursor";
let activePickerModel = null;

function isProviderConnected(pid) {
  if (!pid) return false;
  const rawId = String(pid).toLowerCase().trim();
  const normId = (rawId === "anthropic" || rawId === "claude-code") ? "claude"
               : (rawId === "google" ? "gemini"
               : (rawId === "grok" ? "xai" : rawId));

  // 1. Check MODEL_CATALOG first (by exact ID or normalized alias)
  for (const id of [rawId, normId]) {
    const p = (MODEL_CATALOG || []).find((c) => (c.id || "").toLowerCase() === id);
    if (p) {
      if (typeof p.connected === "boolean") return p.connected;
      const conn = p.connection || {};
      if (conn.connection_status === "ACCOUNT_CONNECTED" || conn.connection_status === "API_KEY_CONNECTED" || conn.connection_status === "CONNECTED") return true;
      if (conn.connection_status === "DISCONNECTED" || conn.connection_status === "NOT_CONNECTED") return false;
    }
  }

  // 2. Check PROVIDERS array (by exact name or normalized alias)
  for (const id of [rawId, normId]) {
    const prov = (PROVIDERS || []).find((x) => (x.name || "").toLowerCase() === id);
    if (prov) {
      if (typeof prov.connected === "boolean") return prov.connected;
      const conn = prov.connection || {};
      if (conn.connection_status === "ACCOUNT_CONNECTED" || conn.connection_status === "API_KEY_CONNECTED" || conn.connection_status === "CONNECTED") return true;
      if (conn.connection_status === "DISCONNECTED" || conn.connection_status === "NOT_CONNECTED") return false;
    }
  }

  return false;
}

// Record a model choice everywhere the app reads one.
//
// The composer picker used to save only the agent's binding, and the composer
// then sent `$("#provider").value` — a hidden select on the AI model screen that
// nobody had updated. On the server the request wins over the agent binding, so
// the stale value overrode the fresh choice: the pill said xAI and the turn ran
// on whatever the drawer last held. The agent was answering honestly; it really
// was running on that provider.
//
// Three places read a selection (the hidden select, the two localStorage keys)
// and one writes it, which is exactly how they drifted. They are set together
// now, so "what the pill shows" and "what the turn uses" cannot disagree.
function setActiveModel(providerId, modelId) {
  const sel = $("#provider");
  if (sel && providerId) {
    // The select only holds options the catalog rendered; a provider missing
    // from it would silently keep the previous value, so add it rather than
    // assign into nothing.
    if (!Array.from(sel.options || []).some((o) => o.value === providerId)) {
      const opt = document.createElement("option");
      opt.value = providerId; opt.textContent = providerId;
      sel.appendChild(opt);
    }
    sel.value = providerId;
  }
  if (providerId) localStorage.setItem("chitragupta_provider", providerId);
  if (modelId) localStorage.setItem("chitragupta_model", modelId);
  else localStorage.removeItem("chitragupta_model");   // "Auto" is the absence of one
}

//: Put a flyout where it fits. It opens to the left of the menu, which is the
//: side with room in every normal window — this only moves it back when the
//: left edge would be off-screen, which a narrow window or a collapsed sidebar
//: can do. Measured after it is visible, because a hidden element has no box.
function placeFlyout(el) {
  if (!el) return;
  el.classList.remove("opens-right");
  const r = el.getBoundingClientRect();
  if (r.left < 8) el.classList.add("opens-right");
}

//: Why a model cannot be picked, said in terms of what the user would do.
//:
//: It used to be "Connect in Models" for everything, which is read on the
//: Models screen itself — and for a local server there is no connect step at
//: all, so it pointed at a button that does not and should not exist.
function lockReason(provider) {
  const caps = (provider && provider.capabilities) || {};
  if (provider && provider.locality === "local"
      && !caps.api_key_supported && !caps.oauth_supported) {
    return provider.reason ? "Not running" : "Start it first";
  }
  return "Connect first";
}

function closeAllPickerFlyouts() {
  const pf = $("#cmpProvFlyout");
  const mf = $("#cmpModelFlyout");
  const pr = $("#cmpProvRow");
  const mr = $("#cmpModelRow");
  if (pf) pf.hidden = true;
  if (mf) mf.hidden = true;
  if (pr) pr.classList.remove("cmp-active");
  if (mr) mr.classList.remove("cmp-active");
}

function renderProviderFlyout() {
  const list = $("#cmpProvFlyoutList");
  if (!list) return;

  list.innerHTML = COMPOSER_PROVIDERS.map((p) => {
    const isSel = (p.id === activePickerProvider) ||
                  (p.id === "claude" && activePickerProvider === "claude-code");
    const isConn = isProviderConnected(p.id);
    const badgeHtml = !isConn
      ? `<span class="cmp-lock-badge">${IC.lock} Not Connected</span>`
      : "";
    return `
      <div class="cmp-flyout-item ${isSel ? 'is-selected' : ''} ${!isConn ? 'is-locked' : ''}"
           data-prov="${esc(p.id)}"
           data-connected="${isConn ? 'true' : 'false'}"
           title="${!isConn ? `${esc(p.label)} is not connected. Connect in Models & Accounts first.` : ''}">
        <div class="cmp-flyout-item-label">
          <span>${esc(p.label)}</span>
          ${badgeHtml}
        </div>
        ${isSel ? `<span class="cmp-flyout-check">${IC.check}</span>` : ''}
      </div>
    `;
  }).join("");

  list.querySelectorAll(".cmp-flyout-item").forEach((el) => {
    el.onclick = async (e) => {
      e.stopPropagation();
      const pid = el.dataset.prov;
      const isConn = el.dataset.connected === "true";
      const p = COMPOSER_PROVIDERS.find((x) => x.id === pid);
      const pLabel = p ? p.label : pid;

      if (!isConn) {
        toast(`${pLabel} is not connected. Connect it in Models first.`);
        return;
      }

      activePickerProvider = pid;
      activePickerModel = null;
      setActiveModel(pid, null);          // Auto: provider chosen, model unset
      if ($("#cmpSelectedProvLabel")) $("#cmpSelectedProvLabel").textContent = pLabel;
      if ($("#cmpSelectedModelLabel")) $("#cmpSelectedModelLabel").textContent = "Auto";
      const pillLabel = $("#cmpModelLabel");
      if (pillLabel) pillLabel.textContent = pLabel;
      closeAllPickerFlyouts();

      if (current) {
        try {
          await api(`/api/agents/${current}/model`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider: pid, model: null }),
          });
          toast(`Selected ${pLabel} (Auto) for ${current}`);
          await updateAgentModelChip(current);
          refreshPickerPopover();
        } catch (err) {
          toast("Could not update model: " + (err.message || err));
        }
      }
    };
  });
}

function renderModelFlyout() {
  const list = $("#cmpModelFlyoutList");
  if (!list) return;

  const isProvConn = isProviderConnected(activePickerProvider);
  // The provider itself, for `lockReason` — which wants capabilities and
  // locality, not the catalogue entry. Both were called `p` in the source this
  // came from, and the merge kept the name that was no longer in scope.
  const provider = (PROVIDERS || []).find((x) =>
    (x.name || "").toLowerCase() === String(activePickerProvider || "").toLowerCase());
  const normId = (activePickerProvider === "claude-code" || activePickerProvider === "anthropic") ? "claude" : activePickerProvider;
  const pEntry = (MODEL_CATALOG || []).find((c) => c.id === activePickerProvider)
              || (MODEL_CATALOG || []).find((c) => c.id === normId);
  const models = (pEntry && pEntry.models) || [];

  // If provider is not connected, Auto is locked too!
  const autoLocked = !isProvConn;
  const items = [
    {
      id: "",
      name: "Auto",
      desc: autoLocked ? "Provider not connected" : "Recommended model automatically",
      locked: autoLocked,
      plan_required: autoLocked ? lockReason(provider) : null
    },
    ...models.map((m) => ({
      id: m.id,
      name: m.name,
      desc: m.desc,
      locked: !isProvConn || Boolean(m.locked),
      plan_required: !isProvConn ? (m.plan_required || lockReason(provider)) : (m.plan_required || null),
    })),
  ];

  list.innerHTML = items.map((m) => {
    const isSel = (!activePickerModel && m.id === "") || (activePickerModel === m.id);
    const isLocked = Boolean(m.locked);
    const badgeHtml = isLocked && m.plan_required
      ? `<span class="cmp-lock-badge" title="Requires ${esc(m.plan_required)} plan">${IC.lock} ${esc(m.plan_required)}</span>`
      : (isLocked ? `<span class="cmp-lock-badge">${IC.lock} Locked</span>` : "");
    return `
      <div class="cmp-flyout-item ${isSel ? 'is-selected' : ''} ${isLocked ? 'is-locked' : ''}"
           data-model="${esc(m.id)}"
           data-locked="${isLocked ? 'true' : 'false'}"
           data-plan-req="${esc(m.plan_required || '')}"
           title="${isLocked ? `Requires ${esc(m.plan_required || 'higher')} plan` : esc(m.desc || '')}">
        <div class="cmp-flyout-item-label">
          <span>${esc(m.name)}</span>
          ${badgeHtml}
        </div>
        ${isSel ? `<span class="cmp-flyout-check">${IC.check}</span>` : ''}
      </div>
    `;
  }).join("") + `
    <div class="cmp-flyout-item ${!isProvConn ? 'is-locked' : ''}" data-model="__custom__" data-locked="${!isProvConn ? 'true' : 'false'}">
      <div class="cmp-flyout-item-label">
        <span style="font-size:12px;color:var(--muted)">Custom model identifier…</span>
        ${!isProvConn ? `<span class="cmp-lock-badge">${IC.lock} Locked</span>` : ''}
      </div>
    </div>
  `;

  list.querySelectorAll(".cmp-flyout-item").forEach((el) => {
    el.onclick = async (e) => {
      e.stopPropagation();
      const pSpec = COMPOSER_PROVIDERS.find((x) => x.id === activePickerProvider);
      const pLabel = pSpec ? pSpec.label : activePickerProvider;

      if (!isProvConn) {
        toast(`${pLabel} is not connected. Connect it in Models first.`);
        return;
      }

      if (el.dataset.locked === "true") {
        const req = el.dataset.planReq || "a higher";
        const modelId = el.dataset.model;
        const targetModel = items.find((x) => x.id === modelId);
        const name = targetModel ? targetModel.name : modelId;
        toast(`${name} needs the ${req} plan, which this account does not have.`);
        return;
      }
      let chosen = el.dataset.model;
      if (chosen === "__custom__") {
        const customName = prompt("Enter custom model identifier:", activePickerModel || "");
        if (customName === null) return;
        chosen = customName.trim();
      }
      activePickerModel = chosen || null;
      setActiveModel(activePickerProvider, activePickerModel);
      const displayLabel = chosen ? (items.find((x) => x.id === chosen)?.name || chosen) : "Auto";
      if ($("#cmpSelectedModelLabel")) $("#cmpSelectedModelLabel").textContent = displayLabel;
      const pillLabel = $("#cmpModelLabel");
      if (pillLabel) {
        pillLabel.textContent = chosen ? displayLabel : (pSpec ? pSpec.label : "Auto");
      }
      $("#cmpModelMenu").hidden = true;
      closeAllPickerFlyouts();
      const pill = $("#cmpModelPill");
      if (pill) pill.classList.remove("is-active");

      if (current) {
        try {
          await api(`/api/agents/${current}/model`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider: activePickerProvider, model: activePickerModel }),
          });
          toast(`Selected ${displayLabel} for ${current}`);
          await updateAgentModelChip(current);
        } catch (err) {
          toast("Could not update model: " + (err.message || err));
        }
      }
    };
  });
}

function formatProviderPlanInfo(providerId) {
  const normId = (providerId === "claude-code" || providerId === "anthropic") ? "claude" : providerId;
  const p = (MODEL_CATALOG || []).find((c) => c.id === providerId) || (MODEL_CATALOG || []).find((c) => c.id === normId);
  const pSpec = COMPOSER_PROVIDERS.find((x) => x.id === providerId) || COMPOSER_PROVIDERS.find((x) => x.id === normId);
  const providerLabel = pSpec ? pSpec.label : (p ? p.label : providerId);

  const isConnected = isProviderConnected(providerId);

  if (!isConnected) {
    const acct = (typeof p?.detected_account === "object" && p.detected_account !== null) ? p.detected_account : {};
    return {
      title: `${providerLabel}`,
      plan: "Not connected",
      identity: acct.email || "",
      connected: false,
    };
  }

  const conn = (typeof p?.connection === "object" && p.connection !== null) ? p.connection : {};
  const acct = (typeof p?.detected_account === "object" && p.detected_account !== null) ? p.detected_account : {};

  // Extract clean string for plan
  let planLabel = "";
  if (acct.plan && typeof acct.plan === "string") {
    planLabel = acct.plan;
  } else if (p?.plan && typeof p.plan === "string") {
    planLabel = p.plan;
  } else if (conn.auth_method === "account" || conn.connection_status === "ACCOUNT_CONNECTED") {
    planLabel = (normId === "openai") ? "ChatGPT Subscription" : "Claude Pro";
  } else if (conn.auth_method === "api_key" && !conn.email) {
    planLabel = (normId === "gemini") ? "Google AI Studio API Key" : "API Key";
  } else {
    if (normId === "openai") planLabel = "ChatGPT Subscription";
    else if (normId === "claude") planLabel = "Claude Pro";
    else if (normId === "cursor") planLabel = "Cursor Free";
    else if (normId === "xai") planLabel = "Grok Account";
    else if (normId === "gemini") planLabel = "Google AI Studio API Key";
    else planLabel = "Connected Account";
  }

  // Extract clean string for email / account identity
  let emailOrIdentity = "";
  if (acct.email && typeof acct.email === "string") {
    emailOrIdentity = acct.email;
  } else if (conn.email && typeof conn.email === "string") {
    emailOrIdentity = conn.email;
  } else if (acct.name && typeof acct.name === "string") {
    emailOrIdentity = acct.name;
  } else if (conn.account_display_name && typeof conn.account_display_name === "string") {
    emailOrIdentity = conn.account_display_name;
  } else if (normId === "claude") {
    const claudeEntry = (MODEL_CATALOG || []).find((c) => c.id === "claude");
    if (claudeEntry?.detected_account?.email) emailOrIdentity = claudeEntry.detected_account.email;
    else if (claudeEntry?.connection?.email) emailOrIdentity = claudeEntry.connection.email;
  }

  return {
    title: `${providerLabel} plan`,
    plan: planLabel,
    identity: emailOrIdentity,
    connected: true,
  };
}

function refreshPickerStatusRows() {
  const statusBox = $("#cmpMenuStatus");
  if (!statusBox) return;

  // Show status only for the currently selected provider
  const pid = activePickerProvider;
  if (!pid) { statusBox.innerHTML = ""; return; }

  const info = formatProviderPlanInfo(pid);
  if (!info.connected) {
    statusBox.innerHTML = `
      <div class="cmp-status-row is-disconnected" data-provider="${esc(pid)}">
        <div class="cmp-status-label-group">
          <span class="cmp-status-label" style="color:var(--danger)">${IC.lock} ${esc(info.title)}</span>
          <span class="cmp-status-sub">Provider not connected · Connect in Models & Accounts</span>
        </div>
        <span class="cmp-chevron">›</span>
      </div>
    `;
  } else {
    statusBox.innerHTML = `
      <div class="cmp-status-row" data-provider="${esc(pid)}">
        <div class="cmp-status-label-group">
          <span class="cmp-status-label">${esc(info.title)}</span>
          <span class="cmp-status-sub">${esc(info.plan)}${info.identity ? ` · ${esc(info.identity)}` : ''}</span>
        </div>
        <span class="cmp-chevron">›</span>
      </div>
    `;
  }

  statusBox.querySelectorAll(".cmp-status-row").forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      const pid = el.dataset.provider;
      openDrawer("model");
      toast(`Viewing ${pid} in AI model`);
    };
  });
}

async function refreshPickerPopover() {
  const agentId = current || (agents.length ? agents[0].id : null);
  if (!agentId) return;
  try {
    const data = await api(`/api/agents/${agentId}/model`);
    const isOverride = Boolean(data.is_override);

    const prov = data.configured_provider || data.provider || "cursor";
    activePickerProvider = prov;
    activePickerModel = isOverride ? (data.configured_model || null) : null;

    const isProvConn = isProviderConnected(prov);
    const pEntry = (MODEL_CATALOG || []).find((c) => c.id === prov);
    const pSpec = COMPOSER_PROVIDERS.find((x) => x.id === prov);
    const provName = pSpec ? pSpec.label : (pEntry ? pEntry.label : prov);
    const provDisplay = isProvConn ? provName : `${provName} — locked`;
    if ($("#cmpSelectedProvLabel")) $("#cmpSelectedProvLabel").textContent = provDisplay;

    let modelName = "Auto";
    if (isOverride && data.configured_model) {
      const mEntry = pEntry && (pEntry.models || []).find((m) => m.id === data.configured_model);
      modelName = mEntry ? mEntry.name : data.configured_model;
    } else if (!isProvConn) {
      modelName = "Connect in Models";
    }
    if ($("#cmpSelectedModelLabel")) $("#cmpSelectedModelLabel").textContent = modelName;

    const pillLabel = $("#cmpModelLabel");
    if (pillLabel) {
      if (!isProvConn) {
        pillLabel.textContent = `${provName} — locked`;
      } else {
        pillLabel.textContent = isOverride ? (modelName !== "Auto" ? modelName : provName) : "Auto";
      }
    }

    refreshPickerStatusRows();
  } catch (_) {}
}

function initComposerModelPicker() {
  const pill = $("#cmpModelPill");
  const menu = $("#cmpModelMenu");
  const provRow = $("#cmpProvRow");
  const modelRow = $("#cmpModelRow");
  const provFlyout = $("#cmpProvFlyout");
  const modelFlyout = $("#cmpModelFlyout");
  const wsPill = $("#cmpWorkspacePill");

  if (wsPill && !wsPill._wired) {
    wsPill._wired = true;
    wsPill.onclick = () => {
      openDrawer("sources");
    };
  }

  if (pill && menu && !pill._wired) {
    pill._wired = true;
    pill.onclick = (e) => {
      e.stopPropagation();
      const isHidden = menu.hidden;
      closeAllPickerFlyouts();
      if (isHidden) {
        refreshPickerPopover();
        menu.hidden = false;
        pill.classList.add("is-active");
      } else {
        menu.hidden = true;
        pill.classList.remove("is-active");
      }
    };
  }

  const headerChip = $("#agentModelChip");
  if (headerChip && !headerChip._pickerWired) {
    headerChip._pickerWired = true;
    headerChip.onclick = (e) => {
      e.stopPropagation();
      if (!menu) return;
      closeAllPickerFlyouts();
      refreshPickerPopover();
      menu.hidden = false;
      if (pill) pill.classList.add("is-active");
    };
  }

  if (provRow && !provRow._wired) {
    provRow._wired = true;
    provRow.onclick = (e) => {
      e.stopPropagation();
      if (!provFlyout) return;
      const isHidden = provFlyout.hidden;
      closeAllPickerFlyouts();
      if (isHidden) {
        renderProviderFlyout();
        provFlyout.hidden = false;
        placeFlyout(provFlyout);
        provRow.classList.add("cmp-active");
      }
    };
  }

  if (modelRow && !modelRow._wired) {
    modelRow._wired = true;
    modelRow.onclick = (e) => {
      e.stopPropagation();
      if (!modelFlyout) return;
      const isHidden = modelFlyout.hidden;
      closeAllPickerFlyouts();
      if (isHidden) {
        renderModelFlyout();
        modelFlyout.hidden = false;
        placeFlyout(modelFlyout);
        modelRow.classList.add("cmp-active");
      }
    };
  }

  const claudeRow = $("#cmpClaudeStatusItem");
  if (claudeRow && !claudeRow._wired) {
    claudeRow._wired = true;
    claudeRow.onclick = (e) => {
      e.stopPropagation();
      openDrawer("model");
      const p = (MODEL_CATALOG || []).find((x) => x.id === "claude");
      if (!p || !p.ready) {
        toast("Opening AI model to sign in with Claude");
      }
    };
  }

  const gptRow = $("#cmpChatGptStatusItem");
  if (gptRow && !gptRow._wired) {
    gptRow._wired = true;
    gptRow.onclick = (e) => {
      e.stopPropagation();
      openDrawer("model");
      const p = (MODEL_CATALOG || []).find((x) => x.id === "openai");
      if (!p || !p.ready) {
        toast("Opening AI model to sign in with ChatGPT");
      }
    };
  }

  if (!document._cmpPickerDocWired) {
    document._cmpPickerDocWired = true;
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".cmp-model-picker-wrap") && !e.target.closest("#agentModelChip")) {
        if (menu) menu.hidden = true;
        closeAllPickerFlyouts();
        if (pill) pill.classList.remove("is-active");
      }
    });

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && menu && !menu.hidden) {
        menu.hidden = true;
        closeAllPickerFlyouts();
        if (pill) pill.classList.remove("is-active");
      }
    });
  }
}

//: What `/api/agents/{id}/model` last said is in effect for the open agent.
//: The chip and the composer pill are rendered FROM this, so anything else that
//: needs to know which model a turn will run on reads it here rather than
//: guessing from localStorage — which is how the label and the turn came to
//: disagree. Declared in this file because `updateAgentModelChip` below is its
//: only writer, and this file loads before every reader.
let AGENT_MODEL_BINDING = null;

async function updateAgentModelChip(agentId) {
  const chip = $("#agentModelChip");
  const cmpLabel = $("#cmpModelLabel");
  try {
    const data = await api(`/api/agents/${agentId}/model`);
    AGENT_MODEL_BINDING = data;
    const labelEl = $("#agentModelLabel");
    const provName = data.configured_provider || data.provider || "cursor";
    const modelName = data.configured_model || "";

    const normProv = (provName === "claude-code" || provName === "anthropic") ? "claude" : provName;
    const pEntry = (MODEL_CATALOG || []).find((c) => c.id === provName) || (MODEL_CATALOG || []).find((c) => c.id === normProv);
    const pSpec = COMPOSER_PROVIDERS.find((x) => x.id === provName) || COMPOSER_PROVIDERS.find((x) => x.id === normProv);
    const provLabel = pSpec ? pSpec.label : (pEntry ? pEntry.label : provName);

    const isProvConn = isProviderConnected(provName);
    // The offline mock is what `settings.model_provider` still is on an install
    // where nothing has been connected, and it reports itself ready — so an
    // agent with no binding of its own resolves to it and the label said
    // "Auto". "Auto" sounds like a choice being made well. Say what will
    // actually answer, and let the resolved provider say it, because an agent
    // with no binding never had a `configured_provider` to give this away.
    const isOffline = (data.provider || "").toLowerCase() === "mock";
    let display = "Auto";
    if (isOffline) {
      display = "Offline";
    } else if (!isProvConn) {
      display = `${provLabel} — locked`;
    } else if (data.is_override) {
      if (modelName) {
        const mEntry = pEntry && (pEntry.models || []).find((m) => m.id === modelName);
        display = mEntry ? mEntry.name : modelName;
        if (mEntry && mEntry.locked) {
          display += ` — ${mEntry.plan_required || 'locked'}`;
        }
      } else {
        display = provLabel;
      }
    }

    if (labelEl) labelEl.textContent = display;
    if (chip) {
      chip.classList.toggle("is-override", Boolean(data.is_override));
      chip.title = isOffline
        ? "No AI provider is connected, so replies come from the offline model. "
          + "Open Model to connect one."
        : !isProvConn
        ? `${provLabel} is not connected. Open Model to connect.`
        : (data.is_override
          ? `Dedicated model for this agent: ${provLabel} (${modelName || 'Auto'}). Click to change.`
          : `Using global default model: ${provLabel} (${modelName || 'Auto'}). Click to set custom.`);
    }

    // Update the composer pill smoothly
    if (cmpLabel) {
      cmpLabel.textContent = display;
    }

    // Update privacy lock icon
    const lockEl = $("#cmpPrivacyLock");
    if (lockEl) {
      const isLocal = pEntry && pEntry.locality === "local";
      lockEl.title = isLocal ? "On-device (Private)" : "Leaves your Mac";
      lockEl.style.color = isLocal ? "#10b981" : "#e06c75";
    }

    // Sync composer picker state
    activePickerProvider = data.configured_provider || data.provider || "cursor";
    activePickerModel = data.configured_model || null;
    const activeSpec = COMPOSER_PROVIDERS.find((x) => x.id === activePickerProvider) || COMPOSER_PROVIDERS.find((x) => x.id === normProv);
    const activeProvConn = isProviderConnected(activePickerProvider);
    const activeProvLabel = activeSpec ? activeSpec.label : (pEntry ? pEntry.label : activePickerProvider);

    if ($("#cmpSelectedProvLabel")) {
      $("#cmpSelectedProvLabel").textContent = activeProvConn ? activeProvLabel : `${activeProvLabel} — locked`;
    }
    if ($("#cmpSelectedModelLabel")) {
      if (!activeProvConn) {
        $("#cmpSelectedModelLabel").textContent = "Connect in Models";
      } else {
        $("#cmpSelectedModelLabel").textContent = data.is_override ? (data.configured_model || "Auto") : "Auto";
      }
    }

    refreshPickerStatusRows();
  } catch (_) {}
}

function openAgentModelModal(agentId) {
  const menu = $("#cmpModelMenu");
  const pill = $("#cmpModelPill");
  if (menu && pill) {
    refreshPickerPopover();
    menu.hidden = false;
    pill.classList.add("is-active");
    pill.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function loadAgentModelMatrix() {
  const matrix = $("#agentModelMatrix");
  if (!matrix || matrix.hidden) return;
  api("/api/agents").then((data) => {
    const list = data.agents || [];
    matrix.innerHTML = list.map((a) => {
      const oid = agentOrbId(a);
      const isOver = Boolean(a.model_provider);
      return `
        <div class="matrix-row">
          <div class="matrix-agent">
            <span class="orb orb-sm" style="${orbStyle(oid)}"></span>
            <div>
              <div class="matrix-nm">${esc(a.name)}</div>
              <div class="matrix-role">${esc(a.role || "")}</div>
            </div>
          </div>
          <div class="matrix-actions">
            <span class="pc-badge ${isOver ? 'ready' : 'local'}" style="font-size:10.5px">
              ${isOver ? esc(a.model_provider + (a.model_name ? ` · ${a.model_name}` : '')) : 'Default'}
            </span>
            <button class="tiny ghost matrix-btn" data-agent="${esc(a.id)}">Change</button>
          </div>
        </div>
      `;
    }).join("");

    matrix.querySelectorAll(".matrix-btn").forEach((btn) => {
      btn.onclick = () => openAgentModelModal(btn.dataset.agent);
    });
  });
}

function loadProviderCards() {
  const box = $("#providerCards");
  if (!box) return;

  const GROUPS = [
    {
      id: "openai",
      title: "OpenAI",
      subtitle: "Choose a ChatGPT subscription or OpenAI API key.",
      providers: ["openai"],
    },
    {
      id: "anthropic",
      title: "Anthropic",
      subtitle: "Choose a Claude subscription or Anthropic API key.",
      providers: ["claude"],
    },
    {
      id: "xai",
      title: "xAI",
      subtitle: "Sign in to Grok and use the models available to your account.",
      providers: ["xai"],
    },
    {
      id: "cursor",
      title: "Cursor",
      subtitle: "Use a Cursor account or API key. Model access follows your Cursor plan and team settings.",
      providers: ["cursor"],
    },
    {
      id: "gemini",
      title: "Google Gemini",
      subtitle: "Provide a Gemini API key from Google AI Studio to run Gemini models.",
      providers: ["gemini"],
    },
    {
      id: "local",
      title: "On your Mac",
      subtitle: "Run open models locally through Ollama. No account, no key, no "
        + "tokens billed — and nothing leaves this machine.",
      providers: ["ollama"],
    },
    {
      id: "other",
      title: "Other ways to run models",
      subtitle: "Free models and OpenRouter.",
      providers: ["deepseek", "openrouter"],
    },
  ];

  box.innerHTML = GROUPS.map((g) => {
    return `
      <div class="ts-provider-group" id="grp_${g.id}">
        <div class="ts-provider-head">
          <h3 class="ts-provider-title">${esc(g.title)}</h3>
          <p class="ts-provider-sub">${esc(g.subtitle)}</p>
        </div>
        <div class="ts-group-boxes">
          ${g.providers.map((pid) => `<div id="pbox_${pid}"></div>`).join("")}
        </div>
      </div>
    `;
  }).join("");

  GROUPS.forEach((g) => {
    g.providers.forEach((pid) => {
      const el = $(`#pbox_${pid}`);
      if (el) renderProviderConnectBox(el, pid);
    });
  });
}

async function loadProviders() {
  try {
    const [catData, provData] = await Promise.all([
      api("/api/models/catalog").catch((err) => { console.warn("catalog fetch failed", err); return null; }),
      api("/api/providers").catch((err) => { console.warn("providers fetch failed", err); return null; }),
    ]);

    if (catData && catData.catalog && catData.catalog.length) {
      MODEL_CATALOG = catData.catalog;
    }
    if (provData && provData.providers && provData.providers.length) {
      PROVIDERS = provData.providers;
    }

    // Sync readiness, connection status, and metadata between providers and catalog
    (PROVIDERS || []).forEach((p) => {
      const entry = MODEL_CATALOG.find((c) => c.id === p.name);
      if (entry) {
        if (typeof p.connected === "boolean") entry.connected = p.connected;
        entry.ready = p.ready;
        entry.reason = p.reason;
        entry.connection = p.connection;
        entry.capabilities = p.capabilities;
        entry.detected_account = p.detected_account;
        entry.usage = p.usage;
        entry.plan = p.plan;
      }
    });

    const savedP = localStorage.getItem("chitragupta_provider");
    const active = savedP || (provData && provData.active) || "claude";
    if ($("#provider")) {
      $("#provider").innerHTML = (MODEL_CATALOG || []).map((p) =>
        `<option value="${p.id}" ${p.id === active ? "selected" : ""}>${p.label}${p.ready ? " (Ready)" : " (not ready)"}</option>`).join("");
      // If the saved provider is not in the catalog — a fallback list that has
      // not loaded yet, a provider that went away — none of the options above
      // matched, and the select silently falls back to its first entry. That is
      // a different provider from the one the user chose, and it is what the
      // composer would then send.
      setActiveModel(active, localStorage.getItem("chitragupta_model"));
      $("#modelName").value = localStorage.getItem("chitragupta_model") || "";
      applyModelHint();
      $("#provider").onchange = () => {
        localStorage.setItem("chitragupta_provider", $("#provider").value);
        applyModelHint();
      };
      $("#modelName").onchange = () => localStorage.setItem("chitragupta_model", $("#modelName").value.trim());
    }

    // Enrichment model — independent of the agent model. Empty = "same as agent".
    const ep = $("#enrichProvider");
    if (ep) {
      const savedE = localStorage.getItem("chitragupta_enrich_provider") || "";
      ep.innerHTML = `<option value="">same as agent model</option>` + (MODEL_CATALOG || []).map((p) =>
        `<option value="${p.id}" ${p.id === savedE ? "selected" : ""}>${p.label}${p.ready ? " (Ready)" : " (not ready)"}</option>`).join("");
      $("#enrichModelName").value = localStorage.getItem("chitragupta_enrich_model") || "";
      ep.onchange = () => localStorage.setItem("chitragupta_enrich_provider", ep.value);
      $("#enrichModelName").onchange = () => localStorage.setItem("chitragupta_enrich_model", $("#enrichModelName").value.trim());
    }
  } catch (err) {
    console.error("loadProviders error", err);
  }

  try { loadEnrichCap(); } catch (_) {}
  try { loadAgentModelMatrix(); } catch (_) {}
  try { loadProviderCards(); } catch (_) {}
  try { initComposerModelPicker(); } catch (_) {}
  if (current) updateAgentModelChip(current);
  refreshPickerPopover();
  renderProviderFlyout();
  renderModelFlyout();
}

// Which provider/model to use for enrichment: the dedicated one if set, else the
// agent model (so nothing breaks for users who never touch this).
function enrichModel() {
  const p = (localStorage.getItem("chitragupta_enrich_provider") || "").trim();
  if (p) return { provider: p, model: (localStorage.getItem("chitragupta_enrich_model") || "").trim() || null };
  // The agent binding, not the localStorage pair. This decides whether to warn
  // that enrichment is about to spend real tokens, and the old source could
  // name a provider that was not the one that would run — it read the same
  // stale key that was sending turns to the offline mock. A null provider is
  // the server's own default, and it warns rather than assuming it is free.
  const b = AGENT_MODEL_BINDING;
  return { provider: (b && (b.configured_provider || b.provider)) || null,
           model: (b && b.configured_model) || null };
}

async function loadEnrichCap() {
  const inp = $("#enrichCap"); if (!inp) return;
  try {
    const c = await api("/api/brain/enrich/config");
    inp.value = c.cap;
    const note = $("#enrichCapNote");
    if (note) note.textContent = `${c.remaining} queued`;
  } catch (_) {}
  const btn = $("#enrichCapSave");
  if (btn && !btn._wired) {
    btn._wired = 1;
    btn.onclick = async () => {
      const cap = Math.max(0, parseInt(inp.value, 10) || 0);
      btn.disabled = true;
      try {
        const r = await api("/api/brain/enrich/config", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cap }) });
        const note = $("#enrichCapNote");
        if (note) note.textContent = `${r.remaining} queued`;
        toast(cap ? `Enriching recent ${cap} per bulk source` : "Enriching all items");
      } catch (e) { toast("couldn't save"); }
      btn.disabled = false;
    };
  }
}

// ── the AI model screen ────────────────────────────────────────────────────
// Model used to be a 380px slide-over. Its content is a grid of provider
// cards — each an account, a plan, usage limits and a model list — so it gets
// the whole window, opened exactly the way the brain screen is.
// Which settings panel is on screen. Model and Connectors share the shell, so
// showing one is hiding the others — a panel left visible underneath is how a
// "page" quietly becomes two pages stacked.
function showSettingsPanel(name) {
  document.querySelectorAll(".sp").forEach((el) => { el.hidden = el.dataset.sp !== name; });
  document.querySelectorAll(".ms-nav-item").forEach((b) => {
    const on = b.dataset.msnav === name;
    b.classList.toggle("is-active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  const main = $(".ms-main"); if (main) main.scrollTop = 0;
}
async function openInboxScreen() {
  const m = $("#modelScreen"); if (!m) return;
  m.hidden = false;
  showSettingsPanel("inbox");
  // Each of the three sections loads itself; one failing must not blank the
  // other two, which is what a single await chain would do.
  try { await loadApprovals(); } catch (_) {}
  try { await loadRoutines(); } catch (_) {}
  try { await loadReminders(); } catch (_) {}
}
async function openToolsScreen() {
  const m = $("#modelScreen"); if (!m) return;
  m.hidden = false;
  showSettingsPanel("tools");
  // The picker needs the agent list; the list needs the picker to know which
  // agent it is for. Both come from `agents`, already loaded at startup.
  try { renderAgentToolPicker(); } catch (_) {}
  try { await loadAgentTools(); } catch (_) {}
}
async function openConnectorsScreen() {
  const m = $("#modelScreen"); if (!m) return;
  m.hidden = false;
  showSettingsPanel("connectors");
  try { await loadBrain(); } catch (_) {}   // fills #connectors, #googleCard, sync status
}
// Who you are signed in as, per provider. The cards are #providerCards, which
// loadProviders() has always filled — the panel moved, the renderer did not.
async function openAccountScreen() {
  const m = $("#modelScreen"); if (!m) return;
  m.hidden = false;
  showSettingsPanel("account");
  try { await loadProviders(); } catch (_) {}
}
async function openModelScreen() {
  const m = $("#modelScreen"); if (!m) return;
  m.hidden = false;
  showSettingsPanel("model");
  updateUsage();
  // Nothing here may reject: openDrawer() calls this without awaiting, so an
  // unhandled rejection is all the user would get. The defaults are built FROM
  // the catalog, so they wait for it — without the await the provider list is
  // whatever was cached from the last open, and on a first open that is
  // nothing at all.
  try { await loadProviders(); } catch (_) {}
  try { await loadAgentDefaults(); } catch (_) {}
  // Lives in workspace.js with the approvals queue it belongs to — the grant and
  // the review of it are one subsystem, and splitting them would put the same
  // endpoint in two files.
  try { await loadAllowList(); } catch (_) {}
  // Last, and never awaited into the others: a log that cannot be read must not
  // stop the settings screen rendering the parts that can.
  try { await loadDiagnosticsLog(); } catch (_) {}
}
function closeModelScreen() {
  const m = $("#modelScreen"); if (m) m.hidden = true;
}
{ const c = $("#msClose"); if (c) c.onclick = closeModelScreen; }
// Only the screens that actually exist are listed: Brain and Model are the two
// full-window views, and the rest of the workspace nav is still drawers.
document.querySelectorAll(".ms-nav-item").forEach((b) => {
  b.onclick = () => {
    const to = b.dataset.msnav;
    if (to === "model") return openModelScreen();
    if (to === "account") return openAccountScreen();
    if (to === "connectors") return openConnectorsScreen();
    if (to === "inbox") return openInboxScreen();
    if (to === "tools") return openToolsScreen();
    // Leaves the app entirely, so nothing after it runs and the screen does not
    // need closing — the navigation replaces the document.
    if (to === "onboarding") { window.location.href = "/onboarding?replay=1"; return; }
    // The two that are their own full-window screens rather than panels in this
    // shell: close this one first, or it stays open underneath them.
    closeModelScreen();
    if (to === "brain") openBrainScreen();
    if (to === "library" && typeof openLibrary === "function") openLibrary();
  };
});

// ── "Default AI for new agents" ───────────────────────────────────────────
// All three of these were already being read on every turn and none of them
// had a control. Provider and model live in localStorage (setActiveModel is
// the one writer, so the composer and this page cannot disagree); effort is
// server-side and has had GET/POST /api/agents/effort all along with nothing
// calling it — so it could not be changed from the app at all.
function _defProviderList() {
  return (MODEL_CATALOG || []).map((p) => ({
    id: p.id, label: p.label || p.id, ready: Boolean(p.ready), models: p.models || [],
  }));
}
// The provider is passed in, never read back off the select. On a first open
// nothing is saved, so no <option> carries `selected` and the element's value
// is whatever the browser implicitly picked — which is a different question
// from "which provider are we showing models for".
function renderDefaultModelOptions(pid) {
  const sel = $("#defModel"); if (!sel) return;
  const prov = _defProviderList().find((p) => p.id === pid);
  const saved = localStorage.getItem("chitragupta_model") || "";
  const opts = [`<option value="">Auto — best available</option>`];
  for (const m of (prov ? prov.models : [])) {
    const id = m.id || m.name;
    // A locked model is shown and disabled with the reason, never hidden —
    // hiding it is how "why can't I pick that?" becomes unanswerable.
    const why = m.locked ? ` — ${m.plan_required || "not on your plan"}` : "";
    opts.push(`<option value="${esc(id)}"${m.locked ? " disabled" : ""}`
      + `${id === saved ? " selected" : ""}>${esc(m.name || id)}${esc(why)}</option>`);
  }
  sel.innerHTML = opts.join("");
  if (!saved) sel.value = "";
}
async function loadAgentDefaults() {
  const provSel = $("#defProvider");
  const list = _defProviderList();
  let active = localStorage.getItem("chitragupta_provider")
    || ($("#provider") && $("#provider").value) || "";
  // A saved provider the catalog no longer carries is a stale choice, not a
  // selection: fall back to the first one rather than showing an empty model
  // list under a provider name that is not in the list.
  if (!list.some((p) => p.id === active)) active = list.length ? list[0].id : "";
  if (provSel) {
    provSel.innerHTML = list.map((p) =>
      `<option value="${esc(p.id)}"${p.id === active ? " selected" : ""}>`
      + `${esc(p.label)}${p.ready ? "" : " — not connected"}</option>`).join("");
    provSel.value = active;
    renderDefaultModelOptions(active);
    provSel.onchange = () => {
      // Switching provider invalidates the model: keep Auto rather than
      // carrying an id the new provider has never heard of.
      active = provSel.value;          // the model handler below reads this
      setActiveModel(active, null);
      renderDefaultModelOptions(active);
      applyModelHint();
    };
  }
  const modelSel = $("#defModel");
  if (modelSel) modelSel.onchange = () => {
    setActiveModel(active, modelSel.value || null);
    applyModelHint();
  };

  const effSel = $("#defEffort");
  if (!effSel) return;
  try {
    const { current, levels } = await api("/api/agents/effort");
    effSel.innerHTML = (levels || []).map((l) =>
      `<option value="${esc(l.name)}"${l.name === current ? " selected" : ""}>${esc(l.label)}</option>`).join("");
    const describe = () => {
      const lvl = (levels || []).find((l) => l.name === effSel.value);
      const d = $("#defEffortDesc");
      if (d && lvl) d.textContent = lvl.description;
    };
    describe();
    effSel.onchange = async () => {
      const level = effSel.value;
      try {
        await api("/api/agents/effort", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ level }) });
        describe();
        toast(`Effort set to ${level}`);
      } catch (_) { toast("Could not change effort"); }
    };
  } catch (_) {
    // No effort endpoint to talk to — say so rather than leaving a dead select.
    effSel.innerHTML = `<option>Unavailable</option>`;
    effSel.disabled = true;
  }
}
