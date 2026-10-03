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
  **The refusal names a screen and never a tool.** `NEEDS_PERMISSION` is the
  whole of what the model knows about the problem, so what it omits the user
  never hears. It shipped saying only "ask them for it", and an agent wrote
  "reading it (`calendar_lookup`) is still blocked. Please grant Google
  Calendar read access" — an internal name the user has never seen, and no
  control to go and find. It now names Settings → Agents & tools and forbids
  printing a tool name.
- A routine pre-authorises the routine, not the stranger who wrote the email it
  read. Outbound actions need a recipient on the explicit allow-list; everything
  else queues for one tap. Interactive chat is deliberately not gated.
- **A built-in tool declares its own tier too, in the same vocabulary.**
  `tool_facts._FACTS` carries a `verb:resource` capability per tool — the one
  `connectors/capability.py` defines and every connector and action already
  uses — and `tool_access` derives the tier from the verb rather than storing
  it. Two tables would be a gate giving different answers about the same act.
  **Unknown fails closed**: a tool that declares nothing is DESTRUCTIVE, not
  READ, because the tempting default files the unclassified thing as harmless.
  `execute:process` is its own tier for that reason — `run_python` is unbounded,
  so there is no inverse to describe, and filing it as a write would let it ride
  along on "let it change things".
- **The permission screen is derived, not arranged — and it asks a different
  question from the gate.** Crossing what a tool touches with what it does
  gives four *groups* and a switch per tier inside each: six decisions instead
  of sixty-four. That is `tool_group`, and it is the **gate's** axis —
  `request_permission`, `prompt._withheld` and `tool_snapshot` all reason in
  it. It is the wrong axis for a settings screen, because four reach classes
  put Gmail, the calendar, Telegram, Notion and Linear in one group and nobody
  can say *read GitHub, leave my mail alone*. So `tool_app` asks the same
  resource a second question — which app — and `permission_apps()` derives the
  cards from it, each carrying the word its own changes actually are. One
  table, one derivation, two questions; a second taxonomy is the drift this
  file keeps warning about, and `tests/test_tool_permissions.py` fails if a
  built-in lands on no card or in a tier that disagrees with what it declared.
  The group that cannot leave this machine is granted at creation and is not a
  switch at all — not in the card and not in the list folded under it: an agent
  that cannot read its own memory is not a safer agent, and ten boxes to get
  there teaches somebody the screen is a formality before they reach the boxes
  that matter.
  **`tool_facts` is a leaf and must stay one.** `prompt.py` reads it, and
  importing it from `tools.py` grew the frozen `agents_tools` cycle from five
  modules to ten the first time it was tried.
- **A stored tool list is a choice made from a menu, and `tool_snapshot` stores
  the menu with it.** Without that, a capability shipped later is
  indistinguishable from one the user turned down, so nothing could ever reach
  an agent again — six agents on one machine, one browser, and it was the only
  agent never edited. A tool that was not on the recorded menu is *undecided*
  and takes the default for its bucket; one that was on it and left out stays
  out. Both stores go through the same two helpers, or the column means one
  thing in `tool_overrides.py` and another in `custom.py`.
- **An agent is told what it has NOT been given.** A withheld tool is simply
  absent from its list, so it cannot tell "never granted" from "does not exist"
  — asked whether it could post to a site, one answered *"You haven't enabled
  browser access for this agent yet. To turn it on: Settings → Agents & tools →
  Social Media Manager"*, a path nothing had told it. `prompt._withheld` names
  the **groups** it lacks, says they exist and are the user's to grant, and
  gives the real route. Groups rather than tool names, for `NEEDS_PERMISSION`'s
  reason — and one sentence in the cached prefix rather than thirty tool
  schemas per turn for capabilities it cannot use.
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
- **An agent's prose lives in two files, and `profile_files.py` is the only
  thing that touches them.** `persona.md` is its instructions and `memory.md`
  is what it has learned about doing this job for this user. Both are
  **overrides**, the shape `tool_overrides` already uses: no file means the
  shipped preset, so reset-to-default is deleting one and a later release can
  still improve an agent the user has edited. A custom agent is born with its
  `persona.md` written, so the `custom_agents.system_prompt` column only ever
  answers for agents that predate it — **one live copy of one piece of prose**.
  Three things are load-bearing and each is a test: the file name is matched
  against a closed set and **never sanitised** (a cleaned-up name out of a URL
  is a traversal with extra steps, and `secrets.json` is two directories up);
  **`None` is not `""`** (no file means the shipped default, an empty file
  means the user cleared it, and collapsing them restores a preset somebody
  deliberately emptied); and **deleting an agent deletes its directory**,
  because ids are slugs and the collision is therefore deterministic — notes
  are prose the next agent with that name would act on.
- **`memory.md` fills itself, on the call that already ran.** The post-turn
  learner asks for `facts` and `notes` together and routes three ways — brain,
  this agent, or nowhere, which is most turns. **No second model call**, and no
  heuristic fallback for notes: a fact guessed from a regex is a sentence the
  user wrote and the brain can supersede it; a standing instruction guessed
  from a regex is a rule followed every turn, derived from nothing. The gate
  grew a second cue because `_DISCLOSURE` only matches somebody talking about
  *themselves* — *"never reply to recruiters"* matched nothing, so no call was
  made and the instruction was dropped rather than filed. **Every exclusion in
  the prompt is repeated in `notes.rejected`**, because a prompt is not a
  guarantee and the two worth paying twice for are a credential and a reading.
- **A note the user deleted is never relearned**, and it is derived rather than
  declared: `agent_note_writes` records what was written, and a note that was
  written before and is not in the file now was taken out. A hand-edit leaves
  the same evidence a delete button would, which is why there is no button.
  A full file **refuses** a new note rather than evicting one — the note that
  would go is a standing instruction somebody is relying on.
- **`memory.md` rides in the system message, so it is capped.** It is the same
  on every round of a turn, which is why it belongs in the cached prefix rather
  than beside recall — and why an uncapped one is a turn that costs more every
  time it runs. The block is **absent, never blank**: an agent shown an empty
  heading fills it. It says it is not facts about the user (those are the
  brain's, shared and versioned), that the user can edit or delete any of it,
  and that **a live instruction outranks a written note** — without that last
  sentence the model has two instructions and no rule for choosing.

Providers and entitlements belong to `../models/`; recall order belongs to
`../brain/`. Rules: [`/CLAUDE.md`](../../CLAUDE.md).
