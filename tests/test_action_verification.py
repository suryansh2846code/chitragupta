"""Rung 5: did the thing actually happen, and do we know?

`verify is None` used to mean two different things — *"nobody has written a
verifier yet"* and *"there is no way to check this"* — and reporting them
identically is how the first quietly becomes permanent. An audit looking at the
registry sees half the actions equally unverifiable and has no way to tell which
half is a gap.

So every action must declare one or the other, and the first test here fails on
any that declares neither. That turns a one-off audit into an invariant: the
next action somebody adds cannot silently arrive without a verifier.

The rest exercises the three outcomes through the real executor —
VERIFIED_SUCCESS, VERIFIED_FAILURE, UNVERIFIABLE — including the awkward ones:
a verifier that cannot reach the service, verification after a retry, after a
restart, and after an approval was waited on for four minutes.
"""
from __future__ import annotations

import pytest
from automation_harness import (
    Clock,
    FakeAgent,
    FakeGate,
    FakeWorld,
    action_tag,
    automation,
    build_deps,
    drive,
    fresh_store,
)

from chitragupta.actions import REGISTRY
from chitragupta.automation.executor import (
    UNVERIFIABLE,
    VERIFIED_FAILURE,
    VERIFIED_SUCCESS,
    Executor,
)
from chitragupta.core import automation_store as store
from chitragupta.core.automation_store import RunState


@pytest.fixture(autouse=True)
def _own_database():
    fresh_store()
    yield


def _action_step(run_id: str) -> dict:
    return next(s for s in store.steps_for(run_id) if s["kind"] == "action")


# ── the invariant ──────────────────────────────────────────────────────────

def test_every_action_either_verifies_or_says_why_it_cannot():
    """The audit, as a rule rather than a one-off.

    An action arriving with no verifier and no reason is the thing that made
    verification coverage rot the first time.
    """
    silent = [name for name, spec in REGISTRY.items()
              if spec.verify is None and not spec.unverifiable_because]
    assert not silent, (
        f"{len(silent)} action(s) neither verify nor say why they cannot: "
        f"{sorted(silent)}")


def test_an_action_does_not_claim_both():
    """Declaring a reason *and* shipping a verifier is a contradiction — one of
    the two is stale, and the reason is the half a user reads."""
    both = [name for name, spec in REGISTRY.items()
            if spec.verify is not None and spec.unverifiable_because]
    assert not both, both


def test_the_declared_reason_is_a_sentence_not_a_shrug():
    for name, spec in REGISTRY.items():
        if spec.unverifiable_because:
            assert len(spec.unverifiable_because) > 30, name
            assert " " in spec.unverifiable_because, name


def test_most_of_the_registry_can_actually_be_checked():
    """A floor, so a future change that quietly declares half the registry
    unverifiable to make a suite pass is visible."""
    verifiable = sum(1 for s in REGISTRY.values() if s.verify)
    assert verifiable >= len(REGISTRY) - 2, (
        f"only {verifiable} of {len(REGISTRY)} actions verify")


# ── the three outcomes, through the executor ───────────────────────────────

def test_verified_success_is_recorded_by_name():
    world = FakeWorld(verifications={"create_task": {"verified": True,
                                                     "at": "2026-09-28"}})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")])
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(automation(), deps)

    assert run["state"] == RunState.COMPLETED
    assert _action_step(run["id"])["result"]["verification_status"] == \
        VERIFIED_SUCCESS


def test_verified_failure_does_not_complete_and_says_what_was_missing():
    world = FakeWorld(verifications={"create_task": {"verified": False,
                                                     "detail": "no such task"}})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 5)
    auto = automation().with_policy(
        retry=automation().policy.retry.__class__(max_attempts=1))
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(auto, deps)

    assert run["state"] == RunState.ESCALATED
    assert "no such task" in run["reason"]
    verify_step = next(s for s in store.steps_for(run["id"])
                       if s["kind"] == "verify")
    assert verify_step["result"]["verification_status"] == VERIFIED_FAILURE


def test_an_unverifiable_action_is_its_own_answer_and_says_why():
    """Not success — "done" must never mean "we did not look". Not failure —
    every unverifiable action would look broken and the badge stops being read."""
    world = FakeWorld(verifications={"set_reminder": None})
    agent = FakeAgent(replies=[action_tag("set_reminder", message="x", at="9am")])
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(automation(), deps)

    assert run["state"] == RunState.COMPLETED
    result = _action_step(run["id"])["result"]
    assert result["verification_status"] == UNVERIFIABLE
    assert result["verification_detail"], "it did not say why"


