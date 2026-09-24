"""Writes happen only after the user has seen and approved them.

Connectors have been read-only by design since decision C1. This is the first
path that changes something in the user's account, so the gate is the point of
the feature rather than a wrapper around it — and it fails **closed**: a caller
that forgets to pass confirmation gets a refusal, not an action.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from chitragupta.api.app import app
from chitragupta.connectors.mcp_source import MCPConnector, MCPServerSpec, upsert_server

SERVER = str(Path(__file__).parent / "fake_mcp_server.py")
client = TestClient(app)


def spec_for(mode: str, **over) -> MCPServerSpec:
    return MCPServerSpec(id=over.pop("id", mode), name=f"{mode.title()} Source",
                         command=sys.executable, args=[SERVER, mode], **over)


# ── 4.1 — the gate ─────────────────────────────────────────────────────────


def test_an_unconfirmed_write_is_refused():
    conn = MCPConnector(spec_for("writes"))

    out = conn.perform("delete_record", {"record_id": "1"})

    assert out["ok"] is False
    assert "confirm" in out["error"].lower()


def test_confirmation_is_required_explicitly_not_by_default():
    """`confirmed` defaults to False, so a caller that forgets it fails closed.

    Getting this backwards means every future call site is one missing keyword
    away from changing the user's account without asking.
    """
    import inspect

    signature = inspect.signature(MCPConnector.perform)

    assert signature.parameters["confirmed"].default is False
    assert signature.parameters["confirmed"].kind is inspect.Parameter.KEYWORD_ONLY


def test_a_confirmed_write_runs():
    conn = MCPConnector(spec_for("writes"))

    out = conn.perform("delete_record", {"record_id": "7"}, confirmed=True)

    assert out["ok"] is True
    assert "7" in str(out["detail"])


def test_a_write_outside_the_allow_list_is_refused_even_when_confirmed():
    """Confirmation approves *an* action; it does not widen what the connector
    was permitted to do in the first place."""
    conn = MCPConnector(spec_for("writes", allowed_tools=["list_records"]))

    out = conn.perform("delete_record", {"record_id": "1"}, confirmed=True)

    assert out["ok"] is False
    assert "not allowed" in out["error"]


def test_a_tool_the_server_does_not_have_is_refused():
    conn = MCPConnector(spec_for("writes"))

    out = conn.perform("launch_missiles", {}, confirmed=True)

    assert out["ok"] is False
    assert "no `launch_missiles`" in out["error"]


def test_a_failing_action_explains_itself():
    conn = MCPConnector(spec_for("crash"))

    out = conn.perform("anything", {}, confirmed=True)

    assert out["ok"] is False
    assert "Traceback" not in out["error"]
    assert "vendor database" not in out["error"], "raw stderr reached the user"


# ── 4.2 — the user can see what would change ───────────────────────────────


def test_actions_are_listed_without_being_run():
    """The list a confirmation card is built from. Asking for it must not
    change anything — that is the whole difference between showing and doing."""
    conn = MCPConnector(spec_for("writes"))

    actions = conn.available_actions()

    names = {a["tool"] for a in actions}
    assert names == {"delete_record", "send_message"}
    assert all(a["label"] == "Writes Source" for a in actions)


def test_a_read_only_connector_offers_no_actions():
    assert MCPConnector(spec_for("listing")).available_actions() == []


def test_the_allow_list_narrows_what_is_offered():
    conn = MCPConnector(spec_for("writes", allowed_tools=["send_message"]))

    assert [a["tool"] for a in conn.available_actions()] == ["send_message"]


def test_a_sync_never_calls_a_write(poison):
    """The guarantee underneath all of this: routine background work must not
    be able to reach an action, confirmed or otherwise."""
    called: list[str] = []
    conn = MCPConnector(spec_for("writes"))
    real = MCPConnector.perform
    MCPConnector.perform = lambda self, tool, *a, **k: called.append(tool)
    try:
        conn.sync(interactive=False)
    finally:
        MCPConnector.perform = real

    assert called == []


# ── the HTTP surface ───────────────────────────────────────────────────────


def test_the_endpoint_queues_an_unconfirmed_action():
    """Unconfirmed means *waiting*, not *refused*.

    Refusing was the weaker half of two approval systems: a routine that wanted
    to act while nobody was watching simply failed and forgot. Queuing puts it
    in the same list, with the same notification, as every other pending action.
    """
    from chitragupta.agents import approvals

    upsert_server(spec_for("writes", id="http"))

    resp = client.post("/api/connectors/mcp/http/action",
                       json={"tool": "delete_record", "arguments": {"record_id": "1"}})

    body = resp.json()
    assert resp.status_code == 200
    assert body["queued"] is True
    waiting = approvals.pending()
    assert any(a["id"] == body["approval_id"] for a in waiting)


def test_a_queued_connector_action_is_described_in_the_users_terms():
    """The summary is what the approval list shows, so it must not be an
    internal — and it must not borrow another action's explanation."""
    from chitragupta.agents import approvals

    upsert_server(spec_for("writes", id="http"))
    client.post("/api/connectors/mcp/http/action",
                json={"tool": "send_message", "arguments": {"to": "dev"}})

    row = approvals.pending()[0]
    assert "send_message" in row["summary"]
    assert "Writes Source" in row["summary"]
    assert "automation" not in row["reason"], (
        "a connector action borrowed create_routine's reason")
    assert "connector tool" in row["reason"].lower(), (
        "the reason must say what kind of thing was refused — `http:send_message` "
        "on its own reads like a typo, not like something to approve")
    # **Which one — in words, not as the key.** This asserted the literal
    # `http:send_message` until grants learned to name what a tool reaches;
    # the key can now carry a scope (`github:add_issue_comment@acme/api`) and
    # printing it raw is the internal this project refuses to surface. Both
    # halves still have to be there, and the id form now has to be absent —
    # which is a stronger assertion than the one it replaces, not a weaker one.
    assert "send_message" in row["reason"], "and which tool"
    assert "http" in row["reason"], "and which connector"
    assert "http:send_message" not in row["reason"], (
        "the grant key is an id and must not reach the user verbatim")


