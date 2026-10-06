"""Backing up without being asked.

Everything before this made a backup *possible*; a backup you have to remember
to take is one nobody takes. So the properties that matter here are the ones
that decide whether it is trustworthy rather than merely present:

* it needs **no secret**, and the archive is still openable by the user's own
  passphrase — the whole point of persisting the wraps;
* it is **off until asked for**, because writing hundreds of megabytes to
  somebody's disk is not a thing to start doing on their behalf;
* it **refuses rather than degrades** when the keyring cannot do it;
* pruning only ever deletes files this feature wrote.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from chitragupta.api.app import app
from chitragupta.archive import automatic, identity, job, reader
from chitragupta.config import get_settings


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A home of this test's own — these paths run real backups.

    The same reason `test_backup_api.py` has one: the session home is shared,
    and a restore or a prune in it reaches every other test's databases.
    """
    place = tmp_path / "home"
    place.mkdir()
    monkeypatch.setattr(get_settings(), "home", place)
    monkeypatch.setattr(get_settings(), "auto_backup", False)
    monkeypatch.setattr(get_settings(), "auto_backup_hours", 24)
    monkeypatch.setattr(get_settings(), "auto_backup_keep", 7)
    con = sqlite3.connect(place / "chitragupta.db")
    con.execute("CREATE TABLE memories (v TEXT)")
    con.execute("INSERT INTO memories VALUES ('a memory')")
    con.commit()
    con.close()
    job.reset_for_tests()
    identity.forget()
    yield place
    job.reset_for_tests()
    identity.forget()


