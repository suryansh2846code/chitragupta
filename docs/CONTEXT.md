# Chitragupta — Product Context & Direction

> The living context doc: what we're building, the real Turnstone vision we're
> matching, where we are, the gaps, the plan, and the bug watch-list.
> Repo: https://github.com/suryansh2846code/chitragupta

---

## 1. What we're building

**Chitragupta** = a private, local-first AI workspace for everyday knowledge work.
Connect your apps and files → they become a **second brain** on your own machine
→ specialized **agents** answer *and act* using that brain, on **any model you
bring**. An open, faithful "same-to-same" build of **Turnstone (myturnstone.ai,
YC W26)**, ultimately for a **normal person's daily life** (Gmail, Notion,
Calendar, Drive, messages — everything).

---

## 2. The real Turnstone vision (from founders Aryan Vij & Jai Bhatia's launch)

Turnstone extends the "one-prompt" productivity of AI coding to **all** work.

**The problem they name:**
> "every task still meant hunting through email, Drive, meeting transcripts, and
> our CRM, then rebuilding the context in a blank chat."

**The product:**
- Multiple **specialized agents** (chief of staff, inbox, researcher, personal)
  working **in parallel** on a **second brain** built from your apps + files.
- Each agent **learns your work patterns, writing style, and decisions**.
- **It takes action** — the flagship line: *"my email agent… can you just handle
  that for me?"* → **immediate task execution**, not just answers.
- **Local-first** (brain lives on your Mac, user-owned files, no cloud copy).
- **Free** — bring your own API keys / subscriptions.

**Three pillars we must match:**
1. **Brain from ALL your apps** (email, calendar, docs, transcripts, CRM, notes).
2. **Agents that DO, not just answer** ("handle that for me" = real execution).
3. **Learns your voice/style/decisions** so its output sounds like you.

---

## 3. Where Chitragupta is today (built & working)

- **Brain**: SQLite store, semantic embeddings (local sentence-transformer,
  offline), knowledge graph (typed entities + facts), fused recall. Offline by
  default, no keys needed.
- **Agents**: Inbox, Launch, Research, Personal — share one brain; runtime with
  auto-recall, auto-learn, date-grounding, task-grounding, tool loop.
- **Models (BYO)**: claude-code (your terminal Claude), ollama (free local),
  anthropic/openai/openrouter (keys), subscription slot, mock. Switchable in UI.
- **Tools**: search_brain, remember, list_entities, web_search (live), tasks.
- **Connectors**: local files, manual notes, Gmail, Notion, Google Drive
  (read-only). Files hardened (cap + junk filters).
- **Tasks**: local store + natural due-date parsing + deterministic UI panel.
- **Workspace UI**: chat (markdown, stop, multiline), brain panel (search,
  clickable entities, connectors w/ setup guides), tasks panel, folder picker,
  onboarding, settings (provider+model), responsive, favicon.
- **MCP bridge**: exposes the brain to terminal Claude / Cursor.
- **Docs**: README, docs/PROJECT.md, docs/CONCEPTS.md, this file.

---

## 4. Gap analysis vs the real vision

| Turnstone pillar | Chitragupta now | Gap |
|---|---|---|
| Brain from ALL daily apps | files/notes/gmail/notion/gdrive | **+ Calendar, iMessage, browser, Slack, Linear, transcripts** |
| Agents that **take action** | agents draft/answer; can't *do* | **biggest gap — execution (send email, create event, etc.)** |
| Learns your **writing style** | recalls facts, not style | **capture & reuse the user's voice** |
| Continuous auto-indexing | manual sync | **background sync** |
| Custom agents | 4 fixed presets | let users create agents |
| Mac desktop app | web-first | Tauri shell later |

**The two that matter most for "a normal person's daily brain":**
1. **More connectors** (Gmail-easy, Calendar, messages) — so real daily data flows in.
2. **Action-taking** — "handle it for me" (drafting → actually doing), the heart
   of Turnstone. Must be built carefully (confirmation before any send/write).

---

## 5. Direction / plan (reconsidered toward the vision)

**Now (this phase): connect a normal person's daily apps.**
- Google **Calendar** connector (reuses our Google OAuth) — meetings/schedule.
- Make **Gmail** genuinely usable (it exists; smooth the OAuth path).
- **iMessage** (local Mac chat.db) + **browser history** — local, no auth, very
  "second brain."
- Keep everything read-only until action-taking is designed.

**Next: action-taking (the "handle it for me" leap).**
- Agents propose an action (draft email / calendar event / task) → **user
  confirms** → Chitragupta executes via the connector's write scope.
- Start with the safest: create calendar events, create tasks (done), draft (not
  send) email; then gated send with explicit confirmation.

**Then: learn the user's voice.**
- Store style exemplars (past emails/messages) → agents draft in the user's tone.

**Then: continuous background sync + custom agents + Tauri desktop shell.**
- Plus the deferred **Tier 2 scaling** (sqlite-vec + FTS5) when the brain gets big.

---

## 6. Bug watch-list (keep checking)

- ✅ FIXED: Claude Code blank `(Claude Code error: )` — was a fake transcript
  tripping stop-sequence; now uses --append-system-prompt + parses is_error.
- ⚠️ KNOWN: on the **claude-code** backend, agents can't call our tools, so
  chat-driven actions (add task, remember) are **claimed but not executed**
  ("session-only" reminder). Reliable path: the deterministic UI panels, or the
  **ollama** backend (real tool calls). Real fix: give Claude Code our tools via
  MCP (`--mcp-config`) so it executes for real. TODO.
- ⚠️ WATCH: small local models (3B) sometimes narrate fake tool success or pick
  wrong tools; mitigated by grounding + low temp, not eliminated.
- ⚠️ WATCH: huge folder syncs bloat the brain — capped at CHITRAGUPTA_MAX_FILES
  (default 2000) with junk filters; raise deliberately.
- ⚠️ WATCH: first model/embedder load is a slow cold start (torch import);
  subsequent loads are offline/fast.

---

## 7. Principles (don't break these)

- **Local-first, private** — data on the user's machine; nothing leaves it except
  the model call they chose.
- **Polished, finished** — no demo-grade rough edges; handle errors and edges.
- **Deterministic where it matters** — critical actions (tasks, sends) must not
  depend on flaky model tool-calls; give a reliable UI path.
- **Confirm before acting outward** — never send/modify external data without
  explicit user confirmation.
- **BYO model, swappable** — never hard-couple to one provider.
