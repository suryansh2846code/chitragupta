"""Recall at size — and the two ways making it fast could make it wrong.

Recall runs on *every* agent turn, before the model is called at all, and it was
linear in the total number of memories: 25,000 memories cost 1.02 s of pure
Python re-deriving an order the vector matmul had already computed in 0.7 ms.
`docs/SCALING.md` measured it and deliberately deferred the fix. The trigger it
named — an uncapped connector putting a real user past 10k — is one checkbox
away, so it is built.

Two things could go wrong, and neither shows up as a failure:

* **The shortcut changes an answer.** Scoring only the top candidates by vector
  similarity cuts off the row a vector index is worst at: the exact word the
  user typed, which an embedding can place nowhere near the query. That is the
  query a user is *most* confident about, so getting it wrong is worse than
  being slow.
* **The shortcut engages where it was not needed.** A typical brain is ~3.3k and
  was never slow. Changing its results to save nothing is a regression with no
  upside.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from chitragupta.core.store import MemoryStore


@pytest.fixture
def store():
    os.environ.setdefault("CHITRAGUPTA_EMBEDDING_PROVIDER", "hash")
    path = Path(tempfile.mkdtemp()) / "recall.db"
    return MemoryStore(db_path=path)


def _fill(store: MemoryStore, n: int, *, prefix: str = "note") -> None:
    for i in range(n):
        store.add(f"{prefix} {i} about the quarterly roadmap and release planning",
                  source="files", kind="note", title=f"{prefix}-{i}")


# ── the shortcut must not change a small brain ─────────────────────────────

def test_a_small_brain_still_scores_every_row(store):
    """Below the threshold the answer must be what it has always been. The
    pre-filter is a fix for a brain that had become slow, not a new ranking."""
    _fill(store, 50)
    assert store.count() <= store.FULL_SCAN_LIMIT
    assert store._recall_candidates(None, {"roadmap"}, None, False) is None


def test_the_candidate_set_is_only_built_when_there_is_something_to_save(store):
    """`None` means "score everything", and the full-scan path must keep
    reaching the query with no id list to match against — handing it one is how
    a fast path quietly becomes the slow one."""
    _fill(store, 10)
    assert store._candidate_rows(None) is not None
    assert len(store._candidate_rows(None)) == 10


# ── the shortcut must not lose an exact match ──────────────────────────────

def _sims_ranking_last(store: MemoryStore, memory_id: str):
    """A similarity vector that puts `memory_id` dead last.

    Asserting through `search()` on real embeddings proved nothing: with 2,500
    near-identical filler rows the odd one out lands in the top 400 by being
    odd, so the safety net could be deleted and the test still passed. The
    mechanism is worth testing directly, with the embedding forced into its
    worst case rather than hoped out of it.
    """
    import numpy as np

    store._ensure_vectors()
    sims = np.ones(len(store._ids), dtype="float32")
    sims[store._ids.index(memory_id)] = -1.0
    return sims


def test_an_exact_word_match_survives_the_pre_filter(store):
    """The query a user is most confident about is a name or a number that
    appears verbatim — and verbatim is exactly what an embedding is worst at
    placing near the query. Without the lexical safety net this row is cut off
    before it is ever scored."""
    _fill(store, 2_500)
    mem = store.add("The Zanzibar invoice is numbered QX88317 and is unpaid",
                    source="manual", kind="fact", title="invoice")
    assert mem is not None
    sims = _sims_ranking_last(store, mem.id)

    candidates = store._recall_candidates(sims, {"qx88317"}, None, False)
    assert candidates is not None
    assert mem.id in candidates, (
        "an exact term the embedding ranked last was dropped before scoring")


def test_a_date_bounded_query_keeps_every_row_the_filter_named(store):
    """A date filter is a restriction the user set, not a hint. A row inside it
    that the pre-filter dropped is missing from an answer they bounded
    themselves — and it is the one case where they can see it is missing."""
    _fill(store, 2_500)
    mem = store.add("Signed the lease", source="manual", kind="event",
                    title="lease", event_date="2024-03-04")
    assert mem is not None
    sims = _sims_ranking_last(store, mem.id)

    candidates = store._recall_candidates(sims, set(), {mem.id}, False)
    assert candidates is not None
    assert mem.id in candidates


def test_a_date_bounded_query_answers_end_to_end(store):
    """The same guarantee through the front door, so the two cannot drift."""
    _fill(store, 2_500)
    store.add("Signed the lease", source="manual", kind="event",
              title="lease", event_date="2024-03-04")
    hits = store.search("lease", limit=5,
                        date_start="2024-03-01", date_end="2024-03-31")
    assert [h for h in hits if "lease" in h.memory.text.lower()]


def test_semantic_neighbours_are_still_found_at_size(store):
    """The pre-filter's own job, checked rather than assumed."""
    _fill(store, 2_500)
    store.add("Priya owns the billing migration and reports on Thursdays",
              source="manual", kind="fact", title="priya")
    hits = store.search("who owns the billing migration", limit=8)
    assert any("Priya" in h.memory.text for h in hits)


# ── the N+1 ────────────────────────────────────────────────────────────────

def test_only_the_returned_memories_are_loaded(store):
    """`self.get(mid)` is a query, and it used to run for every row whose score
    cleared `min_score` — which at the default of 0.0 is very nearly all of
    them. One SELECT per memory in the brain, on every agent turn, to build
    objects the sort then threw away."""
    _fill(store, 300)
    loaded: list[str] = []
    real_get = store.get
    store.get = lambda mid: (loaded.append(mid), real_get(mid))[1]  # type: ignore[method-assign]
    hits = store.search("roadmap", limit=5)
    assert len(hits) == 5
    assert len(loaded) == 5, (
        f"loaded {len(loaded)} memories to return 5 — the N+1 is back")


def test_a_short_answer_does_not_load_a_long_one(store):
    _fill(store, 300)
    loaded: list[str] = []
    real_get = store.get
    store.get = lambda mid: (loaded.append(mid), real_get(mid))[1]  # type: ignore[method-assign]
    store.search("roadmap", limit=1)
    assert len(loaded) == 1


# ── the ordering is unchanged by any of it ─────────────────────────────────

def test_the_full_scan_ranking_is_exactly_what_it_was(store):
    """The N+1 fix moved where memories are loaded, not how they are ranked.
    Scores must be identical, in the same order."""
    _fill(store, 100)
    store.add("The launch date moved to November", source="chat", kind="fact")
    hits = store.search("when is the launch", limit=10)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(h.explanation.total_score == h.score for h in hits)


def test_an_empty_brain_answers_without_touching_any_of_this(store):
    assert store.search("anything") == []
