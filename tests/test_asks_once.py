"""One tap, then never again — for every action that reaches somebody.

The promise a user is actually buying is not "an agent asks before it acts".
It is **"it asks once"**. A routine that posts the same daily standup note and
puts up an identical card every morning has not asked for consent; it has
built a habit of tapping, which is the thing consent is supposed to be.

So this file is the promise stated as an acceptance test, once per allow-list:

    day one   → queued, and the card names the exact key a grant would hold
    the user  → grants precisely what the card named, nothing widened
    day two   → runs, no card

The middle line is the one that makes this a test rather than a demonstration.
A grant written against something the card did *not* name is the bug that
shipped once already: "Always allow" wrote a Telegram handle onto the EMAIL
list, `message_send` never read that list, and the button did nothing while
saying it had worked. So the grant here is always `row["blocked"]` fed back —
the same value the frontend sends — never a string this file made up.

What deliberately still asks every time is pinned at the bottom, because the
boundary is the point: an action that cannot name what it reaches, and an
irreversible connector verb, are not promotable and must never quietly become
promotable to make a test like this pass.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import REGISTRY, Risk
from chitragupta.agents import approvals, permissions


@pytest.fixture(autouse=True)
def clean_slate():
    """Nothing queued, nobody permitted — a fresh install, every time."""
    conn = approvals._conn()
    conn.execute("DELETE FROM action_approvals")
    conn.commit()
    perms = permissions._conn()
    perms.execute("DELETE FROM action_permissions")
    perms.commit()
    yield
    perms = permissions._conn()
    perms.execute("DELETE FROM action_permissions")
    perms.commit()


@pytest.fixture(autouse=True)
def never_really_act(monkeypatch):
    """Day two has to RUN to prove it did not ask, and running an action for
    real would send mail. The seam is `run_now`, which is what `run_or_queue`
    reaches when the verdict is allowed — so stubbing it here still exercises
    the whole decision and none of the sending."""
    ran: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        approvals, "run_now",
        lambda t, p, **kw: (ran.append((t, p)), {"ok": True})[1],
        raising=False)
    import chitragupta.actions as actions_mod
    monkeypatch.setattr(
        actions_mod, "run_now",
        lambda t, p, **kw: (ran.append((t, p)), {"ok": True})[1])
    return ran


#: One case per allow-list, written as the user's sentence rather than as an
#: action name. `params` is what an agent would propose on an ordinary morning.
MORNINGS = [
    pytest.param(
        "send_email",
        {"to": "rahul@work.test", "subject": "the numbers", "body": "attached"},
        "rahul@work.test",
        id="email the same person every morning"),
    pytest.param(
        "message_send",
        {"app": "telegram", "chat": "4411", "text": "on my way"},
        "telegram:4411",
        id="message the same chat every morning"),
    pytest.param(
        "github_comment",
        {"url": "https://github.com/acme/api/issues/7", "body": "bumping this"},
        "acme/api",
        id="comment on the same repo every morning"),
    pytest.param(
        "mcp_action",
        {"server_id": "linear", "tool": "create_comment",
         "arguments": {"issueId": "ENG-1", "body": "standup"}},
        "linear:create_comment",
        id="write to the same connector tool every morning"),
]


def _morning(action_type: str, params: dict) -> dict:
    """One unattended run, through the real seam a routine goes through."""
    return approvals.run_or_queue(
        action_type, params, routine_id="r1", routine_name="Daily standup")


@pytest.mark.parametrize("action_type,params,expected_key", MORNINGS)
def test_the_first_morning_asks_and_names_what_a_grant_would_cover(
        action_type, params, expected_key, never_really_act):
    """Nothing is allowed on a fresh install, and the card is specific.

    `blocked` is what the frontend grants against, so a card that named the
    wrong key would produce a grant that silences nothing — and the user would
    be told it had worked.
    """
    result = _morning(action_type, params)

    assert result.get("queued") is True, "it acted without asking"
    assert not never_really_act, "it acted without asking"

    row = approvals.pending()[0]
    assert row["blocked"] == [expected_key], (
        f"the card offers a grant for {row['blocked']}, which is not the key "
        f"the gate judges this action against")
    assert row["kind"] == permissions.RECIPIENT_KINDS[action_type], (
        "the card would put this grant on a list the gate never reads")


@pytest.mark.parametrize("action_type,params,expected_key", MORNINGS)
def test_the_second_morning_does_not_ask(
        action_type, params, expected_key, never_really_act):
    """The whole promise. One tap on day one, silence afterwards.

    The grant is taken from the card rather than written here, because the
    frontend sends exactly `blocked` and `kind` back — this is that request.
    """
    _morning(action_type, params)
    row = approvals.pending()[0]

    for value in row["blocked"]:                      # what the button posts
        permissions.grant(value, kind=row["kind"], note="allowed from a card")

    # One connection, committed on itself — two `_conn()` calls would execute
    # the delete on one and commit the other, leaving day one's card in place
    # and this test failing for a reason that has nothing to do with the app.
    conn = approvals._conn()
    conn.execute("DELETE FROM action_approvals")
    conn.commit()
    never_really_act.clear()

    result = _morning(action_type, params)

    assert not result.get("queued"), (
        f"it asked again on day two — {result.get('detail', '')}")
    assert approvals.pending() == [], "it asked again on day two"
    assert never_really_act == [(action_type, params)], "and it never ran"


@pytest.mark.parametrize("action_type,params,expected_key", MORNINGS)
def test_the_grant_covers_that_one_thing_and_not_its_neighbour(
        action_type, params, expected_key, never_really_act):
    """A standing permission is a statement about one recipient, one repo, one
    chat, one tool — never about the service it lives on.

    Without this, "always allow" would read as "always allow Linear", and the
    user would have agreed to something nobody showed them.
    """
    permissions.grant(expected_key,
                      kind=permissions.RECIPIENT_KINDS[action_type])

    elsewhere = {
        "send_email": {**params, "to": "someone-else@work.test"},
        "message_send": {**params, "chat": "9999"},
        "github_comment": {**params,
                           "url": "https://github.com/acme/billing/issues/7"},
        "mcp_action": {**params, "tool": "create_issue"},
    }[action_type]

    assert not permissions.check(action_type, elsewhere).allowed, (
        "the grant leaked onto a neighbour nobody approved")


@pytest.mark.parametrize("action_type,params,expected_key", MORNINGS)
def test_taking_it_back_makes_it_ask_again(
        action_type, params, expected_key, never_really_act):
    """A permission that cannot be withdrawn is not a permission.

    The allow-list screen is the only place a user can undo a decision they
    made in a hurry, and it removes by (value, kind) — so a revoke that missed
    would leave a grant visible on screen and still live in the gate.
    """
    kind = permissions.RECIPIENT_KINDS[action_type]
    permissions.grant(expected_key, kind=kind)
    assert permissions.check(action_type, params).allowed

    assert permissions.revoke(expected_key, kind=kind) is True
    assert not permissions.check(action_type, params).allowed


# ── and what a grant must never be able to silence ───────────────────────
def test_an_action_that_cannot_name_who_it_reaches_still_asks_every_time():
    """`update_event`, `cancel_event`, `mail_triage`, `create_routine`.

    Not an oversight and not a gap to close later. The people a moved meeting
    reaches are on the existing event, not in the params — so there is no key
    for a user to read, and "always allow" would be a button that agreed to
    something nobody could show them.
    """
    unpromotable = {n for n, s in REGISTRY.items() if s.risk is Risk.RED}
    assert unpromotable == {"update_event", "cancel_event",
                            "mail_triage", "create_routine"}

    for action_type in unpromotable:
        verdict = permissions.check(action_type, {})
        assert not verdict.allowed
        assert verdict.blocked_recipients == (), (
            f"{action_type} offered a grant the allow-list cannot honour")


def test_an_outbound_action_whose_target_we_cannot_read_never_reads_as_nobody():
    """Found by the test above, and it had been live the whole time.

    `check()` treats an empty recipient list as "reaches nobody" and allows it
    — which is correct for a calendar entry with no attendees, and was being
    applied to `message_send`, an action that reaches a person by definition.
    A proposal whose `chat` was missing or unparseable therefore ran
    unattended, with no card and nothing to grant.

    So: every action that reaches somebody must name *something* when its
    target is unreadable, because the placeholder is what no grant can match.
    """
    # Written over the whole set rather than over a list of known cases. The
    # trap is inherited by *forgetting*, so a test that names the actions it
    # checks would go green on the next one added.
    #
    # `create_event` is the one genuine exception and is asserted below as
    # one, so it cannot be quietly widened either.
    for action_type in permissions.OUTBOUND_ACTIONS:
        if action_type == "create_event":
            continue
        targets = permissions.recipients_of(action_type, {})
        assert targets, (
            f"{action_type} reads an empty proposal as reaching nobody, which "
            f"`check()` allows — it must name an unmatched placeholder instead")
        assert not permissions.check(action_type, {}).allowed, (
            f"{action_type} with an unreadable target ran unattended "
            f"(recipients_of returned {targets!r})")

    # The exception, stated: no attendees really is nobody but the user.
    assert permissions.recipients_of("create_event", {"title": "focus"}) == []
    assert permissions.check("create_event", {"title": "focus"}).allowed


def test_no_grant_silences_a_connector_write_that_cannot_be_undone():
    """The one exception that cuts across the tier.

    A user who allowed `linear:create_comment` has agreed to a comment. Nobody
    has ever agreed in advance to `delete_project`, and the app must not let
    them — a standing grant is made once and read forever, which is exactly
    the wrong shape for something with no undo.
    """
    params = {"server_id": "linear", "tool": "delete_project",
              "arguments": {"id": "P-1"}}
    permissions.grant("linear:delete_project",
                      kind=permissions.TOOL_RECIPIENT)
    try:
        verdict = permissions.check("mcp_action", params)
        assert not verdict.allowed
        assert "cannot be undone" in verdict.reason
        assert "approval every time" in verdict.reason, (
            "and it says so in the user's terms, not as a refusal code")
    finally:
        permissions.revoke("linear:delete_project",
                           kind=permissions.TOOL_RECIPIENT)
