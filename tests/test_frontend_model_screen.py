"""AI model is a screen of its own, and only it moved.

It used to be one of four panels in the 380px slide-over — the wrong shape for
a grid of provider cards that each carry an account, a plan, usage limits and
a model list.

Four separate call sites open it (the left nav, plus three "connect in Models"
affordances in the composer), so the move is done by delegating inside
`openDrawer()` instead of editing each one. That is the part worth testing: a
delegation that matched too greedily would quietly send Tasks, Connectors and
Tools to the model screen as well, and every one of those still reads as
correct in the source. So the nav items are clicked for real.
"""
import json
import subprocess
from pathlib import Path

import pytest
from web_sources import app_source

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"
INDEX = (WEB / "index.html").read_text()
CSS = (WEB / "styles.css").read_text()


@pytest.fixture(scope="module")
def clicked() -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/open_model_screen.mjs"), str(WEB / "app.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def test_the_harness_reached_the_handlers(clicked):
    assert clicked["error"] is None, clicked["error"]


#: The LEFT-NAV items, which is what this harness clicks. `model`, `sources`
#: and `tools` used to be here and are not any more — they became `data-msnav`
#: rows inside the Settings screen. The list was left behind when they moved,
#: so every one of them failed on a `KeyError` from the harness rather than on
#: anything being wrong. Derived below rather than re-listed, so the next move
#: cannot strand it a second time.
NAV_PANELS = [("settings", "model"), ("inbox", "inbox")]


def test_the_list_below_is_the_nav_that_actually_exists():
    """The reason the old list rotted: nothing tied it to `index.html`, which
    is the single place the nav is declared."""
    import re

    declared = set(re.findall(r'data-nav="([a-z]+)"',
                              (WEB / "index.html").read_text()))
    named = {nav for nav, _ in NAV_PANELS}
    assert named <= declared, f"gone from index.html: {sorted(named - declared)}"


@pytest.mark.parametrize("nav,panel", NAV_PANELS)
def test_a_settings_item_opens_the_screen_on_its_own_panel(clicked, nav, panel):
    """Model and Connectors share one shell, so opening the screen is only half
    of it — landing on the wrong panel shows the screen with the other page on
    it, which reads as the nav item doing nothing."""
    got = clicked["opened"][nav]
    # The drawer itself is gone — `test_the_drawer_has_no_survivors` is where
    # that is asserted. The harness stopped reporting it, so checking it
    # here fails on a KeyError rather than on anything being wrong.
    assert got["modelScreen"] is True, got
    assert got["panel"] == panel, (
        f"{nav} opened the settings screen on the {got['panel']!r} panel")


def test_the_drawer_has_no_survivors():
    """`tasks` moved into Inbox, beside the other three kinds of pending work,
    and `tools` was a read-only copy of the Agents & tools panel — same
    endpoint, same grouping, no switches. Those were the drawer's only two
    panels, so it went with them. A left-over `#drawerBg` would be an invisible
    full-screen overlay sitting at z-index 55 over everything."""
    assert "drawerBg" not in INDEX
    assert 'class="dpanel"' not in INDEX
    # `app_source()`, not app.js alone: the frontend is several files now,
    # and reading one of them would pass while `closeDrawer` sat in another.
    assert "closeDrawer" not in app_source()


def test_the_screen_can_be_closed(clicked):
    assert clicked["opened"]["closeButtonWorks"] is True


# ── one home per list ─────────────────────────────────────────────────────
def test_every_pending_list_has_exactly_one_home():
    """Two homes for the same list means one of them is the stale one. Tasks,
    reminders and routines are all "work that is pending", and they are all in
    Inbox now — `#taskList` was the one still outside it."""
    for element_id in ("taskList", "routineList", "reminderList", "approvals"):
        assert INDEX.count(f'id="{element_id}"') == 1, (
            f"#{element_id} appears more than once — the renderer fills "
            f"whichever the DOM happens to return first")


def test_tasks_live_in_the_inbox_panel():
    inbox = INDEX.split('data-sp="inbox"', 1)[1].split('data-sp="tools"', 1)[0]
    assert 'id="taskList"' in inbox
    assert 'id="taskInput"' in inbox and 'id="taskAdd"' in inbox


