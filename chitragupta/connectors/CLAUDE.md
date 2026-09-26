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
- **A connector says what it can do, in verbs on resources.** `capabilities =
  caps("read:email", "send:email")` — `capability.py`. Not app names: the gate
  has to reason about `send:email` without knowing whether Gmail, Apple Mail or
  somebody else's server is behind it. **Unknown fails closed**, because the
  tempting default reads an unrecognised write as a harmless read. An action
  may be *stricter* than its capability implies and never weaker, which
  `tests/connectors/test_capability_floor.py` is the whole of.
- **The shared machinery is not optional and not per connector.** Rate limits
  and lanes (`limits.py`), bounded retries that only retry what could work
  (`retry.py`), one error taxonomy with secrets scrubbed on construction
  (`errors.py`), paging with loop guards (`pagination.py`), per-page
  checkpoints (`sync_state.py`), stable external identity and tombstones
  (`resources.py`), provenance into ingest (`provenance.py`). `engine.py`
  composes them; a connector writes `fetch(cursor) -> Page` and an `ingest`.
  The reference is `custom_api.py`; the guide is
  [`docs/development/writing-a-connector.md`](../../docs/development/writing-a-connector.md).
- **Checkpoint AFTER the page is committed, never before.** The other order
  loses a page on a crash *and reports success for it* — the records are gone
  and nothing goes back. `base._finish` writes a watermark only for a clean
  pass, which is safe and is why a 2,000-item sync failing at 1,900 used to
  restart at zero; the fix was to make the unit a page, not to relax the rule.
- **A truncated listing never tombstones.** `sweeps_deletions` requires
  `Walk.complete`, or a sync that hit its own budget deletes the tail of a
  mailbox.
- **One account is not one connector.** `connections.py` is one row per
  account, and every checkpoint, identity and provenance record references it.
  `Connector.connection()` mirrors `is_configured()` into that row — the
  credential itself stays in the Keychain, the token file or the vendor
  session, and nothing here holds one.
- **A 401 is a re-auth; a 403 is not.** Signing in again produces the same
  credential with the same permissions, so sending the user round OAuth for a
  scope problem teaches them the app is broken. `ConnectorError.needs_reauth`
  is authentication only, and that is why it is a separate property.
- **A user-described connector cannot be pointed at this Mac.** `outbound.py`
  refuses loopback and link-local, by literal and by resolution, and re-checks
  where a request landed — `urlopen` follows redirects. The rest of the LAN is
  allowed on purpose: a NAS in the user's house is what a local-first app is
  for. The one residual gap is named in
  [`docs/CONNECTOR-PLATFORM.md`](../../docs/CONNECTOR-PLATFORM.md) §7.
- **A listing that carries no bodies is the normal shape, and `hydrate` is why.**
  Gmail's `messages.list` answers with ids; the body is a second request *per
  message*. So the keep-or-skip decision is answered from the id alone, before
  anything is paid for — 602 requests on a first pass, 2 on every pass after it.
  `Plan.hydrate` runs only for records the engine has decided to keep. Drive has
  the same shape and still downloads a file to find out it already had it.
- **Gmail's fingerprint is the message id, because a received email is
  immutable.** Nobody edits mail that arrived, so the id is sufficient proof we
  still have it. Labels are deliberately *not* in the fingerprint: an archived
  email is the same email, and folding them in would re-read the whole mailbox
  every time somebody tidied an inbox.
- **A window is not an enumeration.** `after:` and `newer_than:90d` describe a
  slice, so `gmail` sets `sweeps_deletions=False`. Sweeping a window tombstones
  everything outside it — which for a mailbox is almost all of it.
- **Build a monkeypatch from the fixture, never `pytest.MonkeyPatch()`.** A
  hand-built one is never undone: four of them in the Gmail tests left `urlopen`
  stubbed for the whole session and broke eighteen tests in `test_tool_bridge.py`,
  a file with no connection to connectors at all.
- **Drive's fingerprint is the whole `modifiedTime`, not its date.** The check
  this replaced compared `modifiedTime[:10]`, so a document edited twice in one
  day read as unchanged the second time and the edit never arrived. A file is
  mutable — unlike mail — so the timestamp *is* the change detector.
- **One record can become several memories, and all of them are recorded.**
  Drive chunks a long document. `ingest` may answer with a list, and
  `resources.seen(memories=…)` stores every id — because recording only the
  first is what leaves the rest orphaned when a user asks to delete what a
  source imported.
- **A connector missing from `tests/connectors/harness.py::FAKES` gets none of
  the generic suites.** Drive was absent from it, so nothing exercised its
  `sync()` at all and its coverage sat at 15% while looking tested. Registering
  a fake is what hands a connector the contract, crash-isolation, idempotency
  and redaction tests.
- Never read another product's app-support directory for credentials or models.

Rules: [`/CLAUDE.md`](../../CLAUDE.md) · [`docs/CONNECTORS.md`](../../docs/CONNECTORS.md)
· contract: [`docs/development/mcp-contract.md`](../../docs/development/mcp-contract.md).
