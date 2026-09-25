# The agents

> How a turn actually runs, what each part is defending against, and what is
> still missing. Numbers here are measured on this machine, not estimated.
>
> Companions: [`PROJECT.md`](PROJECT.md) (the whole app),
> [`BRAIN-V1.5.md`](BRAIN-V1.5.md) (what they recall from),
> [`AUDIT.md`](AUDIT.md) (standing defects).

## What an agent is

A named, domain-scoped worker with its own system prompt, its own tool set, its
own conversation history and its own model binding — sharing one Brain with
every other agent, so what one learns makes the rest smarter.

Four ship by default (Inbox · Launch · Research · Personal), users can create
more, and one is pinned as the **lead**. They are data, not code: presets live
in `agents/presets.py`, user-made ones in SQLite (`agents/custom.py`).

## A turn, start to finish

```
run_turn(agent_id, text, effort=…, on_event=…)
  │
  ├─ resolve model          stale binding re-checked and repaired (entitlements)
  ├─ effort profile         Low | Medium | High → every budget below
  ├─ runtime identity       so "what model are you?" is answered truthfully
  ├─ date, twice            system prompt AND next to the question (small models
  │                         ignore mid-context notes)
  ├─ auto-recall            the brain, BEFORE the model chooses anything
  ├─ open tasks             authoritative list, so it cannot invent them
  ├─ history                recent verbatim + a running summary of older turns
  │
  ├─ LOOP, up to N rounds ──────────────────────────────────────────────┐
  │    provider.stream(...)      text emitted as it is written          │
  │    tool calls?  ──► run in PARALLEL, deduped against a per-turn memo│
  │    re-state the plan                                                │
  │    nudge if a call failed                                           │
  │    two rounds that learn nothing ──► stop, ask it to answer         │
  │    budget spent ──► one more call with tools withheld               │
  │  ───────────────────────────────────────────────────────────────────┘
  │
  ├─ plan check             steps the agent never ticked off ──► told ONCE,
  │                         with tools, so it can finish rather than apologise
  ├─ auto-learn             durable facts → raw memory + canonical curation
  └─ TurnResult             reply, trace, plan, effort, steps_used
```

## Effort — one gear selector

| | Low | Medium | High |
|---|---|---|---|
| Tool rounds | 5 | 12 | 24 |
| Parallel calls | 2 | 4 | 6 |
| Verbatim history | 6 | 10 | 16 |
| Summary written by | heuristic (free) | model | model |
| Delegation depth | off | 1 hop | 2 hops |
| Memories recalled | 6 | 10 | 16 |
| Planning | off | on | on |

Every one of those trades quality against cost in the same direction, so
exposing them separately would be a settings screen nobody can reason about.
One control, and the rest follows.

This matters more here than in a hosted product: the model runs on the user's
own key or subscription, so a deeper loop spends **their** money. Low is a real
choice, not a degraded mode — a 3B local model given 24 rounds mostly finds 24
ways to go wrong.

`GET/POST /api/agents/effort` · stored in `meta.agent_effort` · a client may
override per turn in the chat body.

## The loop

**Depth needs two companions.** A model with rounds left and nothing new to
learn will spend them re-issuing the same call. So `agents/loop.py`:

- **Memoises** every call by `name` + sorted arguments. A repeat is answered
  from the memo with a note to move on, so the model gets its answer *and* the
  information that it is going in circles.
- **Watches for a round that learns nothing.** Two in a row and the turn is
  asked to answer with what it has.

Two cases are deliberately distinguished. The same call three times in **one**
round is a wasteful model: run once, hand all three the answer, not a stall. The
same call in a **later** round is a model that has stopped making progress, and
that is what ends the turn.

**Parallelism.** Calls in a round run concurrently, bounded by the effort
profile. Safe because `sqlite3.threadsafety` is 3 here, every store opens with
`check_same_thread=False` under WAL with a busy timeout, and 60 concurrent mixed
reads and writes across three stores produced no errors. Results are reassembled
in request order — each has to match its `tool_call_id` or the provider rejects
the message.

**Running out.** Spending the budget no longer yields a placeholder. The turn
makes one more call with tools withheld and asks for an answer: an agent that
has researched for twenty rounds usually can give one, it has just never been
told to stop.

## Conversation memory

The window used to be six messages. The comment defending it was right about the
symptom — long histories confuse small models and let stale turns bleed in — and
wrong about the cure. Compress the old part; do not delete it.

`agents/context.py` keeps recent turns verbatim and folds older ones into a
running summary, **extended rather than rewritten**: only the slice that has
newly aged out is folded in, so a long conversation does not re-summarise itself
on every message. The writer follows the effort level, and a failed summary call
falls back to the heuristic rather than failing the turn.

## Delegation

`ask_agent(agent_id, question)` lets any agent put a focused question to any
other and get the answer back as a tool result. The caller stays responsible for
the final reply.

