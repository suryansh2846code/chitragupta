"""The whole loop, not just the doing — risk, verify, remember, undo, log.

An action used to stop at "the handler returned ok". Nothing read the result
back to check it landed, nothing told the brain it had happened, and nothing
could take it back. `docs/ACTION-COVERAGE.md` calls those rungs 5 and 6, and
`ActionSpec` is where they live now.

What these pin, in order of how much it would cost to get wrong:

* **The three tiers are derived from the registry, not restated.** They used to
  be three hand-maintained sets, and the whole point of the change is that an
  action cannot now be AMBER in one place and RED in another.
* **The gate fails closed on an action nobody declared.** It used to fall
  through to "reaches nobody" and run.
* **`mail_triage` is still RED.** It has an undo now, which is a reason to feel
  better about approving one — not a reason to stop asking. A test says so
  because the temptation to promote it is the whole reason it is written down.
* **Undo really inverts.** Archive removes INBOX, so undoing it adds INBOX.
* **Remember reaches the brain**, where every agent reads — not one agent's
  conversation, where only that agent does.
"""
from __future__ import annotations

import pytest

from chitragupta import action_log, actions
from chitragupta.actions import REGISTRY, Risk
from chitragupta.agents import permissions


@pytest.fixture(autouse=True)
def _fresh_log(tmp_path):
    """A log per test, so `(entry,) = recent()` means what it says — and no
    reminders or automations left behind.

    Points the log's own file at `tmp_path` rather than moving
    `settings.home` — every other store in the process is a cached singleton
    holding that directory, and repointing it mid-run leaves one of them bound
    to a path pytest is about to delete.

    Reminders and routines *do* live in the shared home, and these tests make
    plenty of both. `reminders.upcoming()` returns the twenty soonest, so ours
    can bury a row another module created and then went looking for. Snapshot
    and remove exactly what this test added; clearing the tables would delete
    something somebody else was relying on.
    """
    from chitragupta.reminders import get_reminders
    from chitragupta.routines import get_routines

    reminders, routines = get_reminders(), get_routines()
    had_reminders = {r["id"] for r in reminders.upcoming(limit=500)}
    had_routines = {r["id"] for r in routines.list()}

    action_log.reset_for_tests(tmp_path / "actions.db")
    yield
    action_log.reset_for_tests()

    for row in reminders.upcoming(limit=500):
        if row["id"] not in had_reminders:
            reminders.delete(row["id"])
    for row in routines.list():
        if row["id"] not in had_routines:
            routines.delete(row["id"])


# ── the tiers are one declaration ──────────────────────────────────────────

def test_every_action_declares_a_risk():
    """No action may exist without a tier. The gate reads this and nothing else."""
    for name, spec in REGISTRY.items():
        assert isinstance(spec.risk, Risk), f"{name} has no risk tier"


def test_the_permission_sets_are_derived_from_the_registry():
    """They used to be three hand-written literals in `permissions.py`.

    Goes red the moment someone adds an action to the registry and a set here
    stops agreeing with it — which is exactly the drift the change removed.
    """
    red = {n for n, s in REGISTRY.items() if s.risk is Risk.RED}
    amber = {n for n, s in REGISTRY.items() if s.risk is Risk.AMBER}
    kinds = {n: s.recipient_kind for n, s in REGISTRY.items() if s.recipient_kind}

    assert red == set(permissions.NEVER_UNATTENDED)
    assert amber == set(permissions.OUTBOUND_ACTIONS)
    assert kinds == dict(permissions.RECIPIENT_KINDS)


def test_the_tiers_still_hold_the_actions_they_held_before():
    """The refactor must not have moved anything. Written as literals on
    purpose: a test that derives its expectation the same way the code does
    would pass however wrong the code became."""
    never = frozenset({"create_routine", "mcp_action", "mail_triage"})
    outbound = frozenset({"send_email", "create_event", "message_send"})

    assert never == set(permissions.NEVER_UNATTENDED)
    assert outbound == set(permissions.OUTBOUND_ACTIONS)


def test_mail_triage_is_still_refused_unattended_despite_having_an_undo():
    """An undo is a reason to approve more comfortably, not to stop asking.

    A triage agent's whole input is text strangers sent, and "archive
    everything from the bank" is a sentence an email can contain. Promoting it
    needs its own argument and its own commit; this is here so the promotion
    cannot happen quietly.
    """
    assert REGISTRY["mail_triage"].risk is Risk.RED
    assert REGISTRY["mail_triage"].undo is not None
    assert not permissions.check("mail_triage", {"items": []}).allowed


