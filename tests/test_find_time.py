"""“Find a time with Rahul next week” — job 8.

Rung 3 for the calendar is proposing a *specific* slot. An agent that answers
*"what time next week suits you?"* has handed the job back.

**The claim this feature must never make is that a time works for somebody
whose calendar it cannot see.** Google answers an unreadable calendar with an
empty `busy` list and an `errors` entry beside it — so anything reading only
`busy` sees a person with a completely clear week. Calendar sharing is normal
inside one Workspace domain and rare outside it, which makes the unreadable
case the *common* one for exactly the people a user needs to arrange something
with. "Rahul is free Tuesday" when nobody can see Rahul's diary is a sentence
the user gets embarrassed by.

The rest is arithmetic, and the arithmetic has two traps: a gap between two
overlapping meetings is not free, and a gap at 03:00 is free and is not a time
to offer anybody.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from chitragupta.agents import meeting_tools as mt
from chitragupta.core.dateparse import parse_date_range


def _local(day_offset: int, hour: int, minute: int = 0) -> str:
    base = datetime.now().astimezone().replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return (base + timedelta(days=day_offset)).isoformat()


def _next_weekday(offset: int = 1) -> int:
    """Days from today to the next weekday at least `offset` away."""
    today = datetime.now().astimezone()
    for extra in range(offset, offset + 7):
        if (today + timedelta(days=extra)).weekday() in mt.WORK_DAYS:
            return extra
    raise AssertionError("a week with no weekdays in it")


class FakeCal:
    def __init__(self, busy=None, unreadable=()):
        self.busy = busy or {}
        self.unreadable = list(unreadable)
        self.asked: list[list[str]] = []

    def is_configured(self):
        return True, ""

    def free_busy(self, emails, start, end, interactive=False):
        self.asked.append(list(emails))
        return {"busy": dict(self.busy), "unreadable": list(self.unreadable)}


@pytest.fixture
def cal(monkeypatch):
    def install(**kw):
        fake = FakeCal(**kw)
        monkeypatch.setattr(mt, "_gcal", lambda: fake)
        return fake
    return install


def _find(**kw):
    kw.setdefault("when", "next week")
    return str(mt.find_time(**kw))


# ── the claim it must not make ─────────────────────────────────────────────

def test_a_calendar_it_cannot_see_is_named_as_unchecked(cal):
    cal(busy={"primary": []}, unreadable=["rahul@work.test"])
    said = _find(with_people="rahul@work.test")
    assert "NOT checked" in said
    assert "rahul@work.test" in said
    assert "not shared" in said


def test_it_says_to_offer_rather_than_assert(cal):
    """The sentence the user gets embarrassed by."""
    cal(busy={"primary": []}, unreadable=["rahul@work.test"])
    assert "do not assert they are free" in _find(with_people="rahul@work.test")


def test_a_readable_calendar_is_named_as_checked(cal):
    cal(busy={"primary": [], "rahul@work.test": []})
    said = _find(with_people="rahul@work.test")
    assert "Checked against: your calendar, rahul@work.test" in said
    assert "NOT checked" not in said


def test_an_empty_busy_list_is_not_the_same_as_an_unreadable_one(cal):
    """Google returns both as "no busy times". Reading them the same way is the
    whole bug this feature exists around."""
    free = cal(busy={"primary": [], "rahul@work.test": []})
    seen = _find(with_people="rahul@work.test")
    del free

    cal(busy={"primary": []}, unreadable=["rahul@work.test"])
    unseen = _find(with_people="rahul@work.test")
    assert seen != unseen
    assert "NOT checked" in unseen and "NOT checked" not in seen


def test_our_own_calendar_being_unreadable_refuses_rather_than_guesses(cal):
    """Anything suggested would be a guess, and it would look like an answer."""
    cal(busy={}, unreadable=["primary", "rahul@work.test"])
    said = _find(with_people="rahul@work.test")
    assert "could not read your own calendar" in said
    assert "would be a guess" in said


def test_no_calendar_at_all_says_so(monkeypatch):
    monkeypatch.setattr(mt, "_gcal", lambda: None)
    assert "not connected" in _find(with_people="a@b.test")


# ── the arithmetic ─────────────────────────────────────────────────────────

def test_a_busy_block_is_subtracted(cal):
    day = _next_weekday()
    cal(busy={"primary": [(_local(day, 9), _local(day, 11))]})
    said = _find(when="tomorrow" if day == 1 else "next week")
    assert "9:00 AM" not in said


def test_the_other_persons_meetings_count_too(cal):
    day = _next_weekday()
    cal(busy={"primary": [(_local(day, 9), _local(day, 11))],
              "rahul@work.test": [(_local(day, 11), _local(day, 14))]})
    slots = mt._free_slots(
        mt._work_windows(*[(datetime.now().astimezone()
                            + timedelta(days=day)).date().isoformat()] * 2),
        [(datetime.fromisoformat(_local(day, 9)), datetime.fromisoformat(_local(day, 11))),
         (datetime.fromisoformat(_local(day, 11)), datetime.fromisoformat(_local(day, 14)))],
        30)
    assert slots and slots[0][0].hour == 14


def test_overlapping_meetings_leave_no_phantom_gap():
    """The sliver where one ends after the next began is not free time — the
    user is already in something."""
    day = _next_weekday()
    when = (datetime.now().astimezone() + timedelta(days=day)).date().isoformat()
    blocks = [
        (datetime.fromisoformat(_local(day, 9)), datetime.fromisoformat(_local(day, 12))),
        (datetime.fromisoformat(_local(day, 10)), datetime.fromisoformat(_local(day, 11))),
    ]
    slots = mt._free_slots(mt._work_windows(when, when), blocks, 30)
    assert slots and slots[0][0].hour == 12


def test_a_slot_is_never_offered_outside_working_hours():
    day = _next_weekday()
    when = (datetime.now().astimezone() + timedelta(days=day)).date().isoformat()
    for begins, _ in mt._free_slots(mt._work_windows(when, when), [], 30):
        assert mt.WORK_START <= begins.time() < mt.WORK_END


def test_weekends_are_not_offered():
    """A Saturday is free and is not a time to suggest to anybody."""
    saturday = date(2026, 9, 26)
    assert mt._work_windows(saturday.isoformat(),
                            (saturday + timedelta(days=1)).isoformat()) == []


def test_a_period_with_no_working_days_says_so(cal):
    cal(busy={"primary": []})
    assert "no working days" in str(mt.find_time(when="2026-09-26"))


def test_nothing_long_enough_is_reported_as_nothing(cal):
    day = _next_weekday()
    cal(busy={"primary": [(_local(day, 0), _local(day, 23, 59))]})
    said = str(mt.find_time(when=(datetime.now().astimezone()
                                  + timedelta(days=day)).date().isoformat()))
    assert "Nothing" in said


def test_a_slot_in_the_past_is_never_offered():
    """Today's window starts at 09:00 whatever time it is now."""
    today = datetime.now().astimezone()
    if today.weekday() not in mt.WORK_DAYS:
        pytest.skip("only meaningful on a working day")
    for begins, _ in mt._free_slots(
            mt._work_windows(today.date().isoformat(), today.date().isoformat()),
            [], 30):
        assert begins >= today


