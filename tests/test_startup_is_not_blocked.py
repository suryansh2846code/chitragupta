"""Opening the app must not wait for the embedding model.

The local embedding model takes about ten seconds to load, and almost nothing
the workspace does on open needs it — listing connectors, counting memories,
reading the agent list. Building it inside `MemoryStore.__init__` meant every
one of those paid for it.

Measured on this machine, against the real server:

    /                       0.24s      ← the window painted
    /api/connectors        14.97s      ← and then nothing worked
    /api/brain/stats       16.08s

`stats()` was the sharpest version: it loaded the whole model to read the
*name* off it, so "how many memories do I have" — the call the header pill
polls — was the slowest endpoint in the app.

These tests hold the shape of the fix rather than a timing, because a
wall-clock assertion is a flaky test on a busy machine. What matters is *what
gets constructed*, and that is exact.
"""
from __future__ import annotations

import threading
import time

import pytest

import chitragupta.core.embeddings as embeddings
from chitragupta.core.store import MemoryStore


@pytest.fixture
def counting_embedder(monkeypatch):
    """Count model constructions without ever building a real one."""
    built = []

    class Fake:
        name = "fake"
        dim = 8

        def __init__(self) -> None:
            built.append(1)

        def embed_one(self, text):
            import numpy as np
            return np.zeros(self.dim, dtype="float32")

        def embed(self, texts):
            import numpy as np
            return np.zeros((len(texts), self.dim), dtype="float32")

        def embed_query(self, q):
            return self.embed_one(q)

    monkeypatch.setattr(embeddings, "get_embedder", Fake)
    monkeypatch.setattr("chitragupta.core.store.get_embedder", Fake)
    return built


def test_opening_a_store_does_not_build_the_model(tmp_path, counting_embedder):
    """The construction that was costing fifteen seconds of dead window."""
    MemoryStore(db_path=tmp_path / "s.db")

    assert counting_embedder == [], (
        "constructing a store built the embedding model — every endpoint that "
        "touches a store now pays ~10s")


def test_counting_memories_does_not_build_the_model(tmp_path, counting_embedder):
    store = MemoryStore(db_path=tmp_path / "s.db")

    store.count()

    assert counting_embedder == []


def test_reading_stats_does_not_build_the_model(tmp_path, counting_embedder):
    """`stats()` loaded the model to read a string off it, which made the
    endpoint the header pill polls the slowest one in the app."""
    store = MemoryStore(db_path=tmp_path / "s.db")

    out = store.stats()

    assert counting_embedder == []
    assert out["embedder"], "the name still has to be reported"


def test_the_embedder_name_is_reported_without_building_it(tmp_path,
                                                           counting_embedder):
    store = MemoryStore(db_path=tmp_path / "s.db")

    assert store.embedder_name()
    assert counting_embedder == []


def test_connector_state_does_not_build_the_model(tmp_path, counting_embedder):
    """What `/api/connectors` actually does — the 15-second endpoint."""
    store = MemoryStore(db_path=tmp_path / "s.db")

    store.set_connector_state("gmail", status="ok", detail="", last_sync="now")
    store.all_connector_state()

    assert counting_embedder == []


# ── it must still load when it is genuinely needed ─────────────────────────


def test_adding_a_memory_does_build_the_model(tmp_path, counting_embedder):
    """Laziness that never loads is just a broken store."""
    store = MemoryStore(db_path=tmp_path / "s.db")

    store.add("Dev's birthday is the 31st of August.", source="notes")

    assert counting_embedder == [1]


def test_the_model_is_built_once_per_store(tmp_path, counting_embedder):
    store = MemoryStore(db_path=tmp_path / "s.db")

    store.add("first memory about the launch", source="notes")
    store.add("second memory about the launch", source="notes")
    store.search("launch")

    assert counting_embedder == [1], "the lazy property rebuilt the model"


def test_an_injected_embedder_is_still_honoured(tmp_path, counting_embedder):
    """Tests and callers pass their own; laziness must not quietly ignore it."""
    class Given:
        name = "given"
        dim = 4

        def embed_one(self, t):
            import numpy as np
            return np.zeros(4, dtype="float32")

    store = MemoryStore(db_path=tmp_path / "s.db", embedder=Given())

    assert store.embedder_name() == "given"
    assert counting_embedder == []


# ── the background warm-up ─────────────────────────────────────────────────


class _Settings:
    """Only the field `warm_embedder` reads.

    Wrapping the real settings object recursed: the thing being faked is what
    `get_settings` returns, so a wrapper that calls it calls itself.
    """

    def __init__(self, provider: str) -> None:
        self.embedding_provider = provider


def _settings(provider: str) -> _Settings:
    return _Settings(provider)




def _join_warmup(timeout: float = 5.0) -> None:
    """Wait for the background warm-up `warm_embedder` starts.

    It is a daemon thread with a known name, so nothing in the app waits for
    it — correctly, since blocking on it is the bug the thread exists to
    avoid. A *test* that patches module attributes has to, though, or its
    patches are still in flight when the next test installs its own.
    """
    for thread in threading.enumerate():
        if thread.name == "embedder-warmup" and thread.is_alive():
            thread.join(timeout)


def test_warming_does_not_block_the_caller(monkeypatch):
    """It runs during app startup, so it must return immediately — otherwise
    the fix has only moved the fifteen seconds somewhere else."""
    monkeypatch.setattr("chitragupta.config.get_settings",
                        lambda: _settings("local"))
    slow = threading.Event()

    def never_finishes():
        slow.wait(5)
        return object()

    monkeypatch.setattr(embeddings, "get_embedder", never_finishes)

    started = time.perf_counter()
    embeddings.warm_embedder()
    elapsed = time.perf_counter() - started
    slow.set()

    assert elapsed < 1.0, f"warm_embedder blocked for {elapsed:.2f}s"

    # Join before leaving, or this test's thread outlives it. `warm_embedder`
    # spawns a daemon that resolves `get_embedder` from the module at CALL
    # time — so a thread slow to start calls whatever the *next* test patched
    # in, and `test_warming_is_skipped_for_the_offline_embedder` fails with a
    # recorded call it never made. That is a leak from here, not a bug there,
    # and it only ever showed up when test ordering shifted.
    _join_warmup()


def test_warming_is_skipped_for_the_offline_embedder(monkeypatch):
    """`hash` has nothing to load, so a thread for it is pure cost."""
    monkeypatch.setattr("chitragupta.config.get_settings", lambda: _settings("hash"))
    called = []
    monkeypatch.setattr(embeddings, "get_embedder", lambda: called.append(1))

    embeddings.warm_embedder()
    time.sleep(0.2)

    assert called == []


def test_a_failed_warm_up_does_not_take_the_app_down(monkeypatch):
    """It runs in the lifespan. A model that cannot load is a degraded app,
    never one that refuses to start."""
    monkeypatch.setattr("chitragupta.config.get_settings", lambda: _settings("local"))

    def broken():
        raise RuntimeError("no model on disk")

    monkeypatch.setattr(embeddings, "get_embedder", broken)

    embeddings.warm_embedder()          # must not raise
    time.sleep(0.3)
