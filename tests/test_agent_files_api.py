"""The HTTP surface for an agent's two files.

The endpoints are thin, so most of what is worth asserting is what they refuse:
a file name that is not one of the two, an agent nobody has, and a body past
the cap. Each of those arrives from outside the machine, and the first one
reaches a filesystem path.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chitragupta.agents import profile_files as pf
from chitragupta.api.app import app

AGENT = "inbox"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    pf.forget(AGENT)
    yield
    pf.forget(AGENT)


def test_an_agent_with_no_files_reports_both_as_absent(client):
    r = client.get(f"/api/agents/{AGENT}/files")
    assert r.status_code == 200
    files = {f["name"]: f for f in r.json()["files"]}
    assert set(files) == {pf.PERSONA, pf.MEMORY}
    assert files[pf.PERSONA]["exists"] is False
    assert files[pf.MEMORY]["limit"] == pf.LIMITS[pf.MEMORY]


def test_a_file_round_trips_over_http(client):
    r = client.put(f"/api/agents/{AGENT}/files/{pf.MEMORY}",
                   json={"text": "- Replies stay short.\n"})
    assert r.status_code == 200
    assert r.json()["exists"] is True

    got = client.get(f"/api/agents/{AGENT}/files/{pf.MEMORY}")
    assert got.json()["text"] == "- Replies stay short.\n"


def test_a_file_that_is_not_there_is_null_rather_than_a_404(client):
    """"This agent uses the shipped persona" is the ordinary answer, not a
    missing resource — a 404 makes every caller treat the normal case as an
    error."""
    r = client.get(f"/api/agents/{AGENT}/files/{pf.PERSONA}")
    assert r.status_code == 200
    assert r.json()["text"] is None


def test_deleting_reports_whether_there_was_anything_to_delete(client):
    assert client.delete(
        f"/api/agents/{AGENT}/files/{pf.PERSONA}").json()["cleared"] is False
    client.put(f"/api/agents/{AGENT}/files/{pf.PERSONA}", json={"text": "x"})
    assert client.delete(
        f"/api/agents/{AGENT}/files/{pf.PERSONA}").json()["cleared"] is True


# ── what it refuses ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "secrets.json",
    "..%2f..%2fsecrets.json",
    "notes.md",
    "Persona.md",
])
def test_only_the_two_known_names_are_served(client, name):
    """The name is never cleaned up on its way to a path — it is matched whole
    against a closed set, so anything else is simply not a file here."""
    # Either answer is correct and which one depends on the name: a name that
    # decodes to something with a slash in it never matches the route at all
    # (404), and one that does match is refused by the closed set (400). What
    # matters is that neither reaches a file.
    r = client.get(f"/api/agents/{AGENT}/files/{name}")
    assert r.status_code in (400, 404)
    w = client.put(f"/api/agents/{AGENT}/files/{name}", json={"text": "x"})
    assert w.status_code in (400, 404)


def test_a_traversing_name_writes_nothing_anywhere(client):
    """The assertion that matters is not the status code — it is that the file
    it was reaching for was not created."""
    # Planted, so the assertion cannot pass merely because the file was never
    # there — this test is worthless if "unchanged" means "absent both times".
    target = pf.root().parent / "secrets.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("the real thing", encoding="utf-8")

    # And the traversal really does aim at it: naively joined, the name below
    # resolves to exactly the planted file. Without this line the test could
    # pass while pointing at somewhere nothing was ever going to reach.
    naive = pf.root() / AGENT / ".." / ".." / "secrets.json"
    assert naive.resolve() == target.resolve()

    for name in ("..%2f..%2fsecrets.json", "..%2fsecrets.json", "../secrets.json"):
        client.put(f"/api/agents/{AGENT}/files/{name}", json={"text": "owned"})

    assert target.read_text(encoding="utf-8") == "the real thing"


def test_an_unknown_agent_is_a_404_and_creates_nothing(client):
    r = client.put(f"/api/agents/no-such-agent-here/files/{pf.MEMORY}",
                   json={"text": "x"})
    assert r.status_code == 404
    assert not (pf.root() / "no-such-agent-here").exists()


def test_a_body_over_the_cap_is_refused_with_a_sentence(client):
    """These ride in the cached prefix of every turn. The refusal is shown to
    the person who pressed Save, so it says what to do about it."""
    r = client.put(f"/api/agents/{AGENT}/files/{pf.MEMORY}",
                   json={"text": "x" * (pf.LIMITS[pf.MEMORY] + 1)})
    assert r.status_code == 400
    assert "Shorten it" in r.json()["detail"]
    assert pf.read(AGENT, pf.MEMORY) is None
