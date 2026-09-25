# ◆ Chitragupta

**The local-first AI workspace where your agents already know you.**

> **Chitragupta** — in Hindu tradition, the scribe who keeps the record of what
> each person has actually done. Fitting for an app whose whole job is to
> remember your work and always be able to say where a fact came from.

Chitragupta is an open competitor to [Turnstone](https://myturnstone.ai). Connect
your apps and folders; Chitragupta turns them into a **continuously-updated
knowledge-graph brain** on your own machine. Then spin up specialized **agents**
— from a built-in library, or ones you define yourself — that all share that
one brain and run on **any model you already pay for**. Stop re-explaining
yourself to AI.

- 🧠 **Knowledge-graph brain** — memories *and* an entity/relation graph, built locally, shared by every agent.
- 🤖 **A team, one brain** — Inbox · Launch · Research · Personal · Health to start, plus your own, each domain-scoped and each remembering its own work.
- 🔌 **Bring your own model** — subscription gateway · Claude · OpenAI · OpenRouter · Ollama. Swap freely.
- 🛠️ **Agents that act** — tool-using loop: search the brain, search the web, pull live Gmail, remember new facts.
- 🔒 **Local-first** — everything in `~/Library/Chitragupta`, no cloud copy, no telemetry.
- ⚡ **Runs day one** — offline `mock` model + `hash` embeddings mean zero keys required to try it.

> 📖 **Changing the code?** Start with
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — who owns what, which
> direction dependencies may run, the contracts between layers, and the
> invariants that are load-bearing even when the tests are green. The rules an
> agent must follow are in [`CLAUDE.md`](CLAUDE.md) and a short note in each
> code directory.
>
> 📖 **The story:** [`docs/JOURNEY.md`](docs/JOURNEY.md) — how it was built, start to now. **Decisions:** [`docs/DECISIONS.md`](docs/DECISIONS.md). **Known gaps:** [`docs/AUDIT.md`](docs/AUDIT.md). **Connectors plan:** [`docs/CONNECTORS.md`](docs/CONNECTORS.md).
>
> 📖 **The agents:** [`docs/AGENTS.md`](docs/AGENTS.md) — how a turn runs,
> the effort setting, delegation, unattended-action permissions, streaming,
> and the capability scorecard.
>
> 📖 **New here?** Read [`docs/PROJECT.md`](docs/PROJECT.md) — what this is, how
> every part works, and why each decision was made — and
> [`docs/CONCEPTS.md`](docs/CONCEPTS.md) — embeddings, vector search, semantic
> search and the knowledge graph explained from scratch.
>
> 📖 **Working on the desktop window or sign-in?** Read
> [`docs/DESKTOP-SIGNIN.md`](docs/DESKTOP-SIGNIN.md) first — the floating card,
> the macOS window behaviour behind it, and the measured record of what went
> wrong. Several of the obvious explanations turned out to be wrong.

---

## Architecture

```
   ┌─────────── Workspace (web) ───────────┐
   │  Agent tabs   Chat + tool trace   Brain│
   └───────────────────┬────────────────────┘
                        │  FastAPI
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
   Agent runtime   Model providers    Brain
   (tool loop)     (BYO: claude/      ├─ vector memories (SQLite)
   Inbox/Launch/   openai/openrouter/ └─ knowledge graph (entities+facts)
   Research/       ollama/subscription       ▲
   Personal)              │                   │ ingest + extract
        └── tools ─────────┘         Connectors: files · notes · gmail
                                     notion · gdrive (+ imessage/linear/…)
```

Agents are the core primitive. Each runs a **model + tool-use loop**: the model
decides to call tools (`search_brain`, `web_search`, `gmail_search`, `remember`),
Chitragupta executes them against the shared brain and live connectors, and the
model answers — already knowing you.

## Install

```bash
uv venv && uv pip install -e .          # core, runs offline with zero keys
uv pip install -e ".[all]"              # + real embeddings & all connector SDKs
```

## Install (technical testers, macOS)

```bash
curl -LsSf https://raw.githubusercontent.com/suryansh2846code/chitragupta/main/scripts/install.sh | bash
cd ~/chitragupta && .venv/bin/chitragupta app
```
Local-first connectors (Files, Apple Mail, Apple Calendar, iMessage) need no
sign-in — just Full Disk Access.

## Run as a desktop app

```bash
uv pip install -e ".[desktop]"           # native-window deps (pywebview)
chitragupta app                            # opens Chitragupta in a native window
bash scripts/build-macos-app.sh          # → ~/Applications/Chitragupta.app (double-click)
```

## Quickstart (browser)

```bash
chitragupta serve                          # workspace at http://127.0.0.1:8787
chitragupta agents                         # list Inbox/Launch/Research/Personal
chitragupta ingest --text "I build for Indian SMBs on Cloudflare Workers."
chitragupta chat research "what do I build?"
chitragupta stats                          # brain + knowledge-graph stats
chitragupta providers                      # model backends & readiness
```

## Bring your own model

Set `CHITRAGUPTA_MODEL_PROVIDER` (and the matching key) in `.env`:

| provider | how | key |
|----------|-----|-----|
| `mock` | offline, deterministic | none (default) |
| `anthropic` | Claude Messages API | `ANTHROPIC_API_KEY` |
| `openai` | GPT | `OPENAI_API_KEY` |
| `openrouter` | hundreds of models | `OPENROUTER_API_KEY` |
| `ollama` | free local models | none (Ollama on :11434) |
| `subscription` | your paid ChatGPT/Claude/Cursor session via a local OpenAI-compatible gateway | `CHITRAGUPTA_SUBSCRIPTION_BASE_URL` |

> The `subscription` path is the hard, ToS-sensitive route Turnstone advertises.
> Chitragupta treats it as a pluggable gateway (point it at a local subscription
> proxy) rather than reverse-engineering each vendor's private auth.

## The brain

Ingestion writes to **both** a vector store (recallable chunks) and a
**knowledge graph** (entities + facts, extracted by the LLM when available, with
an offline heuristic fallback). Recall fuses both into one injectable context
block, so agents start already knowing your people, projects and preferences.

## Connectors

`files`, `notes`, `imessage`, `apple_mail`, `apple_calendar` and `apple_health`
are local — no sign-in, just Full Disk Access. `gmail`, `gcal`, `gdrive` and
`google_fit` need a Google OAuth Desktop client (`GOOGLE_CLIENT_SECRETS`);
`notion` needs `NOTION_TOKEN`; `github`, `slack`, `linear` and `telegram` each
sign in their own way. Anything else is reached over **MCP** (`mcp:<id>`) or as
a custom API app (`custom:<id>`) — see
[`docs/REACHING-AN-APP.md`](docs/REACHING-AN-APP.md) for which route a given app
takes, and [`docs/CONNECTORS.md`](docs/CONNECTORS.md) for the set.

**Reading is free; changing anything is not.** Sync is read-only, but agents can
also act — send mail, triage an inbox, write to a connected app. Every outbound
action is tiered in `actions.py` and gated in `agents/permissions.py`: it either
names a recipient the user put on an allow-list, or it waits for one tap on a
card the user can correct before confirming.

## Roadmap

- [x] iMessage · Linear · Slack · GitHub · Telegram · Apple Health connectors
- [x] Streaming chat + inline action cards the user can correct before confirming
- [x] Custom user-defined agents, and an agent library to start from
- [x] Native macOS window (pywebview), signed and notarised `.dmg`
- [ ] Browser-history and Granola connectors (match Turnstone's set)
- [ ] Continuous background auto-indexing (watch folders/inbox)

## License

Apache-2.0.
