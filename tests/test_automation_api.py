"""The automation endpoints, against the real app.

Two things worth pinning here beyond "it returns 200".

**The list screen's `state` is the worst live thing about an automation**, not
its last run's verdict. A user scanning a list wants "this one needs you" to win
over "and it also ran fine on Tuesday", and getting that backwards means the
one that mattered is the one they scroll past.

**There is no permission field.** An API that could grant an automation more
authority would be a second permission system, and the first rule of this whole
engine is that there is exactly one.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chitragupta.api.app import app
from chitragupta.core import automation_store as store
from chitragupta.core import events as event_log
from chitragupta.core.automation_store import RunState


@pytest.fixture
def client(monkeypatch):
    path = Path(tempfile.mkdtemp()) / "automation.db"
    store.reset_for_tests(path)
    event_log.reset_for_tests(path)
    return TestClient(app)


@pytest.fixture
def routine(monkeypatch):
    """One automation in the routines table, cleaned up afterwards."""
    from chitragupta.core.routine_store import get_routines

    rows = get_routines()
    made = rows.create("Client replies", "inbox", "new_email",
                       "Draft a reply to the client.")
    rows.set_fields(made["id"], goal="clients get a reply")
    yield made
    rows.delete(made["id"])


def test_the_list_renders_an_automation_with_its_trigger_and_policy(client, routine):
    body = client.get("/api/automations").json()
    mine = next(a for a in body["automations"] if a["id"] == routine["id"])
    assert mine["goal"] == "clients get a reply"
    assert mine["trigger"]["kind"] == "email.received"
    assert mine["policy"]["concurrency"] == "one_active_run"
    assert mine["state"] == "active"


def test_a_waiting_run_makes_the_automation_read_as_needing_you(client, routine):
    run = store.create_run(routine["id"], automation_name="Client replies")
    store.transition(run["id"], RunState.RUNNING)
    store.transition(run["id"], RunState.WAITING_FOR_APPROVAL,
                     approval_id="ap-1")

    mine = next(a for a in client.get("/api/automations").json()["automations"]
                if a["id"] == routine["id"])
    assert mine["state"] == "waiting_for_approval"
    assert mine["waiting"] == 1


def test_an_escalation_outranks_a_completed_run(client, routine):
    """The worst live thing wins. A user who has to read three rows to find the
    one that needs them will stop reading."""
    good = store.create_run(routine["id"])
    store.transition(good["id"], RunState.RUNNING)
    store.transition(good["id"], RunState.COMPLETED, outcome="fine")
    bad = store.create_run(routine["id"])
    store.transition(bad["id"], RunState.RUNNING)
    store.transition(bad["id"], RunState.ESCALATED, reason="needs you")

    mine = next(a for a in client.get("/api/automations").json()["automations"]
                if a["id"] == routine["id"])
    assert mine["state"] == "needs_attention"


def test_a_paused_automation_says_paused_whatever_its_runs_did(client, routine):
    from chitragupta.core.routine_store import get_routines
    get_routines().toggle(routine["id"], False)
    mine = next(a for a in client.get("/api/automations").json()["automations"]
                if a["id"] == routine["id"])
    assert mine["state"] == "paused"


def test_one_run_can_be_opened_whole(client, routine):
    """The "why did it do that" screen. It has to answer months later, which is
    why the context snapshot is stored rather than rebuilt."""
    run = store.create_run(routine["id"], automation_name="Client replies",
                           trigger={"kind": "email.received", "subject": "Hi"})
    store.update_run(run["id"], context={"chars": 900, "pieces": [{}]})
    store.add_step(run["id"], kind="condition", name="conditions")

    body = client.get(
        f"/api/automations/{routine['id']}/runs/{run['id']}").json()
    assert body["run"]["trigger"]["subject"] == "Hi"
    assert body["run"]["context"]["chars"] == 900
    assert [s["kind"] for s in body["steps"]] == ["condition"]


def test_a_run_from_another_automation_is_not_served(client, routine):
    other = store.create_run("someone-else")
    assert client.get(
        f"/api/automations/{routine['id']}/runs/{other['id']}").status_code == 404


def test_an_unknown_automation_is_a_404(client):
    assert client.get("/api/automations/nope").status_code == 404


def test_the_vocabulary_is_published_so_the_ui_does_not_keep_its_own_list(client):
    body = client.get("/api/automations/vocabulary").json()
    assert "event" in body["triggers"] and "schedule" in body["triggers"]
    assert "domain_is" in body["conditions"] and "semantic" in body["conditions"]
    assert "one_active_run" in body["concurrency"]


def test_the_trigger_and_conditions_can_be_changed(client, routine):
    reply = client.patch(f"/api/automations/{routine['id']}", json={
        "trigger": {"type": "schedule", "at_time": "08:00", "days": "mon"},
        "conditions": [{"type": "domain_is", "field": "event.from",
                        "value": "acme.com"}],
    })
    assert reply.status_code == 200
    body = reply.json()
    assert body["trigger"]["at_time"] == "08:00"
    assert body["conditions"][0]["type"] == "domain_is"
    assert body["next_run"], "a schedule with no next run tells the user nothing"


def test_a_policy_is_re_parsed_before_it_is_stored(client, routine):
    """A hand-edited or out-of-date policy must not put a value the engine will
    not understand into the database."""
    reply = client.patch(f"/api/automations/{routine['id']}", json={
        "policy": {"concurrency": "whatever", "retry": {"max_attempts": "x"},
                   "unknown_field": True}})
    policy = reply.json()["policy"]
    assert policy["concurrency"] == "one_active_run"
    assert policy["retry"]["max_attempts"] == 3
    assert "unknown_field" not in policy


def test_there_is_no_way_to_grant_permission_through_this_api():
    """An API that could authorise an action would be a second permission
    system. The field does not exist, and this is the test that keeps it
    from being added by accident."""
    from chitragupta.api.routes.automations import AutomationIn
    fields = set(AutomationIn.model_fields)
    assert not (fields & {"permissions", "allow", "recipients", "risk",
                          "unattended", "grants"})


def test_a_run_can_be_stopped(client, routine):
    run = store.create_run(routine["id"])
    store.transition(run["id"], RunState.RUNNING)
    body = client.post(f"/api/automations/runs/{run['id']}/cancel").json()
    assert body["state"] == "cancelled"
    assert store.get_run(run["id"])["state"] == RunState.CANCELLED


def test_stopping_a_settled_run_is_not_an_error(client, routine):
    """Anything the user starts they can stop, and pressing it twice is not a
    failure they need to hear about."""
    run = store.create_run(routine["id"])
    store.transition(run["id"], RunState.RUNNING)
    store.transition(run["id"], RunState.COMPLETED)
    body = client.post(f"/api/automations/runs/{run['id']}/cancel").json()
    assert body["ok"] is True and body["already"] == "completed"


def test_the_activity_feed_is_newest_first(client, routine):
    first = store.create_run(routine["id"], automation_name="a")
    second = store.create_run(routine["id"], automation_name="b")
    runs = client.get("/api/automations/runs").json()["runs"]
    ids = [r["id"] for r in runs]
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_every_automation_endpoint_is_pinned():
    surface = json.loads(
        (Path(__file__).parent / "api_surface.json").read_text())
    for path in ("GET /api/automations", "GET /api/automations/runs",
                 "GET /api/automations/vocabulary",
                 "PATCH /api/automations/{automation_id}",
                 "POST /api/automations/{automation_id}/run"):
        assert path in surface, path
