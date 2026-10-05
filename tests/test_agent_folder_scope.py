"""Which folders one agent works in, as distinct from which are open at all.

Two layers, and the whole feature is that they are two:

* the **grant** — what may be reached from this app at all. One list, the
  user's consent, the thing `_resolve` enforces against a path an injection
  wrote. It has not moved.
* the **scope** — which of those folders *this* agent works in. A team of
  agents sharing one machine is not a team sharing one filing cabinet, and
  before this there was one list and no way to say otherwise.

A scope only ever narrows a grant. That is what keeps closing a folder in the
panel a thing that closes it for everybody, including an agent whose scope
still names it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chitragupta.agents import access, acting, file_tools
from chitragupta.agents.tools import run_tool


@pytest.fixture
def fresh_store():
    """Forget what the default was captured as.

    The default is frozen on first read **on purpose** — that is what stops one
    agent's folder becoming everybody's. The store outlives a single test, so
    without this the first test to run would freeze it for the rest of the file
    and every later assertion would be about that one's fixture.
    """
    store = file_tools._store()
    store.set_meta(file_tools.DEFAULT_SCOPE_KEY, "")
    store.set_meta(file_tools.SCOPES_KEY, "{}")
    yield
    store.set_meta(file_tools.DEFAULT_SCOPE_KEY, "")
    store.set_meta(file_tools.SCOPES_KEY, "{}")


@pytest.fixture
def folders(tmp_path, monkeypatch, fresh_store):
    """Two granted folders, one ungranted, and a frozen default of both."""
    red = tmp_path / "red"
    blue = tmp_path / "blue"
    away = tmp_path / "away"
    for d in (red, blue, away):
        d.mkdir()
    (red / "red.md").write_text("red")
    (blue / "blue.md").write_text("blue")
    granted = [str(red), str(blue)]
    monkeypatch.setattr(file_tools, "granted_roots", lambda: granted)
    # `grant_folder` is what `set_agent_scope` calls on the way in; with
    # `granted_roots` faked, let it validate and report rather than write.
    monkeypatch.setattr(
        file_tools, "grant_folder",
        lambda p: {"path": str(Path(p).resolve()), "roots": granted})
    return red, blue, away


@pytest.fixture
def acting_as():
    """Run as one agent, the way a turn does. Always reset."""
    tokens = []

    def _as(agent_id):
        tokens.append(acting.acting_as(agent_id))

    yield _as
    for token in reversed(tokens):
        acting.stop_acting(token)


# ── undecided is not empty, and empty is not undecided ───────────────────
def test_an_agent_never_asked_reads_back_as_undecided(folders):
    """None and [] are different answers and the feature is the difference."""
    assert file_tools.agent_scope("never-asked") is None


def test_an_agent_told_to_look_at_nothing_keeps_that_answer(folders):
    """"Look at nothing for this conversation" is a thing the user can say."""
    file_tools.set_agent_scope("monk", [])
    assert file_tools.agent_scope("monk") == []
    assert file_tools.roots_for("monk") == []


def test_putting_an_agent_back_to_undecided_is_not_the_same_as_emptying_it(folders):
    file_tools.set_agent_scope("monk", [])
    file_tools.set_agent_scope("monk", None)
    assert file_tools.agent_scope("monk") is None


# ── the default is frozen, so one agent's folder is not everyone's ───────
def test_the_default_is_whatever_was_open_when_this_first_ran(folders):
    red, blue, _ = folders
    assert sorted(file_tools.default_scope()) == sorted([str(red), str(blue)])


def test_choosing_a_folder_for_one_agent_does_not_hand_it_to_the_others(
        tmp_path, monkeypatch, fresh_store):
    """The reason the default is captured once rather than read live.

    Read live, "exclusive to this agent" would be a lie: picking a folder for
    one agent would appear in every agent nobody had configured yet.
    """
    red = tmp_path / "red"
    later = tmp_path / "later"
    for d in (red, later):
        d.mkdir()
    granted = [str(red)]
    monkeypatch.setattr(file_tools, "granted_roots", lambda: granted)
    monkeypatch.setattr(
        file_tools, "grant_folder",
        lambda p: {"path": str(Path(p).resolve()), "roots": granted})

    assert file_tools.default_scope() == [str(red)]      # captured here

    granted.append(str(later))                           # a new grant, after
    file_tools.set_agent_scope("chotu", [str(red), str(later)])

    assert file_tools.roots_for("chotu") == [str(red), str(later)]
    assert file_tools.roots_for("somebody-else") == [str(red)]


# ── a scope narrows a grant and can never widen one ──────────────────────
def test_a_scope_cannot_reach_a_folder_that_is_not_granted(folders):
    red, _blue, away = folders
    # Written straight past `set_agent_scope`, as a stored scope from before a
    # revoke would be. The intersection is what makes revoking still revoke.
    file_tools._store().set_meta(
        file_tools.SCOPES_KEY, json.dumps({"chotu": [str(red), str(away)]}))
    assert file_tools.roots_for("chotu") == [str(red)]


def test_revoking_a_folder_closes_it_for_an_agent_that_still_names_it(
        folders, monkeypatch):
    red, blue, _ = folders
    file_tools.set_agent_scope("chotu", [str(red), str(blue)])
    monkeypatch.setattr(file_tools, "granted_roots", lambda: [str(blue)])
    assert file_tools.roots_for("chotu") == [str(blue)]


# ── and the tools obey it ────────────────────────────────────────────────
def test_a_tool_reads_the_running_agents_folders(folders, acting_as):
    red, blue, _ = folders
    file_tools.set_agent_scope("redder", [str(red)])
    acting_as("redder")

    assert run_tool("read_file", {"path": str(red / "red.md")}).ok
    out = run_tool("read_file", {"path": str(blue / "blue.md")})
    assert not out.ok, "an agent read a folder it was not given"
    assert "outside every folder" in str(out)


def test_an_agent_scoped_to_nothing_reaches_nothing(folders, acting_as):
    red, _, _ = folders
    file_tools.set_agent_scope("blinkered", [])
    acting_as("blinkered")

    out = run_tool("read_file", {"path": str(red / "red.md")})
    assert not out.ok
    assert "no folder to work in" in str(out)


def test_listing_with_no_path_lists_only_this_agents_folders(folders, acting_as):
    red, blue, _ = folders
    file_tools.set_agent_scope("redder", [str(red)])
    acting_as("redder")

    out = str(run_tool("list_dir", {"path": ""}))
    assert str(red) in out
    assert str(blue) not in out


def test_with_no_agent_running_the_grant_list_is_the_answer(folders):
    """An approved action runs from the queue with no turn around it.

    Scoping a question we cannot attribute to an agent would break attachments
    rather than protect anything — the grant is still the boundary.
    """
    red, blue, _ = folders
    file_tools.set_agent_scope("redder", [str(red)])
    assert sorted(file_tools.active_roots()) == sorted([str(red), str(blue)])


# ── asking for a folder in the chat gives it to the agent that asked ─────
def test_granting_what_an_agent_asked_for_adds_to_what_it_already_had(folders):
    red, blue, _ = folders
    file_tools.set_agent_scope("chotu", [str(red)])
    need = access.parse(f"folder:{blue}")[0]

    assert access.grant("chotu", need)["ok"] is True
    assert file_tools.roots_for("chotu") == [str(red), str(blue)]


def test_granting_to_an_undecided_agent_adds_rather_than_replaces(folders):
    """The base is `roots_for`, never `agent_scope`.

    An agent that was never scoped reads back as None; writing `[target]` over
    that would take away everything it could already reach in the act of
    granting it one more thing.
    """
    red, blue, _ = folders
    assert file_tools.agent_scope("fresh") is None
    need = access.parse(f"folder:{blue}")[0]

    access.grant("fresh", need)
    assert sorted(file_tools.roots_for("fresh")) == sorted([str(red), str(blue)])


def test_the_card_says_granted_about_the_agent_it_is_shown_beside(folders):
    """Not about whether the machine has it open to somebody else."""
    red, blue, _ = folders
    file_tools.set_agent_scope("chotu", [str(red)])
    need = access.parse(f"folder:{blue}")[0]
    assert access.describe("chotu", [need])[0]["granted"] is False


# ── an erased agent takes its folders with it ────────────────────────────
def test_erasing_an_agent_forgets_its_folders(folders):
    red, _, _ = folders
    file_tools.set_agent_scope("gone", [str(red)])
    file_tools.forget_agent_scope("gone")
    assert file_tools.agent_scope("gone") is None


# ── and over the wire, where the three answers have to survive ───────────
@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app
    return TestClient(app)


def test_the_endpoint_answers_with_all_three_lists(client, folders):
    red, blue, _ = folders
    file_tools.set_agent_scope("inbox", [str(red)])

    got = client.get("/api/agents/inbox/folders").json()
    assert sorted(got["available"]) == sorted([str(red), str(blue)])
    assert got["chosen"] == [str(red)]
    assert got["folders"] == [str(red)]


def test_an_empty_list_over_the_wire_means_nothing_not_undecided(client, folders):
    red, _, _ = folders
    file_tools.set_agent_scope("inbox", [str(red)])

    got = client.put("/api/agents/inbox/folders", json={"folders": []}).json()
    assert got["chosen"] == [], "it read an empty list as 'never chosen'"
    assert got["folders"] == []
    assert client.get("/api/agents/inbox/folders").json()["chosen"] == [], \
        "the answer did not survive being read back"


def test_null_over_the_wire_puts_it_back_to_undecided(client, folders):
    red, blue, _ = folders
    file_tools.set_agent_scope("inbox", [str(red)])

    got = client.put("/api/agents/inbox/folders", json={"folders": None}).json()
    assert got["chosen"] is None
    assert sorted(got["folders"]) == sorted([str(red), str(blue)])


def test_a_folder_that_cannot_be_opened_is_refused_in_words(client, fresh_store):
    """The whole home directory is not a boundary, and the sentence saying so
    is written for a person — so it is passed through rather than replaced.

    Deliberately not on the `folders` fixture: that one stubs `grant_folder` so
    the other tests can fake a grant list, and this is the one test about what
    the real `grant_folder` refuses.
    """
    out = client.put("/api/agents/inbox/folders",
                     json={"folders": [str(Path.home())]})
    assert out.status_code == 400
    assert "rather than the whole" in out.json()["detail"]
