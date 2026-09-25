"""One line per action, in the user's terms — the wording, on its own.

Two places need an action put into a sentence and they must agree exactly:

* the **approval card**, where a person decides whether to allow it;
* the **memory and the action log**, written after it happened.

If they word it differently the user is reading two descriptions of one event
and has to work out that they are the same. So there is one function, and it
lives below both callers rather than in either.

It used to live in `agents/approvals.py`, which meant `actions.py` imported
`agents` to write a log line — while `agents/permissions.py` reads this
module's risk tiers at import time. That pair was load-bearing in a
nineteen-module cycle spanning `agents/`, `actions.py` and `routines.py`.

Here it depends only on the feature modules whose actions it phrases —
`training`, `messaging`, `mail_triage`, and the schedule vocabulary in
`core/schedule.py` — none of which import `agents/`. `agents/approvals.py`
re-exports `describe`, so every existing import reads the same.
"""
from __future__ import annotations

import json

from .log import suppressed


def describe(action_type: str, params: dict) -> str:
    """One line, in the user's terms — not the action's internals."""
    params = params or {}
    if action_type in ("send_email", "create_draft"):
        # "Draft" and "Email" are different promises and the word is the whole
        # difference: one of them has left the machine. The attachment is named
        # too — a file going out is half of what a person is approving, and a
        # card that omits it is asking them to trust the summary.
        verb = "Email" if action_type == "send_email" else "Draft"
        line = (f"{verb} “{params.get('subject') or '(no subject)'}” to "
                f"{params.get('to') or 'someone'}")
        files = _attachment_names(params)
        return f"{line} — with {files}" if files else line
    if action_type == "create_event":
        return f"Calendar event “{params.get('title') or 'untitled'}” on {params.get('start') or 'a date'}"
    if action_type == "update_event":
        # What is CHANGING is the decision. A card reading "Change an event"
        # asks the user to approve a diff they were not shown, and the thing
        # being approved emails everybody in the meeting.
        name = str(params.get("title") or "").strip()
        moved = str(params.get("start") or "").strip()
        bits = []
        if moved:
            bits.append(f"move it to {moved}")
        if name:
            bits.append(f"rename it to “{name}”")
        if params.get("location"):
            bits.append(f"at {params['location']}")
        if params.get("attendees") is not None:
            bits.append("change who is coming")
        what = ", ".join(bits) or "change it"
        return f"Meeting — {what} (everybody in it is told)"
    if action_type == "cancel_event":
        return "Cancel a meeting — everybody in it is told"
    if action_type == "create_routine":
        # WHEN it runs is the decision, not the name. "New automation
        # 'Morning brief'" asks the user to approve a schedule they were never
        # shown, and a routine that fires at the wrong hour is discovered by
        # the thing it did at that hour.
        # The leaf, not `routines` — which imports this module to queue a
        # run. See `core/schedule.py`.
        from .core.schedule import describe_schedule, parse_days, parse_time

        at_time = parse_time(params.get("at") or params.get("at_time"))
        trigger = params.get("trigger") or "new_email"
        # The same inference `actions._create_routine` makes, for the same
        # reason: a time was given, so a time is what was meant. The card must
        # promise what the handler will actually build, or the user approves
        # "weekdays at 8:00 AM" and gets "every 60 min".
        if at_time and trigger != "new_email":
            trigger = "daily"
        when = describe_schedule({
            "trigger": trigger, "at_time": at_time,
            "days": parse_days(params.get("days")),
            "interval_min": params.get("interval_min") or 60,
        })
        return f"New automation “{params.get('name') or 'untitled'}” — {when}"
    if action_type == "create_followup":
        # Who owes the answer is the whole content. "Track a follow-up" in the
        # log tells a person nothing about which one, and this row is read a
        # week later when they have forgotten there was a thread at all.
        who = str(params.get("who") or params.get("from") or "").strip()
        about = str(params.get("about") or params.get("description")
                    or params.get("message") or "").strip()
        line = about or (f"Waiting on {who}" if who else "a follow-up")
        when = str(params.get("due") or params.get("at") or "").strip()
        return f"Follow up: {line}" + (f" — chase after {when}" if when else "")
    if action_type == "drive_create_doc":
        return f"New document “{params.get('title') or 'untitled'}” in your Drive"
    if action_type == "drive_share":
        # WHO gets access and WHAT KIND is the whole decision, and "anyone" is
        # the one a person must never skim past.
        role = str(params.get("role") or "reader").strip()
        anyone = str(params.get("anyone") or "").strip().lower() in (
            "1", "true", "yes", "on")
        who = "anyone with the link" if anyone else (
            str(params.get("email") or params.get("to") or "somebody"))
        return f"Give {who} {role} access to a document"
    if action_type == "create_task":
        # The same argument as `create_followup` above: this row is read weeks
        # later, and "Add task" says nothing about which one. The thread is
        # deliberately NOT named here — the id means nothing to a person, and
        # the task row in the workspace carries the link they can actually use.
        title = str(params.get("title") or params.get("task")
                    or params.get("about") or "").strip()
        when = str(params.get("due") or params.get("at") or "").strip()
        return (f"Task: {title or 'something to do'}"
                + (f" — due {when}" if when else ""))
    if action_type == "set_reminder":
        return f"Reminder: {params.get('message') or ''}"
    if action_type == "log_workout":
        from .training import parse_blocks, summarise_session

        blocks, problem = parse_blocks(params.get("blocks"))
        return summarise_session(blocks) if blocks else (problem or "Log a session")
    if action_type == "message_send":
        # The app is named because it is half the decision: the same handle can
        # be two different people on two different apps, and "send a message to
        # dana" does not say which one is about to get it.
        from .messaging import labels

        app = str(params.get("app") or "").strip().lower()
        where = labels().get(app) or app.title() or "a messaging app"
        who = params.get("chat") or params.get("to") or "someone"
        return f"Message {who} on {where}"
    if action_type == "mail_triage":
        # Plain verbs and real subjects, never a label id: "Archive 12 emails"
        # is the decision, and REMOVE INBOX is the implementation.
        from .mail_triage import parse_items, summarise

        items, problem = parse_items(params.get("items"))
        return summarise(items) if items else (problem or "Change your inbox")
    if action_type == "mcp_action":
        # The tool name is the vendor's, so it is shown as a name rather than
        # explained — inventing a description of somebody else's verb would be
        # guessing at what the user is about to approve. What *can* be shown
        # honestly is the connector's real name and the arguments as proposed,
        # because "Run write_file on filesystem" is not enough to judge: which
        # file, and with what in it, is the entire decision.
        where = params.get("connector") or _connector_label(params) or "a connector"
        line = f"Run “{params.get('tool') or 'an action'}” on {where}"
        detail = _argument_summary(params.get("arguments"))
        return f"{line} — {detail}" if detail else line
    return action_type.replace("_", " ")


