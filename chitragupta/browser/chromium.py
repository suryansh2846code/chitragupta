"""Where the browser lives, how it arrives, and how it is cleaned up.

`docs/BROWSER.md` chose a bundled Chromium driven over CDP, **downloaded on
first use rather than shipped**, and the download is what makes the decision
viable: the `.dmg` stays the size it is, and the browser arrives the way a vendor
CLI already does — one button, with progress, managed by us, never a terminal
instruction.

Three things this owns, and the third is a standing invariant rather than a
feature.

**The profile is ours.** `~/Library/Chitragupta/browser/profile`. The user signs in
here, once, to the sites they want an agent to reach — so the blast radius of a
mistake is the set of sites somebody deliberately signed into *in this app*, not
everything they have ever logged into. It is not `secrets.json`: cookies are not
settings and `settings.set_secret` should not learn about them. `brain.export()`
walks the memory table and nothing else, so a cookie jar cannot leave in a
backup — worth stating because a future export that walked the home directory
would turn that into a credential leak dressed as a feature.

**The download is a job.** A refresh must not kill one mid-way, and the UI needs
progress, so it runs on a thread with observable state — the same shape as
`models/cli_manager.py`, which solved this first.

**Anything we spawn, we clean up — across runs.** A browser is a long-lived
process holding a profile lock, and this app has already been here: 158 orphaned
`claude auth login` processes on one machine, because the only handle lived in
module state and every launch forgot the last one. So a Chromium we start is
recorded to disk through `models/login_processes.py` — the generic reaper that
problem produced — and reaped on launch and on quit. Only recorded PIDs are ever
signalled, each re-checked against the command line recorded with it.

**Playwright is optional, and the UI has to know.** A build without it is
perfectly healthy and simply cannot open a page, so `can_drive()` is a separate
question from `is_installed()` and `install_status()` reports both. Offering a
150 MB download that leads nowhere is the "control that cannot work" failure
`/CLAUDE.md` names first, in its most expensive form.

**Visible, not headless.** `docs/BROWSER.md`: a user who can watch is a user who
can stop — and MFA, which is never automated, needs a window a person can reach.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..log import get_logger, suppressed

log = get_logger(__name__)

#: Marker recorded with a spawned browser, so the reaper can recognise ours and
#: refuse to signal a PID that has since been reused by something else.
PROCESS_MARKER = "chitragupta-browser"


class BrowserNotReadyError(RuntimeError):
    """The browser is not installed or cannot be driven yet.

    Carries text written for the user, not for a log: it reaches a tool result
    and a panel, and "FileNotFoundError" is not an answer to "why did that not
    work".
    """


def root() -> Path:
    """Everything this package stores, under the app's own home."""
    return get_settings().home / "browser"


def profile_dir() -> Path:
    """The persistent profile — the thing that makes a site "signed in"."""
    return root() / "profile"


def install_dir() -> Path:
    return root() / "chromium"


def executable() -> Path | None:
    """The managed browser binary, or None if it is not installed.

    Looked up on disk rather than remembered in a setting: a user who deleted the
    directory has uninstalled it, and a stored "yes it is installed" would make
    the app insist otherwise. Detection is not consent in `models/`; here,
    detection is the *only* truth about installation.

    Globbed rather than hardcoded because the payload directory carries a build
    number (`chromium-1243`) and the app inside it is named by whoever packaged
    it. A path written out in full here is a path that breaks on the next
    version, silently, as "the browser is not installed".
    """
    for build in sorted(install_dir().glob("chromium-*"), reverse=True):
        for found in build.glob("*/*.app/Contents/MacOS/*"):
            if found.is_file():
                return found
    return None


def is_installed() -> bool:
    return executable() is not None


def _browsers_env() -> dict[str, str]:
    """Environment that points Playwright at *our* directory, not its own.

    Without this the browser lands in `~/Library/Caches/ms-playwright`, which is
    somebody else's namespace: uninstalling ours would either miss it or delete a
    copy another tool was relying on.
    """
    return {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(install_dir())}


def profile_sites() -> int:
    """How many origins the profile has state for, as a rough "is this set up".

    Deliberately a count and not a list: the list of sites a user has *signed
    into* is answered by `origins.list_grants()`, which is what they consented
    to. Reading their cookie jar to enumerate accounts would be the app snooping
    on itself.
    """
    if not profile_dir().exists():
        return 0
    return sum(1 for _ in profile_dir().glob("Default/Local Storage/**/*"))


# ── the download, as an observable job ──────────────────────────────────
@dataclass
class _Job:
    state: str = "idle"          # idle | running | done | error
    message: str = ""
    percent: int = 0
    error: str = ""
    started_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


_JOB = _Job()


