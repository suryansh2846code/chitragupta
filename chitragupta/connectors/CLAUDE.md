# `chitragupta/connectors/` — the sources

One class per source, registered in `__init__.py::REGISTRY`.

- **A source is reached ONE way.** Where the vendor ships an MCP server that is
  the route — it feeds the brain *and* acts, over OAuth, with the vendor's own
  schema. A connector exists where nothing else can reach: the eight on-device
  sources. Offering both is how an agent writes down the route the user did not
  set up, which is exactly what it did. `notion`, `linear` and `github` have
  stood down; each keeps syncing for whoever has it configured and none of
  them can write any more.
  [`docs/REACHING-AN-APP.md`](../../docs/REACHING-AN-APP.md)
- **The pairing is declared at both ends, and one function decides it.**
  `Connector.prefer_mcp` names the server that supersedes a built-in;
  `CatalogEntry.same_as` names the built-in an entry duplicates. Declaring only
  one end is how GitHub sat CONNECTED on the Connectors screen while **Add a
  connector** offered to connect GitHub — so a catalog id that is already a
  connector name must declare `same_as`, and `tests/test_one_way_to_connect.py`
  fails if it does not.
- Sync idempotently, redact secrets on ingest, survive a crash without taking the
  whole sync down, and stay cancellable.
- **A connector's writes are named methods, never part of `sync()`.** Sync only
  ingests — it runs on a timer, so anything it could change would change without
  anyone asking. A write (`send_email`, `modify_messages`, `send`) is a separate
  method reachable only through a confirmed action in `actions.py::REGISTRY`.
  `mcp_source.py` is the same rule for somebody else's server, and `perform()`'s
  `confirmed` gate is required rather than defaulted, so a caller that forgets
  it fails closed. *Confirmed* now includes a standing per-`server:tool` grant
  the user made deliberately — except for a verb `is_irreversible()` recognises,
  which asks every time and cannot be granted in advance.
  [`ACTION-COVERAGE.md § Per-tool grants`](../../docs/ACTION-COVERAGE.md)
- **A connector that carries conversations implements three methods** — `chats`,
  `history`, `send` — and `../messaging.py` finds it by duck typing. `history`
  returns **oldest first**; every chat API returns the opposite, and a
  conversation read backwards is answered backwards. Which apps are reachable at
  all: [`docs/MESSAGING.md`](../../docs/MESSAGING.md).
- **A connector that measures writes to `../metrics.py`, never to the brain.**
  Apple Health is tens of thousands of readings; as memories they would cost
  every agent a second of recall per turn, forever. Dedup is the unique index on
  (metric, at, source), so pointing at a fresh export upserts rather than
  doubling a year of data.
- **A tool is a write unless it proves otherwise.** `_is_write()` needs evidence
  to call something a read — the server's own `readOnlyHint`, or a recognised
  read verb. It ran the other way once, and handed `merge_pull_request` to a
  model as a tool it could call unattended.
- **A server is local or remote, and remote is not a broker.** `transport="http"`
  reaches the vendor's own endpoint with the user's own credential; nothing sits
  in between, and no third-party code runs as the user. OAuth uses Dynamic
  Client Registration, so we still register no OAuth client.
- **Nothing secret goes in `mcp_servers.json`.** That file is returned verbatim
  by `GET /api/connectors`. Values live in the Keychain under
  `secret_key(server_id, var)`; the spec carries names only.
- **A probe never opens a browser.** `auth_provider(..., interactive=False)` is
  the default because the Connectors page probes on load and the scheduler syncs
  on a timer. Only an explicit Connect may take over the user's screen.
- `converse()` bounds the whole exchange, not just the reply — a server that
  hangs while starting never reaches a call, and one wedged server otherwise
  holds a slot in the six-wide probe lane forever.
- **A model is told what an argument ACCEPTS, not only that it exists.** An
  argument list of bare names is an invitation to remember a vendor's public
  API: handed `command*`, a model filled in `update_attributes` for a Notion
  tool whose `command` is one of exactly six words, and the user approved a
  card that could never have worked. Closed sets are spelled out in the prompt
  (`_argument_names`) and checked before the call (`argument_problem`) — both
  from the schema the server published, and narrowly: a missing required
  argument and a value outside an enum, nothing else. A home-grown validator
  would start refusing calls that would have worked.
- **A count is not a capability.** `tools/list` returns a flat array — 45 for
  GitHub, 45 for Notion — and a screen built straight on it says "this
  connector has 45 tools", which nobody can consent to. `mcp_manifest.py` turns
  that array into the sentence a person is actually deciding about: *"GitHub
  reads 26 things, changes 16, 2 need care."* Three rules make it honest —
  irreversible verbs are their own tier rather than louder writes, a server's
  own furniture is not counted as things about the user, and a ceiling this app
  imposes is labelled as ours. There is deliberately **no scopes field**: MCP
  standardises none, what the vendor granted lives on their consent screen, and
  "unknown" in a field nobody can fill is worse than not asking.
- `mcp_tools.py` exposes a server's **read** tools to the agent loop and
  `write_tools()` the proposable ones. Listing starts every server, so it is
  TTL-cached; results are bounded and say so.
- **A bounded list must be shared fairly and say what it dropped.** The prompt
  took `write_tools()[:20]`; Notion sorts first and publishes eighteen writes,
  so sixteen of GitHub's eighteen never reached the model — and since the block
  says *"use ONLY the arguments listed"*, an unlisted tool is one the agent
  reports as impossible. It told a user its GitHub access was read-only. The
  budget is now round-robin across servers and names what it left out, which
  turns "it cannot" into "I cannot see it".
- Never read another product's app-support directory for credentials or models.

Rules: [`/CLAUDE.md`](../../CLAUDE.md) · [`docs/CONNECTORS.md`](../../docs/CONNECTORS.md)
· contract: [`docs/development/mcp-contract.md`](../../docs/development/mcp-contract.md).
