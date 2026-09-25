"""Where Chitragupta keeps everything on this machine.

One fact, in its own module, because the two modules that need it are both
meant to be leaves and were importing each other to share it: `config` reads
settings and uses `log.suppressed`; `log` needs somewhere to write and was
asking `config` for the home directory. A two-module cycle over one path.

Kept here rather than duplicated in `log`, because "where the home is" is a
rule and a rule written twice is a rule that drifts — and the copy that drifts
is the one nobody reads.

Deliberately dependency-free: stdlib only, no settings, no logging. Anything
that grows here should probably be in `config` instead.
"""
from __future__ import annotations

import os
from pathlib import Path


def default_home() -> Path:
    """The Chitragupta home. macOS-native by default.

    `CHITRAGUPTA_HOME` overrides it, which is how the test suite keeps a run
    out of the real one.
    """
    override = os.environ.get("CHITRAGUPTA_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "posix" and Path.home().joinpath("Library").exists():
        return Path.home() / "Library" / "Chitragupta"
    # Linux / Windows / headless fallback
    return Path.home() / ".chitragupta"
