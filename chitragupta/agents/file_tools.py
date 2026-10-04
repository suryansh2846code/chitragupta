"""Reading and writing files, inside folders the user chose.

This is the capability that changes what an agent can finish. Without it the
answer to "reconcile this CSV" is a description of how you would reconcile it.
On a hosted assistant giving an agent a filesystem is hard and frightening; on
an app that already runs entirely on the user's machine it is the natural next
tool — but only because the boundary below is real.

**The boundary is the folder, and the user draws it.** There are no default
grants. An agent can reach nothing until the user points at a folder, the same
consent gesture the Connectors panel already uses for the Files source. `$HOME`
cannot be granted wholesale, because a grant that wide is not a boundary.

**Why that matters more here than it looks.** These agents read email, issues,
documents and messages written by other people. "Save this to
~/.ssh/authorized_keys" is a sentence an injection would write, and the model is
not the thing standing between that sentence and the filesystem — `_resolve` is.
So it resolves symlinks before deciding, compares real paths rather than
strings, and refuses anything it cannot place inside a grant. A refusal is
cheap; the other mistake is not.

**And the boundary has two layers, which answer different questions.** The
grant says what may be reached from this app at all — one list, the user's
consent, the thing `_resolve` enforces. The *scope* says which of those folders
one agent works in, because a team of agents sharing one machine is not a team
sharing one filing cabinet: the folders a social-media agent should read are
not the ones a tax agent should, and before this there was no way to say so.
A scope only ever narrows a grant, so closing a folder still closes it for
everybody.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..log import get_logger, suppressed
from .acting import acting
from .results import ToolResult

log = get_logger(__name__)

#: Where the granted folders are stored.
GRANTS_KEY = "agent_file_roots"

#: A file the model will read back is charged for, so a big one is cut and the
#: model is told it was — assuming it saw everything is the worse failure.
MAX_READ_CHARS = 20_000

#: A write is the user's disk. Large enough for a document or a data file,
#: small enough that a runaway loop cannot fill a volume.
MAX_WRITE_CHARS = 200_000

#: Entries in one listing.
MAX_ENTRIES = 200

#: Extensions that are text as far as an agent is concerned. Anything else is
#: refused on read rather than handed to a model as mojibake.
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".log", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".html", ".htm", ".css", ".scss", ".sql", ".sh", ".rb", ".go",
    ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".hpp", ".xml",
    ".rst", ".tex", ".env", ".gitignore", "",
}


def _store():
    from ..core.store import get_store
    return get_store()


def granted_roots() -> list[str]:
    """The folders the user has opened to agents. Empty by default."""
    raw = None
    with suppressed("reading the granted agent folders"):
        raw = _store().get_meta(GRANTS_KEY)
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(v) for v in values if isinstance(v, str)] if isinstance(values, list) else []


def folder_problem(path: str) -> str:
    """Why this folder cannot be opened to agents, or "" if it can.

    **Asked before it is answered, as well as while answering it.** An agent can
    now ask for a folder in the chat (`agents/access.py`), and that panel draws
    a switch per thing asked for — so it has to know which asks are refusable
    before it offers a control for one. `folder:/` otherwise became a switch
    labelled "Open " whose only possible outcome was this sentence.

    One function rather than the same two rules written in both places: a
    boundary that one screen enforces and another describes differently is not a
    boundary. `grant_folder` raises what this returns.
    """
    candidate = Path(str(path or "")).expanduser().resolve()
    if not candidate.is_dir():
        return f"{candidate} is not a folder that exists"
    if candidate == Path.home().resolve() or candidate == Path("/"):
        # A grant this wide is not a boundary, it is the absence of one.
        return ("Pick a folder inside your home directory rather than the whole "
                "of it — the point of choosing is that everything else stays out")
    return ""


def grant_folder(path: str) -> dict:
    """Open a folder to agents. Raises ValueError with a reason a person reads."""
    problem = folder_problem(path)
    if problem:
        raise ValueError(problem)
    candidate = Path(str(path or "")).expanduser().resolve()

    roots = granted_roots()
    if str(candidate) not in roots:
        roots.append(str(candidate))
        _store().set_meta(GRANTS_KEY, json.dumps(roots))
        log.info("agents granted access to %s", candidate)
    return {"path": str(candidate), "roots": roots}


def revoke_folder(path: str) -> bool:
    candidate = str(Path(str(path or "")).expanduser().resolve())
    roots = granted_roots()
    if candidate not in roots:
        return False
    roots.remove(candidate)
    _store().set_meta(GRANTS_KEY, json.dumps(roots))
    log.info("agents no longer have access to %s", candidate)
    return True


# ── which of those folders THIS agent may see ────────────────────────────
#
# The grant above is the boundary: it says what may be reached from this app at
# all, it is written once, and `_resolve` is what stands between an injected
# "save this to ~/.ssh/authorized_keys" and the disk. That has not moved.
#
# This is a second, narrower question, and it is the user's rather than the
# security model's: **which of those folders does this particular agent work
# in?** A social-media agent and a tax agent on the same machine have no
# business reading each other's folders, and until now they could not help it —
# one list, every agent, no way to say otherwise. The folders the Connectors
# panel ingests are the opposite of this on purpose: those go into the brain,
# which is shared by everybody.
#
# Narrowing only. A scope can never reach a folder the user has not granted, so
# the intersection below is not an optimisation — it is what makes revoking a
# grant still revoke it everywhere.

#: `{agent_id: [paths]}`. A key that is **absent** and a key holding `[]` mean
#: different things and must stay distinguishable — the same distinction
#: `tool_overrides` draws, and for the same reason. Absent is "never chosen",
#: and takes `default_scope()`. `[]` is the user saying *look at nothing*,
#: which is a thing they asked to be able to say.
SCOPES_KEY = "agent_file_scopes"

#: What an agent that has never been given a scope sees, captured **once**.
#:
#: Without this the feature has to pick between two wrong answers. Default to
#: every granted folder and "exclusive to this agent" is a lie: picking a
#: folder for one agent hands it to every agent that has not been configured.
#: Default to nothing and every machine that already had folders open loses
#: them on upgrade, silently, which is the one thing the root `CLAUDE.md`
#: forbids outright.
#:
#: So the answer is frozen at the moment this code first runs: whatever was
#: granted *then* is what an unconfigured agent sees, and nothing granted
#: afterwards is ever added to it. Existing machines carry on working; every
#: folder chosen from here on belongs to the agent it was chosen for.
DEFAULT_SCOPE_KEY = "agent_file_scope_default"


def _read_json(key: str, fallback):
    # A literal label, not an f-string: `suppressed` is what makes a swallowed
    # failure legible afterwards, and `tests/test_failures_are_recorded.py`
    # reads these labels out of the AST — a computed one is a blank to it.
    with suppressed("reading a stored folder list"):
        raw = _store().get_meta(key)
        if not raw:
            return fallback
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            log.warning("unreadable %s — ignoring it", key)
    return fallback


def default_scope() -> list[str]:
    """What an agent with no scope of its own sees. Captured once, then fixed."""
    stored = _read_json(DEFAULT_SCOPE_KEY, None)
    if isinstance(stored, list):
        return [str(v) for v in stored if isinstance(v, str)]
    captured = granted_roots()
    with suppressed("recording the folders agents could already reach"):
        _store().set_meta(DEFAULT_SCOPE_KEY, json.dumps(captured))
    return captured


def _scopes() -> dict[str, list[str]]:
    value = _read_json(SCOPES_KEY, {})
    if not isinstance(value, dict):
        return {}
    return {str(k): [str(p) for p in v if isinstance(p, str)]
            for k, v in value.items() if isinstance(v, list)}


def agent_scope(agent_id: str) -> list[str] | None:
    """The folders this agent was given, or None if it was never asked.

    None is not `[]`. The caller has to tell them apart, which is why this
    returns the stored value rather than a resolved one.
    """
    return _scopes().get(str(agent_id or ""))


def set_agent_scope(agent_id: str, folders: list[str] | None) -> list[str]:
    """Choose this agent's folders. `None` puts it back to undecided.

    Every path is granted on the way in, because choosing a folder here is the
    same consent gesture as choosing one in the panel — and because a scope
    naming a folder that was never granted would be a switch that cannot do
    anything. `grant_folder` raises the sentence a person reads.
    """
    key = str(agent_id or "").strip()
    if not key:
        raise ValueError("Say which agent.")
    scopes = _scopes()
    if folders is None:
        scopes.pop(key, None)
        _store().set_meta(SCOPES_KEY, json.dumps(scopes))
        return roots_for(key)

    chosen: list[str] = []
    for raw in folders:
        resolved = grant_folder(str(raw))["path"]
        if resolved not in chosen:
            chosen.append(resolved)
    scopes[key] = chosen
    _store().set_meta(SCOPES_KEY, json.dumps(scopes))
    log.info("agent %s works in %d folder(s)", key, len(chosen))
    return chosen


def forget_agent_scope(agent_id: str) -> None:
    """Drop an agent's scope — for when the agent itself is deleted."""
    scopes = _scopes()
    if scopes.pop(str(agent_id or ""), None) is not None:
        _store().set_meta(SCOPES_KEY, json.dumps(scopes))


