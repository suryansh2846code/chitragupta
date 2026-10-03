"""How an agent works, chosen rather than written.

`persona.md` is the file an agent runs on, and it still is — nothing about the
prompt changed. What changed is how it gets written: an empty box asking a
non-technical person to compose a system prompt is a box most people close
again, so the choices that actually matter are offered as choices and the file
is **rendered** from them.

**One direction, and it is stated on the screen.** The selections are the
source; `persona.md` is what they produce. Saving re-renders the file, so prose
typed into it by hand outside the app is replaced the next time somebody
presses Save. That is the cost of a picker, and the honest way to pay it is to
say so rather than to try to parse sentences back into chips — which would be a
parser a user can break by typing, the thing `profile_files` exists to avoid.
The way to keep hand-written prose is the free-text field, which is part of the
document and survives every re-render. An agent that already had a hand-written
`persona.md` and no selections gets it back as that field's starting value, so
the picker never silently eats what somebody wrote.

**Autonomy is not a second permission system.** `/CLAUDE.md` is explicit that
there is one gate and every side effect goes through it. So the level here does
exactly two things, both real and neither a new gate:

* it sets the tool list — `read_only` keeps the tools whose access tier is
  `READ`, derived from `tool_facts.tool_access` rather than listed here, and
  the list it replaced is kept so that moving off the level puts it back;
* it is written into the prompt, which is what changes how much the agent
  checks in before doing something it is already allowed to do.

What it never does is widen what the gate allows. Irreversible verbs, spending
money, and reaching somebody who is not the user ask every time at every level,
and the screen says so — a control implying otherwise would be promising
something the gate is going to refuse.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from ..config import get_settings
from ..log import get_logger, suppressed

log = get_logger(__name__)

#: How it comes across. Multi-select, because "warm and professional" is a real
#: answer and picking one of the two is not.
#:
#: Each option is `(label, fragment)` — what the person reads, and what the
#: agent reads. They are not the same string and pretending they were produced
#: "communicate in a way that is concise and bullet points", which is not
#: English and is what the model would have been asked to act on. The label is
#: a chip; the fragment is a clause in a sentence.
TRAITS: tuple[tuple[str, str], ...] = (
    ("Supportive", "supportive"), ("Warm", "warm"),
    ("Professional", "professional"), ("Friendly", "friendly"),
    ("Calm", "calm"), ("Witty", "witty"), ("Curious", "curious"),
    ("Assertive", "assertive"), ("Empathetic", "empathetic"),
    ("Playful", "playful"), ("Stoic", "unflappable"),
    ("Encouraging", "encouraging"),
)

#: How it says things.
COMMUNICATION: tuple[tuple[str, str], ...] = (
    ("Concise", "keep answers short"),
    ("Detailed", "give the full picture rather than a summary"),
    ("Conversational", "write the way people talk"),
    ("Formal", "keep the register formal"),
    ("Technical", "use precise technical language"),
    ("Step by step", "lay work out as numbered steps"),
    ("Socratic", "ask questions back rather than only answering"),
    ("Bullet points", "answer in bullet points"),
    ("Summary first", "lead with the answer, then the detail"),
    ("Explain simply", "explain as if to somebody new to the subject"),
)

#: How it works a problem.
THINKING: tuple[tuple[str, str], ...] = (
    ("Analytical", "break problems down before answering"),
    ("Creative", "offer the unobvious option as well as the obvious one"),
    ("First principles", "reason from first principles"),
    ("Evidence-driven", "say what the claim rests on"),
    ("Risk-averse", "name what could go wrong before recommending"),
    ("Experimental", "suggest the small test before the big commitment"),
    ("Big picture", "keep the wider goal in view"),
    ("Detail-oriented", "check the details nobody asked about"),
    ("Devil's advocate", "argue the other side before agreeing"),
    ("Outcome-focused", "optimise for the result, not the process"),
)

#: Caps. Every choice is spent on every turn of every conversation, inside the
#: cached prefix — and an agent told it is twelve things at once has been told
#: nothing. Four, three and three reads as a character; all thirty-two reads as
#: a thesaurus.
MAX_TRAITS = 4
MAX_COMMUNICATION = 3
MAX_THINKING = 3

#: The free-text field, which is also where a hand-written `persona.md` lands
#: the first time somebody opens the picker on an agent that had one.
MAX_EXTRA = 4000

#: What the agent decides for itself.
#:
#: Three levels and no more. The reference designs for this kind of control run
#: to five or six, and the difference between "Assist" and "Delegate" is a
#: sentence nobody can act on. These three are the three questions a person
#: actually has: can it change anything, does it check with me first, and does
#: it get on with it.
#:
#: `tools` is the only one that changes what the agent *can* do; the other two
#: change how it behaves with what it already has. That asymmetry is the point
#: — see the module docstring.
AUTONOMY: tuple[dict[str, Any], ...] = (
    {
        "key": "read_only",
        "label": "Read only",
        "blurb": "Looks and reports. Every tool that changes anything is off, "
                 "so there is nothing for it to ask about.",
        "prompt": "You can read and report, and you cannot change anything. "
                  "If the user asks for something that would require a change, "
                  "say plainly that you are set to read only and what they "
                  "would need to turn on.",
    },
    {
        "key": "ask_first",
        "label": "Ask before changing",
        "blurb": "Does the work and brings each change to you as a card to "
                 "confirm. The usual setting.",
        "prompt": "Propose changes rather than assuming them. When something "
                  "could reasonably be done more than one way, ask before you "
                  "pick.",
    },
    {
        "key": "on_its_own",
        "label": "Acts on its own",
        "blurb": "Decides and gets on with it, and tells you after. Anything "
                 "irreversible, anything that spends money and anything that "
                 "reaches another person still asks — that floor is not a "
                 "setting.",
        "prompt": "Decide and act rather than checking in at every step. Use "
                  "your judgement on anything reversible and report what you "
                  "did. You are still asked to confirm anything irreversible, "
                  "anything that spends money, and anything that reaches a "
                  "person other than the user — that is not something this "
                  "setting changes, so do not tell the user it is.",
    },
)

DEFAULT_AUTONOMY = "ask_first"

_BY_KEY = {level["key"]: level for level in AUTONOMY}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_persona (
    agent_id      TEXT PRIMARY KEY,
    traits        TEXT NOT NULL DEFAULT '[]',
    communication TEXT NOT NULL DEFAULT '[]',
    thinking      TEXT NOT NULL DEFAULT '[]',
    autonomy      TEXT NOT NULL DEFAULT '',
    extra         TEXT NOT NULL DEFAULT '',
    -- What the tool list was before `read_only` replaced it, so leaving that
    -- level puts back what the user had rather than a guess at it.
    tools_before  TEXT,
    updated_at    TEXT NOT NULL
);
"""