def test_approving_later_runs_what_was_proposed():
    """The whole reason to queue rather than refuse: the action survives until
    the user gets back, and runs with the parameters it was proposed with."""
    from chitragupta.agents import approvals

    upsert_server(spec_for("writes", id="http"))
    queued = client.post("/api/connectors/mcp/http/action",
                         json={"tool": "send_message",
                               "arguments": {"to": "dev", "body": "ship it"}}).json()

    out = approvals.approve(queued["approval_id"])

    assert out["ok"] is True
    assert not approvals.pending(), "the approved action is still waiting"


def test_rejecting_never_runs_it():
    from chitragupta.agents import approvals

    upsert_server(spec_for("writes", id="http"))
    queued = client.post("/api/connectors/mcp/http/action",
                         json={"tool": "delete_record",
                               "arguments": {"record_id": "9"}}).json()

    out = approvals.reject(queued["approval_id"])

    assert out["ok"] is True
    assert not approvals.pending()


def test_there_is_one_approval_system_not_two():
    """`mcp_action` is a registered action like any other, so it inherits the
    queue, the notification, the history and the approval list rather than
    carrying a private confirmation flag of its own."""
    from chitragupta.actions import REGISTRY
    from chitragupta.agents.permissions import (
        RECIPIENT_KINDS,
        TOOL_RECIPIENT,
        check,
    )

    assert "mcp_action" in REGISTRY
    # It is refused by the same `check()` every other action goes through,
    # against the same allow-list table, keyed by the same recipient machinery.
    assert RECIPIENT_KINDS["mcp_action"] == TOOL_RECIPIENT
    assert not check("mcp_action", {"server_id": "l", "tool": "create_issue"}).allowed


def test_the_endpoint_runs_a_confirmed_action():
    upsert_server(spec_for("writes", id="http"))

    resp = client.post("/api/connectors/mcp/http/action",
                       json={"tool": "send_message", "confirmed": True,
                             "arguments": {"to": "dev", "body": "ship it"}})

    assert resp.json()["ok"] is True


def test_the_endpoint_lists_actions():
    upsert_server(spec_for("writes", id="http"))

    resp = client.get("/api/connectors/mcp/http/actions")

    assert {a["tool"] for a in resp.json()["actions"]} == {"delete_record",
                                                          "send_message"}


def test_an_unknown_connector_is_a_clean_404():
    resp = client.post("/api/connectors/mcp/never-set-up/action",
                       json={"tool": "x", "confirmed": True})

    assert resp.status_code == 404
    assert "not set up" in resp.json()["detail"]
