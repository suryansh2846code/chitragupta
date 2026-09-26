"""Matching what a model wrote against what the app actually has.

A model is handed a list and writes one of them back. It gets it *nearly* right
constantly — different capitals, an id where a name was shown, a space where an
underscore is, "cheif" for "chief". Every one of those is unambiguous to a
person looking at the list, and every one used to be a flat no: the action
failed, the model apologised, and the user paid for two turns and read an error
about a typo they did not make.

The line this file defends is where "nearly" stops. Resolving a near-miss is
worth it only while it cannot be wrong, so the interesting tests are the ones
that make it refuse: a name matching nothing, and — the one that matters — a
name matching two things almost equally, where picking one silently is a coin
toss wearing a decision's clothes.
"""
from __future__ import annotations

from dataclasses import dataclass

from chitragupta.core.naming import flatten, resolve


@dataclass(frozen=True)
class Thing:
    id: str
    name: str


ROSTER = [
    Thing("health", "Health & Fitness"),
    Thing("chief-of-staff", "Chief of Staff"),
    Thing("chotu", "chotu"),
]


def find(named, roster=ROSTER):
    return resolve(named, roster, key=lambda t: (t.id, t.name),
                   label=lambda t: t.name, what="agent")


# ── what it should find ────────────────────────────────────────────────────

def test_the_id_exactly():
    assert find("chief-of-staff").value.id == "chief-of-staff"
    assert find("chief-of-staff").how == "exact"


def test_the_name_as_it_reads_on_screen():
    """A model reads "Chief of Staff" in its prompt and writes the capitals
    back. That is the roster answering to itself."""
    assert find("Chief of Staff").value.id == "chief-of-staff"


def test_punctuation_and_case_stop_counting():
    for spelling in ("chief_of_staff", "CHIEF OF STAFF", "ChiefOfStaff",
                     "chief of staff"):
        assert find(spelling).value.id == "chief-of-staff", spelling


def test_a_typo_with_one_obvious_answer():
    """The case that cost a real user a turn: `agent="cheif of staff"` against
    a roster of three, one of which is Chief of Staff."""
    found = find("cheif of staff")
    assert found.value.id == "chief-of-staff"
    assert found.how == "near"


# ── what it must refuse ────────────────────────────────────────────────────

def test_a_name_matching_nothing_is_still_refused():
    """"inbox" is not a near-miss for anything here. Guessing would give the
    user an automation run by somebody they did not choose."""
    found = find("inbox")
    assert not found
    assert "inbox" in found.problem
    assert "Chief of Staff" in found.problem, "it refused without saying what works"


def test_two_things_almost_equally_close_is_a_refusal_not_a_coin_toss():
    """The one that matters. Picking between two near-identical names silently
    is the kind of decision a user finds out about weeks later."""
    twins = [Thing("quarterly-report-a", "Quarterly Report A"),
             Thing("quarterly-report-b", "Quarterly Report B")]
    found = resolve("quarterly report", twins, key=lambda t: (t.id, t.name),
                    label=lambda t: t.name, what="list")

    assert not found
    assert "could be more than one" in found.problem
    assert "Quarterly Report A" in found.problem
    assert "Quarterly Report B" in found.problem


def test_a_short_name_is_never_guessed_at():
    """Below the length floor only exact matching applies, so two short ids
    that differ by one character cannot be confused for each other."""
    twins = [Thing("inv", "Inv"), Thing("env", "Env")]
    found = resolve("anv", twins, key=lambda t: (t.id, t.name),
                    label=lambda t: t.name, what="list")
    assert not found


def test_an_exact_match_wins_even_when_something_else_is_close():
    """`Invoices` must never be read as `Invoices 2` because they are similar —
    an exact answer ends the question."""
    twins = [Thing("invoices", "Invoices"), Thing("invoices-2", "Invoices 2")]
    found = resolve("Invoices 2", twins, key=lambda t: (t.id, t.name),
                    label=lambda t: t.name, what="list")
    assert found.value.id == "invoices-2"
    assert found.how == "exact"


def test_nothing_named_is_refused_with_the_options():
    found = find("")
    assert not found
    assert "Health & Fitness" in found.problem


def test_an_empty_roster_says_so_rather_than_blaming_the_name():
    found = find("anything", roster=[])
    assert not found
    assert "no agents" in found.problem


def test_a_genuinely_different_short_name_is_not_a_near_miss():
    """Short names are where fuzzy matching goes wrong. "wealth" is one letter
    from "health" and means something else entirely."""
    assert not find("wealth")


# ── the shape ──────────────────────────────────────────────────────────────

def test_flatten_makes_the_three_spellings_one():
    assert flatten("Chief of Staff") == flatten("chief-of-staff") == "chiefofstaff"


def test_a_match_is_falsy_when_there_is_none():
    """So callers can write `if not found: return found.problem` and not have
    to remember which field means what."""
    assert bool(find("chotu")) is True
    assert bool(find("nobody")) is False


# ── and it is used where a model names something ──────────────────────────

def test_the_agent_on_a_new_automation_goes_through_it():
    from chitragupta.agents.custom import get_custom_store
    from chitragupta.core.routine_store import get_routines

    store = get_custom_store()
    agent = store.create("Research Desk", role="looks things up")
    try:
        from chitragupta import actions

        out = actions.run_now("create_routine", {
            "name": "Watch", "trigger": "new_email", "instruction": "tell me",
            # The capitals and a missing letter, exactly as a model writes it.
            "agent": "Reserch Desk"})
        assert out["ok"], out.get("error")
        made = get_routines().get(out["id"])
        assert made["agent_id"] == agent.id
        get_routines().delete(out["id"])
    finally:
        store.delete(agent.id)


def test_delegation_goes_through_it_too():
    """The same matcher, so a model that can reach an agent by name in one
    place can reach it by name everywhere."""
    import inspect

    from chitragupta.agents import delegation

    assert "core.naming" in inspect.getsource(delegation) or \
        "from ..core.naming import resolve" in inspect.getsource(delegation)
