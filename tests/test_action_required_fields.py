"""What an action cannot run without, declared where the card can read it.

A card offered **Confirm & create** over an automation with an empty Agent box.
Pressing it could only ever return "say which agent" — the card was a control
that could not work, which is the thing this app is not allowed to ship. Worse,
the readback line filled the gap with the word "personal", which is not an
agent anybody has; the card named a value that did not exist.

The handlers already refused. Nothing had written down *what* they refuse
without, so the screen could not know either — and a rule that lives in only
one of two places is one the other half has to guess at.

`test_the_handler_really_refuses_without_it` is what keeps the two honest: it
calls each handler with the declared field blanked and fails if the action goes
through anyway. A `required` list that has drifted is worse than none, because
the card would refuse something that works.

**That check only ever looked in one direction, and the hole was the other
one.** It proves *declared implies refused*; nothing proved *refused implies
declared*, so a handler refusing on a field nobody had written down still
shipped a live button — and five of them did.
`test_every_refusal_is_declared` blanks every field of every action and asks
the handler what changed, which is how `send_email`'s recipient,
`set_reminder`'s time, `mcp_action`'s tool and both Drive actions were found. A
list somebody has to remember to extend is not a guard; the walk is.

A `required` entry may also be a **tuple, meaning "at least one of these"**. A
flat list can only ever be *stricter* than the handler it describes:
`create_followup` fills `about` in from `who`, so declaring `about` alone hid
the button on a card that would have run — the same defect pointing the other
way.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import REGISTRY, catalog

#: Enough of a payload for each action to get past everything *except* the
#: field under test.
#:
#: Every proposable action needs one now, not only the ones that declare
#: something: the walk runs each handler with a complete payload to learn what
#: it says when nothing is missing, then takes one field away at a time. A
#: missing fixture is a hole in the walk, so the walk fails rather than skips.
COMPLETE: dict[str, dict] = {
    "send_email": {"to": "a@b.test", "subject": "Hi", "body": "Text"},
    "create_draft": {"to": "a@b.test", "subject": "Hi", "body": "Text"},
    "create_routine": {"name": "Nudge", "trigger": "daily", "at": "8am",
                       "agent": "Chief of Staff", "instruction": "Say hello."},
    "create_event": {"title": "Standup", "start": "2026-10-01T09:00:00"},
    "update_event": {"event_id": "abc", "start": "2026-10-01T09:00:00"},
    "cancel_event": {"event_id": "abc"},
    "set_reminder": {"message": "Water", "at": "tomorrow 9am"},
    "create_task": {"title": "Buy milk"},
    # Internal, so it is not in the walk — but it declares something, and the
    # declaring check blanks `message`, which `_notify` refuses before it puts
    # anything on anybody's screen.
    "notify": {"message": "Hello"},
    "create_followup": {"about": "The invoice", "who": "Rahul",
                        "due": "tomorrow 9am"},
    "message_send": {"app": "whatsapp", "chat": "Dev", "text": "Hi"},
    "mcp_action": {"server_id": "notion", "tool": "create_page",
                   "arguments": {"title": "x"}},
    "mail_triage": {"items": [{"id": "m1", "do": "archive", "subject": "x"}]},
    "log_workout": {"blocks": [{"exercise": "Squat", "sets": 3, "reps": 5,
                                "weight": 100}], "note": "ok"},
    "drive_create_doc": {"title": "Notes", "text": "Body"},
    # Both alternatives present, so each can be left standing on its own. A
    # fixture carrying only one of a group cannot tell an over-strict handler
    # from a missing fixture.
    "drive_share": {"file_id": "f1", "email": "a@b.test", "role": "reader",
                    "anyone": "true"},
    # With no browser open the baseline is "the browser is no longer on the
    # basket" — which is the point: each blanked field has to produce a
    # *different* sentence, and all three are refused before the session is
    # ever asked for. An order that got as far as looking for a page without
    # knowing its total would be the bug this walk exists to find.
    "place_order": {"items": [{"name": "Rolled oats 1kg", "qty": 1}],
                    "total": "₹2,480", "control": "Place your order",
                    "site": "shop.example"},
}

#: What "blank" means for a field that is not a string. `""` where a list
#: belongs is not a shape the handler would ever be handed; an empty list is.
BLANK: dict[str, object] = {"items": [], "blocks": [], "arguments": {}}


def blanked(payload: dict, *fields: str) -> dict:
    return {**payload, **{f: BLANK.get(f, "") for f in fields}}


def alternatives(entry: object) -> tuple[str, ...]:
    """A `required` entry as the set of fields that satisfy it."""
    return (entry,) if isinstance(entry, str) else tuple(entry)  # type: ignore[arg-type]


def declared_fields(spec) -> set[str]:
    return {name for entry in spec.required for name in alternatives(entry)}


def declaring():
    return {name: spec for name, spec in REGISTRY.items() if spec.required}


#: Every action a model may propose — which is every action a *card* can be
#: drawn for. `notify` is excluded because it is `internal`: the engine emits it
#: to deliver a reminder, no card exists for it, and running it in a test would
#: put a notification on the machine running the suite.
def proposable():
    return {name: spec for name, spec in REGISTRY.items() if not spec.internal}


def test_something_declares_it():
    """A guard against the whole mechanism quietly becoming a no-op."""
    assert declaring(), "no action says what it cannot run without"


def test_every_required_field_is_a_field_of_that_action():
    """One naming a parameter the card never shows is a box the user cannot
    fill and a button that never enables."""
    for name, spec in declaring().items():
        for field in declared_fields(spec):
            assert field in spec.fields, (
                f"{name} requires {field!r}, which is not one of its fields: "
                f"{spec.fields}")


def test_an_automation_needs_an_agent_and_an_instruction():
    """The card that reported this. An automation with no agent can never run
    — every turn would be asked of somebody who is not there."""
    assert set(REGISTRY["create_routine"].required) == {"agent", "instruction"}


@pytest.mark.parametrize("name", sorted(declaring()))
def test_the_handler_really_refuses_without_it(name):
    """The claim the registry is making, checked against the code that makes
    it true. Blank each declared field in turn; the action must not go
    through."""
    spec = REGISTRY[name]
    complete = COMPLETE.get(name)
    assert complete is not None, f"{name} declares required fields but has no fixture"

    for entry in spec.required:
        group = alternatives(entry)
        result = spec.handler(blanked(complete, *group))
        assert result.get("ok") is not True, (
            f"{name} ran with {' and '.join(group)} blank, so declaring it "
            f"required makes the card refuse something that works")


@pytest.mark.parametrize("name", sorted(proposable()))
def test_every_refusal_is_declared(name):
    """**The other direction.** Take one field away and ask the handler.

    If the answer changes for the worse, the handler noticed — and a handler
    that noticed must have said so in `required`, or the card offers a button
    whose only possible outcome is that same refusal.

    The comparison is against what the handler says with *nothing* missing,
    which is how this works on a machine where Gmail is not connected: "Gmail
    is not connected" is the baseline, and "a recipient (to) is required" is a
    different sentence, so the field was noticed. A blanked field that changes
    nothing was never read.
    """
    spec = REGISTRY[name]
    complete = COMPLETE.get(name)
    assert complete is not None, (
        f"{name} can be proposed, so a card can be drawn for it — it needs a "
        f"fixture here or this walk cannot check it")

    base = spec.handler(dict(complete))
    declared = declared_fields(spec)

    for field in spec.fields:
        if field not in complete:
            continue
        out = spec.handler(blanked(complete, field))
        if out.get("ok") is True:
            continue                      # it did not care
        worse = base.get("ok") is True or out.get("error") != base.get("error")
        if not worse:
            continue                      # same refusal, for the same reason
        assert field in declared, (
            f"{name} refuses without {field!r} — {out.get('error')!r} — but "
            f"does not declare it, so the card offers a button whose only "
            f"possible answer is that sentence")


@pytest.mark.parametrize("name", sorted(
    n for n, spec in REGISTRY.items()
    if any(not isinstance(e, str) for e in spec.required)))
def test_one_of_a_group_is_enough(name):
    """The point of a group: any single alternative satisfies it.

    A group that behaves like a flat list is the over-strict card this was
    written to stop, so each alternative is left standing alone in turn and the
    handler must get past the check.
    """
    spec = REGISTRY[name]
    complete = COMPLETE[name]
    for entry in spec.required:
        group = alternatives(entry)
        if len(group) < 2:
            continue
        base = spec.handler(dict(complete))
        for keep in group:
            others = [f for f in group if f != keep]
            out = spec.handler(blanked(complete, *others))
            assert out.get("ok") is True or out.get("error") == base.get("error"), (
                f"{name} refused with only {keep!r} of {group} filled in "
                f"({out.get('error')!r}), so the group is really a flat list")


def test_the_catalog_publishes_it():
    """The check runs on the card, so the card has to be told."""
    assert catalog()["create_routine"]["required"] == ["agent", "instruction"]
    # An action with nothing required says so explicitly rather than omitting
    # the key — a missing key and an empty list read the same to JavaScript
    # only by accident. `create_draft` is the honest example: a draft with no
    # recipient yet is a real thing to ask for, and is why it is GREEN.
    assert catalog()["create_draft"]["required"] == []
    # A group crosses the wire as a nested list, and the card reads either
    # shape. Sharing needs an address *or* the anyone-with-the-link flag.
    assert catalog()["drive_share"]["required"] == ["file_id",
                                                   ["email", "anyone"]]


def test_the_flag_that_makes_a_share_public_is_on_the_card():
    """`anyone` was a parameter the card never showed.

    The rows are built from `fields`, so the one fact that turns *sharing with
    Rahul* into *publishing* was invisible on the card asking permission for
    it. `always_ask_when` already forced the tap; the user could not see what
    the tap was about.
    """
    spec = catalog()["drive_share"]
    assert "anyone" in spec["fields"]
    # …and not as an empty box on every ordinary share.
    assert spec["only_when_set"] == ["anyone"]
