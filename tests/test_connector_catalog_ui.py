"""The connector catalog's render paths, executed rather than parsed.

`node --check` passes on a temporal-dead-zone `ReferenceError`, and that is
exactly what once blanked the whole Models drawer. Source-order assertions miss
the detached-container bug. So these run the real functions through
`tests/js/connector_catalog.mjs` and read what actually landed on screen.

The backend half of this feature is covered in `tests/connectors/`; what is new
here is the surface a user actually touches, which had no coverage at all and is
the reason every MCP connector built so far was invisible.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = ROOT / "chitragupta" / "web" / "app.js"
HARNESS = ROOT / "tests" / "js" / "connector_catalog.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

CATALOG = {
    "available": [
        {"id": "slack", "name": "Slack", "notes": "A bot only sees channels it "
         "has been invited to.", "first_party": True, "added": False,
         "needs_env": [{"name": "SLACK_BOT_TOKEN", "help": "A Slack bot token."}]},
        {"id": "github", "name": "GitHub", "notes": "", "first_party": True,
         "added": True, "needs_env": []},
    ],
    "blocked": [
        {"id": "linkedin", "name": "LinkedIn",
         "reason": "LinkedIn does not allow apps to read your feed, connections "
                   "or messages — its terms ban automated access, and the tools "
                   "that claim to do it can get your account banned."},
    ],
}


def run(scenario: dict) -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        input=json.dumps(scenario), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


# ── the harness must be able to fail ───────────────────────────────────────


def test_the_harness_reports_a_thrown_error():
    """A harness that cannot fail proves nothing. This checks it notices when
    the render path throws, which is the whole reason it exists."""
    out = run({"mode": "catalog",
               "api": {"/api/connectors/catalog": {"__throw": "boom"}}})

    # The path catches its own fetch failure and says so on screen rather than
    # dying — so the evidence is in the text it wrote, not in a crash.
    assert out["ok"]
    assert any("boom" in w["text"] for w in out["textWrites"]), out["textWrites"]


# ── the browser ────────────────────────────────────────────────────────────


def test_a_reply_missing_its_list_does_not_take_the_screen_down():
    """The catalog is a section of the Connectors page now, not a modal.

    A modal that throws takes down something the user deliberately opened. A
    section that throws takes down the sources they already have — the half of
    that screen which must render whatever else is wrong. This was a real
    crash the moment the list moved: `data.available.map` on a reply that had
    no `available`, which killed the whole page.
    """
    out = run({"mode": "catalog", "api": {"/api/connectors/catalog": {}}})

    assert out["ok"], out["error"]
    assert out["addButtons"] == []


def test_the_catalog_renders_every_available_connector():
    out = run({"mode": "catalog", "api": {"/api/connectors/catalog": CATALOG}})

    assert out["ok"], out["error"]
    assert "Slack" in out["catalogHtml"]
    assert "GitHub" in out["catalogHtml"]


def test_an_already_added_connector_cannot_be_added_twice():
    out = run({"mode": "catalog", "api": {"/api/connectors/catalog": CATALOG}})

    by_id = {b["id"]: b for b in out["addButtons"]}
    assert by_id["github"]["disabled"] is True
    assert by_id["slack"]["disabled"] is False


def test_a_blocked_source_is_shown_with_its_reason():
    """Omitting LinkedIn teaches the user the app is missing a feature. The
    honest version says no app can do it, where the control would have been."""
    out = run({"mode": "catalog", "api": {"/api/connectors/catalog": CATALOG}})

    assert "LinkedIn" in out["catalogHtml"]
    assert "banned" in out["catalogHtml"]
    assert "unavailable" in out["catalogHtml"]


def test_a_blocked_source_has_no_add_button():
    out = run({"mode": "catalog", "api": {"/api/connectors/catalog": CATALOG}})

    assert "linkedin" not in [b["id"] for b in out["addButtons"]]


def test_the_acronym_never_reaches_the_user():
    """To the user these are connectors. "MCP" is an internal, and CLAUDE.md's
    rule against surfacing one applies to a protocol name as much as to a
    stack trace."""
    out = run({"mode": "catalog", "api": {"/api/connectors/catalog": CATALOG}})

    assert "MCP" not in out["catalogHtml"]
    assert "mcp" not in out["catalogHtml"].lower().replace("data-cx", "")


# ── the permission step ────────────────────────────────────────────────────

# The setup form's fields carry a `label` and a `kind` as well as a name: the
# name is an environment variable and a person should never have to read one,
# and `kind` is what decides whether the field is masked. Both arrived with the
# connector rework — see `docs/development/mcp-contract.md`.
PERMS = {
    "id": "slack", "name": "Slack", "available": True, "reason": "",
    "first_party": True, "notes": "", "can_sync": True, "needs_auth": False,
    "remote": False, "auth": "token",
    "needs_env": [{"name": "SLACK_BOT_TOKEN", "label": "Bot token",
                   "help": "A Slack bot token.", "kind": "secret"}],
    "needs_args": [],
    "reads": ["list_channels", "read_messages"],
    "writes": ["post_message"],
}


def test_reads_and_writes_are_shown_before_adding():
    """Consent to something nobody has been shown is not consent."""
    out = run({"mode": "permissions", "entry": "slack",
               "api": {"/api/connectors/catalog/slack/permissions": PERMS}})

    assert out["ok"], out["error"]
    assert "list_channels" in out["permHtml"]
    assert "post_message" in out["permHtml"]
    assert "Read" in out["permHtml"] and "Change" in out["permHtml"]


def test_a_connector_that_can_change_things_says_it_will_ask_first():
    out = run({"mode": "permissions", "entry": "slack",
               "api": {"/api/connectors/catalog/slack/permissions": PERMS}})

    assert "asks you first" in out["permHtml"]


def test_a_read_only_connector_says_so_plainly():
    read_only = {**PERMS, "writes": []}
    out = run({"mode": "permissions", "entry": "slack",
               "api": {"/api/connectors/catalog/slack/permissions": read_only}})

    assert "read-only" in out["permHtml"]
    assert "asks you first" not in out["permHtml"]


def test_required_credentials_are_asked_for_with_an_explanation():
    out = run({"mode": "permissions", "entry": "slack",
               "api": {"/api/connectors/catalog/slack/permissions": PERMS}})

    assert "SLACK_BOT_TOKEN" in out["permHtml"]
    assert "A Slack bot token." in out["permHtml"]
    assert 'type="password"' in out["permHtml"]


def test_a_search_only_connector_explains_why_it_will_not_sync():
    """The honest failure mode: it can answer questions but cannot enumerate,
    and saying nothing would look like a connector that silently does nothing."""
    search_only = {**PERMS, "can_sync": False}
    out = run({"mode": "permissions", "entry": "slack",
               "api": {"/api/connectors/catalog/slack/permissions": search_only}})

    assert "searched" in out["permHtml"] and "on demand" in out["permHtml"]


def test_an_unavailable_connector_shows_its_reason_not_a_form():
    unavailable = {"id": "x", "available": False,
                   "reason": "That connector needs a runtime that is not installed."}
    out = run({"mode": "permissions", "entry": "x",
               "api": {"/api/connectors/catalog/x/permissions": unavailable}})

    assert "is not installed" in out["permHtml"]
    assert "Add connector" not in out["permHtml"]


def test_a_reason_is_escaped_before_it_reaches_the_page():
    """Reasons come from a server's own output, so they are untrusted text.

    Every other `innerHTML` path in this file goes through `esc()`; this is the
    one that carries a third party's words, and it must not be the exception.
    """
    out = run({"mode": "permissions", "entry": "x",
               "api": {"/api/connectors/catalog/x/permissions": {
                   "id": "x", "available": False,
                   "reason": "<img src=x onerror=alert(1)> broke"}}})

    assert "<img" not in out["permHtml"]
    assert "&lt;img" in out["permHtml"]


# ── the approvals list ─────────────────────────────────────────────────────
# The queue, the notification and the endpoints existed before this; nowhere to
# look did not. A queue nobody can see is not an approval system — it is a
# desktop notification and then silence.

WAITING = {"approvals": [
    {"id": "a1", "summary": "Run “send_message” on Slack",
     "reason": "Anything a connector changes needs your approval.",
     "routine_name": "Morning inbox", "status": "pending"},
    {"id": "a2", "summary": "Email “Re: launch” to sam@example.com",
     "reason": "sam@example.com is not on your allowed list.",
     "routine_name": "", "status": "pending"},
]}


def test_waiting_actions_are_listed_with_their_reason():
    out = run({"mode": "approvals", "api": {"/api/agents/approvals": WAITING}})

    assert out["ok"], out["error"]
    assert "send_message" in out["approvalsHtml"]
    assert "needs your approval" in out["approvalsHtml"]
    assert "not on your allowed list" in out["approvalsHtml"]


def test_each_waiting_action_can_be_approved_or_dismissed():
    out = run({"mode": "approvals", "api": {"/api/agents/approvals": WAITING}})

    assert out["approveButtons"] == ["a1", "a2"]
    assert "Approve" in out["approvalsHtml"]
    assert "Dismiss" in out["approvalsHtml"]


def test_where_a_request_came_from_is_shown():
    """A routine acting on its own is the case this list exists for, so the
    user needs to know which one asked."""
    out = run({"mode": "approvals", "api": {"/api/agents/approvals": WAITING}})

    assert "Morning inbox" in out["approvalsHtml"]


def test_an_empty_queue_shows_nothing_at_all():
    """Not "0 waiting" — an empty state that takes up room trains people to
    stop reading the area it sits in."""
    out = run({"mode": "approvals", "api": {"/api/agents/approvals": {"approvals": []}}})

    assert out["approvalsHidden"] is True
    assert out["approvalsHtml"] == ""


def test_a_failed_poll_does_not_blank_a_live_list():
    """The list is refreshed on a timer. One failed request must not erase
    requests the user can still act on."""
    out = run({"mode": "approvals",
               "api": {"/api/agents/approvals": {"__throw": "offline"}}})

    assert out["ok"], out["error"]
    assert out["approvalsHtml"] == ""


def test_a_summary_is_escaped():
    """Summaries carry a tool name and a connector label, both of which come
    from somebody else's server."""
    out = run({"mode": "approvals", "api": {"/api/agents/approvals": {"approvals": [
        {"id": "x", "summary": "<img src=x onerror=alert(1)>", "reason": "",
         "routine_name": "", "status": "pending"}]}}})

    assert "<img" not in out["approvalsHtml"]
    assert "&lt;img" in out["approvalsHtml"]


