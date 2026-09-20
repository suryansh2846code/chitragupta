"""Job 2 — "draft replies to anything waiting on me".

The job is not "list my inbox". It is the inbox **minus what the user already
answered**, and that subtraction is the entire capability: mail stays in the
inbox after you reply to it, so anything built on `list_mail` alone prepares
answers to conversations that finished last Tuesday. Those answers land in the
user's Drafts folder with their name on them.

So this file is mostly one question asked several ways — *does it actually
check who wrote last, or does it just believe the inbox?* — plus the third
state, which is the same one `followup_tools` needs and for the same reason:
"I could not read that thread" must never come out as "nothing is waiting".

The fake Gmail here answers `list_inbox` and `thread_reply_state` and records
what it was asked. It deliberately does NOT reimplement the filtering: a fake
that decided for itself which threads were unanswered would let the real
`needs_reply` return anything at all and still pass. That mistake has already
been made twice in this repo — once in the calendar tests and once in
`find_time` — so the assertions here are about what came *out* for threads the
fake described, never about the fake's own bookkeeping.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import reply_tools


class FakeGmail:
    """An inbox, and a per-thread answer about who wrote last."""

    def __init__(self, rows, states, *, listing_ok=True):
        self.rows = rows
        self.states = states
        self.listing_ok = listing_ok
        self.queries: list[str] = []
        self.checked: list[str] = []

    def is_configured(self):
        return True, ""

    def list_inbox(self, query="in:inbox", max_results=20, interactive=False):
        self.queries.append(query)
        if not self.listing_ok:
            return {"ok": False, "error": "boom", "messages": []}
        return {"ok": True, "messages": self.rows[:max_results]}

    def thread_reply_state(self, thread_id, interactive=False):
        self.checked.append(thread_id)
        if thread_id not in self.states:
            raise RuntimeError("unreadable thread")
        return self.states[thread_id]


def _row(thread_id, sender, subject, snippet=""):
    return {"id": f"m-{thread_id}", "thread_id": thread_id, "from": sender,
            "subject": subject, "snippet": snippet, "date": "", "unread": True}


def _they_wrote_last(days, sender="Rahul <rahul@work.test>"):
    return {"answered": True, "last_from": sender, "waiting_days": days}


def _user_wrote_last(days=0):
    return {"answered": False, "last_from": "me@work.test",
            "waiting_days": days}


@pytest.fixture
def gmail(monkeypatch):
    """Install a fake and hand it back, so a test can assert what was asked."""
    def install(rows, states, **kw):
        fake = FakeGmail(rows, states, **kw)
        monkeypatch.setattr(reply_tools, "_gmail", lambda: fake)
        return fake
    return install


# ── the subtraction this whole job is ────────────────────────────────────
def test_a_thread_the_user_already_answered_is_not_waiting_on_them(gmail):
    """The failure the tool exists to prevent.

    Both conversations are sitting in the inbox. In one the user has already
    replied. An agent that drafts an answer to that one has written a second
    reply to a finished conversation, in the user's name.

    **The answered thread is 5 days old on purpose.** Written first with
    `waiting_days=0`, this test went red when the answered-check was removed —
    but on the wrong assertion: the `min_days` filter was excluding "Invoice"
    anyway, so the line that is supposed to catch this bug was passing while
    the bug was present. Fail-first is what found that; the age is now past
    `min_days` so nothing but the answered-check can exclude it.
    """
    fake = gmail(
        [_row("t1", "Rahul <rahul@work.test>", "Proposal?"),
         _row("t2", "Dana <dana@work.test>", "Invoice")],
        {"t1": _they_wrote_last(4), "t2": _user_wrote_last(days=5)})

    out = reply_tools.needs_reply()

    assert "Proposal?" in out
    assert "Invoice" not in out, (
        "it offered to reply to a conversation the user had already answered")
    assert "1 already answered" in out, "and it says what it left out"


def test_it_actually_asks_who_wrote_last(gmail):
    """Not inferred from the inbox listing.

    An implementation that trusted `list_inbox` would pass every assertion
    above by accident whenever the fixture happened to agree with it. This
    pins the call itself.
    """
    fake = gmail([_row("t1", "Rahul <rahul@work.test>", "Proposal?")],
                 {"t1": _they_wrote_last(4)})

    reply_tools.needs_reply()

    assert fake.checked == ["t1"]


def test_the_search_excludes_the_users_own_mail_and_the_bulk_categories(gmail):
    """Gmail's own classification, rather than a guess about the word
    "unsubscribe" — and `-from:me` so a sent copy is never a candidate."""
    fake = gmail([], {})

    reply_tools.needs_reply()

    (query,) = fake.queries
    assert "-from:me" in query
    for bucket in ("promotions", "social", "updates", "forums"):
        assert f"-category:{bucket}" in query


# ── the third state ──────────────────────────────────────────────────────
def test_a_thread_it_could_not_read_is_reported_not_dropped(gmail):
    """Unknown is not "nothing waiting".

    A user told their inbox is clear stops looking, which is the one wrong
    answer with a consequence. `followup_tools` draws this line for the
    mirror case; this is the same line.
    """
    gmail([_row("t1", "Rahul <rahul@work.test>", "Proposal?")], {})

    out = reply_tools.needs_reply()

    assert "COULD NOT CHECK" in out
    assert "Proposal?" in out
    assert "Nothing" not in out


def test_an_inbox_we_could_not_read_at_all_says_so(gmail):
    """The same rule one level up. A failed listing is not an empty inbox."""
    gmail([], {}, listing_ok=False)

    out = reply_tools.needs_reply()

    assert "could not read your inbox" in out.lower()
    assert "waiting" in out.lower()


def test_gmail_not_connected_says_that_rather_than_nothing_waiting():
    """First launch. Nothing is configured and it must still answer."""
    import chitragupta.agents.reply_tools as mod

    saved = mod._gmail
    mod._gmail = lambda: None
    try:
        out = mod.needs_reply()
    finally:
        mod._gmail = saved

    assert "not connected" in out.lower()


# ── what it says, in the user's terms ────────────────────────────────────
def test_it_names_the_person_rather_than_the_header(gmail):
    """`"Rahul Mehta <rahul@work.test>"` is a header. People have names."""
    gmail([_row("t1", "x", "Proposal?")],
          {"t1": _they_wrote_last(4, "Rahul Mehta <rahul@work.test>")})

    out = reply_tools.needs_reply()

    assert "Rahul Mehta" in out
    assert "<rahul@work.test>" not in out


def test_it_carries_the_thread_id_the_draft_will_need(gmail):
    """Rule 3 of the recipe is "pass thread_id". The model can only do that
    if the tool that found the conversation said what it was."""
    gmail([_row("t1", "Rahul <rahul@work.test>", "Proposal?")],
          {"t1": _they_wrote_last(4)})

    assert "t1" in reply_tools.needs_reply()


def test_something_that_arrived_this_morning_is_not_a_finding(gmail):
    """A person who has not answered an hour-old email is not behind on it."""
    gmail([_row("t1", "Rahul <rahul@work.test>", "Proposal?")],
          {"t1": _they_wrote_last(0)})

    out = reply_tools.needs_reply(min_days=1)

    assert "Proposal?" not in out
    assert "too recently" in out or "newer than" in out


def test_one_conversation_is_one_row_however_many_messages_are_in_the_inbox(gmail):
    """Three messages from the same thread are one thing to answer."""
    fake = gmail(
        [_row("t1", "Rahul <rahul@work.test>", "Proposal?"),
         _row("t1", "Rahul <rahul@work.test>", "Re: Proposal?"),
         _row("t1", "Rahul <rahul@work.test>", "Re: Re: Proposal?")],
        {"t1": _they_wrote_last(4)})

    out = reply_tools.needs_reply()

    assert fake.checked == ["t1"], "it checked the same conversation 3 times"
    assert out.count("(thread t1)") == 1


def test_a_cap_that_cut_something_off_says_so(gmail):
    """A truncated list that reads as complete is worse than a short one.

    Each check costs a Gmail read, so the cap is real — but silence about it
    tells the user they have seen everything.
    """
    rows = [_row(f"t{n}", "Rahul <rahul@work.test>", f"Subject {n}")
            for n in range(10)]
    states = {f"t{n}": _they_wrote_last(4) for n in range(10)}
    gmail(rows, states)

    out = reply_tools.needs_reply(limit=3)

    assert "not checked" in out
    assert "limit 3" in out


def test_an_empty_inbox_is_said_plainly(gmail):
    gmail([], {})

    assert "Nothing" in reply_tools.needs_reply()
