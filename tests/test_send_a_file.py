"""“Take the latest proposal and send it to Rahul” — job 4.

Two questions, and the dangerous one is the first. Attaching is easy;
**picking the wrong draft is discovered by the recipient, not by the user**,
and by then it is a document somebody else has read.

So rung 3 here is not "find a file". It is *say which one and when it was
modified, before it goes* — a filename alone is not evidence, because
`proposal.pdf` and `proposal.pdf` in two folders are the same sentence and a
week apart.

The search itself has to stay inside the folder grants, like every other way
this app touches the disk: `find_file` searches `granted_roots()` and nothing
else, so an agent reading a stranger's email cannot be talked into finding
`id_rsa`.
"""
from __future__ import annotations

import os
import time

import pytest

from chitragupta.agents import file_tools as ft


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A folder of proposals, aged so "latest" means something."""
    root = tmp_path / "Work"
    (root / "2026" / "Q3").mkdir(parents=True)
    (root / "node_modules" / "junk").mkdir(parents=True)

    made = {
        "newest": (root / "2026" / "Q3" / "Acme proposal.pdf", 100),
        "middle": (root / "proposal-draft.md", 300),
        "oldest": (root / "2026" / "old proposal.pdf", 9000),
        "vendored": (root / "node_modules" / "junk" / "proposal.md", 1),
        "unrelated": (root / "notes.md", 50),
        "hidden": (root / ".proposal.swp", 1),
    }
    for path, age_hours in made.values():
        path.write_text("x" * 40)
        when = time.time() - age_hours * 3600
        os.utime(path, (when, when))

    monkeypatch.setattr(ft, "granted_roots", lambda: [str(root)])
    return {name: path for name, (path, _) in made.items()}


def _found(**kw):
    return str(ft.find_file(**kw))


# ── choosing ───────────────────────────────────────────────────────────────

def test_the_newest_match_comes_first(library):
    said = _found(name="proposal")
    first = said.splitlines()[1]
    assert "Acme proposal.pdf" in first


def test_it_can_be_asked_for_the_oldest_instead(library):
    said = _found(name="proposal", newest_first=False)
    assert "old proposal.pdf" in said.splitlines()[1]


def test_every_match_carries_the_date_that_decides_which_is_latest(library):
    """"The latest" is a judgement, and the user is the one who knows whether
    it is right. They cannot check it against a bare filename."""
    said = _found(name="proposal")
    # The row prefix, not the word: the closing hint says "modified" too.
    assert said.count("\n  modified ") == 3
    assert "bytes" in said


def test_the_full_path_is_given_because_that_is_what_gets_attached(library):
    said = _found(name="proposal")
    assert str(library["newest"]) in said


def test_it_tells_the_agent_to_name_the_one_it_picked(library):
    """The rule this whole job turns on."""
    said = _found(name="proposal")
    assert "Say WHICH one you picked" in said
    assert "look identical in a sentence" in said


def test_several_words_all_have_to_match(library):
    assert "Acme proposal.pdf" in _found(name="acme proposal")
    assert "proposal-draft" not in _found(name="acme proposal")


def test_punctuation_in_the_query_is_not_taken_literally(library):
    """A model asked for "the proposal" will type `proposal-draft.md` as often
    as `proposal draft`."""
    assert "proposal-draft.md" in _found(name="proposal_draft")
    assert "proposal-draft.md" in _found(name="proposal.draft")


# ── what it refuses to search ──────────────────────────────────────────────

def test_vendored_folders_are_not_searched(library):
    """A search returning forty node_modules files has answered a different
    question than the one asked."""
    assert "node_modules" not in _found(name="proposal")


def test_hidden_files_are_not_offered(library):
    assert ".proposal.swp" not in _found(name="proposal")


def test_nothing_outside_a_granted_folder_is_reachable(tmp_path, monkeypatch):
    """The same boundary as every other way this app touches the disk — an
    agent reading a stranger's email must not be talkable into finding
    `id_rsa`."""
    granted = tmp_path / "Work"
    granted.mkdir()
    (granted / "proposal.md").write_text("x")
    secret = tmp_path / "Private"
    secret.mkdir()
    (secret / "proposal-secret.md").write_text("x")

    monkeypatch.setattr(ft, "granted_roots", lambda: [str(granted)])
    said = _found(name="proposal")
    assert "proposal.md" in said
    assert "proposal-secret" not in said


def test_with_no_folder_opened_it_says_so_rather_than_finding_nothing(monkeypatch):
    """"Nothing matched" and "you have not opened a folder" are different
    answers, and only one of them tells the user what to do."""
    monkeypatch.setattr(ft, "granted_roots", list)
    said = _found(name="proposal")
    assert "No folder has been opened" in said


def test_a_search_for_nothing_is_refused(library):
    assert "Say what to look for" in _found(name="")


def test_no_match_names_where_it_looked(library):
    said = _found(name="invoice")
    assert "Nothing matching" in said
    assert "Work" in said


def test_a_long_list_is_capped_and_says_so(tmp_path, monkeypatch):
    root = tmp_path / "Many"
    root.mkdir()
    for n in range(20):
        (root / f"proposal-{n}.md").write_text("x")
    monkeypatch.setattr(ft, "granted_roots", lambda: [str(root)])
    said = _found(name="proposal")
    assert said.count("\n  modified ") == ft.MAX_MATCHES
    assert f"and {20 - ft.MAX_MATCHES} more" in said


def test_a_folder_that_cannot_be_walked_does_not_take_the_search_down(
        tmp_path, monkeypatch):
    monkeypatch.setattr(ft, "granted_roots",
                        lambda: [str(tmp_path / "gone"), str(tmp_path)])
    (tmp_path / "proposal.md").write_text("x")
    assert "proposal.md" in _found(name="proposal")


# ── who is taught the job ──────────────────────────────────────────────────

def test_an_agent_that_can_read_files_can_also_find_one():
    """One that can read and cannot find can only open what it was handed the
    path to — and "the latest proposal" is a description, not a path."""
    from chitragupta.agents.library import TEMPLATES

    for template in TEMPLATES:
        tools = template.resolved_tools()
        if "read_file" in tools:
            assert "find_file" in tools, template.id


def test_an_agent_that_can_find_and_send_gets_the_recipe():
    from chitragupta.agents.prompt import _attach_recipe

    assert _attach_recipe(["find_file"], ["create_draft"])


@pytest.mark.parametrize("tools,acts", [
    ([], ["send_email"]),            # cannot find
    (["find_file"], []),             # cannot send
    (["read_file"], ["send_email"]),  # can read, cannot search
])
def test_an_agent_missing_a_half_is_not_taught_it(tools, acts):
    from chitragupta.agents.prompt import _attach_recipe

    assert _attach_recipe(tools, acts) == ""


def test_the_recipe_forbids_guessing_a_path():
    from chitragupta.agents.prompt import _ATTACH_RECIPE

    assert "Never attach a path you did not get from it" in _ATTACH_RECIPE


def test_the_recipe_requires_naming_the_version_before_the_card():
    from chitragupta.agents.prompt import _ATTACH_RECIPE

    assert "WHICH you chose and WHEN it was last" in _ATTACH_RECIPE


def test_the_recipe_says_to_ask_when_the_choice_is_close():
    """"The latest" is only obvious when it is. Two drafts an hour apart is a
    question, not a decision to make on somebody's behalf."""
    from chitragupta.agents.prompt import _ATTACH_RECIPE

    assert "ASK rather than choose" in _ATTACH_RECIPE


