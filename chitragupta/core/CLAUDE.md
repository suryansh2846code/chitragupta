# `chitragupta/core/` — store, DB, chunking, embeddings, dates

`store.search()` runs on **every** agent turn. It used to be linear in memory
count (~0.05 ms each): 3k ≈ 120 ms, 10k ≈ 430 ms, 50k ≈ 2.5 s. Two things cost
that, and neither was the vector search — an ANN index would have optimised
0.1% of it:

- the eight-factor Python scoring loop, run over **every** row;
- an N+1: `self.get(mid)` per row whose score cleared `min_score`, which at the
  default 0.0 is nearly all of them — one SELECT per memory in the brain, to
  build objects the sort then threw away.

Both are fixed. Measured 2026-09-25: **25k memories 1,145 ms → 15 ms (76x)**,
and 1k went 38 ms → 17.5 ms with no ranking change at all.

Two rules hold that fix in place:

- **The pre-filter must not engage below `FULL_SCAN_LIMIT`.** A typical brain
  (~3.3k) was never slow; changing its answers to save nothing is a regression
  with no upside.
- **Semantic top-K is never the only way into the candidate set.** An exact word
  match is what a vector index is *worst* at and what a user is *most* confident
  about, so the lexical net and the date filter are unioned on top. Deleting
  either is silent — `tests/test_recall_scaling.py` forces the embedding into
  its worst case rather than hoping the filler rows arrange themselves.

Measure with `scripts/benchmark_recall.py` before and after touching the scoring.
Analysis: [`docs/SCALING.md`](../../docs/SCALING.md).

`_migrate` is additive `ALTER TABLE` only, and idempotent.

`naming.py` is where a name a **model** wrote is matched against what the app
has — agents, apps, tools. Three rules in order: exact, then bar punctuation and
case, then a single close-enough match. The third only applies to names of
`LONG_ENOUGH` characters and only when one candidate is clearly ahead of the
next: "wealth" is one letter from "health" and a different word, and picking
between two near-identical names silently is a coin toss wearing a decision's
clothes. A refusal always names the real options — "no" without them is an
error the next attempt repeats.

