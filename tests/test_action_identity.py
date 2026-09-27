"""What names an action, so a card can find its own run after an edit.

A card matches itself against the action log by its parameters, because a
stored message and a log row share nothing else. Requiring **every** parameter
to match works right up until the user corrects a field before confirming —
which is the whole point of the fields being editable, and what executes is
what is on the card when Confirm is pressed, never what was proposed.

On a real machine: a card proposing `agent="chotu"` was confirmed after the
agent box was changed, the run was logged with the edited value, and the card
could never recognise it again. It sat pending forever under an
`[Action result]` line saying what had happened to it.

So an action may declare the fields that *name* the thing rather than describe
it. `create_routine` is its name; an automation with the same name and a
different agent is the same proposal, corrected. Actions that declare nothing
keep the strict rule, which is the safe default.
"""
from __future__ import annotations

from chitragupta.actions import REGISTRY, catalog


def test_every_identity_field_is_a_field_of_that_action():
    """An identity naming a parameter the action does not have matches
    nothing, silently — the failure this whole mechanism is about."""
    for name, spec in REGISTRY.items():
        for field in spec.identity:
            assert field in spec.fields, (
                f"{name} identifies itself by {field!r}, which is not one of "
                f"its fields: {spec.fields}")


def test_an_automation_is_named_by_its_name():
    """The case that shipped. Everything else about an automation — the agent,
    the trigger, the instruction — is a thing the user may correct on the card
    before pressing Confirm."""
    assert REGISTRY["create_routine"].identity == ("name",)


def test_an_email_is_named_by_who_it_goes_to_and_what_it_says():
    """The body is edited constantly; the recipient and subject are what make
    it that email rather than another one."""
    assert set(REGISTRY["send_email"].identity) == {"to", "subject"}


def test_an_action_that_declares_nothing_keeps_the_strict_rule():
    """The default has to be the safe one: with no identity, every parameter
    must match, which can only ever fail to settle a card — never settle the
    wrong one."""
    assert REGISTRY["mcp_action"].identity == ()


def test_the_catalog_publishes_it():
    """The match runs in the card, so the card has to be told."""
    assert catalog()["create_routine"]["identity"] == ["name"]
    assert catalog()["mcp_action"]["identity"] == []
