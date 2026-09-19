"""GitHub connector — ingests issues/PRs assigned to or opened by you.

Single-token auth: a GitHub personal access token (classic or fine-grained,
`repo` read scope). Declared as a `secret_field`, so the UI renders an in-app
field — no .env editing.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..config import get_settings
from .base import Connector, SyncResult

API = "https://api.github.com"


class GitHubConnector(Connector):
    name = "github"
    label = "GitHub"
    # Was never in the scheduler's list, for no reason anyone recorded: a
    # token-backed source the user connected that then never refreshed.
    auto_sync = True
    incremental = True
    secret_field = {
        "key": "GITHUB_TOKEN",
        "label": "GitHub personal access token",
        "placeholder": "ghp_… / github_pat_…",
        "help_url": "https://github.com/settings/tokens",
        "steps": [
            "Open <b>github.com/settings/tokens</b> → <b>Generate new token</b> "
            "with <b>repo</b> (read) scope, then copy it.",
            "Paste it below and click Save.",
        ],
    }

    def is_configured(self) -> tuple[bool, str]:
        if get_settings().get_secret("GITHUB_TOKEN"):
            return True, ""
        return False, "click setup to paste your GitHub token"

    def _get(self, path: str, token: str) -> Any:
        req = urllib.request.Request(
            f"{API}{path}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "Chitragupta"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    # ── writing ──────────────────────────────────────────────────────────
    #
    # `sync` only ingests; these are named methods reachable solely through a
    # confirmed action, the rule `connectors/CLAUDE.md` states for every
    # connector that can change somebody else's system.

    def _post(self, path: str, token: str, body: dict,
              method: str = "POST") -> Any:
        req = urllib.request.Request(
            f"{API}{path}", method=method,
            data=json.dumps(body).encode() if body else None,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "User-Agent": "Chitragupta"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}

    def _refusal(self, exc: Exception) -> dict:
        """GitHub's "no" in the one sentence that says what to do about it."""
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code in (401, 403):
                return {"ok": False, "reauth": True, "error":
                        "GitHub refused that — the token is read-only or has "
                        "no access to that repository. Make one with `repo` "
                        "scope and paste it under Connectors."}
            if exc.code == 404:
                return {"ok": False, "error":
                        "GitHub says that does not exist. A private repository "
                        "the token cannot see looks the same as a missing one."}
            if exc.code == 410:
                return {"ok": False, "error": "Issues are turned off on that "
                                              "repository."}
        return {"ok": False, "error": str(exc)[:200]}

    def comment(self, owner: str, repo: str, number: int, body: str) -> dict:
        """Add a comment to an issue or pull request (WRITE).

        One endpoint for both: GitHub files PR conversation comments as issue
        comments, and a caller that branched on the URL saying `/pull/` would
        be reaching for the *review* API, which is a different thing that
        wants a commit sha.
        """
        token = get_settings().get_secret("GITHUB_TOKEN")
        if not token:
            return {"ok": False, "error": "GitHub is not connected."}
        try:
            made = self._post(
                f"/repos/{owner}/{repo}/issues/{number}/comments", token,
                {"body": body})
        except Exception as exc:
            return self._refusal(exc)
        return {"ok": True, "id": made.get("id"), "link": made.get("html_url"),
                "owner": owner, "repo": repo,
                "detail": f"Commented on {owner}/{repo}#{number}"}

    def delete_comment(self, owner: str, repo: str, comment_id: str) -> dict:
        """Remove a comment this app added. The inverse of `comment`."""
        token = get_settings().get_secret("GITHUB_TOKEN")
        if not token:
            return {"ok": False, "error": "GitHub is not connected."}
        try:
            self._post(f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
                       token, {}, method="DELETE")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"ok": True, "detail": "That comment was already gone"}
            return self._refusal(exc)
        except Exception as exc:
            return self._refusal(exc)
        return {"ok": True, "detail": "Comment deleted"}

    def create_issue(self, owner: str, repo: str, title: str,
                     body: str = "", labels: list[str] | None = None) -> dict:
        """Open an issue (WRITE)."""
        token = get_settings().get_secret("GITHUB_TOKEN")
        if not token:
            return {"ok": False, "error": "GitHub is not connected."}
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        try:
            made = self._post(f"/repos/{owner}/{repo}/issues", token, payload)
        except Exception as exc:
            return self._refusal(exc)
        return {"ok": True, "id": made.get("number"),
                "link": made.get("html_url"), "owner": owner, "repo": repo,
                "detail": f"Opened {owner}/{repo}#{made.get('number')}"}

    def issue_exists(self, owner: str, repo: str, number: int) -> dict:
        """Read one back, so "opened" is checked rather than assumed."""
        token = get_settings().get_secret("GITHUB_TOKEN")
        if not token:
            return {"verified": False}
        try:
            got = self._get(f"/repos/{owner}/{repo}/issues/{number}", token)
        except Exception:
            return {"verified": False}
        return {"verified": bool(got.get("number")),
                "at": got.get("created_at", "")}

    def sync(self, *, max_items: int = 150, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        token = get_settings().get_secret("GITHUB_TOKEN")
        if not token:
            result.errors.append("GitHub token not set")
            return self._finish(result)
        started = self.now()
        resume = since if since is not None else self.since(full_history=full_history)
        max_items = limit or max_items
        try:
            # issues + PRs involving the authenticated user, most-recent first
            params = {"filter": "involves", "state": "all", "sort": "updated",
                      "per_page": min(max_items, 100)}
            if resume:
                # GitHub's own "updated since" filter, so an unchanged backlog
                # is not re-downloaded on every background pass.
                params["since"] = resume
            q = urllib.parse.urlencode(params)
            items = self._get(f"/issues?{q}", token)
            from ..brain import get_brain
            brain = get_brain()

            def ingest(it) -> int:
                is_pr = "pull_request" in it
                repo = (it.get("repository", {}) or {}).get("full_name") or \
                    it.get("html_url", "").split("/issues")[0].split("/pull")[0]
                title = f"{repo}#{it.get('number', '?')}: {it.get('title', '(untitled)')}"
                text = (
                    f"GitHub {'PR' if is_pr else 'issue'} {title}\n"
                    f"State: {it.get('state', '?')}"
                    f" · Author: {(it.get('user') or {}).get('login', '?')}"
                    + (f"\n\n{it['body']}" if it.get("body") else "")
                )
                out = brain.ingest(text, source=self.name,
                                   kind="pr" if is_pr else "issue",
                                   title=title, fast=True, uri=it.get("html_url"))
                return out["memories"]

            self.each_guarded(items[:max_items], result, ingest,
                              cancel=cancel, progress=progress)
            result.detail = result.detail or f"{len(items)} issues/PRs"
            result.cursor = started
        except urllib.error.HTTPError as exc:
            result.errors.append(
                "invalid GitHub token" if exc.code in (401, 403)
                else f"GitHub API error {exc.code}")
            result.detail = "sync failed"
        except Exception as exc:
            result.errors.append(str(exc))
            result.detail = "sync failed"
        return self._finish(result)
