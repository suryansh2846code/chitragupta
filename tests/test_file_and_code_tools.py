"""Hands, and the boundary that makes them safe to have.

These agents read email, issues and messages written by other people. "Save this
to ~/.ssh/authorized_keys" is a sentence an injection would write, and the model
is not what stands between that sentence and the filesystem — the path check is.
So most of what is worth testing here is the refusals.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chitragupta.agents import file_tools
from chitragupta.agents.presets import PRESETS
from chitragupta.agents.tools import TOOL_DEFS, TOOL_IMPLS, build_tools, run_tool


@pytest.fixture
def granted(tmp_path, monkeypatch):
    """One folder open to agents, and nothing else."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "notes.md").write_text("the quarterly numbers\n", encoding="utf-8")

    roots = [str(root.resolve())]
    monkeypatch.setattr(file_tools, "granted_roots", lambda: roots)
    return root


# ── nothing is reachable until the user says so ──────────────────────────
def test_with_no_grant_nothing_on_disk_is_reachable(monkeypatch):
    monkeypatch.setattr(file_tools, "granted_roots", list)
    for call in (("read_file", {"path": "/etc/hosts"}),
                 ("write_file", {"path": "/tmp/x.txt", "content": "x"}),
                 ("list_dir", {"path": "/"})):
        out = run_tool(*call)
        assert not out.ok, f"{call[0]} worked with no folder granted"
        # The refusal has to name the control that fixes it, not just say no.
        # A refusal a tap would fix and that never says which tap is the dead
        # end the whole access vocabulary exists to stop.
        assert "no folder to work in" in str(out)
        assert "folder button" in str(out)


def test_the_whole_home_directory_cannot_be_granted():
    """A grant that wide is not a boundary."""
    with pytest.raises(ValueError, match="rather than the whole"):
        file_tools.grant_folder(str(Path.home()))
    with pytest.raises(ValueError):
        file_tools.grant_folder("/")


# ── escaping the boundary ────────────────────────────────────────────────
def test_dot_dot_cannot_climb_out(granted):
    out = run_tool("read_file", {"path": str(granted / ".." / ".." / "etc" / "hosts")})
    assert not out.ok
    assert "outside every folder" in out


def test_an_absolute_path_elsewhere_is_refused(granted):
    out = run_tool("write_file", {"path": "/tmp/chitragupta-escape.txt",
                                  "content": "should never be written"})
    assert not out.ok
    assert not Path("/tmp/chitragupta-escape.txt").exists()


def test_a_symlink_planted_inside_the_folder_does_not_escape(granted, tmp_path):
    """Writing into a granted folder is allowed, so planting a link is reachable."""
    secret = tmp_path / "outside.txt"
    secret.write_text("private", encoding="utf-8")
    (granted / "innocent.txt").symlink_to(secret)

    out = run_tool("read_file", {"path": str(granted / "innocent.txt")})
    assert not out.ok, "a symlink read straight out of the granted folder"
    assert "outside every folder" in out


def test_writing_through_a_symlinked_directory_is_refused(granted, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (granted / "link").symlink_to(elsewhere)

    out = run_tool("write_file", {"path": str(granted / "link" / "x.txt"),
                                  "content": "nope"})
    assert not out.ok
    assert not (elsewhere / "x.txt").exists()


# ── the happy path ───────────────────────────────────────────────────────
def test_reading_and_writing_inside_the_folder_works(granted):
    assert "quarterly" in run_tool("read_file", {"path": str(granted / "notes.md")})

    out = run_tool("write_file", {"path": str(granted / "out.csv"),
                                  "content": "a,b\n1,2\n"})
    assert out.ok
    assert (granted / "out.csv").read_text() == "a,b\n1,2\n"


def test_listing_with_no_path_shows_which_folders_are_open(granted):
    out = run_tool("list_dir", {})
    assert out.ok
    assert str(granted) in out


def test_a_binary_file_is_refused_rather_than_handed_over_as_noise(granted):
    (granted / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out = run_tool("read_file", {"path": str(granted / "photo.png")})
    assert not out.ok
    assert "not a text file" in out


def test_a_long_file_is_cut_and_says_so(granted, monkeypatch):
    monkeypatch.setattr(file_tools, "MAX_READ_CHARS", 100)
    (granted / "big.txt").write_text("x" * 5000, encoding="utf-8")
    out = run_tool("read_file", {"path": str(granted / "big.txt")})
    assert out.ok and out.truncated
    assert "Cut here" in out, "the model was not told it had seen only part"


def test_an_oversized_write_is_refused(granted):
    out = run_tool("write_file", {"path": str(granted / "huge.txt"),
                                  "content": "x" * (file_tools.MAX_WRITE_CHARS + 1)})
    assert not out.ok
    assert not (granted / "huge.txt").exists()


# ── the interpreter ──────────────────────────────────────────────────────
def test_the_scratchpad_does_arithmetic_a_model_would_guess_at():
    out = run_tool("run_python", {"code": "print(sum(int(x) for x in '12345'))"})
    assert out.ok, out
    assert out.strip() == "15"


def test_a_failing_snippet_returns_the_error_so_it_can_be_fixed():
    out = run_tool("run_python", {"code": "1/0"})
    assert not out.ok
    assert "ZeroDivisionError" in out


def test_a_snippet_that_prints_nothing_says_so():
    out = run_tool("run_python", {"code": "x = 1 + 1"})
    assert out.ok
    assert "printed nothing" in out


def test_an_endless_loop_is_stopped(monkeypatch):
    monkeypatch.setattr("chitragupta.agents.code_tools.TIMEOUT_SECONDS", 2)
    out = run_tool("run_python", {"code": "while True: pass"})
    assert not out.ok
    assert "without finishing" in out


def test_the_snippet_cannot_reach_chitragupta_or_the_users_brain():
    """-I and -S keep it out of this process's world."""
    out = run_tool("run_python", {
        "code": "import chitragupta; print('reached', chitragupta.__file__)"})
    assert not out.ok, "a snippet imported the app it is running inside"
    assert "ModuleNotFoundError" in out or "ImportError" in out


def test_empty_code_is_a_failure_not_a_run():
    assert not run_tool("run_python", {"code": "   "}).ok


# ── consent ──────────────────────────────────────────────────────────────
def test_only_agents_whose_job_is_code_can_run_it_and_the_library_says_so():
    """Adding one of these from the library IS the consent — so the card must
    say what it does before the user adds it, not after."""
    from chitragupta.agents.library import describe

    coders = {t["id"] for t in describe(include_status=False) if t["runs_code"]}
    assert coders, "no template declares that it runs code"
    for agent in PRESETS.values():
        if "run_python" in agent.tools:
            assert agent.id in coders, (
                f"{agent.id} runs code but its library card does not say so")
        else:
            assert agent.id not in coders


def test_an_agent_that_declares_the_file_tools_really_gets_them():
    """Safe to ship with, because they reach nothing until a folder is granted."""
    checked = 0
    for agent in PRESETS.values():
        if "read_file" not in agent.tools:
            continue
        checked += 1
        names = {t.name for t in build_tools(agent.tools, self_id=agent.id)}
        assert {"read_file", "write_file", "list_dir"} <= names
    assert checked >= 3, "almost no shipped agent can touch a file"


def test_every_new_tool_is_declared_and_implemented():
    for name in ("list_dir", "read_file", "write_file", "run_python"):
        assert name in TOOL_DEFS and name in TOOL_IMPLS
