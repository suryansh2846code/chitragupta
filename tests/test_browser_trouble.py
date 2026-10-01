"""One browser failure, named once, for every audience.

The reported bug: an agent was asked to open WhatsApp Web and the user was told
*"The browser itself failed to start — not a sign-in or permission issue on your
end."* The browser had started twenty seconds earlier and was still running the
next afternoon. A navigation that ran out of time had fallen through a
classifier that had words for a locked profile and for a closed browser and
treated everything else as "would not start".

Fixing that in `agents/browse_tools` left `api/routes/browser` answering the
identical failure with *"Page.goto: Timeout 20000ms exceeded. Call log: -
navigating to…"*, because the rule lived in two places and only one of them was
edited. It actually lived in four. `browser/trouble.py` is the one place now,
and these are the tests that keep it one.
"""
from __future__ import annotations

import pytest

from chitragupta.browser import trouble
from chitragupta.browser.trouble import Trouble

#: Verbatim from a real log, which is the point: these are the strings the
#: browser actually produces, not tidied versions of them.
SLOW = ('Page.goto: Timeout 20000ms exceeded.\nCall log:\n  - navigating to '
        '"https://web.whatsapp.com/", waiting until "load"')
LOCKED = ("BrowserType.launch_persistent_context: Failed to create a "
          "ProcessSingleton for your profile directory. This usually means "
          "that the profile is already in use by another instance of Chromium.")
GONE = "Target page, context or browser has been closed"


@pytest.mark.parametrize("message,expected", [
    (SLOW, Trouble.SLOW),
    (LOCKED, Trouble.BUSY),
    (GONE, Trouble.CLOSED),
    ("Timeout: the browser did not answer the goto it was given.", Trouble.SLOW),
    ("something nobody has ever seen", Trouble.UNKNOWN),
])
def test_each_real_failure_gets_its_own_name(message, expected):
    assert trouble.classify(Exception(message)).trouble is expected


def test_a_browser_that_is_not_set_up_keeps_its_own_sentence():
    """The one case that passes the exception's words through. They are already
    written for a person — "choose Set up browsing" — and replacing them with
    something general would lose the single instruction that works."""
    exc = trouble.BrowserNotReadyError(
        "The browser has not been set up yet. Open Connectors and choose "
        "“Set up browsing”.")

    found = trouble.classify(exc)

    assert found.trouble is Trouble.NOT_SET_UP
    assert "Set up browsing" in found.for_person


def test_a_browser_that_went_away_mid_navigation_is_closed_not_slow():
    """Order, not wording — which is why it is a test and not a comment. A
    closed browser times out too, and "a fresh one will open" is the more useful
    of the two true answers."""
    found = trouble.classify(Exception(
        "Timeout 20000ms exceeded: Target page, context or browser has been "
        "closed"))

    assert found.trouble is Trouble.CLOSED


# ── the two facts every caller needs, decided once ───────────────────────
#
# `BROWSER_CLOSED`'s advice was once the exact inversion of the truth — "could
# not be started, do not retry" for the one failure retrying *does* fix — and it
# got that way by being written out in prose in one place and inferred in
# another. They are fields now.
@pytest.mark.parametrize("message,stale", [
    (GONE, True),      # the handle is dead and must be dropped
    (SLOW, False),     # the browser is alive and the page is still open
    (LOCKED, False),   # ours never started; somebody else's is fine
])
def test_only_a_closed_browser_is_stale(message, stale):
    assert trouble.classify(Exception(message)).stale is stale


@pytest.mark.parametrize("message,retry", [
    (SLOW, True),      # the second attempt lands on a warm cache
    (GONE, True),      # retrying is the *only* thing that fixes this
    (LOCKED, False),   # nothing about retrying closes the other window
])
def test_whether_retrying_could_work_is_a_fact_not_a_sentence(message, retry):
    assert trouble.classify(Exception(message)).may_retry is retry


def test_a_slow_page_is_never_called_a_browser_that_would_not_start():
    """The reported bug, stated as the thing that must not come back."""
    person = trouble.classify(Exception(SLOW)).for_person.lower()

    assert "could not be started" not in person
    assert "failed to start" not in person
    assert "slow" in person or "too long" in person