@pytest.mark.parametrize("minutes,want", [
    (0, 30),                  # unspecified, not "a five-minute meeting"
    (None, 30),
    (-9, mt.MIN_MINUTES),
    (99999, mt.MAX_MINUTES),
    (45, 45),
])
def test_a_silly_length_is_clamped(cal, minutes, want):
    cal(busy={"primary": []})
    assert f"{want}-minute" in _find(minutes=minutes)


# ── what it asks Google for ────────────────────────────────────────────────

def test_it_asks_about_the_user_as_well_as_the_others(cal):
    fake = cal(busy={"primary": []})
    _find(with_people="a@x.test, b@x.test")
    assert fake.asked[0] == ["primary", "a@x.test", "b@x.test"]


# ── the real connector, against a fake Google ──────────────────────────────
#
# Everything above fakes `free_busy` itself, so it proves the TOOL and says
# nothing about whether the connector reads Google's answer correctly. It was
# not caught: deleting the `errors` check — the one line this whole feature
# rests on — passed all 31 of them.


def _connector(response):
    """A real `GoogleCalendarConnector` whose Google answers with `response`."""
    from chitragupta.connectors.gcal import GoogleCalendarConnector

    conn = GoogleCalendarConnector()
    sent: list[dict] = []

    class Freebusy:
        @staticmethod
        def query(body):
            sent.append(body)
            return type("Request", (), {
                "execute": staticmethod(lambda: response)})()

    service = type("Service", (), {
        "freebusy": staticmethod(lambda: Freebusy())})()
    conn._client = lambda interactive=False: (service, "")  # type: ignore[method-assign]
    return conn, sent


def test_an_errored_calendar_is_unreadable_not_empty():
    """Google's shape for "not shared with you": an empty busy list AND an
    errors entry. Reading only `busy` sees a person free all week."""
    conn, _ = _connector({"calendars": {
        "me@x.test": {"busy": [{"start": "s", "end": "e"}]},
        "rahul@work.test": {"busy": [], "errors": [{"reason": "notFound"}]},
    }})
    out = conn.free_busy(["me@x.test", "rahul@work.test"], "s", "e")
    assert out["unreadable"] == ["rahul@work.test"]
    assert "rahul@work.test" not in out["busy"]
    assert out["busy"]["me@x.test"] == [("s", "e")]


def test_a_calendar_google_omitted_entirely_is_unreadable_too():
    conn, _ = _connector({"calendars": {"me@x.test": {"busy": []}}})
    out = conn.free_busy(["me@x.test", "ghost@x.test"], "s", "e")
    assert out["unreadable"] == ["ghost@x.test"]


