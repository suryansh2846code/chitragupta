"""A scratchpad for arithmetic, parsing and small transforms.

Paired with `file_tools`, this is what turns "here's how you'd reconcile that
CSV" into the reconciled CSV. A model doing arithmetic in its head is a model
guessing; a model that can run four lines of Python is not.

**This runs code, so read the boundary before widening it.**

The agents here read email, issues and messages written by other people. A
sentence in one of them is a plausible attempt at "run this script", and unlike
every other tool in this package, the blast radius of getting that wrong is not
a wrong answer. So:

* **Not in the base tool set.** No shipped agent has it. The user puts
  `run_python` in an agent's tool list themselves, the same way `forget_fact`
  works, and that choice is the consent.
* **A separate process, never this one.** `-I` isolates it from the environment
  and `-S` from site-packages, so the snippet cannot reach Chitragupta's own
  modules, the brain, or the user's credentials by importing them.
* **One allowlisted package back in: numpy.** `-S` also removed the thing this
  tool exists for — "work out my weight trend from this export" is arithmetic
  over a few thousand numbers, and pure Python does it slower, longer and less
  correctly. It is linked in by name into a scratch directory, not restored by
  putting site-packages on the path: everything else in there is the app's own
  supply chain, including the modules that know where the credentials are.
  `ALLOWED_PACKAGES` is the list, and nothing on it can open a socket.
* **A temporary working directory**, thrown away afterwards, so a script that
  writes files writes them somewhere that does not matter. Writing somewhere
  that *does* matter is `file_tools`' job, where the user has drawn a boundary.
* **`sandbox-exec` when it is there.** macOS still ships it. The profile denies
  the network outright and confines writes to the scratch directory. It is
  deprecated, so this is belt-and-braces rather than the only guard — if it is
  missing, the isolation above still holds and the timeout still fires.
* **A wall-clock timeout and a capped output**, because a loop that never ends
  and a script that prints forever are the two accidents that cost nothing to
  prevent.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..log import get_logger, suppressed
from .results import ToolResult

log = get_logger(__name__)

#: Long enough for real work on a data file, short enough that a runaway loop is
#: an inconvenience rather than a hung app.
TIMEOUT_SECONDS = 20

#: The output is read back by a model, so it is charged for.
MAX_OUTPUT_CHARS = 8_000

#: Denies the network and confines writes to the scratch directory. Reads stay
#: open: a snippet that cannot read the standard library cannot import anything.
_SANDBOX_PROFILE = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
  (subpath "{scratch}")
  (subpath "/private/var/folders")
  (subpath "/tmp")
  (literal "/dev/null"))
"""


def _sandbox_prefix(scratch: Path) -> list[str]:
    """`sandbox-exec` invocation if this machine has it, else nothing."""
    binary = shutil.which("sandbox-exec")
    if not binary:
        log.debug("sandbox-exec not available; relying on process isolation")
        return []
    return [binary, "-p", _SANDBOX_PROFILE.format(scratch=scratch)]


#: Packages a snippet may import, beyond the standard library.
#:
#: `-S` removes site-packages wholesale, which is the isolation — but it also
#: removed the one thing this tool exists for. "Work out my weight trend from
#: this export" is arithmetic over a few thousand numbers, and a model asked to
#: do it in pure Python writes a loop that is slower, longer and likelier to be
#: wrong than one line of numpy. The tool could run code and not do the job.
#:
#: Named individually rather than by putting site-packages back: everything
#: else in there is the app's own supply chain — `httpx`, the vendor SDKs, the
#: modules that know where the user's credentials live. An allowlist of one is
#: a boundary; a path entry is not. Nothing is added here that can open a
#: socket or read a keychain.
ALLOWED_PACKAGES = ("numpy",)


def _link_allowed_packages(scratch: Path) -> Path | None:
    """A directory holding a symlink to each allowed package, or None.

    A directory of links rather than site-packages itself, because adding
    site-packages to `sys.path` would make every dependency the app has
    importable — which is the thing `-S` was there to prevent.
    """
    libs = scratch / "_libs"
    libs.mkdir(exist_ok=True)
    linked = 0
    for name in ALLOWED_PACKAGES:
        with suppressed("linking an allowed package into the code sandbox"):
            module = __import__(name)
            source = Path(module.__file__ or "").parent
            if source.is_dir():
                (libs / name).symlink_to(source, target_is_directory=True)
                linked += 1
    return libs if linked else None


def run_python(code: str) -> ToolResult:
    """Run a short Python snippet and return whatever it printed."""
    source = (code or "").strip()
    if not source:
        return ToolResult.failed("Give some code to run.")

    with tempfile.TemporaryDirectory(prefix="chitragupta-scratch-") as tmp:
        scratch = Path(tmp)
        libs = _link_allowed_packages(scratch)
        script = scratch / "snippet.py"
        script.write_text(source, encoding="utf-8")
        # A separate runner, not a preamble prepended to the snippet. The
        # traceback is handed back to the model precisely so it can fix its own
        # code, and a prepended line shifts every number in it by one — which
        # points the model at the line above the mistake, on every single
        # failure. The path goes on `sys.path` here rather than through
        # PYTHONPATH because `-I` ignores the environment, which is most of why
        # it is there.
        if libs:
            entry = scratch / "run.py"
            entry.write_text(
                f"import sys\n"
                f"sys.path.insert(0, {str(libs)!r})\n"
                f"source = open({str(script)!r}, encoding='utf-8').read()\n"
                f"exec(compile(source, {str(script)!r}, 'exec'), "
                f"{{'__name__': '__main__'}})\n",
                encoding="utf-8")
        else:
            entry = script

        # -I: ignore environment and the user's site dir. -S: no site-packages.
        # Between them the snippet cannot import Chitragupta, reach the brain, or
        # read a credential file by importing the module that knows where it is.
        # What it CAN import is `ALLOWED_PACKAGES` and nothing else.
        argv = [*_sandbox_prefix(scratch), sys.executable, "-I", "-S", str(entry)]
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                cwd=str(scratch), check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failed(
                f"The code ran for {TIMEOUT_SECONDS} seconds without finishing, "
                "so it was stopped. Try a smaller step.")
        except OSError as exc:                     # pragma: no cover - defensive
            return ToolResult.failed(f"Could not run that: {exc}")

        out = (done.stdout or "").strip()
        err = (done.stderr or "").strip()

    if done.returncode != 0:
        # The traceback is what the model needs to fix its own code, so it is
        # handed back — but the last line first, because that is the error.
        tail = err.splitlines()[-1] if err else f"exit code {done.returncode}"
        detail = err[-1500:] if err else ""
        return ToolResult.failed(f"The code failed: {tail}\n\n{detail}".strip())

    if not out:
        return ToolResult(
            "The code ran without error but printed nothing. "
            "Use print() for anything you want back.")
    if len(out) > MAX_OUTPUT_CHARS:
        return ToolResult(
            out[:MAX_OUTPUT_CHARS]
            + f"\n\n[Cut here — {len(out):,} characters were printed.]",
            truncated=True)
    return ToolResult(out)
