# Which agent can reach which tools — the contract

> Written before the code, per `/CLAUDE.md` ("a change that spans two layers").
> The frontend half is a separate session working from this document, so every
> field is named here, and **ids are named separately from display strings**.
>
> One layer up from [`mcp-contract.md`](mcp-contract.md): that document settled
> what a connector promises the agent layer. This one settles what an *agent*
> has been granted out of it, and how that changes.

## Why this landed

A user connected Notion — remote MCP, signed in, twenty-five working tools —
and asked their agent to summarise a page. It answered out of Notion's
*notification emails* and said the connector was probably still syncing. The
trace was one step: `search_brain`.

Nothing was broken. The agent's stored tools were:

```
["search_brain","remember","list_entities","web_search",
 "add_task","list_tasks","complete_task","gmail_search"]
```

No `mcp` sentinel, so no connector access. It was built before Notion was
connected, and `agents/mcp_tools.py::describe()` returns `[]` when nothing is
connected — deliberately, because a control that cannot do anything reads as a
broken app. So the option did not exist to tick at build time, and **nothing
ever told anyone to go back**.

Four things were missing, and each is a section below:

1. nowhere showed which agent could reach which tools;
2. a custom agent's tools could not be changed at all;
3. a new custom agent defaulted to five tools, connectors not among them, while
   every preset got them;
4. adding a connector granted nothing to agents that already existed, and said
   so to nobody.

---

## 1. Two decisions, made deliberately

### Every agent is editable — a change is recorded as an override

**This reverses the first answer written here, and the reversal is the
interesting part.** The original decision was that presets are *not* editable,
because making them editable needs an overrides table and that table has to
answer a hard question: when a template gains a tool in a later version, does a
stale override withhold it?

A parallel session building the frontend answered it, and answered it better.
Recording the change as an **override** rather than an edit of the original
means:

* the preset stays a code constant, so a later release that improves a preset is
  not permanently blocked by the fact that somebody once touched it;
* reset-to-default is deleting a row, rather than needing to remember what the
  default used to be;
* one table covers presets and custom agents, so `get_agent()` applies it at one
  seam instead of each caller knowing which kind it holds.

The stale-override question is real and is not dissolved — an agent with an
override does not automatically pick up a tool its template gains later. It is
made *recoverable* instead of permanent, which is the part that matters: the
default is still there, the UI can show that the two differ, and one tap
restores it. That is a better trade than refusing to let anyone change anything.

So there is no `editable` flag. Every agent can be changed, and the payload
instead reports what is genuinely useful under this design:

| field | meaning |
|---|---|
| `custom` | did the user build it, or does it ship with Chitragupta — which is about whether *deleting* it makes sense, not whether it can be changed |
| `overridden` | has the user changed this agent's tools from its default |
| `default_tools` | what it would have if they never had, so "reset" and "what did I change" are both answerable |

Storage and the write path are `agents/tool_overrides.py`, which distinguishes
`None` (untouched, fall back to the default) from `[]` (an agent the user
deliberately stripped of everything) — collapsing those two would silently undo
the second.

### New custom agents **do** get connector access

Arguments against, taken seriously: connector access reaches third-party data,
and a default is a permission nobody chose.

Arguments for, which win:

- **A preset gets it.** A custom agent is an agent. Two different defaults for
  the same capability is the kind of accident this document exists to prevent.
- **The sentinel is a standing grant, not a live one.** It resolves to whatever
  is connected *at the time of the turn*. Granting it while nothing is connected
  costs nothing and means the agent works the day a connector is added — which
  is exactly the failure above, in advance.
- **It is visible at the moment it is set.** The builder shows the tool list
  when the agent is created. A default the user can see and untick is a default
  they chose; the one to avoid is the one nobody is shown.

New custom agents therefore default to `library.BASE_TOOLS` — the same base
every preset gets, from the same constant.

**This changes nothing for agents that already exist.** See §5.

---

## 2. `GET /api/agents/{agent_id}/tools`

What this agent has, what it could have, and for anything it cannot have, why.

```jsonc
{
  "agent_id": "chotu",            // id
  "agent_name": "chotu",          // display
  "custom": true,                 // user-built, vs ships with Chitragupta
  "overridden": false,            // has the user changed its tools
  "default_tools": ["…"],         // ids — what it has if they never had
  "reaches_connectors": false,    // does it have the sentinel, or any connector tool
  "connector_tool_count": 25,     // readable connector tools existing right now
  "tools": [ /* rows, see below */ ]
}
```

