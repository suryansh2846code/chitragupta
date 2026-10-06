"""Backup and restore — the invariants, not the instances.

The test that matters most is `test_no_tier0_byte_reaches_the_archive`: it is
parametrised over every Tier 0 item, so a credential file declared later is
covered without anyone remembering to add a case. The rest of the file follows
the same rule — "a failed card has no button" is worth more than "the
automation card has no button".
"""
from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from chitragupta.archive import crypto, reader, writer
from chitragupta.core import exclusions

#: A recognisable secret per Tier 0 entry. The value is what the leak test
#: hunts for in the finished archive.
TIER0_CONTENT = {
    "secrets.json": '{"NOTION_TOKEN": "ntn_LEAKCANARY_secrets"}',
    "google_token.json": '{"refresh_token": "LEAKCANARY_google_refresh"}',
    "google_client_secret.json": '{"client_secret": "LEAKCANARY_client"}',
    "google_account.json": '{"email": "LEAKCANARY_account@example.com"}',
    "chatgpt_token.json": '{"access_token": "LEAKCANARY_chatgpt"}',
    "xai_token.json": '{"access_token": "LEAKCANARY_xai"}',
}

#: Tier 0 directories, with a file inside each.
TIER0_DIRS = {
    "telegram": ("session.session", "LEAKCANARY_telegram_mtproto"),
    "browser": ("Cookies", "LEAKCANARY_browser_cookie"),
}


def _make_db(path: Path, table: str, value: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(f"CREATE TABLE {table} (v TEXT)")
        con.execute(f"INSERT INTO {table} VALUES (?)", (value,))
        con.commit()
    finally:
        con.close()


@pytest.fixture
def populated_home(tmp_path: Path) -> Path:
    """A home with one of everything: databases, Tier 0, derived, unknown."""
    home = tmp_path / "home"
    home.mkdir()

    for name in sorted(exclusions.KNOWN_DATABASES):
        _make_db(home / name, "t", f"row-in-{name}")

    for name, content in TIER0_CONTENT.items():
        (home / name).write_text(content)
    for name, (child, content) in TIER0_DIRS.items():
        (home / name).mkdir()
        (home / name / child).write_text(content)

    # derived — must be skipped, not refused
    (home / ".port").write_text("8787")
    (home / "logs").mkdir()
    (home / "logs" / "app.log").write_text("some log line")
    (home / "brain-export").mkdir()
    (home / "brain-export" / "about-you.md").write_text("# About You")

    # archivable configuration and user content
    (home / "mcp_servers.json").write_text(json.dumps({
        "acme": {"id": "acme", "url": "https://mcp.example.com",
                 "env": {"ACME_TOKEN": "LEAKCANARY_mcp_inline_token"}},
    }))
    (home / "agents").mkdir()
    (home / "agents" / "notes.md").write_text("an agent profile file")

    # an unrecognised file — the fail-closed case
    (home / "something-new.json").write_text("LEAKCANARY_unknown_file")
    return home


# ── the rule about what may leave ───────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(exclusions.NEVER_ARCHIVE))
def test_tier0_is_refused_by_name(name: str) -> None:
    verdict = exclusions.classify(name, is_dir=name in TIER0_DIRS)
    assert verdict.refused, f"{name} must never be archived"
    assert verdict.reason, "a refusal must say why, for the reconnect checklist"


@pytest.mark.parametrize("name", sorted(exclusions.KNOWN_DATABASES))
def test_every_known_database_is_archived(name: str) -> None:
    """Pins the set. A database added later is archived by pattern anyway; this
    fails if one is ever *excluded*, which is how 1-of-44 happened."""
    assert exclusions.classify(name).archived


def test_an_unknown_database_is_archived_without_anyone_declaring_it() -> None:
    assert exclusions.classify("something_new.db").archived


def test_unknown_files_and_directories_fail_closed() -> None:
    assert not exclusions.classify("mystery.json").archived
    assert not exclusions.classify("mystery", is_dir=True).archived


def test_tier0_wins_over_the_database_pattern(monkeypatch) -> None:
    """Order, not coincidence: a Tier 0 name ending in .db must still refuse.

    `monkeypatch.setitem` rather than save-mutate-restore. The first version of
    this test did the latter and bound `original` to the live dict instead of a
    copy, so its own `clear()` emptied the Tier 0 list for every test that ran
    afterwards — and the leak tests below went on passing, because fail-closed
    caught the files anyway. A test that quietly disarms the suite's most
    important assertion is worse than no test.
    """
    monkeypatch.setitem(exclusions.NEVER_ARCHIVE, "leaky.db",
                        "a credential that happens to look like a database")
    assert exclusions.classify("leaky.db").refused


# ── the leak test ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("canary", sorted(
    [v.split("LEAKCANARY_")[1].split('"')[0] for v in TIER0_CONTENT.values()]
    + [c.split("LEAKCANARY_")[1] for _, c in TIER0_DIRS.values()]
))
def test_no_tier0_byte_reaches_the_archive(populated_home: Path, tmp_path: Path,
                                           canary: str) -> None:
    """Not one byte of a Tier 0 secret may appear in a finished archive.

    Searched over the **encrypted** file as well as the staged payload, because
    the thing being asserted is "it never got in", not "it got in and was
    encrypted".
    """
    needle = f"LEAKCANARY_{canary}".encode()
    # A leak test that passes because the secret was never there is worthless.
    # Prove the canary is in the home before asserting it is not in the output.
    on_disk = any(needle in p.read_bytes()
                  for p in populated_home.rglob("*") if p.is_file())
    assert on_disk, f"fixture did not plant {canary} — the test would be vacuous"

    out = tmp_path / "backup.cgarch"
    packed = writer.pack(populated_home, out, passphrase="a strong passphrase")
    assert packed.withheld, "Tier 0 was present, so something must be withheld"
    assert needle not in out.read_bytes()


