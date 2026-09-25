"""Logging, and a place to put failures we intend to survive.

The codebase is full of operations that genuinely may fail without the user
caring: a Keychain that is locked, a vendor CLI that is not installed, a
metadata probe for a provider nobody has connected. Written as
`try: ... except Exception: pass`, each of those is correct at runtime and
invisible afterwards — which is how a user's "it just doesn't work" becomes
unfalsifiable, because the one place that knew what went wrong threw it away.

`suppressed()` keeps the runtime behaviour exactly (the failure is still not
raised) and keeps the evidence. A rotating file in the app's home means a bug
report has something in it even when nobody was watching a terminal, and
`CHITRAGUPTA_DEBUG=1` additionally mirrors everything to stdout for whoever is.

**The logger is permissive; the handlers decide.** That order is load-bearing:
a logger discards a record below its own level before any handler sees it, so a
root at INFO threw away every `suppressed()` line — which is to say all of the
evidence this module was written to keep. See `configure()`.

What reaches the user is `api/routes/diagnostics.py`, because a log nobody can
read is the same as no log: "it just doesn't work" stays unfalsifiable if the
answer is in a file the user would need a terminal to open.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_ROOT = "chitragupta"
_configured = False

#: The file `configure()` writes to. Named once, because two places now need it
#: — the handler that writes it and the endpoint that reads it back — and a
#: second spelling of a path is a bug that only shows up as an empty panel.
LOG_FILENAME = "chitragupta.log"


def log_file() -> Path | None:
    """The current log file, or None if this machine has nowhere to write one."""
    directory = _log_dir()
    return None if directory is None else directory / LOG_FILENAME


def _log_dir() -> Path | None:
    """The Chitragupta home's log directory, or None if it cannot be written.

    Reads `home.py` rather than `config`: logging must not drag settings in at
    import time, and asking `config` for the path made the two a cycle over one
    directory name.
    """
    try:
        from .home import default_home

        path = default_home() / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return None


def configure(force: bool = False) -> None:
    """Attach handlers once, on first use.

    Idempotent, because every entry point (`serve`, `app`, the test suite, a
    script) may be the first to log and none of them owns the others.
    """
    global _configured
    if _configured and not force:
        return
    _configured = True

    root = logging.getLogger(_ROOT)
    # **The logger is permissive and the handlers are selective**, which is the
    # way round that makes the promise in this module's docstring true.
    #
    # It used to be the other way: the root sat at INFO unless the debug
    # environment variable was set, and a logger drops a record below its own
    # level *before any handler sees it*. `suppressed()` logs at DEBUG. So every
    # swallowed failure — 107 call sites, the entire reason this module exists —
    # was discarded on the machine of every user who had not set an environment
    # variable they have never heard of. The file said nothing precisely when a
    # bug report needed it to say something.
    root.setLevel(logging.DEBUG)
    root.propagate = False
    if root.handlers and not force:
        return

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    # Only real problems reach the terminal — the desktop app's stdout is not a
    # log viewer, and noise there trains people to ignore it. `CHITRAGUPTA_DEBUG`
    # now means "show me everything *here*", which is what a developer setting
    # it actually wants; it no longer decides what is recorded at all.
    stream = logging.StreamHandler()
    stream.setLevel(logging.DEBUG if os.environ.get("CHITRAGUPTA_DEBUG")
                    else logging.WARNING)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    directory = _log_dir()
    if directory is not None:
        try:
            from logging.handlers import RotatingFileHandler

            handler = RotatingFileHandler(
                directory / LOG_FILENAME, maxBytes=2_000_000, backupCount=3,
                encoding="utf-8")
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(fmt)
            root.addHandler(handler)
        except Exception:
            # A log that cannot be written must never stop the app starting.
            pass


def get_logger(name: str) -> logging.Logger:
    """A logger under the `chitragupta` root. Pass `__name__`."""
    configure()
    if name == _ROOT or name.startswith(_ROOT + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT}.{name}")


@contextmanager
def suppressed(doing: str, *, logger: logging.Logger | None = None,
               level: int = logging.DEBUG) -> Iterator[None]:
    """Run a block whose failure is survivable, and record it rather than lose it.

    `doing` is a plain-language fragment naming the attempt, so the line reads
    as a sentence: `suppressed("reading the saved port")` →
    *"failed while reading the saved port: [Errno 2] ..."*.

    Deliberately catches `Exception` and not `BaseException`: a KeyboardInterrupt
    or a SystemExit is not a survivable failure and must keep travelling.
    """
    try:
        yield
    except Exception as exc:
        (logger or get_logger("suppressed")).log(
            level, "failed while %s: %s", doing, exc, exc_info=level <= logging.DEBUG)
