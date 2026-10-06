"""Desktop app — run Chitragupta as a native window instead of a browser tab.

Starts the FastAPI server in a background thread and shows the UI in a native
webview window. `chitragupta app` launches it; the macOS .app bundle calls the
same entry point.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .log import suppressed


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _bind(host: str, port: int) -> socket.socket:
    """Bind a listening socket the way uvicorn would.

    SO_REUSEADDR is not a detail here. A server that has just exited leaves
    connections in TIME_WAIT, and a plain bind on that port fails while uvicorn's
    would succeed — so a probe without it calls the port taken one launch after
    every quit.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(128)
    return s


def _reserve_port(host: str) -> tuple[int, socket.socket]:
    """Claim the port we will serve on, and hold it.

    Reusing one port across launches is what keeps the webview origin stable;
    localStorage lives on the origin, so a different port means the onboarding
    flag, the chosen model and the lead agent all silently vanish and the app
    looks empty on launch. Returning the bound socket also closes the gap
    between checking a port and serving on it.
    """
    from .config import get_settings
    pf = get_settings().home / ".port"
    try:
        sock = _bind(host, int(pf.read_text().strip()))
    except Exception:
        sock = _bind(host, 0)        # genuinely taken (or never saved) → fresh one
    port = sock.getsockname()[1]
    with suppressed("pf.parent.mkdir(parents=True, exist_ok=True) …"):
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(str(port))
    return port, sock


#: Shown as the alert's bold first line when the server never came up.
LAUNCH_FAILURE_TITLE = "Chitragupta could not start"


def _log_path() -> Path:
    from .config import get_settings

    return get_settings().home / "logs" / "chitragupta.log"


def launch_failure_message(log_path: Path) -> str:
    """What the user reads when the server did not come up.

    It has one job: leave them with something to *do*. "Failed to start" on its
    own is the same dead end as saying nothing, only louder.
    """
    return (
        "The Chitragupta server did not start, so there is nothing to show yet.\n\n"
        "This is usually temporary — try opening Chitragupta again. If it keeps "
        "happening, the log below records what went wrong:\n\n"
        f"{log_path}"
    )


def _show_launch_failure_alert(log_path: Path) -> None:  # pragma: no cover - needs AppKit
    """The native half of `report_launch_failure`.

    Unlike the sign-in card, this one *does* activate the app
    (`activateIgnoringOtherApps_`). The card deliberately does not, because it
    appears while the user is working in another app and stealing focus there is
    rude — see docs/DESKTOP-SIGNIN.md. This is the opposite situation: the user
    just double-clicked the icon and is waiting for a window. An alert that
    opens behind whatever they had in front is the same silence with extra
    steps.

    `run_app` is still on the main thread here and the GUI loop has not started,
    so this is the one Cocoa call in this module that must NOT go through
    `AppHelper.callAfter` — there is no run loop yet for it to be delivered to.
    """
    import AppKit

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

    alert = AppKit.NSAlert.alloc().init()
    alert.setAlertStyle_(AppKit.NSAlertStyleCritical)
    alert.setMessageText_(LAUNCH_FAILURE_TITLE)
    alert.setInformativeText_(launch_failure_message(log_path))
    alert.addButtonWithTitle_("Show Log")
    alert.addButtonWithTitle_("Quit")

    app.activateIgnoringOtherApps_(True)
    if alert.runModal() == AppKit.NSAlertFirstButtonReturn:
        AppKit.NSWorkspace.sharedWorkspace().selectFile_inFileViewerRootedAtPath_(
            str(log_path), "")


def report_launch_failure() -> None:
    """Say the server never started, somewhere the user will actually see it.

    `print()` was the entire report here, and a bundled `.app` is not launched
    from a shell: stdout goes nowhere. The user double-clicked the icon and
    *nothing happened* — no window, no dialog, not even a dock bounce. It reads
    as a broken app, and it leaves neither them nor us anything to act on.

    A source checkout keeps the print, because there is a terminal and that is
    where everything else already went. A frozen bundle gets a real alert.
    """
    print("Chitragupta server failed to start.")
    if not getattr(sys, "frozen", False):
        return
    # An alert that itself explodes must not replace one silent failure with
    # another, so the path is best-effort and the print above always happens.
    with suppressed("showing the launch-failure alert"):
        _show_launch_failure_alert(_log_path())


