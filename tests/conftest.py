"""Pin the test environment BEFORE any chitragupta module (and thus `.env`) loads.

Tests must be fully offline and deterministic. Individual test modules used
`os.environ.setdefault(...)`, which silently loses to a developer's `.env`
(e.g. CHITRAGUPTA_MODEL_PROVIDER=claude-code, EMBEDDING_PROVIDER=local) as soon
as another module imports `chitragupta.config` first — making results depend on
collection order and hitting a real model. conftest.py is imported before every
test module, so setting the vars here is authoritative.
"""
import os
import tempfile

os.environ["CHITRAGUPTA_MODEL_PROVIDER"] = "mock"
os.environ["CHITRAGUPTA_MODEL_NAME"] = "mock-1"
os.environ["CHITRAGUPTA_EMBEDDING_PROVIDER"] = "hash"
os.environ.setdefault("CHITRAGUPTA_HOME", tempfile.mkdtemp(prefix="chitragupta-tests-"))


# ── every directory a test makes, removed when the run ends ──────────────────
#
# Ten places call `tempfile.mkdtemp()` and none of them removed anything. Most
# are once per session and harmless; `automation_harness.fresh_store()` is once
# per test, and a Chitragupta home is ~560 MB by the time a brain, an agents
# database and an action log are in it.
#
# That filled a developer's disk: **29,474 directories, 64 GB**, in one
# afternoon of running the suite. The machine hit zero bytes free, which is the
# kind of failure that stops the whole computer rather than the test run — and
# nothing in the output said the suite had done it.
#
# Wrapped here rather than fixed at the ten call sites, because the eleventh is
# the one that matters: a test written next month calls `mkdtemp` and is
# cleaned up without its author knowing there was a rule.
#
# Set `CHITRAGUPTA_KEEP_TEST_DIRS=1` to keep them, for the afternoon you are
# reading a database a failing test left behind.
_MADE: list[str] = []
_REAL_MKDTEMP = tempfile.mkdtemp


def _tracked_mkdtemp(*args, **kwargs):
    path = _REAL_MKDTEMP(*args, **kwargs)
    # pytest's own `tmp_path` goes through here too, and pytest already keeps
    # only the last three runs. Removing those would take away the artifacts a
    # developer inspects after a failure, which is the one case this must not
    # make worse.
    if "pytest-of-" not in path:
        _MADE.append(path)
    return path


tempfile.mkdtemp = _tracked_mkdtemp


import pytest
from starlette.testclient import TestClient

# `TestClient` addresses the app as `http://testserver`, and the origin guard
# (api/security.py) refuses any Host that is not this machine — rightly, since
# that is what stops a DNS-rebinding page from reaching the API. Rather than
# teach the guard a hostname that exists only in tests — a production allowance
# bought for a test's convenience — the client is pointed at loopback, which is
# where it is pretending to be anyway.
#
# Applied at import, not in a fixture: two modules build a client at module
# scope, which runs before any fixture does.
_real_test_client_init = TestClient.__init__


def _from_loopback(self, app, *args, **kwargs):
    kwargs.setdefault("base_url", "http://127.0.0.1")
    return _real_test_client_init(self, app, *args, **kwargs)


TestClient.__init__ = _from_loopback


@pytest.fixture(autouse=True, scope="session")
def _leave_no_directories_behind():
    """Remove what the run created, however it ended.

    At the end rather than per test: a directory is often handed to a store
    that stays open for the session, and removing it while something still
    holds a connection to a file inside it is a different bug.

    Failures are ignored on purpose. A directory that cannot be removed is
    disk somebody has to clear by hand later; a teardown that *raises* turns a
    green run red for a reason that has nothing to do with the code.
    """
    yield
    if os.environ.get("CHITRAGUPTA_KEEP_TEST_DIRS"):
        print(f"\nkept {len(_MADE)} test directories "
              "(CHITRAGUPTA_KEEP_TEST_DIRS is set)")
        return
    import shutil

    for path in _MADE:
        shutil.rmtree(path, ignore_errors=True)
    # The session home, which was made before any fixture could record it.
    home = os.environ.get("CHITRAGUPTA_HOME", "")
    if "chitragupta-tests-" in home:
        shutil.rmtree(home, ignore_errors=True)


@pytest.fixture(autouse=True, scope="session")
def _never_touch_the_real_keychain():
    """`set_secret` writes to the macOS login Keychain, which CHITRAGUPTA_HOME does
    NOT isolate — so any test saving an API key would leave a real entry behind
    (and could clobber the developer's own). Force the file-backed path, which
    lives inside the temporary CHITRAGUPTA_HOME above.
    """
    from chitragupta.config import Settings

    original = Settings._keychain_ok
    Settings._keychain_ok = lambda self: False
    try:
        yield
    finally:
        Settings._keychain_ok = original


