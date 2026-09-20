"""The approval card can put somebody on the allow-list, and only when it can.

`permissions.check()` refuses every unattended outbound action whose recipient
is not allow-listed, and there was no way in the product to allow anybody:
`GET/POST/DELETE /api/agents/permissions` had zero callers in `chitragupta/web/`.
A routine created to email the same person every week asked forever.

The half that is easy to get wrong is not the button, it is *when* the button
appears and *what* it grants:

* offered only when the server said an allow-list could clear this action, so it
  is never a control that cannot work
* granting the addresses the server named in `blocked`, never an address
  recovered from the sentence beside them — that sentence is prose, and
  `permissions._every_address_in` exists because `allowed@x attacker@y` in one
  `to:` field read as the allowed address alone

So the harness presses the button and records the requests, rather than
asserting on HTML. A render assertion would have passed with no handler attached
at all.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

BLOCKED_EMAIL = {
    "id": "a1",
    "summary": 'Email "the report" to dana@work.test',
    "reason": ("Waiting for your approval — dana@work.test is not on your "
               "allowed list."),
    "blocked": ["dana@work.test"],
    "routine_name": "Weekly report",
}

#: Refused by `NEVER_UNATTENDED`, where no allow-list could ever help.
NOT_ALLOWABLE = {
    "id": "a2",
    "summary": 'New automation "digest"',
    "reason": "Creating automations always needs your approval.",
    "blocked": [],
}


def _run(rows):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/allow_recipient.mjs"), str(WEB / "app.js")],
        input=json.dumps({"rows": rows}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def queue():
    return _run([BLOCKED_EMAIL, NOT_ALLOWABLE])


def test_the_queue_rendered(queue):
    assert queue["error"] is None, queue["error"]


# ── when the button exists ───────────────────────────────────────────────
def test_only_the_action_an_allow_list_could_clear_offers_a_grant(queue):
    """One button across two cards.

    `CLAUDE.md`: never show a control that cannot work. An "always allow" on a
    `create_routine` would be tapped once and then never believed again.
    """
    assert queue["allowButtons"] == 1


def test_the_address_is_spelled_out_in_the_label(queue):
    """A standing grant the user cannot read before tapping is not consent —
    the same reason a batch triage is one card and not four."""
    assert "Always allow dana@work.test" in queue["html"]
    assert "Always allow this" not in queue["html"]


def test_approving_once_is_still_the_first_button(queue):
    """Order is the recommendation. The permanent choice must not be the one a
    hand lands on by default."""
    html = queue["html"]
    assert html.index("data-aprok=") < html.index("data-apralw=")


def test_the_unallowable_card_still_says_why(queue):
    """Removing the button must not remove the explanation with it."""
    assert "Creating automations always needs your approval." in queue["html"]


# ── what pressing it does ────────────────────────────────────────────────
def test_pressing_it_grants_the_address_the_server_named(queue):
    assert queue["pressed"] == "ok", queue["pressed"]
    grants = [c for c in queue["calls"]
              if c["url"] == "/api/agents/permissions" and c["method"] == "POST"]
    assert [g["body"]["value"] for g in grants] == ["dana@work.test"]


def test_pressing_it_then_runs_the_action_that_was_waiting(queue):
    """Granting alone would leave the email unsent and the card gone — the user
    pressed a button about *this* action, so this action has to happen."""
    assert any(c["url"] == "/api/agents/approvals/a1/approve"
               and c["method"] == "POST" for c in queue["calls"])


def test_the_grant_happens_before_the_action(queue):
    """In the other order a failed grant leaves the user believing they will not
    be asked again, having already sent the mail."""
    urls = [c["url"] for c in queue["calls"] if c["method"] == "POST"]
    assert urls.index("/api/agents/permissions") < urls.index(
        "/api/agents/approvals/a1/approve")


def test_the_user_is_told_what_was_granted(queue):
    """Naming the address, because the consequence outlives this card."""
    assert any("dana@work.test" in t for t in queue["toasts"]), queue["toasts"]


# ── more than one recipient ──────────────────────────────────────────────
def test_every_blocked_recipient_is_granted_not_just_the_first():
    """The injection case. An agent that read a stranger's email can propose a
    `to:` with two addresses in it; `permissions.check()` reports both as
    blocked, and a grant that covered one would silently leave the other
    queueing — or worse, read as having covered both.
    """
    out = _run([{**BLOCKED_EMAIL,
                 "blocked": ["dana@work.test", "billing@acme.test"]}])
    grants = [c["body"]["value"] for c in out["calls"]
              if c["url"] == "/api/agents/permissions" and c["method"] == "POST"]

    assert grants == ["dana@work.test", "billing@acme.test"]


def test_two_recipients_are_counted_rather_than_crammed_into_the_label():
    """Two addresses in a button label is unreadable, and an unreadable label is
    the habituation this design is trying to avoid. The count goes in the label
    and the addresses go in the tooltip."""
    out = _run([{**BLOCKED_EMAIL,
                 "blocked": ["dana@work.test", "billing@acme.test"]}])

    assert "Always allow 2 people" in out["html"]
    assert 'title="dana@work.test, billing@acme.test"' in out["html"]


#: A connector write. The allow-list holds `server:tool`, which is a thing and
#: not a person — and the card is the only place the user reads what they are
#: agreeing to stop being asked about.
BLOCKED_TOOL = {
    "id": "a3",
    "summary": 'Run "create_issue" on Linear',
    "reason": ("Waiting for your approval — the connector tool "
               "linear:create_issue is not on your allowed list."),
    "blocked": ["linear:create_issue"],
    "kind": "connector_tool",
}


def test_a_connector_tool_is_granted_onto_the_connector_list():
    """The bug this repeats: "Always allow" wrote a Telegram handle onto the
    EMAIL list, so the gate for `message_send` never read it — the tap did
    nothing and said it had worked. `kind` comes from the server, per action.
    """
    out = _run([BLOCKED_TOOL])
    posts = [c["body"] for c in out["calls"]
             if c["url"] == "/api/agents/permissions" and c["method"] == "POST"]

    assert posts == [{"value": "linear:create_issue", "kind": "connector_tool",
                      "note": "allowed from an approval"}]


def test_a_connector_tool_is_not_described_as_a_person():
    """"2 people won't be asked about again" after approving a write to Linear
    is not a sentence the user can check against anything."""
    out = _run([{**BLOCKED_TOOL,
                 "blocked": ["linear:create_issue", "linear:create_comment"]}])

    assert "Always allow 2 connector tools" in out["html"]
    assert "people" not in out["html"]
    assert not any("people" in t for t in out["toasts"]), out["toasts"]


def test_a_row_without_the_field_at_all_is_safe():
    """An approval stored before `blocked_json` shipped has no `blocked`.

    The card must render with no grant offered, not throw and blank the panel —
    `approvals._public` defaults it, and this is the frontend half of the same
    upgrade path.
    """
    out = _run([{k: v for k, v in BLOCKED_EMAIL.items() if k != "blocked"}])

    assert out["error"] is None
    assert out["allowButtons"] == 0
    assert "Always allow" not in out["html"]
