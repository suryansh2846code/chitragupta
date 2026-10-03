"""The agent profile popup, driven through `tests/js/agent_profile.mjs`.

The popup is where an agent's instructions, its memory, its model, its
permissions and its deletion all live, so most of what is asserted here is that
each of those reaches the right endpoint — a tab that silently does nothing is
indistinguishable from one that works until the day somebody checks.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent
APP_JS = ROOT / "chitragupta" / "web" / "app.js"
HARNESS = ROOT / "tests" / "js" / "agent_profile.mjs"


@pytest.fixture(scope="module")
def report():
    proc = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert not data.get("error"), data["error"]
    return data


def test_the_popup_markup_is_in_the_page(report):
    """Not injected by `profile.js`. The focus observer and the Escape handler
    in `app.js` bind to every `.modal-bg` once, at load — an overlay added
    afterwards opens without taking focus and never closes on Escape."""
    assert report["markupInPage"]


def test_the_row_control_opens_the_profile(report):
    assert report["moreIsBound"], "the ⋯ on an agent row has no handler"
    opened = report["opened"]
    assert opened["shown"]
    assert opened["title"] == "Inbox", "it opened on the wrong agent"
    assert opened["firstPaneDrawn"], "the dialog opened on an empty pane"


def test_it_opens_on_appearance(report):
    """Press the dots on a face and the face is what you get.

    The landing tab is read off `PROF_TABS[0]` rather than written out a second
    time, so the tab the rail shows first and the tab the popup opens on are
    the same fact — reordering the rail cannot leave the landing tab behind.
    """
    opened = report["opened"]
    assert opened["landedOn"] == "appearance"
    assert opened["firstTabLabel"] == "Appearance"


def test_every_tab_draws_something(report):
    """A tab that renders an empty pane looks fine in review and is a dead
    screen in the app. Parametrised over the whole set rather than the one
    being worked on, which is how the last two of these were missed."""
    for tab, children in report["tabs"].items():
        assert children > 0, f"the {tab} tab drew nothing"


def test_the_persona_tab_offers_choices_rather_than_a_blank_box(report):
    """It was a textarea asking a non-technical person to compose a system
    prompt, which is a box most people close again."""
    persona = report["persona"]
    assert persona["chips"] == 7, "the chip groups did not render"
    assert persona["levels"] == 3, "the autonomy levels did not render"


def test_the_options_come_from_the_server(report):
    """A copy of these lists in JavaScript would be a second copy to keep
    current, and the one that drifts is the one somebody is choosing from."""
    assert report["persona"]["chipLabels"] == [
        "Supportive", "Warm", "Professional",      # traits
        "Concise", "Bullet points",                # communication
        "Analytical", "First principles",          # thinking
    ]


def test_what_was_already_chosen_comes_back_on(report):
    """A picker that forgets its own state reads as a picker that did not
    save."""
    assert report["persona"]["onAtLoad"] == ["Warm"]
    assert report["persona"]["levelOnAtLoad"] == 1, "the stored level is not lit"


def test_the_free_text_field_holds_what_was_written(report):
    assert report["persona"]["loaded"] == "Be brief."


def test_choosing_an_autonomy_level_replaces_the_last_one(report):
    """Three levels lit at once is not a choice. It happened: clearing the
    others by searching the DOM for them found nothing, so the first stayed
    on."""
    assert report["persona"]["levelsOnAfterPick"] == 1


def test_saving_sends_every_choice_together(report):
    """One document is rendered from all of them server-side, so a save that
    sent only the field that changed would render the rest away."""
    saved = report["persona"]["saved"]
    assert saved and saved["method"] == "PUT"
    assert saved["url"].endswith("/persona")
    body = json.loads(saved["body"])
    assert body["traits"] == ["Warm", "Professional"]
    assert body["autonomy"] == "on_its_own"
    assert body["extra"] == "Answer only in haiku."


def test_forgetting_deletes_the_file_rather_than_saving_a_blank_one(report):
    """`None` and `""` mean different things to the server: no file is "use the
    default", an empty file is "the user cleared it". Saving a blank would make
    *forget* and *clear* the same act."""
    deleted = report["memory"]["deleted"]
    assert deleted and deleted["method"] == "DELETE"
    assert deleted["url"].endswith("/files/memory.md")


def test_a_full_memory_says_so_and_says_what_it_costs(report):
    """A note refused for want of room is silent otherwise — the agent simply
    stops learning, and nothing anywhere would say why."""
    meter = report["memory"]["meter"]
    assert "Full" in meter
    assert "cannot learn anything new" in meter


def test_removing_a_preset_is_not_the_same_act_as_deleting_an_agent(report):
    """One keeps the conversation and everything learned; the other destroys
    both. Two acts, so two questions and two endpoints."""
    preset, custom = report["danger"]["preset"], report["danger"]["custom"]
    assert preset["url"] == "/api/agents/roster/inbox"
    assert custom["url"] == "/api/agents/custom/chotu"
    assert "Nothing it learned is deleted" in preset["asked"]
    assert "memory and conversation go with it" in custom["asked"]


def test_closing_with_unsaved_work_stops_to_ask(report):
    guard = report["guard"]
    assert guard["asked"], "it closed without asking"
    assert guard["stayedOpen"], "it closed anyway after the user said no"
    assert guard["closedWhenAllowed"]


def test_the_avatar_editor_mounts_into_the_popup(report):
    """Not into the settings panel it used to live in, which no longer exists.
    `appearance.js` reached its parts by id while there was exactly one of each
    on the page; they are built per pane now, so it holds them instead."""
    ap = report["appearance"]
    assert ap["mounted"], "the editor was never mounted"
    assert ap["gotADocument"], "the editor was mounted without a character"
    assert ap["insidePane"]


def test_an_unsaved_avatar_is_defended_on_close(report):
    """The editor tracks its own `apDirty`, which is a different flag from the
    profile's. Asking only about the profile's would let somebody close the
    dialog on a half-built character — the tab where the work is hardest to
    redo."""
    assert report["appearance"]["guarded"]


def test_the_settings_rail_no_longer_offers_either_of_them(report):
    """Both are tabs in the profile now. A rail item and a tab for the same
    thing is the duplication this move exists to remove — and the panel left
    behind would be the copy that drifts."""
    removed = report["removed"]
    assert removed["toolsNav"] and removed["toolsPanel"]
    assert removed["appearanceNav"] and removed["appearancePanel"]


def test_permissions_draws_into_the_popups_own_container(report):
    """Handing `renderAgentTools` the Settings panel's `#agentToolList` reads
    identically and renders nothing inside the dialog."""
    perms = report["perms"]
    assert perms["gotABox"], "the permissions tab never called the renderer"
    assert perms["isOwnBox"]
    assert perms["boxId"] == "profToolList"