def test_every_red_action_says_why_it_waits():
    """One sentence per action, not one for the set — a user told 'creating
    automations always needs your approval' about a Slack message learns
    nothing except that the app is confused."""
    for name, spec in REGISTRY.items():
        if spec.risk is Risk.RED:
            assert spec.always_ask_because, f"{name} refuses without saying why"
            assert permissions.check(name, {}).reason == spec.always_ask_because


def test_an_action_nobody_declared_is_refused_rather_than_allowed():
    """This used to fall through to `Verdict(True)`, so a typo in an action
    name read as "reaches nobody" and ran."""
    verdict = permissions.check("send_emial", {"to": "a@b.test"})
    assert not verdict.allowed


def test_green_runs_unattended_without_any_allow_list():
    assert permissions.check("set_reminder", {"message": "x"}).allowed
    assert REGISTRY["set_reminder"].risk is Risk.GREEN


# ── the log ────────────────────────────────────────────────────────────────

def test_every_action_lands_in_the_log_with_the_words_the_card_used():
    out = actions.run_now("set_reminder",
                          {"message": "call Rahul", "at": "tomorrow 10am"})
    assert out["ok"] and out["log_id"]

    (entry,) = action_log.recent()
    assert entry["action_type"] == "set_reminder"
    assert entry["risk"] == "green"
    # The same sentence as the approval card, not a second phrasing of it.
    from chitragupta.agents.approvals import describe
    assert entry["summary"] == describe("set_reminder", {"message": "call Rahul"})


def test_a_failed_action_is_logged_as_a_failure_not_dropped():
    out = actions.run_now("set_reminder", {"message": "", "at": "whenever"})
    assert not out["ok"]
    (entry,) = action_log.recent()
    assert entry["ok"] is False
    assert entry["detail"]


