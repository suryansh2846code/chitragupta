"""One list of everything an agent wants to tell the user.

It replaced two things that did not work and one that nobody looks at.

* A **desktop notification** is gone if the machine was asleep or the user
  glanced away, with no record it existed.
* The **agent's own chat** was tried and undone: a result is not part of a
  conversation somebody was having, and putting it there also put the
  automation's whole prompt in beside it, attributed to a user who typed none
  of it.
* The **run history** is complete, correct, and somewhere you only go once you
  already know something happened.

So the claims are: it says who is speaking, it outlives the moment, and it can
be marked read — which is the whole difference between a notification and a
record.
"""
from __future__ import annotations

import pytest

from chitragupta import messages


@pytest.fixture(autouse=True)
def _own_store(tmp_path, monkeypatch):
    from chitragupta.config import get_settings

    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path))
    get_settings.cache_clear()
    messages.reset_for_tests()
    yield
    messages.reset_for_tests()
    get_settings.cache_clear()


def store():
    return messages.get_messages()


# ── saying something ───────────────────────────────────────────────────────

def test_a_message_says_who_sent_it():
    """A list of messages with no sender is a list of things that happened to
    somebody."""
    sent = store().send("health", "You have not logged a session in four days.")

    assert sent["agent_id"] == "health"
    assert "four days" in sent["body"]
    assert sent["read_at"] == "", "it arrived already read"


def test_nothing_is_sent_for_an_empty_message():
    """A line in the list that costs a glance and says nothing — and the
    callers most likely to produce one are the ones reporting on something that
    did nothing."""
    assert store().send("health", "   ") == {}
    assert store().recent() == []


def test_an_unknown_kind_becomes_a_plain_note():
    """A hand-edited or out-of-date value must not vanish from the list, and it
    must not invent a badge nothing knows how to draw."""
    sent = store().send("health", "hello", kind="urgent-super-important")
    assert sent["kind"] == messages.NOTE


def test_it_carries_where_it_came_from():
    """A result you cannot trace back is one you have to take on faith."""
    sent = store().send("chief-of-staff", "3 new replies", kind=messages.RESULT,
                        source="automation", source_id="run-77")
    assert sent["source"] == "automation"
    assert sent["source_id"] == "run-77"


# ── reading it ─────────────────────────────────────────────────────────────

def test_newest_first():
    store().send("a", "first")
    store().send("b", "second")
    assert [m["body"] for m in store().recent()] == ["second", "first"]


def test_read_and_unread_are_listed_together():
    """A list that hid what you had read would be impossible to find something
    in a second time, which is the one thing it has to be good at."""
    first = store().send("a", "first")
    store().send("b", "second")
    store().mark_read(first["id"])

    assert len(store().recent()) == 2
    assert store().unread() == 1


def test_marking_one_read_is_idempotent():
    sent = store().send("a", "hello")
    assert store().mark_read(sent["id"]) is True
    assert store().mark_read(sent["id"]) is False, "it was already read"
    assert store().unread() == 0


def test_marking_all_read_reports_how_many():
    for n in range(3):
        store().send("a", f"message {n}")
    assert store().mark_all_read() == 3
    assert store().unread() == 0


def test_deleting_one_leaves_the_rest():
    keep = store().send("a", "keep me")
    drop = store().send("b", "drop me")

    assert store().delete(drop["id"]) is True
    assert store().delete(drop["id"]) is False
    assert [m["id"] for m in store().recent()] == [keep["id"]]


# ── it survives what it is for ─────────────────────────────────────────────

def test_an_unread_message_is_never_pruned():
    """The point of a message is that somebody still has to see it. Pruning by
    age alone would drop exactly the ones that matter on a machine that was
    left alone for a fortnight."""
    old = store().send("a", "the one that mattered")
    for n in range(messages.MAX_KEPT + 20):
        sent = store().send("b", f"noise {n}")
        store().mark_read(sent["id"])

    assert store().get(old["id"]), "an unread message was pruned"


def test_sending_never_raises():
    """An agent telling the user something must not be able to fail the work it
    was telling them about."""
    def broken():
        raise RuntimeError("the store is gone")

    import chitragupta.messages as module
    original = module.get_messages
    module.get_messages = broken
    try:
        assert messages.send("a", "hello") == {}
    finally:
        module.get_messages = original


# ── through the API ────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app
    return TestClient(app)


def test_the_list_names_the_agent_rather_than_printing_its_id(client):
    """"chief-of-staff" is a column value. The point of the screen is that a
    person can see who is talking to them."""
    from chitragupta.agents.custom import get_custom_store

    agent = get_custom_store().create("Desk", role="watches")
    try:
        store().send(agent.id, "something happened")
        body = client.get("/api/messages").json()

        assert body["messages"][0]["agent_name"] == "Desk"
        assert body["unread"] == 1
    finally:
        get_custom_store().delete(agent.id)


def test_a_message_from_a_deleted_agent_is_still_shown(client):
    """What it said happened, and deleting the agent does not unsay it."""
    store().send("gone-forever", "I did a thing before I was removed")
    body = client.get("/api/messages").json()

    assert body["messages"][0]["agent_name"] == "gone-forever"


def test_marking_read_through_the_api(client):
    sent = store().send("a", "hello")
    assert client.post(f"/api/messages/{sent['id']}/read").json()["ok"] is True
    assert client.get("/api/messages").json()["unread"] == 0


def test_marking_something_that_is_gone_is_not_an_error(client):
    """The user asked for it to be read and it is read. A red box there would
    be the app arguing about bookkeeping."""
    reply = client.post("/api/messages/never-existed/read")
    assert reply.status_code == 200
    assert reply.json()["already"] is True


def test_deleting_one_that_is_gone_is_a_404(client):
    assert client.delete("/api/messages/never-existed").status_code == 404
