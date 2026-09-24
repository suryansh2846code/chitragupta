"""Undo on a connector write — only where the server publishes the inverse.

`mcp_action` carried no undo at all, and the note said why: the verb belongs
to somebody else's server and nothing tells us what its inverse is. The first
half stands. The second was not quite true — the server's own tool list tells
us, for the one case where an edit honestly retracts something.

This matters now because retiring the built-in GitHub connector takes
`github_comment` with it, and that action shipped `undo_label="Delete the
comment"`. GitHub's MCP server publishes no delete-comment tool at all: of its
45 tools the only delete is `delete_file`. So the choice was a button that
lies, no button, or a button that says what can actually happen.

The rule it settles, which is about shape rather than GitHub:

* **The inverse has to be published.** Not inferred from the verb, not a table
  of known servers — present in the tool list, or there is no button.
* **An edit retracts a comment and nothing else.** Editing a created issue is
  not undoing it, and a merged pull request has no inverse at any price.
* **The label says what happens.** The comment stays in the thread with its
  history visible. "Delete" would be the button describing a different event.
"""
from __future__ import annotations

import pytest

from chitragupta import actions


class _Ref:
    def __init__(self, server_id: str, tool: str) -> None:
        self.server_id, self.tool = server_id, tool


@pytest.fixture
def published(monkeypatch):
    """What the servers say they can do. Nothing is assumed about any vendor."""
    def install(*pairs):
        refs = [_Ref(s, t) for s, t in pairs]
        import chitragupta.connectors.mcp_tools as mcp_tools
        monkeypatch.setattr(mcp_tools, "write_tools", lambda: refs)
    return install


POSTED = {"ok": True, "detail": {"id": 4242, "body": "looks good"}}
CALL = {"server_id": "github", "tool": "add_issue_comment",
        "arguments": {"owner": "acme", "repo": "api", "issue_number": 7,
                      "body": "looks good"}}


# ── when a button appears at all ───────────────────────────────────────────


def test_a_comment_is_retractable_when_the_server_can_edit_one(published):
    published(("github", "add_issue_comment"), ("github", "update_issue_comment"))

    assert actions._connector_undo_label(CALL, POSTED) == "Retract the comment"


def test_no_button_when_the_server_publishes_no_way_to_edit(published):
    """GitHub's real tool list, which is how this case was found: it can add a
    comment and update one, but if it could only add, there is no inverse and
    the card must not pretend otherwise."""
    published(("github", "add_issue_comment"))

    assert actions._connector_undo_label(CALL, POSTED) == ""


def test_no_button_when_the_server_never_said_what_it_posted(published):
    """Without an id there is nothing to edit. A button that would fail is
    worse than no button — that is what `undo` is documented never to be."""
    published(("github", "add_issue_comment"), ("github", "update_issue_comment"))

    assert actions._connector_undo_label(CALL, {"ok": True, "detail": {}}) == ""


def test_merging_a_pull_request_offers_nothing(published):
    """The case that makes a static undo on `mcp_action` wrong. Every connector
    write would have advertised itself as reversible, including this."""
    published(("github", "merge_pull_request"), ("github", "update_pull_request"))

    assert actions._connector_undo_label(
        {"server_id": "github", "tool": "merge_pull_request",
         "arguments": {"owner": "acme", "repo": "api", "pullNumber": 3}},
        {"ok": True, "detail": {"id": 9}}) == ""


def test_creating_an_issue_is_not_undone_by_editing_it(published):
    """An issue that exists, is numbered and has already notified everybody
    watching is not un-opened by changing its title. `github_create_issue`
    shipped with no undo for exactly this reason, and that has to survive."""
    published(("github", "issue_write"))

    assert actions._connector_undo_label(
        {"server_id": "github", "tool": "issue_write",
         "arguments": {"method": "create", "owner": "acme", "repo": "api"}},
        {"ok": True, "detail": {"id": 5}}) == ""


def test_the_inverse_is_never_borrowed_from_another_server(published):
    """One server publishing an editor says nothing about another's."""
    published(("github", "add_issue_comment"), ("other", "update_issue_comment"))

    assert actions._connector_undo_label(CALL, POSTED) == ""


# ── what the undo actually does ────────────────────────────────────────────


def test_it_overwrites_through_the_servers_own_tool(published, monkeypatch):
    published(("github", "add_issue_comment"), ("github", "update_issue_comment"))
    seen = {}
    monkeypatch.setattr(actions, "_mcp_action",
                        lambda p: seen.update(p) or {"ok": True})

    out = actions._undo_connector_action(CALL, POSTED)

    assert out["ok"]
    assert seen["tool"] == "update_issue_comment"
    assert seen["arguments"]["comment_id"] == "4242"
    assert seen["arguments"]["body"] == actions.RETRACTION_BODY


def test_it_keeps_the_scope_and_drops_the_original_words(published, monkeypatch):
    """The retraction lands on the same comment, and is a different sentence —
    replaying the original body would repost what is being withdrawn."""
    published(("github", "add_issue_comment"), ("github", "update_issue_comment"))
    seen = {}
    monkeypatch.setattr(actions, "_mcp_action",
                        lambda p: seen.update(p) or {"ok": True})

    actions._undo_connector_action(CALL, POSTED)

    assert seen["arguments"]["owner"] == "acme"
    assert seen["arguments"]["repo"] == "api"
    assert "looks good" not in str(seen["arguments"])
    assert "issue_number" not in seen["arguments"]


def test_an_unretractable_write_refuses_rather_than_half_running(published):
    published(("github", "add_issue_comment"))

    out = actions._undo_connector_action(CALL, POSTED)

    assert out["ok"] is False
    assert "cannot be taken back" in out["error"]


# ── the button the user is shown ───────────────────────────────────────────


def _finished(params: dict, result: dict) -> dict:
    """What the card is actually handed, through the real post-run path."""
    spec = actions.REGISTRY["mcp_action"]
    return actions._finish("mcp_action", spec, params, dict(result))


def test_the_card_is_told_per_run_and_not_per_action_type(published, monkeypatch):
    """The wiring, end to end, not the predicate on its own.

    `reversible` used to be `spec.undo is not None` — one answer for the whole
    action type, which is why a static undo on `mcp_action` would have put
    "Delete the comment" on a merged pull request. This asserts the card gets
    a different answer for two runs of the same action.
    """
    published(("github", "add_issue_comment"), ("github", "update_issue_comment"))

    retractable = _finished(CALL, POSTED)
    assert retractable["reversible"] is True
    assert retractable["undo_label"] == "Retract the comment"

    merged = _finished(
        {"server_id": "github", "tool": "merge_pull_request",
         "arguments": {"owner": "acme", "repo": "api"}},
        {"ok": True, "detail": {"id": 9}})
    assert merged["reversible"] is False
    assert not merged.get("undo_label"), (
        "a merged pull request must not be offered an Undo button")


def test_a_failed_write_is_never_offered_an_undo(published):
    """Nothing was posted, so there is nothing to retract."""
    published(("github", "add_issue_comment"), ("github", "update_issue_comment"))

    assert _finished(CALL, {"ok": False, "error": "nope"})["reversible"] is False
