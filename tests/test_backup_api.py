"""Backup and restore, reachable from the product.

The archive format itself is covered in `test_archive.py`. What matters here is
the behaviour a *person* meets: that a long job reports progress instead of
hanging, that it can be stopped, that a wrong passphrase is refused before
anything starts, that the recovery code is shown exactly once, and that two
operations cannot run over the same home at the same time.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from chitragupta.api.app import app
from chitragupta.archive import identity, job, progress
from chitragupta.config import get_settings


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point the endpoints at a home of this test's own.

    **The reason this exists is the worst bug in this feature's history, and it
    was in the tests rather than the product.** These routes act on
    `get_settings().home`, and under pytest that is one directory shared by the
    whole session (`conftest.py` sets `CHITRAGUPTA_HOME` once at import). So
    `/api/backup/restore` replaced the live databases every other test was
    using, and `_clear_sidecars` deleted their `-wal` and `-shm` files out from
    under open connections — leaving `agents.db` locked for the rest of the
    run. **62 unrelated tests failed** with `database is locked`, in files that
    pass perfectly on their own, and every one of them sorts after this file.

    A test that drives a *restore* has to own the thing being restored over.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(get_settings(), "home", home)
    assert get_settings().home == home
    # A database to back up, so the archive is not empty.
    con = sqlite3.connect(home / "chitragupta.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE memories (v TEXT)")
    con.execute("INSERT INTO memories VALUES ('a memory')")
    con.commit()
    con.close()

    job.reset_for_tests()
    identity.forget()
    yield home
    job.reset_for_tests()
    identity.forget()


def _wait_for_idle(client: TestClient, timeout: float = 30.0) -> dict:
    """Poll the status endpoint the way the UI does."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get("/api/backup/status").json()
        if not state["running"]:
            return state
        time.sleep(0.05)
    raise AssertionError("the job never finished")


# ── state ───────────────────────────────────────────────────────────────────

def test_state_says_backup_is_not_set_up_yet(client):
    state = client.get("/api/backup/state").json()
    assert state["set_up"] is False
    assert state["recovery_code_saved"] is False
    assert state["default_dir"]


def test_state_names_what_will_not_be_backed_up(client):
    """Tier 0 is surfaced before the user commits, not discovered after."""
    withheld = client.get("/api/backup/state").json()["withheld"]
    assert "google_token.json" in withheld
    assert "telegram" in withheld
    assert all(reason for reason in withheld.values())


# ── the recovery code is shown once ─────────────────────────────────────────

def test_the_recovery_code_is_returned_on_the_first_backup_only(client, tmp_path):
    first = client.post("/api/backup/start", json={
        "passphrase": "a strong passphrase",
        "path": str(tmp_path / "one.cgarch")}).json()
    assert first["first_backup"] is True
    code = first["recovery_code"]
    assert code and len(code.replace("-", "")) == 32
    _wait_for_idle(client)

    second = client.post("/api/backup/start", json={
        "passphrase": "a strong passphrase",
        "path": str(tmp_path / "two.cgarch")}).json()
    assert second["first_backup"] is False
    assert second["recovery_code"] is None, \
        "the code must never be re-sent — it is not stored, so it cannot be"
    _wait_for_idle(client)


def test_both_backups_open_with_the_same_secret(client, tmp_path):
    """The master key persists, which is the whole reason it is in the Keychain:
    one passphrase opens every backup this Mac has written."""
    first = client.post("/api/backup/start", json={
        "passphrase": "pw", "path": str(tmp_path / "one.cgarch")}).json()
    _wait_for_idle(client)
    code = first["recovery_code"]

    client.post("/api/backup/start", json={
        "passphrase": "pw", "path": str(tmp_path / "two.cgarch")})
    _wait_for_idle(client)

    for name in ("one.cgarch", "two.cgarch"):
        seen = client.post("/api/backup/inspect",
                           json={"path": str(tmp_path / name)})
        assert seen.status_code == 200, seen.text
        assert sorted(seen.json()["unlock_methods"]) == ["passphrase", "recovery"]

    # and the FIRST code opens the SECOND backup
    from chitragupta.archive import crypto, reader
    with (tmp_path / "two.cgarch").open("rb") as src:
        header, _, _ = crypto.read_header(src)
    assert reader.master_key_from(header, None, code)


