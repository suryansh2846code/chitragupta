"""How hard one connector may push, and how much of the app it may hold.

Two different failures, which is why there are two mechanisms here.

**A rate limit is about the vendor.** Slack allows roughly one request a second
per method and says so by returning 429; Gmail bills against a per-minute quota
shared with everything else the account does. Asking faster than allowed does
not go faster — it goes slower, with a penalty, and eventually with a ban.

**A concurrency lane is about us.** `scheduler.py` sweeps connectors in a
serial `for` loop, so one connector that blocks for its timeout blocks every
connector behind it. Making that parallel without a bound would replace one
stuck connector with fifteen simultaneous ones, which on a laptop is worse. The
answer is the one `api/concurrency.py` already reached for the HTTP handlers:
**a bounded lane per kind of work**, so a slow one cannot crowd out the rest.

This module is the thread-side twin of that one. It is not `anyio`, because the
scheduler is a plain daemon thread and connectors are blocking code all the way
down; a capacity limiter that needs an event loop would mean rewriting fifteen
connectors to get a bound on two.

## The gate is per connector, and shared across every caller

A `Gate` is looked up by connector id and cached, so the scheduler's sweep, a
user pressing Sync Now and an agent reading a thread all draw from the *same*
budget. A limiter constructed per call is not a limit; it is a decoration.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ..log import get_logger
from .contract import Limits

log = get_logger(__name__)


class RateLimitedError(Exception):
    """The local budget is spent and waiting is not on offer.

    Distinct from a vendor's 429 (`ConnectorErrorKind.RATE_LIMITED`): this one
    means *we* stopped, before the request left the machine. Worth telling
    apart, because a connector that constantly hits this is one whose declared
    `Limits` are wrong, and a connector that constantly gets 429s is one whose
    declared limits are too generous for the account.
    """


@dataclass
class Meter:
    """What a gate has been doing. Read by `observability.py` and the UI.

    Plain counters rather than a metrics library: they are read by one screen
    and one diagnostics endpoint, and a dependency for that is a dependency to
    ship, sign and notarise.
    """

    allowed: int = 0
    #: Times a caller had to wait for the budget.
    delayed: int = 0
    #: Seconds spent waiting for the budget, in total.
    delayed_seconds: float = 0.0
    #: Times a caller was refused because waiting was not on offer.
    refused: int = 0
    #: The most that were ever in flight at once.
    peak_concurrency: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "delayed": self.delayed,
                "delayed_seconds": round(self.delayed_seconds, 3),
                "refused": self.refused,
                "peak_concurrency": self.peak_concurrency}


@dataclass
class Gate:
    """One connector's share of the vendor and of this app.

    A sliding window rather than a token bucket. The vendors that publish a
    limit publish it as *"N requests per minute"*, and a sliding window is that
    sentence — a bucket with a refill rate is a different sentence that happens
    to average the same, and the difference shows up exactly at the start of a
    sync, when a burst of page-one requests goes out together.
    """

    connector: str
    limits: Limits = field(default_factory=Limits)
    meter: Meter = field(default_factory=Meter)

    _lock: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.Lock()),
        repr=False)
    #: Monotonic timestamps of requests inside the current window.
    _recent: deque[float] = field(default_factory=deque, repr=False)
    _in_flight: int = 0

    #: Injected so the schedule is testable without a test that actually waits.
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    # ── the two questions ────────────────────────────────────────────────

    def _prune(self, now: float) -> None:
        window = self.limits.per_seconds
        while self._recent and now - self._recent[0] >= window:
            self._recent.popleft()

    def _wait_seconds(self, now: float) -> float:
        """How long until the rate budget has room. 0 means now."""
        if self.limits.requests <= 0:      # unmetered
            return 0.0
        self._prune(now)
        if len(self._recent) < self.limits.requests:
            return 0.0
        # The oldest request in the window is the one that has to age out.
        return max(0.0, self.limits.per_seconds - (now - self._recent[0]))

    @contextmanager
    def __call__(self, *, wait: bool = True, timeout: float | None = None,
                 cancel: Any = None) -> Iterator[None]:
        """Hold one slot for the duration of the block.

            with gate():
                response = urlopen(request)

        `wait=False` refuses rather than blocks, which is what a health check
        wants: a cheap probe that queues behind a sync is not cheap, and a
        health check that reports "slow" because it waited for a rate limit is
        reporting on the wrong thing.
        """
        self._acquire(wait=wait, timeout=timeout, cancel=cancel)
        try:
            yield
        finally:
            self._release()

    def _acquire(self, *, wait: bool, timeout: float | None,
                 cancel: Any) -> None:
        deadline = None if timeout is None else self.clock() + timeout
        waited_from: float | None = None
        with self._lock:
            while True:
                if cancel is not None and cancel.is_set():
                    raise RateLimitedError(
                        f"{self.connector}: cancelled while waiting")
                now = self.clock()
                room = self._in_flight < max(1, self.limits.concurrency)
                pause = self._wait_seconds(now)
                if room and pause <= 0:
                    self._recent.append(now)
                    self._in_flight += 1
                    self.meter.allowed += 1
                    self.meter.peak_concurrency = max(
                        self.meter.peak_concurrency, self._in_flight)
                    if waited_from is not None:
                        self.meter.delayed += 1
                        self.meter.delayed_seconds += now - waited_from
                    return
                if not wait:
                    self.meter.refused += 1
                    raise RateLimitedError(
                        f"{self.connector} is at its own limit just now")
                if deadline is not None and now >= deadline:
                    self.meter.refused += 1
                    raise RateLimitedError(
                        f"{self.connector} did not get a slot in time")
                if waited_from is None:
                    waited_from = now
                # Bounded even when nothing notifies: a concurrency slot is
                # released by `notify`, but a *rate* slot ages out with no
                # event at all, so a pure wait-for-notify would sleep past it.
                nap = min(0.1 if pause <= 0 else pause, 0.25)
                if deadline is not None:
                    nap = min(nap, max(0.0, deadline - now))
                self._lock.wait(nap)

    def _release(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._lock.notify_all()

    # ── what the vendor told us ──────────────────────────────────────────

    def note_rate_limit(self, *, seconds: float = 0.0) -> None:
        """The vendor said slow down. Spend the local budget so we do.

        Without this, a 429 is absorbed by `retry.py` and the *next* request
        goes out at the same rate that caused it. Filling the window makes the
        gate itself hold everything back for one window — which is what the
        vendor asked for, applied to every caller rather than only the one that
        got the 429.
        """
        with self._lock:
            now = self.clock()
            if self.limits.requests > 0:
                self._recent.clear()
                self._recent.extend([now] * self.limits.requests)
            if seconds > 0:
                # A longer hold than one window: push the window's start into
                # the future so `_wait_seconds` returns what the vendor asked.
                ahead = now + max(0.0, seconds - self.limits.per_seconds)
                self._recent = deque([ahead] * max(1, self.limits.requests))
            self._lock.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            self._prune(now)
            return {"connector": self.connector,
                    "in_flight": self._in_flight,
                    "in_window": len(self._recent),
                    "waiting_seconds": round(self._wait_seconds(now), 3),
                    "limits": self.limits.as_dict(),
                    "meter": self.meter.as_dict()}


# ── the shared registry ──────────────────────────────────────────────────
#
# One gate per connector, for the life of the process. Constructed on first
# use rather than eagerly, because MCP and custom connectors are one per user
# configuration and the set is not known at import.

_GATES: dict[str, Gate] = {}
_GATES_LOCK = threading.Lock()


def gate_for(connector: str, limits: Limits | None = None) -> Gate:
    """The gate for one connector, shared by every caller.

    `limits` is applied only when the gate is first created. A later call with
    different limits does not silently re-tune a budget other threads are
    already drawing from — a limit that changes underneath an in-flight sync is
    a limit nobody can reason about. Use `retune()` deliberately.
    """
    with _GATES_LOCK:
        found = _GATES.get(connector)
        if found is None:
            found = Gate(connector=connector, limits=limits or Limits())
            _GATES[connector] = found
        return found


def retune(connector: str, limits: Limits) -> Gate:
    """Deliberately change one connector's budget, keeping its meter."""
    with _GATES_LOCK:
        found = _GATES.get(connector)
        if found is None:
            found = Gate(connector=connector, limits=limits)
            _GATES[connector] = found
        else:
            found.limits = limits
        return found


def all_snapshots() -> list[dict[str, Any]]:
    """Every gate, for the diagnostics screen."""
    with _GATES_LOCK:
        gates = list(_GATES.values())
    return [g.snapshot() for g in gates]


def reset() -> None:
    """Forget every gate. Tests only — a process that did this at runtime would
    hand every connector a fresh budget and un-apply every backoff in flight."""
    with _GATES_LOCK:
        _GATES.clear()