# ── connectors the vendor signs you in to ──────────────────────────────────
# The remote half of the rework. There is nothing to list before consent
# exists, so the screen has to say what happens next instead of showing an
# empty tool list that reads as "this connector does nothing".

OAUTH_PERMS = {
    "id": "linear", "name": "Linear", "available": True, "reason": "",
    "first_party": True, "notes": "Issues, projects and cycles.",
    "can_sync": False, "needs_auth": True, "remote": True, "auth": "oauth",
    "needs_env": [], "needs_args": [], "reads": [], "writes": [],
}


def test_a_vendor_signin_says_where_you_are_going():
    out = run({"mode": "permissions", "entry": "linear",
               "api": {"/api/connectors/catalog/linear/permissions": OAUTH_PERMS}})

    assert out["ok"], out["error"]
    assert "Linear" in out["permHtml"]
    assert "sign in" in out["permHtml"].lower()
    # The button says what it does. "Add connector" would be a lie: the next
    # thing that happens is a browser, not a connector.
    assert "Connect" in out["permHtml"]


def test_a_vendor_signin_promises_writes_will_ask_first():
    """The tools are unknown before consent, so the promise has to be made
    without them — a connector that can change things must say so somewhere."""
    out = run({"mode": "permissions", "entry": "linear",
               "api": {"/api/connectors/catalog/linear/permissions": OAUTH_PERMS}})

    assert "asks you first" in out["permHtml"]