def roots_for(agent_id: str) -> list[str]:
    """The folders this agent can actually reach, now.

    Intersected with the live grant list rather than trusted: closing a folder
    in the panel has to close it for everyone, including an agent that still
    names it in a scope written before.
    """
    granted = granted_roots()
    chosen = agent_scope(agent_id)
    if chosen is None:
        chosen = default_scope()
    allowed = {str(Path(r).resolve(strict=False)) for r in granted}
    return [c for c in chosen if str(Path(c).resolve(strict=False)) in allowed]


def active_roots() -> list[str]:
    """The folders the agent whose turn is running may reach.

    **No acting agent means the grant list**, not nothing. An approved action
    is executed from the queue long after the turn that proposed it, with no
    turn context around it — `actions._attachments_for` goes through `_resolve`
    for exactly the injection reason that boundary exists, and scoping a
    question we cannot attribute to an agent would break attachments rather
    than protect anything. The boundary is still the grant.
    """
    who = acting()
    return roots_for(who) if who else granted_roots()


def _resolve(path: str) -> tuple[Path | None, str]:
    """The real path this refers to, if it is inside a granted folder.

    Resolves first and compares afterwards. A string comparison would be
    defeated by `granted/../../.ssh`, and a check that ignored symlinks would be
    defeated by a link planted inside a granted folder — which is reachable,
    because writing into that folder is exactly what an agent is allowed to do.
    """
    roots = active_roots()
    if not roots:
        return None, ("This agent has no folder to work in. The user chooses "
                      "which folders each agent can see from the folder button "
                      "beside the message box, or in the agent's Folders tab — "
                      "nothing on disk is reachable until they do.")
    raw = str(path or "").strip()
    if not raw:
        return None, "Give a path."

    target = Path(raw).expanduser()
    try:
        # strict=False: a file being written does not exist yet, but every
        # existing part of its path — including symlinks — is still resolved.
        real = target.resolve(strict=False)
    except OSError as exc:                         # pragma: no cover - defensive
        return None, f"Could not read that path: {exc}"

    for root in roots:
        root_real = Path(root).resolve(strict=False)
        if real == root_real or root_real in real.parents:
            return real, ""
    return None, (f"'{raw}' is outside every folder the user opened to agents. "
                  f"Allowed: {', '.join(roots)}.")