### A tool row

```jsonc
{
  "name": "notion_search",   // id — the exact value stored in the agent's list
  "label": "search",         // display — never render `name`
  "description": "…",        // display
  "source": "mcp",           // id: "builtin" | "category" | "mcp"
  "connector": "Notion",     // display label; "" for builtin and category
  "state": "granted",        // "granted" | "available" | "unavailable"
  "via": "category",         // "direct" | "category" | ""
  "reason": ""               // display; see below
}
```

| field | promise |
|---|---|
| `name` | The stored id. **Never rendered.** `source: "mcp"` carries the protocol's acronym and `tests/test_connector_catalog_ui.py` pins that it never reaches the screen — that pin covers this endpoint too. |
| `label` | The only part a person reads. For the sentinel it is *"Everything my connectors can read"*, never `mcp`. |
| `state` | `granted` — the agent has it. `available` — it could be granted. `unavailable` — it cannot be used right now, and `reason` says why. |
| `via` | How a `granted` row is granted. `direct` — `name` is in the stored list. `category` — it resolves through the sentinel and is **not** in the stored list, so unticking it is not a thing the UI can do; unticking the sentinel is. `""` when not granted. |
| `reason` | A sentence for a person. Set when `state` is `unavailable`, and when a `granted` row needs explaining (a sentinel with nothing behind it yet). |

### The sentinel row is always present here

`GET /api/agents/tools` — the global catalogue — omits the sentinel when no
connector is connected, because that endpoint answers *"what can be added right
now"* and offering an empty category is offering nothing.

This endpoint answers a different question: *"what does this agent have, and
what could it have"*. A standing grant with nothing behind it yet is a real
answer to that, and hiding it is what produced the bug at the top of this page.
So the row is always here, and when nothing is connected it says so:

```jsonc
{ "name": "mcp", "label": "Everything my connectors can read",
  "source": "category", "state": "available", "via": "",
  "reason": "No connectors are set up yet. Granting this now covers them as soon as there are." }
```

The two endpoints differ on purpose. Both are honest about their own question.

### A connector that is signed out appears where its tools would

A connector whose server will not answer contributes no tools — `_readable()`
swallows the failure so a broken connector cannot stop an agent answering. That
is right for a turn and wrong for this view, which would silently show nothing
where twenty-five tools used to be.

So each configured connector that is **currently contributing no tools** gets
one row, in the `tools` array, where its tools would have been:

```jsonc
{ "name": "", "label": "Notion", "description": "",
  "source": "mcp", "connector": "Notion",
  "state": "unavailable", "via": "",
  "reason": "Notion is signed out — reconnect it under Connectors." }
```

`name` is empty because **there is nothing to grant** — that is the point of the
row, not an omission. A consumer grouping rows by `connector` renders it in the
place the missing tools would have occupied, which is the only place it means
anything.

Cost: a connector that *is* working produces tools and is never probed. Only the
ones already failing cost a probe, which is exactly when the reason is needed.
The handler is on the `@probes_a_provider` lane for that reason.

---

## 3. `PATCH /api/agents/{agent_id}/tools`

The same path as §2, which is the point: one resource, read with `GET` and
written with `PATCH`, rather than a read here and a write somewhere else.

```jsonc
{ "tools": ["search_brain", "mcp"] }   // the WHOLE list, not a delta
```

* **The whole list, never a delta.** A delta needs client and server to agree on
  what the list was a moment ago, and the screen sending this can have been open
  while a connector was added. Last writer wins is what a person expects from a
  row of switches.
* **Works for any agent**, preset or custom. `404` only when the id is nothing.
* **The names are not validated against the live tool catalogue**, deliberately.
  A connector that is signed out drops its tools out of the catalogue, so
  validating here would strip every Notion tool from an agent the moment Notion
  went down — turning an outage into a permanent edit. Resolving names to real
  tools is `build_tools()`'s job and it already ignores what it cannot find.
* Nothing is recreated, so the chat history (`agent_messages`) and the model
  binding (`agent_model_configs`), both keyed by `agent_id`, are untouched.
* Returns the agent as it now is: `{id, name, tools}`.

---

## 3b. Setting the whole lot in one press

Two controls do this, and they answer different questions. **Allow all** on a
group heading is one group; the **presets** above the groups are every group at
once. Both exist because six decisions beats sixty-four and is still six, and
the common answer is not six decisions — it is *"this one is mine, let it do
everything"* or *"let it look and nothing else"*.

