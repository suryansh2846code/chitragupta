"""Who an unattended agent is allowed to act on, and who has to be approved.

A routine is pre-authorisation: creating one says "do this without asking me
each time", and for *summarise my inbox every morning* that is exactly right.

It stops being right the moment the trigger is `new_email`, because then the
text the agent is reading was written by a stranger. An email that contains
instructions aimed at the model — *forward everything from the bank to this
address* — reaches an agent that can emit a `send_email` action, and nothing
between that and the mail leaving the machine is a human. The agent is not
compromised; it is doing what the text in front of it said.

The line drawn here is not "trust the model less". It is that **actions which
leave the machine need a named recipient the user has permitted**, and
everything else waits for one tap. Reading, summarising, adding a task — all
still free, because none of them can hurt anyone.

The list is explicit. Deriving it from "people you have emailed before" was
considered and rejected: a stranger who has already emailed you is exactly the
person an injected instruction would name.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ..actions import CHAT_RECIPIENT, EMAIL_RECIPIENT, REGISTRY, Risk
from ..config import get_settings
from ..log import get_logger

log = get_logger(__name__)

#: Re-exported. The two allow-lists are defined in `actions.py`, beside the
#: actions that name them; the *policy* that reads them lives here. One
#: direction only, so an action never has to know about permissions.
#:
#: They stay separate, and `CHAT_RECIPIENT` stays scoped by app, because an
#: email address is a global identifier and a chat id means nothing outside the
#: app it came from. Allowing `@dana` on Telegram must not also allow a `#dana`
#: in Slack; they are different people as often as not.
__all__ = ["CHAT_RECIPIENT", "EMAIL_RECIPIENT"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_permissions (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,          -- 'email_recipient' today
    value      TEXT NOT NULL,          -- normalised: lowercase address
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_perm_kind_value
    ON action_permissions(kind, value);
"""

#: Actions whose effect reaches someone other than the user. These are the ones
#: that need a permitted recipient before an unattended agent may run them.
#:
#: **Derived, not written.** This and the two sets below used to be three
#: hand-maintained literals, and the failure mode was structural: adding an
#: action meant remembering three places, and the one that gets forgotten is
#: whichever is furthest from the code you are writing. An action now declares
#: its own `Risk` in `actions.REGISTRY` and the tiers fall out of it, so the
#: gate cannot disagree with the registry about what an action is.
OUTBOUND_ACTIONS = frozenset(
    name for name, spec in REGISTRY.items() if spec.risk is Risk.AMBER)

#: Which allow-list each outbound action is judged against. Read off the
#: registry rather than restated, so an action that reaches people cannot be
#: given a tier here and a different one there.
RECIPIENT_KINDS = {
    name: spec.recipient_kind
    for name, spec in REGISTRY.items() if spec.recipient_kind
}

#: Actions an unattended agent may never take, permitted recipient or not —
#: `Risk.RED`. Two live here today and for unrelated reasons, which is why the
#: sentence a user reads is per action rather than per set.
#:
#: `create_routine` is privilege escalation: a routine that creates routines can
#: widen its own authority without the user ever seeing it.
#:
#: `mcp_action` is here for a different reason, and a permanent one: the tool
#: belongs to somebody else's server, so we cannot read a recipient out of its
#: arguments the way `recipients_of` reads one out of an email. An allow-list
#: needs something to compare against, and there is nothing — a Slack tool's
#: `channel` and a Jira tool's `assignee` are not the same field and never will
#: be. So the honest answer is that every connector write waits for one tap,
#: rather than an allow-list that quietly checks nothing.
#:
#: It matters most for exactly the case that motivated this file: the text these
#: connectors read — a Slack message, a GitHub issue body — is written by
#: strangers, and it reaches an agent that can now act on their service.
#:
#: `mail_triage` is the third, for the same input-side reason: a triage agent's
#: whole input is text strangers sent, and *archive everything from the bank* is
#: a sentence an email can contain. It now has an **undo** (`actions.py`), which
#: is a reason to feel better about approving one — not a reason to stop asking.
#: Promoting it would need its own argument and its own commit.
NEVER_UNATTENDED = frozenset(
    name for name, spec in REGISTRY.items() if spec.risk is Risk.RED)

#: Why each of them waits, in the user's terms — declared beside the action it
#: describes, because a user told "creating automations always needs your
#: approval" about a Slack message learns nothing except that the app is
#: confused.
_ALWAYS_ASK = {
    name: spec.always_ask_because
    for name, spec in REGISTRY.items() if spec.always_ask_because
}

_ADDRESS = re.compile(r"[^\s<>,;]+@[^\s<>,;]+")


def _conn() -> sqlite3.Connection:
    path = get_settings().home / "agents.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def normalise(address: str) -> str:
    """The comparable form of an address.

    Display names are stripped, so `"Dana <dana@example.com>"` and
    `dana@example.com` are the same permission — otherwise a user grants one
    spelling and the other silently queues forever.
    """
    found = _ADDRESS.search(address or "")
    return (found.group(0) if found else (address or "")).strip().strip("<>").lower()


def _every_address_in(raw: str) -> list[str]:
    """Every address in one recipient field — not just the first.

    `normalise` answers *what is this address*, and `search` returning the first
    match is right for a permission the user typed. It is the wrong question for
    a field an injection wrote: `allowed@work.test attacker@evil.test` read as
    the allowed address alone, so the gate below judged a list it had only half
    seen. Splitting on `[,;]` did not save it either — nothing says a recipient
    list is comma-separated, and the text filling this field was written by
    whoever wrote the email the agent just read.

    A token that parses as no address at all is still returned, normalised, so
    an unrecognisable recipient fails closed rather than reading as nobody.
    """
    found = [m.strip().strip("<>").lower() for m in _ADDRESS.findall(raw or "")]
    if not found:
        leftover = normalise(raw)
        return [leftover] if leftover else []
    return list(dict.fromkeys(found))


