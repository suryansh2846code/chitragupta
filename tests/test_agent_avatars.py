"""Per-agent avatars: the store, and the three endpoints over it.

The renderer owns the format and has its own suite (`character/test/`). What is
worth pinning here is the boundary: that an agent with no row is a normal agent
rather than a missing one, that a document from somewhere else is refused with a
sentence a person can read, and that a row nobody can parse costs one agent its
avatar rather than costing every agent theirs.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from chitragupta.agents import avatars
from chitragupta.api.app import app


def _scene(name: str = "Test") -> dict:
    """The smallest thing the store is willing to accept.

    Deliberately not a full document: the server does not parse one, and writing
    a realistic one here would be this file quietly asserting a schema it has no
    business knowing about.
    """
    return {
        "schema": "character.scene",
        "version": 1,
        "metadata": {"name": name},
        "scene": {"entity": {"parts": [{"shape": "sphere"}]}},
    }


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Each test gets its own `agents.db`, so none of them can see another's rows."""
    from chitragupta import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "home", tmp_path, raising=False)
    # Patched on the module that reads it, not only on `config`: `avatars`
    # imported the name directly, so replacing it on `config` alone would leave
    # the store writing to the developer's real home.
    monkeypatch.setattr(avatars, "get_settings", lambda: settings)
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ── the store ───────────────────────────────────────────────────────────────

def test_an_agent_with_no_row_has_no_override():
    assert avatars.get_agent_avatar("inbox") is None
    assert avatars.list_agent_avatars() == {}


def test_a_saved_avatar_comes_back_unchanged():
    scene = _scene("Inbox")
    avatars.set_agent_avatar("inbox", scene)
    assert avatars.get_agent_avatar("inbox") == scene
    assert avatars.list_agent_avatars()["inbox"]["scene"] == scene


def test_saving_twice_replaces_rather_than_duplicates():
    avatars.set_agent_avatar("inbox", _scene("First"))
    avatars.set_agent_avatar("inbox", _scene("Second"))
    assert avatars.get_agent_avatar("inbox")["metadata"]["name"] == "Second"
    assert list(avatars.list_agent_avatars()) == ["inbox"]


def test_clearing_returns_the_agent_to_its_generated_character():
    avatars.set_agent_avatar("inbox", _scene())
    assert avatars.clear_agent_avatar("inbox") is True
    assert avatars.get_agent_avatar("inbox") is None
    # Clearing something already clear is not an error — the user pressed a
    # button that means "use the generated one", and it already does.
    assert avatars.clear_agent_avatar("inbox") is False


@pytest.mark.parametrize("bad", [None, [], "a string", 7, {"schema": "oneworks.avatar"}, {}])
def test_a_document_from_somewhere_else_is_refused(bad):
    with pytest.raises(avatars.AvatarRejectedError):
        avatars.set_agent_avatar("inbox", bad)


def test_an_enormous_document_is_refused():
    huge = _scene()
    huge["scene"]["junk"] = "x" * (avatars.MAX_SCENE_BYTES + 1)
    with pytest.raises(avatars.AvatarRejectedError) as exc:
        avatars.set_agent_avatar("inbox", huge)
    # The message is shown to whoever pressed Save, so it has to read like one.
    assert "too large" in str(exc.value)


def test_a_row_nobody_can_parse_costs_one_agent_not_all_of_them():
    """A truncated or foreign row must not blank the whole roster.

    `list_agent_avatars` is called once at boot, before the agent rail paints.
    Raising there would mean one bad row takes every custom avatar with it —
    and the rail would be left drawing generated faces with no explanation.
    """
    avatars.set_agent_avatar("inbox", _scene("Good"))
    conn = sqlite3.connect(str(avatars.get_settings().home / "agents.db"))
    conn.execute(
        "INSERT INTO agent_avatars (agent_id, scene, updated_at) VALUES (?, ?, ?)",
        ("broken", "{not json", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    listed = avatars.list_agent_avatars()
    assert "inbox" in listed
    assert "broken" not in listed


# ── the endpoints ───────────────────────────────────────────────────────────

def test_get_avatar_answers_null_rather_than_404_when_there_is_no_override(client):
    """"This agent uses its generated character" is a normal answer.

    A 404 would make every caller treat the ordinary case as an error, and the
    frontend would have to tell two kinds of "not found" apart to draw a rail.
    """
    r = client.get("/api/agents/inbox/avatar")
    assert r.status_code == 200
    assert r.json() == {"agent_id": "inbox", "scene": None}


def test_put_then_get_round_trips_through_the_api(client):
    scene = _scene("Mine")
    r = client.put("/api/agents/inbox/avatar", json={"scene": scene})
    assert r.status_code == 200
    assert r.json()["scene"] == scene

    assert client.get("/api/agents/inbox/avatar").json()["scene"] == scene
    assert client.get("/api/agents/avatars").json()["avatars"]["inbox"]["scene"] == scene


def test_delete_removes_the_override(client):
    client.put("/api/agents/inbox/avatar", json={"scene": _scene()})
    assert client.delete("/api/agents/inbox/avatar").json() == {"cleared": True}
    assert client.get("/api/agents/inbox/avatar").json()["scene"] is None


def test_an_unknown_agent_is_a_404_on_every_verb(client):
    assert client.get("/api/agents/nope/avatar").status_code == 404
    assert client.put("/api/agents/nope/avatar", json={"scene": _scene()}).status_code == 404
    assert client.delete("/api/agents/nope/avatar").status_code == 404


def test_a_rejected_avatar_says_what_happened(client):
    r = client.put("/api/agents/inbox/avatar", json={"scene": {"schema": "oneworks.avatar"}})
    assert r.status_code == 400
    detail = r.json()["detail"]
    # No internals: not a traceback, not an exception class, not a field path.
    assert "different tool" in detail
    assert "Error" not in detail and "Traceback" not in detail


def test_the_stored_json_is_compact(client):
    """One column per agent, read on every launch. Whitespace is dead weight."""
    client.put("/api/agents/inbox/avatar", json={"scene": _scene()})
    conn = sqlite3.connect(str(avatars.get_settings().home / "agents.db"))
    text = conn.execute("SELECT scene FROM agent_avatars WHERE agent_id = 'inbox'").fetchone()[0]
    conn.close()
    assert ", " not in text and '": ' not in text
    assert json.loads(text)["schema"] == "character.scene"
