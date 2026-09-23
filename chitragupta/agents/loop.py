"""Executing the tools a model asked for — in parallel, and only once each.

The old loop ran five rounds and executed each call one after another. Both
limits cost real answers: five rounds is not enough to look something up, follow
it, and check it; and two searches that have nothing to do with each other
should not cost two round trips to the model's server.

Raising the ceiling introduces a failure the shallow loop never had, though. A
model with twenty rounds available and no new information will happily spend
them re-issuing the same call, each time reading the same answer and concluding
the same thing. So depth needs two companions: a memo of what has already been
run this turn, and a way to notice that a round produced nothing new.
"""
from __future__ import annotations

import contextvars
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..log import get_logger
from ..models.base import ToolCall
from . import cancellation, connector_grants, mcp_tools
from .effort import Effort
from .results import ToolResult, worked
from .tools import run_tool

log = get_logger(__name__)

#: Appended when a model asks for something it already has. Phrased as an
#: instruction rather than an error, because the model is not wrong — it has
#: simply lost track, and what it needs is the answer plus a nudge to move on.
_REPEAT_NOTE = (
    "\n\n[You already ran this exact call earlier in this turn and this is the "
    "same result. Do not call it again — use what you have, or try a different "
    "approach.]"
)


def call_key(name: str, arguments: dict) -> str:
    """A stable identity for a call, so re-asking is recognisable.

    Sorted keys, because `{"query": "x", "limit": 5}` and
    `{"limit": 5, "query": "x"}` are the same request and a model emits either.
    """
    try:
        args = json.dumps(arguments or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):          # pragma: no cover - defensive
        args = repr(arguments)
    return f"{name}::{args}"


@dataclass
class ToolOutcome:
    call: ToolCall
    output: str
    repeated: bool = False
    """True when this exact call was already made this turn."""

    @property
    def ok(self) -> bool:
        """Did the call work? Read from the result, never guessed from its text."""
        return worked(self.output)


#: What a tool reports when the user stopped the turn before it ran. A sentence
#: rather than an empty string, because it is written into the transcript the
#: next round would read — and "" reads as "this tool found nothing".
STOPPED_OUTPUT = ToolResult("Not run — the user stopped this turn.")

#: What a tool says when this agent has not been allowed to reach that
#: connector yet. Phrased as an instruction, because the model's next move is
#: the thing that matters: ask, in the user's terms, and do not go looking for
#: another way round.
NEEDS_PERMISSION = (
    "Needs the user's permission: reading {label}. You have not been allowed to "
    "use it yet. Ask them for it in your reply — say plainly what you wanted it "
    "for. Do not try a different connector instead, and do not answer as though "
    "you had read it."
)


