"""Matching what a model wrote against what the app actually has.

A model is given a list — the agents, the connected apps, the tools a server
offers — and writes one of them back. It gets it *nearly* right constantly: the
right thing with different capitals, an id where a name was shown, a space
where an underscore is, "cheif" for "chief". Every one of those is unambiguous
to a person looking at the list, and every one of them used to be a flat no.

A flat no is not free. The action fails, the model apologises, tries again, and
the user pays for two turns and reads an error about a typo they did not make.
Worse, the retry is not guaranteed to be better: a model that spelled it wrong
once will sometimes spell it wrong the same way twice.

So: **resolve what is unambiguous, refuse what is not, and never guess between
two.** The three rules in order of confidence —

1. an exact match on the id or the name;
2. a match once case, spaces, hyphens and underscores stop counting;
3. a single close-enough match, where "close enough" is one small edit and no
   other candidate is anywhere near.

Rule 3 is the one that needs the care. It requires a *unique* nearest match
with a clear gap to the runner-up, because "chotu" and "chotu2" being one
character apart is exactly when picking one silently is worst.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, TypeVar

T = TypeVar("T")

#: How alike two names have to be before a near-miss counts at all.
NEAR = 0.86

#: And how long the written name must be before closeness means anything.
#:
#: Short names are where this goes wrong: "wealth" and "health" are one letter
#: apart and different words, and at six characters a single slip is worth 0.83
#: — higher than the threshold above. Below this length only the first two
#: rules apply, which are exact by construction. Found by the test for it, and
#: kept because a menu of short ids is exactly what a model picks wrongly from.
LONG_ENOUGH = 8

#: How far ahead of the runner-up the winner must be. Without it, two similar
#: names — `invoices` and `invoices-2` — make a coin toss that looks like a
#: decision, and the user finds out weeks later that the wrong one was picked.
CLEAR_BY = 0.08


@dataclass(frozen=True)
class Match:
    """What the name resolved to, or what to say instead."""

    #: The candidate, or None.
    value: Any = None
    #: How it was found: "exact", "loose", "near", or "" when it was not.
    how: str = ""
    #: What to tell whoever asked, when there is no answer. Always names the
    #: real options — "no" without them is an error the next attempt repeats.
    problem: str = ""

    def __bool__(self) -> bool:
        return self.value is not None


def flatten(text: str) -> str:
    """A name with everything that is not a letter or digit removed.

    `Chief of Staff`, `chief-of-staff` and `chief_of_staff` are the same name
    written three ways, and which of the three a model produces depends on
    whether it read the label or the id.
    """
    return "".join(c for c in str(text or "").casefold() if c.isalnum())


def resolve(named: str, candidates: Iterable[T], *,
            key: Callable[[T], Iterable[str]],
            label: Callable[[T], str],
            what: str = "one") -> Match:
    """Find the candidate `named` means, or say why there is not one.

    `key` gives every string a candidate answers to — its id and its display
    name, usually. `label` gives the one to print. `what` names the kind of
    thing for the message: "agent", "app", "tool".
    """
    options = list(candidates)
    if not options:
        return Match(problem=f"there are no {what}s yet")

    wanted = str(named or "").strip()
    if not wanted:
        return Match(problem=(f"say which {what}: "
                              + ", ".join(label(o) for o in options)))

    for option in options:                       # 1. exactly
        if any(str(k) == wanted for k in key(option)):
            return Match(option, "exact")

    flat = flatten(wanted)
    loose = [o for o in options if any(flatten(k) == flat for k in key(o))]
    if len(loose) == 1:                          # 2. bar punctuation and case
        return Match(loose[0], "loose")
    if len(loose) > 1:
        return Match(problem=_ambiguous(wanted, loose, label, what))

    scored = sorted(                             # 3. nearly, and only nearly
        ((_closeness(flat, o, key), o) for o in options),
        key=lambda pair: pair[0], reverse=True)
    if len(flat) < LONG_ENOUGH:
        return Match(problem=(f"there is no {what} called “{wanted}”. "
                              f"The {what}s you have are: "
                              + ", ".join(label(o) for o in options)))
    best, runner_up = scored[0], (scored[1] if len(scored) > 1 else (0.0, None))
    if best[0] >= NEAR and best[0] - runner_up[0] >= CLEAR_BY:
        return Match(best[1], "near")
    if best[0] >= NEAR:
        # Two things are equally close. Picking one is a coin toss wearing a
        # decision's clothes, and the user finds out weeks later.
        close = [o for score, o in scored if score >= NEAR]
        return Match(problem=_ambiguous(wanted, close, label, what))

    return Match(problem=(f"there is no {what} called “{wanted}”. "
                          f"The {what}s you have are: "
                          + ", ".join(label(o) for o in options)))


def _closeness(flat: str, option: T, key: Callable[[T], Iterable[str]]) -> float:
    return max((SequenceMatcher(None, flat, flatten(k)).ratio()
                for k in key(option)), default=0.0)


def _ambiguous(wanted: str, options: list[T], label: Callable[[T], str],
               what: str) -> str:
    return (f"“{wanted}” could be more than one {what}: "
            + ", ".join(label(o) for o in options)
            + ". Say which one.")
