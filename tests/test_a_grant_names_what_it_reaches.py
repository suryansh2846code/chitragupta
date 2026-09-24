"""A connector grant names what the tool reaches, not just what it is.

`ACTION-COVERAGE.md § Per-tool grants` made `mcp_action` promotable by finding
a key the gate could see: `server:tool`. The argument was explicitly that the
key "got better, so the tier followed" — and it stopped one step short. A
write names the **container** it acts inside, and that container is the same
fact a GitHub card already showed under `REPO_RECIPIENT`.

It matters because the two keys this replaces are each too wide in a different
direction, and retiring the built-in GitHub connector would have shipped the
wider of them:

    github:add_issue_comment        every repository the token can reach
    acme/api  (REPO_RECIPIENT)      every GitHub action, merge_pull_request too
    github:add_issue_comment@acme/api   one verb, one repository

The fallback is the safety property. A call whose arguments name no container
is keyed exactly as it was before, so this can only ever narrow a grant — and
a grant written before scopes existed stops matching rather than covering more
than the person meant. A permission change has to fail in that direction.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import (
    connector_scope,
    connector_tool_key,
    connector_tool_label,
)
from chitragupta.agents import permissions


@pytest.fixture(autouse=True)
def clean_slate():
    perms = permissions._conn()
    perms.execute("DELETE FROM action_permissions")
    perms.commit()


def _call(tool: str, **arguments) -> dict:
    return {"server_id": "github", "tool": tool, "arguments": arguments}


# ── deriving the scope ─────────────────────────────────────────────────────


def test_the_container_comes_out_of_the_arguments():
    assert connector_scope({"owner": "acme", "repo": "api"}) == "acme/api"


def test_content_is_never_a_scope():
    """The blob the original argument was right to refuse. A body is what the
    action says, not where it lands, and keying on it would make every grant
    single-use."""
    assert connector_scope({"body": "ship it", "title": "Fix the thing"}) == ""


def test_an_item_id_is_never_a_scope():
    """`acme/api#87` is narrower than `acme/api` and useless: nobody comments
    twice on one issue, so a grant keyed there could never be spent. The
    container is the granularity a person actually decides at."""
    key = connector_tool_key(_call("add_issue_comment", issue_number=87,
                                   comment_id="c1", body="hi"))

    assert key == "github:add_issue_comment"


def test_a_call_that_names_nothing_keeps_the_key_it_always_had():
    """The fallback, and the reason this cannot widen anything: an unscoped
    call is keyed exactly as it was before scopes existed."""
    assert connector_tool_key(
        {"server_id": "linear", "tool": "create_comment",
         "arguments": {"issueId": "ENG-1", "body": "x"}}
    ) == "linear:create_comment"


def test_half_a_key_still_fails_closed():
    """Unchanged and load-bearing — an action missing either half must not
    produce something a grant could match."""
    assert connector_tool_key({"tool": "add_issue_comment"}) == ""
    assert connector_tool_key({"server_id": "github"}) == ""


# ── what the grant then covers ─────────────────────────────────────────────


def _grant_from_card(params: dict) -> str:
    """Grant exactly what the card named, the way `test_asks_once` insists.

    Never a string this file made up: a grant written against something the
    card did not name is a button that says it worked and did nothing.
    """
    verdict = permissions.check("mcp_action", params)
    assert not verdict.allowed, "expected this to be refused first"
    value = verdict.blocked_recipients[0]
    permissions.grant(value, kind=permissions.TOOL_RECIPIENT)
    return value


def test_granting_one_repository_does_not_grant_another():
    """The property the whole change exists for. A bare `add_issue_comment`
    grant covered every repository the token could reach — including private
    ones the user never had in mind."""
    _grant_from_card(_call("add_issue_comment", owner="acme", repo="api",
                           issue_number=1, body="hi"))

    assert permissions.check("mcp_action", _call(
        "add_issue_comment", owner="acme", repo="api",
        issue_number=2, body="again")).allowed
    assert not permissions.check("mcp_action", _call(
        "add_issue_comment", owner="acme", repo="secrets",
        issue_number=1, body="hi")).allowed


def test_granting_one_verb_does_not_grant_another_on_the_same_repository():
    """Narrower than the `REPO_RECIPIENT` grant it replaces, which covered
    every GitHub action on the repo. `merge_pull_request` is the one
    `connectors/CLAUDE.md` names as the reason `_is_write` exists."""
    _grant_from_card(_call("add_issue_comment", owner="acme", repo="api",
                           issue_number=1, body="hi"))

    assert not permissions.check("mcp_action", _call(
        "merge_pull_request", owner="acme", repo="api", pullNumber=3)).allowed


def test_an_old_unscoped_grant_does_not_cover_a_scoped_call():
    """Fails closed across the change. Somebody who granted
    `github:add_issue_comment` before scopes existed is asked once more rather
    than silently keeping the wider permission they were given."""
    permissions.grant("github:add_issue_comment",
                      kind=permissions.TOOL_RECIPIENT)

    assert not permissions.check("mcp_action", _call(
        "add_issue_comment", owner="acme", repo="api",
        issue_number=1, body="hi")).allowed


# ── what the person reads ──────────────────────────────────────────────────


def test_the_key_never_reaches_the_user_verbatim():
    """`/CLAUDE.md`: never surface an internal. The refusal has to say which
    tool and which connector without printing the id that encodes them."""
    verdict = permissions.check("mcp_action", _call(
        "add_issue_comment", owner="acme", repo="api", issue_number=1,
        body="hi"))

    assert "github:add_issue_comment@acme/api" not in verdict.reason
    for part in ("github", "add_issue_comment", "acme/api"):
        assert part in verdict.reason, f"the refusal never says {part}"


def test_the_allow_list_row_carries_its_own_display_string():
    """The id and the display string are separate fields wherever a person
    reads one — a screen that prints keys is a screen nobody can audit."""
    _grant_from_card(_call("add_issue_comment", owner="acme", repo="api",
                           issue_number=1, body="hi"))

    row = permissions.list_permissions(kind=permissions.TOOL_RECIPIENT)[0]

    assert row["value"] == "github:add_issue_comment@acme/api"
    assert row["label"] == "github · add_issue_comment on acme/api"


def test_a_connector_naming_no_container_still_names_itself():
    assert connector_tool_label("http:send_message") == "http · send_message"
