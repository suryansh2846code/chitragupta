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

