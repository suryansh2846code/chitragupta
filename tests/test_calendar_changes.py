"""Moving and cancelling a meeting — jobs 6 and 7.

`gcal.py` had exactly one write method. An agent could put a meeting in the
calendar and could not move it, so *"shift the client call to Friday"* got a
second event beside the first — which is how a diary ends up with two of
everything.

Four things carry this, and three of them are about not doing damage:

* **A patch, never a replace.** An event carries a Meet link, recurrence and
  reminders that nobody named on the card. Writing a body built from the four
  things the user mentioned drops the rest.
* **Attendees are only touched when named.** `attendees=[]` because nobody
  mentioned them uninvites the meeting, delivered as a cancellation to
  everyone on it.
* **Moving keeps the length.** "Move it to Friday afternoon" is about when it
  begins. Reading it as "and end when it used to end" makes a one-hour call
  three days long, or negative.
* **Both are RED.** The people a move reaches are on the *existing event*, not
  in the params — so `recipients_of` would find none, and an amber tier would
  read that as "reaches nobody".
"""
from __future__ import annotations

import pytest

from chitragupta import action_log, actions
from chitragupta.actions import REGISTRY, Risk
from chitragupta.agents import permissions
from chitragupta.agents.approvals import describe
from chitragupta.connectors.gcal import _shift_end

BEFORE = {
    "id": "e1", "summary": "Client call",
    "start": {"dateTime": "2026-09-24T15:00:00+05:30"},
    "end": {"dateTime": "2026-09-24T16:00:00+05:30"},
    "attendees": [{"email": "rahul@work.test"}, {"email": "priya@work.test"}],
    "hangoutLink": "https://meet.google.com/abc-defg-hij",
    "recurringEventId": "r1",
}


@pytest.fixture(autouse=True)
def _fresh_log(tmp_path):
    action_log.reset_for_tests(tmp_path / "actions.db")
    yield
    action_log.reset_for_tests()


class FakeCal:
    """Records the patch body, the way Google would receive it."""

    def __init__(self, before=None, fail=""):
        self.before = BEFORE if before is None else before
        self.patched: list[dict] = []
        self.cancelled: list[str] = []
        self.notified: list[str] = []
        self._fail = fail

    def update_event(self, event_id, **kw):
        if self._fail:
            return {"ok": False, "error": self._fail}
        body = {}
        if kw.get("title"):
            body["summary"] = kw["title"]
        if kw.get("location") is not None:
            body["location"] = kw["location"]
        if kw.get("attendees") is not None:
            body["attendees"] = kw["attendees"]
        if kw.get("start"):
            body["start"] = {"dateTime": kw["start"]}
            body["end"] = {"dateTime": kw.get("end")
                           or _shift_end(self.before, kw["start"])}
        self.patched.append(body)
        return {"ok": True, "id": event_id, "before": self.before,
                "detail": "“Client call” updated"}

    def restore_event(self, event_id, before):
        self.patched.append({"restored": sorted(before)})
        return {"ok": True, "detail": "Put the event back as it was"}

    def cancel_event(self, event_id):
        self.cancelled.append(event_id)
        return {"ok": True, "id": event_id,
                "detail": "“Client call” cancelled — 2 attendee(s) told"}

    def event_exists(self, event_id):
        return {"verified": True, "at": "2026-09-25T15:00:00+05:30"}


@pytest.fixture
def cal(monkeypatch):
    def install(**kw):
        fake = FakeCal(**kw)
        monkeypatch.setattr(actions, "_writer", lambda src, cap: fake)
        return fake
    return install


# ── the tier ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["update_event", "cancel_event"])
def test_changing_a_meeting_always_waits_for_a_tap(kind):
    """The people it reaches are on the existing event, not in the params, so
    `recipients_of` would find none — and an amber tier would read that as
    "reaches nobody" and let an unattended agent rearrange a calendar full of
    other people's mornings."""
    assert REGISTRY[kind].risk is Risk.RED
    assert kind in permissions.NEVER_UNATTENDED
    verdict = permissions.check(kind, {"event_id": "e1", "start": "x"})
    assert not verdict.allowed
    assert "everybody in it" in verdict.reason


def test_creating_a_meeting_is_still_amber():
    """Its attendees ARE on the card, so an allow-list has something to read."""
    assert REGISTRY["create_event"].risk is Risk.AMBER


# ── keeping the meeting the length it was ──────────────────────────────────

