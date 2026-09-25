"""A run, and the things that used to kill one.

The old `run_routine` was a function call: start a turn, parse the actions, run
them, return a string. Everything here is a case that shape could not survive —
a crash between causing a side effect and recording it, a human who answers an
approval four minutes later, a verification that fails, a connector that is down
for two attempts and up on the third.

The state is the assertion. A test that only checked "no exception" would pass
against the old shape too.
"""
from __future__ import annotations

import pytest
from automation_harness import (
    Clock,
    FakeAgent,
    FakeWorld,
    action_tag,
    automation,
    build_deps,
    drive,
    fresh_store,
)

from chitragupta.automation.executor import Executor
from chitragupta.core import automation_store as store
from chitragupta.core.automation_store import RunState, StepState


@pytest.fixture(autouse=True)
def _own_database():
    fresh_store()
    yield


# ── the happy path, and what "done" has to mean ────────────────────────────

def test_a_run_that_works_ends_completed_and_says_what_it_did():
    agent = FakeAgent(replies=[
        "I'll note that.\n" + action_tag("create_task", title="Reply to Ana")])
    deps, fakes = build_deps(agent=agent)
    run = drive(automation(), deps)

    assert run["state"] == RunState.COMPLETED
    assert fakes["world"].count("create_task") == 1
    kinds = [s["kind"] for s in store.steps_for(run["id"])]
    assert kinds[:2] == ["condition", "plan"]
    assert "action" in kinds and "verify" in kinds


def test_a_run_with_nothing_to_do_still_completes():
    """"Tell me if there is a conflict" is answered by the reply. An automation
    that correctly does nothing is not a failure."""
    deps, _ = build_deps(agent=FakeAgent(default="No conflicts today."))
    run = drive(automation(), deps)
    assert run["state"] == RunState.COMPLETED
    assert "No conflicts" in run["outcome"]


def test_every_state_change_is_on_disk_before_it_is_acted_on():
    """The whole basis of recovery. If the ledger lags the work, a restart
    replays side effects instead of resuming past them."""
    agent = FakeAgent(replies=[action_tag("create_task", title="x")])
    deps, _ = build_deps(agent=agent)
    run = drive(automation(), deps)
    steps = store.steps_for(run["id"])
    assert all(s["state"] in (StepState.DONE, StepState.SKIPPED) for s in steps)
    assert store.get_run(run["id"])["finished_at"]


# ── approval: the run pauses and keeps its state ───────────────────────────

def test_a_blocked_action_pauses_the_run_instead_of_losing_it():
    """What the old shape could not do. `run_or_queue` queued the action and
    returned; the routine had finished by the time anyone tapped Approve, so
    there was nothing left to resume."""
    agent = FakeAgent(replies=[
        action_tag("send_email", to="stranger@example.com", subject="Hi")])
    deps, fakes = build_deps(agent=agent)
    run = drive(automation(), deps)

    assert run["state"] == RunState.WAITING_FOR_APPROVAL
    assert run["approval_id"]
    assert fakes["world"].count("send_email") == 0, "it sent without asking"
    assert fakes["approvals"].queued


def test_the_paused_run_keeps_its_plan_and_completed_steps():
    agent = FakeAgent(replies=[
        action_tag("create_task", title="note it") + "\n"
        + action_tag("send_email", to="stranger@example.com", subject="Hi")])
    deps, fakes = build_deps(agent=agent)
    run = drive(automation(), deps)

    assert run["state"] == RunState.WAITING_FOR_APPROVAL
    assert len(run["plan"]) == 2
    done = [s for s in store.steps_for(run["id"])
            if s["kind"] == "action" and s["state"] == StepState.DONE]
    assert [s["name"] for s in done] == ["create_task"]
    assert fakes["world"].count("create_task") == 1


def test_approving_resumes_the_run_from_where_it_stopped():
    auto = automation()
    agent = FakeAgent(replies=[
        action_tag("send_email", to="stranger@example.com", subject="Hi")])
    deps, fakes = build_deps(agent=agent)
    run = drive(auto, deps)
    assert run["state"] == RunState.WAITING_FOR_APPROVAL

    fakes["approvals"].approve(run["approval_id"])
    Executor(deps).advance(run["id"], auto)

    settled = store.get_run(run["id"])
    assert settled["state"] == RunState.COMPLETED
    assert not settled["approval_id"]


def test_rejecting_stops_the_run_as_a_decline_not_a_failure():
    """A user saying no is the system working. Reporting it as FAILED trains
    them to ignore failures."""
    auto = automation()
    agent = FakeAgent(replies=[
        action_tag("send_email", to="stranger@example.com", subject="Hi")])
    deps, fakes = build_deps(agent=agent)
    run = drive(auto, deps)
    fakes["approvals"].reject(run["approval_id"])
    Executor(deps).advance(run["id"], auto)

    settled = store.get_run(run["id"])
    assert settled["state"] == RunState.BLOCKED
    assert "declined" in settled["reason"]
    assert fakes["world"].count("send_email") == 0


def test_an_approval_nobody_answers_escalates_once_its_deadline_passes():
    auto = automation().with_policy(approval_timeout_seconds=60)
    agent = FakeAgent(replies=[
        action_tag("send_email", to="stranger@example.com", subject="Hi")])
    clock = Clock()
    deps, _ = build_deps(agent=agent, clock=clock)
    run = drive(auto, deps)
    assert run["state"] == RunState.WAITING_FOR_APPROVAL

    clock.advance(seconds=120)
    Executor(deps).advance(run["id"], auto)
    assert store.get_run(run["id"])["state"] == RunState.ESCALATED