def _install_edit_menu_now() -> None:
    """Main-thread half of `_install_edit_menu`.

    macOS delivers ⌘C, ⌘V, ⌘A and friends through the Edit menu's key
    equivalents, walking the responder chain until something answers. pywebview
    builds no menu bar at all, so there was no Edit menu, no key equivalents,
    and nothing answered: text in the conversation selected fine and then could
    not be copied, and the composer could not be pasted into.

    The items use the standard AppKit selectors with a nil target, which is
    what sends them down the responder chain to whatever has focus — the
    WKWebView for the conversation, the text field for the composer. A Python
    callback (what `webview.menu.MenuAction` offers) cannot do this: it takes
    no key equivalent, and it would have to guess which view to act on.
    """
    import AppKit

    app = AppKit.NSApplication.sharedApplication()
    main = app.mainMenu()
    if main is None:
        return
    for i in range(main.numberOfItems()):
        if main.itemAtIndex_(i).title() == "Edit":
            return                      # already there — do not add a second

    items = [
        ("Undo", "undo:", "z"),
        ("Redo", "redo:", "Z"),
        (None, None, None),
        ("Cut", "cut:", "x"),
        ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"),
        (None, None, None),
        ("Select All", "selectAll:", "a"),
    ]
    edit = AppKit.NSMenu.alloc().initWithTitle_("Edit")
    for title, selector, key in items:
        if title is None:
            edit.addItem_(AppKit.NSMenuItem.separatorItem())
            continue
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, selector, key)
        edit.addItem_(item)

    holder = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
    holder.setSubmenu_(edit)
    main.addItem_(holder)


def _install_edit_menu() -> None:
    """Give the app an Edit menu once AppKit is up.

    Every Cocoa mutation in this app goes through AppHelper.callAfter: calling
    these setters off the main thread hangs the process, and pywebview runs
    this callback on a worker.
    """
    with suppressed("installing the Edit menu"):
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_install_edit_menu_now)



#: What a save panel should open with when the page offers no name.
BACKUP_SUFFIX = ".cgarch"
DEFAULT_BACKUP_NAME = "chitragupta-backup" + BACKUP_SUFFIX


def backup_filename(suggested: str = "") -> str:
    """The filename to put in a save panel's name field.

    Module level, and so is `first_path` below, because `_AppBridge` is nested
    inside `run_app` and needs a live pywebview window — which makes the two
    parts of the file pickers that can actually be wrong unreachable from a
    test unless they live out here.

    The suffix is appended rather than required: the page suggests a name, and
    a user who clears the field and types `brain` should still get a file the
    restore screen will recognise.

    **It is a name, never a path.** This is the one bridge method that takes an
    argument from the page, and `tests/test_full_disk_access_refusal.py` pins
    why the others do not: an argument is a thing a page can redirect, and
    `open_privacy_settings` deliberately takes none so there is nothing to
    point somewhere else. A *filename* cannot redirect a save panel — the user
    still chooses the directory and confirms — but only as long as it stays a
    filename, so every separator and every `..` is stripped here rather than
    handed to a filesystem API to interpret.
    """
    raw = (suggested or "").strip().replace("\\", "/")
    # The basename, and nothing above it. `PurePosixPath(...).name` drops every
    # directory part, so "../../etc/passwd" becomes "passwd" and "/" becomes "".
    name = PurePosixPath(raw).name.strip() if raw else ""
    if name in ("", ".", ".."):
        name = DEFAULT_BACKUP_NAME
    return name if name.endswith(BACKUP_SUFFIX) else name + BACKUP_SUFFIX


def first_path(chosen: object) -> str:
    """One path out of whatever a pywebview dialog answered with.

    `OPEN_DIALOG` returns a sequence and `SAVE_DIALOG` has returned a bare
    string in some versions and a one-item sequence in others, so neither shape
    is assumed. Cancelling gives `None` or an empty sequence, and that is not
    an error — it must come back as `""` so the page leaves whatever the user
    had already typed alone.
    """
    if not chosen:
        return ""
    if isinstance(chosen, str):
        return chosen
    try:
        first = next(iter(chosen))      # type: ignore[call-overload]
    except (TypeError, StopIteration):
        return ""
    return str(first) if first else ""

