"""Action framework — agents propose, the user confirms, Chitragupta executes.

Nothing here runs without an explicit user confirmation (the API endpoint is
only called from a UI Confirm button). Each action validates its params and
runs the corresponding connector's WRITE method.

**An action is the whole loop, not just the doing.** See
`docs/ACTION-COVERAGE.md` for why. A user watching an agent work moves through
six rungs — understand, recommend, prepare, act, verify, remember — and the
last three used to live nowhere: the handler ran, the card said "done", and
that was the end of it. Nothing read the result back to check it landed, and
nothing told the brain it had happened, so asking a different agent next week
whether the proposal went out got a blank look.

So `ActionSpec` carries the rungs as slots beside the handler:

* `risk` — may an unattended agent do this at all (see `agents/permissions.py`)
* `verify` — read it back from the service and say when it landed
* `remember` — write what happened into the brain, not just the conversation
* `undo` — the inverse, where one honestly exists

An action that ships without them is an action that stops at rung 4, and the
registry is the one place that is visible.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .connectors import get_connector
from .log import suppressed

_ACTION_RE = re.compile(r"<action\s+([^>]*?)>(.*?)</action>", re.I | re.S)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


class Risk(str, Enum):
    """How much of the user's world an action can disturb.

    Declared per action rather than inferred, because the three tiers used to
    be three separate hand-maintained sets in `agents/permissions.py` — and an
    action added to two of them and forgotten in the third is a gap nobody sees
    until something has already been sent.

    A `str` enum so the value crosses the wire to the card as its own name.
    """

    #: Reaches nobody. Local, or reversible, or both — a task, a draft, a
    #: reminder on the user's own laptop. May run unattended with no allow-list,
    #: because there is no one for it to reach. The action log is the audit.
    GREEN = "green"

    #: Reaches a person, or changes a system somebody else can see. Needs a
    #: permitted recipient to run unattended, and otherwise waits for one tap.
    #: A repeated approval of the same shape may be promoted to a standing
    #: grant — this is the tier where that is a coherent offer.
    AMBER = "amber"

    #: Never unattended, and never promotable to a standing grant. Either the
    #: effect cannot be undone, or — more often here — the *input* was written
    #: by a stranger and there is nothing stable to allow-list against.
    RED = "red"


@dataclass(frozen=True)
class ActionSpec:
    """One action, and everything the loop needs to run it honestly.

    `fields` is the order a card shows and the set a user may correct before
    confirming. It was already here and read by nobody; `/api/actions/catalog`
    publishes it now, so the frontend renders a form from the registry instead
    of keeping a second list of its own that drifts.
    """

    handler: Callable[[dict], dict]
    label: str
    fields: list[str]
    risk: Risk

    #: Which allow-list `permissions.check()` judges recipients against. Only
    #: meaningful for AMBER; "" means the action reaches nobody by name.
    recipient_kind: str = ""

    #: RED only: why it waits, in the user's terms. One sentence per action —
    #: a user told "creating automations always needs your approval" about a
    #: Slack message learns nothing except that the app is confused.
    always_ask_because: str = ""

    #: Rung 5. `(params, result) -> dict` merged into the result. Reads the
    #: thing back from the service that now holds it, so "sent" is something we
    #: checked rather than something we assumed from a 200.
    verify: Callable[[dict, dict], dict] | None = None

    #: Rung 6. `(params, result) -> None`. Writes into the brain, where every
    #: agent can see it — not into one agent's conversation, where only that
    #: agent can.
    remember: Callable[[dict, dict], None] | None = None

    #: The inverse, where one honestly exists. `(params, result) -> dict`.
    #: None is the common and correct answer: a sent email is gone, and an
    #: Undo button that quietly does nothing is worse than no button.
    undo: Callable[[dict, dict], dict] | None = None

    #: Human-readable, present tense, for the Undo button's own label.
    undo_label: str = ""

    def public(self) -> dict[str, Any]:
        """What the card needs to render itself, without the callables."""
        return {
            "label": self.label,
            "fields": list(self.fields),
            "risk": self.risk.value,
            "reversible": self.undo is not None,
            "undo_label": self.undo_label,
            "always_ask_because": self.always_ask_because,
        }


#: The allow-lists recipients are judged against. Declared here, beside the
#: actions that name them, and re-exported by `agents/permissions.py` — which
#: owns the *policy* and reads these as data. One direction only: actions know
#: nothing about permissions.
EMAIL_RECIPIENT = "email_recipient"
CHAT_RECIPIENT = "chat_recipient"


_PLAN_RE = re.compile(r"<plan(?:\s+([^>]*?))?>(.*?)</plan>", re.I | re.S)


@dataclass(frozen=True)
class ActionPlan:
    """Several actions the user approves once, as one piece of work.

    *"Clear the emails that don't need my attention"* is seventeen decisions
    and one intention. Rendering it as seventeen cards asks a person to make
    the same judgement seventeen times, and the second one is already being
    made without reading.

    So a model may wrap its proposals in `<plan>`, and the card shows what it
    understood, what it will do, and one button.

    **Attended only.** `parse_actions` still finds every `<action>` inside a
    plan, so an *unattended* routine keeps putting each one through
    `agents/approvals.run_or_queue` on its own merits — which is stricter than
    plan-level approval and needs no change to `routines.py`. The wrapper
    groups a decision a person is present to make; it never widens one.
    """

    rationale: str
    steps: list[dict]

    def risk(self) -> Risk:
        """The highest rung any step reaches.

        A plan is as risky as its worst step, not its average one — nine green
        archives beside one amber send is a card that sends an email, and
        saying "reaches nobody" because eight of nine steps do would be the
        card lying about the only step that matters.
        """
        worst = Risk.GREEN
        for step in self.steps:
            spec = REGISTRY.get(step.get("type", ""))
            if spec is None:
                continue
            if spec.risk is Risk.RED:
                return Risk.RED
            if spec.risk is Risk.AMBER:
                worst = Risk.AMBER
        return worst

    def as_dict(self) -> dict[str, Any]:
        return {"rationale": self.rationale, "steps": list(self.steps),
                "risk": self.risk().value}


def parse_plans(text: str) -> list[ActionPlan]:
    """Extract `<plan>…</plan>` groups from a model reply.

    A plan with no usable steps is dropped rather than shown as an empty card,
    the same rule `parse_actions` applies to a malformed action: half-parsing a
    model's proposal and running the result is how an action does something
    nobody proposed.
    """
    plans = []
    for attrs, plan_body in _PLAN_RE.findall(text or ""):
        # `plan_body` is a slice of the model's own reply, which is the only
        # thing `parse_actions` may ever be handed —
        # `test_only_the_reply_is_ever_handed_to_parse_actions` greps for that
        # by argument name, so this one is spelled to say where it came from
        # rather than reusing a name any regex group could carry.
        steps = parse_actions(plan_body)
        if not steps:
            continue
        found = dict(_ATTR_RE.findall(attrs or ""))
        rationale = (found.get("rationale") or "").strip()
        plans.append(ActionPlan(rationale=rationale, steps=steps))
    return plans


def strip_plans(text: str) -> str:
    """The reply with plan wrappers removed, leaving the actions to be parsed.

    Only the tags go; the actions inside them stay where they were, so a caller
    that does not understand plans sees exactly what it saw before.
    """
    return _PLAN_RE.sub(lambda m: m.group(2), text or "")


def parse_actions(text: str) -> list[dict]:
    """Extract <action …>…</action> proposals from a model reply (server-side
    twin of the UI parser) so routines can auto-execute them.

    Finds them inside a `<plan>` as well as outside one — deliberately. The
    unattended path judges every action on its own, and a wrapper the model
    wrote must not be able to change that.
    """
    out = []
    for attrs, inner in _ACTION_RE.findall(text or ""):
        a: dict = {"params": {}}
        for k, v in _ATTR_RE.findall(attrs):
            if k == "type":
                a["type"] = v
            else:
                a["params"][k] = v
        t = a.get("type")
        if t == "send_email":
            a["params"]["body"] = inner.strip()
        elif t == "create_event":
            a["params"]["description"] = inner.strip()
        elif t == "message_send":
            a["params"]["text"] = inner.strip()
        elif t == "set_reminder":
            a["params"]["message"] = inner.strip()
        elif t == "create_routine":
            a["params"]["instruction"] = inner.strip()
        elif t == "mcp_action":
            # A vendor's tool takes an object, and an action tag's attributes
            # are flat strings — so the arguments are the body, as JSON.
            a["params"]["server_id"] = (a["params"].pop("server", "")
                                        or a["params"].get("server_id", ""))
            parsed = _arguments(inner)
            if parsed is None:
                # Malformed JSON is dropped, never guessed at. Half-parsing a
                # model's arguments and running the result is how an action
                # does something nobody proposed.
                continue
            a["params"]["arguments"] = parsed
        elif t == "log_workout":
            # A session is a list of blocks, which does not fit in flat
            # attributes — same reason `mcp_action` puts its arguments in the
            # body, and parsed by the same rules on both sides.
            blocks = _items(inner)
            if blocks is None:
                continue
            a["params"]["blocks"] = blocks
        elif t == "mail_triage":
            # A list of messages will not fit in flat attributes either, so the
            # body is JSON here too — `{"items": [...]}` or the bare list.
            items = _items(inner)
            if items is None:
                continue
            a["params"]["items"] = items
        if t:
            out.append(a)
    return out


def _arguments(inner: str) -> dict | None:
    """The JSON object inside an `mcp_action` tag, or None if it is not one.

    Tolerates a fenced block, because models wrap JSON in ``` roughly half the
    time and a refused action teaches the user nothing about why.
    """
    import json

    text = (inner or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _items(inner: str) -> list | None:
    """The message list inside a `mail_triage` tag, or None if it is not one.

    Accepts `{"items": [...]}` and a bare `[...]`, because both are natural
    ways to write it and refusing one teaches the user nothing.
    """
    import json

    text = (inner or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(value, dict):
        value = value.get("items")
    return value if isinstance(value, list) else None


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _attachments_for(params: dict) -> tuple[list | None, str]:
    """Files named on the action, read from the folders the user has granted.

    `(attachments, problem)`. The path goes through `file_tools._resolve`
    rather than being opened directly, because the filename on this action was
    very often written by a model reasoning about somebody else's email —
    `attach ../../.ssh/id_rsa` is a sentence an injection would write, and the
    folder grants are the boundary that already answers it.
    """
    named = params.get("attach") or params.get("attachments") or []
    if isinstance(named, str):
        named = [p.strip() for p in named.split(",") if p.strip()]
    if not named:
        return None, ""

    from .agents.file_tools import _resolve

    out = []
    for raw in named:
        path, problem = _resolve(str(raw))
        if path is None:
            return None, problem
        if not path.is_file():
            return None, f"There is no file at {path}."
        try:
            data = path.read_bytes()
        except OSError as exc:
            return None, f"Could not read {path.name}: {exc}"
        if len(data) > MAX_ATTACHMENT_BYTES:
            return None, (f"{path.name} is {len(data) // 1_000_000} MB. "
                          f"Gmail refuses anything over "
                          f"{MAX_ATTACHMENT_BYTES // 1_000_000} MB.")
        out.append({"name": path.name, "data": data})
    return out, ""


#: Gmail's own ceiling is 25 MB for the whole message, and base64 inflates by a
#: third — so the useful limit on the raw bytes is lower than the number Google
#: quotes. Named rather than discovered as a 400 halfway through a send.
MAX_ATTACHMENT_BYTES = 18_000_000


def _draft_or_send(params: dict, *, send: bool) -> dict:
    """The two outbound paths, which differ in one call and one sentence.

    Written once because everything before that call is identical — the same
    recipient check, the same attachments, the same threading — and two copies
    would be two places for the next header to be added to one of them.
    """
    to = (params.get("to") or "").strip()
    subject = (params.get("subject") or "").strip()
    body = params.get("body") or ""
    cc = (params.get("cc") or "").strip()
    thread_id = (params.get("thread_id") or "").strip()

    # A draft may legitimately have no recipient yet — "write this up and I
    # will decide who it goes to" is a real thing to ask for. A send may not.
    if to and not _EMAIL_RE.match(to):
        return {"ok": False, "error": f"'{to}' is not a valid email address"}
    if send and not to:
        return {"ok": False, "error": "a recipient (to) is required"}

    attachments, problem = _attachments_for(params)
    if problem:
        return {"ok": False, "error": problem}

    capability = "send_email" if send else "create_draft"
    gmail = _writer("gmail", capability)
    if gmail is None:
        return {"ok": False, "error": "Gmail is not connected for sending mail."
                if send else "Gmail is not connected."}

    # Only what this message actually carries. Connectors are duck-typed — the
    # whole point of `_writer` — so a call that always passes five keywords is
    # a call that breaks on any implementation which has not grown all five
    # yet. `send_email(to, subject, body)` stays exactly the call it has always
    # been for the plain case, which is most of them.
    extra: dict[str, Any] = {}
    if cc:
        extra["cc"] = cc
    if attachments:
        extra["attachments"] = attachments
    if thread_id:
        extra["thread_id"] = thread_id
        # What makes a reply land *inside* the conversation for a recipient who
        # is not on Gmail. `threadId` alone only convinces Gmail.
        with suppressed("reading the headers a reply has to carry"):
            found = gmail.thread_headers(thread_id)
            headers = {k: v for k, v in
                       (("In-Reply-To", found.get("in_reply_to", "")),
                        ("References", found.get("references", ""))) if v}
            if headers:
                extra["headers"] = headers

    call = gmail.send_email if send else gmail.create_draft
    return call(to, subject, body, **extra)


def _send_email(params: dict) -> dict:
    return _draft_or_send(params, send=True)


def _create_draft(params: dict) -> dict:
    return _draft_or_send(params, send=False)


def _create_followup(params: dict) -> dict:
    """Remember that somebody owes an answer, and which thread it is in.

    An open loop the agent writes with `create_open_loop` is a sentence. This
    one carries the `thread_id` as well, which is the difference between "you
    are waiting on Rahul" — true until somebody deletes it by hand — and a
    thing that can *notice it has been answered* and close itself.

    Green: it writes one row in the user's own brain and reaches nobody.
    """
    from .brain import get_brain

    about = (params.get("about") or params.get("description")
             or params.get("message") or "").strip()
    who = (params.get("who") or params.get("from") or "").strip()
    if not about and not who:
        return {"ok": False, "error": "say what the follow-up is about"}
    if not about:
        about = f"Waiting on {who}"

    when = (params.get("due") or params.get("at") or params.get("due_at") or "").strip()
    due_at = None
    if when:
        from .reminders import parse_when
        due_at = parse_when(when)
        if not due_at:
            return {"ok": False, "error": f"couldn't understand the time '{when}'"}

    loop = get_brain().store.add_open_loop(
        about, due_at=due_at, source="followup",
        related_entities=[who] if who else None,
        metadata={k: v for k, v in (
            ("thread_id", str(params.get("thread_id") or "")),
            ("who", who)) if v})
    if loop is None:                                   # pragma: no cover
        return {"ok": False, "error": "that follow-up could not be stored"}

    nice = ""
    if due_at:
        from datetime import datetime
        nice = " — chase after " + datetime.fromisoformat(due_at).strftime(
            "%a %b %d")
    return {"ok": True, "id": loop.id, "detail": f"Tracking: {about}{nice}"}


def _undo_followup(params: dict, result: dict) -> dict:
    from .brain import get_brain

    loop_id = str(result.get("id") or "")
    if not loop_id:
        return {"ok": False, "error": "that follow-up cannot be found."}
    # Cancelled, not deleted. The loop is provenance — that the user was once
    # waiting on this is true whether or not they still want chasing about it,
    # and `list_open_loops` filters by status anyway.
    gone = get_brain().store.update_open_loop(loop_id, status="cancelled")
    return {"ok": True, "detail": "Stopped tracking it" if gone
            else "That was already gone"}


def _mail_triage(params: dict) -> dict:
    """Apply one approved batch of inbox changes.

    Refuses whole rather than partly: the user approved a card that named every
    message on it, and applying some of them would mean they approved something
    that did not happen.
    """
    from .connectors.google_auth import NEEDS_MODIFY_SCOPE, may_modify_mail
    from .mail_triage import group, parse_items, summarise

    items, problem = parse_items(params.get("items"))
    if problem:
        return {"ok": False, "error": problem}

    gmail = _writer("gmail", "modify_messages")
    if gmail is None:
        return {"ok": False, "error": "Gmail is not connected."}
    if not may_modify_mail():
        return {"ok": False, "error": NEEDS_MODIFY_SCOPE, "reauth": True}

    from .mail_triage import OPERATIONS

    changed = 0
    for (verb, label), ids in group(items).items():
        operation = OPERATIONS[verb]
        add, remove = list(operation.add), list(operation.remove)
        if operation.names_a_label:
            made = gmail.ensure_label(label)
            if not made.get("ok"):
                return {"ok": False, "error": made.get("error") or
                        f"Could not find or create the label “{label}”.",
                        "reauth": made.get("reauth", False)}
            add.append(str(made.get("id")))
        result = gmail.modify_messages(ids, add=add, remove=remove)
        if not result.get("ok"):
            # Say how far it got. "It failed" after eight of twelve moved is a
            # worse answer than the truth.
            return {"ok": False,
                    "error": f"{result.get('error') or 'Gmail refused the change.'}"
                             + (f" {changed} email(s) had already been changed."
                                if changed else ""),
                    "reauth": result.get("reauth", False)}
        changed += result.get("count", len(ids))

    return {"ok": True, "count": changed, "detail": summarise(items)}


def _log_workout(params: dict) -> dict:
    """Store one training session, after the user has seen and edited it.

    An action rather than a tool, unlike `log_measurement`. The difference is
    how much interpretation sits between what the user said and what gets
    stored: "82 this morning" is one number and hard to get wrong, while
    "5x5 squats, last one a grind, then some bench" is four numbers, a
    judgement and an exercise name — and a session stored wrong is a wrong
    trend for months, found weeks later.

    So the card shows what was understood, the user can correct it in place,
    and what executes is what is on the card at the moment they confirm.
    """
    from .training import log_session, parse_blocks

    blocks, problem = parse_blocks(params.get("blocks"))
    if problem:
        return {"ok": False, "error": problem}
    return log_session(blocks, at=str(params.get("at") or ""),
                       note=str(params.get("note") or ""))


def _message_send(params: dict) -> dict:
    """Send one message on a messaging app the user connected.

    Separate from `send_email` on purpose. They look alike and they are not:
    an email address is a global identifier and a chat id means nothing outside
    the app it came from, so they are allow-listed separately and the card says
    which app it is going to.
    """
    from .messaging import get_app

    app = str(params.get("app") or "").strip().lower()
    chat = str(params.get("chat") or params.get("to") or "").strip()
    text = str(params.get("text") or params.get("body") or "").strip()

    if not app or not chat:
        return {"ok": False, "error": "A message needs an app and a conversation."}
    if not text:
        return {"ok": False, "error": "There is nothing to send."}

    connector = get_app(app)
    if connector is None:
        return {"ok": False,
                "error": f"{app.title()} is not connected for messaging."}
    return connector.send(chat, text)


def _writer(source: str, capability: str):
    """The connector for `source`, only if it can actually perform `capability`.

    Connectors are duck-typed: `send_email` and `create_event` live on the Gmail
    and Calendar classes, not on the base `Connector`. If a source is connected
    read-only — or a future connector simply does not implement the write — the
    bare call raises AttributeError, and the user is shown a 500 with a Python
    traceback in it. Returning None instead lets the caller say what happened.
    """
    connector = get_connector(source)
    return connector if callable(getattr(connector, capability, None)) else None


def _set_reminder(params: dict) -> dict:
    from .reminders import get_reminders, parse_when
    message = (params.get("message") or params.get("body") or "").strip()
    when = params.get("at") or params.get("when") or ""
    if not message:
        return {"ok": False, "error": "reminder message required"}
    fire_at = parse_when(when)
    if not fire_at:
        return {"ok": False, "error": f"couldn't understand the time '{when}'"}
    r = get_reminders().add(message, fire_at, params.get("agent_id"))
    from datetime import datetime
    nice = datetime.fromisoformat(r["fire_at"]).strftime("%a %b %d, %-I:%M %p")
    # The id travels back so the result can be undone. A handler that creates a
    # row and returns only prose is a handler whose effect nothing can address.
    return {"ok": True, "id": r["id"], "detail": f"Reminder set for {nice}"}


def _create_routine(params: dict) -> dict:
    from .routines import describe_schedule, get_routines, parse_time
    instruction = (params.get("instruction") or params.get("body") or "").strip()
    if not instruction:
        return {"ok": False, "error": "an instruction is required"}
    name = (params.get("name") or "Automation").strip()
    agent = params.get("agent") or params.get("agent_id") or "personal"
    interval = int(params.get("interval_min") or 60)
    at_time = parse_time(params.get("at") or params.get("at_time"))
    days = params.get("days") or ""

    trigger = params.get("trigger") or "new_email"
    if trigger not in ("new_email", "schedule", "daily"):
        trigger = "new_email"
    # A time was given, so a time is what was meant. Models reach for the
    # trigger they were shown first and then attach `at="8am"` to it, and
    # honouring the trigger over the time turns "every morning at 8" into
    # "every 60 minutes" — which is not late, it is wrong all day.
    if at_time and trigger != "new_email":
        trigger = "daily"

    row = get_routines().create(name, agent, trigger, instruction, interval,
                                at_time=at_time, days=days) or {}
    return {"ok": True, "id": row.get("id", ""),
            "detail": f"Automation '{name}' created — runs "
                      f"{describe_schedule(row or {'trigger': trigger})}"}


def _create_event(params: dict) -> dict:
    title = (params.get("title") or "").strip()
    start = (params.get("start") or "").strip()
    if not title or not start:
        return {"ok": False, "error": "title and start (ISO datetime) required"}
    attendees = params.get("attendees")
    if isinstance(attendees, str):
        attendees = [a.strip() for a in attendees.split(",") if a.strip()]
    gcal = _writer("gcal", "create_event")
    if gcal is None:
        return {"ok": False, "error": "Google Calendar is not connected for creating events."}
    return gcal.create_event(
        title, start, params.get("end"), params.get("description", ""), attendees)


def _attendees_of(params: dict) -> list[str] | None:
    """The attendee list as given, or None for "do not touch it".

    The distinction is the whole reason this is not `or []`. An `update_event`
    that sent an empty list because nobody mentioned attendees would uninvite
    the meeting — a change nobody asked for, delivered as a cancellation to
    everyone on it.
    """
    raw = params.get("attendees")
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        return [a.strip() for a in raw.split(",") if a.strip()]
    return [str(a).strip() for a in raw if str(a).strip()]


def _update_event(params: dict) -> dict:
    event_id = (params.get("event_id") or params.get("id") or "").strip()
    if not event_id:
        return {"ok": False, "error": "Say which event — use the id from "
                                      "`calendar_lookup`."}
    gcal = _writer("gcal", "update_event")
    if gcal is None:
        return {"ok": False, "error": "Google Calendar is not connected."}
    return gcal.update_event(
        event_id,
        title=(params.get("title") or "").strip(),
        start=(params.get("start") or "").strip(),
        end=(params.get("end") or "").strip(),
        description=params.get("description"),
        location=params.get("location"),
        attendees=_attendees_of(params))


def _cancel_event(params: dict) -> dict:
    event_id = (params.get("event_id") or params.get("id") or "").strip()
    if not event_id:
        return {"ok": False, "error": "Say which event — use the id from "
                                      "`calendar_lookup`."}
    gcal = _writer("gcal", "cancel_event")
    if gcal is None:
        return {"ok": False, "error": "Google Calendar is not connected."}
    return gcal.cancel_event(event_id)


def _verify_update(params: dict, result: dict) -> dict:
    gcal = _writer("gcal", "event_exists")
    if gcal is None:
        return {}
    return gcal.event_exists(str(result.get("id") or ""))


def _remember_update(params: dict, result: dict) -> None:
    title = str(params.get("title") or "").strip()
    before = result.get("before") or {}
    name = title or str(before.get("summary") or "a meeting")
    moved = str(params.get("start") or "")
    _record(f"Moved “{name}” to {moved}." if moved else f"Changed “{name}”.",
            title=f"Event: {name}", event_time=moved)
    _close_named_loop(params)


def _remember_cancel(params: dict, result: dict) -> None:
    _record(str(result.get("detail") or "Cancelled an event") + ".",
            title="Event cancelled")
    _close_named_loop(params)


def _undo_update(params: dict, result: dict) -> dict:
    """Put the event back exactly as `update_event` found it.

    Reversible because the handler kept the other side of the diff. It is not
    silent: attendees were told it moved and are told again that it moved
    back, which is the honest thing — they have the wrong time in their
    calendar until somebody says so.
    """
    gcal = _writer("gcal", "restore_event")
    if gcal is None:
        return {"ok": False, "error": "Google Calendar is not connected."}
    return gcal.restore_event(str(result.get("id") or ""),
                              result.get("before") or {})


def _mcp_action(params: dict) -> dict:
    """Run one tool on a connector the user added.

    Registered here rather than executed inside the connector so it travels the
    same road as sending an email: proposed, queued if nobody is watching,
    approved from the same list, recorded with the same history. The connector
    had its own private confirmation flag, which was a second answer to a
    question this file already answers.
    """
    from .connectors import get_connector
    from .connectors.mcp_source import MCPConnector

    server_id = (params.get("server_id") or "").strip()
    tool = (params.get("tool") or "").strip()
    if not server_id or not tool:
        return {"ok": False, "error": "That action is missing its connector."}
    try:
        conn = get_connector(f"mcp:{server_id}")
    except KeyError:
        return {"ok": False, "error": "That connector is no longer set up."}
    if not isinstance(conn, MCPConnector):       # pragma: no cover - unreachable
        # The concrete type, not the base: this is the one handler that mutates
        # somebody else's account, and a duck-typed lookup would happily call
        # `perform` on anything that later grew the name.
        return {"ok": False, "error": "That connector cannot run actions."}
    # `confirmed=True` because reaching this handler *is* the confirmation:
    # nothing calls it except an approval the user granted or a live click.
    return conn.perform(tool, params.get("arguments") or {}, confirmed=True)


# ── rung 5: verify ─────────────────────────────────────────────────────────
#
# A handler returning `ok` means the service accepted the request. That is a
# weaker claim than "it is in your Sent folder", and the gap between them is
# exactly what a person wants to know before deciding whether to write the
# thing again.
#
# Every verifier here is deliberately unable to fail loudly. An unverifiable
# action is reported as *unverified*, never as failed: the mail may well have
# gone, and telling somebody their email did not send when it did is the worse
# of the two errors by a long way.


def _verify_email(params: dict, result: dict) -> dict:
    gmail = _writer("gmail", "message_sent_at")
    if gmail is None:
        return {}
    return gmail.message_sent_at(str(result.get("id") or ""))


def _verify_draft(params: dict, result: dict) -> dict:
    gmail = _writer("gmail", "draft_exists")
    if gmail is None:
        return {}
    return gmail.draft_exists(str(result.get("id") or ""))


def _verify_event(params: dict, result: dict) -> dict:
    gcal = _writer("gcal", "event_exists")
    if gcal is None:
        return {}
    return gcal.event_exists(str(result.get("id") or ""))


# ── rung 6: remember ───────────────────────────────────────────────────────
#
# `agents/outcomes.py` writes the outcome into the agent's own conversation,
# which is right for the agent and wrong for everything else: ask a *different*
# agent next week whether the proposal went out and it has never heard of it.
#
# So what happened goes into the brain as an episodic memory — the one store
# every agent reads. Not into the canonical layer: "I sent an email on Friday"
# is something that happened, not a curated fact about who the user is, and
# `brain.remember()` would push it into both.


def _record(text: str, *, title: str, event_time: str = "") -> None:
    from .brain import get_brain

    get_brain().ingest(
        text, source="action", kind="event", title=title,
        memory_type="episodic", importance=0.6, confidence=1.0,
        extraction_method="direct", event_time=event_time or None,
        evidence="performed by Chitragupta after the user confirmed it")


def _close_named_loop(params: dict) -> None:
    """Complete the open loop this action was taken to close — if one was named.

    Only ever an explicit `loop_id` the proposing agent put on the action
    because it was working from that loop. Matching by description was the
    obvious alternative and is the wrong one: "send Rahul the proposal" and
    "ask Rahul about the proposal" are one fuzzy match apart, and silently
    closing the wrong commitment is a failure the user cannot see.
    """
    loop_id = str(params.get("loop_id") or "").strip()
    if not loop_id:
        return
    from .log import suppressed

    with suppressed("closing the open loop an action was taken to resolve"):
        from .brain import get_brain

        get_brain().complete_open_loop(loop_id)


def _remember_email(params: dict, result: dict) -> None:
    to = str(params.get("to") or "someone")
    subject = str(params.get("subject") or "").strip()
    about = f" about “{subject}”" if subject else ""
    _record(f"Emailed {to}{about}.", title=f"Email to {to}",
            event_time=str(result.get("at") or ""))
    _close_named_loop(params)


def _remember_message(params: dict, result: dict) -> None:
    from .messaging import labels

    app = str(params.get("app") or "").strip().lower()
    where = labels().get(app) or app.title() or "a messaging app"
    who = str(params.get("chat") or params.get("to") or "someone")
    text = " ".join(str(params.get("text") or "").split())[:160]
    _record(f"Messaged {who} on {where}: {text}", title=f"Message to {who}")
    _close_named_loop(params)


def _remember_event(params: dict, result: dict) -> None:
    title = str(params.get("title") or "an event")
    start = str(params.get("start") or "")
    _record(f"Put “{title}” in the calendar for {start}.",
            title=f"Event: {title}", event_time=start)
    _close_named_loop(params)


def _remember_connector_action(params: dict, result: dict) -> None:
    from .agents.approvals import describe

    _record(describe("mcp_action", params) + ".", title="Connector action")
    _close_named_loop(params)


# ── the inverse, where one honestly exists ─────────────────────────────────
#
# None is the common and correct answer. A sent email is gone; an Undo button
# that quietly does nothing is worse than no button at all, so an action only
# declares `undo` when it can really take the effect back.


def _undo_draft(params: dict, result: dict) -> dict:
    gmail = _writer("gmail", "delete_draft")
    if gmail is None:
        return {"ok": False, "error": "Gmail is not connected."}
    return gmail.delete_draft(str(result.get("id") or ""))


def _undo_event(params: dict, result: dict) -> dict:
    gcal = _writer("gcal", "delete_event")
    if gcal is None:
        return {"ok": False, "error": "Google Calendar is not connected."}
    return gcal.delete_event(str(result.get("id") or ""))


def _undo_triage(params: dict, result: dict) -> dict:
    """Put every message back where it was.

    Free, and the reason is `mail_triage.OPERATIONS`: a verb is an `add`/
    `remove` pair of Gmail labels, so its inverse is the same pair swapped.
    Archive removes INBOX; undoing it adds INBOX. Nothing here has to know what
    archiving *means*.

    A verb that creates a label (`label`) is not inverted by deleting the
    label — the label may be in use elsewhere — only by removing it from these
    messages, which is what swapping add and remove already does.
    """
    from .mail_triage import OPERATIONS, group, parse_items

    items, problem = parse_items(params.get("items"))
    if problem:
        return {"ok": False, "error": problem}
    gmail = _writer("gmail", "modify_messages")
    if gmail is None:
        return {"ok": False, "error": "Gmail is not connected."}

    restored = 0
    for (verb, label), ids in group(items).items():
        operation = OPERATIONS[verb]
        add, remove = list(operation.remove), list(operation.add)
        if operation.names_a_label:
            made = gmail.ensure_label(label)
            if not made.get("ok"):
                return {"ok": False, "error": made.get("error")
                        or f"Could not find the label “{label}”."}
            remove.append(str(made.get("id")))
        out = gmail.modify_messages(ids, add=add, remove=remove)
        if not out.get("ok"):
            return {"ok": False,
                    "error": f"{out.get('error') or 'Gmail refused the change.'}"
                             + (f" {restored} were already put back."
                                if restored else "")}
        restored += out.get("count", len(ids))
    return {"ok": True, "detail": f"Put {restored} email(s) back"}


def _undo_row(store_name: str, label: str):
    """Undo for an action whose whole effect is one row we wrote ourselves."""

    def undo(params: dict, result: dict) -> dict:
        row_id = str(result.get("id") or "")
        if not row_id:
            return {"ok": False, "error": f"That {label} cannot be found."}
        if store_name == "reminder":
            from .reminders import get_reminders
            gone = get_reminders().delete(row_id)
        else:
            from .routines import get_routines
            gone = get_routines().delete(row_id)
        return ({"ok": True, "detail": f"{label.capitalize()} cancelled"} if gone
                else {"ok": True, "detail": f"That {label} was already gone"})

    return undo


#: Every action Chitragupta can take, and the whole loop for each one.
#:
#: This is the single place an action is declared. `risk` used to be three
#: hand-maintained sets in `agents/permissions.py` — an action added to two of
#: them and forgotten in the third is a gap nobody sees until something has
#: already been sent — and that module now reads these as data instead.
REGISTRY: dict[str, ActionSpec] = {
    "send_email": ActionSpec(
        handler=_send_email, label="Send email",
        fields=["to", "cc", "subject", "body", "attach"],
        risk=Risk.AMBER, recipient_kind=EMAIL_RECIPIENT,
        verify=_verify_email, remember=_remember_email,
        # No undo: it has left the machine and no API takes it back.
    ),
    "create_draft": ActionSpec(
        handler=_create_draft, label="Save a draft",
        fields=["to", "cc", "subject", "body", "attach"],
        # **Green, and this is the rung the product skipped.** A draft reaches
        # nobody: it sits in the user's own Drafts folder until *they* press
        # send. So it needs no permitted recipient and no allow-list, which
        # means an agent can do the preparing overnight and the user wakes up
        # to a folder of things to read rather than a queue of things to
        # approve. It is also the only outbound-shaped action that is
        # completely reversible.
        #
        # What green does NOT mean here, stated because it is the tempting
        # mistake: an unattended agent reading a stranger's email can be talked
        # into *drafting* one addressed to that stranger, carrying whatever it
        # knows. Nothing sends it — the user is the send button, and that is
        # the whole defence. It holds because a draft is read before it goes,
        # and it is why the card says "Draft" and never "Email": a person about
        # to press send in Gmail has to be able to tell the two apart.
        #
        # Attachments are the part that would not hold on its own, so they do
        # not rely on this: `_attachments_for` resolves every path through the
        # folder grants in `agents/file_tools.py`, which is the same boundary
        # that already answers "read ~/.ssh/id_rsa".
        risk=Risk.GREEN,
        verify=_verify_draft,
        undo=_undo_draft, undo_label="Discard it",
        # No `remember`: nothing has happened in the world yet. Writing "emailed
        # Rahul" into the brain because a draft exists is how an agent later
        # tells the user a thing was sent that is still sitting unsent.
    ),
    "create_event": ActionSpec(
        handler=_create_event, label="Create calendar event",
        fields=["title", "start", "end", "description", "attendees"],
        risk=Risk.AMBER, recipient_kind=EMAIL_RECIPIENT,
        verify=_verify_event, remember=_remember_event,
        undo=_undo_event, undo_label="Remove the event",
    ),
    "update_event": ActionSpec(
        handler=_update_event, label="Change a calendar event",
        fields=["event_id", "title", "start", "end", "location",
                "description", "attendees"],
        # **Red, not amber**, and the reason is the one `mcp_action` gives:
        # an allow-list needs something to compare against, and there is
        # nothing here. `create_event` is amber because the people it reaches
        # are the `attendees` on the card. The people a *move* reaches are on
        # the existing event — not in these params — so `recipients_of` would
        # find none, and "reaches nobody" is how an unattended agent ends up
        # rearranging a calendar full of other people's mornings.
        risk=Risk.RED,
        always_ask_because="Moving a meeting emails everybody in it, so it "
                           "always needs your approval.",
        verify=_verify_update, remember=_remember_update,
        undo=_undo_update, undo_label="Put it back",
    ),
    "cancel_event": ActionSpec(
        handler=_cancel_event, label="Cancel a calendar event",
        fields=["event_id"],
        risk=Risk.RED,
        always_ask_because="Cancelling a meeting tells everybody in it, so it "
                           "always needs your approval.",
        remember=_remember_cancel,
        # No undo. Recreating it would be a NEW invitation with a new id, sent
        # to everybody who has already been told it was cancelled — which is
        # not the same event and not an undo. Same honesty as `send_email`.
    ),
    "create_followup": ActionSpec(
        handler=_create_followup, label="Track a follow-up",
        fields=["about", "who", "due", "thread_id"],
        # Green: one row in the user's own brain, reaching nobody.
        risk=Risk.GREEN,
        undo=_undo_followup, undo_label="Stop tracking it",
    ),
    "set_reminder": ActionSpec(
        handler=_set_reminder, label="Set reminder",
        fields=["message", "at"],
        # Green: a notification on the user's own laptop reaches nobody else.
        risk=Risk.GREEN,
        undo=_undo_row("reminder", "reminder"), undo_label="Cancel it",
    ),
    "create_routine": ActionSpec(
        handler=_create_routine, label="Create automation",
        fields=["name", "trigger", "agent", "at", "days", "interval_min",
                "instruction"],
        risk=Risk.RED,
        always_ask_because="Creating automations always needs your approval.",
        undo=_undo_row("routine", "automation"), undo_label="Delete it",
    ),
    "mcp_action": ActionSpec(
        handler=_mcp_action, label="Connector action",
        fields=["server_id", "tool", "arguments"],
        risk=Risk.RED,
        always_ask_because="Anything a connector changes needs your approval.",
        remember=_remember_connector_action,
        # No undo: the verb belongs to somebody else's server and nothing tells
        # us what its inverse is — or whether it has one.
    ),
    "mail_triage": ActionSpec(
        handler=_mail_triage, label="Inbox changes",
        fields=["items"],
        risk=Risk.RED,
        always_ask_because="Changing your inbox always needs your approval.",
        undo=_undo_triage, undo_label="Put them back",
    ),
    "message_send": ActionSpec(
        handler=_message_send, label="Send a message",
        fields=["app", "chat", "text"],
        risk=Risk.AMBER, recipient_kind=CHAT_RECIPIENT,
        remember=_remember_message,
    ),
    "log_workout": ActionSpec(
        handler=_log_workout, label="Training session",
        fields=["blocks", "at", "note"],
        # Green: it writes one row in the user's own training log.
        risk=Risk.GREEN,
    ),
}


def catalog() -> dict[str, dict[str, Any]]:
    """Every action as the frontend needs it — fields, risk, reversibility.

    Published so a card renders itself from the registry. The field list was
    already here and read by nobody: the frontend kept its own idea of which
    actions were correctable (`EDITABLE = { log_workout: true }`), which is a
    second list of the same fact and drifted from this one the moment it was
    written.
    """
    return {name: spec.public() for name, spec in REGISTRY.items()}


def _summary(action_type: str, params: dict) -> str:
    """One line in the user's terms, borrowed from the approval card.

    Reused rather than re-worded: the log and the card must call an action the
    same thing, or the user is reading two descriptions of one event and has to
    work out that they are the same.
    """
    from .log import suppressed

    with suppressed("describing an action for the log"):
        from .agents.approvals import describe
        return describe(action_type, params)
    return action_type.replace("_", " ")


def _finish(action_type: str, spec: ActionSpec, params: dict, result: dict, *,
            agent_id: str = "", origin: str = "chat") -> dict:
    """Rungs 5 and 6, then the log. The half of an action after the doing.

    Order matters and is not obvious: **verify before remember**, so the memory
    can carry the time the service reported rather than the time we asked. And
    every step is suppressed, because none of them may turn an action that
    worked into one the user is told failed.
    """
    from .log import suppressed

    if result.get("ok"):
        if spec.verify is not None:
            with suppressed("verifying an action landed"):
                checked = spec.verify(params, result) or {}
                if checked.get("verified"):
                    result["verified"] = True
                    result["verified_at"] = str(checked.get("at") or "")
                    for extra in ("link", "in_sent"):
                        if extra in checked:
                            result[extra] = checked[extra]

        if spec.remember is not None:
            with suppressed("recording what an action did into the brain"):
                spec.remember(params, dict(result))

    result["risk"] = spec.risk.value
    result["reversible"] = spec.undo is not None and bool(result.get("ok"))
    if spec.undo is not None:
        result["undo_label"] = spec.undo_label

    with suppressed("logging an action"):
        from . import action_log
        result["log_id"] = action_log.record(
            action_type, params, result, summary=_summary(action_type, params),
            risk=spec.risk.value, reversible=bool(result.get("reversible")),
            agent_id=agent_id, origin=origin)
    return result


def run_now(action_type: str, params: dict, *, agent_id: str = "",
            origin: str = "chat") -> dict:
    """Run an action immediately (used by the scheduler for due scheduled ones).

    The single chokepoint every action passes through — attended, unattended,
    approved later, or fired by the scheduler. That is why verification, the
    memory write and the log live here rather than at the four call sites that
    would each have to remember them.
    """
    spec = REGISTRY.get(action_type)
    if not spec:
        return {"ok": False, "error": f"unknown action '{action_type}'"}
    params = params or {}
    try:
        result = spec.handler(params)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:200]}
    if not isinstance(result, dict):                   # pragma: no cover
        result = {"ok": False, "error": "that action returned nothing usable"}
    return _finish(action_type, spec, params, result,
                   agent_id=agent_id, origin=origin)


def run_plan(steps: list[dict], *, agent_id: str = "",
             origin: str = "chat") -> dict:
    """Run an approved plan in order, and say exactly how far it got.

    **In order, and stopping at the first failure.** Both halves are
    deliberate. Order, because a plan is a sequence a person read as one —
    "draft the reply, then archive the rest" reversed is a different plan.
    Stopping, because the steps after a failure were written on the assumption
    that the one before it worked, and running them anyway is the app deciding
    that assumption did not matter.

    What it must never do is go quiet about the difference. `_mail_triage`
    learned this first: *"It failed" after eight of twelve moved is a worse
    answer than the truth.* So every step that ran is reported with its own
    result and its own log id, and the ones that never started are named as
    not started rather than left for the user to infer from a count.
    """
    done: list[dict] = []
    failed: dict | None = None

    for index, step in enumerate(steps or []):
        action_type = str(step.get("type") or "")
        params = dict(step.get("params") or {})
        if agent_id:
            params.setdefault("agent_id", agent_id)
        result = run_now(action_type, params, agent_id=agent_id, origin=origin)
        entry = {"index": index, "type": action_type,
                 "summary": _summary(action_type, params), "result": result}
        done.append(entry)
        if not result.get("ok"):
            failed = entry
            break

    skipped = [
        {"index": i, "type": str(s.get("type") or ""),
         "summary": _summary(str(s.get("type") or ""), dict(s.get("params") or {}))}
        for i, s in enumerate(steps or []) if i >= len(done)
    ]
    ran = [d for d in done if d["result"].get("ok")]
    return {
        "ok": failed is None,
        "steps": done,
        "skipped": skipped,
        "detail": _plan_detail(len(ran), len(steps or []), failed),
        "error": (failed["result"].get("error") or "") if failed else "",
        # Everything that can still be taken back, newest first — which is the
        # order it has to be undone in.
        "undoable": [d["result"]["log_id"] for d in reversed(ran)
                     if d["result"].get("reversible") and d["result"].get("log_id")],
        "log_ids": [d["result"].get("log_id", "") for d in done],
    }


def _plan_detail(ran: int, total: int, failed: dict | None) -> str:
    if failed is None:
        return f"All {total} done." if total != 1 else "Done."
    if ran == 0:
        return f"Nothing was done — the first step failed. {total - 1} not started."
    return (f"{ran} of {total} done, then “{failed['summary']}” failed. "
            f"{total - ran - 1} not started.")


def undo_plan(log_ids: list[str]) -> dict:
    """Take back as much of a plan as can be taken back, newest first.

    Reverse order because the steps ran forwards: a plan that created a thing
    and then referred to it has to be unwound the way it was wound.

    Reports per step rather than as one verdict. Some of a plan is undoable
    and some is not — a sent email among four archived threads — and a single
    "undone" over that would be claiming something untrue about the email.
    """
    results = []
    for log_id in log_ids or []:
        out = undo(log_id)
        results.append({"log_id": log_id, **out})
    reversed_count = sum(1 for r in results if r.get("ok"))
    return {"ok": reversed_count > 0, "reversed": reversed_count,
            "steps": results,
            "detail": (f"Took back {reversed_count} of {len(results)}."
                       if results else "There was nothing to take back.")}


def undo(log_id: str) -> dict:
    """Take back a logged action, if it is one that can be taken back.

    Addressed by log entry rather than by (type, params), because the inverse
    needs the *result* — the event id Google returned, the reminder row we
    wrote — and the result is the thing a caller reconstructing the call from
    the proposal does not have.
    """
    from . import action_log

    entry = action_log.get(log_id)
    if not entry:
        return {"ok": False, "error": "That action is not in the log."}
    if entry["undone"]:
        return {"ok": True, "detail": "That was already undone."}

    if entry["origin"] == "scheduled":
        # A scheduled send has not left the machine yet, so its inverse is
        # cancelling the schedule — not the action's own `undo`, which for
        # `send_email` does not exist and for `create_event` would try to
        # delete a calendar entry nobody has created.
        from .scheduled import get_scheduled

        row_id = str(entry["result"].get("id") or "")
        if not row_id:
            return {"ok": False, "error": "That schedule cannot be found."}
        gone = get_scheduled().delete(row_id)
        action_log.mark_undone(log_id)
        return {"ok": True, "detail": "Cancelled — it will not fire" if gone
                else "That was already cancelled"}

    spec = REGISTRY.get(entry["action_type"])
    if spec is None or spec.undo is None:
        return {"ok": False,
                "error": f"{spec.label if spec else 'That action'} cannot be undone."}
    if not entry["ok"]:
        return {"ok": False, "error": "That action did not succeed, so there is "
                                      "nothing to take back."}
    try:
        out = spec.undo(entry["params"], entry["result"])
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    if out.get("ok"):
        action_log.mark_undone(log_id)
    return out


def execute(action_type: str, params: dict, *, origin: str = "chat") -> dict:
    """Confirm-time execution. If the action carries an `at` time (and supports
    scheduling), SCHEDULE it to fire later instead of running now — the user has
    confirmed both the content and the time."""
    spec = REGISTRY.get(action_type)
    if not spec:
        return {"ok": False, "error": f"unknown action '{action_type}'"}
    params = params or {}
    agent_id = str(params.get("agent_id") or "")

    at = (params.get("at") or "").strip()
    if at and action_type in ("send_email", "create_event"):
        from datetime import datetime

        from .reminders import parse_when
        from .scheduled import get_scheduled
        fire_at = parse_when(at)
        if not fire_at:
            return {"ok": False, "error": f"couldn't understand the time '{at}'"}
        sched_params = {k: v for k, v in params.items() if k != "at"}
        row = get_scheduled().add(action_type, sched_params, fire_at,
                                  params.get("agent_id")) or {}
        nice = datetime.fromisoformat(fire_at).strftime("%a %b %d, %-I:%M %p")
        verb = "Email" if action_type == "send_email" else "Event"
        queued = {"ok": True, "scheduled": True, "id": row.get("id", ""),
                  "detail": f"{verb} scheduled — will fire automatically at {nice}"}
        # Logged as its own event, and reversible whatever the action itself is:
        # a *scheduled* send has not left the machine yet, so cancelling it is a
        # real inverse even though sending it would not have been.
        return _log_scheduled(action_type, params, queued, agent_id)

    return run_now(action_type, params, agent_id=agent_id, origin=origin)


def _log_scheduled(action_type: str, params: dict, queued: dict,
                   agent_id: str) -> dict:
    from .log import suppressed

    queued["risk"] = REGISTRY[action_type].risk.value
    queued["reversible"] = bool(queued.get("id"))
    queued["undo_label"] = "Cancel it"
    with suppressed("logging a scheduled action"):
        from . import action_log
        queued["log_id"] = action_log.record(
            action_type, params, queued,
            summary=_summary(action_type, params) + " — scheduled",
            risk=queued["risk"], reversible=queued["reversible"],
            agent_id=agent_id, origin="scheduled")
    return queued
