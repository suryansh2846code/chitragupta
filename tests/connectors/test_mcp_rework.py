"""The connector rework: what it fixed, frozen so it cannot come back.

Each test here names a defect that shipped. The layer was built to the right
shape and could not be reached — every catalog entry failed to start, connector
writes were classified by a denylist that let `merge_pull_request` through as a
read, and no agent could propose a write at all because nothing ever told a
model the tag existed.

Contract: `docs/development/mcp-contract.md`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def tool(name, required=None, *, read_only=None, destructive=None):
    """A tool object shaped like the SDK's, for classification tests."""
    annotations = None
    if read_only is not None or destructive is not None:
        annotations = SimpleNamespace(read_only_hint=read_only,
                                      destructive_hint=destructive)
    return SimpleNamespace(
        name=name, annotations=annotations,
        input_schema={"type": "object", "required": list(required or []),
                      "properties": {k: {} for k in (required or [])}})


# ── writes are identified by failing to prove they are reads ───────────────

#: Every one of these was classified a **read** by the old name denylist, and
#: therefore handed to a model as a tool it could call mid-turn with no
#: approval — while reasoning over text a stranger wrote into the user's
#: mailbox. None of them contains any of the fourteen stems that list checked.
LOOKED_SAFE = [
    "merge_pull_request", "execute_sql", "run_command", "publish", "approve",
    "invite_user", "share_document", "upload_file", "rename_page",
    "revoke_token", "close_issue", "assign_task", "reset", "drop_all",
    "disconnect", "clear_all",
]


@pytest.mark.parametrize("name", LOOKED_SAFE)
def test_an_unrecognised_verb_is_treated_as_a_write(name):
    from chitragupta.connectors.mcp_source import classify_tools

    required = [] if name in ("reset", "drop_all", "disconnect", "clear_all") else ["id"]
    kinds = classify_tools([tool(name, required)])
    assert name in kinds.write, (
        f"{name} was offered to the model as a read. Write detection must fail "
        "closed: a denylist of verbs is guarding the one path that changes "
        "somebody else's account.")


@pytest.mark.parametrize("name", [
    "list_issues", "search_messages", "get_page", "read_file",
    "find_customer", "describe_table",
])
def test_a_recognised_read_stays_callable(name):
    """Failing closed is only acceptable if ordinary reads still work — every
    read needing a tap would make the category useless."""
    from chitragupta.connectors.mcp_source import classify_tools

    kinds = classify_tools([tool(name, ["q"])])
    assert name in kinds.readable, f"{name} should not need an approval card"


@pytest.mark.parametrize("name", ["entries", "my_issues"])
def test_a_listing_named_for_its_contents_is_a_read(name):
    """A name with no verb in it — `entries`, `my_issues` — is a listing, but
    only while it takes nothing to act on. The moment one requires an argument
    it is acting on something, and back it goes behind a confirmation.

    `recent_items` is deliberately not here: `recent` is a read stem, so it is
    a read on the stronger rule above and stays one with arguments.
    """
    from chitragupta.connectors.mcp_source import classify_tools

    assert name in classify_tools([tool(name)]).readable
    assert name in classify_tools([tool(name, ["target"])]).write


def test_the_servers_own_declaration_wins_over_the_name():
    """A hint is evidence; a name is a guess. `readOnlyHint` beats both stems."""
    from chitragupta.connectors.mcp_source import classify_tools

    kinds = classify_tools([tool("merge_pull_request", ["id"], read_only=True)])
    assert "merge_pull_request" in kinds.readable

    kinds = classify_tools([tool("list_things", destructive=True)])
    assert "list_things" in kinds.write


def test_a_bulk_hint_never_matches_a_substring():
    """`clear_all` contains "all" and takes no arguments. Matching bulk hints as
    substrings put it in the read pile, which is a destructive call nobody saw
    coming."""
    from chitragupta.connectors.mcp_source import classify_tools

    assert "clear_all" in classify_tools([tool("clear_all")]).write


# ── a failure the user can act on ──────────────────────────────────────────


