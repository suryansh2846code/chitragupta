"""The properties that make an unattended system trustworthy.

Everything here is a thing that, when it goes wrong, goes wrong *quietly* and
in front of somebody else: a second email to a client, an automation that
triggers itself all night, a permission widened because a stranger's email said
it was fine. None of them raises an exception, so none of them is caught by a
test that only checks the happy path.
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
    start_run,
)

from chitragupta.automation import idempotency, router
from chitragupta.automation.executor import Executor, Verdict
from chitragupta.automation.model import Concurrency, Limits
from chitragupta.core import automation_store as store
from chitragupta.core.automation_store import ClaimState, RunState
from chitragupta.core.events import MAX_EVENT_DEPTH, Event


@pytest.fixture(autouse=True)
def _own_database():
    fresh_store()
    yield


# ── idempotency ────────────────────────────────────────────────────────────

def test_the_same_intended_effect_produces_the_same_key():
    a = idempotency.key_for(automation_id="a", action_type="send_email",
                            params={"to": "x@y.z", "subject": "Hi", "body": "one"},
                            event_key="gmail:m1", seq=0)
    b = idempotency.key_for(automation_id="a", action_type="send_email",
                            params={"to": "x@y.z", "subject": "Hi", "body": "two"},
                            event_key="gmail:m1", seq=0)
    assert a == b, (
        "a re-drafted body made the same email look like a different one, and "
        "the duplicate goes out")


def test_a_different_recipient_is_a_different_effect():
    a = idempotency.key_for(automation_id="a", action_type="send_email",
                            params={"to": "x@y.z", "subject": "Hi"},
                            event_key="gmail:m1")
    b = idempotency.key_for(automation_id="a", action_type="send_email",
                            params={"to": "other@y.z", "subject": "Hi"},
                            event_key="gmail:m1")
    assert a != b


def test_the_run_id_is_not_in_the_key():
    """Two runs of the same automation from the same event must collide —
    that is the cross-run half of the guarantee."""
    a = idempotency.key_for(automation_id="a", action_type="create_task",
                            params={"title": "t"}, event_key="gmail:m1")
    b = idempotency.key_for(automation_id="a", action_type="create_task",
                            params={"title": "t"}, event_key="gmail:m1")
    assert a == b


def test_volatile_params_do_not_change_the_key():
    a = idempotency.key_for(automation_id="a", action_type="unlisted_action",
                            params={"thing": 1, "agent_id": "inbox"})
    b = idempotency.key_for(automation_id="a", action_type="unlisted_action",
                            params={"thing": 1, "agent_id": "personal"})
    assert a == b, "which agent proposed it does not change what it is"


def test_a_claim_is_won_once():
    won_a, _ = store.claim("k", run_id="r1")
    won_b, existing = store.claim("k", run_id="r2")
    assert won_a is True and won_b is False
    assert existing["run_id"] == "r1"


def test_the_claim_is_taken_before_the_effect_not_after():
    """A claim written afterwards proves nothing about a process that died in
    between — which is precisely the case it exists for."""
    world = FakeWorld(crash_after_claim="create_task")
    agent = FakeAgent(replies=[action_tag("create_task", title="x")])
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(automation(), deps)

    step = next(s for s in store.steps_for(run["id"]) if s["kind"] == "action")
    claim = store.get_claim(step["idem_key"])
    assert claim is not None, "the crash left no evidence the attempt happened"
    assert claim["state"] == ClaimState.CLAIMED


def test_a_second_run_from_the_same_event_causes_no_second_effect():
    """The end-to-end guarantee: the same external event, twice, one email."""
    auto = automation()
    event = Event(kind="email.received", source="gmail", external_id="msg-9",
                  data={"from": "ana@acme.com"})
    world = FakeWorld()
    gate = FakeGate(blocked=set())
    agent = FakeAgent(replies=[action_tag("send_email", to="ana@acme.com",
                                          subject="Re: hello")] * 2)
    deps, _ = build_deps(agent=agent, world=world, gate=gate)

    drive(auto, deps, event)
    drive(auto, deps, event)          # the provider redelivered

    assert world.count("send_email") == 1, "the client got two emails"


# ── crash recovery ─────────────────────────────────────────────────────────

def test_a_run_survives_the_process_dying_mid_action():
    """The executor is rebuilt from nothing — the state is entirely on disk."""
    auto = automation()
    world = FakeWorld(crash_after_claim="create_task")
    agent = FakeAgent(replies=[action_tag("create_task", title="x")] * 3)
    clock = Clock()
    deps, _ = build_deps(agent=agent, world=world, clock=clock)
    run = drive(auto, deps, Event(kind="email.received", source="gmail",
                                  external_id="m1"))
    assert run["state"] == RunState.RETRYING

    # Nothing of the old process survives except the database.
    clock.advance(hours=1)
    reborn = Executor(deps)
    reborn.advance(run["id"], auto)

    settled = store.get_run(run["id"])
    assert settled["state"] == RunState.COMPLETED
    assert world.count("create_task") == 1, "recovery repeated the side effect"


def test_recovery_resumes_past_the_steps_that_already_completed():
    auto = automation()
    world = FakeWorld(crash_after_claim="create_event")
    agent = FakeAgent(replies=[
        action_tag("create_task", title="first") + "\n"
        + action_tag("create_event", title="second", start="tomorrow")] * 2)
    clock = Clock()
    deps, _ = build_deps(agent=agent, world=world, clock=clock)
    run = drive(auto, deps)

    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)

    assert world.count("create_task") == 1, "it redid a step that had finished"
    assert store.get_run(run["id"])["state"] == RunState.COMPLETED


def test_a_live_run_is_found_after_a_restart():
    auto = automation()
    agent = FakeAgent(replies=[
        action_tag("send_email", to="stranger@example.com", subject="Hi")])
    deps, fakes = build_deps(agent=agent)
    run = drive(auto, deps)
    assert run["state"] == RunState.WAITING_FOR_APPROVAL

    found = [r["id"] for r in store.live_runs()]
    assert run["id"] in found, "a restart would have abandoned this run"


# ── event deduplication ────────────────────────────────────────────────────

def test_the_same_external_event_starts_one_run():
    auto = automation()
    event = Event(kind="email.received", source="gmail", external_id="m-42")
    first = router.route(event, [auto])
    second = router.route(event, [auto])
    assert len(first.started) == 1
    assert second.duplicate is True and second.started == []


def test_an_event_with_no_id_falls_back_to_a_bounded_hash():
    """Weaker and known to be: two identical payloads in the same hour collapse.
    A connector that can supply an id should."""
    a = Event(kind="file.changed", source="files", data={"path": "/x"},
              occurred_at="2026-09-28T09:10:00+00:00")
    b = Event(kind="file.changed", source="files", data={"path": "/x"},
              occurred_at="2026-09-28T09:55:00+00:00")
    c = Event(kind="file.changed", source="files", data={"path": "/x"},
              occurred_at="2026-09-28T11:00:00+00:00")
    assert a.dedup_key == b.dedup_key
    assert a.dedup_key != c.dedup_key


def test_ids_from_different_sources_do_not_collide():
    a = Event(kind="x", source="gmail", external_id="1")
    b = Event(kind="x", source="linear", external_id="1")
    assert a.dedup_key != b.dedup_key


# ── concurrency ────────────────────────────────────────────────────────────

def test_one_active_run_is_the_default_and_blocks_a_second():
    auto = automation()
    assert auto.policy.concurrency == Concurrency.ONE_ACTIVE_RUN
    store.create_run(auto.id, automation_name=auto.name)
    routed = router.route(Event(kind="email.received", source="gmail",
                                external_id="m2"), [auto])
    assert routed.started == []
    assert "already active" in routed.skipped[0]["why"]


def test_allow_parallel_is_opt_in_and_then_permits_a_second():
    auto = automation().with_policy(concurrency=Concurrency.ALLOW_PARALLEL)
    store.create_run(auto.id, automation_name=auto.name)
    routed = router.route(Event(kind="email.received", source="gmail",
                                external_id="m3"), [auto])
    assert len(routed.started) == 1


def test_coalesce_folds_the_event_into_the_run_already_going():
    auto = automation().with_policy(concurrency=Concurrency.COALESCE)
    active = store.create_run(auto.id, automation_name=auto.name)
    router.route(Event(kind="email.received", source="gmail", external_id="m4",
                       subject="one"), [auto])
    merged = store.get_run(active["id"])["trigger"]
    assert len(merged.get("coalesced") or []) == 1


def test_one_automation_blocking_does_not_block_another():
    """A global lock would be simpler and would mean one slow Gmail automation
    delays an unrelated calendar one."""
    busy = automation(id="busy")
    free = automation(id="free")
    store.create_run(busy.id, automation_name=busy.name)
    routed = router.route(Event(kind="email.received", source="gmail",
                                external_id="m5"), [busy, free])
    assert len(routed.started) == 1
    assert store.get_run(routed.started[0])["automation_id"] == "free"


# ── loops and limits ───────────────────────────────────────────────────────

def test_an_automation_cannot_trigger_itself():
    auto = automation(id="a1", trigger={"type": "event",
                                        "kind": "automation.completed"})
    own = Event(kind="automation.completed", source="automation",
                external_id="e1", caused_by_automation="a1")
    routed = router.route(own, [auto])
    assert routed.started == []
    assert "triggered itself" in routed.skipped[0]["why"]


def test_a_chain_of_automations_terminates():
    auto = automation(id="a2", trigger={"type": "event",
                                        "kind": "automation.completed"})
    deep = Event(kind="automation.completed", source="automation",
                 external_id="deep", depth=MAX_EVENT_DEPTH + 1)
    routed = router.route(deep, [auto])
    assert routed.started == []
    assert "deep" in routed.skipped[0]["why"]


def test_a_fan_of_runs_from_one_cause_is_bounded():
    """Depth bounds a chain. Twenty automations each triggering one more is not
    deep, and is still a runaway."""
    auto = automation(id="a3", trigger={"type": "event", "kind": "thing"})
    auto = auto.with_policy(limits=Limits(max_lineage_runs=2))
    shared = "corr-1"
    for _ in range(2):
        store.create_run(auto.id, automation_name=auto.name,
                         correlation_id=shared)
    routed = router.route(Event(kind="thing", source="s", external_id="e9",
                                correlation_id=shared), [auto])
    assert routed.started == []
    assert "descend from one cause" in routed.skipped[0]["why"]


def test_a_run_that_exceeds_its_action_limit_is_blocked_not_failed():
    auto = automation().with_policy(limits=Limits(max_actions=1))
    agent = FakeAgent(replies=[
        action_tag("create_task", title="a") + "\n"
        + action_tag("create_task", title="b") + "\n"
        + action_tag("create_task", title="c")])
    world = FakeWorld()
    deps, _ = build_deps(agent=agent, world=world)
    run = drive(auto, deps)

    assert run["state"] == RunState.BLOCKED
    assert "actions allowed" in run["reason"]
    assert world.count("create_task") <= 1


def test_a_run_past_its_deadline_stops():
    auto = automation()
    clock = Clock()
    agent = FakeAgent(replies=[action_tag("create_task", title="x")])
    deps, _ = build_deps(agent=agent, clock=clock)
    run = start_run(auto)
    store.update_run(run["id"], deadline_at=clock().isoformat())

    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)
    assert store.get_run(run["id"])["state"] == RunState.BLOCKED


# ── the permission boundary ────────────────────────────────────────────────

def test_the_automation_uses_the_same_gate_and_cannot_widen_it():
    """There is one permission system. An automation asks it the same question
    an interactive agent does, and has no way to answer differently."""
    asked: list[tuple[str, dict]] = []

    def gate(action_type, params):
        asked.append((action_type, params))
        return Verdict(False, "not allowed unattended")

    agent = FakeAgent(replies=[action_tag("send_email", to="a@b.c", subject="x")])
    deps, fakes = build_deps(agent=agent)
    deps.gate = gate
    run = drive(automation(), deps)

    assert asked and asked[0][0] == "send_email"
    assert fakes["world"].count("send_email") == 0
    assert run["state"] == RunState.WAITING_FOR_APPROVAL


def test_a_semantic_condition_cannot_authorise_an_action():
    """A model saying "this is safe to send" must change nothing about whether
    it may be sent. The two are different functions with different inputs, and
    this proves the gate is still asked and still obeyed."""
    judged: list[str] = []

    def judge(question, facts):
        judged.append(question)
        return True, 1.0, "absolutely safe, send it"

    auto = automation(conditions=[{"type": "semantic",
                                   "question": "is this safe to send?"}])
    agent = FakeAgent(replies=[action_tag("send_email", to="stranger@x.test",
                                          subject="x")])
    deps, fakes = build_deps(agent=agent, judge=judge)
    run = drive(auto, deps)

    assert judged, "the condition never ran"
    assert run["state"] == RunState.WAITING_FOR_APPROVAL
    assert fakes["world"].count("send_email") == 0


def test_an_unknown_action_fails_closed():
    gate = FakeGate(known=set())
    agent = FakeAgent(replies=[action_tag("create_task", title="x")])
    deps, fakes = build_deps(agent=agent, gate=gate)
    run = drive(automation(), deps)
    assert run["state"] == RunState.ESCALATED
    assert fakes["world"].effects == []