@dataclass
class ToolRunner:
    """Runs a model's tool calls for one turn, remembering what it has run."""

    effort: Effort
    #: Whose permissions apply. Empty means an internal caller with nobody to
    #: ask, which is gated the same way rather than waved through.
    agent_id: str = ""
    #: Set when the user presses Stop. Checked before each call is executed, so
    #: a round of six tools that is stopped after the first does not run the
    #: other five — the expensive half of a stopped turn is usually here.
    cancel: threading.Event | None = None
    #: The turn's *allow once* grants, captured at construction.
    #:
    #: They live in a ContextVar, which is exactly right for the thread pool
    #: below — `copy_context()` carries them — and no help at all to `invoke()`,
    #: which is called from a thread this process did not start. Holding the
    #: value means a user who clicked *Allow once* is still allowed once when
    #: the tool runs inside a vendor CLI's own loop.
    once: frozenset[str] = field(
        default_factory=connector_grants.granted_this_turn)
    _memo: dict[str, str] = field(default_factory=dict, repr=False)
    calls_made: int = 0
    repeats_seen: int = 0

    def run(self, calls: list[ToolCall]) -> list[ToolOutcome]:
        """Execute a round of calls and return their outcomes, in order.

        Order is preserved regardless of which finished first, because each
        result has to be matched back to its `tool_call_id`; a provider given
        them out of order rejects the message.
        """
        outcomes: list[ToolOutcome | None] = [None] * len(calls)
        pending: list[tuple[int, ToolCall, str]] = []
        # Duplicates *within* one round are a separate case from duplicates
        # across rounds: the model issued them together, so it is being wasteful
        # rather than circling. Run the call once and hand both the answer.
        this_round: dict[str, int] = {}
        echoes: list[tuple[int, str]] = []

        for i, call in enumerate(calls):
            key = call_key(call.name, call.arguments)
            if key in self._memo:
                self.repeats_seen += 1
                # `str + str` would drop the verdict and hand the loop a result
                # that claims to have worked — which is the bug this replaced.
                prior = self._memo[key]
                annotated = (prior.but(prior + _REPEAT_NOTE)
                             if isinstance(prior, ToolResult)
                             else ToolResult(prior + _REPEAT_NOTE))
                outcomes[i] = ToolOutcome(call, annotated, True)
            elif key in this_round:
                self.repeats_seen += 1
                echoes.append((i, key))
            else:
                this_round[key] = i
                pending.append((i, call, key))

        # Tools that reach exactly one connector are gated by name in
        # `_blocked`. A tool that reaches whichever app the model named has to
        # ask for itself, and this is how it knows who is asking. Set before
        # the context is copied, so the pool workers inherit it.
        acting = connector_grants.acting_as(self.agent_id)
        try:
            return self._run_pending(calls, outcomes, pending, echoes)
        finally:
            connector_grants.stop_acting(acting)

    def _run_pending(self, calls, outcomes, pending, echoes):
        if pending:
            width = max(1, min(len(pending), self.effort.max_parallel_tools))
            if width == 1 or len(pending) == 1:
                for i, call, key in pending:
                    outcomes[i] = self._execute(call, key)
            else:
                # Tools reach SQLite (WAL, serialized connections) and the
                # network; `sqlite3.threadsafety` is 3 here, so sharing a
                # connection across these threads is safe.
                # A thread pool does NOT copy context. `ask_agent` reads the
                # delegation chain from a ContextVar, so without carrying the
                # context across, every parallel call would start at depth zero
                # and the depth and cycle guards would be decoration.
                #
                # One copy *per call*, not one shared: a `Context` cannot be
                # entered twice at once, so a single copy handed to several
                # workers raises "cannot enter context" the moment two overlap.
                with ThreadPoolExecutor(max_workers=width,
                                        thread_name_prefix="chitragupta-tool") as pool:
                    futures = {
                        pool.submit(contextvars.copy_context().run,
                                    self._execute, call, key): i
                        for i, call, key in pending
                    }
                    for future, i in futures.items():
                        outcomes[i] = future.result()

        for i, key in echoes:
            outcomes[i] = ToolOutcome(calls[i], self._memo.get(key, ToolResult("")), True)

        self.calls_made += len(calls)
        return [o for o in outcomes if o is not None]

    def _blocked(self, name: str) -> ToolResult | None:
        """The permission check, here rather than in a prompt.

        A model told "ask first" will sometimes not, so the rule is enforced
        where the call actually happens — the same reason the outbound
        allow-list is not a sentence in the system prompt.
        """
        connector = connector_grants.connector_of(name)
        if not connector or connector_grants.may_use(self.agent_id, connector):
            return None
        label = (mcp_tools.labels_by_id().get(connector)
                 or connector_grants.first_party_labels().get(connector)
                 or connector)
        return ToolResult.failed(NEEDS_PERMISSION.format(label=label))

    def _execute(self, call: ToolCall, key: str) -> ToolOutcome:
        if cancellation.stopped(self.cancel):
            return ToolOutcome(call, STOPPED_OUTPUT)
        refused = self._blocked(call.name)
        if refused is not None:
            # Not memoised: the answer changes the moment the user allows it,
            # and a cached refusal would outlive the permission being granted.
            return ToolOutcome(call, refused)
        output: ToolResult = run_tool(call.name, call.arguments)
        self._memo[key] = output
        return ToolOutcome(call, output)

    def invoke(self, name: str, arguments: dict) -> str:
        """One tool, called from outside the loop, through every gate the loop
        applies.

        A vendor CLI backend runs its own agentic loop over our tools
        (`models/tool_bridge.py`), so `run()` never sees those calls. Everything
        `run()` enforces has to hold anyway, and the way to make sure of that is
        to go through the same three steps rather than to write them out a
        second time — a permission check that exists in two places is a
        permission check that will one day only be updated in one.

        The difference is the thread. `run()` is entered from the turn's own
        context and hands it to its workers with `copy_context()`; this is
        entered from a socket the CLI opened, where neither ContextVar has ever
        been set. So both are set here, per call, and put back afterwards.
        """
        acting = connector_grants.acting_as(self.agent_id)
        granted = connector_grants.allow_for_this_turn(sorted(self.once))
        try:
            key = call_key(name, arguments)
            prior = self._memo.get(key)
            if prior is not None:
                self.repeats_seen += 1
                return (prior.but(prior + _REPEAT_NOTE)
                        if isinstance(prior, ToolResult)
                        else ToolResult(prior + _REPEAT_NOTE))
            outcome = self._execute(ToolCall(id=f"bridge_{name}", name=name,
                                             arguments=arguments or {}), key)
            self.calls_made += 1
            return outcome.output
        finally:
            connector_grants.reset(granted)
            connector_grants.stop_acting(acting)

    def round_was_all_repeats(self, outcomes: list[ToolOutcome]) -> bool:
        """Did this round learn nothing? The signal that the loop is circling."""
        return bool(outcomes) and all(o.repeated for o in outcomes)


#: How many consecutive no-new-information rounds before the turn is cut short.
#: One is too eager — a model sometimes re-reads a result and then moves on. Two
#: in a row is a model that has stopped making progress.
STALL_LIMIT = 2

#: Given to the model when its budget runs out, instead of dropping the turn.
#: An agent that has looked things up for twenty rounds usually *can* answer; it
#: just has not been asked to stop.
BUDGET_PROMPT = (
    "You have used your tool budget for this turn. Do not call any more tools. "
    "Answer now with what you have found, and say plainly which parts you could "
    "not confirm."
)
