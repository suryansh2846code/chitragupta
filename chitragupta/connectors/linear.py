"""Linear connector — ingests your issues via the Linear GraphQL API.

Single-token auth: a Linear personal API key (Settings → API → Personal keys).
Declared as a `secret_field`, so the UI renders an in-app field — no .env.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..config import get_settings
from .base import Connector, SyncResult

API = "https://api.linear.app/graphql"
QUERY = """
{ issues(first: %d, orderBy: updatedAt) { nodes {
    identifier title description
    state { name } priorityLabel
    team { name } assignee { name } updatedAt
} } }
"""


class LinearConnector(Connector):
    name = "linear"
    label = "Linear"
    auto_sync = True          # see GitHub — same silent omission
    incremental = True
    secret_field = {
        "key": "LINEAR_API_KEY",
        "label": "Linear personal API key",
        "placeholder": "lin_api_…",
        "help_url": "https://linear.app/settings/api",
        "steps": [
            "Open Linear → <b>Settings → API → Personal API keys</b> → "
            "<b>Create key</b>, then copy it.",
            "Paste it below and click Save.",
        ],
    }

    def is_configured(self) -> tuple[bool, str]:
        if get_settings().get_secret("LINEAR_API_KEY"):
            return True, ""
        return False, "click setup to paste your Linear API key"

    def sync(self, *, max_issues: int = 200, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        key = get_settings().get_secret("LINEAR_API_KEY")
        if not key:
            result.errors.append("Linear API key not set")
            return self._finish(result)
        started = self.now()
        max_issues = limit or max_issues
        try:
            req = urllib.request.Request(
                API, data=(QUERY % max_issues).encode(),
                headers={"Authorization": key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            nodes = (data.get("data") or {}).get("issues", {}).get("nodes", [])
            from ..brain import get_brain
            brain = get_brain()

            def ingest(it) -> int:
                ident = it.get("identifier") or "?"
                title = f"{ident}: {it.get('title', '(untitled)')}"
                text = (
                    f"Linear issue {title}\n"
                    f"Status: {(it.get('state') or {}).get('name', '?')}"
                    f" · Priority: {it.get('priorityLabel') or '—'}"
                    f" · Team: {(it.get('team') or {}).get('name', '?')}"
                    f" · Assignee: {(it.get('assignee') or {}).get('name', 'unassigned')}"
                    + (f"\n\n{it['description']}" if it.get("description") else "")
                )
                out = brain.ingest(text, source=self.name, kind="issue",
                                   title=title, fast=True,
                                   uri=f"https://linear.app/issue/{ident}")
                return out["memories"]

            self.each_guarded(nodes, result, ingest,
                              cancel=cancel, progress=progress)
            result.detail = result.detail or f"{len(nodes)} issues"
            result.cursor = started
        except urllib.error.HTTPError as exc:
            result.errors.append(
                "invalid Linear API key" if exc.code in (400, 401)
                else f"Linear API error {exc.code}")
            result.detail = "sync failed"
        except Exception as exc:
            result.errors.append(str(exc))
            result.detail = "sync failed"
        return self._finish(result)

    # ── writes ───────────────────────────────────────────────────────────
    #
    # Named methods, never part of `sync()` — sync runs on a timer, so
    # anything it could change would change without anyone asking. Each of
    # these is reachable only through a confirmed action in `actions.py`.

    def _call(self, query: str, variables: dict) -> dict:
        """One GraphQL request, with Linear's "no" turned into a sentence.

        GraphQL answers **200 and puts the failure in the body**, so a caller
        checking the status code alone reads every refusal as success — which
        is how an agent ends up reporting an issue it did not file.
        """
        key = get_settings().get_secret("LINEAR_API_KEY")
        if not key:
            return {"ok": False, "error": "Linear is not connected."}
        try:
            req = urllib.request.Request(
                API,
                data=json.dumps({"query": query, "variables": variables}).encode(),
                headers={"Authorization": key,
                         "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403):
                return {"ok": False, "reauth": True, "error":
                        "Linear refused that — the API key is missing or has "
                        "no access. Paste a fresh one under Connectors."}
            return {"ok": False, "error": f"Linear API error {exc.code}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

        errors = body.get("errors") or []
        if errors:
            said = str((errors[0] or {}).get("message") or "")[:160]
            return {"ok": False, "error": f"Linear refused that — {said}"}
        return {"ok": True, "data": body.get("data") or {}}

    def _team_id(self, team: str = "") -> tuple[str, str]:
        """(team id, problem). The named team, or the only one there is.

        Guessing a team puts the issue in front of the wrong people, so with
        several teams and no name this refuses and says which exist rather
        than picking the first.
        """
        found = self._call("{ teams(first: 50) { nodes { id name key } } }", {})
        if not found.get("ok"):
            return "", str(found.get("error") or "")
        nodes = ((found["data"].get("teams") or {}).get("nodes")) or []
        if not nodes:
            return "", "That Linear account has no teams I can file into."
        wanted = (team or "").strip().lower()
        if wanted:
            for node in nodes:
                if wanted in (str(node.get("name") or "").lower(),
                              str(node.get("key") or "").lower()):
                    return str(node.get("id") or ""), ""
            names = ", ".join(str(n.get("name") or "") for n in nodes[:8])
            return "", f"There is no Linear team called “{team}”. There is: {names}."
        if len(nodes) == 1:
            return str(nodes[0].get("id") or ""), ""
        names = ", ".join(str(n.get("name") or "") for n in nodes[:8])
        return "", f"Which Linear team? There is: {names}."

    def _issue_id(self, issue: str) -> tuple[str, str]:
        """(uuid, problem) for an identifier like `ENG-12`.

        Every mutation wants the uuid; what a person reads and says out loud
        is the identifier. The lookup lives here rather than being something
        each caller has to remember.
        """
        wanted = (issue or "").strip()
        if not wanted:
            return "", "Which issue?"
        found = self._call(
            "query($id: String!) { issue(id: $id) { id identifier } }",
            {"id": wanted})
        if found.get("ok"):
            node = found["data"].get("issue") or {}
            if node.get("id"):
                return str(node["id"]), ""
        return "", f"There is no Linear issue {wanted} that this key can see."

    def create_issue(self, title: str, description: str = "",
                     team: str = "") -> dict:
        """File an issue (WRITE)."""
        title = (title or "").strip()
        if not title:
            return {"ok": False, "error": "An issue needs a title."}
        team_id, problem = self._team_id(team)
        if problem:
            return {"ok": False, "error": problem}

        made = self._call(
            "mutation($input: IssueCreateInput!) { issueCreate(input: $input) "
            "{ success issue { id identifier url title } } }",
            {"input": {"teamId": team_id, "title": title,
                       "description": description or ""}})
        if not made.get("ok"):
            return made
        payload = made["data"].get("issueCreate") or {}
        issue = payload.get("issue") or {}
        if not payload.get("success") or not issue.get("id"):
            return {"ok": False, "error": "Linear did not create that issue."}
        return {"ok": True, "id": str(issue["id"]),
                "identifier": str(issue.get("identifier") or ""),
                "url": str(issue.get("url") or ""),
                "detail": f"Filed {issue.get('identifier') or 'the issue'}"}

    def comment(self, issue: str, body: str) -> dict:
        """Comment on an issue (WRITE). `issue` is an identifier like ENG-12."""
        body = (body or "").strip()
        if not body:
            return {"ok": False, "error": "There is nothing to say."}
        issue_id, problem = self._issue_id(issue)
        if problem:
            return {"ok": False, "error": problem}

        made = self._call(
            "mutation($input: CommentCreateInput!) { commentCreate(input: $input) "
            "{ success comment { id url } } }",
            {"input": {"issueId": issue_id, "body": body}})
        if not made.get("ok"):
            return made
        payload = made["data"].get("commentCreate") or {}
        comment = payload.get("comment") or {}
        if not payload.get("success") or not comment.get("id"):
            return {"ok": False, "error": "Linear did not post that comment."}
        return {"ok": True, "id": str(comment["id"]),
                "url": str(comment.get("url") or ""),
                "detail": f"Commented on {issue}"}

    def delete_comment(self, comment_id: str) -> dict:
        """Remove a comment we posted (WRITE) — the inverse of `comment`."""
        if not comment_id:
            return {"ok": False, "error": "That comment cannot be found."}
        made = self._call(
            "mutation($id: String!) { commentDelete(id: $id) { success } }",
            {"id": comment_id})
        if not made.get("ok"):
            return made
        return {"ok": True, "detail": "Comment removed"}

    def issue_exists(self, issue_id: str) -> dict:
        """Read one back, for rung 5."""
        if not issue_id:
            return {"ok": False}
        found = self._call(
            "query($id: String!) { issue(id: $id) { id identifier url } }",
            {"id": issue_id})
        if not found.get("ok"):
            return {"ok": False}
        node = found["data"].get("issue") or {}
        return {"ok": bool(node.get("id")), "url": str(node.get("url") or ""),
                "identifier": str(node.get("identifier") or "")}

    def _lookup_assignee(self, who: str) -> tuple[str, str]:
        """(user id, problem) for a name or email.

        Refuses on ambiguity rather than picking: assigning somebody else's
        work to the wrong person is quiet, and the wrong person finds out
        before the user does.
        """
        wanted = (who or "").strip().lower()
        if not wanted:
            return "", ""
        found = self._call(
            "{ users(first: 100) { nodes { id name displayName email } } }", {})
        if not found.get("ok"):
            return "", str(found.get("error") or "")
        nodes = ((found["data"].get("users") or {}).get("nodes")) or []
        hits = [n for n in nodes
                if wanted in (str(n.get("email") or "").lower(),
                              str(n.get("name") or "").lower(),
                              str(n.get("displayName") or "").lower())]
        if len(hits) == 1:
            return str(hits[0].get("id") or ""), ""
        if not hits:
            return "", f"There is no Linear user called “{who}”."
        names = ", ".join(str(n.get("email") or n.get("name")) for n in hits[:5])
        return "", f"More than one Linear user matches “{who}”: {names}."

    def _lookup_state(self, issue_id: str, state: str) -> tuple[str, str]:
        """(state id, problem) for a status name, within the issue's own team.

        States are per team — one team's "Done" is a different row from
        another's — so this is scoped to the issue rather than searched
        globally, which would move an issue into a state its board cannot show.
        """
        wanted = (state or "").strip().lower()
        if not wanted:
            return "", ""
        found = self._call(
            "query($id: String!) { issue(id: $id) { team { states(first: 50) "
            "{ nodes { id name } } } } }", {"id": issue_id})
        if not found.get("ok"):
            return "", str(found.get("error") or "")
        team = (found["data"].get("issue") or {}).get("team") or {}
        nodes = ((team.get("states") or {}).get("nodes")) or []
        for node in nodes:
            if str(node.get("name") or "").lower() == wanted:
                return str(node.get("id") or ""), ""
        names = ", ".join(str(n.get("name") or "") for n in nodes[:10])
        return "", f"That board has no status called “{state}”. It has: {names}."

    def issue_state(self, issue: str) -> dict:
        """Who it is assigned to and what status it is in, right now.

        Read BEFORE an update so the undo has the other side of the diff —
        the same discipline `update_event` uses, and for the same reason: an
        undo that guesses the previous value is not an undo.
        """
        found = self._call(
            "query($id: String!) { issue(id: $id) { id identifier url "
            "assignee { id name } state { id name } } }",
            {"id": (issue or "").strip()})
        if not found.get("ok"):
            return {"ok": False, "error": str(found.get("error") or "")}
        node = found["data"].get("issue") or {}
        if not node.get("id"):
            return {"ok": False, "error": f"There is no Linear issue {issue}."}
        return {"ok": True, "id": str(node["id"]),
                "identifier": str(node.get("identifier") or ""),
                "url": str(node.get("url") or ""),
                "assignee_id": str((node.get("assignee") or {}).get("id") or ""),
                "assignee": str((node.get("assignee") or {}).get("name") or ""),
                "state_id": str((node.get("state") or {}).get("id") or ""),
                "state": str((node.get("state") or {}).get("name") or "")}

    def update_issue(self, issue: str, *, assignee: str = "",
                     state: str = "") -> dict:
        """Assign an issue, move it, or both (WRITE).

        **A patch, never a replace.** Only the fields named are sent, so an
        issue keeps its labels, estimate, project and everything else nobody
        mentioned — the `update_event` lesson, which cost a Meet link the
        first time it was learned here.
        """
        before = self.issue_state(issue)
        if not before.get("ok"):
            return before

        changes: dict = {}
        if assignee:
            assignee_id, problem = self._lookup_assignee(assignee)
            if problem:
                return {"ok": False, "error": problem}
            changes["assigneeId"] = assignee_id
        if state:
            state_id, problem = self._lookup_state(before["id"], state)
            if problem:
                return {"ok": False, "error": problem}
            changes["stateId"] = state_id
        if not changes:
            return {"ok": False, "error":
                    "Say what to change — who it is assigned to, or its status."}

        made = self._call(
            "mutation($id: String!, $input: IssueUpdateInput!) "
            "{ issueUpdate(id: $id, input: $input) { success } }",
            {"id": before["id"], "input": changes})
        if not made.get("ok"):
            return made
        if not (made["data"].get("issueUpdate") or {}).get("success"):
            return {"ok": False, "error": "Linear did not apply that change."}

        said = []
        if assignee:
            said.append(f"assigned to {assignee}")
        if state:
            said.append(f"moved to {state}")
        return {"ok": True, "id": before["id"],
                "identifier": before["identifier"], "url": before["url"],
                # The other side of the diff, so `undo` restores rather than
                # guesses.
                "before": {"assignee_id": before["assignee_id"],
                           "assignee": before["assignee"],
                           "state_id": before["state_id"],
                           "state": before["state"]},
                "detail": f"{before['identifier']} — {' and '.join(said)}"}

    def restore_issue(self, issue_id: str, before: dict) -> dict:
        """Put an issue back the way `update_issue` found it (WRITE)."""
        if not issue_id or not before:
            return {"ok": False, "error": "There is nothing to put back."}
        changes = {}
        if before.get("assignee_id") is not None:
            changes["assigneeId"] = before.get("assignee_id") or None
        if before.get("state_id"):
            changes["stateId"] = before["state_id"]
        if not changes:
            return {"ok": False, "error": "There is nothing to put back."}

        made = self._call(
            "mutation($id: String!, $input: IssueUpdateInput!) "
            "{ issueUpdate(id: $id, input: $input) { success } }",
            {"id": issue_id, "input": changes})
        if not made.get("ok"):
            return made
        was = before.get("state") or "how it was"
        return {"ok": True, "detail": f"Put it back to {was}"}