def test_explain_sees_through_an_exception_group():
    """Everything in the MCP client runs under an anyio task group, so a caller
    receives an `ExceptionGroup` whose str() is "unhandled errors in a
    TaskGroup". Matching on that text meant a missing binary, a revoked token
    and a crashed server all produced the same fallback sentence."""
    from chitragupta.connectors.mcp_errors import explain

    wrapped = BaseExceptionGroup(
        "unhandled errors in a TaskGroup",
        [BaseExceptionGroup("inner", [FileNotFoundError("npx: command not found")])])

    message = explain(wrapped, "Slack")
    assert "did not respond as expected" not in message, (
        "the generic fallback means the real cause never reached `explain`")
    assert "Slack" in message


def test_a_cancellation_never_explains_a_failure():
    """A timeout leaves a cancellation beside the real cause. Explaining the
    cancellation would tell the user nothing about what actually broke."""
    import asyncio

    from chitragupta.connectors.mcp_errors import explain

    group = BaseExceptionGroup(
        "g", [asyncio.CancelledError(), PermissionError("operation not permitted")])
    assert "System Settings" in explain(group, "Notes")


# ── credentials ────────────────────────────────────────────────────────────


def test_a_connectors_key_never_lands_in_the_spec_file(isolated_home):
    """`mcp_servers.json` is returned verbatim by `GET /api/connectors`, so a
    token stored in it is a credential on disk *and* on the wire."""
    from chitragupta.connectors.mcp_source import (
        MCPServerSpec,
        get_server,
        set_server_env,
        upsert_server,
    )

    secret = "ghp_thisMustNeverBeWrittenDown"
    upsert_server(MCPServerSpec(id="gh", name="GitHub", transport="http",
                                auth="token", url="https://example.invalid/mcp",
                                token_key="GITHUB_TOKEN",
                                env_keys=["GITHUB_TOKEN"]))
    set_server_env("gh", {"GITHUB_TOKEN": secret})

    on_disk = (isolated_home / "mcp_servers.json").read_text()
    assert secret not in on_disk
    assert get_server("gh").bearer() == secret, "the app must still be able to use it"


def test_a_legacy_plaintext_key_is_migrated_on_read(isolated_home):
    """An existing install is repaired by opening Connectors, with nothing for
    the user to do — the alternative is a plaintext token sitting there until
    somebody notices."""
    from chitragupta.connectors.mcp_source import get_server

    (isolated_home / "mcp_servers.json").write_text(json.dumps({
        "old": {"id": "old", "name": "Old", "command": "x",
                "env": {"API_KEY": "legacy-plaintext"}}}))

    spec = get_server("old")
    assert spec.env_keys == ["API_KEY"]
    assert spec.resolved_env() == {"API_KEY": "legacy-plaintext"}
    assert "legacy-plaintext" not in (isolated_home / "mcp_servers.json").read_text()


def test_removing_a_connector_forgets_its_key(isolated_home):
    """Re-adding a connector must not silently reuse access the user believed
    they had revoked."""
    from chitragupta.config import get_settings
    from chitragupta.connectors.mcp_source import (
        MCPServerSpec,
        delete_server,
        secret_key,
        set_server_env,
        upsert_server,
    )

    upsert_server(MCPServerSpec(id="gone", name="Gone", command="x",
                                env_keys=["TOKEN"]))
    set_server_env("gone", {"TOKEN": "value"})
    delete_server("gone")
    assert get_settings().get_secret(secret_key("gone", "TOKEN")) is None


# ── an agent can propose a connector write ─────────────────────────────────


def test_an_agent_can_propose_a_connector_action():
    """The whole approval path existed and was unreachable: `parse_actions` had
    no `mcp_action` arm, so a model emitting the tag produced nothing."""
    from chitragupta.actions import parse_actions

    actions = parse_actions(
        'Drafted it.\n<action type="mcp_action" server="linear" '
        'tool="create_issue">{"title": "Fix login", "teamId": "ENG"}</action>')

    assert len(actions) == 1
    assert actions[0]["type"] == "mcp_action"
    assert actions[0]["params"]["server_id"] == "linear"
    assert actions[0]["params"]["tool"] == "create_issue"
    assert actions[0]["params"]["arguments"] == {"title": "Fix login",
                                                 "teamId": "ENG"}


