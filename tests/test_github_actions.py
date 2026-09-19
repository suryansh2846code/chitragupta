"""Commenting on a PR and filing an issue — jobs 14 and 15.

Four connectors were read-only; this is the first of them to write. It is also
the first action whose allow-list is a **place** rather than a person, and that
is the interesting part.

The tier test has never been "does this reach somebody" — it is **can the gate
see what it reaches**. `update_event` went RED because the people a move
touches are on the event, not in the params. A comment on `acme/api` reaches
whoever watches `acme/api`, which nobody can enumerate — but the repository is
right there in the URL, deterministically. So `acme/api` is a stable,
comparable, revocable key, which makes *"always allow comments on acme/api"* a
coherent offer and the action AMBER.

The other judgement worth pinning: **a comment can be deleted and an issue
cannot.** GitHub has no delete-issue API, and closing one is not the inverse of
opening it — it is still there, still numbered, and everybody watching has
already been told.
"""
from __future__ import annotations

import pytest

from chitragupta import action_log, actions
from chitragupta.actions import REGISTRY, Risk, github_target
from chitragupta.agents import permissions as p
from chitragupta.agents.approvals import describe


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    action_log.reset_for_tests(tmp_path / "actions.db")
    for row in p.all_permissions():
        p.revoke(row["value"], kind=row["kind"])
    yield
    for row in p.all_permissions():
        p.revoke(row["value"], kind=row["kind"])
    action_log.reset_for_tests()


class FakeGitHub:
    def __init__(self, fail=""):
        self.calls: list[tuple] = []
        self._fail = fail

    def comment(self, owner, repo, number, body):
        if self._fail:
            return {"ok": False, "error": self._fail}
        self.calls.append(("comment", owner, repo, number, body))
        return {"ok": True, "id": 555, "owner": owner, "repo": repo,
                "detail": f"Commented on {owner}/{repo}#{number}"}

    def delete_comment(self, owner, repo, comment_id):
        self.calls.append(("delete", owner, repo, comment_id))
        return {"ok": True, "detail": "Comment deleted"}

    def create_issue(self, owner, repo, title, body="", labels=None):
        if self._fail:
            return {"ok": False, "error": self._fail}
        self.calls.append(("issue", owner, repo, title, body, labels))
        return {"ok": True, "id": 88, "owner": owner, "repo": repo,
                "detail": f"Opened {owner}/{repo}#88"}

    def issue_exists(self, owner, repo, number):
        return {"verified": True, "at": "2026-09-20T10:00:00Z"}


@pytest.fixture
def github(monkeypatch):
    def install(**kw):
        fake = FakeGitHub(**kw)
        monkeypatch.setattr(actions, "_writer", lambda src, cap: fake)
        return fake
    return install


# ── reading the target ─────────────────────────────────────────────────────

@pytest.mark.parametrize("url,want", [
    ("https://github.com/acme/api/issues/87", ("acme", "api", "87")),
    ("https://github.com/acme/api/pull/87", ("acme", "api", "87")),
    ("http://github.com/Acme-Co/my.repo/issues/3", ("Acme-Co", "my.repo", "3")),
    ("https://github.com/a/b.git/issues/1", ("a", "b", "1")),
    ("see https://github.com/acme/api/issues/9 for context",
     ("acme", "api", "9")),
])
def test_a_github_url_gives_owner_repo_and_number(url, want):
    assert github_target({"url": url}) == want


@pytest.mark.parametrize("url", ["", "not a url", "https://gitlab.com/a/b/issues/1",
                                 "https://github.com/acme/api"])
def test_something_that_is_not_an_issue_url_gives_nothing(url):
    assert github_target({"url": url}) == ("", "", "")


def test_a_repo_and_number_work_too():
    assert github_target({"repo": "acme/api", "number": 12}) == ("acme", "api", "12")


def test_a_pull_request_comment_uses_the_issue_endpoint(github):
    """GitHub files PR conversation comments as issue comments. Branching on
    `/pull/` would reach for the review API, which wants a commit sha."""
    fake = github()
    actions.run_now("github_comment", {
        "url": "https://github.com/acme/api/pull/87", "body": "Looks good."})
    assert fake.calls[0] == ("comment", "acme", "api", 87, "Looks good.")


# ── the tier, and why it is amber ──────────────────────────────────────────

@pytest.mark.parametrize("kind", ["github_comment", "github_create_issue"])
def test_a_repository_write_is_amber_not_red(kind):
    """Unlike a calendar move, the gate can SEE what this reaches — not the
    people, but the place, and the place is what an allow-list can hold."""
    assert REGISTRY[kind].risk is Risk.AMBER
    assert REGISTRY[kind].recipient_kind == p.REPO_RECIPIENT


def test_without_a_grant_it_waits(github):
    verdict = p.check("github_comment",
                      {"url": "https://github.com/acme/api/issues/87"})
    assert not verdict.allowed
    assert verdict.blocked_recipients == ("acme/api",)


def test_a_grant_is_per_repository(github):
    p.grant("acme/api", kind=p.REPO_RECIPIENT)
    assert p.check("github_comment",
                   {"url": "https://github.com/acme/api/issues/1"}).allowed
    assert not p.check("github_comment",
                       {"url": "https://github.com/other/repo/issues/1"}).allowed


def test_a_grant_ignores_how_the_repository_was_spelled():
    """GitHub is case-insensitive; a permission that depended on the spelling
    in a URL would be one that sometimes works."""
    p.grant("Acme/API", kind=p.REPO_RECIPIENT)
    assert p.check("github_comment",
                   {"url": "https://github.com/ACME/Api/pull/2"}).allowed
    assert p.revoke("acme/api", kind=p.REPO_RECIPIENT)


