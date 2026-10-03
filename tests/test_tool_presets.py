"""Giving an agent the lot, in one press, per category or all at once.

Six decisions beats sixty-four and is still six. The common case is not six
decisions — it is *"this one is mine, let it do everything"* or *"let it look
and nothing else"* — and a screen that compacts the list and still makes
somebody set every switch has only done half the job.

Two controls, because the real answer is usually about one group: **Allow all**
on a category, and the presets above them for every category at once.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import tool_facts as tf
from chitragupta.connectors.capability import Access


def test_the_presets_are_the_three_answers_people_actually_give():
    assert [p["key"] for p in tf.PRESETS] == ["all", "read", "none"]


@pytest.mark.parametrize("preset", tf.PRESETS)
def test_every_preset_says_what_it_includes(preset):
    """The sentence is on the button, not in a tooltip. "Allow everything"
    includes running code, and a control that hid that would be the
    tap-nobody-reads failure at the worst possible scale."""
    assert preset["label"] and preset["blurb"].endswith(".")


def test_allow_everything_really_is_everything():
    assert set(tf.preset_tools("all")) == tf.builtin_names()


def test_allow_everything_says_out_loud_that_it_runs_code():
    said = next(p for p in tf.PRESETS if p["key"] == "all")["blurb"].lower()

    assert "running code" in said


def test_read_only_can_change_nothing():
    """The point of the preset. If one write slipped in, "read only" would be
    a label on a control that does something else."""
    for name in tf.preset_tools("read"):
        assert tf.tool_access(name) is Access.READ or \
            tf.tool_group(name) in tf.ALWAYS, name


def test_read_only_still_lets_it_think():
    """A preset that took the brain away would not be read-only, it would be
    broken — the always-on group is in every preset."""
    assert set(tf.granted_by_default()) <= set(tf.preset_tools("read"))
    assert set(tf.granted_by_default()) <= set(tf.preset_tools("none"))


def test_nothing_yet_is_exactly_what_a_new_agent_gets():
    assert set(tf.preset_tools("none")) == set(tf.granted_by_default())


def test_nothing_yet_reaches_nothing_outside_this_machine():
    for name in tf.preset_tools("none"):
        assert tf.tool_group(name) in tf.ALWAYS, name


def test_a_preset_nobody_recognises_grants_nothing():
    """It arrives from a client. Returning "everything" for a typo would be the
    worst possible failure mode for this particular control."""
    assert tf.preset_tools("") == []
    assert tf.preset_tools("everything") == []
    assert tf.preset_tools("ALL") == []


def test_the_presets_are_ordered_loudest_first():
    """"Allow everything" is the one people are looking for, and burying it
    under the cautious options is how a screen gets ignored."""
    assert tf.PRESETS[0]["key"] == "all"


# ── the merge that stops a preset taking Notion away ─────────────────────
def test_a_preset_covers_built_ins_and_says_nothing_about_connectors():
    """The save is the whole tool list. A preset answering with its own names
    alone would make "allow everything" an "allow everything and also forget
    the connectors" button, which is the opposite of what it says."""
    names = set(tf.preset_tools("all"))

    assert names == tf.builtin_names()
    assert not any(":" in n for n in names), "a connector tool got in"


def test_the_route_keeps_what_a_preset_does_not_mention(monkeypatch):
    """The merge, executed. An agent holding the connector sentinel keeps it
    through a preset that has no opinion about connectors at all."""
    from chitragupta.agents.agent import Agent
    from chitragupta.api.routes import agents as route

    held = Agent(id="a1", name="A", role="r", system_prompt="",
                 tools=["search_brain", "mcp", "notion__search"])
    written: dict = {}

    class _Overrides:
        def set(self, agent_id, tools):
            written[agent_id] = list(tools)

    monkeypatch.setattr("chitragupta.agents.presets.get_agent", lambda i: held)
    monkeypatch.setattr("chitragupta.agents.tool_overrides.get_tool_overrides",
                        lambda: _Overrides())

    route.set_agent_tools("a1", route.AgentTools(preset="all"))

    got = set(written["a1"])
    assert "mcp" in got, "the preset dropped the connector sentinel"
    assert "notion__search" in got, "the preset dropped a connector tool"
    assert tf.builtin_names() <= got


