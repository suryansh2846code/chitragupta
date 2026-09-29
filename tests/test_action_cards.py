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
    assert cards.get("chief-of-staff", KEY) == {}
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

    assert cards.get("health", KEY)["state"] == "done"
    assert len(cards.for_agent("health")) == 1, "replaced, not appended"


def test_we_tried_and_do_not_know_is_a_state_of_its_own(client):
    """`unknown` is what the screen records when the confirm got no reply.

    Not a shade of `failed`: a 500 from `/api/actions/execute` can arrive after
    the email has gone, so "it did not work" would be a claim nobody checked and
    a button would offer to send it twice. The frontend's `settledState` prefers
    a logged run over one of these, which is what makes it self-correcting
    rather than a dead end.
    """
    cards.remember("health", KEY, state=cards.UNKNOWN,
                   detail="Internal Server Error")

    got = cards.for_agent("health")[KEY]
    assert got["state"] == "unknown"
    assert got["detail"] == "Internal Server Error"
    assert cards.UNKNOWN in cards.SETTLED, (
        "a card with this state must not be drawn as pending — that is the "
        "duplicate the table exists to prevent")


def test_an_unknown_is_overwritten_once_the_log_settles_it(client):
    """The other half of the same idea. An action whose reply was lost and which
    really did run is recorded again as done, so the row stops disagreeing with
    the log."""
    cards.remember("health", KEY, state=cards.UNKNOWN, detail="timed out")
    cards.remember("health", KEY, state=cards.DONE, detail="Sent", log_id="L9")

    assert cards.get("health", KEY)["state"] == "done"
    assert len(cards.for_agent("health")) == 1


def test_a_state_nobody_defined_is_refused(client):
    """The vocabulary is closed. A typo'd state stored as-is would draw a card
    with a tag nobody wrote and no way back to its buttons."""
    assert cards.remember("health", KEY, state="probably-fine") == {}
    assert cards.get("health", KEY) == {}


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


# ── a card is named by its conversation as well as its key ─────────────────

def test_two_agents_can_answer_the_same_proposal_independently(client):
    """**The key is not unique on its own.** It is computed from the action, its
    parameters and its position in the conversation — nothing in it mentions the
    agent. So `key TEXT PRIMARY KEY` meant two agents proposing the same thing at
    the same point shared one row, and the second answer overwrote the first
    *including its `agent_id`*, moving the row out of the first conversation.
    """
    cards.remember("health", KEY, state=cards.CANCELLED)
    cards.remember("chief-of-staff", KEY, state=cards.DONE, detail="Created")

    assert cards.get("health", KEY)["state"] == "cancelled"
    assert cards.get("chief-of-staff", KEY)["state"] == "done"
    assert len(cards.for_agent("health")) == 1
    assert len(cards.for_agent("chief-of-staff")) == 1


def test_a_cancellation_is_the_one_that_could_not_be_recovered(client):
    """Why the shared row mattered, in the words of the state that pays for it.

    A card the user cancelled leaves no trace in the action log — that is
    exactly why the recorded answer outranks the log. Once another agent had
    taken the row, nothing was left to recognise it by, so it came back offering
    its button over something the user had explicitly declined.
    """
    cards.remember("health", KEY, state=cards.CANCELLED)
    cards.remember("chief-of-staff", KEY, state=cards.DONE)

    mine = cards.for_agent("health")
    assert KEY in mine and mine[KEY]["state"] == "cancelled", (
        "the cancellation was lost, so the card is a live proposal again")


def test_clearing_one_conversation_leaves_the_other_alone(client):
    cards.remember("health", KEY, state=cards.DONE)
    cards.remember("chief-of-staff", KEY, state=cards.DONE)

    cards.forget_agent("health")

    assert cards.get("health", KEY) == {}
    assert cards.get("chief-of-staff", KEY)["state"] == "done"


def test_a_database_written_before_this_keeps_its_answers(client, tmp_path):
    """The migration. SQLite cannot alter a primary key, so the table is rebuilt
    — and a card's answer is a fact about the world, which is the whole reason
    this is not `localStorage`. Losing rows to get the shape right would
    reintroduce the bug the table exists to prevent.
    """
    import sqlite3

    from chitragupta.agents import cards as store

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.row_factory = sqlite3.Row
    old.executescript("""
        CREATE TABLE action_cards (
            key TEXT PRIMARY KEY, agent_id TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'done', detail TEXT NOT NULL DEFAULT '',
            log_id TEXT NOT NULL DEFAULT '', verified_at TEXT NOT NULL DEFAULT '',
            reversible INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL);
        CREATE INDEX idx_cards_agent ON action_cards(agent_id, at);
    """)
    old.execute("INSERT INTO action_cards (key,agent_id,state,detail,at) "
                "VALUES ('k1','health','cancelled','','2026-09-01T00:00:00')")
    old.commit()

    # The column has to exist before the rebuild copies it.
    old.execute("ALTER TABLE action_cards ADD COLUMN undo_label "
                "TEXT NOT NULL DEFAULT ''")
    old.commit()
    store._repair_primary_key(old)

    pk = {r["name"] for r in old.execute("PRAGMA table_info(action_cards)")
          if r["pk"]}
    assert pk == {"agent_id", "key"}, pk
    kept = old.execute("SELECT * FROM action_cards").fetchall()
    assert len(kept) == 1
    assert kept[0]["state"] == "cancelled", "the answer was dropped"
    assert kept[0]["agent_id"] == "health"
    old.close()