def test_saving_the_code_is_recorded(client, tmp_path):
    client.post("/api/backup/start", json={
        "passphrase": "pw", "path": str(tmp_path / "x.cgarch")})
    _wait_for_idle(client)
    assert client.get("/api/backup/state").json()["recovery_code_saved"] is False

    client.post("/api/backup/recovery-code/saved")
    assert client.get("/api/backup/state").json()["recovery_code_saved"] is True


# ── progress and stopping ───────────────────────────────────────────────────

def test_a_backup_reports_progress_and_then_a_result(client, tmp_path):
    out = tmp_path / "progress.cgarch"
    started = client.post("/api/backup/start",
                          json={"passphrase": "pw", "path": str(out)})
    assert started.status_code == 200, started.text
    assert started.json()["running"] is True

    final = _wait_for_idle(client)
    assert final["error"] == ""
    assert final["cancelled"] is False
    assert final["result"]["bytes"] > 0
    assert final["result"]["summary"]
    assert out.exists()


def test_status_is_readable_before_anything_has_ever_run(client):
    state = client.get("/api/backup/status").json()
    assert state["running"] is False
    assert state["result"] is None
    assert "stop" not in state, "the internal stop flag must not be published"


def test_a_stopped_job_is_not_reported_as_an_error(client, tmp_path):
    """Stopping is the user succeeding at something, not the app failing."""
    job.reset_for_tests()
    prog = job._claim(job.BACKUP)
    job.stop()

    def work(p: progress.Progress):
        p.enter("x", total=3)
        p.step()        # raises CancelledError, because stop() was called
        raise AssertionError("should not get here")

    job._run(job.BACKUP, work, prog)
    state = client.get("/api/backup/status").json()
    assert state["cancelled"] is True
    assert state["error"] == ""
    assert state["running"] is False


def test_stop_on_an_idle_job_is_harmless(client):
    assert client.post("/api/backup/stop").json()["running"] is False


# ── one at a time ───────────────────────────────────────────────────────────

def test_a_second_job_is_refused_while_one_runs(client, tmp_path):
    """Two operations over the same eleven databases would snapshot a home that
    is halfway through being replaced."""
    job.reset_for_tests()
    job._claim(job.BACKUP)          # hold the slot without doing any work
    try:
        clash = client.post("/api/backup/start", json={
            "passphrase": "pw", "path": str(tmp_path / "clash.cgarch")})
        assert clash.status_code == 409
        assert "already running" in clash.json()["detail"]
    finally:
        job.reset_for_tests()


# ── inspect ─────────────────────────────────────────────────────────────────

def test_inspect_needs_no_secret_and_lists_what_to_reconnect(client, tmp_path):
    out = tmp_path / "look.cgarch"
    client.post("/api/backup/start", json={"passphrase": "pw", "path": str(out)})
    _wait_for_idle(client)

    seen = client.post("/api/backup/inspect", json={"path": str(out)}).json()
    assert seen["created_at"]
    assert seen["manifest"]
    assert "passphrase" in seen["unlock_methods"]
    # Whatever Tier 0 the test home happens to hold is named, with a reason.
    assert isinstance(seen["reconnect_needed"], list)


def test_inspecting_a_missing_file_is_a_404(client, tmp_path):
    missing = client.post("/api/backup/inspect",
                          json={"path": str(tmp_path / "nope.cgarch")})
    assert missing.status_code == 404


