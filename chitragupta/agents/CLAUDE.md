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
- **The plan is checked, not just echoed.** A turn that ends with steps its own
  plan never ticked off is told so **once** and must either finish them or name
  them. Once is the design: a genuinely impossible step — a connector that
  refused, a page that never loaded — would drive a second nudge forever. The
  closing round is offered tools, because the useful outcome is that it finishes
  the work rather than apologises more precisely for skipping it.
- **A reply is sized by the effort profile, not by a constant.** It was 1,500
  tokens for every level, which is under a page: a drafted email fitted, an
  eight-step plan with its reasoning was cut mid-sentence, and the loop carried
  the severed half into the next round as though it were a finished thought.
- **`edit_file` is preferred over `write_file` on a file that already exists.**
  Rewriting a whole file from the model's memory of it is billed twice and is
  lossy in a way nothing reports: a row goes missing from a 900-line CSV and the
  reply still says "updated". An ambiguous or absent anchor is refused rather
  than applied somewhere.
- **`run_python` can import exactly `ALLOWED_PACKAGES`, and nothing else.**
  `-S` is the isolation and it also removed the arithmetic the tool exists for,
  so numpy is linked in by name into a scratch directory — never by putting
  site-packages back on the path, which would hand a snippet every dependency
  the app has, including the ones that know where the credentials live.
- Every new capability gets a case in `evaluation.py`.

Providers and entitlements belong to `../models/`; recall order belongs to
`../brain/`. Rules: [`/CLAUDE.md`](../../CLAUDE.md).