def test_the_reason_comes_from_the_action_that_declared_it():
    """The sentence is written beside the action, so what the user reads and
    what the registry says cannot drift."""
    from chitragupta.automation.engine import _unverifiable_reason

    assert "connected app" in _unverifiable_reason("mcp_action")
    assert _unverifiable_reason("create_task") == "", (
        "an action with a verifier should have no excuse")
    assert _unverifiable_reason("not_an_action") == ""


# ── the awkward ones ───────────────────────────────────────────────────────

def test_a_verifier_that_cannot_reach_the_service_is_not_a_pass():
    """Absence of evidence is not evidence. A verifier that raised has proved
    nothing, and treating that as success is how "sent" stops meaning sent."""
    def exploding_verify(action_type, params, result):
        raise TimeoutError("the calendar did not answer in time")

    agent = FakeAgent(replies=[action_tag("create_event", title="x",
                                          start="tomorrow")] * 4)
    auto = automation().with_policy(
        retry=automation().policy.retry.__class__(max_attempts=1))
    deps, _ = build_deps(agent=agent)
    deps.verify = exploding_verify
    run = drive(auto, deps)

    assert run["state"] == RunState.ESCALATED
    assert "could not check" in run["reason"]


def test_the_history_distinguishes_unreachable_from_absent():
    """"the calendar says no" and "the calendar is down" ask different things
    of the user, so they are different rows."""
    def exploding_verify(action_type, params, result):
        raise TimeoutError("timed out")

    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 4)
    auto = automation().with_policy(
        retry=automation().policy.retry.__class__(max_attempts=1))
    deps, _ = build_deps(agent=agent)
    deps.verify = exploding_verify
    run = drive(auto, deps)

    verify_step = next(s for s in store.steps_for(run["id"])
                       if s["kind"] == "verify")
    assert verify_step["result"]["reached_the_service"] is False


def test_a_failed_verification_is_checked_again_on_the_next_attempt():
    """A service can be eventually consistent. Settling a failure permanently
    meant the run was never re-checked and then completed — reporting success
    for a thing that is not there."""
    world = FakeWorld(verifications={"create_task": {"verified": False,
                                                     "detail": "not yet"}})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 5)
    clock = Clock()
    auto = automation()
    deps, _ = build_deps(agent=agent, world=world, clock=clock)
    run = drive(auto, deps)
    assert run["state"] == RunState.RETRYING

    world.verifications.clear()               # the service caught up
    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)

    settled = store.get_run(run["id"])
    assert settled["state"] == RunState.COMPLETED
    assert _action_step(run["id"])["result"]["verification_status"] == \
        VERIFIED_SUCCESS
    assert world.count("create_task") == 1, "it did the thing twice"


def test_verification_runs_after_a_process_restart():
    """The executor is rebuilt from nothing; the step ledger is what says which
    actions still need checking."""
    world = FakeWorld(crash_after_claim="create_task")
    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 4)
    clock = Clock()
    auto = automation()
    deps, _ = build_deps(agent=agent, world=world, clock=clock)
    run = drive(auto, deps)

    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)     # a brand-new executor

    assert store.get_run(run["id"])["state"] == RunState.COMPLETED
    assert _action_step(run["id"])["result"]["verification_status"] == \
        VERIFIED_SUCCESS
    assert world.count("create_task") == 1


def test_verification_runs_after_an_approval_was_waited_on():
    """The run parked for four minutes on a human. When it resumes, the action
    still has to be confirmed — a tap is permission, not evidence."""
    auto = automation()
    agent = FakeAgent(replies=[
        action_tag("send_email", to="stranger@example.com", subject="Hi")])
    clock = Clock()
    deps, fakes = build_deps(agent=agent, clock=clock,
                             gate=FakeGate(blocked={"send_email"}))
    run = drive(auto, deps)
    assert run["state"] == RunState.WAITING_FOR_APPROVAL

    clock.advance(minutes=4)
    fakes["approvals"].approve(run["approval_id"])
    Executor(deps).advance(run["id"], auto)

    settled = store.get_run(run["id"])
    assert settled["state"] == RunState.COMPLETED
    kinds = [s["kind"] for s in store.steps_for(run["id"])]
    assert "verify" in kinds, "an approved action was never confirmed"


def test_turning_verification_off_does_not_claim_it_happened():
    """A run that skipped the check must not carry a status saying it passed."""
    world = FakeWorld(verifications={"create_task": {"verified": False}})
    agent = FakeAgent(replies=[action_tag("create_task", title="x")])
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(automation().with_policy(verify=False), deps)

    assert run["state"] == RunState.COMPLETED
    assert not _action_step(run["id"])["result"].get("verification_status")
