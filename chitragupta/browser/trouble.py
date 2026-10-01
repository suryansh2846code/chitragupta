"""What went wrong with the browser, decided once, for every audience.

**This module exists because the same question had four answers.** "The browser
did not do the thing — why, and what now?" was answered in `agents/browse_tools`
(a string-matching table plus wording for a model), in `api/routes/browser` (three
`raise HTTPException(..., str(exc))` that handed Playwright's own paragraphs to a
person), in `browser/signin` (swallowed under `suppressed`, then reported
success), and nowhere at all for the Browser screen's stale-handle case. Three of
the four were wrong about at least one failure, and the one that was right was
right only for agents.

The cost was measured. A navigation that ran out of time was reported to a user
as *"The browser itself failed to start — not a sign-in or permission issue on
your end."* The browser had started; it was still running the next day. Fixing
that in `browse_tools` left the HTTP route saying *"Page.goto: Timeout 20000ms
exceeded. Call log: - navigating to…"* for the identical failure, because the
rule lived in two places and only one of them was edited.

So the rule lives here, once:

* **`classify` is the only place a browser failure is named.** One table, one
  set of fragments, every caller reading the same verdict.
* **A `Diagnosis` is data, never prose.** It says which failure it is, whether
  the cached handle is dead, and whether retrying could possibly help. Those are
  facts about the browser and they do not vary by who is asking.
* **Wording varies by audience and nothing else.** `for_person` is the sentence
  a human reads in the UI. `agents/browse_tools` keeps its own table, because an
  agent needs "do not retry this call" and a person needs no such thing — and
  both select on the same `Trouble`, so neither can learn about a failure the
  other has not heard of.

**It is a leaf, deliberately.** `tests/test_import_layering.py` freezes the
`browser` cycle at `{chromium, session, signin}` as a ceiling that may not grow,
so this module imports nothing from its own package — which is why the two
exception types live *here* rather than in `driver` and `chromium` where they
started. Moving them down is what let everything above read one vocabulary
instead of three. `driver` and `chromium` re-export them, so every existing
`from .driver import BrowserError` still means what it did.

Recovery is **not** here for the same reason: dropping the shared browser is
`chromium`'s to do because `chromium` owns it. A `Diagnosis` only reports that
the handle is stale; `chromium.recover_from` is what acts on it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrowserError(RuntimeError):
    """Something went wrong driving the browser. Carries a readable message.

    Defined here rather than in `driver` so that `classify` — which every layer
    reads — does not have to import the driver to recognise one. `driver`
    re-exports it.
    """


class BrowserNotReadyError(RuntimeError):
    """The browser is not installed or cannot be driven yet.

    Carries text written for the user, not for a log: it reaches a tool result
    and a panel, and "FileNotFoundError" is not an answer to "why did that not
    work". That is why `classify` passes its message through untouched instead
    of replacing it — see `Diagnosis.for_person`.
    """


class Trouble(Enum):
    """The failures a browser can hand back. A closed set, on purpose.

    Every caller selects on this, so a failure nobody has a name for shows up as
    `UNKNOWN` in one place rather than as four different wrong sentences.
    """

    #: Not downloaded, or this build cannot drive one. The exception's own
    #: message is already written for a person and says which.
    NOT_SET_UP = "not_set_up"
    #: Another Chromium holds the profile. Retrying cannot close it.
    BUSY = "busy"
    #: It was alive and is not any more. Retrying is the only thing that fixes
    #: this, and the cached handle must be dropped first.
    CLOSED = "closed"
    #: The page, or the browser thread, ran out of time. Nothing is broken.
    SLOW = "slow"
    #: A person is using the one window to sign in to a site.
    SIGNING_IN = "signing_in"
    #: Anything we have no name for yet.
    UNKNOWN = "unknown"


#: Playwright's name for "another Chromium already holds this profile". Matched
#: as a fragment because the rest of that message is a path and a paragraph of
#: advice aimed at whoever wrote the code, not at this user.
_BUSY = ("processsingleton", "already in use")

#: How Playwright says "the thing you were driving is gone". Matched on the
#: phrase rather than an exception type because it arrives as a plain message
#: from the browser thread, already turned into a `BrowserError` by `_call`.
_CLOSED = ("has been closed", "target closed", "target page, context or browser")

#: How anything says "that took too long". Playwright's timeouts carry the word
#: and the budget — `Page.goto: Timeout 20000ms exceeded` — and `driver._call`
#: words its own wedged-thread failure to match, so one fragment covers a slow
#: page and a slow browser alike.
_SLOW = ("timeout", "timed out")


@dataclass(frozen=True)
class Diagnosis:
    """Which failure this is, and the two facts every caller needs about it.

    Both booleans are properties of the browser rather than of the audience,
    which is the whole reason they are computed once here. `BROWSER_CLOSED`'s
    advice used to be the exact inversion of the truth — "could not be started,
    do not retry" for the one failure retrying *does* fix — and it got that way
    by being written out in prose in one place and inferred in another.
    """

    trouble: Trouble
    #: The cached browser handle is dead and must be dropped before anything
    #: else is tried. A driver's thread outlives its browser, so a stale handle
    #: is one that looks healthy and answers nothing.
    stale: bool
    #: Could doing exactly this again work? Never a guess: `BUSY` is false
    #: because nothing about retrying closes the other window, `SLOW` is true
    #: because the second attempt lands on a warm cache.
    may_retry: bool
    #: The sentence a person reads. Never Playwright's words, with one
    #: deliberate exception — see `classify`.
    for_person: str


#: What a person is told, per failure. The UI and every HTTP body read this.
#:
#: Written for somebody who did not build the app and is looking at a screen
#: that did not do what they asked: what happened, and what they can do. No
#: internals, because `/CLAUDE.md` says so and because "ProcessSingleton" is not
#: an answer to anything.
_FOR_PERSON: dict[Trouble, str] = {
    Trouble.BUSY: (
        "Another browser window this app opened is still using the browsing "
        "profile. Close the leftover window — usually the one from connecting a "
        "site — and this will work again straight away."),
    Trouble.CLOSED: (
        "The browser window was closed, so the page went with it. Try that "
        "again and a fresh one will open."),
    Trouble.SLOW: (
        "That page took too long to load. The browser is fine and so is your "
        "sign-in — big sites are often slow the first time. Try it again; it is "
        "usually quick the second time."),
    Trouble.SIGNING_IN: (
        "You are part-way through signing in to a site in the browser window. "
        "Finish there, or press Cancel, and this will be free again."),
    Trouble.UNKNOWN: (
        "The browser could not do that. Nothing is wrong with your sign-in or "
        "your settings. Try again, and if it keeps happening, closing and "
        "reopening the app will start a fresh browser."),
}


def classify(exc: BaseException) -> Diagnosis:
    """Name a browser failure, once, for everybody.

    Matched on the message rather than on exception types because that is what
    there is: the browser runs on a thread of its own and its failures arrive as
    text, already wrapped in a `BrowserError` by `driver._call`.

    Order matters and is tested. A browser that went away mid-navigation times
    out *and* reports being closed; `CLOSED` is checked first because "a fresh
    one will open" is the more useful of the two true answers.
    """
    if isinstance(exc, BrowserNotReadyError):
        # The one case that keeps the exception's own words. They are already
        # written for a person — "Open Connectors and choose Set up browsing" —
        # and replacing them with something general would lose the single
        # instruction that works.
        return Diagnosis(Trouble.NOT_SET_UP, stale=False, may_retry=False,
                         for_person=str(exc))

    detail = str(exc).lower()
    if any(phrase in detail for phrase in _CLOSED):
        return Diagnosis(Trouble.CLOSED, stale=True, may_retry=True,
                         for_person=_FOR_PERSON[Trouble.CLOSED])
    if any(phrase in detail for phrase in _BUSY):
        return Diagnosis(Trouble.BUSY, stale=False, may_retry=False,
                         for_person=_FOR_PERSON[Trouble.BUSY])
    if any(phrase in detail for phrase in _SLOW):
        # **Not stale.** The browser is alive and whatever page an agent already
        # had is still open; dropping the session would turn a slow navigation
        # into a lost set of refs for nothing.
        return Diagnosis(Trouble.SLOW, stale=False, may_retry=True,
                         for_person=_FOR_PERSON[Trouble.SLOW])
    return Diagnosis(Trouble.UNKNOWN, stale=False, may_retry=True,
                     for_person=_FOR_PERSON[Trouble.UNKNOWN])


def signing_in() -> Diagnosis:
    """The one failure that is not an exception: somebody is using the window.

    A wait rather than a fault, and it ends at Done or Cancel. It has a
    `Diagnosis` of its own so that callers have exactly one shape to render and
    cannot grow a second path for the one state that is nobody's bug.
    """
    return Diagnosis(Trouble.SIGNING_IN, stale=False, may_retry=False,
                     for_person=_FOR_PERSON[Trouble.SIGNING_IN])
