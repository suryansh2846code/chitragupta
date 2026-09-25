"""How hard an agent tries — one setting, chosen by the user.

Every knob that trades quality against cost pulls in the same direction: more
tool steps find more, a model-written summary remembers better than a heuristic
one, deeper delegation answers harder questions, a wider recall grounds better.
Exposing them individually would be a settings screen nobody can reason about,
and leaving them fixed means the same budget for "what's my next meeting" and
"compare these four vendors and draft the email".

So there is one gear selector. The user picks Low, Medium or High; everything
below follows from it.

This matters more here than in a hosted product: the model runs on the user's
own key or their own subscription, so a deeper loop spends *their* money. Low is
a real choice, not a degraded mode — for a small local model it is often the
better one, because a 3B model given 24 steps mostly finds 24 ways to go wrong.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The setting's storage key, in `meta` and in the client's localStorage.
EFFORT_KEY = "agent_effort"
DEFAULT_EFFORT = "medium"


@dataclass(frozen=True)
class Effort:
    """A complete budget for one turn."""

    name: str
    label: str
    description: str

    #: Tool-calling rounds before the turn is cut off. The dominant cost knob:
    #: each round is a full model call carrying the whole conversation.
    max_steps: int

    #: Independent tool calls executed at once. Two searches that do not depend
    #: on each other should not cost two round trips.
    max_parallel_tools: int

    #: Recent turns kept word-for-word. Everything older is compacted.
    history_verbatim: int

    #: Write the running summary with the model (accurate, costs tokens) or with
    #: the offline heuristic (free, blunter).
    summarise_with_model: bool

    #: How many agents deep a question may be passed. 0 disables delegation.
    max_delegation_depth: int

    #: Memories pulled from the brain before the model chooses anything.
    recall_limit: int

    #: Whether the agent is asked to keep an explicit plan on multi-step work.
    allow_planning: bool

    #: The ceiling on ONE reply. Not a cost knob like the rest of this class —
    #: a reply that hits it is *truncated mid-sentence*, which is a defect the
    #: user sees rather than a budget they chose. It sat at a hardcoded 1500 for
    #: every level, which is under a page: a drafted email survived, an
    #: eight-step plan with its reasoning did not, and the loop then carried the
    #: severed half into the next round as if it were finished.
    max_output_tokens: int = 4000

    #: How much of the reply a reasoning model may spend thinking before it
    #: writes. 0 asks for none, which is also what every model without the
    #: capability gets. The catalog already knows which models can do this
    #: (`models/discovery.py`) and nothing was ever asking them to — an Opus
    #: selected at High answered like a model with no reasoning at all.
    #: Must stay comfortably below `max_output_tokens`: the vendor takes it out
    #: of the same allowance, so a budget that eats the whole ceiling leaves no
    #: room for the answer itself.
    thinking_tokens: int = 0

    #: Tokens one turn may spend, counted across every model call it makes —
    #: including the ones a sub-agent makes on its behalf. Rounds were the only
    #: ceiling before, and rounds are a poor proxy: a round carrying a long
    #: conversation and four tool results costs many times one that carries a
    #: sentence. This is also what finally bounds a chain in aggregate, which
    #: `docs/AGENTS.md` has listed as missing since delegation landed.
    #: 0 means unbounded.
    max_tokens_per_turn: int = 0

    def child(self) -> Effort:
        """The budget a delegated sub-agent gets.

        Deliberately smaller than its parent's. A sub-agent is answering one
        question on someone else's behalf, and without this a chain of three
        agents at High could spend 72 model calls on a question the user
        expected to cost one.
        """
        return Effort(
            name=self.name, label=self.label, description=self.description,
            max_steps=max(3, self.max_steps // 2),
            max_parallel_tools=self.max_parallel_tools,
            history_verbatim=2,          # a sub-agent has no conversation of its own
            summarise_with_model=False,
            max_delegation_depth=max(0, self.max_delegation_depth - 1),
            recall_limit=self.recall_limit,
            allow_planning=False,
            # Not halved: the ledger is SHARED down the chain, so a sub-agent
            # spends what the parent has left rather than an allowance of its
            # own. Halving here would bound each hop twice and the whole chain
            # not at all.
            max_tokens_per_turn=self.max_tokens_per_turn,
            # A sub-agent writes a finding, not a chapter — but it still must
            # not be cut in half, which is the one failure that propagates
            # upward as a confident wrong answer.
            max_output_tokens=self.max_output_tokens,
            thinking_tokens=self.thinking_tokens,
        )


LOW = Effort(
    name="low", label="Low",
    description="Quick answers. Best for small local models and simple questions.",
    max_steps=5, max_parallel_tools=2, history_verbatim=6,
    summarise_with_model=False, max_delegation_depth=0, recall_limit=6,
    allow_planning=False, max_tokens_per_turn=40_000,
    max_output_tokens=2_000, thinking_tokens=0,
)

MEDIUM = Effort(
    name="medium", label="Medium",
    description="The default. Looks things up properly without running up a bill.",
    max_steps=12, max_parallel_tools=4, history_verbatim=10,
    summarise_with_model=True, max_delegation_depth=1, recall_limit=10,
    allow_planning=True, max_tokens_per_turn=150_000,
    max_output_tokens=8_000, thinking_tokens=2_000,
)

HIGH = Effort(
    name="high", label="High",
    description="Digs in: more lookups, asks other agents, keeps a plan. Costs more.",
    max_steps=24, max_parallel_tools=6, history_verbatim=16,
    summarise_with_model=True, max_delegation_depth=2, recall_limit=16,
    allow_planning=True, max_tokens_per_turn=500_000,
    max_output_tokens=16_000, thinking_tokens=8_000,
)

LEVELS: dict[str, Effort] = {e.name: e for e in (LOW, MEDIUM, HIGH)}


def get_effort(name: str | None = None) -> Effort:
    """Resolve a level by name, falling back to the saved default.

    An unknown name is not an error: the value arrives from a client's
    localStorage and from a stored setting, either of which can outlive a rename.
    Falling back beats refusing to answer.
    """
    if name:
        hit = LEVELS.get(name.strip().lower())
        if hit:
            return hit
    return LEVELS.get(saved_effort(), MEDIUM)


def saved_effort() -> str:
    """The level stored on this machine, or the default."""
    from ..log import suppressed

    value = None
    with suppressed("reading the saved agent effort level"):
        from ..core.store import get_store
        value = get_store().get_meta(EFFORT_KEY)
    return value if value in LEVELS else DEFAULT_EFFORT


def set_saved_effort(name: str) -> str:
    """Persist the level. Returns what was actually stored."""
    from ..core.store import get_store

    chosen = name.strip().lower()
    if chosen not in LEVELS:
        raise ValueError(f"unknown effort level {name!r}; expected one of {sorted(LEVELS)}")
    get_store().set_meta(EFFORT_KEY, chosen)
    return chosen


def describe_levels() -> list[dict[str, object]]:
    """The levels, for the settings UI — label, description and what changes."""
    return [
        {
            "name": e.name, "label": e.label, "description": e.description,
            "max_steps": e.max_steps,
            "max_tokens_per_turn": e.max_tokens_per_turn,
            "max_output_tokens": e.max_output_tokens,
            "thinking": e.thinking_tokens > 0,
            "delegation": e.max_delegation_depth > 0,
            "planning": e.allow_planning,
        }
        for e in (LOW, MEDIUM, HIGH)
    ]