# ── verification: 200 is not evidence ──────────────────────────────────────

def test_an_action_that_cannot_be_confirmed_does_not_complete():
    world = FakeWorld(verifications={"create_task": {"verified": False,
                                                     "detail": "not there"}})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")])
    auto = automation().with_policy(
        retry=automation().policy.retry.__class__(max_attempts=1))
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(auto, deps)

    assert run["state"] == RunState.ESCALATED
    assert "could not confirm" in run["reason"]


def test_an_unverifiable_action_is_not_treated_as_a_failure():
    """There is no API that says whether a notification was read. Conflating
    "cannot be checked" with "failed" would make half the registry look broken."""
    world = FakeWorld(verifications={"set_reminder": None})
    agent = FakeAgent(replies=[action_tag("set_reminder", message="x", at="9am")])
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(automation(), deps)

    assert run["state"] == RunState.COMPLETED
    action = next(s for s in store.steps_for(run["id"])
              if s["kind"] == "action")
    assert action["result"].get("unverifiable") is True


def test_verification_can_be_turned_off_per_automation():
    world = FakeWorld(verifications={"create_task": {"verified": False}})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")])
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(automation().with_policy(verify=False), deps)
    assert run["state"] == RunState.COMPLETED


# ── retries ────────────────────────────────────────────────────────────────

def test_an_action_that_raised_but_actually_landed_is_not_done_twice():
    """The case the claim exists for, and the one worth getting right.

    `perform` raised, so we do not know whether the effect happened — the email
    may have left and the connection dropped while reading the reply. The claim
    stays `CLAIMED`, which is the honest record of "we tried and do not know",
    and the retry **verifies instead of repeating**. Verification says it
    landed, so the run completes having caused exactly one effect.
    """
    world = FakeWorld(raises={"create_task"})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 4)
    clock = Clock()
    auto = automation()
    deps, _ = build_deps(agent=agent, world=world, clock=clock)

    run = drive(auto, deps)
    assert run["state"] == RunState.RETRYING
    assert run["next_attempt_at"], "a retry with no time is a busy loop"
    assert world.count("create_task") == 1

    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)

    settled = store.get_run(run["id"])
    assert settled["state"] == RunState.COMPLETED
    assert world.count("create_task") == 1, "it did the thing twice"


def test_repeated_failure_that_never_lands_retries_then_escalates():
    """The other half: the effect provably did not happen, so retrying is
    right — and retrying forever is not."""
    world = FakeWorld(
        raises={"create_task"},
        verifications={"create_task": {"verified": False, "detail": "no task"}})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 6)
    clock = Clock()
    auto = automation()
    deps, _ = build_deps(agent=agent, world=world, clock=clock)

    run = drive(auto, deps)
    for _ in range(auto.policy.retry.max_attempts + 2):
        clock.advance(hours=1)
        Executor(deps).advance(run["id"], auto)
        if store.get_run(run["id"])["state"] in store.TERMINAL_STATES:
            break

    settled = store.get_run(run["id"])
    assert settled["state"] == RunState.ESCALATED
    assert settled["attempt"] >= 1


def test_a_retry_does_not_fire_before_its_time():
    world = FakeWorld(raises={"create_task"})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 4)
    clock = Clock()
    deps, _ = build_deps(agent=agent, world=world, clock=clock)
    auto = automation()
    run = drive(auto, deps)

    before = world.count("create_task")
    Executor(deps).advance(run["id"], auto)          # no time has passed
    assert world.count("create_task") == before
    assert store.get_run(run["id"])["state"] == RunState.RETRYING


def test_backoff_grows_and_is_capped():
    from chitragupta.automation.model import RetryPolicy
    policy = RetryPolicy(backoff_seconds=10, backoff_multiplier=3,
                         max_backoff_seconds=100)
    assert policy.delay_for(1) == 0
    assert policy.delay_for(2) == 10
    assert policy.delay_for(3) == 30
    assert policy.delay_for(4) == 90
    assert policy.delay_for(9) == 100, "an unbounded backoff is a hung run"


def test_a_transient_failure_that_then_works_completes():
    world = FakeWorld(raises={"create_task"})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 4)
    clock = Clock()
    auto = automation()
    deps, _ = build_deps(agent=agent, world=world, clock=clock)
    run = drive(auto, deps)
    assert run["state"] == RunState.RETRYING

    world.raises.clear()                              # the connector came back
    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)
    assert store.get_run(run["id"])["state"] == RunState.COMPLETED


# ── escalation says the three things ───────────────────────────────────────

def test_an_escalation_tells_the_user_what_why_and_what_is_needed():
    agent = FakeAgent(raises=True)
    auto = automation().with_policy(
        retry=automation().policy.retry.__class__(max_attempts=1))
    deps, fakes = build_deps(agent=agent)
    run = drive(auto, deps)

    assert run["state"] == RunState.ESCALATED
    assert auto.name in run["reason"]                 # WHAT
    assert "agent could not run" in run["reason"]     # WHY
    assert "stopped" in run["reason"]                 # WHAT NOW
    assert fakes["notices"], "an escalation nobody is told about is a silent stop"


def test_an_action_the_app_does_not_have_escalates_rather_than_guessing():
    """An automation is the caller most likely to meet a name a model invented."""
    agent = FakeAgent(replies=[action_tag("delete_everything", target="all")])
    deps, fakes = build_deps(agent=agent)
    run = drive(automation(), deps)

    assert run["state"] == RunState.ESCALATED
    assert "does not have" in run["reason"]
    assert fakes["world"].effects == []