def test_the_tool_description_carries_the_same_warning():
    from chitragupta.agents.tools import TOOL_DEFS

    assert "which one you picked" in TOOL_DEFS["find_file"].description


# ── the scorecard ──────────────────────────────────────────────────────────

def test_sending_a_file_is_on_the_scorecard():
    from chitragupta.agents import evaluation

    card = evaluation.run(include_slow=False)
    failed = [c.detail for c in card.checks
              if c.key.startswith("attach") and not c.passed]
    assert not failed, failed
    assert {"attach_searches", "attach_names_the_version",
            "attach_sends_what_it_named"} <= {c.key for c in card.checks}


def test_the_attachment_still_goes_through_the_folder_grants():
    """`find_file` hands back a path and `_attachments_for` re-resolves it.
    Two checks rather than one on purpose: the search could be widened one day
    and the send must not widen with it."""
    import inspect

    from chitragupta import actions

    block = inspect.getsource(actions._attachments_for)
    assert "_resolve" in block


def test_a_path_find_file_would_never_return_is_still_refused(tmp_path, monkeypatch):
    from chitragupta import actions

    granted = tmp_path / "Work"
    granted.mkdir()
    monkeypatch.setattr(ft, "granted_roots", lambda: [str(granted)])
    _, problem = actions._attachments_for({"attach": "/etc/hosts"})
    assert "outside" in problem


def test_a_file_that_was_found_can_be_attached(library, monkeypatch):
    from chitragupta import actions

    attachments, problem = actions._attachments_for(
        {"attach": str(library["newest"])})
    assert problem == ""
    assert attachments[0]["name"] == "Acme proposal.pdf"


def test_the_card_names_the_file_the_user_is_approving(library):
    from chitragupta.agents.approvals import describe

    line = describe("send_email", {"to": "rahul@work.test",
                                   "subject": "The proposal",
                                   "attach": str(library["newest"])})
    assert "Acme proposal.pdf" in line
    assert str(library["newest"].parent) not in line, "it printed the whole path"
