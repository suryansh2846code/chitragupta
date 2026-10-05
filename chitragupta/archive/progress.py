"""Telling the user how a backup is going, and letting them stop it.

Two requirements from CLAUDE.md, both load-bearing:

> **Never make the user wait without telling them.** Long work is a background
> job with progress that survives a refresh. A spinner with no end state is a
> bug.

> **Anything the user starts, they can stop.** A close button that silently
> leaves work running is a lie.

A real home is ~560 MB once a brain, an agents database and an action log are
in it, so a backup is tens of seconds of disk and CPU — long enough that a
modal spinner is not an honest answer.

Both needs are one small object rather than two mechanisms, because they are
asked at the same moments: every place that can report "I have done *n* of *m*"
is also a place that can notice it was asked to stop. `Progress.step` does
both, and raises `CancelledError` so the caller's existing cleanup path — the one
that already deletes a half-written `.partial` — runs without knowing
cancellation exists.

A **leaf**: it imports nothing from Chitragupta. `agents/cancellation.py` solves
a similar problem one layer up, and `archive/` may not import `agents/`
([`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §3 rule 3) — so this does
not try to share it. The two are small enough that a shared abstraction would
cost more than the duplication.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

#: The phases a user is shown, in order. Named for what is happening to *their*
#: data, not for the implementation: "Compressing" rather than "tar -z".
READING = "Reading your data"
COMPRESSING = "Compressing"
ENCRYPTING = "Encrypting"
VERIFYING = "Checking the backup"
WRITING = "Putting your data back"


class CancelledError(Exception):
    """The user stopped this job.

    Not an error in the sense that something went wrong: the caller's cleanup
    should run exactly as it would for a failure, which is why this is raised
    rather than returned. It is deliberately **not** an `ArchiveError` — an
    archive that was never finished is not a damaged archive, and telling
    someone who pressed Stop that their backup is corrupt would be a lie.

    `connectors/retry.py` has a class of the same name for the same idea one
    package over. They are not shared because siblings do not import each other
    ([`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §3 rule 2), and both
    are four lines — moving one down to `core/` to save them would put a rule
    about retrying HTTP requests in the storage layer.
    """


@dataclass
class Progress:
    """Where a job is, and whether it has been asked to stop.

    `on_change` is called on every step so a caller can mirror this into
    whatever the UI polls. Kept as a callback rather than having this class
    know about job state, so the same object works for a backup, a restore, and
    a test that just wants to count.
    """
    phase: str = READING
    done: int = 0
    total: int = 0
    detail: str = ""
    on_change: Callable[[Progress], None] | None = None
    should_stop: Callable[[], bool] | None = None
    _finished: bool = field(default=False, repr=False)

    def enter(self, phase: str, total: int = 0, detail: str = "") -> None:
        """Begin a phase. Resets the counter, because `done` is within a phase."""
        self.phase = phase
        self.total = total
        self.done = 0
        self.detail = detail
        self._notify()

    def step(self, detail: str = "", by: int = 1) -> None:
        """Advance, and raise `CancelledError` if the user asked us to stop.

        The check happens *before* the increment so a cancelled job does not
        report having finished the unit it abandoned.
        """
        self.check()
        self.done += by
        if detail:
            self.detail = detail
        self._notify()

    def check(self) -> None:
        """Raise `CancelledError` if a stop was requested. Safe to call anywhere."""
        if self.should_stop and self.should_stop():
            raise CancelledError("stopped")

    def finish(self) -> None:
        self._finished = True
        self.phase = ""
        self._notify()

    @property
    def fraction(self) -> float:
        """0.0–1.0, or 0.0 when a phase has no countable total.

        A phase with no total is honest about it rather than inventing a
        percentage: `tar` does not report how far through it is, and a bar that
        sits at a made-up 50% is the spinner-with-no-end-state in disguise.
        """
        if self._finished:
            return 1.0
        if self.total <= 0:
            return 0.0
        return min(1.0, self.done / self.total)

    def snapshot(self) -> dict[str, object]:
        """What the status endpoint returns."""
        return {
            "phase": self.phase,
            "detail": self.detail,
            "done": self.done,
            "total": self.total,
            "fraction": round(self.fraction, 4),
        }

    def _notify(self) -> None:
        if self.on_change:
            self.on_change(self)


#: A progress object that reports nowhere and never cancels — so every function
#: below can take one unconditionally instead of writing `if progress:` at each
#: of its call sites.
def inert() -> Progress:
    return Progress()
