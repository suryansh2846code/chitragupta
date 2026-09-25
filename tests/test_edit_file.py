"""Changing part of a file, instead of rewriting all of it from memory.

Before this the only way to amend a file was `read_file` then `write_file` with
the whole thing retyped out of the model's recollection of it. That is expensive
— the file is billed twice, once in and once out — but the cost is not the
problem. The problem is that it is **lossy and silent**: a 900-line CSV comes
back with a row quietly missing, the reply says "updated the file", and the user
finds out weeks later from the file.

The refusals are the feature here. An edit that lands *somewhere* is worse than
an edit that does not land, because only one of the two tells anyone.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import file_tools


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """A granted folder, the way the user's consent gesture produces one."""
    monkeypatch.setattr(file_tools, "granted_roots", lambda: [str(tmp_path)])
    return tmp_path


def _write(folder, name: str, text: str):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


# ── it edits ───────────────────────────────────────────────────────────────

def test_one_passage_changes_and_nothing_else_moves(folder):
    path = _write(folder, "notes.md", "alpha\nbeta\ngamma\n")
    result = file_tools.edit_file(str(path), "beta", "BETA")
    assert result.ok
    assert path.read_text() == "alpha\nBETA\ngamma\n"


def test_an_empty_replacement_deletes_the_passage(folder):
    path = _write(folder, "notes.md", "keep\nDROP ME\nkeep\n")
    assert file_tools.edit_file(str(path), "DROP ME\n", "").ok
    assert path.read_text() == "keep\nkeep\n"


def test_the_reply_says_what_actually_changed(folder):
    """A model that is told only "done" cannot tell a no-op from an edit."""
    path = _write(folder, "notes.md", "alpha\n")
    said = str(file_tools.edit_file(str(path), "alpha", "alphabet"))
    assert "1 replacement" in said


# ── it refuses, and says what to do instead ────────────────────────────────

def test_a_passage_that_is_not_there_changes_nothing(folder):
    """A near-match edited anyway is an edit to something the model never saw."""
    path = _write(folder, "notes.md", "alpha\nbeta\n")
    result = file_tools.edit_file(str(path), "BETA", "x")
    assert not result.ok
    assert path.read_text() == "alpha\nbeta\n"
    assert "Read the file again" in str(result)


def test_an_ambiguous_passage_is_refused_rather_than_guessed(folder):
    """Four matches replaced in the first is a change to an arbitrary one of
    them, and which one is not knowable from the reply."""
    path = _write(folder, "data.csv", "x,1\nx,1\nx,1\nx,1\n")
    result = file_tools.edit_file(str(path), "x,1", "x,2")
    assert not result.ok
    assert path.read_text() == "x,1\nx,1\nx,1\nx,1\n"
    assert "4 times" in str(result)


def test_the_refusal_says_how_to_succeed(folder):
    """A model told only "no" re-issues the same call."""
    path = _write(folder, "data.csv", "x,1\nx,1\n")
    said = str(file_tools.edit_file(str(path), "x,1", "x,2"))
    assert "surrounding lines" in said and "all=true" in said


def test_all_is_opt_in_and_then_it_does_change_every_one(folder):
    path = _write(folder, "data.csv", "x,1\nx,1\nx,1\n")
    result = file_tools.edit_file(str(path), "x,1", "x,2", all=True)
    assert result.ok and "3 replacements" in str(result)
    assert path.read_text() == "x,2\nx,2\nx,2\n"


def test_an_empty_find_is_refused(folder):
    """An empty string is in every file, at every position."""
    path = _write(folder, "notes.md", "alpha\n")
    result = file_tools.edit_file(str(path), "", "x")
    assert not result.ok
    assert path.read_text() == "alpha\n"


# ── the boundary is the same one every other file tool uses ────────────────

def test_it_cannot_edit_outside_a_granted_folder(folder, tmp_path_factory):
    """`_resolve` is the thing standing between "save this to
    ~/.ssh/authorized_keys" — a sentence an injection would write — and the
    filesystem. A new tool that forgets to call it is a new way around it."""
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.txt"
    outside.write_text("untouched", encoding="utf-8")
    result = file_tools.edit_file(str(outside), "untouched", "owned")
    assert not result.ok
    assert outside.read_text() == "untouched"


def test_a_missing_file_is_not_created_by_an_edit(folder):
    """`write_file` creates; `edit_file` amends. Creating here would make a
    typo'd path look like a successful change."""
    result = file_tools.edit_file(str(folder / "nope.md"), "a", "b")
    assert not result.ok
    assert not (folder / "nope.md").exists()


def test_a_binary_file_is_refused(folder):
    path = folder / "image.png"
    path.write_bytes(b"\x89PNG\r\n")
    assert not file_tools.edit_file(str(path), "PNG", "GIF").ok


def test_an_edit_over_the_size_cap_is_refused(folder):
    """A runaway loop must not be able to fill a volume through the back door."""
    path = _write(folder, "notes.md", "seed")
    huge = "y" * (file_tools.MAX_WRITE_CHARS + 10)
    result = file_tools.edit_file(str(path), "seed", huge)
    assert not result.ok
    assert path.read_text() == "seed"


# ── it is actually reachable ───────────────────────────────────────────────

def test_the_tool_is_registered_and_offered(folder):
    """A tool that exists in `file_tools` and nowhere else is a function."""
    from chitragupta.agents.library import _FILES
    from chitragupta.agents.tools import TOOL_DEFS, TOOL_IMPLS
    assert "edit_file" in TOOL_DEFS
    assert "edit_file" in TOOL_IMPLS
    assert "edit_file" in _FILES


def test_write_file_is_still_offered_beside_it():
    """Creating a file and amending one are different jobs. An agent given only
    the second cannot start anything."""
    from chitragupta.agents.library import _FILES
    assert "write_file" in _FILES
