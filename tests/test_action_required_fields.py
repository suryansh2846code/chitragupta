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
through anyway. A `required` list that has drifted from its handler is worse
than none, because the card would refuse something that works.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import REGISTRY, catalog

#: Enough of a payload for each action to get past everything *except* the
#: field under test. Only actions that declare `required` need one.
COMPLETE = {
    "create_routine": {"name": "Nudge", "trigger": "daily", "at": "8am",
                       "agent": "Chief of Staff", "instruction": "Say hello."},
    "create_event": {"title": "Standup", "start": "2026-10-01T09:00:00"},
    "update_event": {"event_id": "abc", "start": "2026-10-01T09:00:00"},
    "cancel_event": {"event_id": "abc"},
    "set_reminder": {"message": "Water", "at": "tomorrow 9am"},
    "create_task": {"title": "Buy milk"},
    "create_followup": {"about": "The invoice", "due": "friday"},
    "notify": {"message": "Hello"},
    "message_send": {"app": "whatsapp", "chat": "Dev", "text": "Hi"},
}


def declaring():
    return {name: spec for name, spec in REGISTRY.items() if spec.required}


def test_something_declares_it():
    """A guard against the whole mechanism quietly becoming a no-op."""
    assert declaring(), "no action says what it cannot run without"


def test_every_required_field_is_a_field_of_that_action():
    """One naming a parameter the card never shows is a box the user cannot
    fill and a button that never enables."""
    for name, spec in declaring().items():
        for field in spec.required:
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

    for field in spec.required:
        result = spec.handler({**complete, field: ""})
        assert result.get("ok") is not True, (
            f"{name} ran with {field!r} blank, so declaring it required makes "
            f"the card refuse something that works")


def test_the_catalog_publishes_it():
    """The check runs on the card, so the card has to be told."""
    assert catalog()["create_routine"]["required"] == ["agent", "instruction"]
    # An action with nothing required says so explicitly rather than omitting
    # the key — a missing key and an empty list read the same to JavaScript
    # only by accident.
    assert catalog()["log_workout"]["required"] == []