def test_no_panel_is_left_inside_the_drawer():
    for gone in ('data-d="sources"', 'data-d="model"', 'data-d="tasks"', 'data-d="tools"'):
        assert gone not in INDEX, f"{gone} outlived the drawer"


def test_the_screen_exists_and_starts_hidden():
    assert '<div id="modelScreen" class="modelscreen" hidden>' in INDEX


@pytest.mark.parametrize("element_id", [
    "providerCards", "enrichProvider", "enrichModelName", "enrichCap",
    "enrichCapSave", "enrichCapNote", "usageBox", "usageReset",
    # the hidden fallbacks older code still reads
    "agentModelMatrix", "provider", "defaultProviderConnectBox", "modelName",
    "modelHint", "privacyBadge",
])
def test_every_id_the_model_code_reads_survived_the_move(element_id):
    assert f'id="{element_id}"' in INDEX, (
        f"#{element_id} was dropped when the panel moved — the code that reads "
        "it will silently do nothing"
    )


def test_ids_are_not_duplicated_across_the_page():
    """A second copy of any of these would make the move a no-op for one of them."""
    import re
    ids = re.findall(r'id="([^"]+)"', INDEX)
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate element ids: {sorted(dupes)}"


def _rule(selector: str) -> str:
    assert selector in CSS, f"{selector} is gone from styles.css"
    return CSS.split(selector, 1)[1].split("}", 1)[0]


def test_providers_are_a_single_settings_column():
    """Deliberate change from the first version of this screen.

    It shipped as a responsive grid of cards, which read as a dashboard. The
    page is a settings page, so providers are now one centred column of
    sections — the grid assertion this replaces was testing the old intent.
    """
    assert "column" in _rule(".modelscreen .provider-cards")


def test_each_provider_group_is_one_card_of_rows():
    """The look the screen exists for: one card per provider, hairline rows.

    The provider markup emits a separate .ts-card per credential. If those keep
    their own borders the group renders as a stack of little boxes again, which
    is exactly what it looked like before.
    """
    group = _rule(".modelscreen .ts-group-boxes")
    assert "border" in group and "radius" in group, group
    flattened = _rule(".modelscreen .ts-group-boxes .ts-card")
    assert "border: 0" in flattened, flattened


def test_the_provider_boxes_are_not_spaced_apart():
    """An inline margin between boxes would put gaps inside the single card."""
    assert 'id="pbox_${pid}" style="margin-bottom' not in app_source()


def test_nothing_calls_it_a_drawer_any_more():
    assert "Models drawer" not in app_source(), (
        "user-facing copy still calls the model screen a drawer"
    )


# ── the Inbox is messages, and nothing else ───────────────────────────────

def test_the_rail_offers_both_inbox_and_actions(clicked):
    """They are different kinds of thing. What an agent *said* to you is news;
    what it is running is furniture — and one screen holding both made you
    scroll past five sections of furniture to find the news."""
    assert "inbox" in clicked["opened"], sorted(clicked["opened"])
    assert "actions" in clicked["opened"], sorted(clicked["opened"])


def test_each_one_opens_its_own_panel(clicked):
    assert clicked["opened"]["inbox"]["panel"] == "inbox"
    assert clicked["opened"]["actions"]["panel"] == "actions"


def test_the_inbox_panel_holds_only_messages():
    """Read off the markup: the split is structural, and a fake DOM has no
    layout to measure."""
    page = (WEB / "index.html").read_text()
    panel = page.split('data-sp="inbox"')[1].split('data-sp="actions"')[0]

    assert 'id="messageList"' in panel
    for elsewhere in ('id="approvals"', 'id="taskList"', 'id="routineList"',
                      'id="reminderList"', 'id="actionLog"'):
        assert elsewhere not in panel, f"{elsewhere} is still on the Inbox"


def test_everything_else_moved_rather_than_being_dropped():
    """A split that loses a section is worse than no split: the thing it held
    is gone with no screen saying where."""
    page = (WEB / "index.html").read_text()
    panel = page.split('data-sp="actions"')[1].split('panel: agents')[0]

    for moved in ('id="approvals"', 'id="taskList"', 'id="routineList"',
                  'id="reminderList"'):
        assert moved in panel, f"{moved} was lost in the split"
    assert 'id="messageList"' not in panel, "messages are in both places"