# ── the tools ────────────────────────────────────────────────────────────
def list_dir(path: str = "") -> ToolResult:
    """What is in a folder the user opened to this agent."""
    roots = active_roots()
    if not path and roots:
        listing = "\n".join(f"- {r}" for r in roots)
        return ToolResult("Folders you can work in:\n" + listing)

    real, why = _resolve(path)
    if real is None:
        return ToolResult.failed(why)
    if not real.is_dir():
        return ToolResult.failed(f"{real.name} is not a folder.")

    entries = []
    with suppressed("listing a granted folder"):
        for child in sorted(real.iterdir())[:MAX_ENTRIES]:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                entries.append(f"- {child.name}/")
            else:
                size = child.stat().st_size
                entries.append(f"- {child.name}  ({size:,} bytes)")
    if not entries:
        return ToolResult(f"{real} is empty.")
    return ToolResult(f"{real}:\n" + "\n".join(entries))


#: Enough to choose from, few enough to read. A folder of ninety drafts
#: answered in full is a wall nobody checks, which defeats the point of
#: showing the evidence at all.
MAX_MATCHES = 8

#: Anything deeper than this is somebody's node_modules, and walking it costs
#: the whole turn.
MAX_DEPTH = 6

#: Never walked into. Not a security boundary — `_resolve` is that — but a
#: search that returns forty vendored licence files has answered a different
#: question than the one asked.
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", ".git", "build",
             "dist", ".next", "target", "Library"}