def test_inspecting_something_that_is_not_a_backup_says_so(client, tmp_path):
    junk = tmp_path / "holiday.jpg"
    junk.write_bytes(b"\xff\xd8\xff\xe0 not a backup")
    bad = client.post("/api/backup/inspect", json={"path": str(junk)})
    assert bad.status_code == 400
    assert "not a Chitragupta backup" in bad.json()["detail"]


# ── restore ─────────────────────────────────────────────────────────────────

def test_a_wrong_passphrase_is_refused_before_any_job_starts(client, tmp_path):
    out = tmp_path / "r.cgarch"
    client.post("/api/backup/start", json={"passphrase": "right", "path": str(out)})
    _wait_for_idle(client)

    bad = client.post("/api/backup/restore",
                      json={"path": str(out), "passphrase": "wrong"})
    assert bad.status_code == 400
    assert "does not open this archive" in bad.json()["detail"]
    assert client.get("/api/backup/status").json()["running"] is False, \
        "a refused restore must not leave a job behind"


def test_restore_with_no_secret_at_all_is_refused(client, tmp_path):
    out = tmp_path / "r2.cgarch"
    client.post("/api/backup/start", json={"passphrase": "pw", "path": str(out)})
    _wait_for_idle(client)

    bare = client.post("/api/backup/restore", json={"path": str(out)})
    assert bare.status_code == 400
    assert "passphrase or recovery code" in bare.json()["detail"]


def test_restoring_a_missing_file_is_a_404(client, tmp_path):
    gone = client.post("/api/backup/restore",
                       json={"path": str(tmp_path / "gone.cgarch"),
                             "passphrase": "pw"})
    assert gone.status_code == 404


def test_a_restore_rebuilds_this_machines_backup_identity(client, tmp_path):
    """After recovering onto a fresh Mac the next backup must join the same set,
    so the master key is remembered from the archive it was unwrapped from."""
    out = tmp_path / "ident.cgarch"
    client.post("/api/backup/start", json={"passphrase": "pw", "path": str(out)})
    _wait_for_idle(client)
    original = identity.keyring()
    assert original and original.master_key

    identity.forget()                       # as a brand-new machine would be
    assert identity.keyring() is None

    started = client.post("/api/backup/restore",
                          json={"path": str(out), "passphrase": "pw"})
    assert started.status_code == 200, started.text
    _wait_for_idle(client)

    rebuilt = identity.keyring()
    assert rebuilt and rebuilt.master_key == original.master_key
    assert identity.recovery_code_acknowledged() is True
    # And the rebuilt keyring carries both wraps out of the archive's header,
    # so the NEXT backup from this Mac — including an automatic one — is
    # openable by the passphrase and the code the user already has.
    assert rebuilt.unlock_methods == ["passphrase", "recovery"]
    assert identity.can_run_unattended() is True


def test_finish_restore_runs_migrations_and_does_not_raise(client):
    """A restored database may predate this version. The call is idempotent, so
    running it on an already-current home must be a quiet no-op."""
    done = client.post("/api/backup/finish-restore")
    assert done.status_code == 200
    assert "migrated" in done.json()


# ── where backups land ──────────────────────────────────────────────────────

def test_a_backup_with_no_path_lands_in_the_home_and_is_listed(client,
                                                               isolated_home):
    started = client.post("/api/backup/start", json={"passphrase": "pw"})
    assert started.status_code == 200, started.text
    final = _wait_for_idle(client)

    written = Path(final["result"]["path"])
    assert written.exists()
    assert written.parent == isolated_home / "backups"
    assert written.suffix == ".cgarch"

    listed = client.get("/api/backup/state").json()["backups"]
    assert str(written) in [b["path"] for b in listed]


def test_a_path_without_the_suffix_gets_one(client, tmp_path):
    started = client.post("/api/backup/start", json={
        "passphrase": "pw", "path": str(tmp_path / "no-suffix")})
    assert started.status_code == 200
    final = _wait_for_idle(client)
    assert final["result"]["path"].endswith(".cgarch")
