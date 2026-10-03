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


# ── giving the lot, in one press ─────────────────────────────────────────
#
# "categorise the permission in more briefer and compact category AND give
# option to select and give all the permission" — the first half shipped and
# the second did not, so the screen compacted the list and still made somebody
# set every switch.
PRESETS = [
    {"key": "all", "label": "Allow everything",
     "blurb": "Every group, including running code on this Mac."},
    {"key": "read", "label": "Read only",
     "blurb": "It can look at everything and change nothing."},
    {"key": "none", "label": "Nothing yet",
     "blurb": "Only its own memory and your day."},
]


@pytest.fixture(scope="module")
def withpresets() -> dict:
    return run(["search_brain", "remember"], GROUPED, [], groups=SPECS,
               presets=PRESETS)


def test_the_presets_are_offered_above_the_switches(withpresets):
    for key in ("all", "read", "none"):
        assert f'data-preset="{key}"' in withpresets["html"]


def test_each_preset_says_what_it_includes_on_the_button(withpresets):
    """On the button, not in a tooltip. "Allow everything" includes running
    code, and a control that hid that would be the worst possible version of
    this control."""
    assert "running code on this Mac" in withpresets["html"]
    assert "change nothing" in withpresets["html"]


def test_every_group_offers_allow_all(withpresets):
    """Per group, because the real answer is usually about one of them: let it
    have websites, leave the Mac alone."""
    assert withpresets["html"].count("Allow all") >= 2


def test_the_always_on_group_offers_no_allow_all(withpresets):
    """There is nothing to allow — it is already on, and a button that changed
    nothing would teach that buttons change nothing."""
    start = withpresets["html"].index("Its own memory and your day")
    end = withpresets["html"].index("Websites")

    assert "Allow all" not in withpresets["html"][start:end]


def test_a_preset_sends_its_NAME_not_a_list_of_tools():
    """"Allow everything" has to mean everything *now*. A screen open while a
    tool shipped would otherwise send its own stale idea of the word and
    quietly withhold the new one."""
    out = run(["search_brain"], GROUPED, [], groups=SPECS, presets=PRESETS,
              preset="all")

    assert out["presetUsed"], "the preset button never ran"
    assert out["presetUsed"]["sent"] == {"preset": "all"}
    assert "tools" not in out["presetUsed"]["sent"]


def test_a_preset_is_one_request():
    out = run(["search_brain"], GROUPED, [], groups=SPECS, presets=PRESETS,
              preset="read")

    assert out["presetUsed"]["patches"] == 1


def test_allow_all_on_a_group_turns_on_everything_in_it():
    """It reuses the bucket switch, so it is one PATCH for the whole group
    rather than one per tool."""
    out = run(["search_brain"], GROUPED, [], groups=SPECS, presets=PRESETS,
              bulk="browse_open browse_read browse_click")

    assert out["bulked"]["patches"] == 1
    assert set(out["bulked"]["sent"]) >= {"browse_open", "browse_read",
                                          "browse_click"}


def test_an_older_server_without_presets_still_renders():
    """Additive. A panel that went blank because a field was absent would be a
    worse failure than the one being fixed."""
    out = run(["search_brain"], GROUPED, [], groups=SPECS, presets=[])

    assert out["html"].strip()
    assert "data-preset" not in out["html"]
    assert 'data-tool="search_brain"' in out["html"]


# ── where the other half of a permission is set ──────────────────────────
#
# "there is no option where i can give access to browser in agent and tools
# section" — there was, under a heading that said "Websites", and flipping it
# still would not have worked: WHICH sites an agent may touch is a list kept on
# the Connectors screen, and this panel never said so. A user who turned every
# switch here on and was still refused had nowhere to go next.
LINKED = [
    {"key": "on_device", "label": "Its own memory and your day", "always": True,
     "blurb": "Nothing here leaves this machine.",
     "read": ["search_brain"], "change": ["remember"], "run": []},
    {"key": "websites", "label": "Websites and the browser", "always": False,
     "blurb": "Opening pages in a real browser, on sites you have allowed.",
     "more": {"screen": "connectors", "label": "Choose which sites"},
     "read": ["browse_open", "browse_read"], "change": ["browse_click"], "run": []},
    {"key": "mac", "label": "Your Mac", "always": False,
     "blurb": "Files and folders on this computer.",
     "read": [], "change": [], "run": ["run_python"]},
]


