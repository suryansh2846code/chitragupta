"""What a tool is allowed to do, decided once and derived everywhere.

The permissions panel asked "which tool is this?" sixty-four times, in thirteen
categories, before the user had sent the agent a single message — the moment
they know least about what it will need. Six of those rows showed our own
function names, and four categories sorted below the bucket meant for things
nobody had got round to naming.

The fix is not a shorter list. It is that a tool now **declares what it does**,
in the same `verb:resource` vocabulary every connector and every action already
uses, and both the tier and the grouping are derived from that declaration
rather than written down a second time beside it.

These tests pin the two properties that make the derivation trustworthy: every
tool declares something the shared parser accepts, and anything that fails to
declare is treated as dangerous rather than as harmless.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import prompt, tools
from chitragupta.connectors.capability import Access
from chitragupta.connectors.capability import parse as parse_capability


@pytest.mark.parametrize("name", sorted(tools.TOOL_DEFS))
def test_every_tool_says_what_it_does(name):
    """A tool that declares nothing is a tool the gate cannot reason about, and
    a gate that cannot reason about something has to refuse it — which shows up
    as a capability that mysteriously does not work."""
    raw = tools.tool_capability(name)

    assert raw, f"{name} declares no capability"
    parse_capability(raw)       # raises UnknownCapabilityError if it is not real


@pytest.mark.parametrize("name", sorted(tools.TOOL_DEFS))
def test_the_tier_is_derived_from_the_verb_never_stored(name):
    """`capability.Access` already answers this for every connector and every
    action. A second table in `agents/` that disagreed with it would be a gate
    giving two answers about the same act."""
    assert tools.tool_access(name) is parse_capability(
        tools.tool_capability(name)).access


def test_an_undeclared_tool_is_treated_as_the_dangerous_kind():
    """Unknown fails closed — `capability.parse`'s own rule, and it matters more
    here. The tempting default reads a tool nobody classified as the harmless
    one, which is exactly how an unclassified write gets granted in bulk."""
    assert tools.tool_access("a_tool_nobody_declared") is Access.DESTRUCTIVE
    assert tools.tool_group("a_tool_nobody_declared") not in tools.ALWAYS


def test_running_code_is_its_own_tier_not_a_louder_write():
    """What `run_python` does is unbounded, so there is no inverse to describe.
    Filing it as a write would let it ride along on "let it change things"."""
    assert tools.tool_access("run_python") is Access.DESTRUCTIVE


# ── the grouping ─────────────────────────────────────────────────────────
def test_every_tool_lands_in_exactly_one_group():
    """A tool in no group is one nobody can grant; a tool in two is one that
    two switches disagree about."""
    seen = [n for group in tools.permission_groups()
            for key in ("read", "change", "run") for n in group[key]]

    assert sorted(seen) == sorted(tools.TOOL_DEFS)
    assert len(seen) == len(set(seen)), "a tool appears in two groups"


def test_nothing_that_reaches_outside_is_granted_at_creation():
    """The whole safety claim of making creation frictionless. Tier one is what
    cannot leave this machine; if anything else is in it, "granted by default"
    quietly became a blank cheque."""
    for name in tools.granted_by_default():
        assert tools.tool_group(name) in tools.ALWAYS, name


def test_the_group_granted_at_creation_touches_nothing_outside():
    """Stated against the resources rather than the group names, so renaming a
    group cannot quietly move something into it."""
    outside = {"email", "thread", "draft", "chat", "message", "event",
               "calendar", "record", "webpage", "file", "folder", "process"}

    for name in tools.granted_by_default():
        resource = parse_capability(tools.tool_capability(name)).resource.value
        assert resource not in outside, f"{name} reaches {resource}"


def test_an_agent_created_today_can_still_think():
    """The other half. An agent that cannot read its own memory is not a safer
    agent, it is a broken one — and making somebody tick ten boxes to get there
    teaches them the screen is a formality before they reach the boxes that
    matter."""
    granted = set(tools.granted_by_default())

    for essential in ("search_brain", "who_is", "remember", "list_tasks",
                      "update_plan", "ask_agent"):
        assert essential in granted, essential


def test_the_panel_is_a_handful_of_decisions_not_sixty_four():
    """The complaint, as a number. Groups that are always on are not decisions
    at all; the rest are one switch per thing a group can do."""
    decisions = sum(
        0 if group["always"] else
        len([k for k in ("read", "change", "run") if group[k]])
        for group in tools.permission_groups())

    assert decisions <= 8, f"{decisions} decisions is on its way back to 64"
    assert len(tools.TOOL_DEFS) > 50, "premise changed — recount the decisions"


def test_reading_and_changing_are_separate_switches():
    """"Allow websites" as one switch would mean allowing an agent to type into
    them in order to let it read one, which is not a choice anybody means to
    make."""
    websites = next(g for g in tools.permission_groups()
                    if g["key"] == "websites")

    assert "browse_open" in websites["read"]
    assert "browse_click" in websites["change"]
    assert "browse_click" not in websites["read"]


def test_every_group_says_what_it_is_in_words_a_person_reads():
    """The panel renders these. A group with no sentence is a switch with no
    explanation, which is how sixty-four function names happened."""
    for group in tools.permission_groups():
        assert group["label"] and not group["label"].islower()
        assert group["blurb"].endswith("."), group["key"]
        assert "_" not in group["label"], "that is an identifier, not a label"


# ── an agent has to know what it has NOT been given ──────────────────────
#
# Asked whether it could post to a site, an agent answered: "You haven't enabled
# browser access for this agent yet. To turn it on: Settings → Agents & tools →
# Social Media Manager." Nothing had told it that. A withheld tool is simply
# absent from its list, so it could not tell "never granted" from "does not
# exist" and filled the gap from general knowledge — including a settings path
# it had no way to check.
#
# This is the prerequisite for every permission model, including the one that
# ships today. It matters *more* under ask-in-chat, not less: an agent that
# starts with nothing and cannot name what it lacks will invent capabilities and
# invent the instructions for enabling them.
def _prompt_for(granted):
    return prompt.build(name="social media manager", role="content",
                        system_prompt="", tools=granted, agent_id="x")


def test_an_agent_is_told_which_groups_it_lacks():
    said = _prompt_for(tools.granted_by_default())

    assert "WHAT YOU HAVE NOT BEEN GIVEN" in said
    for group in ("your connected accounts", "websites", "your mac"):
        assert group in said


def test_it_is_told_the_capability_exists_rather_than_that_it_cannot():
    """The exact failure. "I can't do that" is false and sends the user away;
    "you have not switched that on for me" is true and actionable."""
    said = _prompt_for(tools.granted_by_default())

    assert "they are simply not switched on for you" in said
    assert "never say a capability does not exist when it is only withheld" \
        in said.lower()


def test_it_is_told_it_cannot_grant_itself_anything():
    """Otherwise the next thing it invents is a way to try."""
    assert "cannot switch them on yourself" in _prompt_for(
        tools.granted_by_default())


def test_it_is_given_the_real_route_so_it_stops_inventing_one():
    assert "Settings → Agents & tools" in _prompt_for(tools.granted_by_default())
    assert "Never guess at a different route" in _prompt_for(
        tools.granted_by_default())


def test_an_agent_that_has_everything_is_told_nothing():
    """Every sentence in the prompt is billed on every turn. This one earns its
    place only when something is actually missing."""
    assert "HAVE NOT BEEN GIVEN" not in _prompt_for(list(tools.TOOL_DEFS))


def test_the_note_names_groups_not_tools():
    """An agent told it lacks `browse_select` reports that it cannot choose from
    a dropdown — true, and useless to the person who has to act on it."""
    said = _prompt_for(tools.granted_by_default())
    start = said.index("WHAT YOU HAVE NOT BEEN GIVEN")

    for raw in ("browse_select", "browse_click", "read_file", "list_mail"):
        assert raw not in said[start:]


def test_granting_a_group_removes_it_from_the_note():
    """The note has to track what is actually held, or it becomes another thing
    that is wrong about the agent's own permissions."""
    with_sites = [*tools.granted_by_default(), "browse_open"]

    said = _prompt_for(with_sites)

    assert "websites" not in said[said.index("WHAT YOU HAVE NOT BEEN GIVEN"):]
    assert "your mac" in said