def test_a_genuinely_empty_calendar_is_readable_and_free():
    """The distinction only means something if the other side works too."""
    conn, _ = _connector({"calendars": {"free@x.test": {"busy": []}}})
    out = conn.free_busy(["free@x.test"], "s", "e")
    assert out["unreadable"] == []
    assert out["busy"] == {"free@x.test": []}


def test_a_query_that_raises_makes_everybody_unreadable():
    """Never "they are all free" because the network fell over."""
    from chitragupta.connectors.gcal import GoogleCalendarConnector

    conn = GoogleCalendarConnector()

    def boom():
        raise RuntimeError("network")

    service = type("Service", (), {"freebusy": staticmethod(
        lambda: type("F", (), {"query": staticmethod(
            lambda body: type("R", (), {
                "execute": staticmethod(boom)})())})())})()
    conn._client = lambda interactive=False: (service, "")  # type: ignore[method-assign]
    out = conn.free_busy(["a@x.test", "b@x.test"], "s", "e")
    assert out["busy"] == {}
    assert out["unreadable"] == ["a@x.test", "b@x.test"]


def test_duplicate_addresses_are_asked_about_once():
    """Deduplicated where the request is built, so any caller gets it."""
    conn, sent = _connector({"calendars": {"a@x.test": {"busy": []}}})
    conn.free_busy(["a@x.test", "a@x.test", ""], "s", "e")
    assert [i["id"] for i in sent[0]["items"]] == ["a@x.test"]


def test_finding_time_alone_needs_nobody(cal):
    cal(busy={"primary": []})
    said = _find()
    assert "slots in next week" in said
    assert "NOT checked" not in said


# ── the date phrase this job is named after ────────────────────────────────

def test_next_week_is_a_period_this_app_understands():
    """This module grew up answering "what did I get", so every relative
    phrase in it pointed backwards — and `calendar_lookup("next week")`, the
    most ordinary question anybody asks a diary, came back "I could not read
    that as a period"."""
    sunday = date(2026, 9, 20)
    assert parse_date_range("next week", today=sunday) == ("2026-09-21",
                                                           "2026-09-27")


def test_next_week_from_midweek_is_the_week_after_this_one():
    wednesday = date(2026, 9, 23)
    assert parse_date_range("this week", today=wednesday) == ("2026-09-21",
                                                              "2026-09-27")
    assert parse_date_range("next week", today=wednesday) == ("2026-09-28",
                                                              "2026-10-04")


@pytest.mark.parametrize("phrase,want", [
    ("next month", ("2026-10-01", "2026-10-31")),
    ("next 7 days", ("2026-09-20", "2026-09-27")),
])
def test_the_other_forward_phrases_work_too(phrase, want):
    assert parse_date_range(phrase, today=date(2026, 9, 20)) == want


def test_looking_backwards_still_means_backwards():
    """The new branches must not have captured the old phrases."""
    sunday = date(2026, 9, 20)
    assert parse_date_range("last week", today=sunday) == ("2026-09-07",
                                                           "2026-09-13")
    assert parse_date_range("last month", today=sunday) == ("2026-08-01",
                                                            "2026-08-31")
    assert parse_date_range("past 3 days", today=sunday) == ("2026-09-17",
                                                             "2026-09-20")


# ── who can do it ──────────────────────────────────────────────────────────

def test_an_agent_with_a_diary_can_find_a_gap_in_it():
    """One that can read a calendar and cannot find a gap answers "when suits
    you?", which is the job handed back."""
    from chitragupta.agents.library import TEMPLATES

    for template in TEMPLATES:
        tools = template.resolved_tools()
        if "calendar_lookup" in tools:
            assert "find_time" in tools, template.id


def test_finding_a_time_needs_the_calendar_granted():
    """It reads other people's free/busy as well as the user's own, so it is
    at least as much a reach into Google as the lookup beside it."""
    from chitragupta.agents.connector_grants import FIRST_PARTY_TOOLS

    assert FIRST_PARTY_TOOLS["find_time"] == "gcal"


def test_the_recipe_says_to_offer_not_to_assert():
    from chitragupta.agents.prompt import _CALENDAR_RECIPE

    assert "find_time" in _CALENDAR_RECIPE
    assert "could NOT check" in _CALENDAR_RECIPE
    assert "do not share a calendar" in _CALENDAR_RECIPE


def test_the_tool_description_warns_the_model_too():
    from chitragupta.agents.tools import TOOL_DEFS

    assert "could NOT check" in TOOL_DEFS["find_time"].description


# ── the scorecard ──────────────────────────────────────────────────────────

def test_arranging_a_meeting_is_on_the_scorecard():
    from chitragupta.agents import evaluation

    card = evaluation.run(include_slow=False)
    failed = [c.detail for c in card.checks
              if c.key.startswith("find_time") and not c.passed]
    assert not failed, failed
    assert {"find_time_checks", "find_time_offers",
            "find_time_concrete"} <= {c.key for c in card.checks}
