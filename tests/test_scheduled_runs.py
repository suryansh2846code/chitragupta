"""A reminder and a scheduled action, as durable runs.

Both used to be a bare call inside the scheduler: `desktop_notify` for a
reminder, `actions.run_now` for a scheduled action. Neither appeared in any
history, neither was retried, and a failure was a log line. They now go down the
same path every automation takes — which is only an improvement if the things
the old path *did* get right still happen, so half this file is about those.

The engine is driven through the fakes in `automation_harness`: no model, no
connector, no clock that has to be waited on.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from automation_harness import Clock, FakeAgent, FakeGate, FakeWorld, build_deps, fresh_store

from chitragupta.automation import engine
from chitragupta.automation.model import Automation, Limits, Policy, RetryPolicy
from chitragupta.core import automation_store as store


@pytest.fixture(autouse=True)
def _own_database():
    fresh_store()


def scheduled_deps(**kw):
    """Deps whose gate knows the two action types a one-off run ever uses."""
    gate = FakeGate(known={"notify", "send_email"}, blocked={"send_email"})
    return build_deps(gate=gate, **kw)


# ── what the old path got right, and must still ────────────────────────────

def test_a_due_reminder_reaches_the_user():
    """The only thing a reminder has to do. Everything else here is new."""
    deps, fakes = scheduled_deps()
    outcome = engine.run_once(
        name="Reminder: call the dentist",
        actions=[("notify", {"message": "call the dentist",
                             "title": "◆ Chitragupta"})],
        external_id="reminder:r1", deps=deps)

    assert outcome["state"] == str(store.RunState.COMPLETED)
    assert fakes["world"].effects == [
        ("notify", {"message": "call the dentist", "title": "◆ Chitragupta"})]


def test_a_reminder_fires_once_even_if_the_clock_beats_twice():
    """A scheduler that restarted inside the same minute, or two of them.

    The reminder store's `mark_fired` is not the guard — it is a different
    database, written after. The event ledger is.
    """
    deps, fakes = scheduled_deps()
    first = engine.run_once(name="Reminder: stand up",
                            actions=[("notify", {"message": "stand up"})],
                            external_id="reminder:r2", deps=deps)
    second = engine.run_once(name="Reminder: stand up",
                             actions=[("notify", {"message": "stand up"})],
                             external_id="reminder:r2", deps=deps)

    assert first["duplicate"] is False and second["duplicate"] is True
    assert second["run_id"] == ""
    assert fakes["world"].count("notify") == 1


def test_no_model_is_asked_what_to_do():
    """The user already said. Asking a model would make a reminder cost a turn,
    and give it a chance to decide something else."""
    deps, fakes = scheduled_deps(agent=FakeAgent(replies=["I would not"]))
    engine.run_once(name="Reminder: water the plants",
                    actions=[("notify", {"message": "water the plants"})],
                    external_id="reminder:r3", deps=deps)

    assert fakes["agent"].prompts == []


def test_a_scheduled_action_proceeds_on_the_approval_it_already_has():
    """The user confirmed this exact content when they scheduled it.

    `FakeGate` blocks `send_email` to an unlisted address exactly as the real
    gate does — and it must still be blocked for anything that was *not*
    pre-approved, which the next test checks.
    """
    deps, fakes = scheduled_deps()
    outcome = engine.run_once(
        name="Scheduled send_email",
        actions=[("send_email", {"to": "ana@example.test", "subject": "hi"})],
        external_id="scheduled:s1", pre_approved=True, deps=deps)

    assert outcome["state"] == str(store.RunState.COMPLETED)
    assert fakes["world"].count("send_email") == 1
    assert fakes["approvals"].queued == {}, "it asked a second time"


def test_pre_approval_does_not_leak_to_anything_else():
    """The dangerous half of the previous test. A run that was not
    pre-approved must still wait, or `pre_approved` is a permission bypass with
    a friendly name."""
    deps, fakes = scheduled_deps()
    outcome = engine.run_once(
        name="Scheduled send_email",
        actions=[("send_email", {"to": "stranger@example.test"})],
        external_id="scheduled:s2", pre_approved=False, deps=deps)

    assert outcome["state"] != str(store.RunState.COMPLETED)
    assert fakes["world"].count("send_email") == 0


def test_what_a_one_off_did_is_on_the_record():
    """The reason for all of this. A reminder that fired at 07:00 and a
    scheduled email that did not send are both answerable questions now."""
    deps, _ = scheduled_deps()
    outcome = engine.run_once(name="Reminder: pay rent",
                              actions=[("notify", {"message": "pay rent"})],
                              external_id="reminder:r4", deps=deps)

    run = store.get_run(outcome["run_id"]) or {}
    steps = store.steps_for(outcome["run_id"])
    assert run["automation_name"] == "Reminder: pay rent"
    assert [s["name"] for s in steps if s["kind"] == "action"] == ["notify"]


# ── the part that was missing: it can be picked up again ────────────────────

def test_a_one_off_run_survives_the_process_that_started_it():
    """The regression test for a run that could not be resumed.

    A reminder's automation is never written to the routines table — it is one
    occurrence, not a standing rule. `recover` looked it up there, found
    nothing, concluded it had been deleted and blocked the run. So a scheduled
    action that crashed or failed got exactly one attempt and then died
    quietly, which is the opposite of what moving it here was for.

    The spec now travels on the run, so `recover` can rebuild it.
    """
    world = FakeWorld(crash_after_claim="send_email")
    clock = Clock()
    deps, fakes = scheduled_deps(world=world, clock=clock)
    outcome = engine.run_once(
        name="Scheduled send_email",
        actions=[("send_email", {"to": "ana@example.test"})],
        external_id="scheduled:s3", pre_approved=True, deps=deps)

    run_id = outcome["run_id"]
    assert store.get_run(run_id)["state"] == str(store.RunState.RETRYING)

    clock.now += timedelta(minutes=5)
    assert engine.recover(deps=deps) == [run_id], \
        "the run was not picked up — its automation was treated as deleted"

    settled = store.get_run(run_id) or {}
    assert settled["state"] not in {str(store.RunState.BLOCKED)}
    assert "deleted" not in (settled["reason"] or "")
    # And the crash did not turn into a second email: the claim was taken
    # before the effect, so the retry verified rather than repeating.
    assert fakes["world"].count("send_email") == 1


def test_a_resumed_one_off_keeps_its_own_policy():
    """The spec carries the policy, not just the name.

    A rebuilt automation with default limits would silently give a run
    different retries and a different deadline than the one it started with.
    """
    original = Automation(
        id="once:x", name="Scheduled thing", agent_id="health",
        instruction="do it", goal="it is done",
        policy=Policy(retry=RetryPolicy(max_attempts=7),
                      limits=Limits(max_actions=2), verify=False))

    rebuilt = Automation.from_row(engine._spec_row(original))

    assert rebuilt.id == original.id
    assert rebuilt.agent_id == "health"
    assert rebuilt.policy.retry.max_attempts == 7
    assert rebuilt.policy.limits.max_actions == 2
    assert rebuilt.policy.verify is False


def test_a_run_whose_automation_really_was_deleted_is_still_blocked():
    """The behaviour the fix must not have swallowed.

    A recurring automation the user deleted leaves runs that genuinely cannot
    be finished — its goal and its policy went with it. Blocked, not failed:
    nothing went wrong.
    """
    deps, _ = scheduled_deps()
    run = store.create_run("auto-gone", automation_name="Deleted automation")

    assert engine.recover(deps=deps) == []
    settled = store.get_run(run["id"]) or {}
    assert settled["state"] == str(store.RunState.BLOCKED)
    assert "deleted" in settled["reason"]


def test_a_failing_scheduled_action_is_retried_and_then_escalated():
    """Fire-once-and-report became retry-then-escalate.

    A transient connector error used to lose the email outright. It is now
    retried to the policy's limit and then escalated, which is where a failure
    a human has to see belongs. The user is still told — later, and with a
    better answer.
    """
    world = FakeWorld(results={"send_email": {"ok": False, "error": "no recipient"}})
    clock = Clock()
    deps, fakes = scheduled_deps(world=world, clock=clock)
    outcome = engine.run_once(
        name="Scheduled send_email", actions=[("send_email", {"to": "a@b.test"})],
        external_id="scheduled:s4", pre_approved=True, deps=deps)

    run_id = outcome["run_id"]
    terminal = {str(state) for state in store.TERMINAL_STATES}
    for _ in range(8):
        clock.now += timedelta(hours=1)
        engine.recover(deps=deps)
        if str((store.get_run(run_id) or {}).get("state")) in terminal:
            break

    settled = store.get_run(run_id) or {}
    assert settled["state"] == str(store.RunState.ESCALATED)
    assert "no recipient" in settled["reason"]
    assert fakes["notices"], "it gave up without telling the user"
    assert int(settled["attempt"]) > 1, "it did not retry at all"
