"""`memory.md`'s shape, and the rule that makes deleting a note stick.

The agent writes its own notes — nobody approves each one — so everything here
is built around the two ways that goes wrong.

**A model handed a file to rewrite silently drops lines.** So the model never
emits the file. It proposes one note at a time, and this module splices it into
the existing lines. Anything it does not recognise is left exactly where it is:
a user who opens `memory.md` and writes a paragraph of their own keeps that
paragraph, because the writer only ever inserts and deletes whole bullets it
can account for. A re-render would be tidier and would eat their prose.

**A note the user deletes must not come straight back.** A learner that
relearns what you just removed is worse than one that never learned, and the
obvious fix — a "rejected" button — only works if there is a button. There is
not one yet, and a user editing the file by hand would get nothing from it
anyway. So the rule is derived instead: every note this module writes is
recorded, and a note that **was written before and is no longer in the file**
was removed by the user. It is never written again. Hand-edit, API, or a future
delete button all produce the same evidence.

The format is deliberately the dullest markdown that could work:

    ## How you like this done
    - Replies sign off as "Suryansh". (2026-10-03)

A closed set of headings, because a model inventing its own ends up with
fourteen sections of one bullet each. One bullet per note, so a note is a line
— which is what makes splicing and deleting exact rather than approximate.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..config import get_settings
from ..core.redact import is_sensitive, redact
from ..log import get_logger
from . import profile_files

log = get_logger(__name__)

#: The sections a note may be filed under. Anything else is filed under the
#: first: a heading the model invented is still a note worth keeping, and
#: refusing it would lose the content over a formatting disagreement.
HEADINGS: tuple[str, ...] = (
    "How you like this done",
    "Standing instructions",
    "What didn't work",
)

#: Shorter than this is not an instruction; longer than this is an essay, and
#: twenty of them is the whole cap spent on one turn's enthusiasm.
MIN_CHARS = 8
MAX_CHARS = 220

#: How many a single turn may add. A turn that produces six notes has
#: misunderstood what a note is, and the cost of being wrong six times at once
#: is a file the user has to clean up by hand.
MAX_PER_TURN = 3

#: A reading, not a preference. `/CLAUDE.md`: *a number over time is not a
#: memory* — these belong in `metrics.py`, where they are a series with a unit
#: rather than a sentence that will be wrong next week. The units are physical
#: on purpose: "replies stay under 200 words" is a standing instruction and
#: must survive this filter.
_MEASUREMENT = re.compile(
    r"\b\d+(?:\.\d+)?\s*"
    r"(?:kgs?|kilos?|lbs?|pounds?|grams?|cm|mm|km|miles?|mi|bpm|kcal|cals?|"
    r"calories|reps?|sets?|steps?|bpm|mmhg|°c|°f|celsius|fahrenheit)\b",
    re.I)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_note_writes (
    agent_id    TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    note        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (agent_id, fingerprint)
);
"""


@dataclass(frozen=True)
class Note:
    """One bullet in the file.

    There is deliberately no line number here. An earlier version carried one
    and it was wrong by construction: every insert shifts the lines below it,
    so a position captured before one points at a different bullet by the time
    anything uses it. Identity is the text.
    """
    id: str                 # "n3" — positional, and valid only for this read
    heading: str
    text: str