def find_file(name: str, newest_first: bool = True) -> ToolResult:
    """Files matching a description, with what makes one the *latest*.

    "Take the latest proposal and send it to Rahul" is two questions, and the
    dangerous one is the first. An agent that attaches a file without saying
    which it picked has asked the user to approve a filename they did not
    choose — and attaching last quarter's draft is discovered by the recipient,
    not by them.

    So every match carries its modified date, its size and where it lives, and
    the caller is told to show them. The ranking is a suggestion; the evidence
    is what makes disagreeing possible.

    Searches only folders the user has opened. No grant, no answer.
    """
    words = [w for w in re.split(r"[\s_\-.]+", str(name or "").lower()) if w]
    if not words:
        return ToolResult.failed("Say what to look for — a word from the "
                                 "filename is enough.")
    roots = active_roots()
    if not roots:
        return ToolResult.failed(
            "This agent has no folder to work in, so there is nothing to "
            "search. The user chooses its folders from the folder button "
            "beside the message box.")

    found: list[tuple[float, Path]] = []
    for root in roots:
        base = Path(root)
        with suppressed("searching a granted folder"):
            for child in base.rglob("*"):
                if len(found) >= 400:            # a bounded walk, always
                    break
                if child.is_dir() or child.name.startswith("."):
                    continue
                relative = child.relative_to(base)
                if len(relative.parts) > MAX_DEPTH:
                    continue
                if SKIP_DIRS & set(relative.parts):
                    continue
                haystack = child.name.lower()
                if all(word in haystack for word in words):
                    found.append((child.stat().st_mtime, child))

    if not found:
        where = ", ".join(roots)
        return ToolResult(f"Nothing matching “{name}” in {where}.")

    found.sort(reverse=bool(newest_first))
    lines = [f"{len(found)} file(s) matching “{name}”"
             + (" — newest first:" if newest_first else " — oldest first:")]
    for when, path in found[:MAX_MATCHES]:
        stamp = datetime.fromtimestamp(when).strftime("%d %b %Y, %H:%M")
        lines.append(f"- {path.name}\n"
                     f"  {path}\n"
                     f"  modified {stamp} · {path.stat().st_size:,} bytes")
    if len(found) > MAX_MATCHES:
        lines.append(f"…and {len(found) - MAX_MATCHES} more.")
    lines.append("")
    lines.append("Say WHICH one you picked and when it was modified before "
                 "attaching it. Two drafts a week apart look identical in a "
                 "sentence.")
    return ToolResult("\n".join(lines))


def read_file(path: str) -> ToolResult:
    """Read a text file from inside a granted folder."""
    real, why = _resolve(path)
    if real is None:
        return ToolResult.failed(why)
    if not real.exists():
        return ToolResult.failed(f"There is no file at {real}.")
    if real.is_dir():
        return ToolResult.failed(f"{real.name} is a folder — use list_dir.")
    if real.suffix.lower() not in TEXT_SUFFIXES:
        return ToolResult.failed(
            f"{real.name} is not a text file, so there is nothing useful to read "
            "out of it here.")

    try:
        text = real.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult.failed(f"Could not read {real.name}: {exc}")

    if len(text) > MAX_READ_CHARS:
        # Say what was held back, so the model narrows instead of assuming.
        return ToolResult(
            text[:MAX_READ_CHARS]
            + f"\n\n[Cut here. {real.name} is {len(text):,} characters; this is "
              f"the first {MAX_READ_CHARS:,}.]",
            truncated=True)
    return ToolResult(text)


def write_file(path: str, content: str) -> ToolResult:
    """Write a text file inside a granted folder."""
    real, why = _resolve(path)
    if real is None:
        return ToolResult.failed(why)
    body = "" if content is None else str(content)
    if len(body) > MAX_WRITE_CHARS:
        return ToolResult.failed(
            f"That is {len(body):,} characters, over the {MAX_WRITE_CHARS:,} "
            "limit for one file.")
    if real.is_dir():
        return ToolResult.failed(f"{real.name} is a folder.")

    existed = real.exists()
    try:
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(body, encoding="utf-8")
    except OSError as exc:
        return ToolResult.failed(f"Could not write {real.name}: {exc}")

    verb = "Replaced" if existed else "Wrote"
    return ToolResult(f"{verb} {real} ({len(body):,} characters).")