### A preset is sent by NAME, and resolved at the moment it is applied

```jsonc
PATCH /api/agents/chotu/tools   { "preset": "all" }
POST  /api/agents/custom        { "name": "Sales", "preset": "all",
                                  "tools": ["mcp", "notion__search"] }
```

* **The built-ins come from the key, never from a list on the wire.** "Allow
  everything" has to mean everything *now*; a screen left open while a tool
  shipped would otherwise send its own stale idea of the word and quietly
  withhold the new one — the failure `agents/grants.py` already records for
  connectors, where the option did not exist to tick at build time and nothing
  ever told anyone to go back.
* **Everything that is not a built-in survives.** No preset can name a
  connector tool: they do not exist until the user adds one. `_preset_merge` in
  `api/routes/agents.py` is the one place that merge happens, used by both
  endpoints — two copies of it is how one of them ends up being an "allow
  everything" button that silently drops Notion.
* The two differ only in **where the rest comes from**: `PATCH` keeps what the
  agent already holds, and `POST` takes it from `tools`, because there is no
  agent yet and only the screen knows which connectors exist.
* An unrecognised key is a **400**, on both. It arrives from a client, and
  returning "everything" for a typo is the worst possible failure mode for this
  particular control.

### `GET /api/agents/tools` carries a preview, and it is only a preview

```jsonc
"presets": [
  { "key": "all", "label": "Allow everything", "blurb": "…",
    "tools": ["add_task", "browse_click", …] }   // PREVIEW. Not what is sent.
]
```

The agent **builder** is a screen with no agent behind it: nothing to PATCH, and
nothing to read a saved answer back from. `tools` is what lets it show what the
button will do to the draft on screen. What Create sends is still the key, so a
stale screen shows yesterday's preview and grants today's definition — which is
the right way round.

### A group says where the other half of its permission is set

```jsonc
{ "key": "websites", "label": "Websites and the browser", "blurb": "…",
  "more": { "screen": "connectors", "label": "Choose which sites" } }
```

Turning *change websites* on grants an agent nothing by itself: **which** sites
it may touch is a per-site list on the Connectors screen. A user who turned
every switch on here and was still refused had no way to learn that from this
panel — and an agent asked about it invented a Settings path, because the screen
it was pointed at did not contain the control it described.

`screen` is an id the frontend resolves to its own opener, and one it does not
recognise draws no button — so naming a new destination here can never produce a
control that goes nowhere. `more` is absent when there is nothing else to open.

---

## 4. `GET /api/agents` gains one field

```jsonc
{ "id": "chotu", …, "reaches_connectors": false }
```

One boolean, so a list of agents can show which ones cannot reach the
connectors the user has — without the caller re-deriving it from `tools` and the
sentinel, which is a rule that would then exist in two places.

---

## 5. `GET /api/agents/connector-gaps`

The answer to *"I just connected something — who cannot use it?"*

```jsonc
{
  "connectors": ["Notion", "Linear"],   // display labels, currently readable
  "tool_count": 25,
  "agents": [                            // only those that cannot reach any of it
    { "agent_id": "chotu", "agent_name": "chotu", "custom": true }
  ]
}
```

`agents: []` means there is nothing to say and the caller should say nothing.

**This endpoint reads. It never grants.** Connector access is a permission the
user sets, and an agent that already exists was configured by somebody who did
not tick this box — quietly ticking it later because we added a feature is
exactly the kind of change `/CLAUDE.md` means by *"never surface an internal"*
turned inside out: it would be surfacing nothing while changing something.

So: surfacing the gap is in scope, closing it without asking is not. The caller
shows the list and a way to act on it; each act goes through §3.

---

## 6. Where each promise lives

| layer | owns |
|---|---|
| `agents/library.py` | `BASE_TOOLS` — the one definition of what every agent starts with, preset or custom |
| `agents/grants.py` | the per-agent view: states, reasons, and which connectors are contributing nothing |
| `agents/tool_overrides.py` | what the user changed, as an override; `None` ≠ `[]` |
| `agents/presets.py` | applies the override at one seam, for both kinds of agent |
| `api/routes/agents.py` | the three endpoints, thin over the above |

`agents/grants.py` is new and deliberately not in `tools.py`: `describe_tools()`
answers "what exists", this answers "what does *this agent* have", and the
second is not a filter over the first — it needs the agent, the sentinel's
resolution, and the connector's health.
