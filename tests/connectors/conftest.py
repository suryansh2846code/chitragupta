"""Isolation and fake-transport primitives for the connector suite.

Connectors are the layer that talks to the outside world, and until now the
suite has not executed a single `sync()` — the only connector code it touched
were five pure helpers. Every meaningful connector fix in `JOURNEY.md` (HTML
noise, `.docx`, shared files, duplicates, date parsing) was therefore found by a
user, not by a test.

These fixtures exist so a connector can be run end-to-end — its real `sync()`,
its real parsing, its real writes into a real brain — with only the *transport*
replaced. Nothing here mocks the connector itself: if a fake returns three
records, the assertion is about what the connector genuinely did with them.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """A brand-new Chitragupta home, and therefore a brand-new brain, per test.

    The session-wide `CHITRAGUPTA_HOME` in the root conftest keeps tests off the
    developer's machine; it does not keep them off *each other*. Memory counts
    are the assertion in most of these tests, so a brain carried between them
    would make every count depend on collection order.

    Three `lru_cache`s stand between the environment variable and the database
    file, and clearing only the first is worse than clearing none: the settings
    then report the new home while `get_store()` hands back a `MemoryStore` still
    holding an open connection to the previous test's file. The failure is
    silent and misleading — the second connector to ingest the same fixture sees
    every record as an already-stored duplicate and reports zero added, which
    reads as "this connector is broken".

    **`connectors.db` is a fourth handle and behaves identically.** It holds the
    resource-identity table, so a stale one makes every fixture record read as
    UNCHANGED — the same silent zero-added, arriving by a different route. It is
    a module global rather than an `lru_cache`, which is why it is reset
    separately rather than added to the tuple.
    """
    from chitragupta.brain.brain import get_brain
    from chitragupta.config import get_settings
    from chitragupta.connectors import db as connector_db
    from chitragupta.connectors import limits
    from chitragupta.core.store import get_store

    caches = (get_settings, get_store, get_brain)
    connector_db.reset_for_tests()
    # Gates are per connector and live for the process, which is correct at
    # runtime and wrong here: a budget spent by one test would throttle the
    # next, and a 429 injected by a failure-injection test would hold every
    # later test back for a window.
    limits.reset()

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(home))
    for cache in caches:
        cache.cache_clear()
    # Assert rather than assume: this is the check that would have caught the
    # two caches missing above, instead of eleven connectors looking broken.
    assert get_settings().home == home, "CHITRAGUPTA_HOME did not take"
    assert get_store().db_path.parent == home, "the store is on the wrong brain"
    yield home
    connector_db.reset_for_tests()
    limits.reset()
    for cache in caches:
        cache.cache_clear()


@pytest.fixture
def fake_module(monkeypatch):
    """Install a module into `sys.modules` for the duration of a test.

    Connectors import their SDKs lazily *inside* `sync()` (so the app starts
    with nothing installed — CLAUDE.md, "assume nothing is installed"), which
    means the import cannot be patched at module scope. Putting a stand-in in
    `sys.modules` is what that import then finds.
    """
    def install(name: str, **attrs) -> types.ModuleType:
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)
        # `from a.b import c` needs `a` to exist and to carry `b`.
        if "." in name:
            parent_name, _, child = name.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is None:
                parent = types.ModuleType(parent_name)
                monkeypatch.setitem(sys.modules, parent_name, parent)
            monkeypatch.setattr(parent, child, mod, raising=False)
        return mod

    return install


class FailOnNth:
    """Make the Nth ingest call raise, so one bad item can be simulated.

    Crash isolation is decision H2 — "one bad item never aborts a sync" — and
    the only honest way to test it is to break one item and check the others
    still land. Patching the *ingest seam* rather than contorting a fixture
    means the same probe works for every connector regardless of whether it
    writes through `Brain.ingest` or `MemoryStore.add`.
    """

    def __init__(self, nth: int = 2) -> None:
        self.nth = nth
        self.calls = 0
        self.raised = False

    def wrap(self, real):
        def wrapper(*args, **kwargs):
            self.calls += 1
            if self.calls == self.nth:
                self.raised = True
                raise RuntimeError("poisoned item")
            return real(*args, **kwargs)

        return wrapper


@pytest.fixture
def poison(monkeypatch):
    """Break exactly one ingested item, whichever seam the connector uses."""
    def install(nth: int = 2) -> FailOnNth:
        from chitragupta.brain.brain import Brain
        from chitragupta.core.store import MemoryStore

        breaker = FailOnNth(nth)
        monkeypatch.setattr(Brain, "ingest", breaker.wrap(Brain.ingest))
        monkeypatch.setattr(MemoryStore, "add", breaker.wrap(MemoryStore.add))
        return breaker

    return install


def write_emlx(path: Path, *, sender: str, subject: str, body: str) -> Path:
    """An Apple Mail `.emlx` file: byte-length line, RFC822 message, plist."""
    message = (f"From: {sender}\r\nSubject: {subject}\r\n"
               f"Date: Mon, 1 Sep 2026 10:00:00 +0000\r\n"
               f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}")
    raw = message.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"{len(raw)}\n".encode() + raw
                     + b"<?xml version=\"1.0\"?><plist><dict></dict></plist>")
    return path
