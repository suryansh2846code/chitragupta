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

from ..action_phrasing import describe
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
    # WHICH allow-list a grant for this action would go on. Sent rather than
    # inferred in the frontend, because there is exactly one mapping from
    # action to list and it lives in `permissions.RECIPIENT_KINDS`.
    #
    # Without it the UI granted everything as an email recipient: the "Always
    # allow" button on a Telegram card wrote `telegram:@dana` onto the email
    # list, told the user they would not be asked again, and then asked again
    # every time — because `check()` reads the chat list for that action. The
    # tap did nothing and said it had worked.
    with suppressed("naming the allow-list an approval belongs to"):
        from .permissions import RECIPIENT_KINDS
        row["kind"] = RECIPIENT_KINDS.get(row.get("action_type", ""), "")
    row.setdefault("kind", "")
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