An agent that can call an agent can call itself, so the guards are the feature
(`agents/delegation.py`):

- **Depth** from the effort profile, with a sub-agent on half its parent's
  budget — otherwise a chain of three at High turns one question into dozens of
  model calls.
- **No agent twice in one chain.** Inbox → Research → Inbox ends only when the
  budget does.
- **Context propagation.** The chain lives in a `ContextVar`, and tools run in a
  thread pool, which does **not** copy context. One `copy_context()` **per call**
  — a `Context` cannot be entered twice at once, so one copy shared between
  workers raises the moment two overlap. Miss this and the other two guards are
  decoration.

Refusals are returned as prose, never raised: the caller is a model, and a
sentence it can act on beats an exception the loop has to translate.

## Planning

`update_plan(steps, done_through)` lets the agent write down what it intends to
do and revise it. The plan is re-stated every round so it does not scroll out of
attention, streamed to the UI, and returned in the result. Offered only where
the effort level allows it — planning costs a round, and at Low that round is
better spent answering.

A failed tool call now says so explicitly, because left alone a model re-issues
the same broken call and the memo then answers it from cache, so it never learns.

## The user's own connectors, mid-turn

The thirteen built-in tools are known when the app is built. A connector's tools
are not: they arrive when the user adds an MCP server, they differ per install,
and they change when the server updates. So an agent's stored tool list cannot
name them — a list written today would name tools that do not exist until
tomorrow, and `build_tools()` would drop them forever.

An agent opts in to the **category** instead, with `mcp` in its tools list
(`agents/mcp_tools.SENTINEL`; all four presets have it). The concrete tools are
resolved when the turn starts, from
`connectors.mcp_tools.list_tools()` → `MCPToolRef`, and called through
`call_tool()`. Naming one tool explicitly still works, for a custom agent
narrowed to a single tool.

**Read-only, with no exception.** A ref whose `writes` flag is set is never
offered, at any effort, to any agent. Connector writes stay on the propose →
confirm path as `mcp_action`, which is in `permissions.NEVER_UNATTENDED` — there
is no field to read a recipient out of on somebody else's server, so there is
nothing an allow-list could check. Filtering here is that same decision applied
one layer earlier: a model cannot propose what it was never handed.

Four details that are load-bearing:

- **Names are made provider-safe.** `notion:search` is a legal MCP name and an
  illegal tool name upstream; a name a provider rejects fails the whole request,
  not just the tool. It is offered as `notion_search`, and the qualified name is
  what goes back to the connector.
- **Discovery is cached for `CACHE_SECONDS` (20s).** Listing tools starts every
  configured server as a subprocess, and a turn resolves names more than once.
- **Connector calls have their own ceiling** (`MAX_CONCURRENT_CALLS`, 3). The
  loop's parallel width (6 at High) is sized for SQLite reads; these are
  subprocesses. Bounding them here keeps the width for the cheap tools.
- **A server's answer is capped** at `MAX_RESULT_CHARS`, because whatever it
  returns is read by a model and charged to the user.

A failure — server missing, not signed in, timed out — comes back as the
connector layer's own sentence (`connectors/mcp_errors.explain`), never a
traceback, and never as an exception into the loop.

`GET /api/agents/tools` describes every tool with `source` (`builtin` | `mcp`)
and `connector`, so the agent builder can group the user's connectors instead of
listing their tools as if they shipped with the app.

## Acting on the world

Agents **propose**; the user **confirms**. A reply carries a tag the UI parses
into a Confirm card:

```
<action type="send_email" to="…" subject="…" at="tonight 9pm">body</action>
```

This is deliberately not tool-calling, so it works on backends that have none.

### Unattended actions — the important part

A routine is pre-authorisation, and for *summarise my inbox every morning* that
is exactly right. It stops being right the moment the trigger is `new_email`,
because the text driving the agent was then written by a stranger. An email
carrying instructions aimed at the model reaches an agent that can emit a
`send_email` action, and before this there was **nothing between that and the
mail leaving the machine**.

| action | unattended? |
|---|---|
| `set_reminder`, notes, tasks, summaries | always — none of them reach anyone |
| `create_event` with no attendees | yes |
| `send_email`, `create_event` with attendees | **only to a permitted recipient** |
| `create_routine` | **never** — it widens its own authority |

The allow-list is explicit and user-managed. Deriving it from "people you have
emailed before" was considered and rejected: a stranger already in your inbox is
exactly who an injected instruction would name.

A blocked action is **queued, not dropped**. Dropping is worse than it sounds —
a routine that quietly declines to send the mail it exists to send looks exactly
like one that is working, and the user finds out when somebody asks why they
never replied. The proposal is kept verbatim, described in the user's terms,
notified, and approving runs exactly what the agent proposed.

Interactive chat is **not** gated: the Confirm button is a stronger signal than
any stored list, and a test fails if that path ever starts asking twice.

