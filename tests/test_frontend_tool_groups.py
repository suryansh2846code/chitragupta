"""Sixty-four switches became six, executed against the real render.

The panel asked "which tool is this?" sixty-four times, in thirteen categories,
before the user had sent the agent a single message — the moment they know least
about what it will need. It now arranges itself by what a tool **touches** and
what it **does** to it, both derived server-side from the capability each tool
declares and sent down on the row.

Executed rather than asserted on source order. `node --check` passes on the
temporal-dead-zone `ReferenceError` that once blanked this whole panel, and
`tests/js/CLAUDE.md`'s rule is the one that found it: if you add a render path
or a click handler, run it.
"""
from __future__ import annotations

import pytest
from test_frontend_agent_tools import run

#: Rows as `describe_tools()` sends them now — `group` and `access` beside the
#: label and category they are replacing as the thing the panel arranges by.
GROUPED = [
    {"name": "search_brain", "label": "Search", "category": "Memory",
     "description": "Search your brain.", "source": "builtin", "connector": "",
     "group": "on_device", "access": "read"},
    {"name": "remember", "label": "Remember", "category": "Memory",
     "description": "Remember something.", "source": "builtin", "connector": "",
     "group": "on_device", "access": "write"},
    {"name": "browse_open", "label": "Open page", "category": "Websites you allow",
     "description": "Open a page.", "source": "builtin", "connector": "",
     "group": "websites", "access": "read"},
    {"name": "browse_read", "label": "Re-read", "category": "Websites you allow",
     "description": "Read it again.", "source": "builtin", "connector": "",
     "group": "websites", "access": "read"},
    {"name": "browse_click", "label": "Click", "category": "Websites you allow",
     "description": "Click something.", "source": "builtin", "connector": "",
     "group": "websites", "access": "write"},
    {"name": "run_python", "label": "Run", "category": "Your Mac",
     "description": "Run Python.", "source": "builtin", "connector": "",
     "group": "mac", "access": "destructive"},
]

#: What each group IS, in the server's words. The panel renders these and never
#: writes them, for the reason every other label on that screen comes down the
#: wire.
SPECS = [
    {"key": "on_device", "label": "Its own memory and your day", "always": True,
     "blurb": "Nothing here leaves this machine.",
     "read": ["search_brain"], "change": ["remember"], "run": []},
    {"key": "websites", "label": "Websites", "always": False,
     "blurb": "Pages on sites you have allowed.",
     "read": ["browse_open", "browse_read"], "change": ["browse_click"], "run": []},
    {"key": "mac", "label": "Your Mac", "always": False,
     "blurb": "Files and folders on this computer.",
     "read": [], "change": [], "run": ["run_python"]},
]


@pytest.fixture(scope="module")
def tiered() -> dict:
    return run(["search_brain", "remember"], GROUPED, [], groups=SPECS)


def test_built_ins_are_grouped_by_what_they_touch(tiered):
    """Not by the thirteen subject headings. "Websites" is one decision; which
    of the six browser tools performs it is not."""
    builtin = [g["name"] for g in tiered["groups"] if g["kind"] == "builtin"]

    assert builtin == ["Its own memory and your day", "Websites", "Your Mac"]


def test_a_group_says_what_it_is(tiered):
    """Sixty-four switches had sixty-four descriptions and no answer at all to
    "what am I deciding?"."""
    assert "Nothing here leaves this machine." in tiered["html"]
    assert "Pages on sites you have allowed." in tiered["html"]


def test_the_group_that_never_leaves_the_machine_is_not_a_switch(tiered):
    """An agent that cannot read its own memory is not a safer agent, it is a
    broken one — and ten boxes to get there teaches somebody the screen is a
    formality before they reach the boxes that matter."""
    assert "Always on" in tiered["html"]
    assert 'data-bulk="search_brain"' not in tiered["html"]


def test_reading_and_changing_a_website_are_separate_switches(tiered):
    """A single "allow websites" switch would mean allowing an agent to type
    into a site in order to let it read one."""
    assert 'data-bulk="browse_open browse_read"' in tiered["html"]
    assert 'data-bulk="browse_click"' in tiered["html"]


def test_running_code_is_offered_as_its_own_thing(tiered):
    """It has no inverse, so it must never ride along on "let it change
    things"."""
    assert "Irreversible" in tiered["html"]
    assert 'data-bulk="run_python"' in tiered["html"]


def test_the_per_tool_rows_are_still_there_behind_a_disclosure(tiered):
    """The complaint was that per-tool control was the ONLY thing on offer, not
    that it should not exist."""
    assert 'data-tool="browse_click"' in tiered["html"]
    assert "Show all" in tiered["html"]


def test_one_switch_turns_on_every_tool_in_its_bucket():
    """The whole point of the compaction."""
    out = run(["search_brain", "remember"], GROUPED, [], groups=SPECS,
              bulk="browse_open browse_read")

    assert out["bulked"], "the bulk switch never ran"
    assert set(out["bulked"]["sent"]) >= {"browse_open", "browse_read"}


def test_a_group_switch_sends_exactly_one_request():
    """Looping over the per-tool toggle would fire one PATCH per tool, each
    sending the whole list — and whichever replied last would win, so turning a
    group on could land as a group half on, depending on the network."""
    out = run(["search_brain"], GROUPED, [], groups=SPECS,
              bulk="browse_open browse_read")

    assert out["bulked"]["patches"] == 1


def test_turning_a_group_off_removes_all_of_it_and_nothing_else():
    out = run(["search_brain", "browse_open", "browse_read"], GROUPED, [],
              groups=SPECS, bulk="browse_open browse_read")

    assert "browse_open" not in out["bulked"]["sent"]
    assert "browse_read" not in out["bulked"]["sent"]
    assert "search_brain" in out["bulked"]["sent"], "it took an unrelated tool"


def test_a_partly_granted_group_says_so_rather_than_lying():
    """Somebody who used the per-tool rows leaves a group half on. A switch
    showing a plain "off" over two live tools would be the screen lying about
    what the agent can do."""
    out = run(["search_brain", "browse_open"], GROUPED, [], groups=SPECS)

    assert "1 of 2 on" in out["html"]


def test_an_older_server_without_groups_still_renders():
    """`groups` is additive, and a panel that went blank because a field was
    absent would be a worse failure than the one being fixed."""
    out = run(["search_brain"], GROUPED, [], groups=[])

    assert out["html"].strip(), "the panel went blank"
    assert 'data-tool="search_brain"' in out["html"]
