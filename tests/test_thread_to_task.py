"""Job 13 — "turn this thread into a task".

Every part of this existed and the job still did not work, for two reasons
that are easy to miss because neither throws:

**`add_task` was a tool, not an action.** So a task never appeared in the
action log — *"what did you do this week?"* has never once mentioned one —
there was no card to correct before it landed, and nothing could take one
back. Rungs 4 to 6 missing from a capability that looked finished at 3.

**Nothing carried the thread across.** `read_thread` could read it, `add_task`
could store a sentence, and the sentence was all that survived. Three weeks
later the task says *"send Rahul the revised figures"* and the user goes and
searches their inbox for the conversation — which is exactly the work they
asked to have taken off them.

So the capability is the link, and most of this file is about the link
surviving each hop: action → store → tool listing → the task row on screen.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import REGISTRY, Risk, run_now
from chitragupta.agents.approvals import describe
from chitragupta.tasks import get_tasks


@pytest.fixture(autouse=True)
def clean_tasks():
    store = get_tasks()
    for task in store.list(include_done=True):
        store.delete(task["id"])
    yield
    for task in store.list(include_done=True):
        store.delete(task["id"])


def _make(**params):
    return run_now("create_task", {"title": "Send the figures", **params})


# ── the link, hop by hop ─────────────────────────────────────────────────
def test_the_thread_survives_into_the_task():
    result = _make(thread_id="t7")

    task = get_tasks().get(result["id"])
    assert task["source"] == "email"
    assert task["source_ref"] == "t7"


def test_a_task_with_no_thread_is_not_labelled_as_email():
    """A task typed into the box has no origin, and an empty origin is not a
    missing value — the UI decides whether to offer a link on exactly this."""
    task = get_tasks().get(_make()["id"])

    assert not task["source"]
    assert not task["source_ref"]


def test_the_tool_listing_names_the_thread_so_an_agent_can_read_it():
    """`read_thread` needs the id. An agent asked "what was that about?" that
    cannot get from the task to the conversation has to guess."""
    from chitragupta.agents.tools import TOOL_IMPLS

    _make(thread_id="t7")

    listing = TOOL_IMPLS["list_tasks"]()
    assert "t7" in listing


def test_an_ordinary_task_does_not_clutter_the_listing():
    from chitragupta.agents.tools import TOOL_IMPLS

    _make()

    assert "from email thread" not in TOOL_IMPLS["list_tasks"]()


# ── rungs 4 to 6, which is what the action buys ──────────────────────────
def test_it_is_green_because_it_reaches_nobody():
    """One row in the user's own list. There is nobody to allow-list, so
    asking would be a card with no decision on it."""
    assert REGISTRY["create_task"].risk is Risk.GREEN


def test_it_is_verified_by_reading_it_back():
    """A task the store silently dropped is one the user is counting on and
    will not find."""
    assert _make()["verified"] is True


def test_it_lands_in_the_action_log():
    """The whole reason this is an action. A tool call leaves no row, so
    "what did you do this week?" never mentioned a task."""
    from chitragupta import action_log

    result = _make(thread_id="t7")

    assert result.get("log_id")
    assert any(row["action_type"] == "create_task"
               for row in action_log.recent(10, since=""))


def test_it_can_be_taken_back():
    result = _make()
    assert result["reversible"] is True

    undone = REGISTRY["create_task"].undo({}, result)

    assert undone["ok"]
    assert get_tasks().get(result["id"]) is None


def test_undo_deletes_rather_than_completes():
    """Deliberately the opposite of `create_followup`, which cancels.

    A cancelled follow-up is provenance — the user really was waiting on
    somebody. A task created by mistake is not a task they finished, and
    marking it done would put work they never did into "what did you do this
    week?".
    """
    result = _make()
    REGISTRY["create_task"].undo({}, result)

    assert get_tasks().list(include_done=True) == []


# ── the card ─────────────────────────────────────────────────────────────
def test_the_card_says_what_the_task_is():
    """Read weeks later in the log, where "Add task" names nothing."""
    line = describe("create_task", {"title": "Send the figures", "due": "friday"})

    assert "Send the figures" in line
    assert "friday" in line
    assert "create_task" not in line, "an internal reached the user"


def test_the_card_does_not_show_the_thread_id():
    """`t7` means nothing to a person. The task row carries the link they can
    actually use; the card carries the decision."""
    line = describe("create_task", {"title": "Send the figures", "thread_id": "t7"})

    assert "t7" not in line


# ── the due date the thread stated ───────────────────────────────────────
def test_a_due_date_is_kept():
    task = get_tasks().get(_make(due="friday")["id"])

    assert task["due"]


def test_a_task_with_no_title_is_refused_rather_than_stored_blank():
    assert run_now("create_task", {"thread_id": "t7"})["ok"] is False


# ── an existing database opens unchanged ─────────────────────────────────
def test_a_tasks_db_written_before_this_shipped_still_opens(tmp_path):
    """The migration is additive, and the failure mode if it is not is that
    the app will not start for anybody who already had tasks."""
    import sqlite3

    path = tmp_path / "tasks.db"
    old = sqlite3.connect(str(path))
    old.executescript(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "due TEXT, done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, "
        "done_at TEXT);")
    old.execute("INSERT INTO tasks (id,title,done,created_at) "
                "VALUES ('old1','from before',0,'2026-01-01')")
    old.commit()
    old.close()

    from chitragupta.tasks import TaskStore

    store = TaskStore(db_path=path)

    kept = store.get("old1")
    assert kept["title"] == "from before"
    assert kept["source"] is None, "the new column is absent, not invented"
    assert store.add("something new", source="email", source_ref="t9")
