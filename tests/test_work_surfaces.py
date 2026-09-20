"""Phase 4 — Linear, Notion and Drive stop being read-only.

Three connectors that could read and not write, so an agent could tell the
user what was on their board and never put anything on it.

**The interesting work is not the API calls, it is the tier of each.** One
question decides all seven, and it is the same question `update_event` and
`mcp_action` were decided by: *can the gate see a key a PERSON could read and
revoke?* Not "who does this reach" — nobody can enumerate who watches a Linear
team either.

    Linear  →  🟡  `ENG` is in every issue id. "Always allow filing into ENG"
                   is a sentence somebody can agree to and later withdraw.
    Notion  →  🔴  a page is a uuid and nothing else. `notion:a1b2c3d4-…` on
                   the allow-list screen is an internal shown to the user,
                   and a permission nobody can read is one nobody can audit.
    Drive   →  🟢  for `create_doc`: it lands in the user's own Drive and
                   reaches nobody until they share it — `create_draft`'s
                   argument exactly.
               🟡  for `share`, against the EMAIL list, because that is
                   genuinely who it reaches and the address is on the card.
               …except a public link, which has no recipient to allow-list
                   and therefore asks every time.

The roadmap predicted Notion amber. This is the second time the tier test has
overruled the prediction, and for the same reason both times.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import REGISTRY, Risk
from chitragupta.agents import permissions
from chitragupta.agents.approvals import describe

WORK_ACTIONS = ("linear_create_issue", "linear_comment", "linear_update_issue",
                "notion_append", "notion_create_page",
                "drive_create_doc", "drive_share")


@pytest.fixture(autouse=True)
def clean_slate():
    conn = permissions._conn()
    conn.execute("DELETE FROM action_permissions")
    conn.commit()
    yield
    conn = permissions._conn()
    conn.execute("DELETE FROM action_permissions")
    conn.commit()


# ── every one of them is a real action, not a half one ───────────────────
@pytest.mark.parametrize("name", WORK_ACTIONS)
def test_it_is_in_the_registry_with_a_tier(name):
    assert name in REGISTRY
    assert REGISTRY[name].risk in (Risk.GREEN, Risk.AMBER, Risk.RED)


@pytest.mark.parametrize("name", WORK_ACTIONS)
def test_the_model_is_taught_how_to_propose_it(name):
    """An action in the registry and not in the prompt is one no agent can
    ever use — `test_email_drafts.py` pins that, and this says which."""
    from chitragupta.agents.prompt import _BLOCKS, KNOWN_ACTIONS

    assert name in KNOWN_ACTIONS
    assert name in _BLOCKS


@pytest.mark.parametrize("name", WORK_ACTIONS)
def test_the_card_says_something_a_person_can_judge(name):
    """No internals, and never the bare action name."""
    line = describe(name, {"team_key": "ENG", "issue": "ENG-12",
                           "title": "Search is slow", "body": "…",
                           "page_id": "a1b2c3", "parent_id": "a1b2c3",
                           "text": "the note", "file_id": "f1",
                           "email": "rahul@work.test", "role": "reader"})
    assert line and name not in line
    assert "_" not in line.replace("rahul@work.test", ""), line


def test_a_notion_card_never_shows_the_page_id():
    """The uuid is the reason these are red. Putting it on the card would be
    the same internal in a second place."""
    line = describe("notion_append",
                    {"page_id": "a1b2c3d4-e5f6", "text": "Decision: ship it"})

    assert "a1b2c3d4" not in line
    assert "Decision: ship it" in line, "so the user judges the words"


def test_a_drive_share_card_leads_with_who_and_what_kind():
    assert "rahul@work.test" in describe(
        "drive_share", {"file_id": "f", "email": "rahul@work.test",
                        "role": "writer"})
    assert "writer" in describe(
        "drive_share", {"file_id": "f", "email": "rahul@work.test",
                        "role": "writer"})


def test_a_public_share_card_says_anyone_rather_than_an_address():
    """The one a person must not skim past."""
    line = describe("drive_share", {"file_id": "f", "anyone": "true"})

    assert "anyone with the link" in line


# ── Linear: a team is a place, the way a repository is ───────────────────
def test_a_linear_write_is_refused_until_the_team_is_allowed():
    verdict = permissions.check("linear_comment",
                                {"issue": "ENG-12", "body": "hi"})

    assert not verdict.allowed
    assert verdict.blocked_recipients == ("linear:eng",)


def test_allowing_a_team_covers_that_team_and_no_other():
    permissions.grant("linear:eng", kind=permissions.LINEAR_RECIPIENT)

    assert permissions.check("linear_comment",
                             {"issue": "ENG-12", "body": "hi"}).allowed
    assert not permissions.check("linear_comment",
                                 {"issue": "OPS-3", "body": "hi"}).allowed


def test_filing_and_commenting_land_on_the_same_allow_list_row():
    """The split-key bug, caught before it shipped.

    Keyed by whatever string the model happened to use, filing into
    "Engineering" and commenting on `ENG-12` would be two grants for one
    team. Each would work, so nothing is unsafe — but the user is asked twice
    for one decision and cannot see why. `team_key` is the short prefix that
    also opens every issue id, so both produce `linear:eng`.
    """
    permissions.grant("linear:eng", kind=permissions.LINEAR_RECIPIENT)

    assert permissions.check(
        "linear_create_issue", {"team_key": "ENG", "title": "x"}).allowed
    assert permissions.check(
        "linear_comment", {"issue": "ENG-12", "body": "x"}).allowed


def test_filing_with_no_team_named_fails_closed():
    """The connector would happily pick the only team there is. The GATE
    cannot see which that would be, so it must not let it through — the same
    rule that stops an unaddressed message going out."""
    verdict = permissions.check("linear_create_issue", {"title": "x"})

    assert not verdict.allowed
    assert verdict.blocked_recipients == ("an unspecified Linear team",)


def test_moving_an_issue_is_reversible_for_real():
    """Not nominally. The handler reads the issue BEFORE changing it, so undo
    restores the assignee and status it actually had — the `update_event`
    discipline, where guessing the previous value is not an undo."""
    spec = REGISTRY["linear_update_issue"]

    assert spec.undo is not None
    source = __import__("inspect").getsource(
        __import__("chitragupta.connectors.linear", fromlist=["x"])
        .LinearConnector.update_issue)
    assert "issue_state" in source, "it changed the issue without reading it first"
    assert "before" in source


def test_filing_an_issue_declares_no_undo():
    """Linear can archive an issue, not unmake it, and everyone subscribed to
    the team has already been notified."""
    assert REGISTRY["linear_create_issue"].undo is None


# ── Notion: no key a person can read, so no standing grant ───────────────
@pytest.mark.parametrize("name", ("notion_append", "notion_create_page"))
def test_a_notion_write_always_asks(name):
    verdict = permissions.check(name, {"page_id": "abc", "text": "x"})

    assert not verdict.allowed
    assert verdict.blocked_recipients == (), (
        "it offered a grant the allow-list cannot honour")
    assert "page id" in verdict.reason, "and says why, in the user's terms"


@pytest.mark.parametrize("name", ("notion_append", "notion_create_page"))
def test_a_notion_write_can_still_be_taken_back(name):
    """Asking every time is not a reason to skip the undo — it is the reason
    the undo matters, because the user is approving under time pressure."""
    assert REGISTRY[name].undo is not None


# ── Drive: one green, one amber, one that always asks ────────────────────
def test_making_a_document_needs_no_approval():
    """`create_draft`'s argument. It lands in the user's own Drive; nobody
    else can see it until they share it. This is the rung an agent can do
    overnight."""
    assert REGISTRY["drive_create_doc"].risk is Risk.GREEN
    assert permissions.check("drive_create_doc", {"title": "Notes"}).allowed


def test_making_a_document_can_be_undone():
    assert REGISTRY["drive_create_doc"].undo is not None


def test_sharing_with_a_person_is_judged_against_the_email_list():
    """Sharing a document with Rahul is the same kind of decision as emailing
    him one, so it is the same list — not a fourth one nobody would think to
    look at."""
    assert permissions.RECIPIENT_KINDS["drive_share"] == \
        permissions.EMAIL_RECIPIENT

    params = {"file_id": "f1", "email": "rahul@work.test"}
    assert not permissions.check("drive_share", params).allowed

    permissions.grant("rahul@work.test", kind=permissions.EMAIL_RECIPIENT)
    assert permissions.check("drive_share", params).allowed


def test_a_public_link_is_refused_even_to_a_user_who_allowed_everybody():
    """"Anyone with the link" has no recipient. A standing grant made about
    one person must never quietly cover an unbounded audience."""
    permissions.grant("rahul@work.test", kind=permissions.EMAIL_RECIPIENT)

    verdict = permissions.check(
        "drive_share", {"file_id": "f1", "email": "rahul@work.test",
                        "anyone": "true"})

    assert not verdict.allowed
    assert "anyone who has the link" in verdict.reason


def test_a_share_we_cannot_address_fails_closed():
    """The same rule as every other outbound action, and the one that has
    already been got wrong twice in this repo."""
    verdict = permissions.check("drive_share", {"file_id": "f1"})

    assert not verdict.allowed


def test_sharing_can_be_taken_back_and_says_what_that_does_not_undo():
    """They may already have opened it. An undo that implied otherwise would
    stop the user doing the thing they actually need to do."""
    import inspect

    from chitragupta.connectors.gdrive import GoogleDriveConnector

    assert REGISTRY["drive_share"].undo is not None
    assert "already have opened" in inspect.getsource(
        GoogleDriveConnector.unshare)


# ── the scope we had to ask for ──────────────────────────────────────────
def test_drive_writes_ask_for_the_narrowest_scope_that_works():
    """`drive.file` reaches only files this app created. The full `drive`
    scope would have bought nothing the 18 jobs need and asked for the user's
    entire Drive."""
    from chitragupta.connectors.google_auth import SCOPES

    assert any("drive.file" in s for s in SCOPES)
    assert not any(s.endswith("/auth/drive") for s in SCOPES)


def test_an_old_token_is_told_to_reconnect_rather_than_failing_oddly():
    """A token issued before we asked for `drive.file` carries read and
    nothing else, so every create comes back 403. Checked BEFORE anything is
    proposed — the `gmail.modify` lesson: never approve a card that cannot
    work."""
    import inspect

    from chitragupta.connectors.gdrive import GoogleDriveConnector

    source = inspect.getsource(GoogleDriveConnector._service)
    assert "may_write_drive" in source
    assert "NEEDS_DRIVE_SCOPE" in source
