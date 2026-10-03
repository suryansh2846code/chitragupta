"""The ask arrives as switches in the chat, not as a route to a settings screen.

Measured, verbatim, from a real conversation: *"Browsing is still not granted —
I checked just now, same block. Settings → Agents & tools → Health & Fitness →
let it browse the web, plus allow changes on `amazon.in`. Say 'go' when it's
on."*

Nothing there is false. It is still the failure: it ends the conversation, hands
the user a path to re-derive the decision they were already being asked to make,
and asks them to come back and say "go".

`node --check` passes on every version of that card, and so does an assertion
about the source. So the harness runs the real parser over the real tag, builds
the real card, and presses the switch the renderer actually bound.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

#: What the agent writes. The exact ask from the conversation above: a tool
#: group it has, a website it does not, and an account that is not a switch.
TAG = ('Here is where we are.\n'
       '<action type="request_permission" '
       'needs="websites:read, site:amazon.in:change, connector:gmail">'
       'I need to open amazon.in and build your ten-item basket.</action>\n'
       'Tell me when.')

#: What the server resolves it to. Built here rather than fetched, because this
#: is about the panel; `test_access.py` is about the resolution.
ROWS = [
    {"key": "tool_group:websites:read", "kind": "tool_group",
     "title": "Read websites", "means": "Open pages in a real browser.",
     "granted": True, "control": "switch"},
    {"key": "site:amazon.in:change", "kind": "site",
     "title": "Click and type on amazon.in",
     "means": "Let agents click, type and submit on amazon.in.",
     "granted": False, "control": "switch"},
    {"key": "connector:gmail", "kind": "connector", "title": "Connect Gmail",
     "means": "Signing in happens in its own window.",
     "granted": False, "control": "opens", "screen": "connectors"},
]
AFTER = [{**r, "granted": True} if r["key"] == "site:amazon.in:change" else r
         for r in ROWS]


def run(**over) -> dict:
    payload = {"tag": TAG, "rows": ROWS, "afterGrant": AFTER, **over}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/access_card.mjs"), str(WEB / "app.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


@pytest.fixture(scope="module")
def drawn() -> dict:
    return run(press="site:amazon.in:change")


# ── what the model wrote reaches the card ────────────────────────────────
def test_the_reason_the_agent_gave_is_on_the_card(drawn):
    """**The whole point of the ask, and it was being dropped.** The protocol
    puts the sentence in the tag's body; the card asks for `params.why`; nothing
    carried one to the other, on either side. The row rendered empty."""
    assert drawn["why"] == "I need to open amazon.in and build your ten-item basket."


def test_the_action_tag_leaves_the_prose_alone(drawn):
    assert drawn["clean"] == "Here is where we are.\n\nTell me when."


# ── it is a panel, not a question ────────────────────────────────────────
def test_there_is_no_confirm_button(drawn):
    """A Confirm would make "allow two of these three" impossible to express,
    which is the case the panel exists for. Each switch IS the decision."""
    assert drawn["hasConfirm"] is False


def test_every_thing_asked_for_gets_a_row(drawn):
    assert drawn["titles"] == ["Read websites", "Click and type on amazon.in",
                               "Connect Gmail"]


def test_a_row_is_a_sentence_not_a_pair_of_identifiers(drawn):
    """`websites:change` on a switch asks a person to consent to two
    identifiers. The key is how the press is addressed; the sentence is the
    control."""
    for title in drawn["titles"]:
        assert ":" not in title, title


def test_what_is_already_on_is_shown_as_on_and_not_as_a_switch(drawn):
    """The grant stays in the list — the panel is a picture of what this agent
    may do, and one that dropped what it had already given would stop answering
    the question somebody opened it with. It is not a switch, because taking a
    permission back belongs where the user can see what else it sits beside."""
    assert drawn["onCount"] == 1
    assert drawn["onRows"] == ["tool_group:websites:read"]
    assert "tool_group:websites:read" not in drawn["switches"]


def test_an_account_offers_its_own_setup_rather_than_a_switch(drawn):
    """Google wants a sign-in window, Telegram wants credentials typed, an MCP
    server wants a URL. A switch would be a control that cannot work."""
    assert drawn["opens"] == ["connectors"]
    assert "connector:gmail" not in drawn["switches"]


# ── pressing one ─────────────────────────────────────────────────────────
def test_a_press_grants_exactly_the_key_it_was_drawn_for(drawn):
    grant = [p for p in drawn["posted"] if p["path"].endswith("/access/grant")]
    assert len(grant) == 1
    assert grant[0]["body"]["key"] == "site:amazon.in:change"


def test_a_press_sends_the_ask_back_so_the_server_can_check_it(drawn):
    """The server re-parses the card's own `needs` and refuses a key that is not
    in it. Without this the endpoint is a general-purpose "grant anything"
    route, and a page an agent is reading could try to address it."""
    grant = next(p for p in drawn["posted"]
                 if p["path"].endswith("/access/grant"))
    assert grant["body"]["needs"] == ("websites:read, site:amazon.in:change, "
                                      "connector:gmail")


def test_the_panel_repaints_from_what_the_server_read_back(drawn):
    """Never from what the client hoped it sent. A grant that silently did not
    land has to show as off, which is the same rule the tools panel follows."""
    assert drawn["afterOn"] == 2
    assert drawn["afterSwitches"] == []
    assert drawn["rowError"] == ""


def test_a_failed_grant_says_so_on_the_row_that_failed():
    """Not in a toast that has gone by the time the eye gets back to the panel.
    And the switch goes back to being pressable — the permission was not given,
    so the control that gives it must still work."""
    out = run(press="site:amazon.in:change", fail="amazon.in is not a website.")

    assert out["rowError"] == "amazon.in is not a website."
    assert out["stillDisabled"] is False
    assert out["afterOn"] == 1, "a failure must not paint the row as granted"