@pytest.fixture(scope="module")
def linked() -> dict:
    return run(["search_brain", "remember"], GROUPED, [], groups=LINKED)


def test_a_group_says_where_the_rest_of_its_permission_is_set(linked):
    """A switch here grants the agent nothing on its own."""
    assert 'data-screen="connectors"' in linked["html"]
    assert "Choose which sites" in linked["html"]


def test_that_button_opens_the_screen_it_names(linked):
    """A control that goes nowhere is the dead end this closes, not a new one."""
    assert linked["screens"] == ["connectors"]


def test_a_group_with_nowhere_else_to_go_draws_no_such_button(linked):
    """"Your Mac" has no second screen, and a button that led nowhere would
    teach that these buttons lead nowhere."""
    start = linked["html"].index("Your Mac")
    assert "data-screen" not in linked["html"][start:]


def test_an_older_server_that_names_no_screen_still_renders():
    out = run(["search_brain"], GROUPED, [], groups=SPECS)

    assert out["html"].strip()
    assert "data-screen" not in out["html"]


# ── the screen must not argue with itself ────────────────────────────────
def test_turning_one_tool_on_moves_the_switch_above_it():
    """The other direction was handled from the start — a bucket switch patches
    the rows under it — and this one was not, so a tool switched on inside the
    disclosure left the switch over it reading a plain "off"."""
    out = run(["search_brain", "remember"], GROUPED, [], groups=SPECS,
              toggle="browse_open")

    reading = next(g for g in out["groupAfter"]
                   if g["bulk"] == "browse_open browse_read")
    assert reading["some"] is True, reading
    assert reading["part"] == "1 of 2 on", reading


def test_turning_the_last_tool_of_a_bucket_on_fills_its_switch():
    out = run(["search_brain", "browse_open"], GROUPED, [], groups=SPECS,
              toggle="browse_read")

    reading = next(g for g in out["groupAfter"]
                   if g["bulk"] == "browse_open browse_read")
    assert reading["on"] is True
    assert reading["part"] == "", "it is whole, so there is nothing to count"


def test_allow_all_says_what_it_would_do_now():
    """It is a button, so it says the state in words rather than in a knob —
    and a button still offering "Allow all" over a fully granted group is the
    same lie as a stale switch."""
    out = run(["search_brain", "remember", "browse_open", "browse_read"],
              GROUPED, [], groups=SPECS, toggle="browse_click")

    allbtn = next(g for g in out["groupAfter"]
                  if set(g["bulk"].split()) == {"browse_open", "browse_read",
                                                "browse_click"})
    assert allbtn["label"] == "Turn all off", allbtn


# ── a repaint must not undo what the user opened ─────────────────────────
def test_a_disclosure_the_user_opened_survives_a_repaint():
    """A preset repaints the whole panel, and every list somebody had opened to
    decide with closed under them."""
    out = run(["search_brain"], GROUPED, [], groups=SPECS, openGroup="Websites")

    assert out["reopened"] is True


# ── the row says where you are, not only where you could go ──────────────
def test_the_preset_that_is_already_true_is_marked():
    """Three buttons that look identical whatever the agent holds mean the only
    way to tell "read only" from "read only, already applied" is to count
    switches — the thing this row replaced."""
    previewed = [{**p, "tools": ["search_brain"]} if p["key"] == "read" else p
                 for p in PRESETS]
    out = run(["search_brain"], GROUPED, [], groups=SPECS, presets=previewed)

    assert 'aria-pressed="true"' in out["html"]
    assert "is-current" in out["html"]


def test_no_preset_is_marked_when_none_of_them_is_true():
    previewed = [{**p, "tools": ["search_brain"]} if p["key"] == "read" else p
                 for p in PRESETS]
    out = run(["search_brain", "browse_click"], GROUPED, [], groups=SPECS,
              presets=previewed)

    assert 'aria-pressed="true"' not in out["html"]


def test_a_server_that_sends_no_preview_marks_nothing_rather_than_guessing():
    out = run(["search_brain"], GROUPED, [], groups=SPECS, presets=PRESETS)

    assert 'aria-pressed="true"' not in out["html"]
    assert 'data-preset="all"' in out["html"], "the presets still render"
