"""One connector cannot spend the vendor's quota or the app's capacity.

Two different bounds, which is why `Gate` holds two:

* **The rate limit is about the vendor.** Asking faster than allowed does not
  go faster; it goes slower, with a penalty, and eventually with a ban.
* **The concurrency lane is about us.** `scheduler.py` sweeps connectors
  serially, so one that blocks for its timeout blocks every connector behind
  it. Making that parallel without a bound replaces one stuck connector with
  fifteen simultaneous ones, which on a laptop is worse.

The property that makes it real rather than decorative: **the gate is shared**.
A limiter constructed per call is not a limit. `gate_for` caches by connector,
so the scheduler's sweep, a user pressing Sync Now and an agent reading a
thread all draw from one budget.

The clock is injected throughout. A rate-limit test that actually waits a
minute is a test nobody runs.
"""
from __future__ import annotations

import threading

import pytest

from chitragupta.connectors.contract import Limits
from chitragupta.connectors.limits import (
    Gate,
    RateLimitedError,
    all_snapshots,
    gate_for,
    reset,
    retune,
)


class Ticker:
    """A clock that only moves when a test says so."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _forget_gates():
    reset()
    yield
    reset()


def build(**limits) -> tuple[Gate, Ticker]:
    tick = Ticker()
    return Gate(connector="x", limits=Limits(**limits), clock=tick), tick


# ── the rate budget ───────────────────────────────────────────────────────


def test_requests_inside_the_budget_go_straight_through():
    gate, _ = build(requests=3, per_seconds=60.0)

    for _ in range(3):
        with gate(wait=False):
            pass

    assert gate.meter.allowed == 3
    assert gate.meter.refused == 0


def test_the_request_past_the_budget_is_held():
    gate, _ = build(requests=2, per_seconds=60.0)
    for _ in range(2):
        with gate(wait=False):
            pass

    with pytest.raises(RateLimitedError):
        with gate(wait=False):
            pass

    assert gate.meter.refused == 1


def test_the_budget_is_a_sliding_window_not_a_reset_at_the_minute():
    """The vendors that publish a limit publish it as "N per minute", and a
    bucket with a refill rate is a different sentence that happens to average
    the same. The difference shows at the start of a sync, when a burst of
    page-one requests goes out together."""
    gate, tick = build(requests=2, per_seconds=60.0)
    with gate(wait=False):
        pass
    tick.advance(30)
    with gate(wait=False):
        pass

    with pytest.raises(RateLimitedError):
        with gate(wait=False):
            pass

    tick.advance(31)          # the first request ages out, the second has not
    with gate(wait=False):    # so exactly one slot opens
        pass
    with pytest.raises(RateLimitedError):
        with gate(wait=False):
            pass


def test_an_unmetered_connector_is_never_rate_limited():
    """`requests=0` means the vendor publishes nothing we can honour, and
    inventing a limit would throttle a connector for no stated reason."""
    gate, _ = build(requests=0, concurrency=10)

    for _ in range(50):
        with gate(wait=False):
            pass

    assert gate.meter.refused == 0


# ── the concurrency lane ──────────────────────────────────────────────────


def test_only_so_many_may_be_in_flight_at_once():
    gate, _ = build(requests=0, concurrency=2)
    held = gate(wait=False)
    other = gate(wait=False)
    held.__enter__()
    other.__enter__()

    with pytest.raises(RateLimitedError):
        with gate(wait=False):
            pass

    held.__exit__(None, None, None)
    with gate(wait=False):    # a slot came back
        pass
    other.__exit__(None, None, None)


def test_a_slot_is_returned_even_when_the_body_raises():
    """A connector that throws must not leak its lane — one bad page would
    otherwise narrow the connector permanently."""
    gate, _ = build(requests=0, concurrency=1)

    with pytest.raises(ValueError):
        with gate(wait=False):
            raise ValueError("the request blew up")

    with gate(wait=False):
        pass


def test_the_peak_is_recorded_so_a_lane_can_be_sized():
    gate, _ = build(requests=0, concurrency=3)
    holders = [gate(wait=False) for _ in range(3)]
    for h in holders:
        h.__enter__()
    for h in holders:
        h.__exit__(None, None, None)

    assert gate.meter.peak_concurrency == 3


def test_a_waiting_caller_gets_through_when_a_slot_frees():
    """The blocking path, run for real — with a real (tiny) clock, because
    what is being tested is that `notify` wakes the waiter at all."""
    gate = Gate(connector="x", limits=Limits(requests=0, concurrency=1))
    started, done = threading.Event(), threading.Event()

    def second():
        started.set()
        with gate(timeout=5.0):
            done.set()

    with gate():
        worker = threading.Thread(target=second, daemon=True)
        worker.start()
        started.wait(1.0)
        assert not done.is_set(), "it must not have got in while we hold the slot"

    worker.join(5.0)
    assert done.is_set()
    assert gate.meter.delayed == 1


def test_waiting_has_a_deadline():
    """A caller that waits forever is a UI that hangs forever."""
    gate = Gate(connector="x", limits=Limits(requests=0, concurrency=1))

    with gate():
        with pytest.raises(RateLimitedError):
            with gate(timeout=0.05):
                pass


def test_a_cancel_releases_a_waiting_caller():
    class Stop:
        def is_set(self):
            return True

    gate = Gate(connector="x", limits=Limits(requests=0, concurrency=1))

    with gate():
        with pytest.raises(RateLimitedError):
            with gate(cancel=Stop()):
                pass


# ── what the vendor told us ───────────────────────────────────────────────


def test_a_429_holds_every_caller_back_not_just_the_one_that_got_it():
    """Without this, the 429 is absorbed by `retry.py` and the next request
    goes out at the rate that caused it."""
    gate, _ = build(requests=10, per_seconds=60.0)
    with gate(wait=False):
        pass

    gate.note_rate_limit()

    with pytest.raises(RateLimitedError):
        with gate(wait=False):
            pass


def test_the_hold_lifts_after_the_window():
    gate, tick = build(requests=5, per_seconds=60.0)
    gate.note_rate_limit()
    tick.advance(61)

    with gate(wait=False):
        pass


def test_a_longer_hold_than_a_window_is_honoured():
    gate, tick = build(requests=5, per_seconds=60.0)
    gate.note_rate_limit(seconds=300)

    tick.advance(61)
    with pytest.raises(RateLimitedError):
        with gate(wait=False):
            pass

    tick.advance(300)
    with gate(wait=False):
        pass


# ── the gate is shared, which is the whole point ──────────────────────────


def test_the_same_connector_gets_the_same_gate():
    """A limiter constructed per call is not a limit; it is a decoration."""
    assert gate_for("gmail") is gate_for("gmail")
    assert gate_for("gmail") is not gate_for("slack")


def test_limits_are_not_silently_retuned_by_a_later_lookup():
    """A budget that changes underneath an in-flight sync is one nobody can
    reason about."""
    first = gate_for("gmail", Limits(requests=10))

    again = gate_for("gmail", Limits(requests=9999))

    assert again is first
    assert again.limits.requests == 10


def test_retuning_is_deliberate_and_keeps_the_meter():
    gate = gate_for("gmail", Limits(requests=10, concurrency=5))
    with gate(wait=False):
        pass

    retuned = retune("gmail", Limits(requests=2))

    assert retuned is gate
    assert retuned.limits.requests == 2
    assert retuned.meter.allowed == 1, "history is not thrown away"


def test_every_gate_can_be_read_for_the_diagnostics_screen():
    gate_for("gmail", Limits(requests=5))
    gate_for("slack", Limits(requests=5))

    names = {row["connector"] for row in all_snapshots()}

    assert names == {"gmail", "slack"}


def test_a_snapshot_says_how_long_the_wait_is():
    gate, _ = build(requests=1, per_seconds=60.0)
    with gate(wait=False):
        pass

    snapshot = gate.snapshot()

    assert snapshot["in_window"] == 1
    assert snapshot["waiting_seconds"] == pytest.approx(60.0)
    assert snapshot["limits"]["imposed_by"] == "Chitragupta"