def _attachment_names(params: dict) -> str:
    """The files this message would carry, by name — never by path.

    The path says where it is on disk, which the user already knows and which
    is long enough to push the subject off the card. The *name* is what they
    are checking: that it is the right document.
    """
    from pathlib import Path

    named = (params or {}).get("attach") or (params or {}).get("attachments") or []
    if isinstance(named, str):
        named = [p.strip() for p in named.split(",") if p.strip()]
    names = [Path(str(p)).name for p in named if str(p).strip()]
    if not names:
        return ""
    if len(names) <= 2:
        return " and ".join(names)
    return f"{names[0]} and {len(names) - 1} more files"


def _connector_label(params: dict) -> str:
    """The connector's own name, so a card never shows an internal id."""
    server_id = (params or {}).get("server_id") or ""
    if not server_id:
        return ""
    with suppressed("naming the connector an action belongs to"):
        from .connectors.mcp_source import get_server

        spec = get_server(server_id)
        if spec is not None:
            return spec.name
    return server_id


def _argument_summary(arguments: object, limit: int = 90) -> str:
    """The proposed arguments, short enough to read on a card.

    Values are truncated rather than dropped: a user approving a write needs to
    see *what* is being written, and a card showing only field names is asking
    them to trust the agent's summary of its own request.
    """
    if not isinstance(arguments, dict) or not arguments:
        return ""
    parts: list[str] = []
    for key, value in list(arguments.items())[:4]:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        text = " ".join(str(text).split())
        if len(text) > 40:
            text = text[:39] + "…"
        parts.append(f"{key}: {text}")
    joined = ", ".join(parts)
    return joined[:limit - 1] + "…" if len(joined) > limit else joined
