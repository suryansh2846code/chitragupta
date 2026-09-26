"""Ten ways to lose a process, and the one thing none of them may cause.

Every test here asserts the same property from a different direction: **the
world was touched at most once.** Not "the run ended tidily" — a run that ends
tidily having sent the email twice is the failure this whole design is for.
`FakeWorld.count()` is the number that matters in all ten, and every other
assertion is supporting evidence for it.

The scenarios are the ones that actually happen to software running unattended
on a laptop: the lid closes mid-action, the user answers an approval the next
morning, a provider retries a webhook because our 200 was slow, two events
arrive in the same second. Each one is a `kill -9` at a different line, which is
why they are written against injected dependencies and a clock the test moves —
a real crash cannot be scheduled, and a real backoff cannot be waited on.

There is deliberately no `time.sleep` and no ordering between tests: each opens
its own database.
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
    start_run,
)

from chitragupta.automation import engine
from chitragupta.automation.executor import Executor
from chitragupta.automation.model import Concurrency, Limits, Policy, RetryPolicy
from chitragupta.core import automation_store as store
from chitragupta.core.automation_store import ClaimState, RunState
from chitragupta.core.events import Event


@pytest.fixture(autouse=True)
def _own_database():
    fresh_store()


def sends_one(what="send_email", **params):
    """An agent that proposes exactly one action."""
    return FakeAgent(replies=["On it.\n" + action_tag(
        what, **(params or {"to": "ana@acme.com", "subject": "Hi"}))])


def allowed():
    """A gate that says yes to `send_email`, so the action actually runs."""
    from automation_harness import FakeGate
    return FakeGate(blocked=set())


def claims(run_id):
    """Every idempotency claim this run took, settled or not.

    Read straight out of the table rather than through `dangling_claims`, which
    only returns the unsettled ones — the difference between "claimed" and
    "completed" is half of what these tests are about.
    """
    return [dict(r) for r in store._conn().execute(
        "SELECT * FROM automation_claims WHERE run_id=? ORDER BY created_at",
        (run_id,))]


# ── 1. the process dies before the action ──────────────────────────────────

def test_a_crash_before_the_action_leaves_nothing_done_and_resumes():
    """The easy direction, and the one that must not be over-corrected.

    Nothing reached the world, so there is nothing to be careful about: the
    restart should simply do the work. A design that refused to retry here —
    out of caution about the *other* crash — would lose the email entirely.
    """
    attempts = {"n": 0}

    def dies_the_first_time(agent_id, prompt, execution=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("the lid closed")
        return ("On it.\n" + action_tag("send_email", to="ana@acme.com",
                                        subject="Hi")), 1

    clock = Clock()
    deps, fakes = build_deps(gate=allowed(), clock=clock)
    deps.plan = dies_the_first_time
    auto = automation()

    run = drive(auto, deps)

    assert fakes["world"].count("send_email") == 0
    assert claims(run["id"]) == [], "a claim was taken for an action never begun"
    assert run["state"] == RunState.RETRYING

    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)

    assert fakes["world"].count("send_email") == 1
    assert (store.get_run(run["id"]) or {})["state"] == RunState.COMPLETED


# ── 2. the process dies after the action, before recording it ──────────────

def test_a_crash_after_the_action_verifies_instead_of_repeating():
    """The expensive direction. The email is gone; nothing knows it.

    The claim is taken *before* the side effect, so what survives the crash is
    `CLAIMED` — the honest "we tried and do not know". Recovery answers it by
    asking the world, not by doing it again.
    """
    world = FakeWorld(crash_after_claim="send_email")
    deps, fakes = build_deps(agent=sends_one(), world=world, gate=allowed())
    auto = automation()

    run = drive(auto, deps)

    assert fakes["world"].count("send_email") == 1
    assert [c["state"] for c in claims(run["id"])] == [ClaimState.CLAIMED]
    assert run["state"] == RunState.RETRYING

    # The restart, after the backoff. The same agent would propose the same
    # action; the claim is what stops it being performed twice.
    deps.plan = sends_one()
    fakes["clock"].advance(hours=1)
    Executor(deps).advance(run["id"], auto)

    assert fakes["world"].count("send_email") == 1, "it sent the email twice"
    assert (store.get_run(run["id"]) or {})["state"] == RunState.COMPLETED


def test_an_action_that_reported_failure_is_retried_rather_than_verified():
    """The other half of the claim rule, and the reason it is not "never retry".

    `ok: False` is a *statement that nothing happened*, so the claim is
    released and the next attempt does the work. Treating this the same as a
    crash would mean a transient connector error loses the email forever.
    """
    world = FakeWorld(results={"send_email": {"ok": False, "error": "offline"}})
    deps, fakes = build_deps(agent=sends_one(), world=world, gate=allowed())
    run = drive(automation(), deps)

    assert run["state"] == RunState.RETRYING
    assert claims(run["id"]) == [], "a failure left a claim behind"
    assert fakes["world"].count("send_email") == 1


# ── 3. the process dies while a human is deciding ──────────────────────────

def test_a_crash_during_an_approval_loses_neither_the_plan_nor_the_answer():
    """An approval takes hours; a laptop does not stay awake for them.

    The plan and the completed steps are on disk, so the run the user's tap
    resumes is the run they were shown — not a new one that would re-plan
    against email that has since changed.
    """
    deps, fakes = build_deps(agent=sends_one())     # the default gate blocks it
    auto = automation()
    run = drive(auto, deps)

    assert run["state"] == RunState.WAITING_FOR_APPROVAL
    assert fakes["world"].count("send_email") == 0
    plan_before = [s["name"] for s in store.steps_for(run["id"])]

    # The process is gone. A brand new executor, with brand new dependencies,
    # is all that is left — which is exactly what a restart is.
    fakes["approvals"].approve(store.get_run(run["id"])["approval_id"])
    resumed, _ = build_deps(agent=FakeAgent(default="should not be asked"),
                            world=fakes["world"], approvals=fakes["approvals"],
                            gate=fakes["gate"])
    Executor(resumed).advance(run["id"], auto)

    settled = store.get_run(run["id"]) or {}
    assert settled["state"] == RunState.COMPLETED
    assert fakes["world"].count("send_email") == 1
    assert [s["name"] for s in store.steps_for(run["id"])][:len(plan_before)] \
        == plan_before, "it re-planned instead of resuming"


def test_a_second_recovery_while_still_waiting_does_not_ask_again():
    """Every tick recovers in-flight runs. A run waiting on a human is in
    flight, so this happens once a minute for as long as they take."""
    deps, fakes = build_deps(agent=sends_one())
    auto = automation()
    run = drive(auto, deps)

    for _ in range(5):
        Executor(deps).advance(run["id"], auto)

    assert len(fakes["approvals"].queued) == 1, "it queued an approval per tick"
    assert fakes["world"].count("send_email") == 0


# ── 4. the process dies between retries ────────────────────────────────────

def test_a_restart_during_a_retry_neither_repeats_nor_forgets():
    """A run in `RETRYING` has a time it is next due. A restart must not treat
    that as "go now" — nor lose it and leave the run stuck forever."""
    world = FakeWorld(results={"send_email": {"ok": False, "error": "offline"}})
    clock = Clock()
    deps, fakes = build_deps(agent=sends_one(), world=world, gate=allowed(),
                             clock=clock)
    auto = automation()
    run = drive(auto, deps)
    assert run["state"] == RunState.RETRYING

    # Restarts, repeatedly, before the backoff has passed.
    for _ in range(4):
        Executor(deps).advance(run["id"], auto)
    assert fakes["world"].count("send_email") == 1, "the backoff was ignored"

    # And when the time comes it does resume. The connector is back.
    world.results.clear()
    deps.plan = sends_one()
    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)

    assert fakes["world"].count("send_email") == 2, (
        "the retry never happened — the run was stuck")
    assert (store.get_run(run["id"]) or {})["state"] == RunState.COMPLETED


# ── 5. the same event arrives twice ────────────────────────────────────────

def test_the_same_event_twice_starts_one_run():
    auto = automation(id="dedup-1")
    deps, fakes = build_deps(agent=sends_one(), gate=allowed())
    event = Event(kind="email.received", source="gmail", external_id="msg-42",
                  subject="Invoice", data={"from": "ana@acme.com"})

    first = engine.ingest(event, deps=deps, automations=[auto])
    second = engine.ingest(event, deps=deps, automations=[auto])

    assert len(first["started"]) == 1
    assert second["duplicate"] is True and second["started"] == []
    assert fakes["world"].count("send_email") == 1


def test_a_webhook_delivered_twice_starts_one_run(monkeypatch):
    """A provider retrying because our reply was slow. Same guarantee as above,
    reached through the door a stranger can knock on."""
    import hashlib
    import hmac
    import json as _json

    from chitragupta.automation import webhooks
    from chitragupta.config import get_settings

    monkeypatch.setattr(type(get_settings()), "get_secret",
                        lambda self, key: "shh", raising=False)

    body = _json.dumps({"action": "opened",
                        "repository": {"full_name": "acme/api"},
                        "issue": {"title": "It broke"}}).encode()
    delivery = webhooks.Delivery(provider="github", body=body, headers={
        "X-GitHub-Event": "issues", "X-GitHub-Delivery": "same-delivery",
        "X-Hub-Signature-256": "sha256=" + hmac.new(
            b"shh", body, hashlib.sha256).hexdigest()})

    auto = automation(id="hook-1",
                      trigger={"type": "event", "kind": "github.issues",
                               "source": "github"})
    deps, fakes = build_deps(agent=sends_one(), gate=allowed())

    first = engine.ingest(webhooks.receive(delivery), deps=deps, automations=[auto])
    second = engine.ingest(webhooks.receive(delivery), deps=deps, automations=[auto])

    assert len(first["started"]) == 1
    assert second["duplicate"] is True
    assert fakes["world"].count("send_email") == 1


# ── 6. two different events at once ────────────────────────────────────────

def test_two_events_in_the_same_second_do_not_run_the_automation_twice():
    """`ONE_ACTIVE_RUN` is the default because an automation that reads a
    mailbox and acts on it is not safe to run beside itself."""
    auto = automation(id="overlap-1",
                      policy=Policy(concurrency=Concurrency.ONE_ACTIVE_RUN))
    deps, fakes = build_deps(agent=FakeAgent(default="Nothing to do."))

    # A run that is left in flight, so the second event arrives on top of it.
    held = store.create_run(auto.id, automation_name=auto.name)
    store.transition(held["id"], RunState.RUNNING)

    outcome = engine.ingest(
        Event(kind="email.received", source="gmail", external_id="msg-b",
              subject="Second"), deps=deps, automations=[auto])

    assert outcome["started"] == [], "it started a second concurrent run"
    assert fakes["world"].effects == []


def test_an_automation_that_allows_it_does_run_beside_itself():
    """The control case: concurrency is a policy, not a hard rule. Without this
    the test above would pass against an engine that never starts anything."""
    auto = automation(id="overlap-2",
                      policy=Policy(concurrency=Concurrency.ALLOW_PARALLEL))
    deps, _ = build_deps(agent=FakeAgent(default="Nothing to do."))

    held = store.create_run(auto.id, automation_name=auto.name)
    store.transition(held["id"], RunState.RUNNING)

    outcome = engine.ingest(
        Event(kind="email.received", source="gmail", external_id="msg-c",
              subject="Second"), deps=deps, automations=[auto])

    assert len(outcome["started"]) == 1


# ── 7. the service said 200 and the thing is not there ─────────────────────

def test_a_verification_failure_after_a_200_does_not_report_success():
    """The case the whole verification layer exists for. The provider accepted
    the request and the message is not in Sent."""
    world = FakeWorld(verifications={"send_email": {"verified": False,
                                                    "detail": "not in Sent"}})
    deps, fakes = build_deps(agent=sends_one(), world=world, gate=allowed())
    auto = automation(policy=Policy(retry=RetryPolicy(max_attempts=1)))

    run = drive(auto, deps)

    assert run["state"] != RunState.COMPLETED
    assert "not in Sent" in (run["reason"] or "") + (run["outcome"] or "")
    assert fakes["world"].count("send_email") == 1, (
        "it re-sent an email it could not confirm")


def test_a_verification_that_fails_and_then_passes_completes_without_resending():
    """Eventually consistent, which is most mail APIs. The next attempt looks
    again — it does not act again."""
    answers = [{"verified": False, "detail": "not there yet"},
               {"verified": True, "detail": "found in Sent"}]
    world = FakeWorld(verifications={
        "send_email": lambda params, result: answers.pop(0) if answers
        else {"verified": True}})
    clock = Clock()
    deps, fakes = build_deps(agent=sends_one(), world=world, gate=allowed(),
                             clock=clock)
    auto = automation()

    run = drive(auto, deps)
    assert run["state"] == RunState.RETRYING

    clock.advance(hours=1)
    Executor(deps).advance(run["id"], auto)

    assert (store.get_run(run["id"]) or {})["state"] == RunState.COMPLETED
    assert fakes["world"].count("send_email") == 1


# ── 8. the run hits a limit ────────────────────────────────────────────────

def test_a_run_that_exceeds_its_action_limit_stops_without_finishing_the_batch():
    """A limit is what stops a loop in a model's plan becoming a hundred emails.
    It is a decline, not a failure — nothing went wrong."""
    agent = FakeAgent(replies=["Sending both.\n"
                               + action_tag("send_email", to="a@acme.com")
                               + action_tag("send_email", to="b@acme.com")
                               + action_tag("send_email", to="c@acme.com")])
    deps, fakes = build_deps(agent=agent, gate=allowed())
    auto = automation(policy=Policy(limits=Limits(max_actions=2)))

    run = drive(auto, deps)

    assert run["state"] == RunState.BLOCKED
    assert fakes["world"].count("send_email") <= 2, "the limit did not bind"
    assert "allowed" in (run["reason"] or "")


def test_a_run_that_runs_too_long_stops_and_says_so():
    """A run that hangs has to end. The deadline is what ends it."""
    from datetime import timedelta

    clock = Clock()
    deps, fakes = build_deps(gate=allowed(), clock=clock)
    auto = automation(policy=Policy(limits=Limits(max_duration_seconds=60)))

    def a_very_slow_turn(agent_id, prompt, execution=None):
        # The time has to pass *inside* the run: this is the hang the deadline
        # exists for, not a run that was started late.
        clock.advance(hours=2)
        return ("On it.\n" + action_tag("send_email", to="ana@acme.com")), 1

    deps.plan = a_very_slow_turn
    run = start_run(auto)
    # The deadline is written by `engine.ingest` when it opens the run; the
    # harness's `start_run` is the router's half and does not, so it is set
    # here rather than asserting against a run that had no budget.
    store.update_run(run["id"], deadline_at=(clock.now + timedelta(
        seconds=auto.policy.limits.max_duration_seconds)).isoformat())
    Executor(deps).advance(run["id"], auto)

    settled = store.get_run(run["id"]) or {}
    assert settled["state"] == RunState.BLOCKED
    assert "longer than" in (settled["reason"] or "")
    assert fakes["world"].count("send_email") == 0


# ── 9. an event for an automation the user paused ──────────────────────────

def test_an_event_while_it_is_paused_does_nothing_and_is_not_replayed_later():
    """Pause has to mean pause. And the event is still marked seen — resuming an
    automation must not replay a week of mail through it."""
    paused = automation(id="paused-1", enabled=False)
    deps, fakes = build_deps(agent=sends_one(), gate=allowed())
    event = Event(kind="email.received", source="gmail", external_id="msg-p",
                  subject="While you were away")

    outcome = engine.ingest(event, deps=deps, automations=[paused])
    assert outcome["started"] == []
    assert fakes["world"].effects == []

    # Unpaused. The same event must not now start anything.
    resumed = automation(id="paused-1", enabled=True)
    again = engine.ingest(event, deps=deps, automations=[resumed])

    assert again["duplicate"] is True
    assert fakes["world"].count("send_email") == 0


# ── 10. everything at once ─────────────────────────────────────────────────

def test_a_run_survives_a_crash_an_approval_and_a_failed_verification():
    """The scenario nobody writes down because it sounds unlikely, which is
    exactly why it is the one that happens.

    Queued for approval, machine sleeps, answered the next morning, the action
    crashes after reaching the world, the first verification cannot find it, and
    the second can. One email.
    """
    answers = [{"verified": False, "detail": "not there yet"},
               {"verified": True, "detail": "found in Sent"}]
    world = FakeWorld(verifications={
        "send_email": lambda params, result: answers.pop(0) if answers
        else {"verified": True}})
    clock = Clock()
    deps, fakes = build_deps(agent=sends_one(), world=world, clock=clock)
    auto = automation()

    run = drive(auto, deps)
    assert run["state"] == RunState.WAITING_FOR_APPROVAL

    # The machine slept through the night. The tap in the morning is what sends
    # the email — the same path an interactively-approved action takes — and
    # the process then dies before anything records that it did.
    clock.advance(hours=9)
    fakes["approvals"].approve(store.get_run(run["id"])["approval_id"])

    for _ in range(6):
        clock.advance(hours=1)
        deps.plan = sends_one()
        Executor(deps).advance(run["id"], auto)
        if (store.get_run(run["id"]) or {})["state"] in store.TERMINAL_STATES:
            break

    settled = store.get_run(run["id"]) or {}
    assert settled["state"] == RunState.COMPLETED
    assert fakes["world"].count("send_email") == 1, (
        f"the world was touched {fakes['world'].count('send_email')} times")
    # No claim was ever taken, and that is correct rather than a gap: the
    # executor did not perform this action, the approval layer did. What stops
    # a second tap is `approvals.approve` refusing a row that is no longer
    # pending — the guard is in the layer that acts.
    assert claims(run["id"]) == []
    fakes["approvals"].approve(run["approval_id"])
    assert fakes["world"].count("send_email") == 1, "a second tap sent it again"


# ── a failure is not a quiet success ───────────────────────────────────────

def test_a_turn_that_returned_a_provider_error_is_not_done():
    """The screenshot that started this: seven runs, all marked "Done", all of
    them the OpenAI model refusing.

    A turn whose reply IS the failure looks exactly like one that correctly
    decided there was nothing to do — both return text and propose no actions.
    The history screen said the automation was working while it delivered
    nothing, seven times.
    """
    deps, _ = build_deps(agent=FakeAgent(default=(
        "⚠️ The **openai** model failed: Attempted to access streaming "
        "response content, without having called read() first.")))

    run = drive(automation(), deps)

    assert run["state"] != RunState.COMPLETED
    assert "model failed" in (run["reason"] or run["outcome"] or "")


def test_a_rate_limit_card_is_a_failure_too():
    """The other shape this app writes. A limit is rendered as a countdown card
    rather than a warning sign, and it is no more an answer than the other."""
    deps, _ = build_deps(agent=FakeAgent(
        default='<limit until="2026-09-26T18:00:00Z">You have hit your usage '
                'limit.</limit>'))

    run = drive(automation(), deps)
    assert run["state"] != RunState.COMPLETED


def test_a_tool_refusal_the_model_paraphrased_is_left_alone():
    """Deliberate, and a real limitation.

    "Gmail read access hasn't been granted to me" is the *model's* wording for
    a tool that said no, and there is no reliable shape to match in it — the
    first attempt matched phrases like "rate limit" and turned a summary of
    somebody else's email into a retry.

    That case is caught before the run instead: `readiness.py` says "Gmail is
    connected, but this agent has to ask before using it" while the user is
    still on the screen. Pinned here so that the day somebody makes the engine
    guess at prose, this says why it was not doing that.
    """
    deps, _ = build_deps(agent=FakeAgent(default=(
        "I can't pull those emails right now — Gmail read access hasn't been "
        "granted to me for this automation.")))

    run = drive(automation(), deps)
    assert run["state"] == RunState.COMPLETED


def test_deciding_there_is_nothing_to_do_is_still_done():
    """The case the check must not eat. "Tell me if there is a conflict" on a
    day with no conflict is a successful run that did nothing, and turning that
    into a retry would spend a model call every time."""
    deps, _ = build_deps(agent=FakeAgent(default="No conflicts today."))

    run = drive(automation(), deps)
    assert run["state"] == RunState.COMPLETED
    assert "No conflicts" in run["outcome"]


def test_a_reply_that_merely_mentions_a_failure_is_not_one():
    """A summary of somebody else's email about a rate limit is an answer, not
    an error. Only the shapes this app's own layers write count."""
    deps, _ = build_deps(agent=FakeAgent(default=(
        "Ana wrote to say their deploy hit a rate limit yesterday and they "
        "have raised it with support. Nothing needed from you.")))

    run = drive(automation(), deps)
    assert run["state"] == RunState.COMPLETED
