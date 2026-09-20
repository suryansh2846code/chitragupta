# `chitragupta/agents/` — the loop

The turn loop, tools, effort, delegation, planning, grounding, approvals,
permissions — and `library.py`, which is what Chitragupta *offers*.

- **The library is not the roster.** `library.py` holds every agent that ships;
  the roster is the few the user took on, and `list_agents()` returns only
  those. An agent nobody picked is one nobody opens. A template that leaves the
  roster keeps its conversation — removing is not deleting.

- Effort is one gear selector (Low/Medium/High), not a settings screen — it
  derives every budget at once, and it spends the *user's* money. It caps
  **tokens** as well as rounds, on one ledger shared down the delegation chain,
  so three agents spend one budget between them.
- **An agent is only told what it can do.** The system prompt is assembled from
  blocks in `prompt.py`; `Agent.actions` selects the proposal protocols. An
  agent with no actions has no way to claim it sent anything.
- Tool calls in a round run in parallel; results reassemble by `tool_call_id`.
  Delegation guards live in a `ContextVar`, so it is one `copy_context()` **per
  call**.
- Compress old turns, never drop them.
- **Anything the user starts, they can stop.** A turn carries a stop event
  (`cancellation.py`); the loop reads it between rounds, before each tool, and
  while a stream is arriving. A stopped turn keeps the half-written answer and
  spends nothing more — not one wrap-up call, not the post-turn learning — and
  its delegated sub-agents stop with it.
- **Anything we add to the model's input, we take back out before a tool reads
  it.** The date note goes in with `grounding.prefixed` and comes out with
  `grounding.strip_arguments` — a model copies its own input, and a date inside
  `search_brain`'s query is read by recall as a filter.
- **An agent's conversation is its own.** A delegated turn runs `persist=False`
  — no history in, nothing written out, no learning — because the "user" of that
  turn is another agent. The brain stays shared; the chat does not.
- **An agent asks before it reaches a connector**, and the asking is enforced in
  `loop.py`, never in a prompt. This covers built-in connector tools too
  (`list_mail`, `gmail_search`, `calendar_lookup`) via
  `connector_grants.FIRST_PARTY_TOOLS` — without it the gate stopped an agent
  reading a Notion page and let it read the whole inbox. Three ways in: `once`
  (a turn, from `@` or *Allow once*), `always` (per agent+connector), `unrestricted` —
  declared by a template, and only Chief of Staff has it.
  [`connector-permissions.md`](../../docs/development/connector-permissions.md)
- A routine pre-authorises the routine, not the stranger who wrote the email it
  read. Outbound actions need a recipient on the explicit allow-list; everything
  else queues for one tap. Interactive chat is deliberately not gated.
- **An action declares its own tier, in one place.** `actions.ActionSpec.risk`
  is green (reaches nobody — runs unattended), amber (reaches someone — needs a
  permitted recipient) or red (never unattended, never promotable). The tier
  test is not "does this reach somebody" — it is **can the gate SEE what it
  reaches**: red is where no key exists that a person could read and revoke.
  A fourth switch cuts across all three: `always_ask_when(params)` returns a
  sentence for the case that is promotable in general and not *this time*, and
  it is consulted **before** the tier — that is what stops a standing grant
  from covering `delete_project`.
  `NEVER_UNATTENDED`, `OUTBOUND_ACTIONS` and `RECIPIENT_KINDS` are **derived**
  from it; they used to be three hand-kept sets and the one you forget is
  whichever is furthest from the code you are writing. An action nobody
  declared is refused, not allowed.
- **An action is the whole loop.** `verify` reads it back from the service,
  `remember` writes it to the *brain* (not one agent's conversation), `undo` is
  the inverse where one honestly exists — and `None` is the right answer for a
  sent email. Everything lands in `action_log`. See
  [`ACTION-COVERAGE.md`](../../docs/ACTION-COVERAGE.md).
- **Every agent can notice, and every agent can ask for a browser.**
  `_PROACTIVE` (a reminder and a routine) is on all of them: neither reaches
  anybody, and a routine is the same agent later with the same tools — it
  decides *when*, not *what*. `_BROWSE` is in `BASE_TOOLS` for the same reason
  the connector sentinel is: offered to everyone, refused until granted.
  What an agent may *send* is still per-template, and `OUTBOUND_ACTIONS` is the
  list that says which actions reach a person.
- Every new capability gets a case in `evaluation.py`.

Providers and entitlements belong to `../models/`; recall order belongs to
`../brain/`. Rules: [`/CLAUDE.md`](../../CLAUDE.md).
