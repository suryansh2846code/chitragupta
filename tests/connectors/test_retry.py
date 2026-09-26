"""Trying again, bounded — and not trying again when it could not help.

The dangerous half of a retry loop is the half that retries. Four failures must
never be repeated, and each has a shipped-bug shape behind it:

* `403` — the same credential is refused identically, and hammering it turns a
  scope problem into a rate-limit problem.
* `400` — the request is wrong; repeating it asks the vendor to say no thrice.
* `401` — retrying an expired credential is the loop that locks accounts out.
* `404` — the thing is gone. News to record, not a failure to repeat.

Everything here injects `sleep`, `rand` and `now`, so the schedule is asserted
rather than waited for. A backoff test that actually sleeps is a test nobody
runs and therefore a bound nobody checks.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chitragupta.connectors.errors import (
    ConnectorError,
    ConnectorErrorKind,
    classify_http,
)
from chitragupta.connectors.retry import (
    DEFAULT,
    CancelledError,
    RetryPolicy,
    delay_for,
    with_retries,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
NO_JITTER = RetryPolicy(attempts=4, base_seconds=1.0, max_seconds=8.0, jitter=0)


class Clock:
    """Records what was slept instead of sleeping."""

    def __init__(self):
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)

    @property
    def total(self) -> float:
        return sum(self.slept)


class Stop:
    def __init__(self, after: int = 0):
        self.after, self.checks = after, 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > self.after


def failing(*errors, then=None):
    """A callable that raises each error in turn, then returns `then`."""
    queue = list(errors)

    def operation():
        if queue:
            raise queue.pop(0)
        return then

    return operation


def err(kind, retry_at=""):
    return ConnectorError(kind=kind, connector="x", message="m",
                          retry_at=retry_at)


# ── the schedule ──────────────────────────────────────────────────────────


def test_the_backoff_is_exponential_and_capped():
    rate = err(ConnectorErrorKind.PROVIDER)
    delays = [delay_for(rate, n, NO_JITTER, now=NOW) for n in range(1, 6)]

    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0], "doubles, then holds at the cap"


def test_jitter_spreads_both_ways_so_a_convoy_breaks_up():
    """The scheduler wakes every connector on the same tick. A backoff that
    only ever *adds* delay moves the whole convoy together."""
    policy = RetryPolicy(base_seconds=10.0, max_seconds=10.0, jitter=0.5)
    error = err(ConnectorErrorKind.NETWORK)

    lowest = delay_for(error, 1, policy, rand=lambda: 0.0, now=NOW)
    middle = delay_for(error, 1, policy, rand=lambda: 0.5, now=NOW)
    highest = delay_for(error, 1, policy, rand=lambda: 1.0, now=NOW)

    assert lowest == 5.0 and middle == 10.0 and highest == 15.0


def test_a_delay_is_never_negative():
    policy = RetryPolicy(base_seconds=1.0, jitter=2.0)   # absurd on purpose

    assert delay_for(err(ConnectorErrorKind.NETWORK), 1, policy,
                     rand=lambda: 0.0, now=NOW) >= 0.0


# ── the provider wins ─────────────────────────────────────────────────────


def test_a_retry_after_beats_our_own_schedule():
    """Backing off *less* than we were told to is the one direction that
    cannot be safe."""
    asked = (NOW + timedelta(seconds=45)).isoformat()

    assert delay_for(err(ConnectorErrorKind.RATE_LIMITED, asked), 1,
                     NO_JITTER, now=NOW) == 45.0


def test_a_retry_after_is_not_jittered_downward():
    asked = (NOW + timedelta(seconds=30)).isoformat()
    policy = RetryPolicy(base_seconds=1.0, jitter=0.9)

    assert delay_for(err(ConnectorErrorKind.RATE_LIMITED, asked), 1, policy,
                     rand=lambda: 0.0, now=NOW) == 30.0


def test_a_retry_after_that_has_already_passed_falls_back_to_our_schedule():
    stale = (NOW - timedelta(minutes=5)).isoformat()

    assert delay_for(err(ConnectorErrorKind.RATE_LIMITED, stale), 1,
                     NO_JITTER, now=NOW) == 1.0


# ── what is retried, and what is not ──────────────────────────────────────


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_a_transient_failure_is_tried_again(status):
    clock = Clock()
    operation = failing(classify_http("x", status), then="landed")

    got = with_retries(operation, connector="x", policy=NO_JITTER,
                       classify=lambda e: e, sleep=clock)

    assert got == "landed"
    # Asserted as elapsed time, not as a count of `sleep` calls: the wait is
    # served in 0.25s slices so a cancel lands inside it rather than after it.
    assert clock.total == pytest.approx(1.0)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_a_failure_that_repeating_cannot_fix_is_raised_at_once(status):
    clock = Clock()
    operation = failing(classify_http("x", status), then="never reached")

    with pytest.raises(ConnectorError) as caught:
        with_retries(operation, connector="x", policy=NO_JITTER,
                     classify=lambda e: e, sleep=clock)

    assert caught.value.status == status
    assert clock.slept == [], "nothing should have been slept"


def test_retries_are_bounded_by_attempts():
    clock = Clock()
    policy = RetryPolicy(attempts=3, base_seconds=1.0, jitter=0,
                         total_seconds=1000)
    operation = failing(*[classify_http("x", 503)] * 10)

    with pytest.raises(ConnectorError):
        with_retries(operation, connector="x", policy=policy,
                     classify=lambda e: e, sleep=clock)

    assert clock.total == pytest.approx(3.0), \
        "three attempts means two waits, of 1s and 2s"


def test_retries_are_bounded_by_total_time_as_well_as_by_count():
    """A provider asking for an hour is honoured by *stopping*, not by
    sleeping for an hour inside a request handler."""
    clock = Clock()
    asked = (datetime.now(UTC) + timedelta(seconds=3600)).isoformat()
    policy = RetryPolicy(attempts=5, total_seconds=30.0)
    operation = failing(*[err(ConnectorErrorKind.RATE_LIMITED, asked)] * 5)

    with pytest.raises(ConnectorError):
        with_retries(operation, connector="x", policy=policy,
                     classify=lambda e: e, sleep=clock)

    assert clock.total <= 30.0


def test_one_attempt_means_no_retrying_at_all():
    clock = Clock()
    operation = failing(classify_http("x", 503))

    with pytest.raises(ConnectorError):
        with_retries(operation, connector="x",
                     policy=RetryPolicy(attempts=1), classify=lambda e: e,
                     sleep=clock)

    assert clock.slept == []


def test_a_first_attempt_that_works_never_sleeps():
    clock = Clock()

    assert with_retries(lambda: "fine", connector="x", sleep=clock) == "fine"
    assert clock.slept == []


# ── what the caller gets back ─────────────────────────────────────────────


def test_it_raises_the_classified_error_never_the_vendors_exception():
    """A caller cannot accidentally put a library repr in front of a user."""
    class VendorBoomError(Exception):
        pass

    with pytest.raises(ConnectorError) as caught:
        with_retries(failing(VendorBoomError("internal detail")), connector="x",
                     label="Acme", policy=RetryPolicy(attempts=1),
                     sleep=Clock())

    assert "Acme" in caught.value.message
    assert isinstance(caught.value.__cause__, VendorBoomError)


def test_the_caller_is_told_each_time_it_backs_off():
    """So a sync can report "waiting on Slack" rather than looking hung."""
    seen = []
    with_retries(failing(classify_http("x", 429), then="ok"), connector="x",
                 policy=NO_JITTER, classify=lambda e: e, sleep=Clock(),
                 on_retry=lambda e, n, pause: seen.append((e.kind, n, pause)))

    assert seen == [(ConnectorErrorKind.RATE_LIMITED, 1, 1.0)]


# ── stopping ──────────────────────────────────────────────────────────────


def test_a_cancel_before_the_first_attempt_runs_nothing():
    ran = []

    with pytest.raises(CancelledError):
        with_retries(lambda: ran.append(1), connector="x", cancel=Stop(),
                     sleep=Clock())

    assert ran == []


def test_a_cancel_lands_inside_a_backoff_rather_than_after_it():
    """A single `sleep(20)` is a Stop button that does nothing for twenty
    seconds — the failure `/CLAUDE.md` calls out by name."""
    clock = Clock()
    policy = RetryPolicy(attempts=3, base_seconds=20.0, jitter=0,
                         total_seconds=1000)

    with pytest.raises(CancelledError):
        with_retries(failing(*[classify_http("x", 503)] * 3), connector="x",
                     policy=policy, classify=lambda e: e, cancel=Stop(after=2),
                     sleep=clock)

    assert clock.total < 20.0, "the wait was interrupted, not served in full"


def test_cancelled_is_not_mistaken_for_a_result():
    """A sync that read "cancelled" as "finished with nothing" would write a
    watermark past records it never fetched — what `base._finish` exists to
    stop."""
    assert not issubclass(CancelledError, ConnectorError)


def test_the_default_policy_is_small_on_purpose():
    """The next scheduled pass is the real retry. Five minutes of retrying
    inside one sync is five minutes the Stop button spends being ignored."""
    assert DEFAULT.attempts <= 3
    assert DEFAULT.total_seconds <= 60
