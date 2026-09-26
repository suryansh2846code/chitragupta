"""What makes two side effects the same side effect.

The failure this prevents: a process dies after `send_email` reached Gmail and
before the result was written. On restart the run resumes, reaches the same
step, and sends it again. The user finds out from the recipient.

A key has to be **stable across restarts** and **specific to one intended
effect**, which rules out both obvious answers. A UUID per attempt is stable
across nothing. The action name alone is specific to nothing.

What goes in, and why each part:

* **`automation_id`** — two automations that both email the same person about
  the same thing want to send two emails. They are different intents.
* **the triggering event's dedup key** — so a provider redelivering the event
  maps to the same key even if the event ledger somehow let it through. Belt
  and braces on `core/events.is_duplicate`, and cheap.
* **`action_type`** — obviously.
* **a hash of the params that carry the action's *identity*** — see
  `_identity`, which is where the judgement lives.
* **the step's position in the plan** — so a plan that deliberately does the
  same thing twice can, while a *retry* of one step cannot.

What stays out: the run id. Two runs of the same automation from the same event
must collide — that is the cross-run half of the guarantee, and including the
run id would quietly turn it off.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

#: Params that describe *how* rather than *what*, and change between attempts.
#: Hashing them makes a retry look like a new action, which is the bug.
#:
#: `at` is here because a scheduled action's identity is the thing it will do,
#: not the moment it was asked for; `agent_id` because which agent proposed an
#: email does not change whether it is the same email.
_VOLATILE = frozenset({
    "agent_id", "at", "origin", "run_id", "automation_id", "idempotency_key",
    "_source", "_trace", "created_at", "timestamp",
})

#: Fields that carry an action's identity, per action type. A hand-written list
#: for the actions where a generic hash gets it wrong.
#:
#: `send_email` is the one that matters. Hashing every param would make a
#: re-drafted body — one word different because a model ran twice — read as a
#: different email, and the duplicate goes out. Recipient and subject are what
#: a *person* would call the same email, so that is what the key uses.
_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "send_email": ("to", "subject"),
    "create_draft": ("to", "subject"),
    "message_send": ("app", "chat_id", "to", "text"),
    "create_event": ("title", "start"),
    "update_event": ("event_id",),
    "cancel_event": ("event_id",),
    "create_task": ("title",),
    "set_reminder": ("message", "at"),
    "create_routine": ("name",),
    "mcp_action": ("server", "tool", "arguments"),
    "log_workout": ("date", "exercise"),
}


def _identity(action_type: str, params: dict[str, Any]) -> str:
    """The stable part of these params, as canonical JSON.

    Falls back to "everything that is not volatile" for an action with no
    declared identity. That is the conservative direction: an unlisted action
    keys on more rather than less, so the risk is a legitimate second action
    being refused, not a duplicate being sent. Refusing too much is visible in
    the run history; sending twice is visible to the recipient.
    """
    fields = _IDENTITY_FIELDS.get(action_type)
    if fields:
        subset = {k: params.get(k) for k in fields if params.get(k) not in (None, "")}
    else:
        subset = {k: v for k, v in (params or {}).items() if k not in _VOLATILE}
    return json.dumps(subset, sort_keys=True, default=str)


def key_for(*, automation_id: str, action_type: str, params: dict[str, Any],
            event_key: str = "", seq: int = 0) -> str:
    """The idempotency key for one intended side effect.

    Readable on purpose — `auto:a1|evt:gmail:m17|send_email|3|9f2c…`. A key
    nobody can read is a key nobody debugs, and this is the row a support
    question starts from.
    """
    digest = hashlib.sha256(
        _identity(action_type, params or {}).encode()).hexdigest()[:16]
    return "|".join([
        f"auto:{automation_id or '-'}",
        f"evt:{event_key or '-'}",
        action_type or "-",
        str(seq),
        digest,
    ])


def same_effect(action_type: str, left: dict[str, Any],
                right: dict[str, Any]) -> bool:
    """Would these two params cause the same side effect?

    Used when recovering a claim whose outcome is unknown: the question is
    whether the thing we find at the provider is the thing we were trying to
    do, and that is this comparison rather than dictionary equality.
    """
    return _identity(action_type, left or {}) == _identity(action_type, right or {})