def edit_file(path: str, find: str, replace: str, all: bool = False) -> ToolResult:
    """Replace an exact passage inside a file, leaving the rest untouched.

    Without this, changing one line of a long file means `read_file` then
    `write_file` with the whole thing rewritten from the model's memory of it.
    That is expensive — the file is billed twice, once in and once out — and it
    is *lossy*, which is the part that actually hurts: a 900-line CSV comes back
    with a row quietly dropped, and nothing in the result says so. The user
    finds out later, from the file.

    Three rules, and each one is a failure mode that has a name:

    * **`find` must appear exactly.** Not fuzzily, not after normalising
      whitespace. A near-match edited anyway is an edit to something the model
      did not actually see.
    * **`find` must appear ONCE**, unless `all` is passed deliberately. A
      snippet that matches in four places and is replaced in the first is a
      change to an arbitrary one of them, and which one is not knowable from
      the reply.
    * **Nothing is written when either rule is broken.** The failure says what
      to do about it — how many matches there were, and to widen the passage —
      because a model told only "no" re-issues the same call.
    """
    real, why = _resolve(path)
    if real is None:
        return ToolResult.failed(why)
    if not real.exists():
        return ToolResult.failed(f"There is no file at {real}.")
    if real.is_dir():
        return ToolResult.failed(f"{real.name} is a folder.")
    if real.suffix.lower() not in TEXT_SUFFIXES:
        return ToolResult.failed(
            f"{real.name} is not a text file, so there is nothing to edit here.")
    if not find:
        return ToolResult.failed(
            "Give the exact text to replace. To rewrite the whole file, use "
            "write_file.")

    try:
        before = real.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ToolResult.failed(f"Could not read {real.name}: {exc}")

    count = before.count(find)
    if count == 0:
        return ToolResult.failed(
            f"That passage is not in {real.name}. Read the file again — it may "
            "have changed, or the indentation may differ from what you have.")
    if count > 1 and not all:
        return ToolResult.failed(
            f"That passage appears {count} times in {real.name}, so replacing "
            "it would change an arbitrary one. Include more of the surrounding "
            "lines to make it unique, or pass all=true to change every one.")

    after = before.replace(find, replace, -1 if all else 1)
    if len(after) > MAX_WRITE_CHARS:
        return ToolResult.failed(
            f"That edit would make {real.name} {len(after):,} characters, over "
            f"the {MAX_WRITE_CHARS:,} limit.")

    try:
        real.write_text(after, encoding="utf-8")
    except OSError as exc:
        return ToolResult.failed(f"Could not write {real.name}: {exc}")

    changed = count if all else 1
    delta = len(after) - len(before)
    return ToolResult(
        f"Edited {real} — {changed} replacement{'s' if changed != 1 else ''}, "
        f"{delta:+,} characters.")


def move_file(path: str, to: str) -> ToolResult:
    """Rename or move a file, with BOTH ends inside a granted folder.

    `_resolve` twice, deliberately. Checking only the source would let
    `move("notes.md", "~/Library/LaunchAgents/x.plist")` walk a file straight
    out of the sandbox the grant exists to define — the destination is the
    half that decides where the file ends up, so it is the half that matters
    most.

    Refuses to overwrite. A move that silently replaces something is a
    deletion nobody was shown, and the user finds out when they look for the
    file that used to be there.
    """
    source, why = _resolve(path)
    if source is None:
        return ToolResult.failed(why)
    target, why = _resolve(to)
    if target is None:
        # Said in terms of the destination, so the user is not left thinking
        # the file they named is the problem.
        return ToolResult.failed(
            f"I cannot put it there — {why[0].lower()}{why[1:]}")

    if not source.exists():
        return ToolResult.failed(f"{source.name} is not there.")
    if source.is_dir():
        return ToolResult.failed(
            f"{source.name} is a folder. I only move files.")
    if target.exists():
        return ToolResult.failed(
            f"There is already a {target.name} there. Pick another name, or "
            f"the user can move the existing one first.")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    except OSError as exc:
        # A rename across two filesystems fails with EXDEV rather than
        # copying, and "Invalid cross-device link" is not a sentence anybody
        # should be shown.
        if getattr(exc, "errno", None) == 18:
            import shutil

            try:
                shutil.move(str(source), str(target))
            except OSError as second:
                return ToolResult.failed(
                    f"Could not move {source.name}: {second}")
        else:
            return ToolResult.failed(f"Could not move {source.name}: {exc}")

    renamed = source.parent == target.parent
    verb = "Renamed" if renamed else "Moved"
    where = target.name if renamed else str(target)
    return ToolResult(f"{verb} {source.name} to {where}.")
