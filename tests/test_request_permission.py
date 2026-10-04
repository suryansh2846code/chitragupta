"""An agent asks for what it needs, where the work is, and the user taps once.

The failure this replaces was measured. Asked whether it could post to a site,
an agent answered *"You haven't enabled browser access for this agent yet. To
turn it on: Settings → Agents & tools → Social Media Manager."* Nothing had told
it that path — a withheld tool is simply absent from its list, so it could not
tell "never granted" from "does not exist" and filled the gap from general
knowledge.

Even with that corrected, naming a settings screen ends the conversation and
sends somebody off to re-derive the decision they were already being asked to
make. `request_permission` puts the ask on a card in the chat, carrying the
agent's own reason, which is the part a person actually needs in order to
answer.

**The agent asks; it never grants.** Every test here is a way of saying that.
"""
from __future__ import annotations

import pytest

from chitragupta import actions
from chitragupta.actions import REGISTRY, Risk
from chitragupta.agents import tool_facts as tf


# ── what may be asked for at all ─────────────────────────────────────────
def test_reading_and_changing_can_be_asked_for():
    assert tf.tools_for("websites", "read")
    assert tf.tools_for("websites", "change")
    assert tf.tools_for("mac", "read")


def test_running_code_cannot_be_asked_for():
    """The one thing that must be granted on the settings screen, deliberately
    and not in the middle of a flow where somebody is trying to get something
    done. A tap given to unblock a task is not the same decision as one given
    while reading a page about what the tap means."""
    assert tf.tools_for("mac", "run") == []
    assert "run" not in tf.ASKABLE


def test_the_group_that_is_always_on_cannot_be_asked_for():
    """It is already granted. A card for it would change nothing and still want
    a tap, which teaches that taps change nothing."""
    assert tf.tools_for("on_device", "read") == []


@pytest.mark.parametrize("group,level", [
    ("nonsense", "read"), ("websites", "nonsense"), ("", ""),
    ("websites", "destructive"), ("mac", "execute"),
])
def test_anything_unrecognised_grants_nothing(group, level):
    """These strings come from a model. `capability.parse` fails closed on its
    input for the same reason."""
    assert tf.tools_for(group, level) == []


# ── the action, and the one rule under it ────────────────────────────────
def test_asking_is_red_so_an_unwatched_run_can_never_reach_it():
    """The whole threat model in one line. A routine reads text a stranger
    wrote; an agent that could widen itself on that input is the thing every
    other gate in this codebase exists to prevent."""
    from chitragupta.agents import permissions

    assert REGISTRY["request_permission"].risk is Risk.RED
    assert "request_permission" in permissions.NEVER_UNATTENDED


def test_it_is_not_promotable_to_a_standing_grant():
    """"Always allow" on this card would be a standing permission to acquire
    permissions, which is every permission."""
    from chitragupta.agents import permissions

    assert "request_permission" not in permissions.OUTBOUND_ACTIONS
    assert REGISTRY["request_permission"].always_ask_because


def test_the_card_says_why_it_waits():
    """RED actions owe the user a sentence. "This always needs approval" told
    about a Slack message teaches nothing except that the app is confused."""
    said = REGISTRY["request_permission"].always_ask_because

    assert "tap" in said.lower()


def test_asking_without_an_agent_grants_nothing():
    out = actions.REGISTRY["request_permission"].handler(
        {"group": "websites", "level": "read"})

    assert out["ok"] is False


def test_asking_for_something_unknown_names_what_is_allowed():
    """A vague refusal would have the model guess again rather than read the
    list — and it chose those two strings, so it can choose better ones."""
    out = actions.REGISTRY["request_permission"].handler(
        {"group": "websites", "level": "obliterate", "agent_id": "chotu"})

    assert out["ok"] is False
    assert "read" in out["error"] and "change" in out["error"]


def test_asking_to_run_code_points_at_the_control_instead():
    """It named a Settings page, which was deleted when permissions moved into
    each agent's own profile. The route is `signposts.PERMISSIONS` now, written
    once, because four strings carried on naming the old one."""
    from chitragupta.agents import signposts

    out = actions.REGISTRY["request_permission"].handler(
        {"group": "mac", "level": "run", "agent_id": "chotu"})

    assert out["ok"] is False
    assert signposts.PERMISSIONS in out["error"]


