"""Getting the browser onto the machine, and saying so honestly while it happens.

The browser is a ~150 MB download that arrives on first use rather than in the
`.dmg`. `/CLAUDE.md` has two rules that meet here: never make the user wait
without telling them, and never show a control that cannot work. So the job
carries observable progress, and `drivable` is a *separate* question from
`installed` — a build without Playwright is healthy and simply cannot open a
page, and offering it a download would be the most expensive version of a button
that does nothing.

The download itself is injected, because the state machine is the part that has
gone wrong in this codebase before and the bytes are not.
"""
from __future__ import annotations

import pytest

from chitragupta.browser import chromium


@pytest.fixture(autouse=True)
def fresh_job(tmp_path, monkeypatch):
    monkeypatch.setattr(chromium, "root", lambda: tmp_path / "browser")
    chromium._JOB.state = "idle"
    chromium._JOB.message = ""
    chromium._JOB.percent = 0
    chromium._JOB.error = ""
    yield


def _finish(job_done):
    """Wait for the install thread without sleeping on a clock."""
    import time
    for _ in range(200):
        if chromium._JOB.state in job_done:
            return chromium._JOB.state
        time.sleep(0.01)
    raise AssertionError(f"job never left {chromium._JOB.state!r}")


# ── where things live ───────────────────────────────────────────────────
def test_nothing_is_installed_on_a_fresh_machine():
    assert chromium.is_installed() is False
    assert chromium.executable() is None


def test_the_browser_is_found_by_shape_not_by_a_written_out_path(tmp_path):
    """The payload directory carries a build number and the app inside it is
    named by whoever packaged it. A full path in the source is a path that breaks
    on the next version, silently, as "the browser is not installed"."""
    app = (chromium.install_dir() / "chromium-9999" / "chrome-mac-arm64"
           / "Some Chromium.app" / "Contents" / "MacOS" / "Some Chromium")
    app.parent.mkdir(parents=True)
    app.write_text("#!/bin/sh\n")

    assert chromium.is_installed() is True
    assert chromium.executable() == app


def test_the_newest_build_wins_when_two_are_present():
    """An interrupted upgrade leaves both. Picking the older one would run a
    browser the installed Playwright does not expect."""
    for build in ("chromium-1000", "chromium-1243"):
        app = (chromium.install_dir() / build / "mac" / "C.app" / "Contents"
               / "MacOS" / "C")
        app.parent.mkdir(parents=True)
        app.write_text("#!/bin/sh\n")

    assert "chromium-1243" in str(chromium.executable())


def test_the_browser_is_kept_in_our_own_directory():
    """Not `~/Library/Caches/ms-playwright`, which belongs to whoever else uses
    Playwright on this machine — uninstalling ours would either miss it or
    delete a copy something else depends on."""
    env = chromium._browsers_env()

    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(chromium.install_dir())


# ── the job ─────────────────────────────────────────────────────────────
def test_a_fresh_status_offers_the_download_and_names_its_size():
    status = chromium.install_status()

    assert status["installed"] is False
    assert status["state"] == "idle"
    assert status["approx_mb"] == 150, "said before it starts, not after"


def test_progress_is_reported_while_it_runs():
    """"Never make the user wait without telling them." A spinner with no end
    state is a bug."""
    seen = []

    def fake_download(target, note):
        note("Downloading the browser… 40%", 40)
        seen.append(chromium.install_status()["percent"])
        (target / "chromium-1" / "m" / "C.app" / "Contents" / "MacOS").mkdir(parents=True)
        (target / "chromium-1" / "m" / "C.app" / "Contents" / "MacOS" / "C").write_text("x")

    chromium.start_install(fetch=fake_download)

    assert _finish({"done", "error"}) == "done"
    assert seen == [40]
    assert chromium.install_status()["percent"] == 100


def test_a_download_that_produces_no_browser_is_a_failure_not_a_success():
    """The quiet version of this bug: the job says done, the panel hides the
    button, and nothing works with no explanation anywhere."""
    chromium.start_install(fetch=lambda target, note: None)

    assert _finish({"done", "error"}) == "error"
    assert "no browser was found" in chromium.install_status()["error"]


def test_a_failed_download_says_what_to_do():
    def boom(target, note):
        raise chromium.BrowserNotReadyError(
            "The browser download did not finish. Check your connection and "
            "try again.")

    chromium.start_install(fetch=boom)

    assert _finish({"done", "error"}) == "error"
    assert "try again" in chromium.install_status()["message"]


def test_a_second_install_does_not_start_a_second_download():
    """The button is pressable twice, and 150 MB twice over a slow link is the
    kind of thing a user notices on their bill."""
    started = []

    def slow(target, note):
        started.append(1)
        import time
        time.sleep(0.2)

    chromium.start_install(fetch=slow)
    chromium.start_install(fetch=slow)
    _finish({"done", "error"})

    assert started == [1]