class PersonaRejectedError(ValueError):
    """Carries a sentence meant for the person who pressed Save."""


def _get_db() -> sqlite3.Connection:
    path = get_settings().home / "agents.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def vocabulary() -> dict[str, Any]:
    """Everything the screen needs to draw itself.

    Sent rather than duplicated in the frontend: a second copy of these lists in
    JavaScript is a second copy to keep current, and the one that drifts is the
    one somebody is choosing from.
    """
    return {
        "traits": [label for label, _ in TRAITS],
        "communication": [label for label, _ in COMMUNICATION],
        "thinking": [label for label, _ in THINKING],
        "autonomy": [dict(level) for level in AUTONOMY],
        "limits": {"traits": MAX_TRAITS, "communication": MAX_COMMUNICATION,
                   "thinking": MAX_THINKING, "extra": MAX_EXTRA},
        "default_autonomy": DEFAULT_AUTONOMY,
    }


def _clean(values: Any, allowed: tuple[tuple[str, str], ...], cap: int,
           what: str) -> list[str]:
    """The chosen values, in the order the vocabulary lists them.

    Ordered by the vocabulary rather than by what the user clicked, so the
    rendered sentence reads the same whichever order the chips were pressed —
    otherwise a save that changed nothing still rewrites the file.
    """
    if values is None:
        return []
    if not isinstance(values, list):
        raise PersonaRejectedError(f"Could not read the {what}.")
    labels = [label for label, _ in allowed]
    chosen = {v for v in values if isinstance(v, str)}
    unknown = chosen - set(labels)
    if unknown:
        # Refused rather than dropped: a value we do not know is a client that
        # disagrees with us about the vocabulary, and silently discarding it
        # would make the screen show a choice that was never stored.
        raise PersonaRejectedError(f"That is not one of the {what}.")
    kept = [v for v in labels if v in chosen]
    if len(kept) > cap:
        raise PersonaRejectedError(
            f"Pick at most {cap} — more than that stops describing anything.")
    return kept


