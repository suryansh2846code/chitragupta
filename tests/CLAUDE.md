# `tests/`

`pytest` from the repo root. Baseline, measured 2026-09-25: **3725 passed,
30 skipped in ~2min**. Locally you will see a slightly different split — a handful of tests
skip when a provider is genuinely connected on the machine — so compare against
your own last run, not this number. Python venv at `.venv`; use
`./.venv/bin/python`.

`pytest --cov --cov-report=term-missing` prints which code the tests reach; CI
runs it on every push. **80%** at 2026-09-25. There is no threshold and
should not be one — the per-module table is the point, not the percentage. It
has already earned itself: it is how `grok_cli.cancel_cli_login` was found to
have no test while its byte-identical twin in `cursor.py` did.

Four standing rules, each bought the hard way:

- **A test must never start a real sign-in.** `conftest.py` swaps the argv of any
  `login` spawn, because the flows reach `claude auth login`, which opens a
  browser and then waits forever. Nothing reaped them and **158 accumulated on
  one machine**. The guard is itself covered — a guard nobody exercises quietly
  stops working. Earlier versions of this mistake wrote to the real Keychain and
  bound the fixed OAuth port 1455.
- **Never delete, skip or weaken a test to get green.** If a test exposes an
  inconvenient architecture problem, that is the test doing its job.
- **A bug fix ships with a regression test, and you must watch it fail.**
  Reintroduce the bug, confirm red, restore. A test written after the fix and
  never seen red is a guess about what it covers.
- **Execute frontend render paths and click handlers.** The `tests/js/` harnesses
  exist because `node --check` and source-order assertions both pass while a TDZ
  error or a detached container has broken the screen. Two harnesses here were
  themselves blind on the first attempt and only the fail-first check caught it:
  one asserted on a DOM its own error handler had already cleaned up, and one
  counted elements by position instead of by class.

`api_surface.json` pins the HTTP surface — a red `test_api_surface.py` means an
endpoint moved. Fix the move or make it deliberately; never re-baseline to get
green.
