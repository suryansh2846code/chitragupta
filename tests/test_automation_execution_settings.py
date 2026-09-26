"""What an automation may say about *how* it runs, and why none of it widens.

Four settings, taken from a user who had written a careful instruction and found
the form had nowhere to put half of it: which model, which apps, whether it may
look at web pages or send anything, and how quickly to check.

The claim that makes them safe is one sentence: **every field narrows or
substitutes, and none of them widen.** An automation may never be able to do
something an interactive agent may not — so a test here that showed one of these
granting something would be the feature being wrong, not the test.

The other claim is that they reach the turn at all. A setting stored and ignored
is worse than one that does not exist: the screen says the automation is running
on a cheap model and it is not.
"""
from __future__ import annotations

import pytest
from automation_harness import (
    FakeAgent,
    action_tag,
    automation,
    build_deps,
    drive,
    fresh_store,
)

from chitragupta.agents import connector_grants, permissions
from chitragupta.automation import engine
from chitragupta.automation.model import Execution
from chitragupta.core.automation_store import RunState


@pytest.fixture(autouse=True)
def _own_database():
    """A fresh run ledger, and no watches left behind.

    `fresh_store` gives this test its own automation database; the *routines*
    live in another one that the whole session shares, so a watch created here
    would still be asking for a fast check in every test after it.
    """
    from chitragupta.core.routine_store import get_routines

    fresh_store()
    before = {r["id"] for r in get_routines().list()}
    yield
    for row in get_routines().list():
        if row["id"] not in before:
            get_routines().delete(row["id"])


# ── it reaches the turn ────────────────────────────────────────────────────

def test_the_settings_reach_the_turn_rather_than_being_stored_and_ignored():
    """A screen saying an automation runs on a cheap model while it runs on the
    expensive one is worse than no setting at all."""
    agent = FakeAgent(default="nothing to do")
    deps, fakes = build_deps(agent=agent)
    auto = automation(execution=Execution(provider="openai", model="gpt-x",
                                          effort="low"))

    drive(auto, deps)

    assert fakes["agent"].executions[-1]["provider"] == "openai"
    assert fakes["agent"].executions[-1]["model"] == "gpt-x"
    assert fakes["agent"].executions[-1]["effort"] == "low"


def test_an_automation_that_says_nothing_runs_on_the_users_settings():
    """The default and what every automation made before this has. Empty is
    "whatever the user chose", not "a provider called empty string"."""
    deps, fakes = build_deps(agent=FakeAgent(default="ok"))
    drive(automation(), deps)

    said = fakes["agent"].executions[-1]
    assert said["provider"] == "" and said["model"] == "" and said["effort"] == ""


# ── the apps it may reach: a ceiling, never a grant ────────────────────────

def test_a_scope_beats_a_stored_grant():
    token = connector_grants.only_these(["gmail"])
    try:
        assert connector_grants.may_use("chief-of-staff", "gmail") is True
        assert connector_grants.may_use("chief-of-staff", "gcal") is False
    finally:
        connector_grants.release_only(token)


def test_a_scope_beats_an_unrestricted_agent():
    """The case the whole thing is for. Chief of Staff may reach everything;
    a mail-watching automation running as Chief of Staff may not."""
    unrestricted = next(
        (aid for aid in ("chief-of-staff", "chief_of_staff")
         if connector_grants.unrestricted(aid)), "")
    if not unrestricted:
        pytest.skip("no unrestricted template in this build")

    token = connector_grants.only_these(["gmail"])
    try:
        assert connector_grants.may_use(unrestricted, "gcal") is False
    finally:
        connector_grants.release_only(token)
    assert connector_grants.may_use(unrestricted, "gcal") is True


def test_choosing_no_apps_is_not_a_scope_of_nothing():
    """An automation that may reach no app at all is not a thing anyone wants,
    and reading empty that way would silently break every automation whose
    settings are empty because they predate this."""
    unscoped = connector_grants.may_use("personal", "gmail")
    token = connector_grants.only_these([])
    try:
        assert connector_grants.scoped_to() is None
        assert connector_grants.may_use("personal", "gmail") is unscoped, (
            "an empty choice changed what the agent may reach")
    finally:
        connector_grants.release_only(token)


def test_the_ceiling_is_lifted_on_every_way_out():
    """It is a ContextVar. One turn that raised and left it set would silently
    restrict every turn after it."""
    agent = FakeAgent(raises=True)
    deps, _ = build_deps(agent=agent)
    auto = automation(execution=Execution(connectors=("gmail",)))

    drive(auto, deps)

    assert connector_grants.scoped_to() is None
    assert permissions.browsing_banned() is False


# ── whether it may look at a page ──────────────────────────────────────────

def test_browsing_off_refuses_every_page_tool():
    from chitragupta.agents import browse_tools

    token = permissions.without_browsing()
    try:
        for call in (lambda: browse_tools.browse_open("https://example.test"),
                     browse_tools.browse_read,
                     browse_tools.browse_sites,
                     lambda: browse_tools.browse_find("anything")):
            assert call().ok is False
    finally:
        permissions.allow_browsing(token)