def test_the_log_survives_an_action_whose_summary_cannot_be_built(monkeypatch):
    """The audit trail must never be able to fail a send."""
    monkeypatch.setattr(action_log, "record",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    out = actions.run_now("set_reminder", {"message": "x", "at": "tomorrow 9am"})
    assert out["ok"], "a broken log took down the action it was only watching"


def test_summarise_counts_undone_separately_from_done():
    out = actions.run_now("set_reminder", {"message": "x", "at": "tomorrow 9am"})
    actions.undo(out["log_id"])
    counts = action_log.summarise()
    assert counts["undone"] == 1 and counts["done"] == 0


# ── undo ───────────────────────────────────────────────────────────────────

def test_undo_cancels_a_reminder_and_marks_the_entry():
    from chitragupta.reminders import get_reminders

    out = actions.run_now("set_reminder", {"message": "x", "at": "tomorrow 9am"})
    assert any(r["id"] == out["id"] for r in get_reminders().upcoming())

    assert actions.undo(out["log_id"])["ok"]
    assert not any(r["id"] == out["id"] for r in get_reminders().upcoming())
    assert action_log.get(out["log_id"])["undone"] is True


def test_undo_is_idempotent():
    out = actions.run_now("set_reminder", {"message": "x", "at": "tomorrow 9am"})
    assert actions.undo(out["log_id"])["ok"]
    again = actions.undo(out["log_id"])
    assert again["ok"] and "already" in again["detail"].lower()


def test_an_action_with_no_inverse_says_so_rather_than_pretending():
    """`send_email` has left the machine. The card must not offer a button that
    would quietly do nothing."""
    assert REGISTRY["send_email"].undo is None
    assert actions.catalog()["send_email"]["reversible"] is False


def test_undo_refuses_an_action_that_did_not_succeed():
    out = actions.run_now("set_reminder", {"message": "", "at": "nonsense"})
    assert not out["ok"]
    assert not actions.undo(out["log_id"])["ok"]


def test_undoing_triage_puts_every_label_back(monkeypatch):
    """The inverse is free, and this is why: a verb is an add/remove pair of
    Gmail labels, so its inverse is the same pair swapped. Nothing here has to
    know what archiving *means*."""
    calls = []

    class FakeGmail:
        def modify_messages(self, ids, add=None, remove=None):
            calls.append({"ids": sorted(ids), "add": list(add or []),
                          "remove": list(remove or [])})
            return {"ok": True, "count": len(ids)}

        def ensure_label(self, name):
            return {"ok": True, "id": "Label_9"}

    monkeypatch.setattr(actions, "_writer", lambda src, cap: FakeGmail())
    items = [{"id": "m1", "do": "archive"}, {"id": "m2", "do": "archive"},
             {"id": "m3", "do": "star"},
             {"id": "m4", "do": "label", "label": "Receipts"}]

    assert actions._undo_triage({"items": items}, {"ok": True})["ok"]
    by_ids = {tuple(c["ids"]): c for c in calls}
    # archive REMOVES inbox, so undoing it ADDS inbox back.
    assert by_ids[("m1", "m2")] == {"ids": ["m1", "m2"], "add": ["INBOX"],
                                    "remove": []}
    assert by_ids[("m3",)]["remove"] == ["STARRED"]
    # A label is taken off these messages — never deleted, since it may be in
    # use somewhere the user never asked us to touch.
    assert by_ids[("m4",)]["remove"] == ["Label_9"]


# ── remember ───────────────────────────────────────────────────────────────

def test_a_sent_email_is_remembered_in_the_brain_not_just_the_chat():
    """`outcomes.record()` writes to one agent's conversation. Ask a *different*
    agent next week and it had never heard of it."""
    from chitragupta.core.store import get_store

    actions._remember_email({"to": "rahul@work.test", "subject": "Proposal"},
                            {"ok": True, "at": "2026-09-19T15:42:00+05:30"})
    texts = [m.text for m in get_store().list(limit=20)]
    assert any("rahul@work.test" in t and "Proposal" in t for t in texts)

    stored = next(m for m in get_store().list(limit=20)
                  if "rahul@work.test" in m.text)
    assert stored.memory_type == "episodic", "an action is something that happened"
    assert stored.source == "action"


def test_an_open_loop_closes_only_when_the_action_named_it():
    """Matching by description was the obvious alternative and is the wrong
    one: "send Rahul the proposal" and "ask Rahul about the proposal" are one
    fuzzy match apart, and closing the wrong commitment is invisible."""
    from chitragupta.core.store import get_store

    store = get_store()
    loop = store.add_open_loop("Send Rahul the revised proposal")

    actions._remember_email({"to": "rahul@work.test", "subject": "Proposal"},
                            {"ok": True})
    assert store.get_open_loop(loop.id).status == "open", "closed without being named"

    actions._remember_email(
        {"to": "rahul@work.test", "subject": "Proposal", "loop_id": loop.id},
        {"ok": True})
    assert store.get_open_loop(loop.id).status == "completed"


# ── verify ─────────────────────────────────────────────────────────────────

def test_a_verified_send_says_when_rather_than_merely_that(monkeypatch):
    class FakeGmail:
        def send_email(self, to, subject, body, interactive=False):
            return {"ok": True, "id": "m99", "detail": f"Email sent to {to}"}

        def message_sent_at(self, message_id, interactive=False):
            return {"verified": True, "at": "2026-09-19T15:42:00+05:30"}

    monkeypatch.setattr(actions, "_writer", lambda src, cap: FakeGmail())
    out = actions.run_now("send_email",
                          {"to": "a@b.test", "subject": "Hi", "body": "there"})
    assert out["verified"] and out["verified_at"].startswith("2026-09-19")

    from chitragupta.agents.outcomes import describe
    line = describe("send_email", {"to": "a@b.test", "subject": "Hi"}, out)
    assert "confirmed at 3:42 PM" in line


def test_an_unverifiable_send_is_reported_as_unverified_never_as_failed(monkeypatch):
    """Telling somebody their email failed when it did not is the worse of the
    two errors by a long way."""
    class FakeGmail:
        def send_email(self, to, subject, body, interactive=False):
            return {"ok": True, "id": "m99", "detail": "Email sent"}

        def message_sent_at(self, message_id, interactive=False):
            raise RuntimeError("network")

    monkeypatch.setattr(actions, "_writer", lambda src, cap: FakeGmail())
    out = actions.run_now("send_email",
                          {"to": "a@b.test", "subject": "Hi", "body": "x"})
    assert out["ok"] is True
    assert not out.get("verified")


# ── the catalog the card renders from ──────────────────────────────────────

def test_the_catalog_publishes_every_action_with_its_fields():
    published = actions.catalog()
    assert set(published) == set(REGISTRY)
    for name, spec in REGISTRY.items():
        assert published[name]["fields"] == spec.fields
        assert published[name]["risk"] == spec.risk.value


def test_send_email_is_correctable_because_its_fields_are_published():
    """The frontend used to hardcode `EDITABLE = { log_workout: true }`, so the
    one action where a typo is worst could not be fixed before confirming."""
    fields = actions.catalog()["send_email"]["fields"]
    assert fields[:4] == ["to", "cc", "subject", "body"]
    # `attach` is published so the card can NAME the files, and excluded from
    # the typeable set in `chat.js` — a half-typed path is an attachment that
    # silently vanishes.
    assert "attach" in fields


def test_the_catalog_carries_no_callables_over_the_wire():
    import json

    json.dumps(actions.catalog())   # raises if a handler leaked into it