def test_moving_the_start_moves_the_end_with_it():
    assert _shift_end(BEFORE, "2026-09-25T14:30:00+05:30") == \
        "2026-09-25T15:30:00+05:30"


def test_a_two_hour_meeting_stays_two_hours():
    longer = {**BEFORE, "end": {"dateTime": "2026-09-24T17:00:00+05:30"}}
    assert _shift_end(longer, "2026-09-25T09:00:00+05:30") == \
        "2026-09-25T11:00:00+05:30"


@pytest.mark.parametrize("before", [{}, {"start": {}, "end": {}},
                                    {"start": {"date": "2026-09-24"}}])
def test_an_event_with_no_readable_pair_falls_back_to_an_hour(before):
    """An all-day event, or a stored value we cannot parse."""
    assert _shift_end(before, "2026-09-25T14:30:00+05:30") == \
        "2026-09-25T15:30:00+05:30"


def test_an_unreadable_new_start_is_handed_back_untouched():
    """Better Google's own error than a guess built on top of one."""
    assert _shift_end(BEFORE, "friday afternoon") == "friday afternoon"


def test_an_explicit_end_is_respected(cal):
    fake = cal()
    actions.run_now("update_event", {
        "event_id": "e1", "start": "2026-09-25T09:00:00+05:30",
        "end": "2026-09-25T09:15:00+05:30"})
    assert fake.patched[0]["end"]["dateTime"] == "2026-09-25T09:15:00+05:30"


# ── not touching what nobody named ─────────────────────────────────────────

def test_a_move_does_not_mention_attendees_at_all(cal):
    """`attendees: []` because nobody said otherwise uninvites the meeting."""
    fake = cal()
    actions.run_now("update_event", {"event_id": "e1",
                                     "start": "2026-09-25T09:00:00+05:30"})
    assert "attendees" not in fake.patched[0]


def test_attendees_are_changed_only_when_given(cal):
    fake = cal()
    actions.run_now("update_event", {"event_id": "e1",
                                     "attendees": "a@x.test, b@x.test"})
    assert fake.patched[0]["attendees"] == ["a@x.test", "b@x.test"]


def test_a_move_does_not_rename_or_relocate(cal):
    fake = cal()
    actions.run_now("update_event", {"event_id": "e1",
                                     "start": "2026-09-25T09:00:00+05:30"})
    assert "summary" not in fake.patched[0]
    assert "location" not in fake.patched[0]


def test_an_empty_attendee_string_is_not_an_instruction_to_uninvite():
    assert actions._attendees_of({"attendees": ""}) is None
    assert actions._attendees_of({}) is None


# ── the real connector, against a fake Google ──────────────────────────────
#
# Everything above this line goes through `FakeCal`, which stands in for the
# connector — so it proves the ACTION layer and says nothing about whether
# `update_event` is wired up correctly inside. It was not caught: replacing
# `_shift_end(before, start)` with the old finish time passed all 36 of them.
#
# So these drive the real `GoogleCalendarConnector` and fake only Google.


class FakeEvents:
    def __init__(self, store):
        self.store = store
        self.calls: list[tuple[str, dict]] = []

    def _exec(self, name, kwargs):
        """Google's builder shape: `.events().patch(...).execute()`."""
        self.calls.append((name, kwargs))
        store = self.store

        def execute():
            if name == "get":
                return dict(store)
            if name == "patch":
                store.update(kwargs["body"])
                return dict(store)
            return {}

        return type("Request", (), {"execute": staticmethod(execute)})()

    def get(self, **kw):
        return self._exec("get", kw)

    def patch(self, **kw):
        return self._exec("patch", kw)

    def delete(self, **kw):
        return self._exec("delete", kw)


class FakeService:
    def __init__(self, store):
        self.ev = FakeEvents(store)

    def events(self):
        return self.ev


@pytest.fixture
def connector(monkeypatch):
    from chitragupta.connectors.gcal import GoogleCalendarConnector

    def build(before=None):
        store = dict(BEFORE if before is None else before)
        conn = GoogleCalendarConnector()
        service = FakeService(store)
        monkeypatch.setattr(conn, "_client", lambda interactive=False: (service, ""))
        return conn, service
    return build


def _body(service):
    return next(kw["body"] for name, kw in service.ev.calls if name == "patch")


