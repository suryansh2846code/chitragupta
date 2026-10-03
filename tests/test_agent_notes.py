"""What the agent is allowed to write into its own `memory.md`, and how.

Nobody approves these one at a time, so the tests that matter are the ones
about restraint: what it refuses to store, what it refuses to store *again*,
and what it leaves alone in a file the user has also been editing.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import notes
from chitragupta.agents import profile_files as pf

AGENT = "notes-test-agent"


@pytest.fixture(autouse=True)
def _clean():
    pf.forget(AGENT)
    notes.forget(AGENT)
    yield
    pf.forget(AGENT)
    notes.forget(AGENT)


def body():
    return pf.read(AGENT, pf.MEMORY) or ""


def note(text, heading="How you like this done", replaces=""):
    return {"note": text, "heading": heading, "replaces": replaces}


# ── writing ─────────────────────────────────────────────────────────────────

def test_a_note_lands_under_its_heading_with_a_date():
    written = notes.record(AGENT, [note("Replies sign off as 'Suryansh'")])
    assert written == ["Replies sign off as 'Suryansh'"]
    text = body()
    assert "## How you like this done" in text
    assert "- Replies sign off as 'Suryansh' (" in text


def test_a_second_note_joins_the_existing_section():
    notes.record(AGENT, [note("Replies stay short")])
    notes.record(AGENT, [note("Never reply to recruiters")])
    assert body().count("## How you like this done") == 1
    assert len(notes.parse(body())) == 2


def test_a_note_under_a_new_heading_starts_a_new_section():
    notes.record(AGENT, [note("Replies stay short")])
    notes.record(AGENT, [note("The summary goes out on Fridays",
                              heading="Standing instructions")])
    text = body()
    assert "## How you like this done" in text
    assert "## Standing instructions" in text


def test_an_empty_section_is_filled_rather_than_duplicated():
    """A heading the user typed with nothing under it yet is still that
    section. Appending a second copy of it leaves the file with two identical
    headings and the note under the wrong one."""
    pf.write(AGENT, pf.MEMORY, "## Standing instructions\n")

    notes.record(AGENT, [note("The summary goes out on Fridays",
                              heading="Standing instructions")])

    text = body()
    assert text.count("## Standing instructions") == 1
    assert notes.parse(text)[0].heading == "Standing instructions"


def test_an_invented_heading_is_filed_rather_than_refused():
    """Losing the content over a formatting disagreement would be the worse
    trade — the heading is ours to normalise, the instruction is not."""
    notes.record(AGENT, [note("Replies stay short", heading="Vibes")])
    assert "## Vibes" not in body()
    assert len(notes.parse(body())) == 1


def test_one_turn_cannot_fill_the_file():
    many = [note(f"A standing instruction number {i} about the work")
            for i in range(10)]
    notes.record(AGENT, many)
    assert len(notes.parse(body())) == notes.MAX_PER_TURN


# ── what it refuses ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,why", [
    ("ok", "too short"),
    ("x" * 400, "too long"),
    ("Their login code is 448201, do not share this code", "a credential"),
    ("The api_key is sk-abcdefghijklmnopqrstuvwx", "a key"),
    ("They weighed 72 kg this morning", "a reading"),
    ("They ran 5 km before work", "a reading"),
    ("They did 30 reps of the first set", "a reading"),
])
def test_what_may_never_become_a_note(text, why):
    assert notes.rejected(text) is not None, why
    assert notes.record(AGENT, [note(text)]) == []
    assert pf.read(AGENT, pf.MEMORY) is None


def test_a_number_without_a_physical_unit_is_still_an_instruction():
    """"Under 200 words" is a preference; "72 kg" is a reading. The filter has
    to tell them apart or it eats half of what it exists to keep."""
    assert notes.rejected("Replies stay under 200 words") is None
    assert notes.rejected("Never put more than 5 bullets in a summary") is None


def test_the_same_note_is_not_written_twice():
    notes.record(AGENT, [note("Replies sign off as 'Suryansh'")])
    notes.record(AGENT, [note("replies sign off as 'Suryansh'.")])
    assert len(notes.parse(body())) == 1


# ── delete means deleted ────────────────────────────────────────────────────

def test_a_note_the_user_removed_is_never_relearned():
    """The whole reason the ledger exists. A learner that puts back what you
    just deleted is worse than one that never learned anything."""
    notes.record(AGENT, [note("Replies sign off as 'Suryansh'")])
    pf.write(AGENT, pf.MEMORY, "")               # the user cleared it

    assert notes.record(AGENT, [note("Replies sign off as 'Suryansh'")]) == []
    assert "Suryansh" not in body()


def test_removing_one_note_by_hand_does_not_block_the_others():
    notes.record(AGENT, [note("Replies stay short")])
    notes.record(AGENT, [note("Never reply to recruiters")])
    kept = [n for n in notes.parse(body()) if "recruiters" in n.text]
    pf.write(AGENT, pf.MEMORY, f"## How you like this done\n- {kept[0].text}\n")

    notes.record(AGENT, [note("Replies stay short")])        # deleted → stays out
    notes.record(AGENT, [note("Drafts use their own voice")])  # new → goes in

    text = body()
    assert "Replies stay short" not in text
    assert "Drafts use their own voice" in text


def test_deleting_the_agent_clears_the_ledger_too():
    """Otherwise the next agent with this name starts unable to learn what its
    predecessor learned and lost — ids are slugs, so that agent is coming."""
    notes.record(AGENT, [note("Replies sign off as 'Suryansh'")])
    pf.forget(AGENT)
    notes.forget(AGENT)

    assert notes.record(AGENT, [note("Replies sign off as 'Suryansh'")]) != []


# ── correcting itself ───────────────────────────────────────────────────────

def test_a_note_can_supersede_an_earlier_one():
    """Two contradicting standing instructions is worse than none: the model
    picks one at random on every turn."""
    notes.record(AGENT, [note("Replies sign off as 'Suryansh Singh'")])
    old = notes.parse(body())[0]

    notes.record(AGENT, [note("Replies sign off as 'Suryansh'",
                              replaces=old.id)])

    text = body()
    assert "Suryansh Singh" not in text
    assert "Replies sign off as 'Suryansh'" in text
    assert len(notes.parse(text)) == 1


def test_superseding_something_that_is_not_there_still_keeps_the_new_note():
    notes.record(AGENT, [note("Replies stay short", replaces="n99")])
    assert len(notes.parse(body())) == 1


def test_a_note_cannot_delete_itself_by_superseding_itself():
    notes.record(AGENT, [note("Replies stay short")])
    first = notes.parse(body())[0]
    notes.record(AGENT, [note("Replies stay short", replaces=first.id)])
    assert len(notes.parse(body())) == 1


# ── the user's own prose ────────────────────────────────────────────────────

def test_a_paragraph_the_user_wrote_is_left_exactly_where_it_was():
    """These are files people open. A writer that reflows what it does not
    understand is a writer that eats their work."""
    hand = ("# My own notes\n\n"
            "Some prose I wrote myself, which is not a bullet.\n\n"
            "## How you like this done\n"
            "- Keep it brief\n")
    pf.write(AGENT, pf.MEMORY, hand)

    notes.record(AGENT, [note("Never reply to recruiters")])

    text = body()
    assert "# My own notes" in text
    assert "Some prose I wrote myself, which is not a bullet." in text
    assert "- Keep it brief" in text
    assert "Never reply to recruiters" in text


def test_nothing_is_written_when_every_proposal_is_refused():
    pf.write(AGENT, pf.MEMORY, "## How you like this done\n- Keep it brief\n")
    before = body()
    notes.record(AGENT, [note("no")])
    assert body() == before


def test_a_full_file_refuses_the_note_rather_than_evicting_one():
    """The note that would be evicted is a standing instruction the user is
    relying on. Losing one silently is the worse failure."""
    # Filled to just under the cap, so the file is legal but one more bullet
    # is not — the state a long-running agent actually reaches.
    limit = pf.LIMITS[pf.MEMORY]
    filler = "- " + ("a long standing instruction about the work " * 2) + "\n"
    text = "## How you like this done\n" + filler * ((limit - 300) // len(filler))
    text = text.rstrip("\n") + "x" * (limit - 20 - len(text.encode("utf-8"))) + "\n"
    pf.write(AGENT, pf.MEMORY, text)
    before = body()

    assert notes.record(AGENT, [note("Never reply to recruiters")]) == []
    assert body() == before