def get(agent_id: str) -> dict[str, Any] | None:
    """This agent's choices, or None if nobody has made any."""
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM agent_persona WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row:
        return None
    return {
        "traits": json.loads(row["traits"]),
        "communication": json.loads(row["communication"]),
        "thinking": json.loads(row["thinking"]),
        "autonomy": row["autonomy"] or DEFAULT_AUTONOMY,
        "extra": row["extra"],
        "updated_at": row["updated_at"],
    }


def render(choices: dict[str, Any]) -> str:
    """The choices as the markdown an agent actually reads.

    Sentences, not a list of adjectives: "You are supportive and concise" is
    something a model can act on, where `traits: [supportive, concise]` is a
    data structure it has to interpret first. Empty sections are left out
    entirely — a heading with nothing under it is the invitation to invent that
    `prompt._notes` already records.
    """
    parts: list[str] = []
    fragments = {"traits": dict(TRAITS), "communication": dict(COMMUNICATION),
                 "thinking": dict(THINKING)}

    def phrase(values: list[str], group: str) -> str:
        words = [fragments[group][v] for v in values if v in fragments[group]]
        if not words:
            return ""
        if len(words) == 1:
            return words[0]
        return ", ".join(words[:-1]) + " and " + words[-1]

    traits = phrase(choices.get("traits") or [], "traits")
    communication = phrase(choices.get("communication") or [], "communication")
    thinking = phrase(choices.get("thinking") or [], "thinking")

    if traits or communication or thinking:
        lines = ["## How you work", ""]
        if traits:
            lines.append(f"Be {traits}.")
        if communication:
            lines.append(f"When you answer: {communication}.")
        if thinking:
            lines.append(f"When you work something out: {thinking}.")
        parts.append("\n".join(lines))

    # Only when it is not the default. The default *is* how the agent already
    # behaves, so spelling it out would spend prefix tokens on every turn to
    # say nothing — and, worse, a persona record holding nothing but the
    # default would render a document and so replace a preset's shipped
    # instructions with three lines about asking first.
    level_key = choices.get("autonomy") or DEFAULT_AUTONOMY
    level = _BY_KEY.get(level_key)
    if level and level_key != DEFAULT_AUTONOMY:
        parts.append("## How much to decide yourself\n\n" + level["prompt"])

    extra = (choices.get("extra") or "").strip()
    if extra:
        # Last, and under its own heading: it is the user's own words, and
        # anything they wrote outranks a sentence we generated from a chip.
        parts.append("## Also\n\n" + extra)

    return "\n\n".join(parts).strip() + "\n" if parts else ""