def run_app(dev: bool = False) -> None:
    try:
        import webview
    except ImportError:
        raise SystemExit(
            "The desktop window needs pywebview. Install it with:\n"
            "    uv pip install -e '.[desktop]'   (or: pip install pywebview)\n"
            "Or run the browser version instead:  chitragupta serve") from None

    from .config import get_settings

    get_settings()          # load settings / ensure the home dir exists

    # A sign-in the user walked away from leaves its CLI running forever. Clear
    # out anything a previous launch left behind before starting another.
    from .models import login_processes
    strays = login_processes.reap_all()
    if strays:
        print(f"Cleaned up {strays} unfinished sign-in "
              f"process{'es' if strays != 1 else ''} from a previous session.")

    host = "127.0.0.1"
    # Held until the server takes it over, so nothing can slip in between.
    port, sock = _reserve_port(host)

    server = None
    proc = None
    if dev:
        # Run the backend as a uvicorn subprocess with --reload so Python edits
        # hot-reload — then Cmd+R in the window picks up frontend + backend both.
        import subprocess
        sock.close()             # the reloader subprocess binds it itself
        pkg = str(Path(__file__).resolve().parent)
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "chitragupta.api.app:app",
             "--host", host, "--port", str(port),
             "--reload", "--reload-dir", pkg, "--log-level", "warning"])
    else:
        import uvicorn

        from .api.app import app as fastapi_app
        config = uvicorn.Config(fastapi_app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        # Serve on the socket we already own rather than binding again — a second
        # bind can lose the port to whatever grabbed it in the meantime, and the
        # window would then load a stranger's server.
        threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True).start()

    if not _wait_for_port(host, port, timeout=30.0 if dev else 15.0):
        if proc:
            proc.terminate()
        report_launch_failure()
        return

    from . import hud
    hud._attach_to_api()

    class _AppBridge:
        """Reachable from the page as `window.pywebview.api`.

        The floating sign-in card is raised from here rather than from the API,
        because under --dev the backend is a separate process with no handle on
        the webview.
        """

        #: Assigned after `create_window` below — the bridge has to exist first,
        #: because it is passed in as `js_api`. Deliberately untyped: `webview`
        #: is imported inside this function (it is an optional extra), so there
        #: is no module-level name for mypy to annotate against.
        window: Any = None

        def open_signin_hud(self, provider: str, brand: str, auth_url: str = "") -> bool:
            return hud.open_signin(provider, brand, auth_url)

        def close_signin_hud(self) -> None:
            hud.close()

        def open_privacy_settings(self) -> bool:
            """Open System Settings at Full Disk Access.

            Here rather than behind an endpoint on purpose. `/api/open-browser`
            accepts `http(s)` only — handing the system opener an arbitrary
            scheme would let any page reach any URL handler an installed app
            registered — and that guard is right. This takes **no argument**:
            the URL is a module constant, so there is nothing for a caller to
            redirect and nothing to widen.

            `/usr/bin/open` rather than AppKit: pywebview runs `js_api` calls on
            a worker thread, and this way the question of which Cocoa calls are
            safe off the main thread does not arise at all.
            """
            import subprocess

            from .connectors.permissions import FULL_DISK_ACCESS_PANE
            with suppressed("opening the Full Disk Access pane"):
                subprocess.run(["/usr/bin/open", FULL_DISK_ACCESS_PANE], check=True)
                return True
            return False

        # ── picking a backup file ───────────────────────────────────────────
        #
        # Here rather than behind an endpoint, for the same reason as
        # `open_privacy_settings` above and one more. A real `NSOpenPanel`
        # returns **only what the user chose**, so nothing has to list the
        # user's files to offer a picker — and `/api/fs/browse` can stay
        # directories-only, which is what it was deliberately written as.
        #
        # **Safe from this worker thread, and only because we let pywebview do
        # the dispatching.** `js_api` calls arrive off the main thread, and
        # CLAUDE.md's desktop invariant is that every Cocoa mutation goes
        # through `AppHelper.callAfter` or the process hangs. pywebview's
        # `create_file_dialog` already does exactly that when its
        # `main_thread` argument is left at its default of False: it
        # `callAfter`s the panel onto the main thread and blocks on a
        # semaphore until it closes. **Never pass `main_thread=True` from
        # here** — that branch runs `runModal()` on the calling thread, which
        # is the hang the invariant is about.

        def pick_backup_file(self) -> str:
            """Ask the user for an existing backup. "" if they cancelled."""
            if self.window is None:
                return ""
            return first_path(self.window.create_file_dialog(
                webview.OPEN_DIALOG,
                directory=str(get_settings().home),
                allow_multiple=False,
                file_types=("Chitragupta backup (*.cgarch)", "All files (*.*)"),
            ))

        def pick_backup_destination(self, suggested_name: str = "") -> str:
            """Ask the user where to write a backup. "" if they cancelled.

            A save panel rather than a folder picker: the user gets to name the
            file, and macOS handles "this already exists, replace it?" — which
            is a question we would otherwise have to ask badly.
            """
            if self.window is None:
                return ""
            return first_path(self.window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory=str(get_settings().home),
                save_filename=backup_filename(suggested_name),
            ))

    bridge = _AppBridge()
    window = webview.create_window(
        "Chitragupta" + (" (dev)" if dev else ""),
        f"http://{host}:{port}",
        width=1280, height=860, min_size=(920, 620),
        js_api=bridge,
    )
    # The file dialogs need a window to hang off, and the bridge had to exist
    # before there was one to give it. Same handover `hud.configure` does.
    bridge.window = window
    hud.configure(f"http://{host}:{port}", window)
    # Built now, on the main thread, and shown later from the page.
    hud.prepare(webview.create_window)

    try:
        # `func` runs once the GUI loop is up, which is the earliest point an
        # NSApplication exists to hang a menu on.
        webview.start(_install_edit_menu)   # blocks until the window is closed
    finally:
        # Quitting must not leave a login process waiting for a callback that
        # will never come — that is how 158 of them piled up on one machine.
        login_processes.reap_all()
        if server is not None:
            server.should_exit = True
        if proc is not None:
            proc.terminate()
