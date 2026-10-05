# `chitragupta/api/` — the HTTP surface

`app.py` is the composition root (middleware, lifespan, mounts); routes live in
`routes/` and mount from `ALL_ROUTERS`.

- `security.py` is the origin guard. It compares the request's `Origin` against
  the `Host` it arrived on — *not* "is the Origin loopback", which would let any
  local dev server call us.
- `concurrency.py` gives model turns and provider probes bounded
  `CapacityLimiter`s. Every handler is a plain `def` sharing one worker pool, and
  the UI is served by the same server, so slow work without a lane takes the
  window with it.
- A pydantic body model used by a route must be **defined above** it — FastAPI
  resolves the annotation at decoration time.
- `tests/api_surface.json` pins the endpoint list. A red `test_api_surface.py`
  means a path moved.
- `routes/backup.py` is also where a restore's **follow-up** lives:
  `archive/` may not import `brain/` (siblings), so re-running migrations after
  a restore is done at this layer, which is allowed to know about both.

Rules: [`/CLAUDE.md`](../../CLAUDE.md).
