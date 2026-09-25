# `chitragupta/models/` — providers, auth, entitlements

Providers, auth flows, entitlements, discovery, the CLI manager, the error
taxonomy, streaming wire formats.

**Model availability is resolved per user, never hardcoded** — read that section
of [`/CLAUDE.md`](../../CLAUDE.md) before adding a model id anywhere. The short
version: the provider's own answer beats every table we ship, hardcoded lists are
fallbacks flagged `is_fallback=True`, a stored id is re-checked before use, and
**detection is never consent** — finding a CLI on the machine lets us *offer* it
and never marks it connected.

**A backend that takes `tools=` must actually use them.** The three vendor
CLIs accepted the argument and dropped it, so an agent holding fifty-four tools
called none of them and the model answered with the CLI's own — `ToolSearch`,
`SendMessage`, and `Bash` over the user's filesystem that nobody granted.
`tool_bridge.py` hands them over as MCP for the length of one turn: ephemeral
port, bearer token in a 0600 file rather than on the command line, the CLI's own
built-ins turned off with `--tools ""`, and the calls it made stripped off the
result so the loop does not run them again.
[`docs/development/cli-tool-bridge.md`](../../docs/development/cli-tool-bridge.md)

**Two things every provider payload must get right, and both are invisible when
wrong.**

*Caching.* A turn is a loop, and each round re-sends the agent's prompt and every
tool schema unchanged. Uncached, twelve rounds bill that prefix twelve times on
the user's own key. `caching.py` places the four markers Anthropic allows, in its
prefix order (tools → system → messages). **Never mark a block rebuilt per
turn** — `Message.stable` draws the boundary and recall and the task list sit
outside it on purpose. OpenAI and DeepSeek cache automatically and need only a
stable prefix, which is the same discipline.

*Accounting.* Anthropic reports cached tokens **outside** `input_tokens`; OpenAI
reports them **inside** `prompt_tokens`. Add the first, never the second. Get it
backwards and either the turn ledger stops bounding the loop or the whole prefix
is counted twice.

*Reasoning.* `reasoning.py` owns both vendor spellings — a word for OpenAI, a
token budget for Anthropic. **The budget lives in a `ContextVar`, never on the
provider.** `get_provider` is `@lru_cache`d, so one instance answers every
concurrent turn: an attribute there means a turn at Low silently cancels the
thinking of a turn at High already mid-loop, intermittently and under load only.
Same rule, same reason, as the tool-call observer in `tool_bridge`. Enabling Anthropic's also forces `temperature` to 1
and requires the model's **thinking blocks handed back with their signatures**
on every replayed assistant turn, or round two of a tool loop is a 400. A model
we guessed wrong about costs one silent retry, never an error message about a
feature the user never asked for and cannot see.

**This package is a DAG and must stay one.** It was one strongly-connected
component of fourteen modules — every provider plus the catalog, entitlements,
errors and the registry — and `ruff` was clean throughout, because every edge
had been pushed inside a function body. `tests/test_models_layering.py` counts
lazy imports as the real dependencies they are.

The layering, bottom to top. An import that runs *upward* is the bug:

```
errors · base · streaming · capabilities · cache   nothing above them
entitlement_rules                                  pure policy, stdlib only
claude_cli                                         where the CLI is
accounts · connection_state                        what the user has
discovery                                          the catalog, decorated
entitlements                                       choosing a model to run
registry                                           everything
```

The two that will tempt you: a provider needs to re-check a model before
sending it — import `entitlement_rules`, never `entitlements`, which also
detects. And something that changes a credential should call
`cache.credentials_changed()`, never `registry.clear_provider_cache` — the
registry is the top of the package.

Adding a provider means registering an `AuthFlow` and its capabilities, not
adding a route or a UI branch.