@pytest.mark.parametrize("message", [SLOW, LOCKED, GONE])
def test_no_internal_ever_reaches_a_person(message):
    """`/CLAUDE.md`: never surface an internal. These sentences go into HTTP
    bodies and onto panels."""
    person = trouble.classify(Exception(message)).for_person

    for leak in ("Playwright", "ProcessSingleton", "Page.goto", "Call log",
                 "20000ms", "launch_persistent_context", "Traceback"):
        assert leak not in person, f"{leak!r} reached a person"


def test_every_trouble_has_something_to_say_to_a_person():
    """A failure with no sentence is one that falls back to a traceback at the
    worst possible moment. `NOT_SET_UP` is excluded because it carries the
    exception's own words by design."""
    for name in Trouble:
        if name is Trouble.NOT_SET_UP:
            continue
        assert trouble._FOR_PERSON.get(name), f"{name} has nothing to say"


def test_signing_in_is_a_diagnosis_like_any_other():
    """The one state that is nobody's bug still has to render through the same
    shape, or callers grow a second path for it."""
    found = trouble.signing_in()

    assert found.trouble is Trouble.SIGNING_IN
    assert found.stale is False
    assert "sign" in found.for_person.lower()


# ── it has to stay a leaf ────────────────────────────────────────────────
def test_the_vocabulary_imports_nothing_from_its_own_package():
    """`tests/test_import_layering.py` freezes the `browser` cycle at
    `{chromium, session, signin}` as a ceiling that may not grow. This module is
    read by `driver`, `chromium`, `signin`, `agents/` and `api/`, so the moment
    it imports any of them it joins that cycle and drags the rest in.

    It is also why both exception types were moved down here: a module that has
    to *recognise* a browser failure must not need a driver to do it.
    """
    import ast
    import pathlib

    source = pathlib.Path(trouble.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("chitragupta"), node.module
            assert node.level == 0, f"relative import of {node.module!r}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("chitragupta"), alias.name


def test_the_exception_types_are_the_same_objects_everywhere():
    """They moved down, and `driver` and `chromium` re-export them. An `except`
    clause written against either spelling has to catch what the other raises —
    two classes with one name is how a failure escapes a handler written for it.
    """
    from chitragupta.browser import chromium, driver

    assert driver.BrowserError is trouble.BrowserError
    assert chromium.BrowserNotReadyError is trouble.BrowserNotReadyError


# ── every audience reads the same verdict ────────────────────────────────
#
# The reason this file exists. Four modules answered "why did the browser not
# work" and three of them were wrong about at least one failure; fixing the
# agent side left the HTTP side still handing Playwright's paragraphs to a
# person. These are the tests that would have caught that, and they are written
# over *all* the consumers rather than one of them.
def test_the_agent_table_covers_every_failure_the_classifier_can_produce():
    """Both tables select on the same `Trouble`, which is the point: a failure
    nobody has words for cannot reach only one of them. `NOT_SET_UP` is handled
    by passing the exception's own sentence through, so it needs no entry."""
    from chitragupta.agents.browse_tools import FOR_AGENT

    missing = {t for t in Trouble if t is not Trouble.NOT_SET_UP} - set(FOR_AGENT)

    assert not missing, f"{sorted(t.value for t in missing)} has no agent wording"


@pytest.mark.parametrize("message", [SLOW, LOCKED, GONE])
def test_the_agent_is_never_handed_an_internal_either(message):
    """Same rule, other audience. A tool result is user-facing by the time a
    model has repeated it back to somebody."""
    from chitragupta.agents.browse_tools import FOR_AGENT

    said = FOR_AGENT[trouble.classify(Exception(message)).trouble]

    for leak in ("Playwright", "ProcessSingleton", "Page.goto", "20000ms"):
        assert leak not in said


def test_the_agent_wording_agrees_with_the_verdict_about_retrying():
    """Prose and fact, kept in step. The reported inversion — "do not retry" on
    the one failure retrying fixes — was prose disagreeing with reality, and
    nothing was comparing them."""
    from chitragupta.agents.browse_tools import FOR_AGENT

    for name, said in FOR_AGENT.items():
        if name is Trouble.SIGNING_IN:
            continue        # a wait, and the advice is to stop rather than retry
        refuses = "do not retry" in said.lower()
        sample = {Trouble.BUSY: LOCKED, Trouble.CLOSED: GONE,
                  Trouble.SLOW: SLOW}.get(name)
        if sample is None:
            continue
        assert refuses is not trouble.classify(Exception(sample)).may_retry, (
            f"{name.value}: the words and the verdict disagree about retrying")