def test_inline_mcp_tokens_are_stripped_but_config_survives(
        populated_home: Path, tmp_path: Path) -> None:
    """The legacy shape kept MCP tokens inline. The token must go; the server
    the user configured must not."""
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")
    assert b"LEAKCANARY_mcp_inline_token" not in out.read_bytes()

    home2 = tmp_path / "restored"
    reader.restore(out, home2, passphrase="pw")
    spec = json.loads((home2 / "mcp_servers.json").read_text())
    assert spec["acme"]["url"] == "https://mcp.example.com"
    assert "env" not in spec["acme"]


def test_unrecognised_files_are_not_archived(populated_home: Path,
                                             tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")
    assert b"LEAKCANARY_unknown_file" not in out.read_bytes()


# ── round trip ──────────────────────────────────────────────────────────────

def test_every_database_round_trips(populated_home: Path, tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")

    home2 = tmp_path / "restored"
    result = reader.restore(out, home2, passphrase="pw")

    for name in sorted(exclusions.KNOWN_DATABASES):
        assert (home2 / name).exists(), f"{name} did not come back"
        con = sqlite3.connect(home2 / name)
        try:
            assert con.execute("SELECT v FROM t").fetchone()[0] == f"row-in-{name}"
        finally:
            con.close()
    assert (home2 / "agents" / "notes.md").read_text() == "an agent profile file"
    assert result["needs_migrations"] and result["needs_reembed"]


def test_derived_and_tier0_do_not_come_back(populated_home: Path,
                                            tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")
    home2 = tmp_path / "restored"
    reader.restore(out, home2, passphrase="pw")

    for name in (*exclusions.NEVER_ARCHIVE, *exclusions.DERIVED):
        assert not (home2 / name).exists(), f"{name} should not have been restored"


def test_restore_reports_what_must_be_reconnected(populated_home: Path,
                                                  tmp_path: Path) -> None:
    """Tier 0 never travels, so the user must be told what to re-authorise."""
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")

    seen = reader.inspect(out)
    assert "google_token.json" in seen.reconnect_needed
    assert "telegram" in seen.reconnect_needed
    assert seen.withheld["telegram"]

    result = reader.restore(out, tmp_path / "restored", passphrase="pw")
    assert result["reconnect_needed"] == seen.reconnect_needed


def test_inspect_needs_no_secret(populated_home: Path, tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    packed = writer.pack(populated_home, out, passphrase="pw")
    seen = reader.inspect(out)
    assert seen.unlock_methods == [crypto.BY_PASSPHRASE, crypto.BY_RECOVERY]
    assert {m["name"] for m in seen.manifest} == {m["name"] for m in packed.manifest}
    assert seen.created_at


# ── the two doors ───────────────────────────────────────────────────────────

def test_recovery_code_opens_what_the_passphrase_opens(populated_home: Path,
                                                       tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    packed = writer.pack(populated_home, out, passphrase="pw")
    assert packed.recovery_code

    home2 = tmp_path / "by-code"
    reader.restore(out, home2, recovery_code=packed.recovery_code)
    assert (home2 / "chitragupta.db").exists()


def test_recovery_code_survives_how_a_person_types_it(populated_home: Path,
                                                      tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    packed = writer.pack(populated_home, out, passphrase="pw")
    typed = packed.recovery_code.lower().replace("-", " ")
    reader.restore(out, tmp_path / "typed", recovery_code=typed)


def test_wrong_secret_is_refused_and_changes_nothing(populated_home: Path,
                                                     tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")

    target = tmp_path / "untouched"
    target.mkdir()
    (target / "chitragupta.db").write_text("the brain they still have")

    with pytest.raises(crypto.WrongSecretError):
        reader.restore(out, target, passphrase="not the passphrase")
    assert (target / "chitragupta.db").read_text() == "the brain they still have"


def test_no_secret_at_all_is_refused(populated_home: Path, tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")
    with pytest.raises(crypto.WrongSecretError):
        reader.restore(out, tmp_path / "nope")


# ── damage ──────────────────────────────────────────────────────────────────

def test_a_truncated_backup_refuses_rather_than_half_restoring(
        populated_home: Path, tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")
    blob = out.read_bytes()
    out.write_bytes(blob[: len(blob) // 2])

    target = tmp_path / "half"
    with pytest.raises(crypto.CorruptArchiveError):
        reader.restore(out, target, passphrase="pw")
    assert not target.exists() or not list(target.iterdir())


def test_a_modified_backup_is_refused(populated_home: Path, tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")
    blob = bytearray(out.read_bytes())
    blob[-100] ^= 0x01
    out.write_bytes(bytes(blob))
    with pytest.raises(crypto.CorruptArchiveError):
        reader.restore(out, tmp_path / "bad", passphrase="pw")


def test_a_file_that_is_not_a_backup_says_so(tmp_path: Path) -> None:
    junk = tmp_path / "holiday.jpg"
    junk.write_bytes(b"\xff\xd8\xff\xe0 not a backup at all")
    with pytest.raises(crypto.CorruptArchiveError, match="not a Chitragupta backup"):
        reader.inspect(junk)


def test_an_interrupted_backup_leaves_no_file_to_restore_from(
        populated_home: Path, tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "backup.cgarch"

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(crypto, "seal", boom)
    with pytest.raises(OSError):
        writer.pack(populated_home, out, passphrase="pw")
    assert not out.exists()
    assert not list(tmp_path.glob("*.partial"))


# ── restoring onto a home that already has something in it ──────────────────

def test_existing_files_are_moved_aside_not_destroyed(populated_home: Path,
                                                      tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "chitragupta.db").write_text("PRECIOUS")

    result = reader.restore(out, target, passphrase="pw")
    assert "chitragupta.db" in result["replaced"]
    aside = list(target.glob("chitragupta.db.replaced-*"))
    assert len(aside) == 1
    assert aside[0].read_text() == "PRECIOUS"


def test_replace_existing_false_skips_instead(populated_home: Path,
                                              tmp_path: Path) -> None:
    out = tmp_path / "backup.cgarch"
    writer.pack(populated_home, out, passphrase="pw")

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "chitragupta.db").write_text("PRECIOUS")

    result = reader.restore(out, target, passphrase="pw", replace_existing=False)
    assert "chitragupta.db" in result["skipped"]
    assert (target / "chitragupta.db").read_text() == "PRECIOUS"


# ── a hostile archive ───────────────────────────────────────────────────────

def _rebuild_with_payload(src: Path, dest: Path, payload: Path,
                          passphrase: str) -> None:
    """Re-seal `payload` under a fresh key, as a hostile sender would."""
    import hashlib

    mk, dek = crypto.new_key(), crypto.new_key()
    salt = crypto.new_salt()
    data = payload.read_bytes()
    header = crypto.Header(
        salt=crypto._b64(salt),
        wrapped_dek=crypto.wrap(mk, dek, crypto.WRAP_DEK),
        wrapped_mk={crypto.BY_PASSPHRASE: crypto.wrap(
            crypto.derive_from_passphrase(passphrase, salt), mk,
            crypto.WRAP_PASSPHRASE)},
        total_chunks=max(1, -(-len(data) // crypto.CHUNK_SIZE)),
        plaintext_sha256=hashlib.sha256(data).hexdigest(),
        plaintext_bytes=len(data),
    )

    with dest.open("wb") as sink:
        crypto.seal(io.BytesIO(data), sink, header, dek)


def test_a_backup_cannot_write_outside_the_home(tmp_path: Path) -> None:
    """Path traversal in a tar member is refused, on every Python 3.11."""
    staging = tmp_path / "evil"
    (staging / writer.PAYLOAD_DIR).mkdir(parents=True)
    (staging / writer.PAYLOAD_DIR / "ok.db").write_text("fine")
    tarball = tmp_path / "evil.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(staging / writer.PAYLOAD_DIR, arcname=writer.PAYLOAD_DIR)
        info = tarfile.TarInfo(f"{writer.PAYLOAD_DIR}/../../escaped.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"oops"))

    archive = tmp_path / "evil.cgarch"
    _rebuild_with_payload(tarball, archive, tarball, "pw")
    with pytest.raises(crypto.CorruptArchiveError, match="unsafe path"):
        reader.restore(archive, tmp_path / "home", passphrase="pw")
    assert not (tmp_path / "escaped.txt").exists()


def test_a_backup_cannot_smuggle_a_symlink(tmp_path: Path) -> None:
    staging = tmp_path / "evil2"
    (staging / writer.PAYLOAD_DIR).mkdir(parents=True)
    tarball = tmp_path / "evil2.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(staging / writer.PAYLOAD_DIR, arcname=writer.PAYLOAD_DIR)
        info = tarfile.TarInfo(f"{writer.PAYLOAD_DIR}/secrets.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)

    archive = tmp_path / "evil2.cgarch"
    _rebuild_with_payload(tarball, archive, tarball, "pw")
    with pytest.raises(crypto.CorruptArchiveError, match="not a file"):
        reader.restore(archive, tmp_path / "home2", passphrase="pw")


def test_a_recovery_wrap_without_its_master_key_is_refused(populated_home: Path,
                                                           tmp_path: Path) -> None:
    """The pair is only meaningful together.

    A wrap made against a different master key unwraps to nothing, so an
    archive built from a mismatched pair would advertise a `recovery` unlock
    method that could never work — which is the bug this guard replaced, in a
    quieter form.
    """
    with pytest.raises(ValueError, match="both or neither"):
        writer.pack(populated_home, tmp_path / "x.cgarch", passphrase="pw",
                    recovery_wrap="not-paired-with-anything")


def test_a_reused_wrap_reports_no_new_code(populated_home: Path,
                                           tmp_path: Path) -> None:
    """`Packed.recovery_code` is how the caller knows whether to show one."""
    first = writer.pack(populated_home, tmp_path / "a.cgarch", passphrase="pw")
    assert first.recovery_code

    with (tmp_path / "a.cgarch").open("rb") as src:
        header, _, _ = crypto.read_header(src)
    mk = reader.master_key_from(header, "pw", None)

    second = writer.pack(populated_home, tmp_path / "b.cgarch", passphrase="pw",
                         master_key=mk,
                         recovery_wrap=header.wrapped_mk[crypto.BY_RECOVERY])
    assert second.recovery_code is None
    # and the original code still opens the new file
    reader.restore(tmp_path / "b.cgarch", tmp_path / "out",
                   recovery_code=first.recovery_code)


# ── SQLite's sidecars ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["chitragupta.db-wal", "chitragupta.db-shm",
                                  "agents.db-journal"])
def test_sidecars_are_never_archived(name: str) -> None:
    """A write-ahead log in an archive is the same bug, delivered."""
    verdict = exclusions.classify(name)
    assert not verdict.archived
    assert exclusions.is_sidecar(name)


def test_a_restore_clears_a_stale_write_ahead_log(tmp_path: Path) -> None:
    """The one that made a successful restore do nothing at all.

    Every database runs in WAL mode (`core/db.py`), so a home that has been
    open has a `-wal` beside each file. Replace the main file and leave the old
    log, and SQLite applies the log on the next open: measured against the real
    server, a fresh read after a restore returned **the old rows** while the
    app reported success.

    Built with live connections on purpose. The rest of this file uses
    `tmp_path` fixtures that never open a database in WAL mode, which is
    exactly why they all passed while this was broken — `CLAUDE.md`: *verify
    against the real thing, not only the harness*.
    """
    home = tmp_path / "home"
    home.mkdir()
    live = home / "chitragupta.db"

    # A database with rows sitting in an un-checkpointed WAL.
    con = sqlite3.connect(live)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES ('OLD')")
    con.commit()
    assert (home / "chitragupta.db-wal").exists(), "fixture made no WAL"

    # Back up a DIFFERENT home, so the restore is distinguishable.
    source = tmp_path / "source"
    source.mkdir()
    _make_db(source / "chitragupta.db", "t", "RESTORED")
    out = tmp_path / "b.cgarch"
    writer.pack(source, out, passphrase="pw")

    reader.restore(out, home, passphrase="pw")
    con.close()

    assert not (home / "chitragupta.db-wal").exists(), \
        "a stale write-ahead log survived the restore"
    fresh = sqlite3.connect(home / "chitragupta.db")
    try:
        assert fresh.execute("SELECT v FROM t").fetchone()[0] == "RESTORED", \
            "the restore reported success and left the old data in place"
    finally:
        fresh.close()
