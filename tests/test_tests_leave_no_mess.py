"""The suite cleans up after itself, and the guard that does it is covered.

Ten places called `tempfile.mkdtemp()` and none removed anything. Most are once
per session; `automation_harness.fresh_store()` is once per *test*, and a
Chitragupta home is ~560 MB by the time a brain, an agents database and an
action log are in it.

An afternoon of running the suite left **29,474 directories and 64 GB**, and
took the machine to zero bytes free — which stops the whole computer rather than
the test run, and nothing in the output said the suite had done it. Measured
after the fix: one 417-test run leaves **321** directories without it and
**none** with it.

A guard nobody exercises quietly stops working, and this one is invisible when
it does work — nothing fails, there is just less on the disk. So the pieces are
exercised directly: what gets recorded, what gets spared, and what the teardown
removes.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import conftest


def test_every_directory_the_suite_makes_goes_through_the_wrapper():
    """The reason this is one wrapper rather than ten fixes at ten call sites:
    the eleventh is the one that matters, and its author will not know there
    was a rule."""
    assert tempfile.mkdtemp is conftest._tracked_mkdtemp


def test_a_directory_a_test_makes_is_recorded():
    made = tempfile.mkdtemp(prefix="probe-")
    try:
        assert made in conftest._MADE
    finally:
        Path(made).rmdir()


def test_pytests_own_directories_are_left_alone():
    """`tmp_path` is what a developer reads after a failure, and pytest already
    keeps only the last three runs. Taking those away would make the one case
    this must not make worse, worse."""
    spared = conftest._tracked_mkdtemp(prefix="pytest-of-someone-")
    try:
        assert spared not in conftest._MADE
    finally:
        Path(spared).rmdir()


def test_the_teardown_removes_what_was_recorded(monkeypatch):
    """Driven directly, because the real one runs once at the end of the
    session — after this file has finished, which is too late to assert on."""
    monkeypatch.delenv("CHITRAGUPTA_KEEP_TEST_DIRS", raising=False)
    monkeypatch.setattr(conftest, "_MADE", [])

    doomed = tempfile.mkdtemp(prefix="probe-")
    (Path(doomed) / "a-database.db").write_text("x")   # not empty, like a home
    assert doomed in conftest._MADE

    teardown = conftest._leave_no_directories_behind.__wrapped__()
    next(teardown)
    next(teardown, None)                               # run the finaliser

    assert not Path(doomed).exists()


def test_the_escape_hatch_keeps_them(monkeypatch, capsys):
    """For the afternoon you are reading a database a failing test left
    behind — and it says so, rather than leaving somebody to wonder why the
    disk did not come back."""
    monkeypatch.setenv("CHITRAGUPTA_KEEP_TEST_DIRS", "1")
    monkeypatch.setattr(conftest, "_MADE", [])

    kept = tempfile.mkdtemp(prefix="probe-")
    try:
        teardown = conftest._leave_no_directories_behind.__wrapped__()
        next(teardown)
        next(teardown, None)

        assert Path(kept).exists()
        assert "kept 1 test director" in capsys.readouterr().out
    finally:
        Path(kept).rmdir()


def test_a_directory_that_cannot_be_removed_does_not_fail_the_run(monkeypatch):
    """Disk somebody clears by hand later is a nuisance. A teardown that raises
    turns a green run red for a reason that has nothing to do with the code."""
    monkeypatch.delenv("CHITRAGUPTA_KEEP_TEST_DIRS", raising=False)
    monkeypatch.setattr(conftest, "_MADE", ["/nowhere/that/exists"])

    teardown = conftest._leave_no_directories_behind.__wrapped__()
    next(teardown)
    next(teardown, None)          # must not raise
