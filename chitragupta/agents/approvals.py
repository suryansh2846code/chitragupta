"""Actions an unattended agent wanted to take, waiting for one tap.

The alternative to queuing is dropping, and dropping is worse than it sounds: a
routine that quietly declines to send the email it was created to send looks
exactly like a routine that is working. The user finds out when somebody asks
why they never replied.

So a blocked action is kept, described in the user's terms, and surfaced — a
desktop notification when it lands, and a list they can approve or dismiss. The
parameters are stored as they were, so approving runs the action the agent
actually proposed rather than a reconstruction of it.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from ..config import get_settings
from ..log import get_logger, suppressed

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_approvals (
    id          TEXT PRIMARY KEY,
    routine_id  TEXT NOT NULL DEFAULT '',
    routine_name TEXT NOT NULL DEFAULT '',
    agent_id    TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL,
    params_json TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    blocked_json TEXT NOT NULL DEFAULT '[]',      -- see `blocked` in _public()
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
    result      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    decided_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status
    ON action_approvals(status, created_at);
"""

#: Columns added after the table shipped. `CREATE TABLE IF NOT EXISTS` does
#: nothing to a table that already exists, so a user upgrading in place keeps the
#: old shape and every read of the new column raises — which is why this list
#: exists rather than a second CREATE.
_ADDED_COLUMNS = {"blocked_json": "TEXT NOT NULL DEFAULT '[]'"}


def _conn() -> sqlite3.Connection:
    path = get_settings().home / "agents.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(action_approvals)")}
    for column, decl in _ADDED_COLUMNS.items():
        if column not in have:
            conn.execute(f"ALTER TABLE action_approvals ADD COLUMN {column} {decl}")
            conn.commit()
    return conn


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
    if action_type == "create_routine":
        # WHEN it runs is the decision, not the name. "New automation
        # 'Morning brief'" asks the user to approve a schedule they were never
        # shown, and a routine that fires at the wrong hour is discovered by
        # the thing it did at that hour.
        from ..routines import describe_schedule, parse_days, parse_time

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
    if action_type == "set_reminder":
        return f"Reminder: {params.get('message') or ''}"
    if action_type == "log_workout":
        from ..training import parse_blocks, summarise_session

        blocks, problem = parse_blocks(params.get("blocks"))
        return summarise_session(blocks) if blocks else (problem or "Log a session")
    if action_type == "message_send":
        # The app is named because it is half the decision: the same handle can
        # be two different people on two different apps, and "send a message to
        # dana" does not say which one is about to get it.
        from ..messaging import labels

        app = str(params.get("app") or "").strip().lower()
        where = labels().get(app) or app.title() or "a messaging app"
        who = params.get("chat") or params.get("to") or "someone"
        return f"Message {who} on {where}"
    if action_type == "mail_triage":
        # Plain verbs and real subjects, never a label id: "Archive 12 emails"
        # is the decision, and REMOVE INBOX is the implementation.
        from ..mail_triage import parse_items, summarise

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
        from ..connectors.mcp_source import get_server

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


def queue(action_type: str, params: dict, *, reason: str = "",
          blocked: tuple[str, ...] = (), routine_id: str = "",
          routine_name: str = "", agent_id: str = "") -> dict:
    """Hold an action for approval and tell the user it is waiting.

    `blocked` is who the allow-list refused, as `permissions.check()` computed
    them — kept as data beside the sentence in `reason`, never parsed back out
    of it. The UI offers a standing grant for exactly these addresses, and an
    address read out of a display string is one nobody can be sure of.
    """
    row_id = str(uuid.uuid4())
    summary = describe(action_type, params)
    conn = _conn()
    conn.execute(
        "INSERT INTO action_approvals "
        "(id,routine_id,routine_name,agent_id,action_type,params_json,summary,"
        " reason,blocked_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
        (row_id, routine_id, routine_name, agent_id, action_type,
         json.dumps(params or {}), summary, reason, json.dumps(list(blocked)),
         datetime.now(UTC).isoformat()))
    conn.commit()

    with suppressed("notifying the user about a queued action"):
        from ..notify import desktop_notify
        desktop_notify("◆ Chitragupta · needs your approval", summary)

    log.info("queued %s for approval: %s", action_type, reason)
    return {"id": row_id, "summary": summary, "reason": reason, "status": "pending"}