def _wait_idle(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not job.is_running():
            return
        time.sleep(0.05)
    raise AssertionError("the job never finished")


def _set_up(passphrase: str = "the user's passphrase") -> str:
    """Do what a first manual backup does: mint and store a keyring."""
    _keys, code = identity.begin_setup(passphrase)
    return code


# ── off until asked for ─────────────────────────────────────────────────────

def test_it_is_off_until_the_user_asks(home):
    """Writing hundreds of megabytes because somebody installed an update is
    not a feature they chose."""
    plan = automatic.plan()
    assert plan.enabled is False
    assert plan.due is False
    assert "off" in plan.reason.lower()
    assert automatic.run_if_due()["started"] is False
    assert not list((home / automatic.FOLDER).glob("*")) if (
        home / automatic.FOLDER).exists() else True


def test_turning_it_on_is_remembered(home):
    automatic.set_settings(on=True, hours=6, keep_count=3)
    assert automatic.enabled() is True
    assert automatic.every_hours() == 6
    assert automatic.keep() == 3
    # And on disk, so it survives a restart.
    assert (home / automatic.STATE_FILE).exists()


def test_only_the_fields_given_move(home):
    automatic.set_settings(on=True, hours=6, keep_count=3)
    automatic.set_settings(hours=12)
    assert automatic.enabled() is True, "the switch moved when only hours were sent"
    assert automatic.every_hours() == 12
    assert automatic.keep() == 3


# ── it refuses rather than degrades ─────────────────────────────────────────

def test_on_but_not_set_up_says_to_back_up_once_first(home):
    """There is no keyring yet, so there is nothing to encrypt with."""
    automatic.set_settings(on=True)
    plan = automatic.plan()
    assert plan.enabled is True
    assert plan.possible is False
    assert plan.due is False
    assert "yourself first" in plan.reason
    assert automatic.run_if_due()["started"] is False


def test_a_keyring_without_a_passphrase_wrap_cannot_run_unattended(home):
    """An install upgraded from the older keyring shape.

    It can still *restore* with its recovery code, but it cannot write an
    archive the user's passphrase opens — so nothing is written at all. The
    alternative is archives only a recovery code opens, from a user who
    believes their passphrase works.
    """
    keys, _code = identity.mint("pw")
    identity.remember(identity.Keyring(
        master_key=keys.master_key, salt=b"",
        wraps={"recovery": keys.wraps["recovery"]}))
    automatic.set_settings(on=True)

    assert identity.is_set_up() is True
    assert identity.can_run_unattended() is False
    plan = automatic.plan()
    assert plan.possible is False
    assert "Back up once with your passphrase" in plan.reason
    assert automatic.run_if_due()["started"] is False


# ── the thing it exists for ─────────────────────────────────────────────────

def test_it_runs_with_no_secret_and_the_passphrase_still_opens_it(home, tmp_path):
    """The whole design, in one test.

    The scheduler holds no passphrase. The wraps were minted from one at setup
    and are reused, so the archive it writes is openable by the passphrase the
    user chose — which never existed on disk.
    """
    code = _set_up("the user's passphrase")
    automatic.set_settings(on=True)

    outcome = automatic.run_if_due()
    assert outcome["started"] is True, outcome
    _wait_idle()

    written = Path(outcome["path"])
    assert written.exists()
    assert written.parent == home / automatic.FOLDER

    seen = reader.inspect(written)
    assert seen.unlock_methods == ["passphrase", "recovery"]

    # Both doors work on a backup nobody was asked about.
    reader.restore(written, tmp_path / "by-phrase",
                   passphrase="the user's passphrase")
    reader.restore(written, tmp_path / "by-code", recovery_code=code)
    assert (tmp_path / "by-phrase" / "chitragupta.db").exists()


def test_it_does_not_run_again_until_it_is_due(home):
    _set_up()
    automatic.set_settings(on=True, hours=24)

    assert automatic.run_if_due()["started"] is True
    _wait_idle()
    assert automatic.plan().due is False
    assert automatic.run_if_due()["started"] is False

    later = time.time() + 24 * 3600 + 1
    assert automatic.plan(now=later).due is True


def test_it_waits_rather_than_racing_another_job(home):
    """Two operations over the same eleven databases would snapshot a home
    halfway through being replaced."""
    _set_up()
    automatic.set_settings(on=True)
    job.reset_for_tests()
    job._claim(job.BACKUP)          # hold the slot
    try:
        outcome = automatic.run_if_due()
        assert outcome["started"] is False
        assert "already running" in outcome["reason"]
        # The slot was not consumed, so the next tick tries again.
        assert automatic.plan().due is True
    finally:
        job.reset_for_tests()


def test_a_backup_that_cannot_start_does_not_mark_itself_done(home, monkeypatch):
    """`last_at` must not move for a run that never happened, or the next
    attempt is a day away."""
    _set_up()
    automatic.set_settings(on=True)

    def refuse(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(job, "start_backup", refuse)
    outcome = automatic.run_if_due()
    assert outcome["started"] is False
    assert "disk full" in outcome.get("error", "")
    assert automatic.plan().due is True, "a failed start consumed the slot"


# ── retention ───────────────────────────────────────────────────────────────

def _plant(home: Path, name: str, age_seconds: float = 0) -> Path:
    folder = home / automatic.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    found = folder / name
    found.write_bytes(b"not a real archive")
    if age_seconds:
        import os
        when = time.time() - age_seconds
        os.utime(found, (when, when))
    return found


def test_pruning_keeps_the_newest_and_drops_the_rest(home):
    for index in range(5):
        _plant(home, f"chitragupta-2026010{index}-000000.cgarch",
               age_seconds=(5 - index) * 3600)

    removed = automatic.prune(keep_count=2)
    assert len(removed) == 3
    left = sorted(p.name for p in (home / automatic.FOLDER).glob("*.cgarch"))
    assert len(left) == 2
    assert "chitragupta-20260104-000000.cgarch" in left, "the newest must survive"


def test_pruning_never_touches_a_file_this_feature_did_not_write(home):
    """A folder the user chose is not ours to tidy.

    A backup they renamed to mean "keep this" is the case that matters, and it
    is also how somebody's unrelated file ends up deleted.
    """
    keep_me = _plant(home, "my-important-backup.cgarch", age_seconds=99999)
    holiday = _plant(home, "holiday.jpg", age_seconds=99999)
    for index in range(4):
        _plant(home, f"chitragupta-2026010{index}-000000.cgarch",
               age_seconds=(4 - index) * 3600)

    automatic.prune(keep_count=1)
    assert keep_me.exists(), "a renamed backup was deleted"
    assert holiday.exists(), "an unrelated file was deleted"
    assert len(list((home / automatic.FOLDER).glob("chitragupta-*.cgarch"))) == 1


def test_keeping_zero_means_keeping_everything(home):
    """The honest answer for somebody who would rather spend the disk."""
    for index in range(3):
        _plant(home, f"chitragupta-2026010{index}-000000.cgarch")
    assert automatic.prune(keep_count=0) == []
    assert len(list((home / automatic.FOLDER).glob("*.cgarch"))) == 3


def test_pruning_an_empty_folder_is_harmless(home):
    assert automatic.prune(keep_count=2) == []


# ── the endpoints ───────────────────────────────────────────────────────────

def test_the_plan_is_readable_before_anything_is_set_up(client):
    body = client.get("/api/backup/automatic").json()
    assert body["enabled"] is False
    assert body["possible"] is False
    assert body["reason"]
    assert body["every_hours"] >= 1


def test_the_settings_round_trip(client):
    body = client.post("/api/backup/automatic",
                       json={"enabled": True, "every_hours": 12,
                             "keep": 4}).json()
    assert body["enabled"] is True
    assert body["every_hours"] == 12
    assert body["keep"] == 4
    assert client.get("/api/backup/automatic").json()["enabled"] is True


def test_an_absurd_interval_is_refused_by_the_schema(client):
    assert client.post("/api/backup/automatic",
                       json={"every_hours": 0}).status_code == 422
    assert client.post("/api/backup/automatic",
                       json={"keep": -1}).status_code == 422


def test_the_backup_state_endpoint_carries_the_plan(client):
    body = client.get("/api/backup/state").json()
    assert "automatic" in body
    assert "enabled" in body["automatic"]
    assert "reason" in body["automatic"]


def test_prune_is_reachable_and_reports_what_went(client, home):
    for index in range(3):
        _plant(home, f"chitragupta-2026010{index}-000000.cgarch",
               age_seconds=(3 - index) * 3600)
    client.post("/api/backup/automatic", json={"keep": 1})
    body = client.post("/api/backup/prune").json()
    assert len(body["removed"]) == 2


def test_a_first_backup_through_the_api_still_needs_a_passphrase(client):
    """The keyring has to come from somewhere, and only the user has it."""
    refused = client.post("/api/backup/start", json={})
    assert refused.status_code == 400
    assert "passphrase" in refused.json()["detail"]
