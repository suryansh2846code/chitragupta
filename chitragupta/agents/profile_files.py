"""The two files an agent keeps about itself, on disk, in the user's own words.

    ~/Library/Chitragupta/agents/<agent_id>/persona.md   what it is, and how it works
    ~/Library/Chitragupta/agents/<agent_id>/memory.md    what it has learned since

Files rather than another table, because these two are **prose a person writes
and reads**. Everything else an agent carries is structured and belongs in
`agents.db` beside `agent_models`, `agent_avatars` and `agent_tool_overrides` —
a model id is not something anybody opens in a text editor. These are, and a
user who wants to keep, diff, back up or paste one should not have to go
through us to do it.

Three rules hold the pair honest.

**`persona.md` is an override, never an edit of the original.** The shipped
presets stay code constants, exactly as `tool_overrides` kept the shipped tool
lists: a preset whose text is whatever the user last typed is a preset a later
release can never improve, and reset-to-default would need to remember what the
default used to be instead of just deleting a file.

**One writer per fact.** A custom agent's prompt lives in
`custom_agents.system_prompt` *until* a file exists for it, and `custom.py`
writes that file at creation — so from the moment an agent is born the file is
the answer and the column is history. Two live copies of one piece of prose is
the drift trap this repo keeps paying for, and the copy that drifts is always
the one being read.

**Nothing here parses the prose.** `persona.md` is the instruction body and
nothing else — no front matter, no title line, no name or role to extract. A
format with a parser is a format a user can break by typing, and the failure
would be an agent that silently lost its instructions. Name and role stay
structured, where a form field can own them.

Two further things it would be easy to get wrong, both of which are why this
module exists at all rather than the routes doing it inline:

* **The file name is matched against a closed set, never sanitised.** A name
  that arrives from a URL and gets cleaned up is a path traversal with extra
  steps; `secrets.json` sits two directories above these.
* **The agent id is part of a path too**, and it also arrives from a URL. It is
  checked for shape *and* the resolved directory is checked to be inside the
  root, because those catch different mistakes and only one of them survives
  somebody later deciding ids may contain a dot.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ..config import get_settings
from ..log import get_logger, suppressed

log = get_logger(__name__)

#: The agent's instructions — what `Agent.system_prompt` resolves to.
PERSONA = "persona.md"

#: What this agent has learned about doing its job for this user.
MEMORY = "memory.md"

#: The whole set. Matched by equality: see the module docstring.
FILES = frozenset({PERSONA, MEMORY})

#: How much of each file an agent can carry.
#:
#: Both ride in the system message, which is the cached prefix — so every byte
#: here is re-read on every round of every turn, on the user's own key. The
#: persona is deliberately the roomier of the two because the user writes it
#: once, deliberately; the notes accumulate on their own, and a cap is the only
#: thing standing between "it learns" and "every turn costs more than the last".
LIMITS: dict[str, int] = {PERSONA: 16 * 1024, MEMORY: 6 * 1024}

#: What an id may look like. Deliberately narrower than "a safe path segment":
#: every id in the app is a slug (`custom._slug`) or a library key, so anything
#: else is a caller mistake and should be refused rather than accommodated.
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class FileRejectedError(ValueError):
    """We are not willing to store that.

    Carries a sentence meant for a person, because one is shown it — the same
    contract as `avatars.AvatarRejectedError`, for the same reason.
    """


def root() -> Path:
    return get_settings().home / "agents"


def agent_dir(agent_id: str) -> Path:
    """The directory for one agent. Raises `FileRejectedError` for a bad id.

    The containment check is not redundant with the regex. The regex is what we
    believe about ids today; the check is what is true about the path we are
    about to open, and it keeps holding if somebody later widens the regex.
    """
    if not _ID.match(agent_id or ""):
        raise FileRejectedError("That is not an agent.")
    base = root().resolve()
    path = (base / agent_id).resolve()
    if path.parent != base:
        raise FileRejectedError("That is not an agent.")
    return path


def _file(agent_id: str, name: str) -> Path:
    if name not in FILES:
        raise FileRejectedError("That is not one of this agent's files.")
    return agent_dir(agent_id) / name


def read(agent_id: str, name: str) -> str | None:
    """The file's text, or None when the agent does not have one.

    None and `""` are different answers and both are real: no file at all means
    "use the shipped default", and an empty file means "the user cleared it".
    Collapsing them would make clearing a persona silently restore the preset.
    """
    path = _file(agent_id, name)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        # One unreadable file costs this agent that file, not the whole screen
        # — the same call every other per-agent store makes.
        log.warning("could not read %s for %s: %s", name, agent_id, exc)
        return None


def write(agent_id: str, name: str, text: str) -> dict:
    """Replace one file. Raises `FileRejectedError` with a sentence to show."""
    path = _file(agent_id, name)
    text = (text or "").replace("\r\n", "\n")
    limit = LIMITS[name]
    if len(text.encode("utf-8")) > limit:
        raise FileRejectedError(
            f"That is longer than this agent can carry ({limit // 1024} KB). "
            "Shorten it and save again.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and moved into place: a crash halfway through a
    # plain write leaves a truncated persona, which is an agent that quietly
    # lost its instructions rather than an agent that failed to save.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        # `Path.replace` is `os.replace`: atomic within one filesystem, which
        # this is — the temporary file is made in the target's own directory
        # precisely so the move cannot cross one.
        Path(tmp).replace(path)
    except BaseException:
        with suppressed("removing a half-written agent file"):
            Path(tmp).unlink()
        raise
    return info(agent_id, name)


def clear(agent_id: str, name: str) -> bool:
    """Delete the file. For `persona.md` that is reset-to-shipped."""
    path = _file(agent_id, name)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def info(agent_id: str, name: str) -> dict:
    """What the profile screen shows about one file without opening it."""
    path = _file(agent_id, name)
    try:
        st = path.stat()
    except (FileNotFoundError, OSError):
        return {"name": name, "exists": False, "bytes": 0,
                "limit": LIMITS[name], "updated_at": None}
    return {
        "name": name,
        "exists": True,
        "bytes": st.st_size,
        "limit": LIMITS[name],
        "updated_at": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
    }


def for_prompt(agent_id: str, name: str) -> tuple[str, bool]:
    """`(text, was_truncated)` — what the model may carry of one file.

    A write over the limit is refused, so this only truncates a file somebody
    edited by hand on disk. It still has to be handled rather than trusted: the
    whole point of these files is that a person can open them outside the app.
    Cut on a line boundary, because half a sentence of instruction reads as a
    whole one.
    """
    text = read(agent_id, name)
    if text is None:
        return "", False
    raw = text.encode("utf-8")
    limit = LIMITS[name]
    if len(raw) <= limit:
        return text.strip(), False
    kept = raw[:limit].decode("utf-8", errors="ignore")
    cut = kept.rfind("\n")
    if cut > 0:
        kept = kept[:cut]
    return kept.strip(), True


def forget(agent_id: str) -> None:
    """Delete everything an agent kept. Called when the agent is deleted.

    An id is a slug of the name and so is deterministic: delete "Chotu", build
    another "Chotu", and it lands on the same id. A `memory.md` left behind
    would hand a brand-new agent a deleted one's notes about a job it never
    did — the same hazard `custom.delete` already names for tool overrides, and
    a far more visible one, because these notes are prose it will act on.
    """
    with suppressed("removing an agent's profile files"):
        shutil.rmtree(agent_dir(agent_id), ignore_errors=True)
