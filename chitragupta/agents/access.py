"""What an agent can ask the user for, read back, and have granted by a tap.

An agent asked to shop answered: *"Settings → Agents & tools → Health & Fitness
→ let it browse the web, plus allow changes on `amazon.in`. Say 'go' when it's
on."* Every word of that was true. It is still the failure this module exists
to remove — it ends the conversation, hands the user a route to re-derive a
decision they were already being asked to make, and then asks them to come back
and say "go".

`request_permission` had already removed half of it for tool groups. The half it
could not reach was everything else: a site, a folder, an account. So the agent
fell back to prose for the whole ask, because a half-answer in a card and a
half-answer in a paragraph is worse than one paragraph.

So this is the **one vocabulary of things that can be asked for**, and the one
place that knows, for each of them, what it is called, whether it is already
granted, and who writes it. Three callers read it and none of them keeps a
second copy: `actions._request_permission` (the ask), the two `/access` routes
in `api/routes/agents.py` (the panel behind it), and `prompt.py` (what an agent
is told it may ask for).

**Nothing here grants anything on its own.** `grant()` is reached only from a
human press on a labelled control, and every branch of it delegates to the
module that already owned that write — `tool_overrides`, `browser.origins`,
`file_tools`. A second write path for a permission is how two screens come to
disagree about what the user allowed.

**Unknown fails closed.** `parse` and `normalise` return nothing rather than
guessing, because every string reaching them was chosen by a model, and some of
those models are reading a web page a stranger wrote.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..log import suppressed
from .tool_facts import ASKABLE, permission_groups, tools_for

#: The kinds of thing an agent may ask for. A closed list, and the reason it is
#: closed is the same reason `capability.parse` fails closed: the caller is a
#: handler behind a card, and the strings arrive from a model.
KINDS = ("tool_group", "site", "folder", "connector")

#: Site grants carry one of these, and they are `browser.origins`' own words.
#: Named here rather than imported so this module stays readable on its own;
#: `test_access.py` fails if they ever drift from `origins.READ`/`CHANGE`.
SITE_LEVELS = ("read", "change")


def askable_groups() -> tuple[str, ...]:
    """Tool groups an agent may ask for, derived rather than listed.

    Derived because a hand-kept copy of this drifted once already — "Websites
    you allow" was in one list and missing from another, and the panel rendered
    it below the bucket for things nobody had named. A group that is always on
    has nothing to grant, so it is not something to ask for either.
    """
    return tuple(row["key"] for row in permission_groups()
                 if not row["always"]
                 and any(row.get(level) for level in ASKABLE))


def nothing_to_ask_for(asked: str) -> str:
    """Why an ask resolved to nothing, naming the alternatives.

    Written here rather than at the handler, because the list of what *is*
    askable is here and a sentence that named it from somewhere else is a second
    copy waiting to go stale. The model chose `asked`, so it is quoted back: a
    refusal that does not say which string was wrong makes it guess again
    instead of reading the list.
    """
    return (f"“{asked}” is not something to ask for. A tool group is one of "
            f"{', '.join(askable_groups())} at {' or '.join(ASKABLE)}; a site "
            "is site:HOST:read or site:HOST:change; a folder is folder:PATH. "
            "Running code is granted on the Agents & tools screen rather than "
            "from a card.")


@dataclass(frozen=True)
class Need:
    """One thing an agent is asking for.

    `key` is what the panel's switch is addressed by and what two identical
    asks are deduplicated on, so it has to be derivable from the need alone —
    never assigned, or the same ask gets two keys and the card draws it twice.
    """

    kind: str
    #: `websites` / `accounts` / `mac` for a group; the host, path or connector
    #: name otherwise. Always the thing a person would recognise.
    target: str
    #: `read` or `change`, for the two kinds that have levels.
    level: str = ""

    @property
    def key(self) -> str:
        parts = [self.kind, self.target] + ([self.level] if self.level else [])
        return ":".join(parts)

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "target": self.target, "level": self.level}


# ── reading what an agent asked for ──────────────────────────────────────
#
# The wire format is a compact string rather than JSON, and that is a decision
# about the thing writing it. An action's attributes are flat strings matched by
# `(\w+)="([^"]*)"`, so JSON in one would need quotes it cannot have; and the
# body of this action is already spoken for by `why`, which is the sentence the
# whole card exists to show. A model writes `site:amazon.in:change` reliably and
# writes nested JSON inside an XML attribute badly.


def parse(spec: str) -> list[Need]:
    """Needs from `"websites:read, site:amazon.in:change"`. Bad parts dropped.

    Dropped, not raised: one unreadable entry in a list of three must not cost
    the user the other two, and the card can only show what it understood. What
    was dropped is visible — the panel draws the needs it has, so an ask that
    half-parsed looks short rather than looking wrong.
    """
    out: list[Need] = []
    for raw in str(spec or "").replace("\n", ",").split(","):
        found = normalise_text(raw)
        if found and all(found.key != seen.key for seen in out):
            out.append(found)
    return out


def normalise_text(raw: str) -> Need | None:
    """One `kind:target[:level]` token, or None.

    A bare `websites:read` is accepted as a tool group, because that is the pair
    `request_permission` has always spoken and the shorthand a model reaches for
    first. Everything else must name its kind.
    """
    parts = [p.strip() for p in str(raw or "").strip().split(":")]
    if len(parts) < 2 or not all(parts[:2]):
        return None
    head = parts[0].lower()

    if head == "site":
        # **Split from the right, because the target can contain colons.**
        # Splitting from the left assumed a bare host — which is what the
        # browser's own refusal hands an agent — and `site:https://amazon.in:read`
        # then parsed as a host of "https", resolved to nothing, and the whole
        # ask silently lost its site. A model that writes a full URL where a
        # host would do has not made a mistake worth refusing over.
        #
        # A missing level means `read`, the lesser of the two: asking for the
        # larger by omission is the one direction never to guess in.
        if parts[-1].lower() in SITE_LEVELS and len(parts) > 2:
            target, level = ":".join(parts[1:-1]), parts[-1].lower()
        else:
            target, level = ":".join(parts[1:]), "read"
        return normalise({"kind": "site", "target": target, "level": level})
    if head == "folder":
        # Rejoined: a path may contain a colon, and splitting one is how a
        # folder grant silently becomes a grant on a different folder.
        return normalise({"kind": "folder", "target": ":".join(parts[1:])})
    if head == "connector":
        return normalise({"kind": "connector", "target": parts[1]})
    if head in KINDS:
        return None            # `tool_group:x` without a level says nothing
    # The shorthand: `<group>:<level>`.
    return normalise({"kind": "tool_group", "target": head,
                      "level": parts[1].lower()})


def normalise(need: Any) -> Need | None:
    """A need from a dict, checked against what actually exists, or None.

    Checked, not merely parsed. `websites:run` parses perfectly and must never
    become a switch: `ASKABLE` is the rule that running code is granted on the
    settings screen while reading a sentence about what it means, not in the
    middle of a flow where somebody is trying to get something done.
    """
    if not isinstance(need, dict):
        return None
    kind = str(need.get("kind") or "").strip().lower()
    target = str(need.get("target") or "").strip()
    level = str(need.get("level") or "").strip().lower()
    if kind not in KINDS or not target:
        return None

    if kind == "tool_group":
        # `tools_for` is the one place a group and a level become tool names,
        # and it returns [] for a pair it does not know — including an
        # always-on group, where a card would ask for something already held.
        return (Need(kind, target.lower(), level)
                if tools_for(target.lower(), level) else None)

    if kind == "site":
        if level not in SITE_LEVELS:
            return None
        from ..browser import origins
        try:
            # Through `origins` so the host on the switch is the host the gate
            # will match. A card that said `amazon.in` while the grant landed on
            # something else is the one failure this control cannot survive.
            host = origins.host_of(target if "://" in target
                                   else f"https://{target}")
        except origins.BadOriginError:
            return None
        return Need(kind, host, level)

    if kind == "folder":
        from pathlib import Path
        try:
            # **`resolve`, not just `expanduser`.** `grant_folder` stores the
            # resolved path, and on this platform `/var` is a symlink to
            # `/private/var` — so a need carrying the unresolved spelling was
            # granted successfully and then compared against a different string
            # on the way back. The switch wrote, and stayed off for ever: a
            # grant that landed while the panel said it had not, which is the
            # one failure a panel of live switches cannot survive.
            resolved = Path(target).expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        # **Refused here if it would be refused there.** `file_tools` owns which
        # folders may be opened at all, and it is asked rather than copied — a
        # boundary one screen enforces and another describes differently is not
        # a boundary. Without this, `folder:/` parsed cleanly and became a
        # switch labelled "Open " whose only possible outcome was an error:
        # exactly the control-that-cannot-work this panel exists to remove.
        from .file_tools import folder_problem
        return None if folder_problem(str(resolved)) else Need(kind, str(resolved), "")

    from ..connectors import REGISTRY
    name = target.lower()
    return Need(kind, name, "") if name in REGISTRY else None


def needs_from_params(params: dict) -> list[Need]:
    """Everything one `request_permission` is asking for.

    Two spellings reach this, and they are one list by the time anybody acts on
    it. `needs="…"` is the general form; `group=` + `level=` is the pair this
    action shipped with and still the shortest way to ask for the common thing.
    Keeping both readable here rather than at each call site is what stops the
    handler and the panel disagreeing about what was asked.
    """
    found = parse(params.get("needs") or "")
    pair = normalise({"kind": "tool_group",
                      "target": params.get("group") or "",
                      "level": params.get("level") or ""})
    if pair and all(pair.key != seen.key for seen in found):
        found.insert(0, pair)
    return found


# ── what each need is called, and whether it is already held ─────────────


def _group_label(group: str) -> str:
    for row in permission_groups():
        if row["key"] == group:
            return str(row["label"])
    return group


#: What a grant MEANS, in a sentence somebody can answer.
#:
#: The pair is the key and the sentence is the control. A switch labelled
#: `websites: change` is asking a person to consent to two identifiers.
_GROUP_WORDS: dict[str, tuple[str, str]] = {
    "accounts:read": (
        "Read your connected accounts",
        "Mail, calendar and messages from accounts you have already connected. "
        "Reading only — it cannot send or change anything."),
    "accounts:change": (
        "Send and change things in your accounts",
        "Sending mail, writing events, posting messages. Each one still asks "
        "before it happens."),
    "websites:read": (
        "Read websites",
        "Open pages in a real browser, on the sites you allow below. Reading "
        "only — it cannot click, type or send."),
    "websites:change": (
        "Click and type on websites",
        "Clicking, typing and sending on the sites you allow. Never when "
        "nobody is watching, and placing an order always asks separately."),
    "mac:read": (
        "Read files on this Mac",
        "Opening and searching files in folders you have opened to agents. "
        "Reading only."),
    "mac:change": (
        "Change files on this Mac",
        "Writing, editing and moving files in those folders. Running code is "
        "not included and cannot be asked for here."),
}


def _site_rows(need: Need) -> tuple[str, str, bool]:
    from ..browser import origins

    held = next((g for g in origins.list_grants() if g.host == need.target), None)
    if need.level == "read":
        return (f"Read {need.target}",
                f"Let agents open pages on {need.target} and read them. "
                "Reading only.",
                bool(held and held.may_read))
    return (f"Click and type on {need.target}",
            f"Let agents click, type and submit on {need.target}. This is the "
            "second decision, and it is only for this site.",
            bool(held and held.may_act))


def describe(agent_id: str, needs: list[Need]) -> list[dict[str, Any]]:
    """Each need as the panel draws it: what it is, and whether it is on.

    Resolved server-side, every time the panel asks. The alternative was four
    fetches and the vocabulary written out a second time in JavaScript — and a
    switch whose label and whose state came from different places is the bug
    this whole module is a fix for, one level up.
    """
    from . import list_agents

    found = next((a for a in list_agents() if a.id == agent_id), None)
    held = set(found.tools or []) if found else set()
    rows: list[dict[str, Any]] = []
    for need in needs:
        row: dict[str, Any] = {"key": need.key, **need.as_dict(),
                               "control": "switch", "granted": False,
                               "title": need.key, "means": ""}
        if need.kind == "tool_group":
            words = _GROUP_WORDS.get(f"{need.target}:{need.level}")
            row["title"] = words[0] if words else (
                f"{need.level.title()} {_group_label(need.target).lower()}")
            row["means"] = words[1] if words else ""
            wanted = tools_for(need.target, need.level)
            row["granted"] = bool(wanted) and all(t in held for t in wanted)
        elif need.kind == "site":
            row["title"], row["means"], row["granted"] = _site_rows(need)
        elif need.kind == "folder":
            from .file_tools import granted_roots
            # The last segment, or the whole path when there is no last segment
            # to take. A row reading "Open " is not a thing anyone can answer.
            row["title"] = f"Open {need.target.rstrip('/').rsplit('/', 1)[-1] or need.target}"
            row["means"] = f"Agents may read and write inside {need.target}."
            row["granted"] = need.target in set(granted_roots())
        else:
            # A connector is the one kind with no switch behind it, and saying
            # so is the point. Google wants a sign-in window, Telegram wants
            # credentials typed, an MCP server wants a URL and sometimes an API
            # key — there is no press that connects all three, and a switch
            # that pretended otherwise would be a control that cannot work.
            row.update(_connector_row(need))
        rows.append(row)
    return rows


def _connector_row(need: Need) -> dict[str, Any]:
    from ..connectors import REGISTRY

    cls = REGISTRY.get(need.target)
    label = getattr(cls, "label", need.target) if cls else need.target
    ready = False
    if cls is not None:
        # A literal, not an f-string: `test_failures_are_recorded` reads these
        # labels out of the AST, and an interpolated one is a `JoinedStr` it
        # cannot see inside — so it reads as a suppression with no label at all,
        # which is the thing that test exists to forbid.
        with suppressed("asking a connector whether it is connected"):
            ready = bool(cls().is_configured()[0])
    return {"title": f"Connect {label}", "granted": ready,
            "means": f"Signing in to {label} happens in its own window, "
                     "because what each account asks for is different.",
            "control": "opens", "screen": "connectors"}


# ── the write, reached only from a tap ───────────────────────────────────


def grant(agent_id: str, need: Need) -> dict[str, Any]:
    """Turn one need on. Every branch delegates; none of them writes here.

    The caller is a human press on a control labelled with `describe`'s own
    sentence — never a model, and never an unattended run. `request_permission`
    is RED and in `permissions.NEVER_UNATTENDED` precisely so that the ask
    cannot be raised by a turn reading text a stranger wrote; this is the other
    half of the same rule, and it holds because nothing calls it but the panel.
    """
    if need.kind == "tool_group":
        from . import list_agents
        from .tool_overrides import get_tool_overrides

        wanted = tools_for(need.target, need.level)
        if not wanted:
            return {"ok": False, "error": "that is not something to ask for"}
        found = next((a for a in list_agents() if a.id == agent_id), None)
        if found is None:
            return {"ok": False, "error": "that agent is not on the roster"}
        already = set(found.tools or [])
        added = sorted(t for t in wanted if t not in already)
        if not added:
            # `changed` is False and `added` is empty, and both are said rather
            # than one implied from the other: a caller that showed "granted"
            # for a press that wrote nothing would be reporting our bookkeeping
            # as the user's news.
            return {"ok": True, "detail": "It already had that.",
                    "changed": False, "added": []}
        # An override, not an edit, so a preset keeps its shipped definition and
        # a later release can still improve it.
        get_tool_overrides().set(agent_id, sorted(already | set(wanted)))
        return {"ok": True, "detail": f"{found.name} can now do that.",
                "changed": True, "added": added}

    if need.kind == "site":
        from ..browser import origins

        held = next((g for g in origins.list_grants()
                     if g.host == need.target), None)
        # **Granting `change` does not quietly grant `read`, and granting
        # `read` never grants `change`.** `origins.grant` takes both flags and
        # defaults `may_act` to False, so re-granting a site with one flag would
        # silently clear the other — which is how a user who allowed clicking
        # would find reading turned off by the press that was meant to add to it.
        reading = True if need.level == "read" else bool(held and held.may_read)
        acting = True if need.level == "change" else bool(held and held.may_act)
        try:
            made = origins.grant(held.origin if held else f"https://{need.target}",
                                 may_read=reading, may_act=acting,
                                 note=held.note if held else "")
        except origins.BadOriginError as exc:
            return {"ok": False, "error": str(exc)}
        changed = not held or (held.may_read, held.may_act) != (reading, acting)
        return {"ok": True, "changed": changed, "added": [],
                "detail": f"{made.host} is allowed."}

    if need.kind == "folder":
        from .file_tools import grant_folder
        try:
            grant_folder(need.target)
        except ValueError as exc:
            # The message is written for a person, so it is passed through
            # rather than replaced with something about paths.
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "changed": True, "added": [],
                "detail": f"{need.target} is open to agents."}

    return {"ok": False,
            "error": "Connecting an account happens on its own screen."}