# ── the leaf, and what holds it to the registry ──────────────────────────
#
# `tool_facts` is a leaf so `prompt.py` can read it. The first version of this
# lived in `tools.py`, and importing it from the prompt grew the frozen
# `agents_tools` cycle from five modules to ten — caught by
# `tests/test_import_layering.py` on the commit that tried it.
#
# Moving it down cost one thing: the table can no longer ask the registry what
# tools exist, so it answers from its own keys. These two tests are what makes
# that honest instead of a second list that drifts.
def test_the_facts_table_and_the_tool_registry_are_the_same_set():
    from chitragupta.agents import tool_facts

    assert tool_facts.builtin_names() == frozenset(tools.TOOL_DEFS)


def test_the_table_stays_a_leaf():
    """It may read the shared capability vocabulary and nothing else in
    `agents/`. The moment it imports a sibling it is back inside the cycle it
    was extracted to break, and `prompt.py` cannot use it again."""
    import ast
    import pathlib

    from chitragupta.agents import tool_facts

    source = pathlib.Path(tool_facts.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level:
            assert node.module == "connectors.capability", (
                f"tool_facts imports {node.module!r} — it must stay a leaf")


# ── where the rest of a permission is set ────────────────────────────────
#
# "there is no option where i can give access to browser in agent and tools
# section" — there was, under a heading reading "Websites", and turning it on
# still would not have been enough: WHICH sites an agent may touch is a list
# kept on another screen, and nothing on this one said so.
def test_the_browser_group_is_named_so_somebody_looking_for_it_finds_it():
    """A person hunting for "browser access" searched this screen for the word
    and it was not on it."""
    websites = next(g for g in tools.permission_groups() if g["key"] == "websites")

    assert "browser" in f"{websites['label']} {websites['blurb']}".lower()


def test_a_group_whose_permission_has_a_second_half_says_where_it_is():
    """Turning this on grants the agent nothing on its own."""
    websites = next(g for g in tools.permission_groups() if g["key"] == "websites")

    assert websites["more"]["screen"] == "connectors"
    assert websites["more"]["label"].strip()


def test_connected_accounts_points_somewhere_too():
    """"May read your mail" means nothing until a mailbox is connected, which
    is the same dead end in a different group."""
    accounts = next(g for g in tools.permission_groups() if g["key"] == "accounts")

    assert accounts["more"]["screen"] == "connectors"


def test_a_group_with_nowhere_else_to_go_names_no_screen():
    """An absent key draws no button. A control that led nowhere would teach
    that these buttons lead nowhere."""
    mac = next(g for g in tools.permission_groups() if g["key"] == "mac")

    assert "more" not in mac


# ── the screen's axis, over the same table ────────────────────────────────
#
# `tool_group` answers how far a capability reaches — the gate's question.
# `tool_app` answers which app a person sets it on. One table, one derivation,
# two questions; a second taxonomy is the drift this file exists to catch.

def test_every_built_in_lands_on_exactly_one_card():
    """A tool on no card cannot be granted from the screen at all, and a tool
    on two is a switch that disagrees with another switch."""
    from chitragupta.agents.tool_facts import builtin_names, permission_apps

    seen: list[str] = []
    for app in permission_apps():
        seen += app["read"] + app["change"] + app["run"]

    assert sorted(seen) == sorted(builtin_names()), (
        f"orphaned: {sorted(set(builtin_names()) - set(seen))}; "
        f"duplicated: {sorted({n for n in seen if seen.count(n) > 1})}")


def test_a_cards_tier_lists_agree_with_what_each_tool_declared():
    """The split is derived, never arranged here — the same rule
    `permission_groups` is held to."""
    from chitragupta.agents.tool_facts import permission_apps, tool_access
    from chitragupta.connectors.capability import Access

    for app in permission_apps():
        for name in app["read"]:
            assert tool_access(name) is Access.READ, name
        for name in app["run"]:
            assert tool_access(name) is Access.DESTRUCTIVE, name
        for name in app["change"]:
            assert tool_access(name) not in (Access.READ, Access.DESTRUCTIVE), name


def test_a_card_with_no_tools_is_not_offered():
    """A heading nobody can switch anything on teaches that the screen is
    decorative, before they reach the switches that matter."""
    from chitragupta.agents.tool_facts import APPS, permission_apps

    shown = {a["key"] for a in permission_apps()}
    assert shown <= {a.key for a in APPS}
    for app in permission_apps():
        assert app["read"] or app["change"] or app["run"], app["key"]


def test_the_two_axes_are_derived_from_the_same_table():
    """Not a second taxonomy. Every tool the gate knows about is a tool the
    screen knows about, and neither has an entry the other lacks."""
    from chitragupta.agents.tool_facts import builtin_names, tool_app, tool_group

    for name in builtin_names():
        assert tool_group(name), name
        assert tool_app(name), name


def test_only_the_card_with_three_tiers_names_a_third_switch():
    """`CLAUDE.md` forbids folding `destructive` into "write": running code is
    its own decision, and the word on the switch is the half a person sees."""
    from chitragupta.agents.tool_facts import permission_apps

    for app in permission_apps():
        if app["run"]:
            assert app["run_label"], f"{app['key']} runs code and does not say so"
        else:
            assert not app["run_label"], f"{app['key']} names a switch it has no tools for"


def test_each_ask_row_names_the_list_its_actions_are_actually_judged_against():
    """`App.ask_kind` is a literal, because `tool_facts` is a leaf and importing
    the action registry here would grow the cycle it was extracted to stop. So
    the registry checks the literal instead: a card promising to show "this
    app's standing grants" and reading the wrong list would show somebody
    else's, which is worse than showing none."""
    from chitragupta.actions import REGISTRY
    from chitragupta.agents.tool_facts import _APP_OF, APPS
    from chitragupta.connectors.capability import parse as parse_capability

    #: action capability resource → the app its card sits on, the same mapping
    #: the tools go through.
    declared = {a.key: a.ask_kind for a in APPS if a.ask_kind}
    for name, spec in REGISTRY.items():
        if not spec.recipient_kind or not spec.capability:
            continue
        app = _APP_OF.get(parse_capability(spec.capability).resource.value, "")
        if app not in declared:
            continue
        assert declared[app] == spec.recipient_kind, (
            f"{app} says it is judged against {declared[app]}, but {name} is "
            f"judged against {spec.recipient_kind}")


def test_no_card_offers_to_create_a_grant():
    """A grant is **made** from the approval it would have cleared: the queue
    offers it there with the exact value the gate reads, at the moment somebody
    learns they want one. A box on a settings screen asks them to predict it,
    and writes to the same global list from a second place — so `App` declares
    which list to *read* and nothing about adding to it."""
    from dataclasses import fields

    from chitragupta.agents.tool_facts import App

    names = {f.name for f in fields(App)}
    assert "ask_addable" not in names, "the box came back"
    assert "ask_kind" in names, "the row still has to know which list to show"
