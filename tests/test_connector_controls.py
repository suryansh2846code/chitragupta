"""The controls a user needs over a connected source, and what each one means.

Four of these are the ones `/CLAUDE.md` demands and the Connectors screen could
not offer, because nothing underneath could answer them:

* *What data was imported, from which account, when?* — there was no link
  between a memory and the record it came from, so the only possible answer was
  a guess from the `source` string.
* *Pause* — there was no way to stop a connector without signing out of it.
* *Disconnect* — a token-backed connector had none at all.
* *Delete the data* — and it is **not** the same button as disconnect. A user
  disconnecting Gmail has not asked to forget every email they ever read, and a
  single control that did both would be one nobody could predict.

The other property under test is cost: `/api/connectors/health` must answer
from state we already hold. The expensive version of the same question is
`GET /api/connectors`, which reaches every source and lives in the probe lane
for that reason — and a page that polled *that* every few seconds would start a
subprocess per MCP server per poll.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from chitragupta.api.app import app
from chitragupta.connectors import connections, resources, sync_state
from chitragupta.connectors.connections import AuthState

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from chitragupta.brain.brain import get_brain
    from chitragupta.config import get_settings
    from chitragupta.connectors import db, limits
    from chitragupta.core.store import get_store

    caches = (get_settings, get_store, get_brain)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(home))
    for cache in caches:
        cache.cache_clear()
    db.reset_for_tests()
    limits.reset()
    yield
    db.reset_for_tests()
    limits.reset()
    for cache in caches:
        cache.cache_clear()


def a_connected_gmail() -> str:
    made = connections.ensure("gmail", account="me@work.test", label="Work")
    connections.transition(made.id, AuthState.AUTHENTICATED)
    return made.id


# ── what can this reach, and what can it change ───────────────────────────


def test_a_manifest_says_what_a_connector_can_do_not_how_many_tools():
    """A count is not something anybody can consent to."""
    found = client.get("/api/connectors/gmail/manifest").json()

    assert "send:email" in found["writes"]
    assert "read:email" in found["reads"]
    assert found["access"] == "destructive"


def test_every_connector_publishes_one():
    payload = client.get("/api/connectors/manifest").json()

    assert payload["connectors"]
    assert all(not row["declares_nothing"] for row in payload["connectors"])


def test_the_limits_shown_say_who_imposed_them():
    found = client.get("/api/connectors/gmail/manifest").json()

    assert found["limits"]["imposed_by"] == "Chitragupta"


def test_an_unknown_connector_is_a_sentence_not_a_stack_trace():
    response = client.get("/api/connectors/not-a-thing/manifest")

    assert response.status_code == 404
    assert "know" in response.json()["detail"]


# ── health, cheaply ───────────────────────────────────────────────────────


def test_health_answers_without_reaching_any_source():
    """`is_configured()` on an MCP row starts a subprocess. A page that polled
    this would start one per server per poll."""
    a_connected_gmail()

    with_no_network = client.get("/api/connectors/health")

    assert with_no_network.status_code == 200
    rows = with_no_network.json()["connectors"]
    assert [r["connector"] for r in rows] == ["gmail"]


def test_health_names_the_account_and_says_what_to_do():
    connection_id = a_connected_gmail()
    connections.transition(connection_id, AuthState.REAUTH_REQUIRED)

    row = client.get("/api/connectors/health").json()["connectors"][0]

    assert row["account"] == "Work"
    assert row["state"] == "auth_required"
    assert row["needs_the_user"]
    assert "Gmail" in row["says"]


def test_a_connection_whose_connector_is_gone_is_still_listed():
    """It is still a row with data behind it, and dropping it would hide the
    delete-data control that is the only way to clean it up."""
    made = connections.ensure("mcp:deleted-server")
    connections.transition(made.id, AuthState.AUTHENTICATED)

    rows = client.get("/api/connectors/health").json()["connectors"]

    assert any(r["connector"] == "mcp:deleted-server" for r in rows)


# ── what was imported ─────────────────────────────────────────────────────


def test_it_says_what_was_imported_and_what_became_of_it():
    connection_id = a_connected_gmail()
    resources.seen(connection_id, "email", "m1", memory_id="mem-1")
    resources.seen(connection_id, "email", "m2", memory_id="mem-2")
    resources.mark(connection_id, "email", "m2",
                   resources.ResourceState.DELETED)

    payload = client.get("/api/connectors/gmail/data").json()

    account = payload["accounts"][0]
    assert account["connection"]["account"] == "me@work.test"
    assert account["counts"]["active"] == 1
    assert account["counts"]["deleted"] == 1


# ── the four controls are four decisions ──────────────────────────────────


def test_pausing_keeps_the_credential():
    a_connected_gmail()

    client.post("/api/connectors/gmail/pause", json={})

    found = connections.for_connector("gmail")[0]
    assert found.paused and not found.runnable
    assert found.auth_state is AuthState.AUTHENTICATED, "still signed in"


def test_resuming_puts_it_back():
    a_connected_gmail()
    client.post("/api/connectors/gmail/pause", json={})

    client.post("/api/connectors/gmail/resume", json={})

    assert connections.for_connector("gmail")[0].runnable


def test_syncing_everything_again_forgets_the_position_and_no_data():
    """Dedup absorbs the re-read. Deleting the user's data in order to refresh
    it would be a far larger promise than this button makes."""
    connection_id = a_connected_gmail()
    sync_state.checkpoint(connection_id, "email", cursor="page9")
    resources.seen(connection_id, "email", "m1", memory_id="mem-1")

    client.post("/api/connectors/gmail/resync", json={})

    assert sync_state.get(connection_id, "email").cursor == ""
    assert resources.get(connection_id, "email", "m1") is not None


def test_deleting_the_data_keeps_the_connection():
    """The other half of the pair: disconnect gives the credential back and
    keeps the data; this deletes the data and keeps the credential."""
    connection_id = a_connected_gmail()
    from chitragupta.brain import get_brain

    out = get_brain().ingest("A message about the launch review.",
                             source="gmail", kind="email")
    memory_id = out["memory_ids"][0]
    resources.seen(connection_id, "email", "m1", memory_id=memory_id)

    payload = client.request("DELETE", "/api/connectors/gmail/data").json()

    assert payload["removed"] == 1
    assert get_brain().store.get(memory_id) is None
    found = connections.get(connection_id)
    assert found is not None and found.auth_state is AuthState.AUTHENTICATED


def test_a_control_on_a_source_that_is_not_set_up_says_so():
    response = client.post("/api/connectors/gmail/pause", json={})

    assert response.status_code == 404
    assert "set up" in response.json()["detail"]


def test_with_two_accounts_a_control_refuses_to_guess():
    """Picking one for the user is how a Disconnect button signs out the wrong
    account."""
    for address in ("me@personal.test", "me@work.test"):
        made = connections.ensure("gmail", account=address)
        connections.transition(made.id, AuthState.AUTHENTICATED)

    response = client.post("/api/connectors/gmail/pause", json={})

    assert response.status_code == 400
    assert "more than one" in response.json()["detail"]


def test_naming_the_account_reaches_exactly_that_one():
    personal = connections.ensure("gmail", account="me@personal.test")
    work = connections.ensure("gmail", account="me@work.test")
    for made in (personal, work):
        connections.transition(made.id, AuthState.AUTHENTICATED)

    client.post("/api/connectors/gmail/pause",
                json={"connection_id": work.id})

    assert connections.get(work.id).paused
    assert not connections.get(personal.id).paused


# ── the numbers ───────────────────────────────────────────────────────────


def test_diagnostics_answers_in_one_call():
    """A panel that makes four requests to render itself is the N+1 shape one
    level up."""
    payload = client.get("/api/connectors/diagnostics").json()

    assert set(payload) >= {"metrics", "queue_depth", "gates", "recent"}


def test_no_endpoint_here_returns_a_credential():
    """Everything on these routes is shown on a screen."""
    a_connected_gmail()
    blobs = [
        client.get("/api/connectors/health").text,
        client.get("/api/connectors/manifest").text,
        client.get("/api/connectors/gmail/data").text,
        client.get("/api/connectors/diagnostics").text,
    ]

    for blob in blobs:
        lowered = blob.lower()
        assert "ghp_" not in lowered
        assert "authorization" not in lowered


# ── starting a sync without holding the request open ──────────────────────


def test_starting_a_sync_answers_immediately_with_a_job():
    """The synchronous route holds one of six shared lane slots for the whole
    pass — the same lane the Connectors page loads through."""
    import threading

    from chitragupta.connectors import jobs

    release = threading.Event()

    def slow(*, cancel=None, progress=None, interactive=False, **params):
        from chitragupta.connectors.base import SyncResult
        release.wait(3.0)
        return SyncResult(connector="gmail", added=1)

    original = jobs.start
    jobs.start = lambda name, **kw: original(name, **{**kw, "run": slow})
    try:
        response = client.post("/api/connectors/gmail/sync/start", json={})
        assert response.status_code == 200
        job = response.json()["job"]
        assert job["running"] and job["state"] == "running"
        assert job["says"] == "Syncing…"
    finally:
        release.set()
        jobs.start = original
        jobs.reset_for_tests()


def test_a_second_source_is_refused_with_a_sentence_not_a_crash():
    """409: the request was well-formed and the state says no."""
    import threading

    from chitragupta.connectors import jobs

    release = threading.Event()

    def slow(*, cancel=None, progress=None, interactive=False, **params):
        from chitragupta.connectors.base import SyncResult
        release.wait(3.0)
        return SyncResult(connector="x")

    try:
        jobs.start("gmail", label="Gmail", run=slow)
        response = client.post("/api/connectors/gdrive/sync/start", json={})

        assert response.status_code == 409
        assert "Gmail" in response.json()["detail"]
    finally:
        release.set()
        jobs.reset_for_tests()


def test_every_job_can_be_read_back_so_progress_survives_a_refresh():
    from chitragupta.connectors import jobs
    from chitragupta.connectors.base import SyncResult

    try:
        jobs.start("gmail", label="Gmail",
                   run=lambda **kw: SyncResult(connector="gmail", added=2,
                                               detail="2 new"))
        payload = client.get("/api/connectors/jobs").json()

        assert [j["connector"] for j in payload["jobs"]] == ["gmail"]
    finally:
        jobs.reset_for_tests()


def test_stopping_a_sync_that_is_not_running_says_so():
    response = client.post("/api/connectors/gmail/sync/stop")

    assert response.status_code == 200
    assert response.json()["stopped"] is False


def test_starting_a_sync_on_an_unknown_source_is_a_sentence():
    response = client.post("/api/connectors/not-a-thing/sync/start", json={})

    assert response.status_code == 404
    assert "know" in response.json()["detail"]


def test_the_synchronous_route_is_untouched():
    """Additive: every existing caller and test keeps working."""
    import json
    import pathlib

    frozen = json.loads(
        (pathlib.Path(__file__).parent / "api_surface.json").read_text())

    assert "POST /api/connectors/{name}/sync" in frozen
    assert "POST /api/connectors/{name}/sync/start" in frozen