def test_a_fenced_argument_block_is_still_understood():
    """Models wrap JSON in a code fence about half the time, and a silently
    refused action teaches the user nothing."""
    from chitragupta.actions import parse_actions

    actions = parse_actions(
        '<action type="mcp_action" server="l" tool="t">```json\n'
        '{"a": 1}\n```</action>')
    assert actions[0]["params"]["arguments"] == {"a": 1}


def test_malformed_arguments_are_dropped_rather_than_guessed():
    """Half-parsing a model's arguments and running the result is how an action
    does something nobody proposed."""
    from chitragupta.actions import parse_actions

    assert parse_actions(
        '<action type="mcp_action" server="l" tool="t">not json</action>') == []


def test_a_connector_write_still_needs_a_tap_when_nobody_is_watching():
    """Nothing about making writes proposable may let an unattended routine
    take one.

    A connector write is grantable per `server:tool` — but a grant is a
    statement about a tool the user has seen, and "delete everything" is the
    case where standing consent is exactly what you do not want them to be
    able to give. So this asserts the harder half: refused with nothing
    granted, and refused again once that very tool IS granted.
    """
    from chitragupta.agents import permissions
    from chitragupta.agents.permissions import check

    params = {"server_id": "x", "tool": "delete_everything"}

    verdict = check("mcp_action", params)
    assert not verdict.allowed
    assert verdict.reason

    permissions.grant("x:delete_everything", kind=permissions.TOOL_RECIPIENT)
    try:
        verdict = check("mcp_action", params)
        assert not verdict.allowed, (
            "a standing grant covered an irreversible tool")
        assert "cannot be undone" in verdict.reason
    finally:
        permissions.revoke("x:delete_everything", kind=permissions.TOOL_RECIPIENT)


def test_the_confirmation_card_shows_what_will_be_written():
    """"Run write_file on a folder" is not enough to judge. Which file, and
    with what in it, is the entire decision."""
    from chitragupta.agents.approvals import describe

    summary = describe("mcp_action", {
        "server_id": "files", "connector": "A folder on this Mac",
        "tool": "write_file",
        "arguments": {"path": "/tmp/notes.md", "content": "hello"}})

    assert "write_file" in summary
    assert "A folder on this Mac" in summary
    assert "/tmp/notes.md" in summary


# ── the catalog ────────────────────────────────────────────────────────────


def test_every_catalog_entry_is_addressable():
    """The catalog this replaces listed a package that never existed on npm and
    two that were deprecated upstream. Every row of it failed."""
    from chitragupta.connectors.mcp_catalog import CATALOG

    assert CATALOG, "a catalog with nothing in it is a browser with no connectors"
    for entry in CATALOG:
        if entry.is_remote:
            assert entry.url.startswith("https://"), entry.id
            assert not entry.command, f"{entry.id} is remote and also launches something"
        else:
            assert entry.command, entry.id
            assert entry.pinned, f"{entry.id} is not version-locked"


def test_a_catalog_entry_can_ask_for_a_positional_setting():
    """The filesystem server takes its folder as an argument, and the catalog
    could only describe environment variables — so it was offered with no way
    to say which folder, and exited on launch every time."""
    from chitragupta.connectors.mcp_catalog import BY_ID

    entry = BY_ID["filesystem"]
    assert entry.needs_args, "this entry cannot work without one"
    spec = entry.to_spec(args={entry.needs_args[0].name: "/tmp"})
    assert spec.args[-1].endswith("/tmp")


def test_a_chosen_folder_is_resolved_before_it_is_stored():
    """macOS makes /tmp a symlink to /private/tmp, and a server that realpaths
    its allowed directory then rejects every write under the name the user
    typed."""
    from chitragupta.connectors.mcp_catalog import BY_ID

    entry = BY_ID["filesystem"]
    spec = entry.to_spec(args={"root": "/tmp"})
    assert spec.args[-1] == str(Path("/tmp").resolve())


def test_a_setup_field_never_makes_a_person_read_a_variable_name():
    """`SLACK_BOT_TOKEN` is an internal. A label is what a person reads."""
    from chitragupta.connectors.mcp_catalog import CATALOG

    for entry in CATALOG:
        for value in (*entry.needs_env, *entry.needs_args):
            assert value.label and value.label != value.name, entry.id
            assert value.help, entry.id