# Attempts are recorded rather than merely blocked, so the guard itself can be
# tested — a guard nobody exercises is the kind that quietly stops working.
spawned_logins: list[list[str]] = []


@pytest.fixture(autouse=True, scope="session")
def _never_start_a_real_vendor_login():
    """A vendor CLI's `login` opens a browser and then waits forever.

    `flow.start()` and POST /api/providers/{name}/signin reach `claude auth
    login` for real, and nothing reaps it: one was left running by every single
    pytest run, and 158 were found alive on the developer's machine, each
    holding the OAuth callback port until sign-in stopped working.

    The argv is swapped for a command that exits immediately rather than the
    call being blocked, so the flows still run their real code — only the vendor
    binary is kept out of it.
    """
    import subprocess

    real = subprocess.Popen

    class _Guard:
        """Stands in for the class, not just the call: `subprocess.Popen[str]`
        appears in annotations that are evaluated at runtime, so a plain
        function here breaks unrelated code."""

        def __call__(self, cmd, *args, **kwargs):
            argv = [str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd])]
            if "login" in argv:
                spawned_logins.append(argv)
                cmd = ["true"]
            return real(cmd, *args, **kwargs)

        def __getitem__(self, item):
            return real[item]

        def __instancecheck__(self, obj):
            return isinstance(obj, real)

        def __getattr__(self, name):
            return getattr(real, name)

    subprocess.Popen = _Guard()
    try:
        yield
    finally:
        subprocess.Popen = real


@pytest.fixture(autouse=True)
def _no_stale_cli_auth_cache():
    """CLI sign-in state is cached for a few seconds so polling doesn't spawn a
    subprocess per tick. That cache must not leak between tests.

    `cache.clear_all()` covers the `@ttl_cached` account detectors in
    `accounts.py` as well. Without it, a test that ran `detect_claude_account`
    for real left the answer sitting in a four-second cache, and the next test
    — which had carefully stubbed the CLI away — got the *developer's own*
    account back instead. It passed or failed depending on which tests ran
    before it and on whose machine, which is the worst kind of red: two tests
    here only failed under `-k` selection, and had done for some time.
    """
    from chitragupta.models import cache, cursor, grok_cli

    def _flush():
        for mod in (cursor, grok_cli):
            mod.reset_auth_cache()
            mod.reset_login_state()
        cache.clear_all()

    _flush()
    yield
    _flush()


@pytest.fixture(autouse=True)
def _no_leaked_credentials():
    """A credential one test writes must not still be there for the next one.

    `CHITRAGUPTA_HOME` is session-scoped — one temporary directory for the whole
    run — so an API key saved through `settings.set_secret` and a row written to
    `provider_connections` both outlive the test that made them. That is what
    made **A13** in `docs/AUDIT.md`: `pytest -k "docs or claude"` failed on
    `test_connecting_claude_code_does_not_connect_the_api_provider` while the
    full run passed, because an earlier test in that selection had saved
    `ANTHROPIC_API_KEY=sk-test-key`, and `claude` reads as connected whenever a
    key is stored and its row does not say DISCONNECTED. The file's own fixture
    resets the rows and cannot know about the key.

    The failure was in the **unsafe** direction — a provider reading as
    connected when nobody connected it — over an assertion that is a stated
    invariant, and it only appeared under an unusual invocation, so CI would
    never have shown it.

    Snapshot and restore rather than wipe: a session-scoped fixture that
    deliberately set something up keeps it, and a test that writes a credential
    still sees its own write. Only `secrets.json` and the one table are touched
    — `agents.db` also holds permissions, approvals, avatars and agent rows,
    and resetting the whole file would break fixtures that have nothing to do
    with credentials.
    """
    from chitragupta.config import forget_cached_secrets, get_settings

    def _connections():
        from chitragupta.models import connections
        db = connections._get_db()
        try:
            return [tuple(r) for r in db.execute(
                "SELECT * FROM provider_connections")], [
                d[0] for d in db.execute(
                    "SELECT * FROM provider_connections").description]
        finally:
            db.close()

    def _restore(rows, columns):
        from chitragupta.models import connections
        db = connections._get_db()
        try:
            db.execute("DELETE FROM provider_connections")
            if rows:
                marks = ",".join("?" * len(columns))
                db.executemany(
                    f"INSERT INTO provider_connections VALUES ({marks})", rows)
            db.commit()
        finally:
            db.close()

    secrets = get_settings()._secrets_path()
    before = secrets.read_bytes() if secrets.exists() else None
    rows, columns = _connections()

    yield

    after = secrets.read_bytes() if secrets.exists() else None
    if after != before:
        if before is None:
            secrets.unlink(missing_ok=True)
        else:
            secrets.write_bytes(before)
        forget_cached_secrets()
    now, _ = _connections()
    if now != rows:
        _restore(rows, columns)
    if after != before or now != rows:
        from chitragupta.models.registry import clear_provider_cache
        clear_provider_cache()