def test_a_vendor_signin_never_claims_to_know_the_tools_yet():
    out = run({"mode": "permissions", "entry": "linear",
               "api": {"/api/connectors/catalog/linear/permissions": OAUTH_PERMS}})

    # An empty "Read: nothing" list would be a false statement about a
    # connector that reads plenty — we simply have not asked it yet.
    assert "nothing" not in out["permHtml"].lower()


SETTING_PERMS = {
    "id": "filesystem", "name": "A folder on this Mac", "available": True,
    "reason": "", "first_party": True, "notes": "Reads a folder you choose.",
    "can_sync": True, "needs_auth": False, "remote": False, "auth": "none",
    "needs_env": [],
    "needs_args": [{"name": "root", "label": "Folder",
                    "help": "The folder this connector may read.",
                    "kind": "path"}],
    "reads": [], "writes": [],
}


def test_a_positional_setting_is_asked_for():
    """The catalog could describe environment variables and nothing else, so the
    filesystem server — which needs a folder as an argument — was offered with
    no way to say which folder and exited on launch every time."""
    out = run({"mode": "permissions", "entry": "filesystem",
               "api": {"/api/connectors/catalog/filesystem/permissions": SETTING_PERMS}})

    assert out["ok"], out["error"]
    assert "Folder" in out["permHtml"]
    assert "The folder this connector may read." in out["permHtml"]
    # A path is not a secret: masking it would hide what the user just typed.
    assert 'type="text"' in out["permHtml"]