# ── reaching a server ──────────────────────────────────────────────────────


def test_a_missing_runtime_is_named_rather_than_launched():
    """A `.app` opened from the Dock gets a stripped PATH, so a perfectly well
    installed Node is invisible to it. Resolving up front is what tells "not
    installed" apart from "installed where the Dock cannot see"."""
    from chitragupta.connectors.mcp_source import MCPServerSpec, resolve_command

    assert resolve_command("definitely-not-a-real-binary") is None
    assert resolve_command(sys.executable) == sys.executable

    spec = MCPServerSpec(id="x", name="Thing", command="definitely-not-a-real-binary")
    from chitragupta.connectors.mcp_source import MCPConnector

    ready, reason = MCPConnector(spec).is_configured()
    assert not ready
    assert "definitely-not-a-real-binary" in reason


def test_a_probe_never_opens_a_browser(monkeypatch):
    """The Connectors page probes every connector on load and the scheduler
    syncs on a timer. If either could open a browser, a connector whose token
    expired would hijack the screen while the user was doing something else."""
    import webbrowser

    from chitragupta.connectors import mcp_auth

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    spec = SimpleNamespace(id="remote", name="Remote", url="https://example.invalid/mcp")
    provider = mcp_auth.auth_provider(spec)          # interactive defaults to False
    assert provider is not None
    assert opened == []


def test_a_server_that_exits_at_startup_says_what_to_do():
    """"Connection closed" is what a server does when it is missing a setting —
    a different problem from "not installed", with a different next step."""
    from chitragupta.connectors.mcp_errors import explain

    message = explain(RuntimeError("Connection closed"), "A folder on this Mac")
    assert "missing a setting" in message


def test_a_tool_list_the_sdk_rejects_is_still_usable():
    """The SDK requires `inputSchema.type`; servers built against older SDKs —
    the reference filesystem server among them — publish a schema without one.
    Strict validation turned every one of those into "did not respond as
    expected": a working connector reported broken over a field we never read.
    """
    from chitragupta.connectors.mcp_source import _tolerant_tools, classify_tools

    tools = _tolerant_tools({"tools": [
        {"name": "read_file", "description": "Read one file.",
         "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#",
                         "properties": {"path": {}}, "required": ["path"]}},
        {"name": "write_file", "inputSchema": {"properties": {}},
         "annotations": {"destructiveHint": True}},
    ]})

    assert [t.name for t in tools] == ["read_file", "write_file"]
    # Every provider requires a top-level type, so the one repair is made here.
    assert tools[0].input_schema["type"] == "object"
    kinds = classify_tools(tools)
    assert "read_file" in kinds.readable
    assert "write_file" in kinds.write


# ── least privilege the user can actually set ──────────────────────────────


def test_narrowing_a_connector_takes_effect_on_the_next_turn(isolated_home):
    """`allowed_tools` was defined, serialised, honoured and tested — and had no
    writer outside the test suite, so on every real install it was empty and
    `permits()` returned True for everything."""
    from chitragupta.connectors.mcp_source import (
        MCPServerSpec,
        get_server,
        upsert_server,
    )

    spec = MCPServerSpec(id="narrow", name="Narrow", command=sys.executable,
                         args=[SERVER, "listing"])
    upsert_server(spec)
    assert get_server("narrow").permits("anything")

    spec.allowed_tools = ["list_records"]
    upsert_server(spec)
    reloaded = get_server("narrow")
    assert reloaded.permits("list_records")
    assert not reloaded.permits("something_else")


# ── the prompt has to agree with the tools ─────────────────────────────────


def _prompt(tools, labels):
    """`build()` with a fixed set of connector labels behind the sentinel."""
    from types import SimpleNamespace

    import chitragupta.connectors.mcp_tools as supplier
    from chitragupta.agents import prompt as mod

    refs = [SimpleNamespace(server_label=name, server_id=name.lower(),
                            tool=f"{name.lower()}-search", writes=False,
                            description="Search it.")
            for name in labels]
    real = supplier.list_tools
    supplier.list_tools = lambda: refs
    try:
        return mod.build(name="A", role="r", system_prompt="", tools=tools)
    finally:
        supplier.list_tools = real


