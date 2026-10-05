"""Everything one agent accumulated, forgotten in one place.

An agent leaves a trail across nine stores: its conversation, the summary of
it, its two files, the ledger of what it wrote into them, the persona it was
given, the name it was renamed to, the face it was drawn with, the model it was
bound to, the tools it was granted and the connectors it was allowed.

That list lived inside `custom.delete`, which meant it was the list for *custom*
agents. An agent we ship accumulates exactly the same things and had no way to
be cleared at all — so "delete this and start again" was something only half
the agents could do, and the half that could were the ones where it was
destructive.

**One list, used by both.** A store added later is added here once, and both
paths get it. The alternative is the shape this repo keeps paying for: two
cleanup lists, and the one that gets forgotten is whichever is further from the
code somebody is writing.

Each step is suppressed on its own. A store that cannot be reached must not
stop the other eight — a half-forgotten agent is worse than a slow one, and the
caller is usually a user who pressed a button and is owed an outcome.
"""
from __future__ import annotations

from ..log import get_logger, suppressed

log = get_logger(__name__)


def everything(agent_id: str) -> None:
    """Forget everything this agent has, short of the agent itself.

    Deliberately does **not** remove the agent row or the roster entry: what it
    means to stop existing differs between an agent we ship and one the user
    built, and that decision belongs to the caller. This is only the trail.

    Ids are slugs, so every one of these outlives the agent unless it is
    cleared: delete "Chotu", build another "Chotu", and the new one lands on
    the same id and inherits whatever was left behind — a tool list it never
    chose, notes about a job it never did, somebody else's face.
    """
    from .agent import AgentMemory

    with suppressed("clearing an erased agent's conversation"):
        AgentMemory().clear(agent_id)
    with suppressed("clearing an erased agent's model binding"):
        from .agent_models import clear_agent_model
        clear_agent_model(agent_id)
    with suppressed("clearing an erased agent's connector grants"):
        from .connector_grants import forget_agent
        forget_agent(agent_id)
    with suppressed("clearing an erased agent's folders"):
        from .file_tools import forget_agent_scope
        forget_agent_scope(agent_id)
    with suppressed("clearing an erased agent's tool overrides"):
        from .tool_overrides import get_tool_overrides
        get_tool_overrides().clear(agent_id)
    with suppressed("removing an erased agent's files"):
        from . import profile_files
        profile_files.forget(agent_id)
    with suppressed("clearing an erased agent's note ledger"):
        from . import notes
        notes.forget(agent_id)
    with suppressed("clearing an erased agent's name"):
        from . import identity
        identity.clear(agent_id)
    with suppressed("clearing an erased agent's persona"):
        from . import persona
        persona.forget(agent_id)
    with suppressed("clearing an erased agent's avatar"):
        from .avatars import clear_agent_avatar
        clear_agent_avatar(agent_id)


def reset_shipped(agent_id: str) -> None:
    """Put an agent we ship back to the one we ship.

    There is no row to delete and nothing to destroy for ever — the template is
    in the library whatever the user does. So "delete" means this: forget
    everything it learned and was given, and take it off the team, so adding it
    again from the Library produces a genuinely new agent rather than the old
    one wearing a fresh coat.
    """
    everything(agent_id)
    with suppressed("taking an erased agent off the roster"):
        from .library import remove_from_roster
        remove_from_roster(agent_id)
