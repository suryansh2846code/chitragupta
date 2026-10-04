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
    #: Where the OTHER half of this permission is set, when there is one.
    #:
    #: A switch here says the agent may change a web page. WHICH pages is a
    #: per-site decision, kept on another screen, and a panel that grants one
    #: and never mentions the other is a dead end: the user turns everything on
    #: here, the agent is still refused, and the screen that would fix it was
    #: never named. The same is true of accounts — "may read your mail" means
    #: nothing until a mailbox is connected.
    #:
    #: A screen ID and the words on the button. The ID is a name the frontend
    #: owns and maps to its own opener; an ID it does not recognise draws no
    #: button, so adding one here can never produce a control that goes nowhere.
    more_screen: str = ""
    more_label: str = ""


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
          "accounts you connected, and only what you allow below.",
          more_screen="connectors", more_label="Connect an account"),
    Group("websites", "Websites and the browser",
          "Opening pages in a real browser, on sites you have allowed. Reading "
          "a page is one decision; changing one — clicking, typing, sending — "
          "is another. Which sites it may reach is a list of its own.",
          more_screen="connectors", more_label="Choose which sites"),
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
        row: dict[str, Any] = {
            "key": group.key, "label": group.label, "blurb": group.blurb,
            "always": group.always,
            "read": reads, "change": changes, "run": runs,
        }
        # Only when there IS one. An absent key draws no button, which is the
        # difference between "nothing more to do here" and a control that
        # leads nowhere.
        if group.more_screen:
            row["more"] = {"screen": group.more_screen, "label": group.more_label}
        out.append(row)
    return out


# ── the same tools, arranged the way a person thinks about them ───────────
#
# `GROUPS` above is the **gate's** axis: how far a capability reaches, which is
# the right question for `request_permission`, for `prompt._withheld` and for
# deciding what a grant covers. It is the wrong question for a settings screen.
# It puts Gmail, your calendar, your messages, Notion and Linear on one card
# called "Your connected accounts", so a user who wants to say *this agent may
# read GitHub and not my mail* has no control that says it. People think in
# apps.
#
# So this is a second question over the same key. Not a second taxonomy — the
# resource still decides, exactly as `_GROUP_OF` uses it — just asked at the
# granularity somebody actually sets switches at. Both are derived from
# `_FACTS`, so neither can drift from the tool table, and
# `tests/test_tool_permissions.py` fails if a built-in lands on no card.

#: Resource → the app it belongs to.
_APP_OF: dict[str, str] = {
    "memory": "on_device", "task": "on_device", "measurement": "on_device",
    "agent": "on_device", "routine": "on_device",

    "email": "gmail", "thread": "gmail", "draft": "gmail",
    "event": "calendar", "calendar": "calendar",
    "chat": "messages", "message": "messages", "channel": "messages",
    "contact": "contacts", "note": "sources", "page": "sources",
    "record": "sources",

    "webpage": "browser",

    "file": "mac", "folder": "mac", "document": "mac", "process": "mac",
}

#: The few tools whose resource is honest and still lands them in the wrong
#: place. `web_search` is `search:webpage` because that is what it returns, and
#: it is not the browser: it reads public results and never opens a page the
#: user is signed in to. Filing it under "The browser" would put a harmless
#: tool behind the switch that is the most dangerous one on the screen.
_APP_OVERRIDE: dict[str, str] = {"web_search": "web"}

#: Where a tool nobody classified goes. `mac` for `_FALLBACK_GROUP`'s reason:
#: unknown fails closed, and the harmless-looking default is the trap.
_FALLBACK_APP = "mac"


@dataclass(frozen=True)
class App:
    """One app or surface, as a card with at most three switches."""

    key: str
    label: str
    blurb: str

    #: The word on the second switch. Never a generic "Write" everywhere: a
    #: switch reading the same word on files, on a web page and on somebody
    #: else's inbox is a switch that was set for one of them and granted all
    #: three. `CLAUDE.md` forbids folding `outbound` into `change` for exactly
    #: this, and the wording is the half of that rule a person can see.
    write_label: str = "Write"

    #: The third switch, and only `mac` has one. Running code is unbounded, so
    #: it is never inside the word "write".
    run_label: str = ""

    #: What this app's *changes* are, when they are actions rather than tools —
    #: sending mail, adding an event, running somebody else's verb. There is no
    #: switch for those: each comes to the user as a card. The row says so,
    #: because an app showing only "Read" reads as an app that cannot do
    #: anything else, which is false and is what sends people hunting for a
    #: setting that does not exist.
    ask_label: str = ""
    ask_blurb: str = ""

    #: Which allow-list the cards for this app are judged against, so the row
    #: can show **this app's** standing grants rather than sending somebody to
    #: a screen holding everybody's. The value is `ActionSpec.recipient_kind`.
    #:
    #: Read-only on the card, deliberately. A grant is **made** from the
    #: approval it would have cleared — the queue offers it there with the
    #: exact value the gate reads, at the moment somebody learns they want one.
    #: A box on a settings screen asks them to predict it instead, and writes
    #: to the same global list from a second place.
    #:
    #: Declared beside the two strings above rather than derived from the
    #: action registry: `tool_facts` is a leaf and importing `actions` here
    #: would grow the frozen `agents_tools` cycle — the thing this module was
    #: extracted to stop. `tests/test_tool_permissions.py` pins each one
    #: against what the registry actually says.
    ask_kind: str = ""

    #: True for the surface that cannot leave this machine — granted at
    #: creation, never shown as a switch. Same meaning as `Group.always`.
    always: bool = False

    #: Where the other half of this permission is set, when it is on another
    #: screen. Absent when the control is *in* the card, which is better: see
    #: `mac`, whose folders are opened from the card itself.
    more_screen: str = ""
    more_label: str = ""


