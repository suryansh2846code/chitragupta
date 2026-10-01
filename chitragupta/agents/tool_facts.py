"""What each built-in tool is called, what it does, and what that costs.

**A leaf, and it has to stay one.** `tests/test_import_layering.py` freezes the
`agents_tools` cycle as a ceiling that may not grow, and the first version of
this lived in `tools.py` — where `prompt.py` could not read it without dragging
the loop, the approvals and the library into one ten-module knot. The table
needs nothing from any of them: it is a name, a category and a capability per
tool. So it moved down, which is the first of the three shapes `/CLAUDE.md`
names for a fact that is wanted above where it sits.

Nothing here may import from `agents/`. `connectors.capability` is the one
dependency, and it is the point: a built-in declares itself in the same
`verb:resource` vocabulary as every connector and every action, so one gate can
reason about all three without knowing which is behind a given act.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..connectors.capability import Access, UnknownCapabilityError
from ..connectors.capability import parse as parse_capability

# ── what a person calls each tool ──────────────────────────────────────────
# `name` is the id the model calls and the agent stores. It is not a label: a
# user reading "whats_true_about_me" is reading our variable name, and a list
# of thirty-two of them is a wall nobody can choose from.
#
# So every built-in carries ONE WORD and a category. One word because the
# category already says the subject — under Memory, "Search" needs no further
# qualification, and "Search your brain" only repeats the heading. The category
# is what turns thirty-two rows into nine short lists.
#
# Lives here, not in the UI. A label table in the frontend would be a name
# chain — exactly what a consumer must never grow — and the same names are
# wanted by anything else that lists tools.
_FACTS: dict[str, tuple[str, str, str]] = {
    # Memory — the brain, and what it believes about the user
    "search_brain":             ("Search",    "Memory", "search:memory"),
    "who_is":                   ("People",    "Memory", "read:memory"),
    "whats_true_about_me":      ("Profile",   "Memory", "read:memory"),
    "timeline":                 ("Timeline",  "Memory", "read:memory"),
    "why_do_you_think_that":    ("Evidence",  "Memory", "read:memory"),
    "check_for_contradictions": ("Conflicts", "Memory", "read:memory"),
    "correct_fact":             ("Correct",   "Memory", "update:memory"),
    "forget_fact":              ("Forget",    "Memory", "update:memory"),
    "remember":                 ("Remember",  "Memory", "create:memory"),
    "list_entities":            ("Topics",    "Memory", "read:memory"),
    # Tasks — things to do, and commitments still open
    "add_task":                 ("Add",       "Tasks", "create:task"),
    "list_tasks":               ("List",      "Tasks", "read:task"),
    "complete_task":            ("Done",      "Tasks", "update:task"),
    "create_open_loop":         ("Track",     "Tasks", "create:task"),
    "list_open_loops":          ("Pending",   "Tasks", "read:task"),
    "awaiting_reply":           ("Waiting on", "Tasks", "read:task"),
    "needs_reply":              ("Owed",      "Email", "read:email"),
    "meeting_prep":             ("Prep",      "Calendar", "read:event"),
    "what_i_did":               ("History",   "Automations", "read:routine"),
    "complete_open_loop":       ("Close",     "Tasks", "update:task"),
    # The things it can reach
    "gmail_search":             ("Search",    "Email", "search:email"),
    "list_mail":                ("List",      "Email", "read:email"),
    "read_thread":              ("Read",      "Email", "read:thread"),
    "whats_tracked":            ("Check",     "Measurements", "read:measurement"),
    "measurement_history":      ("Trend",     "Measurements", "read:measurement"),
    "log_measurement":          ("Record",    "Measurements", "create:measurement"),
    "forget_measurement":       ("Correct",   "Measurements", "update:measurement"),
    "list_exercises":           ("Lifts",     "Training", "read:measurement"),
    "lift_progress":            ("Progress",  "Training", "read:measurement"),
    "training_load":            ("Load",      "Training", "read:measurement"),
    "list_chats":               ("List",      "Messages", "read:chat"),
    "read_chat":                ("Read",      "Messages", "read:message"),
    "calendar_lookup":          ("Schedule",  "Calendar", "read:event"),
    "find_time":                ("Find a time", "Calendar", "read:calendar"),
    "web_search":               ("Search",    "Web", "search:webpage"),
    # Websites the user has allowed. Their own group rather than folded into
    # "Web": a search returns public results, and these read pages the user is
    # signed in to — which is a different thing to hand an agent, and the person
    # ticking the box should see it as one.
    "browse_open":              ("Open page", "Websites you allow", "read:webpage"),
    "browse_read":              ("Re-read",   "Websites you allow", "read:webpage"),
    "browse_find":              ("Find on page", "Websites you allow", "read:webpage"),
    "browse_wait":              ("Wait for page", "Websites you allow", "read:webpage"),
    "browse_click":             ("Click",      "Websites you allow", "update:webpage"),
    "browse_type":              ("Type",       "Websites you allow", "update:webpage"),
    "browse_submit":            ("Send",       "Websites you allow", "update:webpage"),
    "browse_select":            ("Choose",     "Websites you allow", "update:webpage"),
    "browse_press":             ("Key",        "Websites you allow", "update:webpage"),
    "browse_reveal":            ("Scroll",     "Websites you allow", "read:webpage"),
    "browse_back":              ("Back",       "Websites you allow", "read:webpage"),
    "browse_sites":             ("Which sites", "Websites you allow", "read:webpage"),
    "what_i_looked_at":         ("History",    "Websites you allow", "read:webpage"),
    # Your Mac — the powers worth naming as a group, because they are the ones
    # a person wants to see gathered before deciding
    "list_dir":                 ("Browse",    "Your Mac", "read:folder"),
    "find_file":                ("Find",      "Your Mac", "search:file"),
    "read_file":                ("Read",      "Your Mac", "read:file"),
    "write_file":               ("Write",     "Your Mac", "create:file"),
    "edit_file":                ("Edit",      "Your Mac", "update:file"),
    "move_file":                ("Move",      "Your Mac", "update:file"),
    "run_python":               ("Run",       "Your Mac", "execute:process"),
    # Agents — asking the rest of the team, and thinking out loud
    "ask_agent":                ("Ask",       "Agents", "read:agent"),
    "ask_agents":               ("Survey",    "Agents", "read:agent"),
    "update_plan":              ("Plan",      "Agents", "update:agent"),
    # Automations — work that runs without being asked
    "list_routines":            ("List",      "Automations", "read:routine"),
    "pause_routine":            ("Pause",     "Automations", "update:routine"),
    "list_scheduled":           ("Queued",    "Automations", "read:routine"),
    "list_pending_approvals":   ("Approvals", "Automations", "read:routine"),
    # Connectors — the deep sources, as opposed to an MCP server's own tools
    "sync_source":              ("Sync",      "Connectors", "read:record"),
    "search_source":            ("Search",    "Connectors", "search:record"),
}

#: The order the categories read in. Memory first because it is what makes an
#: agent know the user at all; the two that reach outside the app last, because
#: they are the ones worth pausing over.
#:
#: **A second hand-kept list that had already drifted.** "Websites you allow"
#: was in `_LABELS` and missing from here, so the panel rendered it *after*
#: "Other" — below the bucket meant for things nobody had got round to naming.
#: `tests/test_tool_labels.py` now fails if the two disagree in either
#: direction, because the drift is silent and shows up only as a screen that
#: reads oddly.
TOOL_CATEGORIES = ("Memory", "Tasks", "Messages", "Email", "Calendar",
                   "Measurements", "Training", "Web", "Agents", "Automations",
                   "Connectors", "Websites you allow", "Your Mac")


def tool_label(name: str) -> tuple[str, str]:
    """`(label, category)` for a built-in, falling back to its own name.

    A tool added without an entry still renders — under "Other", with its id as
    the label, which is ugly on purpose: it is visible enough to be fixed and
    not so broken that the screen fails.
    """
    found = _FACTS.get(name)
    return (found[0], found[1]) if found else (name, "Other")


def tool_capability(name: str) -> str:
    """What this tool does, as `verb:resource`, or "" for one that has not said.

    The same vocabulary `connectors/capability.py` defines and every connector
    and action already declares itself in — deliberately, rather than a second
    taxonomy living in `agents/`. One gate has to be able to reason about
    "changes a web page" without caring whether a built-in tool, a connector or
    an MCP server is behind it, and two vocabularies for that question is how it
    ends up reasoning about neither. The panel showing sixty-four raw tool names
    is what it looks like when nothing can reason about them at all.
    """
    found = _FACTS.get(name)
    return found[2] if found else ""


def tool_access(name: str) -> Access:
    """How much permission this tool needs: read, write, outbound, destructive.

    **Derived from the verb, never stored.** `capability.Access` already answers
    this for every connector and every action, and the one thing that must not
    happen is a second table in `agents/` that disagrees with it — a tool filed
    as a write while the identical action is filed as destructive is a gate that
    gives different answers about the same act.

    An undeclared tool is `DESTRUCTIVE`, not `READ`. Unknown fails closed is the
    rule `capability.parse` was written around, and it matters more here: the
    tempting default reads a tool nobody classified as the harmless one, which
    is exactly how an unclassified write gets granted in bulk.
    """
    raw = tool_capability(name)
    if not raw:
        return Access.DESTRUCTIVE
    try:
        return parse_capability(raw).access
    except UnknownCapabilityError:     # pragma: no cover - the test forbids it
        return Access.DESTRUCTIVE


# ── what a person is actually deciding about ─────────────────────────────
#
# **Sixty-four toggles is not a permission screen, it is an inventory.** The
# panel asked "which tool is this?" sixty-four times, in thirteen categories,
# before the user had sent the agent a single message — which is the moment they
# know least about what it will need.
#
# The better question is what it costs to be wrong, and that has two parts the
# tool table already knows: **what it touches** (the resource) and **what it
# does to it** (the verb's `Access`). Crossing them gives four groups and a
# read/change split inside each, which is six or seven decisions rather than
# sixty-four — and every one of them is a sentence a person can actually weigh.
#
# Neither axis is new. `Access` has decided tiers for every connector and every
# action since `capability.py` was written; this is the first thing to ask it
# about a built-in.


@dataclass(frozen=True)
class Group:
    """One thing an agent can be given, as a person would describe it."""

    key: str
    label: str
    blurb: str
    #: True for the group that cannot reach past this machine. Granted at
    #: creation and never shown as a toggle — see `ALWAYS`.
    always: bool = False


#: Resource → the group it belongs to. The split that matters is **reach**:
#: what can never leave this machine, versus what touches an account, a website
#: or the user's own files.
_GROUP_OF: dict[str, str] = {
    "memory": "on_device", "task": "on_device", "measurement": "on_device",
    "agent": "on_device", "routine": "on_device",

    "email": "accounts", "thread": "accounts", "draft": "accounts",
    "chat": "accounts", "message": "accounts", "channel": "accounts",
    "event": "accounts", "calendar": "accounts", "record": "accounts",
    "contact": "accounts", "note": "accounts", "page": "accounts",

    "webpage": "websites",

    "file": "mac", "folder": "mac", "document": "mac", "process": "mac",
}

GROUPS: tuple[Group, ...] = (
    Group("on_device", "Its own memory and your day",
          "The brain, your tasks, your numbers, and asking the other agents. "
          "Nothing here leaves this machine.", always=True),
    Group("accounts", "Your connected accounts",
          "Mail, calendar, messages and anything you have connected. Only the "
          "accounts you connected, and only what you allow below."),
    Group("websites", "Websites",
          "Pages on sites you have allowed. Reading is one decision; changing "
          "a page — clicking, typing, sending — is another."),
    Group("mac", "Your Mac",
          "Files and folders on this computer. Running code is separate, and "
          "deliberately: what it can do is unbounded."),
)

#: The group every unrecognised resource falls into. `mac` rather than
#: `on_device` for `tool_access`'s reason — unknown fails closed, and the
#: tempting default files a thing nobody classified as the harmless one.
_FALLBACK_GROUP = "mac"

#: Granted when an agent is created, and not shown as a toggle at all.
#:
#: An agent that cannot read its own memory is not a lesser agent, it is a
#: broken one — and making somebody tick ten boxes to get there taught them the
#: screen was a formality, which is the worst possible thing to teach before the
#: boxes that matter.
ALWAYS = tuple(g.key for g in GROUPS if g.always)


def tool_group(name: str) -> str:
    """Which group a built-in belongs to, from the resource it names."""
    raw = tool_capability(name)
    if not raw:
        return _FALLBACK_GROUP
    try:
        resource = parse_capability(raw).resource.value
    except UnknownCapabilityError:     # pragma: no cover - the test forbids it
        return _FALLBACK_GROUP
    return _GROUP_OF.get(resource, _FALLBACK_GROUP)


def builtin_names() -> frozenset[str]:
    """Every built-in tool there is, from the table that names them.

    The same set as `tools.TOOL_DEFS`, read from here so that a caller which
    only needs to know *whether* a name is a built-in does not have to import
    the registry — and through it the loop, the approvals and the library.
    `tests/test_tool_permissions.py` fails if the two ever disagree.
    """
    return frozenset(_FACTS)


def granted_by_default() -> list[str]:
    """Every built-in an agent may have the moment it is created.

    The whole of tier one and nothing else. This is what makes creating an
    agent frictionless without making it a blank cheque: it can think, remember,
    plan and talk to the rest of the roster on day one, and it cannot touch an
    account, a website or a file until somebody says so.
    """
    return sorted(n for n in _FACTS if tool_group(n) in ALWAYS)


#: What an agent may ask for in a chat card, and what it may not.
#:
#: Reading and changing, never the irreversible tier. Running code is the one
#: thing that must be granted on the settings screen, deliberately and not in
#: the middle of a flow where somebody is trying to get something done — a tap
#: given to unblock a task is not the same decision as one given while reading
#: a page about what the tap means. `permissions.NEVER_UNATTENDED` already says
#: the equivalent about actions; this is it for capabilities.
ASKABLE = ("read", "change")


def tools_for(group: str, level: str) -> list[str]:
    """The built-ins a `group` + `level` grant covers, or [] for a bad pair.

    The one place a grant is turned into tool names. Returns empty rather than
    raising for anything it does not recognise, because the caller is a handler
    running behind a card and an unknown pair has to refuse rather than crash —
    and because the strings reach it from a model, which is exactly the input
    `capability.parse` fails closed on.
    """
    if level not in ASKABLE:
        return []
    for found in permission_groups():
        if found["key"] != group:
            continue
        # A group that is always on has nothing to grant: asking for it would
        # produce a card that changes nothing and still wants a tap.
        return [] if found["always"] else list(found.get(level) or [])
    return []


def permission_groups() -> list[dict[str, Any]]:
    """The permission screen, derived rather than hand-arranged.

    Each group carries its tools already split by what they do, so the UI
    renders a sentence and two switches instead of a list of function names. The
    panel never sees a capability string or an `Access` — it sees "read" and
    "change", which are the words on the switches.

    Walks `_FACTS` rather than the tool registry, because this module may not
    import it: the registry is in `tools.py`, which imports *this*. They are the
    same set, and `tests/test_tool_permissions.py` fails if they ever drift —
    which is the honest way to hold that, rather than an import that would
    rebuild the cycle this file was extracted to break.
    """
    out: list[dict[str, Any]] = []
    for group in GROUPS:
        reads, changes, runs = [], [], []
        for name in sorted(_FACTS):
            if tool_group(name) != group.key:
                continue
            tier = tool_access(name)
            if tier is Access.READ:
                reads.append(name)
            elif tier is Access.DESTRUCTIVE:
                runs.append(name)
            else:
                changes.append(name)
        if not (reads or changes or runs):
            continue
        out.append({
            "key": group.key, "label": group.label, "blurb": group.blurb,
            "always": group.always,
            "read": reads, "change": changes, "run": runs,
        })
    return out