def install_status() -> dict[str, Any]:
    """What the panel renders. Never raises — it is polled."""
    installed = is_installed()
    return {
        "installed": installed,
        "path": str(executable()) if installed else None,
        "profile": str(profile_dir()),
        "state": _JOB.state if _JOB.state != "idle" else (
            "done" if installed else "idle"),
        "message": _JOB.message,
        "percent": _JOB.percent if _JOB.state != "idle" else (
            100 if installed else 0),
        "error": _JOB.error,
        # Said up front, because 150 MB on a slow connection is a surprise worth
        # not having. The number is approximate and labelled as such.
        "approx_mb": 150,
        # Whether a page could actually be opened, which is not the same
        # question as whether the browser is downloaded: Playwright is an
        # optional dependency. The UI must not offer a download that leads
        # nowhere.
        "drivable": can_drive(),
    }


def start_install(fetch=None) -> dict[str, Any]:
    """Begin fetching the browser. Returns immediately; poll `install_status()`.

    `fetch` is the download implementation, injected so the job can be tested
    without reaching the network — the state machine is the part that has gone
    wrong before, not the bytes.
    """
    with _JOB.lock:
        if _JOB.state == "running":
            return install_status()
        if is_installed():
            return install_status()
        _JOB.state, _JOB.message, _JOB.percent = "running", "Starting…", 0
        _JOB.error, _JOB.started_at = "", time.time()

    worker = fetch or _download_chromium

    def run() -> None:
        def note(message: str, percent: int) -> None:
            _JOB.message, _JOB.percent = message, max(0, min(100, int(percent)))

        try:
            install_dir().mkdir(parents=True, exist_ok=True)
            worker(install_dir(), note)
            if not is_installed():
                raise BrowserNotReadyError(
                    "The download finished but no browser was found in it.")
            _JOB.state, _JOB.percent = "done", 100
            _JOB.message = "Ready."
        except Exception as exc:
            _JOB.state = "error"
            _JOB.error = str(exc)[:200]
            _JOB.message = f"Could not set up the browser: {_JOB.error}"
            log.warning("browser install failed: %s", exc)

    threading.Thread(target=run, daemon=True).start()
    return install_status()


#: Recognises Playwright's progress output — `|■■■   | 40% of 94.3 MiB`.
_PERCENT = re.compile(r"(\d{1,3})%")