APPS: tuple[App, ...] = (
    App("on_device", "Its own memory and your day",
        "The brain, your tasks, your numbers, and asking the other agents. "
        "Nothing here leaves this machine.", always=True),
    App("gmail", "Gmail",
        "Searching and reading the mail in the account you connected.",
        ask_label="Send", ask_blurb=(
            "Sending or drafting mail always comes to you as a card you "
            "confirm. This only changes when an automation runs it with "
            "nobody watching."),
        ask_kind="email_recipient",
        more_screen="connectors", more_label="Connect an account"),
    App("calendar", "Calendar",
        "Reading what is in the day, and finding a time that works.",
        ask_label="Add or change", ask_blurb=(
            "Creating, moving or cancelling an event always comes to you as a "
            "card you confirm. This only changes when an automation runs it "
            "with nobody watching."),
        # The same list as Gmail, and that is not a mistake: what an event
        # reaches is the people it invites.
        ask_kind="email_recipient",
        more_screen="connectors", more_label="Connect an account"),
    App("messages", "Messages",
        "Reading the chats in an app you connected — Telegram, Slack, "
        "WhatsApp.",
        ask_label="Send", ask_blurb=(
            "Sending a message always comes to you as a card you confirm. "
            "This only changes when an automation runs it with nobody "
            "watching."),
        # Its own list, keyed `app:chat`. A chat id means nothing outside the
        # app it came from, so it is never judged against the email one.
        ask_kind="chat_recipient",
        more_screen="connectors", more_label="Connect an account"),
    App("contacts", "Contacts", "People from an account you connected.",
        more_screen="connectors", more_label="Connect an account"),
    App("sources", "Your sources",
        "Pulling in anything new from a source you connected, and reading "
        "what came back.",
        more_screen="connectors", more_label="Connect a source"),
    App("web", "Web search",
        "Public search results. Nothing you are signed in to — that is the "
        "browser, below."),
    App("browser", "The browser",
        "Opening pages in a real browser, on sites you have allowed. Reading "
        "a page is one decision; changing one — clicking, typing, sending — "
        "is another.",
        write_label="Change",
        more_screen="connectors", more_label="Choose which sites"),
    App("mac", "Your Mac",
        "Files and folders on this computer, inside the folders you open to "
        "agents and nowhere else.",
        write_label="Write", run_label="Run code"),
)

_APP_BY_KEY: dict[str, App] = {a.key: a for a in APPS}


def tool_app(name: str) -> str:
    """Which app card a built-in belongs on, from the resource it names."""
    override = _APP_OVERRIDE.get(name)
    if override:
        return override
    raw = tool_capability(name)
    if not raw:
        return _FALLBACK_APP
    try:
        resource = parse_capability(raw).resource.value
    except UnknownCapabilityError:     # pragma: no cover - the test forbids it
        return _FALLBACK_APP
    return _APP_OF.get(resource, _FALLBACK_APP)


def permission_apps() -> list[dict[str, Any]]:
    """The settings screen, one row per app, derived rather than arranged.

    Same shape and same derivation as `permission_groups()` — `read`, `change`
    and `run` are tool names already split by what they do, so the panel
    renders two switches and a sentence rather than a list of function names.

    An app with no tools at all is dropped: a card nobody can switch anything
    on is a heading that teaches the screen is decorative. An app whose only
    changes are *actions* keeps its card — `ask` is what it says instead of a
    switch, and saying nothing there is what makes a user hunt for a setting
    that does not exist.
    """
    out: list[dict[str, Any]] = []
    for app in APPS:
        reads, changes, runs = [], [], []
        for name in sorted(_FACTS):
            if tool_app(name) != app.key:
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
        row: dict[str, Any] = {
            "key": app.key, "label": app.label, "blurb": app.blurb,
            "always": app.always,
            "read": reads, "change": changes, "run": runs,
            "write_label": app.write_label, "run_label": app.run_label,
        }
        if app.ask_label:
            row["ask"] = {"label": app.ask_label, "blurb": app.ask_blurb,
                          "kind": app.ask_kind}
        if app.more_screen:
            row["more"] = {"screen": app.more_screen, "label": app.more_label}
        out.append(row)
    return out
