# `chitragupta/connectors/` — the sources

One class per source, registered in `__init__.py::REGISTRY`.

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
- `mcp_tools.py` exposes a server's **read** tools to the agent loop and
  `write_tools()` the proposable ones. Listing starts every server, so it is
  TTL-cached; results are bounded and say so.
- Never read another product's app-support directory for credentials or models.

Rules: [`/CLAUDE.md`](../../CLAUDE.md) · [`docs/CONNECTORS.md`](../../docs/CONNECTORS.md)
· contract: [`docs/development/mcp-contract.md`](../../docs/development/mcp-contract.md).