def test_the_connector_really_keeps_the_meeting_the_same_length(connector):
    """The wiring, not the helper. A one-hour call moved to 09:00 must end at
    10:00 — not at 16:00, which is what keeping the old finish time gives."""
    conn, service = connector()
    out = conn.update_event("e1", start="2026-09-25T09:00:00+05:30")
    assert out["ok"]
    assert _body(service)["end"]["dateTime"] == "2026-09-25T10:00:00+05:30"


def test_the_connector_sends_a_patch_not_a_replace(connector):
    """The event carries a Meet link, recurrence and reminders nobody named.
    A body built from what the user mentioned would drop all of it."""
    conn, service = connector()
    conn.update_event("e1", start="2026-09-25T09:00:00+05:30")
    name, _ = service.ev.calls[-1]
    assert name == "patch"
    assert set(_body(service)) == {"start", "end"}
    # and the fields nobody touched are still on the event afterwards
    assert service.ev.store["hangoutLink"]
    assert service.ev.store["recurringEventId"] == "r1"


def test_the_connector_tells_the_attendees(connector):
    """Google's default is to tell nobody, and a meeting silently moved leaves
    everyone holding the old slot."""
    conn, service = connector()
    conn.update_event("e1", start="2026-09-25T09:00:00+05:30")
    assert service.ev.calls[-1][1]["sendUpdates"] == "all"


def test_a_cancellation_tells_the_attendees_too(connector):
    conn, service = connector()
    assert conn.cancel_event("e1")["ok"]
    name, kw = service.ev.calls[-1]
    assert name == "delete" and kw["sendUpdates"] == "all"


def test_undoing_a_create_deliberately_tells_nobody(connector):
    """`delete_event` exists to take back a `create_event` the attendees have
    not seen. Notifying there would be the app announcing a meeting that never
    happened."""
    conn, service = connector()
    assert conn.delete_event("e1")["ok"]
    name, kw = service.ev.calls[-1]
    assert name == "delete" and "sendUpdates" not in kw


def test_the_connector_refuses_a_change_to_an_event_that_is_gone(connector):
    conn, _ = connector(before={})
    out = conn.update_event("e1", start="2026-09-25T09:00:00+05:30")
    assert not out["ok"] and "no longer there" in out["error"]


def test_the_connector_refuses_a_change_that_changes_nothing(connector):
    conn, _ = connector()
    assert not conn.update_event("e1")["ok"]


def test_restore_puts_back_only_what_it_was_given(connector):
    conn, service = connector()
    before = dict(service.ev.store)
    conn.update_event("e1", start="2026-09-25T09:00:00+05:30")
    assert conn.restore_event("e1", before)["ok"]
    body = service.ev.calls[-1][1]["body"]
    assert set(body) <= {"summary", "description", "location", "start", "end",
                         "attendees"}
    assert body["start"]["dateTime"] == "2026-09-24T15:00:00+05:30"


# ── addressing the right meeting ───────────────────────────────────────────

@pytest.mark.parametrize("kind", ["update_event", "cancel_event"])
def test_it_refuses_to_act_without_an_id(cal, kind):
    """The wrong id moves somebody else's meeting, and they find out from the
    invitation."""
    cal()
    out = actions.run_now(kind, {})
    assert not out["ok"]
    assert "calendar_lookup" in out["error"]


def test_the_lookup_hands_the_agent_the_id_it_needs():
    """The sync has stored `event_id` since it was written and the tool never
    showed it — so an agent asked to move a meeting could describe it and not
    address it."""
    import inspect

    from chitragupta.agents import source_tools

    body = inspect.getsource(source_tools.calendar_lookup)
    assert "event_id" in body
    assert "Never guess an id" in body


# ── undo ───────────────────────────────────────────────────────────────────

def test_a_move_can_be_put_back(cal):
    """Reversible because the handler kept the other side of the diff."""
    fake = cal()
    out = actions.run_now("update_event", {"event_id": "e1",
                                           "start": "2026-09-25T09:00:00+05:30"})
    assert out["reversible"] and out["undo_label"] == "Put it back"
    assert actions.undo(out["log_id"])["ok"]
    assert "restored" in fake.patched[-1]


def test_putting_it_back_carries_what_was_there_before(cal):
    fake = cal()
    out = actions.run_now("update_event", {"event_id": "e1",
                                           "start": "2026-09-25T09:00:00+05:30"})
    actions.undo(out["log_id"])
    restored = fake.patched[-1]["restored"]
    assert "start" in restored and "end" in restored and "attendees" in restored


