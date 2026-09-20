"""Renaming and moving a file, with both ends inside a granted folder.

The last of Phase 3's file verbs. `read_file` and `write_file` already went
through `_resolve`; this is the first tool with **two** paths, and that is the
whole test.

Checking only the source would let

    move_file("notes.md", "~/Library/LaunchAgents/run-me.plist")

walk a file straight out of the sandbox the grant exists to define — and the
destination is the half that decides where the file ends up, so it is the half
that matters more. `_resolve` resolves before comparing, so `../` and a planted
symlink are both already covered; what is new here is remembering to call it
twice.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import file_tools


@pytest.fixture
def granted(tmp_path, monkeypatch):
    """One granted folder, and an ungranted one beside it."""
    inside = tmp_path / "granted"
    inside.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setattr(file_tools, "granted_roots", lambda: [str(inside)])
    return inside, outside


# ── the boundary, which is the point ─────────────────────────────────────
def test_it_will_not_move_a_file_out_of_the_granted_folder(granted):
    """The destination is the half that decides where the file ends up."""
    inside, outside = granted
    (inside / "notes.md").write_text("hello")

    result = file_tools.move_file(str(inside / "notes.md"),
                                  str(outside / "notes.md"))

    assert not result.ok
    assert (inside / "notes.md").exists(), "it moved the file anyway"
    assert not (outside / "notes.md").exists()


def test_it_will_not_move_a_file_in_from_outside(granted):
    """The mirror. Pulling a file in is how something unreadable becomes
    readable, and the grant is about both directions."""
    inside, outside = granted
    (outside / "secret.txt").write_text("hello")

    result = file_tools.move_file(str(outside / "secret.txt"),
                                  str(inside / "secret.txt"))

    assert not result.ok
    assert (outside / "secret.txt").exists()


def test_a_traversal_in_the_destination_is_refused(granted):
    """`_resolve` resolves before comparing, so this is already covered — but
    only if it is actually called on the destination."""
    inside, outside = granted
    (inside / "notes.md").write_text("hello")

    result = file_tools.move_file(str(inside / "notes.md"),
                                  str(inside / ".." / "elsewhere" / "n.md"))

    assert not result.ok
    assert (inside / "notes.md").exists()


def test_with_no_folder_granted_nothing_moves(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "granted_roots", list)
    (tmp_path / "a.md").write_text("x")

    result = file_tools.move_file(str(tmp_path / "a.md"),
                                  str(tmp_path / "b.md"))

    assert not result.ok
    assert (tmp_path / "a.md").exists()


# ── and it does the job ──────────────────────────────────────────────────
def test_renaming_in_place(granted):
    inside, _ = granted
    (inside / "draft.md").write_text("hello")

    result = file_tools.move_file(str(inside / "draft.md"),
                                  str(inside / "proposal.md"))

    assert result.ok
    assert (inside / "proposal.md").read_text() == "hello"
    assert not (inside / "draft.md").exists()
    assert "Renamed" in result, "a rename is not described as a move"


def test_moving_into_a_subfolder(granted):
    inside, _ = granted
    (inside / "draft.md").write_text("hello")

    result = file_tools.move_file(str(inside / "draft.md"),
                                  str(inside / "2026" / "draft.md"))

    assert result.ok
    assert (inside / "2026" / "draft.md").read_text() == "hello"
    assert "Moved" in result


# ── the things that would be quiet damage ────────────────────────────────
def test_it_refuses_to_overwrite(granted):
    """A move that silently replaces something is a deletion nobody was
    shown, and the user finds out when they go looking for the file that used
    to be there."""
    inside, _ = granted
    (inside / "a.md").write_text("new")
    (inside / "b.md").write_text("the one that was already there")

    result = file_tools.move_file(str(inside / "a.md"), str(inside / "b.md"))

    assert not result.ok
    assert (inside / "b.md").read_text() == "the one that was already there"
    assert (inside / "a.md").exists(), "and the source is left alone too"


def test_a_missing_file_says_so(granted):
    inside, _ = granted

    result = file_tools.move_file(str(inside / "nope.md"),
                                  str(inside / "yes.md"))

    assert not result.ok
    assert "not there" in result


def test_it_refuses_a_folder(granted):
    """Moving a directory moves everything in it, which is a much larger
    decision than the one the user made."""
    inside, _ = granted
    (inside / "stuff").mkdir()

    result = file_tools.move_file(str(inside / "stuff"),
                                  str(inside / "things"))

    assert not result.ok
    assert (inside / "stuff").is_dir()


def test_every_agent_that_can_write_can_also_move(granted):
    """An agent that can create a file and not rename one leaves the user
    tidying up after it."""
    from chitragupta.agents.tools import TOOL_DEFS, TOOL_IMPLS

    assert "move_file" in TOOL_DEFS
    assert "move_file" in TOOL_IMPLS
