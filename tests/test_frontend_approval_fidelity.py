"""The approval queue shows what it is asking about.

An approval row was a summary line and three buttons:

    Email “Revised proposal” to rahul@work.test
    Waiting for your approval — rahul@work.test is not on your allowed list.
    [Approve] [Always allow rahul@work.test] [Dismiss]

So the user approved an email **without being shown what it said**. And this is
the one surface where that matters most: an approval exists because an agent
acted *unattended*, which means the text it wrote was composed after reading
something a stranger sent. The card in the user's own conversation — where they
asked for the email themselves, and are sitting right there — showed the whole
body and let them correct a typo in it. The weaker of the two consent surfaces
was guarding the riskier of the two paths.

Nothing was missing from the server. `approvals._public()` has always put the
full `params` on the same response the summary arrives in. What was missing was
a way to draw them: "what an action reads as" lived inside `actionCard` in
`chat.js`, eleven branches deep, and the approvals queue could not reach it. It
is `actionFace` now and both callers read it, so a new action is presented
properly on both surfaces by being added to the registry once.

The summary stays the headline: it is the server's own wording, shared with the
action log by rule, and it is what makes a queue scannable. The rows carry what
a summary structurally cannot — the words the agent actually wrote.

**Undo is deliberately not here.** An approved action is undoable from *What
just happened*, which is where the log's own note says Undo belongs afterwards:
"this is where it lives afterwards, which is when a person actually notices the
date was wrong". A button on a row that vanishes on the next poll would be a
control that cannot work.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

BODY = "Here is the revised version — I moved the timeline to March."

#: What a routine's outbound email looks like when it reaches the queue. The
#: shape is `approvals._public()`: the summary, the reason, who was blocked, and
#: the whole proposal in `params`.
EMAIL = {
    "id": "a1",
    "action_type": "send_email",
    "params": {"to": "rahul@work.test", "subject": "Revised proposal",
               "body": BODY, "attach": "/Users/x/Docs/deck.pdf"},
    "summary": "Email \u201cRevised proposal\u201d to rahul@work.test",
    "reason": ("Waiting for your approval — rahul@work.test is not on your "
               "allowed list."),
    "blocked": ["rahul@work.test"],
    "kind": "email_recipient",
    "routine_name": "Weekly report",
}

TRIAGE = {
    "id": "a2",
    "action_type": "mail_triage",
    "params": {"items": [
        {"id": "m1", "do": "archive", "subject": "Your receipt from Figma"},
        {"id": "m2", "do": "archive", "subject": "Standup notes"},
    ]},
    "summary": "Archive 2 emails",
    "reason": "Changing your inbox always needs your approval.",
    "blocked": [],
}

ROUTINE = {
    "id": "a3",
    "action_type": "create_routine",
    "params": {"name": "Morning brief", "trigger": "daily", "at": "8:00am",
               "days": "mon,tue,wed,thu,fri", "agent": "Chief of Staff",
               "instruction": "Summarise what came in overnight."},
    "summary": ("New automation \u201cMorning brief\u201d — "
                "weekdays at 8:00 AM"),
    "reason": "Creating automations always needs your approval.",
    "blocked": [],
}


def _catalog() -> dict:
    from chitragupta.actions import catalog

    return catalog()


def _run(rows, catalog=None):
    """Render the queue. `catalog` defaults to the registry's real one."""
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/allow_recipient.mjs"), str(WEB / "app.js")],
        input=json.dumps({"rows": rows,
                          "catalog": _catalog() if catalog is None else catalog}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


@pytest.fixture(scope="module")
def email_row():
    return _run([EMAIL])


# ── the bug ────────────────────────────────────────────────────────────────

def test_an_approval_shows_the_email_it_is_asking_about(email_row):
    """The whole finding. A person cannot consent to text they were not shown."""
    assert BODY in email_row["html"], email_row["html"]


def test_it_still_leads_with_the_summary(email_row):
    """The server's wording, shared with the action log — and what makes a queue
    of several readable at a glance."""
    assert EMAIL["summary"] in email_row["html"]


def test_it_names_the_attachment(email_row):
    """A file leaving the machine is half of what is being approved."""
    assert "deck.pdf" in email_row["html"]
    # By name, never by path — the same rule the card follows.
    assert "/Users/x/Docs" not in email_row["html"]


def test_it_says_what_the_action_reaches(email_row):
    """The tier, in the user's words. "amber" means nothing to a person."""
    assert "leaves your machine" in email_row["html"]


def test_it_says_what_kind_of_thing_it_is(email_row):
    """A queue of rows that look alike is a queue nobody reads to the end."""
    assert "ac-kind" in email_row["html"]
    assert "Email" in email_row["html"]


# ── the same rule, every action ─────────────────────────────────────────────

def test_an_inbox_approval_lists_the_emails_by_subject():
    """"Archive 2 emails" is the decision; *which* two is what the user is
    checking, and a label id is never what they read."""
    out = _run([TRIAGE])
    assert "Your receipt from Figma" in out["html"]
    assert "Standup notes" in out["html"]
    # And the reassurance the card gives, on the surface that needs it more.
    assert "Nothing is deleted" in out["html"]


def test_an_automation_approval_says_what_it_will_do_when_it_runs():
    """The instruction is the automation. A name and a schedule say when
    something will happen without saying what."""
    out = _run([ROUTINE])
    assert "Summarise what came in overnight." in out["html"]
    assert "8:00 AM" in out["html"]


def test_a_red_action_gives_the_reason_the_registry_wrote_for_it():
    out = _run([TRIAGE])
    assert "Changing your inbox always needs your approval." in out["html"]
    assert 'data-risk="red"' in out["html"]


# ── it must not have become worse at its old job ───────────────────────────

def test_the_buttons_are_still_there(email_row):
    assert "data-aprok" in email_row["html"]
    assert "data-aprno" in email_row["html"]
    assert "data-apralw" in email_row["html"], "the allow-list button vanished"


def test_a_row_the_catalog_knows_nothing_about_still_renders():
    """The catalog is filled at boot and the rows can arrive first. A queue that
    blanked because a fetch had not landed would be a worse bug than the one
    this fixes."""
    out = _run([EMAIL], catalog={})
    assert EMAIL["summary"] in out["html"]
    assert "data-aprok" in out["html"]


def test_a_row_with_no_parameters_at_all_still_renders():
    """`history()` rows and anything stored before this shipped. `actionFace` is
    handed whatever the row has, which may be nothing."""
    out = _run([{"id": "z", "summary": "Something waited", "reason": "why",
                 "blocked": []}])
    assert "Something waited" in out["html"]
    assert "data-aprok" in out["html"]


# ── the other half of the contract ─────────────────────────────────────────
#
# Every assertion above runs against a row this file wrote, so all of them would
# still pass if the server stopped sending the parameters. That is the failure
# mode `/CLAUDE.md` names for a change spanning two layers: two halves that each
# pass their own tests and are wrong about each other. This pins the half the
# frontend cannot see.

def test_the_queue_really_sends_what_the_row_draws(monkeypatch):
    """`pending()` carries the action type and the whole proposal.

    `_public()` has always done this and nothing read it, which is exactly how a
    field quietly stops being sent: no test was looking.
    """
    import chitragupta.notify as notify
    from chitragupta.agents import approvals

    # Queuing posts a desktop notification with a sound. Harmless in the app and
    # not something a test run should do to the machine it runs on.
    monkeypatch.setattr(notify, "desktop_notify", lambda *a, **k: False)

    params = {"to": "rahul@work.test", "subject": "Revised proposal",
              "body": BODY}
    approvals.queue("send_email", params,
                    reason="rahul@work.test is not on your allowed list.",
                    blocked=("rahul@work.test",), routine_name="Weekly report")

    row = next(r for r in approvals.pending()
               if r["summary"].startswith("Email"))
    # What `loadApprovals` reads, field by field. A missing one is a row that
    # silently goes back to being a summary line.
    assert row["action_type"] == "send_email"
    assert row["params"]["body"] == BODY, "the body is not on the wire"
    assert row["summary"] and row["reason"]
    assert row["blocked"] == ["rahul@work.test"]
    assert row["kind"] == "email_recipient"


def test_the_registry_is_what_both_surfaces_draw_from():
    """The card and the approval row both render from the catalog, so an action
    added to the registry is presented properly on both by being added once.
    That is the claim which makes one renderer worth having."""
    from chitragupta.actions import REGISTRY, catalog

    published = catalog()
    assert set(published) == set(REGISTRY)
    for name, spec in published.items():
        assert spec["fields"], f"{name} publishes no fields, so no row draws it"
        assert spec["risk"] in ("green", "amber", "red"), name
