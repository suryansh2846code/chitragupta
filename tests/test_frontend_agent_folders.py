"""The composer's folder pill, driven through `tests/js/agent_folders.mjs`.

The control it replaced said "workspace" and opened the Connectors screen — a
different thing with the opposite blast radius, since folders added there are
read into the brain every agent shares. This one is one agent's own.

So what is asserted is the seam rather than the drawing: whose folders it
shows, what it sends, that several can be on at once, that "look at no files"
survives as an answer, and that one modal serving two actions never describes
the wrong one.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent
APP_JS = ROOT / "chitragupta" / "web" / "app.js"
HARNESS = ROOT / "tests" / "js" / "agent_folders.mjs"


@pytest.fixture(scope="module")
def report():
    proc = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert not data.get("error"), data["error"]
    return data


# ── it is a control, and it is in the page ───────────────────────────────
def test_the_pill_and_its_menu_are_in_the_markup(report):
    """The old one had no menu and no handler worth the name — it opened a
    screen about something else, which is a control that does nothing in the
    only sense the user can check."""
    assert report["markup"]["pill"]
    assert report["markup"]["menu"], "the menu does not ship hidden"
    assert report["markup"]["oldPillGone"], "the dead pill is still in the page"


def test_it_says_nothing_until_it_knows(report):
    """A pill reading "No folders" over an agent whose list has not arrived is
    a control reporting a guess — and that guess reads as "this agent is cut
    off from your files", which is the worst one it could make."""
    assert report["hidden"]["beforeAnyAgent"]
    assert report["hidden"]["inPage"], "the markup does not ship it hidden"


def test_it_shows_the_open_agents_folders(report):
    loaded = report["loaded"]
    assert loaded["shown"]
    assert loaded["label"] == "notes"
    # A count is not an answer to "which ones", so the whole list is on the
    # control where the label can only hold a word.
    assert "/Users/x/notes" in loaded["title"]


def test_the_menu_offers_every_folder_and_ticks_this_agents(report):
    menu = report["menu"]
    assert menu["opens"]
    assert menu["rows"] == 3, "the menu did not list the folders"
    assert menu["on"] == ["/Users/x/notes"]


def test_the_menu_says_this_is_not_the_shared_brain(report):
    """The other folder control in this app does the opposite thing, and the
    two were one word apart on screen."""
    assert report["menu"]["saysItIsJustThisAgent"]


# ── more than one, which is the whole point ──────────────────────────────
def test_a_second_folder_can_be_added_without_losing_the_first(report):
    ticked = report["ticked"]
    assert ticked["url"].endswith("/api/agents/inbox/folders")
    assert ticked["body"] == {"folders": ["/Users/x/notes", "/Users/x/work/tax"]}
    assert ticked["label"] == "2 folders"


def test_the_menu_stays_open_while_several_are_being_picked(report):
    """Closing on the first tick would make choosing three folders three trips."""
    assert report["ticked"]["menuStaysOpen"]


def test_a_folder_can_be_taken_away_again(report):
    assert report["unticked"]["body"] == {"folders": ["/Users/x/work/tax"]}
    assert report["unticked"]["label"] == "tax"


# ── "nothing" is an answer, and it is not the same as "unset" ────────────
def test_look_at_no_files_sends_an_empty_list_rather_than_null(report):
    """`null` reads back as *undecided*, and an undecided agent follows
    whatever was already open — so sending it would leave the agent quietly
    reading files after the user said not to."""
    none = report["none"]
    assert none["sentAList"], "it sent null, which means the opposite"
    assert none["body"] == {"folders": []}
    assert none["label"] == "No folders"


def test_an_agent_never_asked_follows_what_was_already_open(report):
    """Not the same as having nothing. An upgrade must not silently take away
    the files a machine could already read."""
    assert report["perAgent"]["chotuIsUndecided"]
    assert report["perAgent"]["chotu"] == "2 folders"


# ── one browser, two actions, neither mislabelled ────────────────────────
def test_browsing_adds_to_what_the_agent_already_has(report):
    browse = report["browse"]
    assert browse["modalOpen"]
    assert browse["body"] == {"folders": ["/Users/x/notes", "/Users/x"]}, \
        "the press said 'and this one too' and it replaced instead"
    assert browse["url"].endswith("/api/agents/inbox/folders")
    assert browse["modalClosed"]


def test_the_browser_says_which_of_the_two_things_it_is_about_to_do(report):
    """One modal, two outcomes with opposite blast radius. A button reading
    "Give it this folder" over a flow that ingests a thousand files into the
    shared brain is the card describing something other than what it runs."""
    assert report["browse"]["title"] == "Pick a folder for this agent"
    assert report["browse"]["cta"] == "Give it this folder"
    assert report["ingest"]["title"] == "Pick a folder to ingest"
    assert report["ingest"]["cta"] == "Ingest this folder"


# ── it never reports something it was not told ───────────────────────────
def test_a_refused_save_puts_the_label_back(report):
    """A pill left showing a folder the server refused is a control claiming
    the agent can read something it cannot."""
    assert report["failed"]["after"] == report["failed"]["before"]


# ── and the profile tab is the same setting, not a second one ────────────
def test_the_profile_tab_draws_the_same_folders(report):
    pane = report["pane"]
    assert pane["rows"] == 3
    assert pane["hasAdd"], "there is no way to add a folder from the tab"


def test_the_tab_says_which_of_the_three_answers_is_current(report):
    assert report["pane"]["state"] == "Reading 2 folders."


def test_the_tab_and_the_pill_can_be_about_different_agents(report):
    """The rail's ⋯ opens any agent's profile without selecting it, and
    creating an agent opens its profile while the chat is still on somebody
    else. One shared "which agent" repainted the composer — the control you are
    about to send a message from — with another agent's folders."""
    assert report["pane"]["pillUntouched"], \
        "opening another agent's Folders tab moved the composer pill"
    assert report["pane"]["pillWas"] == "notes"


def test_a_press_in_the_tab_writes_the_tabs_agent(report):
    save = report["paneSave"]
    assert save["url"].endswith("/api/agents/chotu/folders"), \
        "the tab saved to the agent the chat was on, not the one it is showing"
    assert save["pillStillUntouched"]