def pending() -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM action_approvals WHERE status='pending' "
        "ORDER BY created_at DESC").fetchall()
    return [_public(dict(r)) for r in rows]


def history(limit: int = 50) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM action_approvals ORDER BY created_at DESC LIMIT ?",
        (limit,)).fetchall()
    return [_public(dict(r)) for r in rows]


def _public(row: dict) -> dict:
    """The row as the UI sees it.

    `reason` is the sentence a person reads; `blocked` is the addresses a grant
    would be written against. They are separate fields on purpose — the moment
    the UI has to recover an address from prose is the moment it disagrees with
    `permissions.recipients_of()` about what an injected `to:` field contained.

    `blocked` is empty when the action was refused for a reason no allow-list
    can clear (`permissions.NEVER_UNATTENDED`), and that emptiness is the signal
    not to offer a standing grant at all.
    """
    row["params"] = json.loads(row.pop("params_json") or "{}")
    with suppressed("reading the blocked recipients of a queued action"):
        row["blocked"] = json.loads(row.pop("blocked_json", None) or "[]")
    row.setdefault("blocked", [])
    row.pop("blocked_json", None)
    return row


def _get(approval_id: str) -> dict | None:
    row = _conn().execute("SELECT * FROM action_approvals WHERE id=?",
                          (approval_id,)).fetchone()
    return _public(dict(row)) if row else None


def approve(approval_id: str) -> dict:
    """Run the action as proposed, and record what happened."""
    from ..actions import run_now

    row = _get(approval_id)
    if not row:
        return {"ok": False, "error": "That request is no longer waiting."}
    if row["status"] != "pending":
        return {"ok": False, "error": f"Already {row['status']}."}

    outcome = run_now(row["action_type"], row["params"],
                      agent_id=row.get("agent_id", ""), origin="approval")
    detail = outcome.get("detail") or outcome.get("error") or ""
    _decide(approval_id, "approved", detail)

    # The agent that proposed this is not in the room — a routine queued it
    # hours ago. Telling it what happened is the only way it can carry on from
    # a failure rather than silently never knowing.
    from .outcomes import settle
    outcome = settle(row.get("agent_id", ""), row["action_type"],
                     row["params"], outcome)

    answer = {"ok": bool(outcome.get("ok", True)), "detail": detail,
              "summary": row["summary"]}
    if outcome.get("agent_note"):
        answer["agent_note"] = outcome["agent_note"]
    return answer


def reject(approval_id: str) -> dict:
    row = _get(approval_id)
    if not row:
        return {"ok": False, "error": "That request is no longer waiting."}
    _decide(approval_id, "rejected", "")
    return {"ok": True, "summary": row["summary"]}


def _decide(approval_id: str, status: str, result: str) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE action_approvals SET status=?, result=?, decided_at=? WHERE id=?",
        (status, result[:500], datetime.now(UTC).isoformat(), approval_id))
    conn.commit()


def run_or_queue(action_type: str, params: dict, *, routine_id: str = "",
                 routine_name: str = "", agent_id: str = "") -> dict:
    """The seam every unattended action goes through.

    Interactive chat does not come this way — there the user sees a Confirm
    button, which is a stronger signal than any stored list.
    """
    from ..actions import run_now
    from .permissions import check

    verdict = check(action_type, params)
    if verdict.allowed:
        return run_now(action_type, params, agent_id=agent_id,
                       origin="routine" if routine_id else "chat")

    queued = queue(action_type, params, reason=verdict.reason,
                   blocked=verdict.blocked_recipients,
                   routine_id=routine_id, routine_name=routine_name,
                   agent_id=agent_id)
    return {"ok": True, "queued": True, "approval_id": queued["id"],
            "detail": f"{queued['summary']} — {verdict.reason}"}