def test_an_unreadable_target_fails_closed():
    """It must never read as "reaches nobody" and run."""
    verdict = p.check("github_comment", {"url": "nonsense"})
    assert not verdict.allowed
    assert verdict.blocked_recipients == ("an unknown repository",)


def test_a_repository_grant_is_visible_and_revocable():
    """The list that exists to review these must show it, or the permission is
    one the user cannot take back."""
    p.grant("acme/api", kind=p.REPO_RECIPIENT)
    row = next(r for r in p.all_permissions() if r["value"] == "acme/api")
    assert row["kind_label"] == "Repository"


# ── doing it ───────────────────────────────────────────────────────────────

def test_a_comment_can_be_deleted_again(github):
    fake = github()
    out = actions.run_now("github_comment", {
        "url": "https://github.com/acme/api/issues/87", "body": "hi"})
    assert out["ok"] and out["reversible"]
    assert out["undo_label"] == "Delete the comment"
    assert actions.undo(out["log_id"])["ok"]
    assert fake.calls[-1] == ("delete", "acme", "api", "555")


def test_an_issue_cannot_be_undone(github):
    """GitHub has no delete-issue API, and closing one is not the inverse of
    opening it — it is still there, still numbered, and everybody watching has
    already been told."""
    github()
    assert REGISTRY["github_create_issue"].undo is None
    out = actions.run_now("github_create_issue",
                          {"repo": "acme/api", "title": "Timeouts"})
    assert out["ok"] and not out["reversible"]
    assert not actions.undo(out["log_id"])["ok"]


def test_an_issue_is_read_back_after_it_is_opened(github):
    github()
    out = actions.run_now("github_create_issue",
                          {"repo": "acme/api", "title": "Timeouts"})
    assert out["verified"]


def test_labels_are_accepted_as_a_comma_list(github):
    fake = github()
    actions.run_now("github_create_issue", {
        "repo": "acme/api", "title": "Timeouts", "labels": "bug, perf"})
    assert fake.calls[0][-1] == ["bug", "perf"]


def test_what_happened_reaches_the_brain(github):
    from chitragupta.core.store import get_store

    github()
    actions.run_now("github_comment", {
        "url": "https://github.com/acme/api/issues/87", "body": "hi"})
    assert any("acme/api#87" in m.text for m in get_store().list(limit=20))


# ── refusals ───────────────────────────────────────────────────────────────

def test_a_comment_with_no_target_is_refused(github):
    github()
    out = actions.run_now("github_comment", {"body": "hi"})
    assert not out["ok"]
    assert "issues/123" in out["error"]


def test_a_comment_with_nothing_to_say_is_refused(github):
    github()
    out = actions.run_now("github_comment",
                          {"url": "https://github.com/a/b/issues/1"})
    assert not out["ok"] and "nothing to say" in out["error"]


def test_an_issue_with_no_title_is_refused(github):
    github()
    assert "needs a title" in actions.run_now(
        "github_create_issue", {"repo": "acme/api"})["error"]


def test_github_not_being_connected_says_so(monkeypatch):
    monkeypatch.setattr(actions, "_writer", lambda src, cap: None)
    out = actions.run_now("github_comment", {
        "url": "https://github.com/a/b/issues/1", "body": "hi"})
    assert "not connected" in out["error"]


@pytest.mark.parametrize("code,expect", [
    (401, "read-only"), (403, "read-only"),
    (404, "does not exist"), (410, "turned off"),
])
def test_githubs_refusals_say_what_to_do_about_them(code, expect):
    """A 404 on a private repository the token cannot see looks exactly like a
    missing one, and the user needs to be told which it might be."""
    import urllib.error

    from chitragupta.connectors.github import GitHubConnector

    out = GitHubConnector()._refusal(
        urllib.error.HTTPError("u", code, "no", {}, None))  # type: ignore[arg-type]
    assert expect in out["error"]
    assert out["ok"] is False


# ── what the user approves ─────────────────────────────────────────────────

def test_the_card_says_which_repository_and_which_thread():
    """A comment on the wrong repository is public, permanent, and somebody
    else's notification."""
    assert describe("github_comment",
                    {"url": "https://github.com/acme/api/issues/87"}) == (
        "Comment on acme/api#87")


def test_the_issue_card_names_the_repository_and_the_title():
    assert describe("github_create_issue",
                    {"repo": "acme/api", "title": "Timeouts on /search"}) == (
        "Open an issue on acme/api — “Timeouts on /search”")


def test_a_card_for_an_unreadable_target_says_so_rather_than_inventing_one():
    assert "unknown repository" in describe("github_comment", {"url": "nope"})


# ── who can do it ──────────────────────────────────────────────────────────

def test_the_engineer_can_write_to_a_repository_and_search_one_first():
    """An agent that can file an issue and cannot search files duplicates."""
    from chitragupta.agents.presets import get_agent

    engineer = get_agent("engineer")
    assert "github_create_issue" in engineer.actions
    assert "github_comment" in engineer.actions
    assert "search_source" in engineer.tools


def test_the_researcher_still_acts_on_nothing():
    """It reports; it does not send, schedule or act on the world — and a
    repository write is acting on the world."""
    from chitragupta.agents.presets import get_agent

    assert not {"github_comment", "github_create_issue"} & set(
        get_agent("research").actions)


def test_the_prompt_forbids_assembling_a_url():
    from chitragupta.agents.prompt import _BLOCKS

    assert "Never assemble one" in _BLOCKS["github_comment"]


def test_the_prompt_says_an_issue_cannot_be_deleted():
    from chitragupta.agents.prompt import _BLOCKS

    assert "CANNOT be deleted" in _BLOCKS["github_create_issue"]
    assert "duplicate" in _BLOCKS["github_create_issue"]
