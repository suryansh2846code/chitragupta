"""Agents run side by side, and switching between them is always free.

The workspace kept one `busy` flag, one AbortController and one turn id for the
whole window. Two consequences, and the user hit both every time they used more
than one agent: `selectAgent` refused outright while a reply was being written
("finishing current reply…"), and nothing else could be started either. A team
of agents sharing one brain was, in practice, one agent at a time.

Nothing below the window had to change for it. `api/concurrency.py` has given
model turns a lane of eight since it was written, and a routine has always been
able to chat while a person does.

Every claim here is about what happens *while two turns are genuinely in
flight*, which is why the turns are held open rather than reasoned about:

* switching away from a working agent works, and the composer belongs to the
  agent on screen rather than to the last one to start a turn;
* two agents really do run at once;
* a reply that lands for an agent the user is not looking at is never written
  into the agent they *are* looking at;
* going back to a working agent shows its question and that it is still working
  — neither of which is in the stored history yet;
* and the half-written message stays with the agent it was addressed to.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"


@pytest.fixture(scope="module")
def steps() -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/concurrent_agents.mjs"), str(WEB / "app.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    report = json.loads(proc.stdout)
    assert report["error"] is None, report["error"]
    return {s["name"]: s for s in report["steps"]}


def test_switching_away_from_a_working_agent_is_allowed(steps):
    """The refusal this whole change exists to remove."""
    assert steps["switched-to-beta"]["current"] == "beta", (
        "the switch did not happen: a working agent still owns the window"
    )
    assert "alpha" in steps["switched-to-beta"]["turns"], (
        "the switch cancelled the turn instead of leaving it running"
    )


def test_two_agents_work_at_the_same_time(steps):
    both = steps["both-working"]
    assert both["turns"] == ["alpha", "beta"], both["turns"]
    assert both["chatPaths"] == ["alpha", "beta"], (
        f"both requests must be out, neither queued behind the other: {both['chatPaths']}"
    )


@pytest.mark.parametrize(
    ("step", "expected"),
    [("alpha-working", True),        # its own turn is running
     ("switched-to-beta", False),    # a different, idle agent
     ("both-working", True),         # this one is running too
     ("back-on-working-alpha", True),
     # Alpha has just FINISHED, and beta — the agent on screen — has not. A turn
     # ending must not free a composer that belongs to somebody else.
     ("alpha-landed-offscreen", True),
     ("back-on-alpha", False)],      # done, while beta is still going
)
def test_the_composer_belongs_to_the_agent_on_screen(steps, step, expected):
    """Not to the last agent to start a turn, and not to any of the others.

    `back-on-alpha` is the one that matters most: beta is still working, and the
    composer must still be usable because beta is not what is on screen.
    """
    assert steps[step]["inputDisabled"] is expected, step


@pytest.mark.parametrize(
    ("step", "reply"),
    [("alpha-landed-offscreen", "ALPHA ANSWER"),
     ("beta-landed-offscreen", "BETA ANSWER")],
)
def test_a_reply_never_lands_in_another_agents_chat(steps, step, reply):
    """Parametrised over both directions, because it is a rule and not a case."""
    assert reply not in steps[step]["screen"], (
        f"{reply} was written into the conversation of the agent on screen"
    )


def test_an_unread_reply_is_flagged_rather_than_dropped(steps):
    assert steps["alpha-landed-offscreen"]["landed"] == ["alpha"]
    assert steps["alpha-landed-offscreen"]["turns"] == ["beta"], "alpha's turn is over"
    # Opened, so read.
    assert steps["back-on-alpha"]["landed"] == []
    assert "ALPHA ANSWER" in steps["back-on-alpha"]["screen"], (
        "the reply has to come back from the stored history"
    )


def test_returning_to_a_working_agent_shows_the_turn_it_is_running(steps):
    """The answer is not in the stored history yet, so it has to come off the
    turn's own record — or walking away loses the only copy of the reply-so-far.
    """
    back = steps["back-on-working-alpha"]
    assert "what did Alpha find?" in back["screen"], "the question did not come back"
    assert "think-bar" in back["screen"], (
        "the agent is still working and the transcript does not say so"
    )


def test_the_question_is_drawn_once(steps):
    """`run_turn` appends the question BEFORE it calls the model.

    So the transcript fetched for a working agent already has it, and a client
    that adds its own copy on top shows the user their question twice. The
    trailing role is what says a turn is in flight — the answer is what is
    missing, never the question.
    """
    back = steps["back-on-working-alpha"]
    assert back["screen"].count("what did Alpha find?") == 2, (
        "once as the message text and once as its dataset.raw — any more is a "
        f"second copy of the question:\n{back['screen']}"
    )


def test_the_composer_does_not_wait_for_the_network(steps):
    """Selecting an agent is immediate; its transcript is not.

    `/api/agents/{id}/connectors` measures 4.5s on a real machine and
    `/api/connectors` 9.8s behind it, so a handful of switches fill the
    browser's six-per-host connection pool and the transcript fetch queues.
    Repainting the composer only after that arrived left an idle agent's input
    disabled by the agent the user had left — which reads as the click having
    been ignored.
    """
    st = steps["beta-selected-history-pending"]
    assert st["current"] == "beta", "the selection itself has to be immediate"
    assert st["inputDisabled"] is False, (
        "beta is idle and selected; its composer must be usable before its "
        "transcript arrives"
    )
    assert st["draft"] == "half-written note to Beta", (
        "the draft is local state and must not wait on a fetch"
    )


@pytest.mark.parametrize(
    ("step", "draft"),
    [("switched-to-beta", ""),                            # beta has none
     ("back-on-working-alpha", "half-written note to Alpha"),
     ("beta-selected-history-pending", "half-written note to Beta"),
     ("back-on-alpha", "half-written note to Alpha")],
)
def test_a_draft_stays_with_the_agent_it_was_written_for(steps, step, draft):
    assert steps[step]["draft"] == draft, step
