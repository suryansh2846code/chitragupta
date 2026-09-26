"""An action may not be gentler than the thing it does.

`agents/permissions.py` is still the gate and still reads `ActionSpec.risk`.
This is the floor underneath it, and it exists because of the one failure
`/CLAUDE.md` names first about connectors: *a connector must never be able to
silently expose an arbitrary write operation as a harmless read operation.*

Two rules, and they run in opposite directions on purpose:

* **An action may be stricter than its capability implies**, and several are.
  `update_event` is an ordinary write and is RED, because the people a move
  reaches are on the event rather than in its params. The capability vocabulary
  cannot know that and must not overrule it.
* **An action may never be weaker.** `cancel:event` is destructive wherever it
  appears, so `cancel_event` cannot be registered at GREEN however convenient
  that would be.

And one more: an action may not reach a verb its connector never claimed. That
is what stops the write surface being whatever methods happen to exist on the
class — which is exactly what it was before `capabilities` was declared.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import REGISTRY, Risk, capability_problems
from chitragupta.connectors import REGISTRY as CONNECTORS
from chitragupta.connectors.capability import Access, UnknownCapabilityError
from chitragupta.connectors.capability import parse as parse_capability
from chitragupta.connectors.mcp_source import MCPConnector

ACTIONS = sorted(REGISTRY.items())
IDS = [name for name, _ in ACTIONS]


def test_the_shipped_registry_agrees_with_the_shipped_connectors():
    """The whole check, as one assertion. Everything below says *why* one of
    these sentences would appear, so a failure here is already explained."""
    assert capability_problems() == []


@pytest.mark.parametrize("name,spec", ACTIONS, ids=IDS)
def test_a_declared_capability_is_one_that_parses(name, spec):
    """Fails closed: an unrecognised capability is not quietly a read."""
    if not spec.capability:
        return
    try:
        parse_capability(spec.capability)
    except UnknownCapabilityError as exc:  # pragma: no cover - the message is the test
        pytest.fail(f"{name}: {exc}")


@pytest.mark.parametrize("name,spec", ACTIONS, ids=IDS)
def test_an_action_that_names_a_connector_says_what_it_does_there(name, spec):
    """Half a declaration is the shape that drifts. `prefer_mcp` /
    `same_as` had to be declared at both ends for the same reason."""
    if spec.connector:
        assert spec.capability, (
            f"{name} names connector {spec.connector!r} and no capability")


@pytest.mark.parametrize("name,spec", ACTIONS, ids=IDS)
def test_the_connector_actually_claims_the_verb(name, spec):
    if not spec.capability:
        return
    capability = parse_capability(spec.capability)
    if spec.connector == "mcp":
        assert capability in MCPConnector.capabilities
    elif spec.connector:
        cls = CONNECTORS[spec.connector]
        assert capability in cls.capabilities, (
            f"{name} reaches {capability} and {spec.connector} does not "
            f"declare it")
    else:
        # Chosen per run — `message_send` picks its connector from `app`. At
        # least one has to be able to do it, or the card offers a control that
        # cannot work.
        assert any(capability in cls.capabilities
                   for cls in CONNECTORS.values()), (
            f"{name} reaches {capability} and no connector declares it")


# ── the floor ─────────────────────────────────────────────────────────────

#: What each tier requires. Written out here rather than imported from
#: `actions.py`, so the test would still fail if the mapping there were
#: loosened — a floor checked against its own definition checks nothing.
REQUIRED = {
    Access.READ: Risk.GREEN,
    Access.WRITE: Risk.GREEN,
    Access.OUTBOUND: Risk.AMBER,
    Access.DESTRUCTIVE: Risk.RED,
}
STRENGTH = {Risk.GREEN: 0, Risk.AMBER: 1, Risk.RED: 2}


@pytest.mark.parametrize("name,spec", ACTIONS, ids=IDS)
def test_no_action_is_gentler_than_what_it_does(name, spec):
    if not spec.capability:
        return
    floor = REQUIRED[parse_capability(spec.capability).access]
    assert STRENGTH[spec.risk] >= STRENGTH[floor], (
        f"{name} is {spec.risk.value}; {spec.capability} needs at least "
        f"{floor.value}")


def test_a_destructive_verb_cannot_be_registered_as_harmless():
    """The failure this whole module exists to stop, stated as a fact about
    the shipped registry rather than only as a rule."""
    destructive = [name for name, spec in ACTIONS if spec.capability
                   and parse_capability(spec.capability).access
                   is Access.DESTRUCTIVE]

    assert destructive, "no destructive action left to check — has one moved?"
    for name in destructive:
        assert REGISTRY[name].risk is Risk.RED


def test_an_outbound_action_is_never_green():
    """A draft reaches nobody and a send reaches a person. The tier split is
    the whole reason `OUTBOUND` is not folded into `WRITE`."""
    outbound = [name for name, spec in ACTIONS if spec.capability
                and parse_capability(spec.capability).access is Access.OUTBOUND]

    assert outbound
    for name in outbound:
        assert REGISTRY[name].risk is not Risk.GREEN


def test_being_stricter_than_the_floor_is_allowed_and_happens():
    """Stated as a test so a later "simplification" that pins risk *to* the
    capability tier fails here rather than quietly re-tiering four actions."""
    spec = REGISTRY["update_event"]

    assert parse_capability(spec.capability).access is Access.WRITE
    assert spec.risk is Risk.RED


# ── the check catches what it claims to ───────────────────────────────────


def test_the_check_notices_a_write_registered_as_harmless(monkeypatch):
    """The regression this file was written for, run against the real check.

    Without the floor, an action carrying `delete:email` at GREEN is a thing an
    unattended agent may do with no allow-list at all.
    """
    import dataclasses

    from chitragupta import actions

    sneaky = dataclasses.replace(
        actions.REGISTRY["send_email"],
        risk=Risk.GREEN, connector="gmail", capability="delete:draft")
    monkeypatch.setitem(actions.REGISTRY, "send_email", sneaky)

    problems = actions.capability_problems()

    assert any("may not be registered below" in p for p in problems), problems


def test_the_check_notices_a_verb_the_connector_never_claimed(monkeypatch):
    import dataclasses

    from chitragupta import actions

    overreach = dataclasses.replace(
        actions.REGISTRY["create_draft"],
        connector="gmail", capability="delete:file")
    monkeypatch.setitem(actions.REGISTRY, "create_draft", overreach)

    problems = actions.capability_problems()

    assert any("does not declare it" in p for p in problems), problems


def test_the_check_notices_a_capability_nobody_recognises(monkeypatch):
    import dataclasses

    from chitragupta import actions

    nonsense = dataclasses.replace(
        actions.REGISTRY["create_task"], connector="", capability="exfiltrate:email")
    monkeypatch.setitem(actions.REGISTRY, "create_task", nonsense)

    problems = actions.capability_problems()

    assert any("unknown verb" in p for p in problems), problems
