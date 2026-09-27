"""One agent asking another.

The README calls this "four agents, one brain", and until now the second half
was true and the first was not: Inbox, Launch, Research and Personal shared a
database and never spoke. Four separate chats is not a team, and the user was
left doing the routing by hand — reading an answer in one tab and pasting it
into another.

Delegation is the whole feature and also the whole risk, because an agent that
can call an agent can call itself. Three guards, all enforced here rather than
trusted to a prompt:

* **Depth.** From the effort profile: none at Low, one hop at Medium, two at
  High. A sub-agent's budget is its parent's halved, so a chain cannot multiply
  a single question into dozens of model calls.
* **Cycles.** An agent already somewhere in the current chain cannot be asked
  again. Without this, Inbox → Research → Inbox is a loop that ends only when
  the budget runs out, having spent it learning nothing.
* **Context propagation.** The chain lives in a `ContextVar`, and tool calls run
  in a thread pool — which does *not* copy context by default. Missing that
  would silently reset the depth counter to zero inside every parallel call,
  turning both guards above into decoration.
"""
from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass
from typing import Any

from ..log import get_logger
from .effort import Effort, get_effort

log = get_logger(__name__)


@dataclass
class Spend:
    """What a turn has spent, shared by everyone working on it.

    Mutable and held by reference on the frozen `Chain`, which is the point: a
    sub-agent gets the same ledger rather than a copy, so three agents at High
    cannot each spend a full allowance. Depth and per-agent step budgets bound
    the shape of a chain; this is what bounds its cost.
    """

    limit: int = 0
    used: int = 0

    def add(self, tokens: int) -> None:
        self.used += max(0, int(tokens or 0))

    @property
    def exhausted(self) -> bool:
        return self.limit > 0 and self.used >= self.limit

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used) if self.limit > 0 else 0


@dataclass(frozen=True)
class Chain:
    """Who is currently asking whom, on what budget, and until when."""

    agents: tuple[str, ...] = ()
    effort: Effort | None = None
    #: The parent turn's stop event, carried down the chain. Without it, Stop
    #: ends the agent the user is talking to and leaves the one it delegated to
    #: running — which is the same lie one level further in.
    cancel: threading.Event | None = None
    #: The shared token ledger for this turn. Inherited, never re-created, so a
    #: delegated question spends the parent's remainder.
    spend: Spend | None = None

    @property
    def depth(self) -> int:
        """Hops taken. The first agent is the user's, so it is depth 0."""
        return max(0, len(self.agents) - 1)


#: `None` rather than an empty `Chain`: a ContextVar default is created once at
#: import and shared by every context that never sets it, so a default must not
#: be something anyone could mutate. `Chain` is frozen today; the indirection
#: below means it stays safe even if that changes.
_CHAIN: contextvars.ContextVar[Chain | None] = contextvars.ContextVar(
    "chitragupta_agent_chain", default=None)

_EMPTY = Chain()


def _named_agent(named: str):
    """Which agent a model meant, through the one matcher."""
    from ..core.naming import resolve
    from . import roster

    return resolve(named, roster.all_agents(),
                   key=lambda a: (a.id, a.name), label=lambda a: a.name,
                   what="agent")


def current_chain() -> Chain:
    return _CHAIN.get() or _EMPTY


def enter(agent_id: str, effort: Effort,
          cancel: threading.Event | None = None):
    """Record that `agent_id` is now running. Returns a token for `leave`."""
    chain = current_chain()
    # The first agent opens the ledger; everyone below it inherits the same one,
    # so a delegated question spends the parent's remainder rather than a fresh
    # allowance of its own.
    spend = chain.spend or Spend(limit=effort.max_tokens_per_turn)
    return _CHAIN.set(Chain(agents=(*chain.agents, agent_id), effort=effort,
                            cancel=cancel if cancel is not None else chain.cancel,
                            spend=spend))


def leave(token) -> None:
    _CHAIN.reset(token)


def refusal(agent_id: str) -> str | None:
    """Why this agent may not be asked right now, or None if it may.

    Returns prose rather than raising: the caller is a model, and a sentence it
    can act on ("you are already inside that agent") produces a better next move
    than an exception the loop has to translate.
    """
    from . import roster

    chain = current_chain()
    effort = chain.effort or get_effort()

    # Exact first, which is the overwhelmingly common case and the one every
    # caller already relied on. Only when that misses is the name matched the
    # way every other place does it: `Research`, `research` and `reserach` are
    # one agent asked for three ways, and refusing the third cost a turn and
    # taught the user nothing. Still refused: a name matching nothing, and one
    # matching two things equally.
    try:
        roster.get_agent(agent_id)
    except KeyError:
        found = _named_agent(agent_id)
        if not found:
            return found.problem[0].upper() + found.problem[1:] + "."
        agent_id = str(found.value.id)

    if effort.max_delegation_depth <= 0:
        return ("Asking other agents is switched off at the current effort "
                "level. Answer using your own tools.")
    if chain.depth >= effort.max_delegation_depth:
        return (f"You have already passed this question through "
                f"{chain.depth + 1} agents, which is the limit. Answer with "
                "what you have.")
    if chain.spend is not None and chain.spend.exhausted:
        return ("This turn has spent its budget, so there is nothing left to "
                "pass on. Answer with what you have.")
    if agent_id in chain.agents:
        return (f"'{agent_id}' is already working on this question further up "
                "the chain — asking it again would loop. Answer yourself.")
    return None


def available_agents(exclude: str | None = None) -> list[str]:
    from . import roster

    return [a.id for a in roster.all_agents() if a.id != exclude]


def roster(exclude: str | None = None) -> str:
    """A one-line description of each agent, for the tool's description."""
    from . import roster as _roster

    return "; ".join(
        f"{a.id} ({a.role})" for a in _roster.all_agents() if a.id != exclude)


def run_sub_turn(agent_id: str, question: str, **kwargs: Any) -> Any:
    """Run one delegated turn, or return None if the loop is not loaded.

    Through `entry`, not `from .runtime import run_turn`: the recursion is the
    design, the import was the cycle. The guards above — who may be asked, how
    deep, on whose budget — stay here, which is why this wrapper exists rather
    than callers reaching `entry` directly.
    """
    from . import entry

    return entry.run(agent_id, question, **kwargs)