def set_persona(agent_id: str, *, traits: Any = None, communication: Any = None,
                thinking: Any = None, autonomy: str | None = None,
                extra: str | None = None) -> dict[str, Any]:
    """Record the choices and rewrite `persona.md` from them.

    Returns the stored choices. Raises `PersonaRejectedError` with a sentence to
    show. Writing the file is part of saving rather than a separate step: two
    calls would mean a state where the choices are stored and the agent is still
    running on the old document.
    """
    current = get(agent_id) or {}
    traits_v = _clean(traits, TRAITS, MAX_TRAITS, "ways of coming across") \
        if traits is not None else current.get("traits", [])
    comm_v = _clean(communication, COMMUNICATION, MAX_COMMUNICATION,
                    "communication styles") \
        if communication is not None else current.get("communication", [])
    think_v = _clean(thinking, THINKING, MAX_THINKING, "thinking styles") \
        if thinking is not None else current.get("thinking", [])

    level = current.get("autonomy", DEFAULT_AUTONOMY) if autonomy is None else autonomy
    if level not in _BY_KEY:
        raise PersonaRejectedError("That is not one of the autonomy levels.")

    extra_v = current.get("extra", "") if extra is None else extra
    if len(extra_v) > MAX_EXTRA:
        raise PersonaRejectedError(
            f"That is longer than this agent can carry "
            f"({MAX_EXTRA // 1000}k characters). Shorten it and save again.")

    choices = {"traits": traits_v, "communication": comm_v, "thinking": think_v,
               "autonomy": level, "extra": extra_v}

    now = datetime.now(UTC).isoformat()
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO agent_persona
            (agent_id, traits, communication, thinking, autonomy, extra, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            traits = excluded.traits,
            communication = excluded.communication,
            thinking = excluded.thinking,
            autonomy = excluded.autonomy,
            extra = excluded.extra,
            updated_at = excluded.updated_at
        """,
        (agent_id, json.dumps(traits_v), json.dumps(comm_v),
         json.dumps(think_v), level, extra_v, now))
    conn.commit()

    _write_document(agent_id, choices)
    apply_autonomy(agent_id, level)
    return {**choices, "updated_at": now}


def _write_document(agent_id: str, choices: dict[str, Any]) -> None:
    """Render and store `persona.md`, or clear it when nothing was chosen.

    Clearing means *deleting* rather than writing an empty file: no file is
    "use the instructions we ship", and an empty one is "the user deliberately
    cleared them". Writing a blank document for somebody who simply unticked
    their last chip would silently strip the agent of its shipped prompt.
    """
    from . import profile_files

    text = render(choices)
    with suppressed("rewriting persona.md from the persona choices"):
        if text.strip():
            profile_files.write(agent_id, profile_files.PERSONA, text)
        else:
            profile_files.clear(agent_id, profile_files.PERSONA)


def apply_autonomy(agent_id: str, level: str) -> None:
    """Make the level true of the tool list, not only of the prompt.

    `read_only` keeps the tools whose access tier is `READ` — derived from
    `tool_facts`, so a tool added tomorrow is classified by the same rule as
    every other — and remembers what it replaced. Leaving the level restores
    that list rather than guessing at one, which is the difference between a
    switch and a trapdoor.

    The other two levels do not touch the list at all. What an agent *may* use
    is the Permissions tab's question; this one is how much it decides for
    itself with what it has.
    """
    from .tool_facts import tool_access
    from .tool_overrides import get_tool_overrides

    store = get_tool_overrides()
    conn = _get_db()
    row = conn.execute("SELECT tools_before FROM agent_persona WHERE agent_id = ?",
                       (agent_id,)).fetchone()
    saved = json.loads(row["tools_before"]) if row and row["tools_before"] else None

    if level == "read_only":
        if saved is not None:
            return                      # already read-only; nothing to stash
        # `roster`, not `presets`. `custom` imports this module, `presets`
        # imports `custom`, so reaching for `presets.get_agent` here closed a
        # three-module cycle — which `tests/test_import_layering.py` caught,
        # because it counts a lazy import as the real edge it is. `roster` is
        # the late-binding seam this package already keeps for exactly the
        # readers that sit below the thing that owns the list.
        from . import roster
        try:
            agent = roster.get_agent(agent_id)
        except KeyError:
            return
        before = list(agent.tools or [])
        kept = [t for t in before if str(tool_access(t)) == "read"]
        conn.execute("UPDATE agent_persona SET tools_before = ? WHERE agent_id = ?",
                     (json.dumps(before), agent_id))
        conn.commit()
        store.set(agent_id, kept)
        return

    if saved is not None:
        store.set(agent_id, saved)
        conn.execute("UPDATE agent_persona SET tools_before = NULL WHERE agent_id = ?",
                     (agent_id,))
        conn.commit()


def forget(agent_id: str) -> None:
    """Drop the choices for a deleted agent.

    Ids are slugs, so the next agent built with that name lands on the same id
    — and would otherwise be born wearing a deleted agent's character.
    """
    conn = _get_db()
    conn.execute("DELETE FROM agent_persona WHERE agent_id = ?", (agent_id,))
    conn.commit()
