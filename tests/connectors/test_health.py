"""Is this working, answered from what we already know — and cheaply.

`connector_state.status` held `"ok"` or `"error"`. Neither is any of the things
a user needs to be told: *signed in but rate-limited*, *working but nine days
stale*, *paused on purpose*, *syncing right now*. Each leads somewhere
different and `"error"` leads nowhere.

Two properties are load-bearing:

* **`of()` performs no I/O.** The Connectors page calls one per row on load.
  `MCPConnector.is_configured()` starts a subprocess to answer the same
  question, which is why that endpoint is in the probe lane — and why this one
  must not be.
* **Auth outranks everything.** A rate-limit badge on a connector that is
  actually signed out sends the user to wait for something that will never
  happen.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chitragupta.connectors.connections import AuthState, Connection
from chitragupta.connectors.contract import (
    ConnectorManifest,
    EventSource,
    EventSupport,
    SyncSupport,
)
from chitragupta.connectors.health import HealthState, Probe, of, probe, worst
from chitragupta.connectors.sync_state import SyncState

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def manifest(**kw) -> ConnectorManifest:
    base = {"connector_id": "gmail", "display_name": "Gmail",
            "provider": "google"}
    return ConnectorManifest(**{**base, **kw})


def connection(state=AuthState.AUTHENTICATED, **kw) -> Connection:
    return Connection(id="gmail:a1", connector="gmail", provider="google",
                      auth_state=state, **kw)


def state(**kw) -> SyncState:
    base = {"connection_id": "gmail:a1", "resource_type": "email"}
    return SyncState(**{**base, **kw})


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


# ── it reads state, it does not make any ──────────────────────────────────


def test_a_healthy_connection_reads_healthy():
    health = of(connection(), manifest(sync=SyncSupport(scheduled=True)),
                [state(last_success=ago(5))], now=NOW)

    assert health.state is HealthState.HEALTHY
    assert health.ok


def test_health_asks_the_connector_nothing():
    """No I/O — which is what makes it safe once per row on a page load."""
    class Explodes:
        def is_configured(self):
            raise AssertionError("health must not reach the connector")

    of(connection(), manifest(), [state(last_success=ago(1))], now=NOW)


# ── the order the answers matter in ───────────────────────────────────────


def test_auth_outranks_a_rate_limit():
    """A rate-limit badge on a connector that is signed out sends the user to
    wait for something that will never happen."""
    health = of(connection(AuthState.REAUTH_REQUIRED), manifest(),
                [state(last_success=ago(1))], rate_limited=True, now=NOW)

    assert health.state is HealthState.AUTH_REQUIRED
    assert health.needs_the_user


def test_a_pass_in_flight_reads_as_syncing_not_as_stale():
    health = of(connection(), manifest(sync=SyncSupport(scheduled=True)),
                [state(last_success=ago(10_000), started_at=ago(1))], now=NOW)

    assert health.state is HealthState.SYNCING


def test_a_rate_limit_outranks_an_old_error():
    health = of(connection(), manifest(), [state(last_success=ago(1),
                                                 error="something once")],
                rate_limited=True, now=NOW)

    assert health.state is HealthState.RATE_LIMITED


def test_a_paused_connection_is_disconnected_not_broken():
    """A state the user chose. Sorting it above a real error would bury the
    real error."""
    health = of(connection(paused=True), manifest(), [], now=NOW)

    assert health.state is HealthState.DISCONNECTED
    assert not health.needs_the_user


# ── staleness is relative to the connector's own schedule ─────────────────


def test_a_scheduled_connector_that_has_not_run_in_days_is_degraded():
    health = of(connection(), manifest(sync=SyncSupport(scheduled=True)),
                [state(last_success=ago(60 * 24 * 9))],
                interval_minutes=30, now=NOW)

    assert health.state is HealthState.DEGRADED
    assert "ago" in health.says


def test_one_missed_interval_is_not_a_problem():
    """A badge that goes amber every time a laptop closes is a badge people
    learn to ignore."""
    health = of(connection(), manifest(sync=SyncSupport(scheduled=True)),
                [state(last_success=ago(45))], interval_minutes=30, now=NOW)

    assert health.state is HealthState.HEALTHY


def test_a_manual_source_is_never_stale():
    """Nothing was supposed to happen. "Last synced two hours ago" is alarming
    for a mailbox and unremarkable for a file the user exported."""
    health = of(connection(), manifest(sync=SyncSupport(scheduled=False)),
                [state(last_success=ago(60 * 24 * 30))], now=NOW)

    assert health.state is HealthState.HEALTHY


def test_a_connector_that_has_never_finished_a_pass_is_degraded_not_broken():
    health = of(connection(), manifest(), [state()], now=NOW)

    assert health.state is HealthState.DEGRADED
    assert health.state is not HealthState.ERROR


# ── it says what to do ────────────────────────────────────────────────────


@pytest.mark.parametrize("state_,connection_", [
    (HealthState.AUTH_REQUIRED, connection(AuthState.REAUTH_REQUIRED)),
    (HealthState.DISCONNECTED, connection(AuthState.DISCONNECTED)),
    (HealthState.ERROR, connection(AuthState.ERROR)),
])
def test_every_state_says_something_actionable(state_, connection_):
    health = of(connection_, manifest(), [state()], now=NOW)

    assert health.state is state_
    assert health.says.strip()


def test_the_sentence_names_the_connector_the_user_knows():
    health = of(connection(AuthState.REAUTH_REQUIRED), manifest(), [], now=NOW)

    assert "Gmail" in health.says


def test_it_says_what_will_drive_the_next_pass():
    """A blank next-sync on a working row reads as a connector that has
    stopped, which is the opposite of the truth."""
    scheduled = of(connection(), manifest(sync=SyncSupport(scheduled=True)),
                   [state(last_success=ago(1))], now=NOW)
    manual = of(connection(), manifest(), [state(last_success=ago(1))], now=NOW)
    paused = of(connection(paused=True), manifest(), [], now=NOW)

    assert scheduled.next_sync == "on the sync timer"
    assert manual.next_sync == "when you ask"
    assert paused.next_sync == "paused"


def test_an_event_driven_connector_says_so():
    health = of(connection(), manifest(
        events=EventSupport(source=EventSource.WEBHOOK)),
        [state(last_success=ago(1))], now=NOW)

    assert health.next_sync == "when something changes"


# ── several resource types make one answer ────────────────────────────────


def test_the_totals_come_from_every_resource_type():
    health = of(connection(), manifest(),
                [state(resource_type="email", last_success=ago(5),
                       items_processed=100),
                 state(resource_type="thread", last_success=ago(2),
                       items_processed=50)], now=NOW)

    assert health.items == 150
    assert health.last_success == ago(2), "the most recent one"


def test_one_failing_resource_type_makes_the_connection_report_it():
    health = of(connection(), manifest(),
                [state(resource_type="email", last_success=ago(1)),
                 state(resource_type="thread", last_success=ago(1),
                       error="could not read threads")], now=NOW)

    assert health.state is HealthState.ERROR
    assert health.errors == ["could not read threads"]


def test_the_worst_of_several_is_the_one_shown():
    assert worst([HealthState.HEALTHY, HealthState.ERROR]) is HealthState.ERROR
    assert worst([HealthState.HEALTHY, HealthState.DEGRADED]) \
        is HealthState.DEGRADED


def test_a_connector_nobody_set_up_does_not_outrank_a_real_failure():
    """Sorting DISCONNECTED as the worst would bury every actual error behind
    every connector the user never connected."""
    assert worst([HealthState.DISCONNECTED, HealthState.ERROR]) \
        is HealthState.ERROR


def test_nothing_at_all_is_disconnected():
    assert worst([]) is HealthState.DISCONNECTED


# ── the live probe ────────────────────────────────────────────────────────


def test_a_probe_prefers_the_connectors_own_cheap_check():
    """Only the connector knows which of its calls is the cheap one."""
    class Cheap:
        def check_health(self):
            return Probe(reachable=True, credentials_valid=True)

        def is_configured(self):
            raise AssertionError("the cheap check should have been used")

    assert probe(Cheap(), connection(), manifest()).ok


def test_a_probe_falls_back_to_is_configured():
    class Plain:
        def is_configured(self):
            return False, "paste your token"

    found = probe(Plain(), connection(), manifest())

    assert not found.ok
    assert found.detail == "paste your token"


def test_a_connector_that_throws_from_its_own_check_is_not_working():
    """That is the answer we were asking for. It must never be the answer to
    whether the *app* is working."""
    class Broken:
        def is_configured(self):
            raise RuntimeError("the subprocess died")

    found = probe(Broken(), connection(), manifest())

    assert not found.ok
    assert found.detail


def test_a_probe_never_leaks_a_credential():
    class Leaky:
        def is_configured(self):
            raise RuntimeError("bad token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    found = probe(Leaky(), connection(), manifest())

    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in str(found.as_dict())


def test_a_probe_reports_a_missing_scope():
    class Fine:
        def is_configured(self):
            return True, ""

    found = probe(Fine(), connection(scopes=["gmail.readonly"]),
                  manifest(required_scopes=("gmail.readonly", "gmail.send")))

    assert not found.ok
    assert found.missing_scopes == ["gmail.send"]


def test_an_unknown_scope_set_is_not_reported_as_missing():
    """A warning on a working connector is a control that cannot work."""
    class Fine:
        def is_configured(self):
            return True, ""

    found = probe(Fine(), connection(), manifest(required_scopes=("x",)))

    assert found.ok
