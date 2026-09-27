"""What a card settled as, remembered past the page that drew it.

The outcome of an action card only ever lived in the DOM, so reopening a
conversation rebuilt every card pending — an automation created an hour ago came
back offering **Confirm & create**. The obvious thing to do with that button is
press it, and the result is a second automation.

These are about the store and its two endpoints. That the screen *draws* a
settled card as settled is `test_frontend_card_state.py`.
"""
from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from chitragupta.agents import cards
from chitragupta.api.app import app

KEY = "create_routine|agent=chief-of-staff&name=Kit check#1"
#: Every key ends in `#n` — the position that tells two identical proposals
#: apart. Unescaped that is a URL *fragment*, so the server would be handed
#: `…Kit check` and store the answer under a key nothing is ever drawn with.
#: The frontend encodes it; `test_the_key_is_escaped_on_the_way_out` pins that.
PATH = quote(KEY, safe="")
AGENTS = ("health", "chief-of-staff", "nobody")


@pytest.fixture
def client():
    """`CHITRAGUPTA_HOME` is session-scoped, so these clear their own rows
    rather than moving the home — a shuffled run is part of finishing, and a
    test that asserts "this conversation has no cards" against a database the
    whole session shares passes only where it happens to sit."""
    for agent in AGENTS:
        cards.forget_agent(agent)
    _wipe_log()
    yield TestClient(app)
    for agent in AGENTS:
        cards.forget_agent(agent)
    _wipe_log()


def _wipe_log():
    """The endpoint also answers with what this agent ran, and an action logged
    without an agent belongs to every conversation — so a shared log makes the
    exact assertions below depend on what ran before them."""
    from chitragupta import action_log

    conn = action_log._conn()
    conn.execute("DELETE FROM action_log")
    conn.commit()


# ── the store ──────────────────────────────────────────────────────────────

def test_a_card_with_no_row_is_pending(client):
    """`pending` is deliberately not a stored state: every card drawn before
    this existed has no row, and must still offer its buttons."""
    assert cards.get(KEY) == {}
    assert cards.for_agent("chief-of-staff") == {}


def test_what_a_card_settled_as_survives(client):
    cards.remember("chief-of-staff", KEY, state=cards.DONE,
                   detail="Automation created", log_id="L7",
                   verified_at="2026-09-26T15:42:00+05:30", reversible=True)

    got = cards.for_agent("chief-of-staff")[KEY]
    assert got["state"] == "done"
    assert got["detail"] == "Automation created"
    assert got["log_id"] == "L7"
    assert got["verified_at"].startswith("2026-09-26")
    assert got["reversible"] is True, "a bool, not sqlite's 1"


def test_answering_twice_keeps_the_second_answer(client):
    """A failed card keeps its buttons on purpose, so the retry has to be able
    to overwrite the failure. If this refused, a card that failed once could
    never be recorded as done."""
    cards.remember("health", KEY, state=cards.FAILED, detail="Gmail said no")
    cards.remember("health", KEY, state=cards.DONE, detail="Sent")

    assert cards.get(KEY)["state"] == "done"
    assert len(cards.for_agent("health")) == 1, "replaced, not appended"


def test_a_state_nobody_defined_is_refused(client):
    """The vocabulary is closed. A typo'd state stored as-is would draw a card
    with a tag nobody wrote and no way back to its buttons."""
    assert cards.remember("health", KEY, state="probably-fine") == {}
    assert cards.get(KEY) == {}


def test_a_card_without_a_key_is_refused(client):
    assert cards.remember("health", "", state=cards.DONE) == {}


def test_one_conversation_does_not_see_another_s_cards(client):
    cards.remember("health", KEY, state=cards.DONE)
    assert cards.for_agent("chief-of-staff") == {}


def test_clearing_a_conversation_takes_its_cards(client):
    """The cards belong to the messages. Leaving them behind means a later
    proposal that happens to key the same opens already marked done, for
    something nobody did."""
    cards.remember("health", KEY, state=cards.DONE)

    r = client.post("/api/agents/health/clear")
    assert r.status_code == 200
    assert cards.for_agent("health") == {}


# ── the endpoints the screen uses ──────────────────────────────────────────

def test_the_screen_can_read_and_write_one(client):
    r = client.post(f"/api/agents/health/cards/{PATH}",
                    json={"state": "done", "detail": "Sent", "log_id": "L9"})
    assert r.status_code == 200, r.text

    back = client.get("/api/agents/health/cards").json()["cards"]
    assert list(back) == [KEY], back
    assert back[KEY]["detail"] == "Sent"


def test_a_state_the_store_rejects_is_a_400_not_a_silent_no_op(client):
    """A screen and a store disagreeing about the vocabulary is worth saying
    out loud — silently accepting it means the card looks answered until the
    next reload disagrees."""
    r = client.post(f"/api/agents/health/cards/{PATH}",
                    json={"state": "probably-fine"})
    assert r.status_code == 400
    assert "probably-fine" in r.text


def test_reading_an_empty_conversation_is_not_an_error(client):
    """The frontend fetches this before every history, including the first
    one, and reads both keys off it every time."""
    r = client.get("/api/agents/nobody/cards")
    assert r.status_code == 200
    assert r.json() == {"cards": {}, "ran": []}
