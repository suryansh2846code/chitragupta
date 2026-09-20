"""Jobs 2 and 3 — the two ways a person asks for a reply.

    "Draft replies to anything waiting on me"   → `_REPLY_RECIPE`
    "Reply to this thread saying X"             → `_THREAD_REPLY_RECIPE`

Both are assembly rather than new capability: `needs_reply`, `read_thread`,
`create_draft`, threading and `<plan>` all existed. **Four capabilities is not
the same as the job**, which is the argument `_INBOX_RECIPE` already makes —
an agent holding every part still has to be told the order, and the order is
where both of these go wrong.

Job 2's failure is drafting an answer to a conversation the user already
finished. Job 3's is an envelope failure: a reply that starts a new thread, or
that goes to whoever started the conversation instead of whoever asked last.

A recipe is only handed to an agent that can follow every line of it — a
recipe for a capability an agent half-has is worse than none, because the
agent describes doing the part it cannot do. So each gate is tested from both
sides.
"""
from __future__ import annotations

import pytest

from chitragupta.agents.prompt import (
    _REPLY_RECIPE,
    _THREAD_REPLY_RECIPE,
    _reply_recipe,
    _thread_reply_recipe,
    build,
)

FULL_TOOLS = ["list_mail", "read_thread", "needs_reply", "gmail_search"]


def _prompt_for(agent_id: str) -> str:
    from chitragupta.agents.presets import get_agent

    agent = get_agent(agent_id)
    return build(name=agent.name, role=agent.role,
                 system_prompt=agent.system_prompt, tools=agent.tools,
                 actions=agent.actions, agent_id=agent.id)


# ── who is taught job 2 ──────────────────────────────────────────────────
def test_an_agent_that_can_check_and_draft_gets_the_reply_recipe():
    assert _reply_recipe(FULL_TOOLS, ["create_draft"]) == _REPLY_RECIPE


@pytest.mark.parametrize("tools,actions", [
    (["list_mail", "read_thread"], ["create_draft"]),   # cannot check
    (FULL_TOOLS, ["send_email"]),                       # cannot draft
    (FULL_TOOLS, []),                                   # can only read
])
def test_an_agent_missing_a_piece_is_not_taught_job_two(tools, actions):
    """Rule 1 is "call `needs_reply` first". An agent without it would be told
    to do something it has no way to do, and the likely outcome is the exact
    failure the rule exists to prevent — working from `list_mail` instead.
    """
    assert _reply_recipe(tools, actions) == ""


# ── who is taught job 3 ──────────────────────────────────────────────────
def test_an_agent_that_can_read_a_thread_and_answer_gets_the_thread_recipe():
    assert _thread_reply_recipe(FULL_TOOLS, ["create_draft"]) == \
        _THREAD_REPLY_RECIPE
    assert _thread_reply_recipe(FULL_TOOLS, ["send_email"]) == \
        _THREAD_REPLY_RECIPE


@pytest.mark.parametrize("tools,actions", [
    (["list_mail", "gmail_search"], ["create_draft"]),  # cannot read a thread
    (FULL_TOOLS, ["create_event"]),                     # cannot answer
])
def test_an_agent_missing_a_piece_is_not_taught_job_three(tools, actions):
    assert _thread_reply_recipe(tools, actions) == ""


# ── the shipped agents ───────────────────────────────────────────────────
@pytest.mark.parametrize("heading", ["DRAFTING REPLIES", "REPLYING TO ONE THREAD"])
def test_the_inbox_agent_is_taught_both(heading):
    assert heading in _prompt_for("inbox")


@pytest.mark.parametrize("heading", ["DRAFTING REPLIES", "REPLYING TO ONE THREAD"])
def test_the_generalist_is_taught_both(heading):
    """Chief of Staff holds every tool, so it must hold every recipe too — a
    generalist that can do the job and was not told how is the case the
    recipes exist for."""
    assert heading in _prompt_for("chief-of-staff")


@pytest.mark.parametrize("heading", ["DRAFTING REPLIES", "REPLYING TO ONE THREAD"])
def test_an_agent_with_no_mail_is_taught_neither(heading):
    assert heading not in _prompt_for("research")


@pytest.mark.parametrize("heading", ["DRAFTING REPLIES", "REPLYING TO ONE THREAD"])
def test_both_come_after_the_planning_block(heading):
    """Each says what to put IN a plan, so both read as nonsense to an agent
    that has not been told plans exist."""
    prompt = _prompt_for("inbox")
    assert prompt.index("SEVERAL ACTIONS AT ONCE") < prompt.index(heading)


# ── what job 2's recipe actually says ────────────────────────────────────
def test_it_names_needs_reply_first_and_says_why_list_mail_is_not_enough():
    """The whole job is the subtraction. An agent told to "draft replies to
    what's waiting" with no reason to distrust `list_mail` will use it."""
    assert "`needs_reply` FIRST" in _REPLY_RECIPE
    assert "list_mail" in _REPLY_RECIPE
    assert "already replied" in _REPLY_RECIPE


def test_it_requires_reading_the_thread_before_writing():
    """Rung 2 to rung 3. A reply written from a snippet answers the subject
    line, and the question is usually further down."""
    assert "read_thread" in _REPLY_RECIPE


def test_it_asks_for_one_plan_not_one_card_each():
    """A tap nobody reads by the fourth time is not consent — the rule
    `CLAUDE.md` states for batches, applied here."""
    assert "ONE plan" in _REPLY_RECIPE
    assert "thread_id" in _REPLY_RECIPE


def test_it_forbids_sending():
    """Job 2 is rungs 1→3 on purpose. The user reads and sends these."""
    assert "NEVER send" in _REPLY_RECIPE


def test_it_refuses_to_treat_unverified_silence_as_a_reason_to_write():
    """The same line `followup_tools` draws, on the way back out: a thread we
    could not read is not one we know is unanswered."""
    assert "COULD NOT CHECK" in _REPLY_RECIPE


# ── what job 3's recipe actually says ────────────────────────────────────
def test_it_forbids_guessing_a_thread_id():
    """A reply filed into a stranger's conversation cannot be taken back."""
    assert "NEVER invent or guess a thread id" in _THREAD_REPLY_RECIPE


def test_it_answers_whoever_asked_last_not_whoever_started_it():
    """The envelope failure. On a five-message thread the person waiting is
    rarely the one in the original `From`."""
    assert "LAST message" in _THREAD_REPLY_RECIPE
    assert "started the thread" in _THREAD_REPLY_RECIPE


def test_it_passes_thread_id_so_the_reply_lands_in_the_conversation():
    assert "thread_id" in _THREAD_REPLY_RECIPE


def test_it_tells_the_model_not_to_write_an_essay():
    """Told "say X", a model helpfully writes three paragraphs of X. The user
    already decided the content; this is addressing, not composition."""
    assert "three paragraphs" in _THREAD_REPLY_RECIPE


def test_it_drafts_unless_the_user_said_send():
    assert "create_draft` unless they said send" in _THREAD_REPLY_RECIPE