def test_acting_on_a_page_is_still_refused_whatever_this_says():
    """The floor under everything unattended. Nothing an automation declares
    about itself can lift it, which is why this setting is only ever a second
    lock on the same door."""
    from chitragupta.agents import browse_tools

    with permissions.as_unattended():
        assert browse_tools.browse_click(ref="1").ok is False
        assert browse_tools.browse_type("hello", ref="1").ok is False
        assert browse_tools.browse_submit(ref="1").ok is False


# ── whether it may send anything outward ───────────────────────────────────

def test_sending_off_stops_an_email_before_the_gate_is_even_asked():
    """The reason the user reads should be the setting they chose, not a
    permission they did not."""
    agent = FakeAgent(replies=[action_tag("send_email", to="ana@acme.com",
                                          subject="Hi")])
    deps, fakes = build_deps(agent=agent)
    auto = automation(execution=Execution(allow_email=False))

    run = drive(auto, deps)

    assert run["state"] == RunState.BLOCKED
    assert "switched off" in (run["reason"] or "")
    assert fakes["world"].count("send_email") == 0
    assert fakes["approvals"].queued == {}, "it asked a human about a settled no"


def test_a_draft_is_not_sending():
    """It lands in the user's own mailbox. That is the half of "write this for
    me" that reaches nobody, and switching sending off should not take it."""
    assert Execution(allow_email=False).blocks("create_draft") == ""
    assert Execution(allow_email=False).blocks("send_email")
    assert Execution(allow_email=False).blocks("message_send")


def test_sending_on_changes_nothing_by_itself():
    """It is not a permission. An address still has to be on the allow-list,
    which is what the gate answers afterwards."""
    agent = FakeAgent(replies=[action_tag("send_email", to="stranger@x.test")])
    deps, fakes = build_deps(agent=agent)

    run = drive(automation(execution=Execution(allow_email=True)), deps)

    assert run["state"] == RunState.WAITING_FOR_APPROVAL
    assert fakes["world"].count("send_email") == 0


# ── how soon it is checked ─────────────────────────────────────────────────

def make_watch(source: str, minutes: int, enabled: bool = True):
    from chitragupta.core.routine_store import get_routines

    row = get_routines().create(f"Watch {source}", "personal", "new_email",
                                "tell me", 60)
    get_routines().set_fields(
        row["id"],
        enabled=1 if enabled else 0,
        trigger_json=f'{{"type": "event", "kind": "email.received", '
                     f'"source": "{source}"}}',
        execution_json=f'{{"check_minutes": {minutes}}}')
    return row["id"]


def test_a_watch_can_ask_for_its_source_sooner():
    make_watch("gmail", 2)
    assert engine.wanted_sooner() == {"gmail": 2}


def test_the_tightest_request_wins():
    """Two automations asking different numbers of the same app is one question
    with one answer, not two schedules."""
    make_watch("gmail", 10)
    make_watch("gmail", 2)
    assert engine.wanted_sooner()["gmail"] == 2


def test_a_paused_watch_asks_for_nothing():
    """Pause has to mean pause, including the cost."""
    make_watch("gmail", 2, enabled=False)
    assert engine.wanted_sooner() == {}


def test_a_watch_with_no_app_named_asks_for_nothing():
    """There is no honest way to poll "any app". The user has to say which one
    before we spend their quota on it."""
    from chitragupta.core.routine_store import get_routines

    row = get_routines().create("Any mail", "personal", "new_email", "x", 60)
    get_routines().set_fields(
        row["id"], trigger_json='{"type": "event", "kind": "email.received"}',
        execution_json='{"check_minutes": 2}')
    assert engine.wanted_sooner() == {}


def test_the_scheduler_checks_it_on_time_and_not_before():
    """Driven, not waited on. The whole point is that it happens between full
    syncs, and a test that slept for two minutes would never be run."""
    from chitragupta.scheduler import Scheduler

    make_watch("gmail", 2)
    scheduler = Scheduler()
    done: list[set] = []
    scheduler._sync_one_pass = lambda only: done.append(only)

    assert scheduler.fast_check(now=1000.0) == ["gmail"]
    assert done == [{"gmail"}]

    assert scheduler.fast_check(now=1060.0) == [], "it checked again too soon"
    assert scheduler.fast_check(now=1121.0) == ["gmail"]
    assert len(done) == 2


def test_nothing_waiting_costs_nothing():
    """This runs once a minute, and on almost every one of them the answer is
    that nobody is waiting."""
    from chitragupta.scheduler import Scheduler

    scheduler = Scheduler()
    scheduler._sync_one_pass = lambda only: pytest.fail("it synced for nobody")
    assert scheduler.fast_check(now=1000.0) == []
