"""The two files an agent keeps, and the ways they could go wrong.

The interesting half of this file is not "does writing a file work". It is the
four things that would each be a real incident:

* a name or an id out of a URL reaching a path it should not;
* "no file" and "an empty file" being collapsed, which silently restores a
  preset the user deliberately cleared;
* an oversize file riding into the cached prefix on every turn;
* a deleted agent's notes being inherited by the next agent with its name,
  which is not hypothetical — ids are slugs, so the collision is deterministic.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import profile_files as pf

AGENT = "test-profile-agent"


@pytest.fixture(autouse=True)
def _clean():
    pf.forget(AGENT)
    yield
    pf.forget(AGENT)


# ── the two paths that arrive from a URL ────────────────────────────────────

@pytest.mark.parametrize("name", [
    "../secrets.json",
    "../../secrets.json",
    "..%2F..%2Fsecrets.json",
    "/etc/passwd",
    "persona.md ",            # a trailing space is not the file
    "Persona.md",             # nor is a different case
    "notes.md",
    "",
])
def test_only_the_two_known_file_names_are_accepted(name):
    """Matched against a closed set, never cleaned up. A sanitised name is a
    traversal with extra steps, and `secrets.json` is two directories up."""
    with pytest.raises(pf.FileRejectedError):
        pf.read(AGENT, name)
    with pytest.raises(pf.FileRejectedError):
        pf.write(AGENT, name, "x")


@pytest.mark.parametrize("agent_id", [
    "..",
    "../..",
    "../other-agent",
    "/absolute",
    "has/slash",
    "Capitals",
    "",
])
def test_an_agent_id_cannot_reach_outside_the_agents_directory(agent_id):
    """The id is part of a path too, and it also arrives from a URL."""
    with pytest.raises(pf.FileRejectedError):
        pf.agent_dir(agent_id)


def test_a_rejected_id_writes_nothing_at_all():
    """Refusing late — after a directory has been created — would leave the
    traversal's footprint behind even though the write failed."""
    before = sorted(p.name for p in pf.root().glob("*")) if pf.root().exists() else []
    with pytest.raises(pf.FileRejectedError):
        pf.write("../escape", pf.PERSONA, "x")
    after = sorted(p.name for p in pf.root().glob("*")) if pf.root().exists() else []
    assert before == after


# ── absent is not empty ─────────────────────────────────────────────────────

def test_absent_and_empty_are_different_answers():
    """`None` means "use the shipped default"; `""` means "the user cleared
    it". Collapsing them makes clearing a persona restore the preset."""
    assert pf.read(AGENT, pf.PERSONA) is None
    pf.write(AGENT, pf.PERSONA, "")
    assert pf.read(AGENT, pf.PERSONA) == ""
    assert pf.clear(AGENT, pf.PERSONA) is True
    assert pf.read(AGENT, pf.PERSONA) is None
    assert pf.clear(AGENT, pf.PERSONA) is False


def test_a_write_round_trips_and_reports_itself():
    pf.write(AGENT, pf.MEMORY, "## How you like this done\n- Short replies.\n")
    assert "Short replies" in pf.read(AGENT, pf.MEMORY)
    meta = pf.info(AGENT, pf.MEMORY)
    assert meta["exists"] is True
    assert meta["bytes"] > 0
    assert meta["limit"] == pf.LIMITS[pf.MEMORY]
    assert meta["updated_at"]


def test_info_for_a_file_that_is_not_there_is_not_an_error():
    meta = pf.info(AGENT, pf.PERSONA)
    assert meta == {"name": pf.PERSONA, "exists": False, "bytes": 0,
                    "limit": pf.LIMITS[pf.PERSONA], "updated_at": None}


# ── the cap, which is a cost control ────────────────────────────────────────

def test_a_write_over_the_limit_is_refused_with_a_sentence_for_a_person():
    """These ride in the cached prefix on every round of every turn, on the
    user's own key. The refusal has to say what to do, not what broke."""
    too_big = "x" * (pf.LIMITS[pf.MEMORY] + 1)
    with pytest.raises(pf.FileRejectedError) as exc:
        pf.write(AGENT, pf.MEMORY, too_big)
    message = str(exc.value)
    assert "6 KB" in message
    assert "Shorten it" in message
    assert pf.read(AGENT, pf.MEMORY) is None     # and nothing was written


def test_a_hand_edited_oversize_file_is_truncated_on_a_line_boundary():
    """A write over the cap is refused, so this only happens to a file someone
    edited outside the app — which is a thing these files exist to allow."""
    path = pf.agent_dir(AGENT) / pf.MEMORY
    path.parent.mkdir(parents=True, exist_ok=True)
    line = "- a standing instruction that is quite long indeed\n"
    path.write_text(line * 200, encoding="utf-8")

    text, truncated = pf.for_prompt(AGENT, pf.MEMORY)
    assert truncated is True
    assert len(text.encode("utf-8")) <= pf.LIMITS[pf.MEMORY]
    assert text.endswith("indeed")          # a whole line, never half of one


def test_a_file_within_the_limit_is_not_reported_as_truncated():
    pf.write(AGENT, pf.MEMORY, "- one note\n")
    assert pf.for_prompt(AGENT, pf.MEMORY) == ("- one note", False)


def test_for_prompt_on_a_missing_file_is_empty_and_not_truncated():
    assert pf.for_prompt(AGENT, pf.MEMORY) == ("", False)


# ── the slug-reuse hazard ───────────────────────────────────────────────────

def test_forgetting_an_agent_removes_every_file_it_kept():
    """Ids are slugs of the name: delete "Chotu", build another "Chotu", and it
    lands on the same id. Notes left behind would be inherited — and unlike a
    stale tool list, these are prose the new agent acts on."""
    pf.write(AGENT, pf.PERSONA, "I am the old agent.")
    pf.write(AGENT, pf.MEMORY, "- the old agent's notes")

    pf.forget(AGENT)

    assert pf.read(AGENT, pf.PERSONA) is None
    assert pf.read(AGENT, pf.MEMORY) is None
    assert not pf.agent_dir(AGENT).exists()


def test_forgetting_an_agent_that_kept_nothing_is_not_an_error():
    pf.forget(AGENT)
    pf.forget(AGENT)
