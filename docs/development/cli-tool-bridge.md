# Giving a vendor CLI our tools

> Written from a screenshot. A Chief of Staff pinned to the Claude subscription
> was asked to check the user's mail and answered: *"No mail tools are actually
> available in this session — the search only surfaces `SendMessage`, not
> `list_mail` or `needs_reply`."*
>
> It was telling the truth, and `SendMessage` is not a tool this app has ever
> had. Companion to [`models.md`](models.md) (providers and auth) and
> [`/docs/AGENTS.md`](../AGENTS.md) (the loop).

## What was wrong

Three provider backends are not APIs. They are the vendor's own CLI run
headless, and Chitragupta reaches them because a user signed in to a
**subscription** rather than pasting an API key:

| provider id | class | reached when |
|---|---|---|
| `claude` / `anthropic` | `AnthropicProvider` | **no API key** → delegates to the CLI |
| `claude-code` | `ClaudeCodeProvider` | always |
| `cursor` | `CursorProvider` | always |
| `xai` (Grok CLI) | `GrokCLIProvider` | always |

Every one of them accepts `tools=` on `chat()` and `stream()`, because that is
the `LLMProvider` interface. Every one of them **dropped it**:

```python
def _stream_command(self, messages, tools):      # `tools` never read again
    cmd = [self._bin, "-p", "--output-format", "stream-json", ...]
```

So `runtime.py` built fifty-four tools for the turn, handed them over, and the
subprocess started with none of them. The model, given no tools of ours, used
the CLI's own — `ToolSearch`, `SendMessage`, `Bash` — found nothing that could
read mail, and said so. Nineteen rounds to discover it.

**Two things were broken at once**, and only one of them was the missing
tools. The other is that a Chief of Staff was holding `Bash`, `Write` and
`Edit` over the user's entire filesystem, because that is what `claude -p`
comes with. Nobody granted that in the agent library. It arrived with the
backend.

## The route in

`claude -p` takes `--mcp-config`. So the tools go over MCP, to a server that
exists for the length of one turn: `models/tool_bridge.py`.

```
runtime.py                          models/                    subprocess
──────────                          ───────                    ──────────
build_tools() ─ 54 Tool objects
      │
      ├─ handler = runner.invoke ──► ToolBridge (127.0.0.1:0)
      │                                   ▲
      └─ provider.stream(tools=…)         │ MCP over HTTP + bearer
              │                           │
              └──────────────────────► claude -p --mcp-config …
                                           runs its own loop,
                                           returns finished text
```

The CLI does the agentic loop. We get text back, which is what this backend
always returned — the difference is that the text is now written by a model
that could actually look things up.

## The four flags, and why each one

Verified against Claude Code **2.1.236**, not assumed.

| flag | without it |
|---|---|
| `--mcp-config <path>` | the tools do not exist |
| `--strict-mcp-config` | the user's **own** terminal MCP servers join the agent's toolset — so what an agent can reach depends on a file this app does not manage |
| `--tools ""` | the CLI's built-ins stay on, and asking an agent to read your mail hands it `Bash` over your filesystem |
| `--allowedTools <a,b,c>` | headless has nobody to ask, so every tool is refused |

Both list flags are variadic, so they are passed **comma-joined**. A variadic
flag followed by `--model` eats it.

`--allowedTools` enumerates every tool rather than wildcarding the server. The
list is the point: a wildcard would keep granting whatever the bridge grows
later.

## The fence

Loopback is not a security boundary — `/CLAUDE.md` says so, and it is why the
API's origin guard compares `Origin` to `Host` rather than checking for
localhost. Every browser the user has open is also "on this machine", and this
server runs `run_python` and `write_file`.

So:

- **A fresh bearer token per turn**, compared in constant time. No token, no
  tools — checked before the handler runs, not after.
- **The token never reaches a command line.** `--mcp-config` takes a JSON
  string too, and a string would put the token in `ps` output for every other
  account on the machine. It goes in a `0600` file, deleted on the way out.
- **An ephemeral port** (`127.0.0.1:0`). Two agents can answer at once; a fixed
  port would make the second fail to bind, or reach the first one's tools.
- **It stops when the turn does.** `finally`, on every path, including the one
  where the subprocess times out.

## Permissions still apply, and the thread is why that took work

`ToolRunner.run()` is what stands between a model and the user's accounts. A
bridged call never reaches it — the call arrives on a socket, in a thread this
process did not start. So the runtime hands the bridge handlers that go through
**`ToolRunner.invoke()`**, which does the same three things `_execute` does:
cancellation, `_blocked`, `run_tool`.

The part that is not a re-export is the ContextVars. Both halves of the
permission state — who is acting (`_ACTING`) and what the user allowed for this
turn (`_ONCE`) — live in ContextVars. That is exactly right for the loop's own
thread pool, which carries them with `copy_context()`, and carries **nothing**
to a socket thread. So:

- `ToolRunner.once` captures the turn's *Allow once* grants at construction, on
  the turn's own thread.
- `invoke()` sets both vars per call and puts them back in a `finally`.

Without this an agent would be refused what the user had just allowed, and
Chief of Staff — whose `unrestricted_connectors` is read against the acting
agent — would be allowed nothing at all.

## Two things that would silently break it

**The CLI's tool calls must not reach our loop.** They arrive in the stream as
`tool_use` blocks, and `streaming.anthropic_events` collects them — correctly,
because on the Messages API path they are work that has not happened yet. Left
on the result, `runtime.py` reads `wants_tools`, takes the CLI's names
(`mcp__chitragupta__list_mail`) and runs every one **a second time**, and the
turn never terminates on the answer the CLI already wrote. `_answered()` strips
them. Verified red against the live CLI before it was written.

**The trace must still show the steps.** Those calls happen in another process,
so the loop never emits them, and a turn that looked six things up would show
the user nothing — which reads as an answer that was made up rather than found.
`runtime._watch` turns each into the same two events and two `TraceStep`s the
loop emits.

The hook reaches the provider through a **ContextVar**
(`tool_bridge.observe`), not an attribute on the provider, and that is not a
style choice: `registry.get_provider` is `@lru_cache`d, so **one instance
answers every concurrent turn**. An attribute would be overwritten by whichever
turn started last, and Chief of Staff's tool calls would be drawn in the Health
agent's trace. Delegation already copies the context per call, so a sub-agent
reports into its own. The bridge reads it once, on the turn's thread, and holds
it — the socket threads never touch the ContextVar.

## Still open

- **Only the Claude CLI is wired.** `cursor.py` and `grok_cli.py` have the same
  hole and each takes different flags; the bridge is provider-agnostic, so
  what is left is one `_bridge_args` per CLI and an end-to-end run against each.
- **No capability flag.** Nothing in `models/` yet says "this backend cannot
  call tools", so a provider that has not been wired still fails the way this
  one did — quietly. `discovery.py` infers `tool_calling` from a model id,
  which is an API-provider question and answers nothing about a CLI.
- **The CLI's own system prompt is still underneath ours.** We append with
  `--append-system-prompt`, so a Chief of Staff is a coding agent that has been
  told to run somebody's day. `--system-prompt` replaces it outright and is
  probably right, but it changes every bridged and unbridged turn on this
  backend and deserves its own change.