def test_a_cancellation_offers_no_undo(cal):
    """Recreating it is a NEW invitation to people already told it was off —
    not the same event, and not an undo."""
    cal()
    assert REGISTRY["cancel_event"].undo is None
    out = actions.run_now("cancel_event", {"event_id": "e1"})
    assert out["ok"] and not out["reversible"]
    assert not actions.undo(out["log_id"])["ok"]


def test_a_failed_move_is_not_offered_for_undo(cal):
    cal(fail="Calendar refused that")
    out = actions.run_now("update_event", {"event_id": "e1", "start": "x"})
    assert not out["ok"] and not out["reversible"]


# ── what the user approves ─────────────────────────────────────────────────

def test_the_card_says_what_is_changing_not_that_something_is():
    """A card reading "Change an event" asks the user to approve a diff they
    were not shown, and the thing being approved emails the whole meeting."""
    assert describe("update_event", {"event_id": "e1", "start": "Fri 2pm"}) == (
        "Meeting — move it to Fri 2pm (everybody in it is told)")


def test_the_card_names_a_rename_and_a_move_together():
    line = describe("update_event", {"event_id": "e1", "title": "Client sync",
                                     "start": "Fri 2pm"})
    assert "move it to Fri 2pm" in line and "Client sync" in line


def test_the_card_says_when_people_are_being_added_or_removed():
    assert "change who is coming" in describe(
        "update_event", {"event_id": "e1", "attendees": "a@x.test"})


def test_the_cancellation_card_says_everybody_is_told():
    assert describe("cancel_event", {"event_id": "e1"}) == (
        "Cancel a meeting — everybody in it is told")


# ── verify and remember ────────────────────────────────────────────────────

def test_a_move_is_verified_and_recorded(cal):
    from chitragupta.core.store import get_store

    cal()
    out = actions.run_now("update_event", {"event_id": "e1",
                                           "start": "2026-09-25T09:00:00+05:30"})
    assert out["verified"]
    texts = [m.text for m in get_store().list(limit=20)]
    assert any("Client call" in t and "Moved" in t for t in texts)


# ── who is taught the job ──────────────────────────────────────────────────

def test_an_agent_that_can_see_and_change_the_calendar_gets_the_recipe():
    from chitragupta.agents.prompt import _calendar_recipe

    assert _calendar_recipe(["calendar_lookup"], ["update_event"])


@pytest.mark.parametrize("tools,acts", [
    ([], ["update_event"]),                    # cannot see the calendar
    (["calendar_lookup"], ["create_event"]),   # cannot change one
    (["calendar_lookup"], []),
])
def test_an_agent_missing_a_half_is_not_taught_it(tools, acts):
    from chitragupta.agents.prompt import _calendar_recipe

    assert _calendar_recipe(tools, acts) == ""


def test_the_recipe_asks_for_a_concrete_slot_not_a_question_back():
    """An agent that answers "what time on Friday works for you?" has handed
    the job back — the user asked to have it moved."""
    from chitragupta.agents.prompt import _CALENDAR_RECIPE

    assert "PICK a concrete slot" in _CALENDAR_RECIPE
    assert "handing the job back" in _CALENDAR_RECIPE


def test_the_recipe_forbids_guessing_an_id():
    from chitragupta.agents.prompt import _CALENDAR_RECIPE

    assert "Never guess" in _CALENDAR_RECIPE


def test_the_prompt_warns_that_a_bare_attendee_list_uninvites_people():
    from chitragupta.agents.prompt import _BLOCKS

    assert "uninvites" in _BLOCKS["update_event"]


def test_the_prompt_says_cancelling_cannot_be_undone():
    from chitragupta.agents.prompt import _BLOCKS

    assert "cannot be undone" in _BLOCKS["cancel_event"]


def test_an_agent_that_can_create_can_also_move_and_cancel():
    """One that can put a meeting in the diary and cannot move it proposes a
    new one beside the old."""
    from chitragupta.agents.library import TEMPLATES

    for template in TEMPLATES:
        if "create_event" in template.actions:
            assert "update_event" in template.actions, template.id
            assert "cancel_event" in template.actions, template.id


# ── the scorecard ──────────────────────────────────────────────────────────

def test_moving_a_meeting_is_on_the_scorecard():
    from chitragupta.agents import evaluation

    card = evaluation.run(include_slow=False)
    failed = [c.detail for c in card.checks
              if c.key.startswith("calendar_") and not c.passed]
    assert not failed, failed
    assert {"calendar_move", "calendar_move_id", "calendar_move_concrete",
            "calendar_move_gate"} <= {c.key for c in card.checks}
