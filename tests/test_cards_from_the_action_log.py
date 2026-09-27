"""A card is settled by what ran, not by the screen remembering to say so.

The first version of this made the frontend responsible: on confirm it POSTed
"done" against the card's key. That works only for cards confirmed *after* it
shipped. Every card the user had already approved — which is all of them —
had nothing to recall, so the screen kept offering to create automations that
had existed for a day. Checking the user's own machine is what showed it:
`action_cards` held **0 rows** while `action_log` held the three approvals the
screenshots were complaining about.

The record of what happened was already on disk. `actions.run_now()` is the one
chokepoint every action passes through and it writes `action_log` — attended or
not, chat or routine. So the endpoint answers with what this agent actually
ran, and the card matches itself against it.

The explicit `action_cards` row still exists and still wins, because it holds
the one thing the log can never know: a card the user **cancelled** never ran,
so it leaves no trace anywhere else.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chitragupta import action_log
from chitragupta.agents import cards
from chitragupta.api.app import app

AGENTS = ("health", "chief-of-staff")

ROUTINE = {"name": "Running kit check when Dev messages on WhatsApp",
           "trigger": "schedule", "interval_min": 15,
           "agent": "chief-of-staff", "instruction": "Open web.whatsapp.com…"}


@pytest.fixture
def client():
    """`CHITRAGUPTA_HOME` is session-scoped, so these clear their own rows."""
    for agent in AGENTS:
        cards.forget_agent(agent)
    _wipe_log()
    yield TestClient(app)
    for agent in AGENTS:
        cards.forget_agent(agent)
    _wipe_log()


def _wipe_log():
    conn = action_log._conn()
    conn.execute("DELETE FROM action_log")
    conn.commit()


def ran(action_type="create_routine", params=None, *, ok=True,
        agent_id="health", detail="Automation created"):
    return action_log.record(
        action_type, params if params is not None else ROUTINE,
        {"ok": ok, "detail": detail}, agent_id=agent_id, summary=detail)


def fetch(client, agent="health"):
    r = client.get(f"/api/agents/{agent}/cards")
    assert r.status_code == 200, r.text
    return r.json()


# ── what actually ran comes back ───────────────────────────────────────────

def test_an_action_this_agent_ran_is_reported(client):
    """The bug, in one assertion. Nothing recorded this card, and the card was
    still drawn pending — over an automation that had existed for a day."""
    ran()
    assert fetch(client)["ran"], "the card has nothing to match itself against"


def test_it_carries_what_the_card_needs_to_draw_itself(client):
    ran()
    entry = fetch(client)["ran"][0]

    assert entry["type"] == "create_routine"
    assert entry["ok"] is True
    assert entry["detail"] == "Automation created"
    # The params are how a card recognises its own action — there is no id
    # shared between a stored message and a log row.
    assert entry["params"]["name"] == ROUTINE["name"]


def test_another_agent_s_actions_are_not_reported(client):
    """A card in one conversation must not settle from another's work."""
    ran(agent_id="chief-of-staff")
    assert fetch(client, "health")["ran"] == []


def test_a_failed_action_is_reported_as_failed(client):
    """It keeps its buttons, so the card has to be able to tell the
    difference — a failure drawn as done is worse than one drawn pending."""
    ran(ok=False, detail="Google said no")
    entry = fetch(client)["ran"][0]
    assert entry["ok"] is False
    assert "Google said no" in entry["detail"]


def test_two_identical_actions_are_two_entries(client):
    """Two identical proposals in one conversation are two cards, and each has
    to be able to claim its own. Collapsing them leaves the second pending."""
    ran()
    ran()
    assert len(fetch(client)["ran"]) == 2


def test_an_undone_action_is_not_reported_as_done(client):
    """Undo exists. A card drawn "done" over something the user took back is
    the app disagreeing with them about their own machine."""
    entry_id = ran()
    action_log.mark_undone(entry_id)
    assert fetch(client)["ran"] == []


# ── the explicit record still wins ─────────────────────────────────────────

def test_a_cancelled_card_is_still_remembered(client):
    """The one thing the log cannot know: a cancelled card never ran, so there
    is nothing to derive it from. This is why the table stays."""
    cards.remember("health", "k1", state=cards.CANCELLED)
    body = fetch(client)
    assert body["cards"]["k1"]["state"] == "cancelled"


def test_an_empty_conversation_answers_with_both_shapes(client):
    """The frontend reads both keys on every history load, including the
    first."""
    body = fetch(client, "chief-of-staff")
    assert body == {"cards": {}, "ran": []}