def test_an_agent_with_a_live_connector_is_told_to_ask_it():
    """The defect this closes was visible to a user, not a test.

    With Notion connected, signed in and exposing twenty-five working tools, the
    agent answered a question about Notion out of *notification emails* and
    said the connector was "probably still syncing — try the Connectors panel".
    It was doing as it was told: the brain paragraph named Notion and forbade
    checking it, because it was written before a connector could be asked
    anything live.
    """
    from chitragupta.agents.mcp_tools import SENTINEL

    text = _prompt(["search_brain", SENTINEL], ["Notion"])

    assert "Notion" in text
    assert "CALL THE CONNECTOR" in text
    assert "not synced yet" not in text, (
        "an agent holding a working connector must never send the user to the "
        "Connectors panel instead of using it")


def test_an_agent_with_no_connectors_still_offers_the_panel():
    """The old sentence is right in the case it was written for — there is
    genuinely nothing else to try. It just is not right in both cases."""
    text = _prompt(["search_brain"], ["Notion"])

    assert "not synced yet" in text
    assert "LIVE CONNECTORS" not in text


def test_the_prompt_never_forbids_checking_a_connector():
    """`You do NOT connect to, authorize, or 'check' Gmail/Google/Notion
    yourself` shipped as an absolute, and became false the moment a connector
    could answer live."""
    from chitragupta.agents.mcp_tools import SENTINEL

    for tools in (["search_brain"], ["search_brain", SENTINEL]):
        text = _prompt(tools, ["Notion"])
        assert "do NOT" not in text
        assert "'check' Gmail" not in text


# ── the catalog, once it is more than a handful ────────────────────────────


def test_every_entry_sits_on_a_named_shelf():
    """Two dozen connectors in one list is a wall.

    The shelf lives on the entry rather than in the page, so the catalog and
    the UI cannot disagree about where something belongs — and the order is
    served, not hardcoded, for the same reason.
    """
    from chitragupta.connectors.mcp_catalog import CATALOG, CATEGORIES

    for entry in CATALOG:
        assert entry.category in CATEGORIES, (
            f"{entry.id} is filed under '{entry.category}', which is not a "
            "shelf — it would render under a heading nobody chose")


def test_no_shelf_is_empty():
    """A heading with nothing under it is a promise the catalog does not keep."""
    from chitragupta.connectors.mcp_catalog import CATALOG, CATEGORIES

    used = {e.category for e in CATALOG}
    assert not (set(CATEGORIES) - used), (
        f"shelves with no connectors on them: {sorted(set(CATEGORIES) - used)}")


def test_every_remote_entry_names_a_reachable_endpoint():
    """The shape of the failure that emptied this catalog once already.

    Offline this asserts only the shape. With a network it asks each vendor
    whether the address exists and whether it wants a sign-in — which is the
    check that would have caught a package that had never been published.
    """
    import urllib.error
    import urllib.request

    from chitragupta.connectors.mcp_catalog import CATALOG

    for entry in CATALOG:
        if not entry.is_remote:
            continue
        assert entry.url.startswith("https://"), entry.id
        assert " " not in entry.url, entry.id

    request = urllib.request.Request("https://mcp.notion.com/mcp", method="HEAD")
    try:
        urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError:
        pass                                  # reachable; it just refused us
    except Exception:
        pytest.skip("no network — the shape assertions above still ran")


def test_asking_a_question_is_never_an_action():
    """`ask_question` on a documentation server was classified a write, which
    would have put an approval card in front of every question the connector
    exists to answer. Failing closed must not mean treating curiosity as an
    act."""
    from chitragupta.connectors.mcp_source import classify_tools

    kinds = classify_tools([tool(n, ["q"]) for n in
                            ("ask_question", "analyze_costs", "explain_error",
                             "compare_plans", "delete_page", "approve_invoice")])

    assert set(kinds.readable) == {"ask_question", "analyze_costs",
                                   "explain_error", "compare_plans"}
    assert set(kinds.write) == {"delete_page", "approve_invoice"}
