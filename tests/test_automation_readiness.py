"""Can it run — asked while the user is still here to do something about it.

An automation is set up once and then left alone for months. Everything it needs
is decided at that moment and discovered at three in the morning, which is the
worst possible time to find out the agent was deleted or the app was signed out.

The rule that keeps this honest: **it is a report, never a decision.** No check
here grants anything, none of them stop a run, and the permission gate is asked
exactly as it would have been. The test that matters most is the one showing
that `asks` is not treated as a fault — "draft it and let me look" is a good
automation, and a screen that called it broken would be teaching the user to
ignore the screen.
"""
from __future__ import annotations

import pytest
from automation_harness import automation, fresh_store

from chitragupta.automation import readiness
from chitragupta.automation.model import Execution


@pytest.fixture(autouse=True)
def _own_database():
    fresh_store()


@pytest.fixture
def an_agent():
    from chitragupta.agents.custom import get_custom_store

    store = get_custom_store()
    agent = store.create("Desk", role="does the desk work")
    yield agent
    store.delete(agent.id)


def apps(**state):
    """A stand-in for the connector layer, which this package may not import."""
    return lambda name: (name.title(), bool(state.get(name, False)))


# ── the verdict ────────────────────────────────────────────────────────────

def test_an_automation_with_what_it_needs_says_so(an_agent):
    report = readiness.review(automation(agent_id=an_agent.id,
                                         trigger={"type": "manual"}))
    assert report["state"] in ("ready", "asks")
    assert report["summary"]


def test_an_agent_that_does_not_exist_is_the_whole_answer(an_agent):
    """The real case this was written for. An automation naming an agent
    nobody has sat in the list reading "Not run yet" all day."""
    report = readiness.review(automation(agent_id="inbox"))

    assert report["state"] == "stuck"
    assert "inbox" in report["summary"]
    agent = next(c for c in report["checks"] if c["name"] == "The agent")
    assert agent["fix"], "it said no without saying what would work"


def test_a_trigger_that_can_never_match_is_caught(an_agent):
    """"Something happens" with nothing named matches no event ever, and the
    automation waits forever without anything saying why."""
    report = readiness.review(automation(
        agent_id=an_agent.id, trigger={"type": "event"}))

    assert report["state"] == "stuck"
    when = next(c for c in report["checks"] if c["name"] == "When it runs")
    assert "does not say what" in when["detail"]


def test_a_time_of_day_with_no_time_is_caught(an_agent):
    report = readiness.review(automation(
        agent_id=an_agent.id, trigger={"type": "schedule", "days": "mon"}))
    assert report["state"] == "stuck"


# ── "it will ask you" is a setting, not a fault ────────────────────────────

def test_waiting_for_a_tap_is_not_reported_as_broken(an_agent):
    """"Draft the reply and let me look" is a good automation. A screen calling
    that broken teaches the user to ignore the screen."""
    report = readiness.review(automation(agent_id=an_agent.id,
                                         trigger={"type": "manual"}))

    assert report["state"] != "stuck"
    if report["state"] == "asks":
        assert "will run" in report["summary"]


def test_sending_switched_off_is_ready_not_a_warning(an_agent):
    """It cannot send, which is exactly what was asked for. Nothing to say
    beyond confirming it."""
    report = readiness.review(automation(
        agent_id=an_agent.id, trigger={"type": "manual"},
        execution=Execution(allow_email=False)))

    sending = next(c for c in report["checks"] if c["name"] == "Sending")
    assert sending["state"] == "ready"
    assert "not send" in sending["detail"]


# ── the apps ───────────────────────────────────────────────────────────────

def test_an_app_that_is_not_connected_stops_it(an_agent):
    report = readiness.review(
        automation(agent_id=an_agent.id, trigger={"type": "manual"},
                   execution=Execution(connectors=("gmail",))),
        app_state=apps(gmail=False))

    assert report["state"] == "stuck"
    gmail = next(c for c in report["checks"] if c["name"] == "Gmail")
    assert "not connected" in gmail["detail"]
    assert "Connectors" in gmail["fix"]


def test_the_app_the_trigger_watches_counts_too(an_agent):
    """An automation that watches Gmail needs Gmail whether or not anybody
    ticked it under "apps it may use"."""
    report = readiness.review(
        automation(agent_id=an_agent.id,
                   trigger={"type": "event", "kind": "email.received",
                            "source": "gmail"}),
        app_state=apps(gmail=False))

    assert any(c["name"] == "Gmail" for c in report["checks"])
    assert report["state"] == "stuck"


def test_an_app_the_agent_must_ask_about_is_a_warning_not_a_stop(an_agent):
    """It will work. It will stop once and wait for a tap, which is the system
    doing what it is for."""
    report = readiness.review(
        automation(agent_id=an_agent.id, trigger={"type": "manual"},
                   execution=Execution(connectors=("gmail",))),
        app_state=apps(gmail=True))

    gmail = next(c for c in report["checks"] if c["name"] == "Gmail")
    assert gmail["state"] in ("ready", "asks")
    assert report["state"] != "stuck"


def test_an_unanswerable_question_is_silence_rather_than_reassurance(an_agent):
    """With no way to ask the connector layer, the apps are simply not
    reported. Saying "ready" about a question nobody asked is the kind of green
    tick that makes every other green tick worthless."""
    report = readiness.review(
        automation(agent_id=an_agent.id, trigger={"type": "manual"},
                   execution=Execution(connectors=("gmail",))))

    assert not any(c["name"] == "Gmail" for c in report["checks"])


# ── it decides nothing ─────────────────────────────────────────────────────

def test_reviewing_grants_nothing_and_stops_nothing():
    """The claim that makes it safe to show. It reads state and returns
    sentences — there is no path from here into the gate."""
    import inspect

    source = inspect.getsource(readiness)
    for forbidden in ("run_or_queue", "permissions.check", "allow_always",
                      "set_fields", "transition(", "create_run"):
        assert forbidden not in source, f"readiness reaches {forbidden}"


def test_the_worst_answer_wins(an_agent):
    """One thing being impossible is the answer, however many other things are
    fine — that is the bit that decides whether leaving it alone is sensible."""
    report = readiness.review(
        automation(agent_id="nobody", trigger={"type": "manual"}),
        app_state=apps())
    assert report["state"] == "stuck"


# ── through the endpoint ───────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app
    return TestClient(app)


def test_the_endpoint_reports_on_a_real_automation(client, an_agent):
    from chitragupta.core.routine_store import get_routines

    row = get_routines().create("Check it", an_agent.id, "daily", "do it", 60)
    try:
        body = client.get(f"/api/automations/{row['id']}/readiness").json()
        assert body["state"] in ("ready", "asks", "stuck")
        assert body["summary"]
        assert any(c["name"] == "The agent" for c in body["checks"])
    finally:
        get_routines().delete(row["id"])


def test_the_endpoint_404s_on_one_that_is_gone(client):
    assert client.get("/api/automations/nope/readiness").status_code == 404