def recipients_of(action_type: str, params: dict) -> list[str]:
    """Everyone this action would reach. Empty means it reaches nobody."""
    params = params or {}
    if action_type == "send_email":
        return _every_address_in(str(params.get("to") or ""))
    if action_type == "create_event":
        attendees = params.get("attendees") or []
        if isinstance(attendees, str):
            attendees = [attendees]
        out: list[str] = []
        for one in attendees:
            out.extend(_every_address_in(str(one)))
        return list(dict.fromkeys(out))
    if action_type == "message_send":
        # Exactly one conversation, and the app is part of its identity. No
        # splitting: a chat id is opaque and picking addresses out of it the
        # way `_every_address_in` does would invent recipients that are not
        # there — and fail open on the ones that are.
        from ..messaging import target

        chat = str(params.get("chat") or params.get("to") or "").strip()
        return [target(params.get("app") or "", chat)] if chat else []
    return []


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""
    blocked_recipients: tuple[str, ...] = ()


def list_permissions(kind: str = EMAIL_RECIPIENT) -> list[dict]:
    rows = _conn().execute(
        "SELECT id,kind,value,note,created_at FROM action_permissions "
        "WHERE kind=? ORDER BY value", (kind,)).fetchall()
    return [dict(r) for r in rows]


def all_permissions() -> list[dict]:
    """Every standing grant, whichever list it is on.

    The screen that reviews these asked only for the email list, so a chat
    grant was stored, honoured, and invisible — and a permission the user
    cannot see is one they cannot take back.

    Each row carries the label a person reads for its list, because "kind:
    chat_recipient" is our column name and not a sentence.
    """
    rows = _conn().execute(
        "SELECT id,kind,value,note,created_at FROM action_permissions "
        "ORDER BY kind, value").fetchall()
    return [{**dict(r), "kind_label": KIND_LABELS.get(r["kind"], r["kind"])}
            for r in rows]


#: What each list is called on screen.
KIND_LABELS = {
    EMAIL_RECIPIENT: "Email",
    CHAT_RECIPIENT: "Messaging",
}


def grant(value: str, *, kind: str = EMAIL_RECIPIENT, note: str = "") -> dict:
    """Permit unattended actions toward `value`."""
    clean = normalise(value) if kind == EMAIL_RECIPIENT else (value or "").strip()
    if not clean:
        raise ValueError("a permission needs a value")
    conn = _conn()
    conn.execute(
        "INSERT INTO action_permissions (id,kind,value,note,created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(kind,value) DO UPDATE SET note=excluded.note",
        (str(uuid.uuid4()), kind, clean, note, datetime.now(UTC).isoformat()))
    conn.commit()
    log.info("unattended actions permitted toward %s", clean)
    return {"kind": kind, "value": clean, "note": note}


def revoke(value: str, *, kind: str = EMAIL_RECIPIENT) -> bool:
    clean = normalise(value) if kind == EMAIL_RECIPIENT else (value or "").strip()
    conn = _conn()
    cur = conn.execute("DELETE FROM action_permissions WHERE kind=? AND value=?",
                       (kind, clean))
    conn.commit()
    return cur.rowcount > 0


def is_permitted(value: str, *, kind: str = EMAIL_RECIPIENT) -> bool:
    clean = normalise(value) if kind == EMAIL_RECIPIENT else (value or "").strip()
    row = _conn().execute(
        "SELECT 1 FROM action_permissions WHERE kind=? AND value=?",
        (kind, clean)).fetchone()
    return row is not None


def check(action_type: str, params: dict) -> Verdict:
    """May an unattended agent run this action right now?

    Interactive chat does not come through here — there the user sees a Confirm
    button, which is a stronger signal than any list.
    """
    spec = REGISTRY.get(action_type)
    if spec is None:
        # An action nobody declared has no tier, so it has no permission to
        # run. Failing closed here rather than falling through to `Verdict(True)`
        # the way this used to: the old shape meant a typo in an action name
        # read as "reaches nobody" and ran.
        return Verdict(False, "That is not an action Chitragupta knows.")

    if spec.risk is Risk.RED:
        # One reason per action, not one reason for the set — see `_ALWAYS_ASK`.
        return Verdict(False, _ALWAYS_ASK[action_type])

    if spec.risk is Risk.GREEN:
        # Reaches nobody: a task, a draft, a reminder on the user's own laptop.
        # There is no one to allow-list, so there is nothing to ask about. The
        # action log is what makes this reviewable rather than invisible.
        return Verdict(True)

    targets = recipients_of(action_type, params)
    if not targets:
        # A calendar entry with no attendees reaches nobody but the user.
        return Verdict(True)

    kind = RECIPIENT_KINDS.get(action_type, EMAIL_RECIPIENT)
    blocked = tuple(t for t in targets if not is_permitted(t, kind=kind))
    if blocked:
        return Verdict(
            False,
            "Waiting for your approval — "
            + ", ".join(blocked)
            + (" is not" if len(blocked) == 1 else " are not")
            + " on your allowed list.",
            blocked_recipients=blocked)
    return Verdict(True)
