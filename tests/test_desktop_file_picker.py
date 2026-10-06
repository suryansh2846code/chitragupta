"""Choosing a backup file through a real macOS panel.

The panel itself is Cocoa and needs a live window, so what is tested here is
the two things around it that can be wrong without anybody noticing: the name a
save panel opens with, and reading one path out of whatever pywebview answered.

**Why the panel is on the pywebview bridge rather than behind an endpoint.**
`NSOpenPanel` hands back only what the user chose, so offering a picker costs
nothing in exposure — where an endpoint would have had to list the user's files,
and `/api/fs/browse` was deliberately written directories-only. The same
argument `desktop.py::open_privacy_settings` already makes for itself.

**And why it is safe from a worker thread.** `js_api` calls arrive off the main
thread, and CLAUDE.md's desktop invariant is that every Cocoa mutation goes
through `AppHelper.callAfter` or the process hangs. pywebview's
`create_file_dialog` does that itself when its `main_thread` argument is left
at its default of False — it `callAfter`s the panel and blocks on a semaphore.
`test_pywebview_still_dispatches_the_panel_itself` pins that, because it is a
fact about somebody else's library and an upgrade could change it.
"""
from __future__ import annotations

import inspect

import pytest

from chitragupta.desktop import (
    BACKUP_SUFFIX,
    DEFAULT_BACKUP_NAME,
    backup_filename,
    first_path,
)

# ── the name a save panel opens with ────────────────────────────────────────

def test_an_empty_suggestion_still_names_a_file():
    assert backup_filename() == DEFAULT_BACKUP_NAME
    assert backup_filename("") == DEFAULT_BACKUP_NAME
    assert backup_filename("   ") == DEFAULT_BACKUP_NAME


def test_a_suggestion_is_used_as_given():
    assert backup_filename("mine.cgarch") == "mine.cgarch"


def test_a_name_without_the_suffix_gets_one():
    """A user who clears the field and types `brain` should still get a file the
    restore screen recognises."""
    assert backup_filename("brain") == "brain" + BACKUP_SUFFIX
    assert backup_filename(" spaced ") == "spaced" + BACKUP_SUFFIX


def test_the_suffix_is_not_doubled():
    assert backup_filename("a.cgarch").count(BACKUP_SUFFIX) == 1


# ── reading one path out of a dialog's answer ───────────────────────────────

@pytest.mark.parametrize("answer", [None, (), [], "", False])
def test_cancelling_is_an_empty_string_not_an_error(answer):
    """Cancelling is the user deciding, not a failure — and the page leaves
    whatever they had already typed alone when it sees ""."""
    assert first_path(answer) == ""


def test_a_sequence_answer_gives_its_first_path():
    assert first_path(("/Users/x/a.cgarch",)) == "/Users/x/a.cgarch"
    assert first_path(["/Users/x/a.cgarch", "/Users/x/b.cgarch"]) \
        == "/Users/x/a.cgarch"


def test_a_bare_string_answer_is_accepted_too():
    """SAVE_DIALOG has answered with a plain string in some pywebview versions
    and a one-item sequence in others, so neither shape is assumed."""
    assert first_path("/Users/x/out.cgarch") == "/Users/x/out.cgarch"


def test_a_sequence_holding_nothing_useful_is_empty():
    assert first_path((None,)) == ""
    assert first_path(("",)) == ""



def test_a_suggested_name_can_never_be_a_path():
    """The one bridge method that takes an argument, so the one that has to
    prove the argument cannot point anywhere.

    `test_full_disk_access_refusal.py` pins why `open_privacy_settings` takes
    none at all: an argument is a thing a page can redirect. A filename cannot
    redirect a save panel — the user still picks the directory and confirms —
    but only while it stays a filename, so every separator and every `..` is
    stripped before it reaches a filesystem API.
    """
    for probe in ("../../etc/passwd", "/etc/passwd", "a/b/c.cgarch",
                  "..", ".", "/", "////", "C:\\\\Windows\\\\x"):
        got = backup_filename(probe)
        assert "/" not in got, f"{probe!r} produced a path: {got!r}"
        assert "\\\\" not in got, f"{probe!r} produced a path: {got!r}"
        assert not got.startswith(".."), f"{probe!r} still climbs: {got!r}"
        assert got.endswith(BACKUP_SUFFIX)


def test_a_path_shaped_suggestion_keeps_its_basename():
    assert backup_filename("/Users/x/Desktop/mine.cgarch") == "mine.cgarch"
    assert backup_filename("../brain") == "brain" + BACKUP_SUFFIX


def test_a_suggestion_that_is_only_separators_falls_back():
    assert backup_filename("/") == DEFAULT_BACKUP_NAME
    assert backup_filename("..") == DEFAULT_BACKUP_NAME

# ── the invariant that lives in somebody else's library ─────────────────────

def test_pywebview_still_dispatches_the_panel_itself():
    """The panel must reach the main thread via `AppHelper.callAfter`.

    We call `create_file_dialog` from a `js_api` worker thread. CLAUDE.md:
    *every Cocoa mutation goes through `AppHelper.callAfter`, or the process
    hangs* — and here that dispatch is pywebview's to do, not ours. Its
    default (`main_thread=False`) `callAfter`s the panel and waits on a
    semaphore; the other branch calls `runModal()` inline, which is the hang.

    So this asserts the shape of a dependency rather than our own code, on
    purpose: if an upgrade flips that default, a backup picker would freeze the
    whole app and nothing else in the suite would notice.
    """
    cocoa = pytest.importorskip("webview.platforms.cocoa",
                                reason="pywebview's macOS backend is needed")
    source = inspect.getsource(cocoa)
    start = source.index("    def create_file_dialog(")
    body = source[start:source.index("\n    def ", start + 10)]

    assert "main_thread=False" in body, \
        "pywebview no longer defaults create_file_dialog off the main thread"
    assert "AppHelper.callAfter" in body, \
        "pywebview no longer marshals the file panel onto the main thread"

    # And we must never opt into the inline branch. Checked through the AST
    # rather than by searching the text: `desktop.py` has a comment saying
    # "never pass main_thread=True", and a substring search matched that
    # comment and failed on the very prose explaining the rule.
    import ast

    from chitragupta import desktop

    tree = ast.parse(inspect.getsource(desktop))
    dialogs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_file_dialog"
    ]
    assert dialogs, "no file dialog call found — has the picker moved?"
    for call in dialogs:
        passed = {kw.arg for kw in call.keywords}
        assert "main_thread" not in passed, (
            "passing main_thread runs runModal() on the js_api worker thread, "
            "which is the hang CLAUDE.md's desktop invariant is about")