def _download_chromium(target: Path, note) -> None:   # pragma: no cover - network
    """Fetch a Chromium build into `target`, reporting progress as it goes.

    Playwright's own downloader is used rather than a hand-rolled fetch: it knows
    which build matches the installed client, checks what it got, and unpacks it.
    Reimplementing that would mean pinning a build number in our source and
    discovering it had drifted when a user's browser refused to start.

    Run as a subprocess and **never as a shell string**, for the same reason
    `models/cli_manager.py` refuses to pipe a vendor's installer into a shell:
    the arguments are ours, and nothing a downloaded file contains can become a
    command.
    """
    note("Starting the download…", 1)
    process = subprocess.Popen(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=_browsers_env(), cwd=str(target))

    last = 0
    assert process.stdout is not None
    for line in process.stdout:
        found = _PERCENT.search(line)
        if found:
            # Scaled into 5–95: the bar reaching 100% while the app is still
            # unpacking reads as a hang, and starting at 0 reads as nothing
            # having happened.
            last = max(last, min(95, 5 + int(found.group(1)) * 9 // 10))
            note(f"Downloading the browser… {last}%", last)
        elif line.strip():
            log.debug("playwright install: %s", line.strip())

    if process.wait() != 0:
        raise BrowserNotReadyError(
            "The browser download did not finish. Check your connection and "
            "try again.")
    note("Finishing up…", 97)


def uninstall() -> bool:
    """Remove the browser. Leaves the profile alone — that is the user's
    sign-ins, and deleting them because they removed a binary would be losing
    their state to our tidying."""
    if not install_dir().exists():
        return False
    shutil.rmtree(install_dir(), ignore_errors=True)
    _JOB.state, _JOB.percent, _JOB.message = "idle", 0, ""
    return True


def forget_everything() -> bool:
    """Delete the profile: every sign-in, every cookie, all of it.

    The heavy half of "sign-out is a real control". Separate from `uninstall()`
    on purpose — one is about disk space, the other is about access, and a user
    who means the second must not have to guess that the first does it.
    """
    if not profile_dir().exists():
        return False
    shutil.rmtree(profile_dir(), ignore_errors=True)
    log.info("browser profile deleted at the user's request")
    return True


# ── the live session ────────────────────────────────────────────────────
def can_drive() -> bool:
    """Is the driver itself available, separately from the browser?

    Playwright is an optional dependency, so a build can be perfectly healthy and
    still unable to open a page. The UI reads this to decide whether offering a
    download would lead anywhere.
    """
    return importlib.util.find_spec("playwright") is not None


def open_session():
    """A `Session` over the managed browser.

    Raises `BrowserNotReadyError` with something a person can act on. The two
    refusals are separate because "set it up" and "this build cannot drive one"
    call for different things from the user, and conflating them sends somebody
    to press a button that will not help.
    """
    if not can_drive():
        raise BrowserNotReadyError(
            "This copy of the app was built without browsing support. "
            "Nothing is wrong with your setup.")
    if not is_installed():
        raise BrowserNotReadyError(
            "The browser has not been set up yet. Open Connectors and choose "
            "“Set up browsing” — it is a one-time download of about 150 MB.")

    from .session import Session

    return Session(shared_driver())


# ── one browser, and only one ───────────────────────────────────────────
#
# The profile allows exactly one Chromium. That is not a quirk to work around,
# it is the shape of the thing: a cookie jar with two writers is a cookie jar
# that loses sign-ins. But three callers each used to launch their own —
# `signin.begin()`, `forget_site()` and every agent through `open_session()` —
# and three more each closed one. Every browser failure this package has
# produced came out of that one fact:
#
#   * two launching at once  -> "Failed to create a ProcessSingleton"
#   * Chromium merging them  -> "Opening in existing browser session"
#   * one closing another's  -> "Target page, context or browser has been closed"
#   * one left open          -> every later launch refused, until the app quits
#
# So there is one browser, it is owned here, and the only thing that closes it
# is the app shutting down or the user deleting the profile. Callers borrow it.
# Nobody else launches, and nobody else closes.
_SHARED: Any = None
_SHARED_LOCK = threading.Lock()


def shared_driver():
    """The one browser, started if it is not running yet.

    Everything that drives Chromium goes through here — the sign-in flow, the
    sign-out, and every agent. They are not competing for a resource any more;
    they are taking turns on one, and the driver's own command queue is what
    makes "taking turns" true.

    This does **not** weaken the boundary. `Session` still wraps this and still
    checks every landing; signing in still bypasses `Session` rather than
    gaining an exception inside it. What changed is how many browsers exist,
    not who is allowed to ask one for a page.
    """
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = open_driver()
        return _SHARED


def reset_shared(*, close: bool = False) -> None:
    """Forget the shared browser, so the next caller gets a fresh one.

    `close=False` is for a browser that is already gone — the user closed the
    window, or it died — where calling `close()` on it would only raise. The
    point is to drop the handle: a driver's thread outlives its browser, so a
    stale handle is one that looks healthy and answers nothing.
    """
    global _SHARED
    with _SHARED_LOCK:
        doomed, _SHARED = _SHARED, None
    if close and doomed is not None:
        with suppressed("closing the shared browser"):
            doomed.close()


def open_driver():
    """The browser itself, with no boundary around it.

    Split out for the sign-in flow, which has to reach a site the user has not
    granted yet — that is the whole point of signing in. It drives the browser
    directly rather than gaining a bypass in `Session`, because `Session` is
    what agents hold and its origin check is the one thing standing between a
    page that says "now go to attacker.example" and an account. A boundary with
    an exception in it is not a boundary; a boundary nothing agent-facing can
    get past is.

    Everything that calls this is user-driven: a person pressed Connect.
    """
    from .driver import PlaywrightDriver

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(install_dir()))
    profile_dir().mkdir(parents=True, exist_ok=True)
    binary = executable()
    # **Visible, not headless.** `docs/BROWSER.md`: a user who can watch is a
    # user who can stop — and MFA, which is never automated, needs a window the
    # person can actually reach.
    return PlaywrightDriver(str(binary) if binary else None,
                            str(profile_dir()), headless=False)


def forget_site(host: str) -> bool:
    """Sign one site out of the profile, leaving the others signed in.

    Dropping a grant alone would leave the user signed in with a cookie jar
    that outlives the permission — a lie about what Disconnect did. This is the
    other half.

    Needs a browser, which is why it is here rather than in the delete handler:
    the alternative is editing Chrome's cookie database on disk, which is
    encrypted, locked while the browser runs, and exactly the thing
    `/CLAUDE.md` refuses to do to another product's files — our own included.

    It borrows the shared browser rather than starting one. Opening a second
    Chromium on this profile is refused by Chromium itself, and closing the one
    it opened used to end whatever page an agent had open.
    """
    clean = str(host or "").strip().lower().lstrip(".")
    if not clean or not is_installed():
        return False
    with suppressed("signing a site out of the browser profile"):
        shared_driver().clear_cookies("." + clean)
        log.info("signed out of %s", clean)
        return True
    return False


def reap() -> int:
    """Kill any browser we left behind, including from a previous run."""
    with suppressed("reaping a browser left over from a previous run"):
        from ..models.login_processes import reap_all

        return reap_all()
    return 0