def _conn() -> sqlite3.Connection:
    # The same database as every other per-agent fact, for the reason
    # `tool_overrides` gives: one file to back up, one to keep consistent.
    path = get_settings().home / "agents.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def fingerprint(text: str) -> str:
    """What "the same note" means.

    Loose enough that re-learning the same instruction in slightly different
    words is usually caught, strict enough that two genuinely different notes
    never collide: case, surrounding punctuation and runs of whitespace are
    noise; the words are not.
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
    cleaned = re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$", "", cleaned)
    return re.sub(r"[^a-z0-9 ]+", "", cleaned).strip()


def parse(text: str) -> list[Note]:
    """Every bullet in the file, in order, with the heading it sits under.

    Lines this does not recognise are not an error and are not reported — they
    are the user's, and the only thing that matters about them is that nothing
    here moves them.
    """
    notes: list[Note] = []
    heading = ""
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
        elif stripped.startswith("- ") and len(stripped) > 2:
            notes.append(Note(id=f"n{len(notes) + 1}", heading=heading,
                              text=stripped[2:].strip()))
    return notes


def rejected(note: str) -> str | None:
    """Why this note may not be stored, or None if it may.

    A prompt is not a guarantee. Every rule here is also stated to the model in
    `runtime._LEARN_SYS`, and every one is repeated as code because the model
    will eventually ignore one — and the two it is most likely to ignore are
    the two that matter most.
    """
    text = (note or "").strip()
    if len(text) < MIN_CHARS:
        return "too short to be an instruction"
    if len(text) > MAX_CHARS:
        return "too long for a note"
    # Secrets are not "redact and keep" here, unlike the brain's ingest path:
    # a redacted husk is useless as an instruction, and we would be writing a
    # file in the user's home directory to hold it.
    if is_sensitive(text) or redact(text) != text:
        return "looks like a credential"
    if _MEASUREMENT.search(text):
        return "a reading, not a preference"
    return None


def _written_before(agent_id: str) -> set[str]:
    conn = _conn()
    try:
        return {r["fingerprint"] for r in conn.execute(
            "SELECT fingerprint FROM agent_note_writes WHERE agent_id = ?",
            (agent_id,))}
    finally:
        conn.close()


def _record(agent_id: str, pairs: list[tuple[str, str]]) -> None:
    conn = _conn()
    try:
        now = datetime.now(UTC).isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO agent_note_writes "
            "(agent_id, fingerprint, note, created_at) VALUES (?,?,?,?)",
            [(agent_id, fp, note, now) for fp, note in pairs])
        conn.commit()
    finally:
        conn.close()


def forget(agent_id: str) -> None:
    """Drop the ledger for a deleted agent.

    Without this the ledger outlives the agent, and because ids are slugs the
    next agent with that name would start life unable to learn anything its
    predecessor had already learned and lost.
    """
    conn = _conn()
    try:
        conn.execute("DELETE FROM agent_note_writes WHERE agent_id = ?",
                     (agent_id,))
        conn.commit()
    finally:
        conn.close()


def _insert(lines: list[str], heading: str, bullet: str) -> list[str]:
    """Put one bullet under its heading, creating the section if needed.

    Inserted after the last bullet already under that heading rather than at
    the end of the file, so a section stays a section — and never before an
    unrecognised line, which is how a user's own paragraph would end up with
    somebody else's bullet in the middle of it.
    """
    out = list(lines)
    # Two different anchors, and both are needed. The last bullet in the
    # section is where a new one belongs; the heading itself is the fallback
    # for a section that exists but is still empty — a heading the user typed
    # with nothing under it yet. Without the second, that case appends a
    # duplicate heading and the file ends up with two identical sections.
    at = -1
    head_at = -1
    in_section = False
    for i, line in enumerate(out):
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped[3:].strip() == heading
            if in_section:
                head_at = i
        elif in_section and stripped.startswith("- "):
            at = i
    if at >= 0:
        out.insert(at + 1, bullet)
        return out
    if head_at >= 0:
        out.insert(head_at + 1, bullet)
        return out
    while out and not out[-1].strip():
        out.pop()
    if out:
        out.append("")
    out.extend([f"## {heading}", bullet])
    return out


def record(agent_id: str, proposals: list[dict]) -> list[str]:
    """Apply what the turn proposed. Returns the notes actually written.

    Every skip is silent to the caller and logged here, because the caller runs
    after the turn has returned and has nowhere to put a complaint. What the
    user sees instead is the file: a note that was not written is simply not in
    it.
    """
    if not proposals:
        return []

    current = profile_files.read(agent_id, profile_files.MEMORY) or ""
    existing = parse(current)
    present = {fingerprint(n.text) for n in existing}
    by_id = {n.id: n for n in existing}
    written = _written_before(agent_id)

    lines = current.split("\n") if current else []
    # What a supersede removes is tracked by note identity, never by line
    # number: every insert shifts the lines below it, so an index captured
    # before one is pointing at the wrong bullet by the time it is used. The
    # ids the model was shown refer to the file as it was read, so `by_id`
    # stays the original parse and is not refreshed inside the loop.
    retire: set[str] = set()
    added: list[tuple[str, str]] = []
    stamp = date.today().isoformat()

    for proposal in proposals[:MAX_PER_TURN]:
        text = str(proposal.get("note") or "").strip()
        why = rejected(text)
        if why:
            log.debug("note not kept for %s (%s): %s", agent_id, why, text[:80])
            continue
        fp = fingerprint(text)
        if not fp or fp in present:
            continue
        if fp in written:
            # Written before and not in the file now: the user took it out.
            log.debug("note not relearned for %s: %s", agent_id, text[:80])
            continue

        superseded = by_id.get(str(proposal.get("replaces") or "").strip())
        if superseded is not None:
            retire.add(fingerprint(superseded.text))

        heading = str(proposal.get("heading") or "").strip()
        if heading not in HEADINGS:
            heading = HEADINGS[0]
        lines = _insert(lines, heading, f"- {text} ({stamp})")
        present.add(fp)
        added.append((fp, text))

    if not added:
        return []

    # Never retire something written this turn: a model that answers
    # `replaces` with the note it is replacing *with* would otherwise delete
    # its own new line and leave the file worse than before.
    retire -= {fp for fp, _ in added}
    if retire:
        lines = [ln for ln in lines
                 if not (ln.strip().startswith("- ")
                         and fingerprint(ln.strip()[2:]) in retire)]

    text_out = "\n".join(lines).strip() + "\n"
    try:
        profile_files.write(agent_id, profile_files.MEMORY, text_out)
    except profile_files.FileRejectedError:
        # The file is full. Refused rather than evicting something to make
        # room: the note we would drop is a standing instruction the user is
        # relying on, and losing one of those silently is the worse failure.
        # The profile screen is where "this agent's memory is full" belongs.
        log.info("notes not written for %s: memory.md is at its limit", agent_id)
        return []

    _record(agent_id, added)
    return [note for _, note in added]
