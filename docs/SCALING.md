# Recall scaling — what we can do, what we need, what scaling would buy

> Measured 2026-09-12 on an M-series Mac, `hash` embedder (256-dim), against the
> real `MemoryStore.search()`. Reproduce with `scripts/benchmark_recall.py`.
>
> **BUILT 2026-09-25.** This document's conclusion was "don't build this yet",
> and its trigger fired. Re-measured on the same script after the change:
>
> | memories | before | after | speedup |
> |---|---|---|---|
> | 1,000 | 38 ms | **17.5 ms** | 2.2x |
> | 3,000 | 119 ms | **11.7 ms** | 10x |
> | 10,000 | 430 ms | **13.1 ms** | 33x |
> | 25,000 | 1,145 ms | **15.1 ms** | **76x** |
>
> Better than the 32x this document predicted, because the profiling below
> missed a second cost of the same size: `search()` called `self.get(mid)` for
> every row whose score cleared `min_score` — which at the default of 0.0 is
> nearly all of them. One SELECT per memory in the brain, on every turn, to
> build objects the sort then discarded. That is why 1,000 memories got 2.2x
> faster without the pre-filter engaging at all: below `FULL_SCAN_LIMIT` every
> row is still scored, and the ranking is unchanged.
>
> The ranking risk named under *What it would cost* is real and is now covered
> by `tests/test_recall_scaling.py`, which forces the embedding into its worst
> case rather than hoping the filler rows arrange themselves favourably. The
> lexical net and the date filter are unioned on top of semantic top-K for
> exactly that reason.
>
> **Still true and still worth doing:** cap the uncapped connectors. This change
> raises the ceiling; it does not make an unbounded iMessage import a good idea.
>
> The original analysis follows unchanged.

## What we can do today

Recall latency is **linear in total memory count** — about **0.05 ms per
memory**, because every query scores every row.

| memories | db size | recall p50 | p95 | feels like |
|---|---|---|---|---|
| 1,000 | 2.4 MB | **38 ms** | 44 ms | instant |
| 3,000 | 6.8 MB | **119 ms** | 128 ms | fine |
| 10,000 | 23 MB | **430 ms** | 435 ms | noticeable lag |
| 25,000 | 57 MB | **1,145 ms** | 1,177 ms | bad |
| 50,000 | 115 MB | **2,452 ms** | 2,616 ms | unusable |

Recall runs on **every agent turn** (auto-injected context), so this is added to
every single message, before the model is even called.

### Where the time actually goes

Profiled at 25,000 memories:

| stage | time | share |
|---|---|---|
| SQL full table scan | 49 ms | 4.6% |
| **numpy vector matmul** | **0.7 ms** | **0.1%** |
| **Python scoring loop** | **1,023 ms** | **95.4%** |

The vector search is *not* the bottleneck — 25k × 256 floats is 25 MB resident
and one BLAS call. The cost is the pure-Python loop in
`core/store.py::search()`, which for **every** row computes eight factors
(semantic, lexical, importance, confidence, recency, temporal validity,
reinforcement, status), including re-tokenising the row's full text.

This corrects an earlier assumption in the repo audit that an ANN index (FAISS
etc.) was needed. It is not. An ANN index would optimise the 0.1%.

## What we actually need

The app is **bounded by default**, which is why this has not bitten:

| source | default cap | notes |
|---|---|---|
| Gmail | 600 messages / last 90 days | `gmail_max: 3000` only for full-history |
| Drive | 500 files | |
| Files | 2,000 files | refuses larger folders outright |
| Notion, GitHub, Linear, **iMessage** | **none** | see risk below |

Chunking multiplies documents into memories — one Drive file averages ~5
memories, one email ~1.1.

**Real datapoint** — a live install with Drive + Gmail connected:

```
3,278 memories · 33.8 MB
  gdrive 2,580 · gmail 683 · agent 10 · gcal 4 · notes 1
```

That sits at ~120 ms: comfortable.

**Realistic ceiling** for a heavy user who connects everything and opts into
full history: ~3.3k (Gmail) + ~2.5k (Drive) + ~10k (Files) ≈ **16k memories**,
or roughly **800 ms** per turn. Slow but not broken.

### The real risk is the uncapped connectors

**iMessage has no cap.** Years of message history is plausibly 100k+ rows, which
lands at 5+ seconds per turn — on its own, from one checkbox. Notion, GitHub and
Linear are also uncapped, though their realistic volumes are lower.

That is a cheaper problem to fix than recall: give those connectors the bounded
window Gmail already has.

## What scaling would buy

Prototype: take the **top-K by vector similarity first** (the matmul is already
computed and effectively free), then run the eight-factor scoring over only
those K rows instead of all of them. K=400.

| memories | today | top-K | speedup |
|---|---|---|---|
| 3,000 | 113 ms | **4.6 ms** | 24.6× |
| 10,000 | 400 ms | **13.9 ms** | 28.7× |
| 25,000 | 1,075 ms | **33.9 ms** | 31.7× |
| 50,000 | 2,320 ms | **70.4 ms** | 32.9× |

**50k memories goes from 2.3 s to 70 ms.** The remaining growth is the SQL scan,
which would also go away by fetching only the K ids rather than every row.

This is a contained change to one function — not an ANN index, not a new
dependency, not a schema migration.

### What it would cost

Ranking quality changes: today every memory is scored, so a row with weak
semantic similarity but a strong lexical or importance signal can still surface.
Top-K pre-filtering would cut those off. K needs to be large enough (400 of
50,000 is the top 0.8%) that it does not, and that needs an eval — the
`evaluation_cases` table in the canonical brain exists for exactly this.

## When to build it

Don't, yet. Revisit when **any** of these is true:

- A real user's brain passes **~10,000 memories** (400 ms/turn).
- iMessage, Notion, GitHub or Linear ships to users still uncapped.
- Recall stops being once-per-turn — batch recall, or a reranking pass, would
  multiply the cost.

Cheaper things to do first, in order:

1. **Cap the uncapped connectors**, mirroring `gmail_recent_days` /
   `gmail_recent_max`. Removes the only realistic path to 100k.
2. **Warn in the Brain panel above ~10k memories**, so the failure is visible
   rather than felt as "the app got slow".
3. Then top-K pre-filtering, with an eval to pick K.