# ── the grant itself ─────────────────────────────────────────────────────
@pytest.fixture
def agent(monkeypatch):
    """One agent on the roster, with only what it is created with."""
    from chitragupta.agents.agent import Agent

    made = Agent(id="asker", name="Asker", role="r", system_prompt="",
                 tools=list(tf.granted_by_default()))
    monkeypatch.setattr("chitragupta.agents.list_agents", lambda: [made])
    written: dict = {}

    class _Overrides:
        def set(self, agent_id, tools):
            written[agent_id] = list(tools)

    monkeypatch.setattr("chitragupta.agents.tool_overrides.get_tool_overrides",
                        lambda: _Overrides())
    return made, written


def test_a_tap_gives_exactly_what_was_asked_for(agent):
    made, written = agent

    out = actions.REGISTRY["request_permission"].handler(
        {"group": "websites", "level": "read", "agent_id": "asker"})

    assert out["ok"] is True
    got = set(written["asker"])
    assert set(tf.tools_for("websites", "read")) <= got


def test_a_tap_gives_nothing_it_was_not_asked_for(agent):
    """The scope is the group and the level. A tap on "let it read websites"
    must not become permission to type into them — that is the difference
    between a card somebody can answer and a blank cheque."""
    made, written = agent

    actions.REGISTRY["request_permission"].handler(
        {"group": "websites", "level": "read", "agent_id": "asker"})

    got = set(written["asker"])
    for changing in tf.tools_for("websites", "change"):
        assert changing not in got, changing
    for mac in tf.tools_for("mac", "read"):
        assert mac not in got, mac


def test_what_it_already_had_is_kept(agent):
    """The write is the whole list, so a grant that forgot the existing tools
    would be a silent revocation of everything else."""
    made, written = agent

    actions.REGISTRY["request_permission"].handler(
        {"group": "websites", "level": "read", "agent_id": "asker"})

    assert set(made.tools) <= set(written["asker"])


def test_asking_for_something_it_already_has_changes_nothing(agent):
    made, written = agent
    made.tools = list(set(made.tools) | set(tf.tools_for("websites", "read")))

    out = actions.REGISTRY["request_permission"].handler(
        {"group": "websites", "level": "read", "agent_id": "asker"})

    assert out["ok"] is True
    assert out["added"] == []
    assert "asker" not in written, "it rewrote the list to change nothing"


def test_an_agent_that_is_not_on_the_roster_gets_nothing(agent):
    _, written = agent

    out = actions.REGISTRY["request_permission"].handler(
        {"group": "websites", "level": "read", "agent_id": "ghost"})

    assert out["ok"] is False
    assert not written


# ── and the agent has to know the action exists ──────────────────────────
def test_every_agent_can_ask():
    """An agent that cannot ask can only describe a settings screen, and the
    one that tried invented the path it described."""
    from chitragupta.agents.library import _PROACTIVE

    assert "request_permission" in _PROACTIVE


def test_the_prompt_tells_an_agent_that_can_ask_to_ask():
    from chitragupta.agents import prompt

    said = prompt.build(name="a", role="r", system_prompt="",
                        tools=tf.granted_by_default(),
                        actions=["request_permission"])

    assert "ASK FOR IT with the request_permission action" in said
    assert "To ASK FOR ANYTHING YOU HAVE NOT BEEN GIVEN" in said
    # It is told the general form, not only the tool-group pair. An agent that
    # knew how to ask for a group and not for a website wrote half its ask as a
    # card and the other half as prose — and then wrote all of it as prose.
    for shape in ("site:HOST:read", "site:HOST:change", "folder:PATH",
                  "connector:NAME", "websites:change"):
        assert shape in said, shape
    # And it is told not to do the thing this whole mechanism replaces.
    assert "NEVER tell them to open Settings" in said


def test_an_agent_that_cannot_ask_is_told_the_screen_instead():
    """Both endings are true. Sending an agent to a settings screen it could
    have replaced with a card is what this pair exists to stop — but an agent
    with no way to ask must still be able to say something useful."""
    from chitragupta.agents import prompt

    said = prompt.build(name="a", role="r", system_prompt="",
                        tools=tf.granted_by_default(), actions=[])

    from chitragupta.agents import signposts

    assert signposts.PERMISSIONS in said
    assert "request_permission" not in said


def test_an_agent_with_everything_is_told_none_of_it():
    from chitragupta.agents import prompt
    from chitragupta.agents.tools import TOOL_DEFS

    said = prompt.build(name="a", role="r", system_prompt="",
                        tools=list(TOOL_DEFS), actions=["request_permission"])

    assert "WHAT YOU HAVE NOT BEEN GIVEN" not in said
    assert "request_permission" not in said, "it kept a protocol it cannot use"
