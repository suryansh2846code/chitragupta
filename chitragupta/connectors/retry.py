"""Trying again, bounded, and only when trying again could work.

There were no retries anywhere in `connectors/` before this. A 429 was a failed
sync that would be attempted again in thirty minutes by the scheduler, and a
dropped TCP connection halfway through a mailbox threw the whole pass away. The
tempting fix — wrap every call in `for _ in range(3)` — is worse than nothing,
because the errors that matter most are the ones that must **not** be retried:

* `403 insufficient scope` — the same credential will be refused identically,
  and hammering it is how a scope problem becomes a rate-limit problem.
* `400 invalid request` — the request is wrong; sending it again does not fix
  it, it just asks the vendor to say no three times.
* `401 expired` — retrying the *same* expired credential is the loop that locks
  accounts out. A refresh followed by a call is one attempt with a different
  credential, which is `connections.py`'s job and not this module's.
* `404` — the thing is gone. That is news to record, not a failure to repeat.

So the decision is `ConnectorError.retryable`, which is a property of the
classified kind, and this module never second-guesses it.

## The schedule

Exponential, jittered, capped, and **the provider wins**. When a vendor sends
`Retry-After`, that is not advice — it is the number that stops us being
blocked, and backing off less than asked is how a rate-limit becomes a ban.
Jitter exists because the scheduler wakes every connector on the same timer: a
fixed backoff means fifteen connectors that failed together retry together,
forever.

## What it does not do

It does not sleep across a cancel. `cancel` is checked before every wait and
inside it, because the alternative is a Stop button that appears to do nothing
for thirty seconds — which is the failure `/CLAUDE.md` names as *"anything the
user starts, they can stop"*.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from ..log import get_logger
from .base import Cancellable
from .errors import ConnectorError, classify_exception

log = get_logger(__name__)

T = TypeVar("T")


class CancelledError(Exception):
    """The caller asked to stop between attempts.

    Raised rather than returned so it cannot be mistaken for a result. A sync
    that treats "cancelled" as "finished with nothing" writes a watermark past
    records it never fetched, which is the bug `base._finish` exists to stop.
    """


@dataclass(frozen=True)
class RetryPolicy:
    """How many times, how long, and how much of it is random.

    The defaults are deliberately small. A connector sync already runs on a
    timer: the *next* pass is the real retry, and this exists to survive a
    blip rather than to outlast an outage. Five minutes of retrying inside one
    sync is five minutes the Stop button spends being ignored.
    """

    #: Total attempts including the first. 1 disables retrying entirely.
    attempts: int = 3
    base_seconds: float = 0.5
    max_seconds: float = 20.0
    #: Fraction of the computed delay that is random, each way. 0 disables it.
    jitter: float = 0.3
    #: Ceiling on everything this call may spend waiting. A provider that asks
    #: for 3600 seconds is honoured by *stopping*, not by sleeping for an hour
    #: inside a request handler.
    total_seconds: float = 60.0


#: What a sync uses. Named so a connector reads `RetryPolicy` only when it
#: means something other than this.
DEFAULT = RetryPolicy()

#: What a user-initiated write uses: somebody is watching, so one quick second
#: attempt is worth having and a third is worth less than the wait.
INTERACTIVE = RetryPolicy(attempts=2, base_seconds=0.4, max_seconds=2.0,
                          total_seconds=4.0)


def _seconds_until(retry_at: str, *, now: datetime | None = None) -> float:
    """How long the provider asked for, from the instant it named."""
    if not retry_at:
        return 0.0
    try:
        when = datetime.fromisoformat(retry_at)
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def delay_for(error: ConnectorError, attempt: int, policy: RetryPolicy = DEFAULT,
              *, rand: Callable[[], float] | None = None,
              now: datetime | None = None) -> float:
    """Seconds to wait before attempt number `attempt + 1`.

    `attempt` is 1-based and counts the one that just failed, so the first
    backoff is `base_seconds`.

    **The provider's own number wins outright**, un-jittered and un-capped by
    `max_seconds`: jittering a `Retry-After` downward is asking again earlier
    than we were told to, and that is the one direction that cannot be safe.
    `total_seconds` still bounds it, in the caller.
    """
    asked = _seconds_until(error.retry_at, now=now)
    if asked > 0:
        return asked

    exponential = min(policy.base_seconds * (2 ** max(0, attempt - 1)),
                      policy.max_seconds)
    if policy.jitter <= 0:
        return exponential
    roll = (rand or random.random)()
    # Full-width jitter around the exponential, never below zero. Symmetric
    # rather than "up to X": the point is to break up a convoy of connectors
    # that failed on the same tick, and only ever adding delay moves the whole
    # convoy together.
    spread = exponential * policy.jitter
    return max(0.0, exponential - spread + roll * 2 * spread)


def _wait(seconds: float, cancel: Cancellable | None,
          sleep: Callable[[float], None]) -> None:
    """Sleep in slices, so a cancel lands within a tick rather than a backoff.

    A single `sleep(20)` is a Stop button that does nothing for twenty seconds.
    """
    remaining = seconds
    while remaining > 0:
        if cancel is not None and cancel.is_set():
            raise CancelledError
        slice_ = min(0.25, remaining)
        sleep(slice_)
        remaining -= slice_


def with_retries(
    operation: Callable[[], T],
    *,
    connector: str,
    label: str = "",
    policy: RetryPolicy = DEFAULT,
    cancel: Cancellable | None = None,
    classify: Callable[[BaseException], ConnectorError] | None = None,
    on_retry: Callable[[ConnectorError, int, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] | None = None,
    now: Callable[[], datetime] | None = None,
) -> T:
    """Run `operation`, retrying only what is worth retrying.

    Raises the **classified** `ConnectorError` when it finally gives up, never
    the vendor's exception — so a caller cannot accidentally put a library repr
    in front of a user. The original is attached as `__cause__` for the log.

    `sleep`, `rand` and `now` are injected so the schedule is testable without
    a test that actually waits: `tests/connectors/test_retry.py` asserts the
    exact delays, which is the only way to know a backoff is bounded.
    """
    clock = now or (lambda: datetime.now(UTC))
    classifier = classify or (
        lambda exc: classify_exception(connector, exc, label=label))

    spent = 0.0
    last: ConnectorError | None = None
    for attempt in range(1, max(1, policy.attempts) + 1):
        if cancel is not None and cancel.is_set():
            raise CancelledError
        try:
            return operation()
        except CancelledError:
            raise
        except BaseException as exc:      # classified below, then re-raised
            error = classifier(exc)
            last = error
            if not error.retryable or attempt >= policy.attempts:
                raise error from exc
            pause = delay_for(error, attempt, policy, rand=rand, now=clock())
            if spent + pause > policy.total_seconds:
                # Honoured by stopping. The connection is left reporting the
                # real reason, and the next scheduled pass is the retry — which
                # is a schedule the user can see, unlike a sleeping thread.
                log.debug("%s: giving up after %.1fs; %s asked for %.1fs more",
                          connector, spent, error.kind.value, pause)
                raise error from exc
            if on_retry is not None:
                on_retry(error, attempt, pause)
            _wait(pause, cancel, sleep)
            spent += pause

    # Unreachable: the loop either returns or raises. Kept explicit so a future
    # edit to the bounds cannot fall out of it returning None.
    raise last if last is not None else RuntimeError(  # pragma: no cover
        f"{connector}: retry loop ended without a result")