def test_an_unknown_preset_is_refused_rather_than_applied(monkeypatch):
    from fastapi import HTTPException

    from chitragupta.agents.agent import Agent
    from chitragupta.api.routes import agents as route

    held = Agent(id="a1", name="A", role="r", system_prompt="", tools=[])
    monkeypatch.setattr("chitragupta.agents.presets.get_agent", lambda i: held)

    with pytest.raises(HTTPException) as raised:
        route.set_agent_tools("a1", route.AgentTools(preset="everything"))

    assert raised.value.status_code == 400


def test_sending_a_tool_list_still_works_without_a_preset(monkeypatch):
    """Additive. The switches send a list and must keep working exactly as
    they did — removing that is a separate landing from adding this."""
    from chitragupta.agents.agent import Agent
    from chitragupta.api.routes import agents as route

    held = Agent(id="a1", name="A", role="r", system_prompt="", tools=[])
    written: dict = {}

    class _Overrides:
        def set(self, agent_id, tools):
            written[agent_id] = list(tools)

    monkeypatch.setattr("chitragupta.agents.presets.get_agent", lambda i: held)
    monkeypatch.setattr("chitragupta.agents.tool_overrides.get_tool_overrides",
                        lambda: _Overrides())

    route.set_agent_tools("a1", route.AgentTools(tools=["search_brain"]))

    assert written["a1"] == ["search_brain"]


# ── pressing one before the agent exists ─────────────────────────────────
#
# The builder is a screen with no agent behind it, so it cannot PATCH a preset
# on afterwards — and resolving "everything" in the browser is exactly the
# staleness the key exists to avoid. The built-ins come from the key, at the
# moment Create is pressed; the rest comes from the screen, because only the
# screen knows which connectors the user has.
def test_the_builder_is_told_what_each_preset_covers():
    """A preview, so a draft can show what the button will do. It is not what
    gets sent — that is still the key."""
    from chitragupta.api.routes.agents import available_tools

    presets = {p["key"]: p for p in available_tools()["presets"]}

    assert set(presets["all"]["tools"]) == tf.builtin_names()
    assert set(presets["none"]["tools"]) == set(tf.granted_by_default())


def test_creating_an_agent_with_a_preset_resolves_it_here(monkeypatch):
    from chitragupta.api.routes import agents as route

    made: dict = {}

    class _Store:
        def create(self, name, role, prompt, tools, recall):
            made.update(name=name, tools=list(tools or []))
            return type("A", (), {"id": "a1", "name": name, "role": role})()

    monkeypatch.setattr("chitragupta.agents.custom.get_custom_store", lambda: _Store())
    route.create_agent(route.NewAgent(name="Sales", preset="all", tools=[]))

    assert tf.builtin_names() <= set(made["tools"])


def test_a_preset_at_creation_keeps_the_connectors_the_screen_knew_about():
    """No preset can name a connector tool — they do not exist until somebody
    adds one — so "allow everything" would otherwise be the one button that
    created an agent unable to see Notion."""
    from chitragupta.api.routes import agents as route

    got = route._preset_merge("all", ["mcp", "notion__search", "search_brain"])

    assert "mcp" in got and "notion__search" in got
    assert tf.builtin_names() <= set(got)


def test_nothing_yet_at_creation_reaches_nothing_outside_this_machine():
    from chitragupta.api.routes import agents as route

    got = route._preset_merge("none", [])

    assert set(got) == set(tf.granted_by_default())


def test_an_unknown_preset_is_refused_at_creation_too(monkeypatch):
    """It arrives from a client, and this is the one endpoint where returning
    "everything" for a typo would create the agent that holds it."""
    from fastapi import HTTPException

    from chitragupta.api.routes import agents as route

    with pytest.raises(HTTPException) as raised:
        route.create_agent(route.NewAgent(name="Sales", preset="everything"))

    assert raised.value.status_code == 400


def test_creating_without_a_preset_is_untouched(monkeypatch):
    """`None` means "no opinion, give it the standard set" and `[]` means
    "deliberately none". Collapsing those is how the builder once created
    agents with no tools at all."""
    from chitragupta.api.routes import agents as route

    seen: list = []

    class _Store:
        def create(self, name, role, prompt, tools, recall):
            seen.append(tools)
            return type("A", (), {"id": "a1", "name": name, "role": role})()

    monkeypatch.setattr("chitragupta.agents.custom.get_custom_store", lambda: _Store())
    route.create_agent(route.NewAgent(name="A"))
    route.create_agent(route.NewAgent(name="B", tools=[]))

    assert seen == [None, []]