`agents/permissions.py` · `agents/approvals.py` ·
`GET/POST /api/agents/permissions` · `GET /api/agents/approvals`

## Streaming

Two wire formats cover every backend, which is a lucky accident worth stating:

| format | backends |
|---|---|
| Anthropic Messages | Claude API · Claude CLI (`stream_event` nesting) · Grok CLI |
| OpenAI chat-completions | OpenAI · OpenRouter · Ollama · DeepSeek · xAI · Gemini |

Both parsers yield text the moment it arrives **and** accumulate tool calls,
because the loop needs them from the same response.

`LLMProvider.stream()` defaults to yielding a whole `chat()` in one piece, so
every provider streams from the day the seam exists and no caller branches on
whether a backend can. The offline `mock` model streams for real, which makes it
the way to exercise the endpoint and the incremental render with no keys and no
spend. CLI backends fall back when a stream yields no text — decided *before*
anything is emitted, since falling back afterwards would duplicate it.

**Streaming is a callback on the existing loop, not a second loop.**
`run_turn(on_event=…)`, and a test asserts that watching a turn does not change
it. Two loops would mean reproducing every bug twice.

The endpoint is SSE (`POST /api/agents/{id}/chat/stream`), running on the
`MODEL_CALLS` lane so a conversation does not hold a worker thread. Event types:
`token` · `tool_call` · `tool_result` · `plan` · `done` · `error`. A client
renders `token`s as a preview and replaces them with `done`'s reply — the
streamed text is what the model said on the way, `done.reply` is what was stored.

**In the browser, reassemble frames across chunk boundaries.** One network chunk
is not one SSE frame; a reader that assumes it passes every hand test and drops
tokens against a real server.

## Is it any good? — the scorecard

`agents/evaluation.py` runs the real loop against a scripted model — no network,
no keys, no spend — and scores seventeen capabilities.

```bash
curl -s localhost:8787/api/agents/evaluate | jq .score
./.venv/bin/pytest tests/test_agent_evaluation.py
```

Deliberately **not** a benchmark of answer quality: that needs a real model and
a human, and it moves when the model does. These check what the harness itself
is responsible for, and those either work or they do not.

The floor is all of them, and that was set by evidence rather than taste. It was
written at 9.0 first; switching off the repeat guard scored **9.3**, because
with fifteen checks each was worth 0.67 and a real regression fitted underneath
the gate. A scorecard that cannot fail measures nothing, so
`test_the_scorecard_notices_when_a_capability_breaks` breaks one on purpose.

## Where this leaves the agents

| | before | after |
|---|---|---|
| Loop / reasoning | 4.5 | **9** |
| Agents working together | 3 | **8.5** |
| Perceived responsiveness | 3 | **9** |
| Acting safely | 5 | **8.5** |
| Memory & grounding | 8.5 | **9** |
| Tool range | 6.5 | **7.5** |
| **Overall** | **5** | **8.5** |

## What is still missing

1. **Tool range now depends on what the user connects.** Thirteen built-ins,
   plus every read tool the user's own MCP connectors expose (see *The user's
   own connectors, mid-turn*). What is still missing is the other half of that
   trade: a connector **write** is a propose → confirm action, so an agent
   cannot complete a task inside someone else's app in one turn, by design.
2. ~~**No agent-level quality evaluation.**~~ **Shipped 2026-09-25** —
   `agents/quality.py` asks the connected model a seeded golden set and grades
   what the answer must and must not contain. It needs a real model and says so
   when it has not got one; it does not need a person, because the failures
   that matter here are not matters of taste. What is still missing is breadth:
   five cases, each one a failure with a name in this repo.
3. ~~**No settings UI for effort, permissions or approvals.**~~ **Shipped** —
   the Model screen carries the effort control and *Acting without asking*, and
   the approval card offers the grant where the user learns they want one.
4. **`tools.py` sits at 65% coverage** — the lowest in the agent layer, and the
   part that touches the real world.
5. ~~**Delegation has no budget ceiling across a chain.**~~ **Shipped** — one
   `ledger` in `runtime.py`, shared down the chain, so three agents at High
   spend one budget between them rather than three.
6. ~~**The plan is advisory.**~~ **Shipped 2026-09-25** — a turn ending with
   steps its own plan never ticked off is told so once, and must either finish
   them or say plainly which it skipped. Once, deliberately: a genuinely
   impossible step would drive a second nudge forever.
   `planning.unfinished_prompt`.

**Where the remaining cost goes.** The three things that used to make a turn
quietly expensive are now measured rather than assumed, and each has a case on
the scorecard:

* the stable prefix is **cached** rather than re-billed once per round
  (`models/caching.py`);
* a reasoning model is actually **asked to reason**, banded off the same effort
  selector (`models/reasoning.py`);
* a reply is no longer **capped at 1,500 tokens** — under a page, which a plan
  with its reasoning did not fit in.
