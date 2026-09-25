"""Where the Claude CLI is on this machine.

One function, in its own file, for a structural reason. `accounts` has to know
whether the CLI is installed in order to detect a Claude account; the provider
has to know in order to run one. With the finder living inside the provider,
`accounts` imported `claude_code` and `claude_code` imported `accounts` —
the last cycle in `models/`, over a call to `shutil.which`.

Detection belongs below both. `claude_code` re-exports the name, so nothing
that already says `from .claude_code import find_claude` changes.

Note the bin-dir list stays here with the function that uses it, and is
deliberately NOT merged with the equivalents in `cursor.py` and `grok_cli.py`:
each also drives its own fallback scan, so one shared list would send Cursor's
finder through `~/.grok/bin`. See `COMPLEXITY_AUDIT.md` D.2.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .cli_login import augmented_path

# Bin dirs GUI apps miss: apps launched from Finder/.app get a minimal PATH
# (/usr/bin:/bin:/usr/sbin:/sbin), so Homebrew, npm-global and the Claude Code
# local install are invisible to shutil.which. Search them explicitly.
_EXTRA_BIN_DIRS = [
    "/opt/homebrew/bin", "/usr/local/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".claude" / "local"),
    str(Path.home() / ".npm-global" / "bin"),
    str(Path.home() / "bin"),
    "/opt/homebrew/sbin",
]


def _augmented_path() -> str:
    return augmented_path(_EXTRA_BIN_DIRS)


def find_claude() -> str | None:
    """Locate the `claude` binary even when PATH is the stripped GUI default."""
    found = shutil.which("claude", path=_augmented_path())
    if found:
        return found
    for d in _EXTRA_BIN_DIRS:
        cand = Path(d) / "claude"
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None