# ── what a session refuses, and why ─────────────────────────────────────
def test_without_playwright_the_refusal_says_the_build_not_the_setup(monkeypatch):
    """"Set it up" and "this build cannot" call for different things from the
    user. Conflating them sends somebody to press a button that will not help."""
    monkeypatch.setattr(chromium, "can_drive", lambda: False)

    with pytest.raises(chromium.BrowserNotReadyError) as caught:
        chromium.open_session()

    assert "without browsing support" in str(caught.value)
    assert "Nothing is wrong with your setup" in str(caught.value)


def test_with_playwright_but_no_browser_it_points_at_the_button(monkeypatch):
    monkeypatch.setattr(chromium, "can_drive", lambda: True)

    with pytest.raises(chromium.BrowserNotReadyError) as caught:
        chromium.open_session()

    assert "Set up browsing" in str(caught.value)
    assert "150 MB" in str(caught.value)


def test_drivable_is_a_different_question_from_installed(monkeypatch):
    monkeypatch.setattr(chromium, "can_drive", lambda: True)

    status = chromium.install_status()

    assert status["drivable"] is True
    assert status["installed"] is False


# ── the user's own sign-ins ─────────────────────────────────────────────
def test_removing_the_browser_leaves_the_sign_ins_alone():
    """One is about disk space and the other is about access. Losing somebody's
    sign-ins because they reclaimed 150 MB is losing their state to our
    tidying."""
    chromium.profile_dir().mkdir(parents=True)
    (chromium.profile_dir() / "Cookies").write_text("x")
    chromium.install_dir().mkdir(parents=True, exist_ok=True)

    chromium.uninstall()

    assert chromium.profile_dir().exists()
    assert (chromium.profile_dir() / "Cookies").exists()


def test_forgetting_everything_really_removes_the_profile():
    chromium.profile_dir().mkdir(parents=True)
    (chromium.profile_dir() / "Cookies").write_text("x")

    assert chromium.forget_everything() is True
    assert chromium.profile_dir().exists() is False


def test_forgetting_twice_is_not_an_error():
    assert chromium.forget_everything() is False


# ── running with no window at all ────────────────────────────────────────
#
# The browser's own window is minimised and the page is shown inside the app,
# which is what "visible, not headless" was always about — being able to watch
# and to stop. A minimised window is still an application, though, so it still
# sits in the Dock, and a user who wanted the browser gone wanted it gone.
#
# Hidden means headless. Measured rather than assumed: the only thing a page can
# tell is that the user-agent says `HeadlessChrome` instead of `Chrome` —
# `navigator.webdriver` is already true either way, and the brands, plugins and
# WebGL renderer are identical. What it really costs is the window, which is why
# it is the user's choice per machine and not ours for everybody.
def test_the_browser_has_a_window_unless_the_user_says_otherwise():
    from chitragupta.browser import chromium

    assert chromium.runs_hidden() is False


def test_hiding_it_is_remembered_on_disk():
    """On disk for `executable()`'s reason: a stored "yes it is hidden" that
    disagreed with how the browser actually started is worse than asking."""
    from chitragupta.browser import chromium

    assert chromium.set_hidden(True) is True
    assert chromium.runs_hidden() is True

    assert chromium.set_hidden(False) is False
    assert chromium.runs_hidden() is False


def test_the_choice_reaches_the_browser_that_gets_started(monkeypatch):
    """The flag is read at launch, so this is the only place it can be wrong."""
    from chitragupta.browser import chromium

    made = {}
    monkeypatch.setattr(
        "chitragupta.browser.driver.PlaywrightDriver",
        lambda binary, profile, headless=False: made.update(headless=headless))

    chromium.set_hidden(True)
    chromium.open_driver()
    assert made["headless"] is True

    chromium.set_hidden(False)
    chromium.open_driver()
    assert made["headless"] is False


def test_changing_it_restarts_the_browser(monkeypatch):
    """A switch that appeared to do nothing until the next launch is a switch
    people press twice. The choice is made at launch, so it has to."""
    from chitragupta.browser import chromium

    monkeypatch.setattr(chromium, "open_driver", lambda: object())
    first = chromium.shared_driver()

    chromium.set_hidden(True)

    assert chromium.shared_driver() is not first
    chromium.set_hidden(False)


def test_the_panel_is_told_which_way_it_is_running():
    """The screen draws two controls from this, and "Open window" must not be
    offered when there is no window to open."""
    from chitragupta.browser import chromium

    assert chromium.install_status()["hidden"] is False
    chromium.set_hidden(True)
    assert chromium.install_status()["hidden"] is True
    chromium.set_hidden(False)
