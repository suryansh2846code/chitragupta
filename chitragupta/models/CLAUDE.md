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

Adding a provider means registering an `AuthFlow` and its capabilities, not
adding a route or a UI branch.
