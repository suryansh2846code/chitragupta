"""A paginated read is a loop a remote party controls, so it is bounded.

Four connectors paged before this and each hand-rolled it; none guarded the
three ways a paginated read actually goes wrong:

* **A cursor that repeats** — a provider bug, a proxy, or a filter changing
  underneath the walk. The symptom is a sync that never finishes and a brain
  filling with duplicates that dedup absorbs silently.
* **A dataset larger than anyone expected** — 400,000 files is not a bug, and
  reading all of them on a laptop is.
* **A next-token that is not a token** — `null`, `""`, `0`, a dict. Each is
  falsy or unusable differently, and `if token:` is right for only two.

And the rule underneath all three: **a truncated read must say it was
truncated.** One that does not is indistinguishable from a complete one, which
is how a checkpoint moves past records nobody fetched.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors.pagination import (
    MAX_PAGES,
    Page,
    Stopped,
    cursor_of,
    link_header_next,
    walk,
)


def paged(*pages, cursors=None):
    """A `fetch` over fixed pages. `cursors[i]` is what page i hands back."""
    marks = cursors if cursors is not None else [
        str(i + 1) if i + 1 < len(pages) else "" for i in range(len(pages))]

    def fetch(cursor: str) -> Page:
        index = 0 if not cursor else marks.index(cursor) + 1
        return Page(records=list(pages[index]), next_cursor=marks[index])

    return fetch


# ── the ordinary walk ─────────────────────────────────────────────────────


def test_it_reads_every_page_until_the_provider_says_stop():
    records, result = walk(paged([1, 2], [3, 4], [5]))

    assert records == [1, 2, 3, 4, 5]
    assert result.pages == 3
    assert result.stopped is Stopped.EXHAUSTED
    assert result.complete


def test_a_single_page_is_a_complete_walk():
    records, result = walk(paged([1, 2, 3]))

    assert records == [1, 2, 3]
    assert result.complete


def test_an_empty_first_page_is_complete_not_broken():
    """A source with nothing in it is a working source."""
    records, result = walk(paged([]))

    assert records == []
    assert result.complete
    assert result.why("Acme") == ""


def test_a_finished_walk_offers_no_resume_cursor():
    """Resuming a finished walk would re-read its last page forever."""
    _, result = walk(paged([1], [2]))

    assert result.resume_cursor == ""


# ── the three bounds ──────────────────────────────────────────────────────


def test_a_repeated_cursor_stops_the_walk():
    """The provider handed back a cursor it already gave us. Left unguarded
    this fetches page 3 forever."""
    fetch = paged([1], [2], [3], cursors=["a", "b", "b"])

    records, result = walk(fetch)

    assert result.stopped is Stopped.LOOPED
    assert records == [1, 2, 3]


def test_a_cursor_that_points_at_itself_stops_the_walk():
    def fetch(cursor):
        return Page(records=[1], next_cursor="same")

    _, result = walk(fetch, start="same")

    assert result.stopped is Stopped.LOOPED


def test_the_record_limit_stops_the_walk_and_trims_the_page():
    records, result = walk(paged([1, 2, 3], [4, 5, 6], [7]), max_records=4)

    assert records == [1, 2, 3, 4], "the page is cut, not taken whole"
    assert result.stopped is Stopped.RECORD_LIMIT
    assert result.records == 4


def test_the_page_limit_stops_a_walk_that_will_not_end():
    seen = {"n": 0}

    def endless(cursor):
        seen["n"] += 1
        return Page(records=[1], next_cursor=f"page{seen['n']}")

    _, result = walk(endless, max_pages=5)

    assert result.stopped is Stopped.PAGE_LIMIT
    assert result.pages == 5


def test_there_is_a_page_ceiling_even_when_the_caller_names_none():
    """A walk that wants more than this is a walk that has gone wrong."""
    assert MAX_PAGES > 0
    calls = {"n": 0}

    def endless(cursor):
        calls["n"] += 1
        return Page(records=[], next_cursor=f"c{calls['n']}")

    _, result = walk(endless)

    assert result.pages == MAX_PAGES


def test_a_cancel_stops_between_pages():
    class Stop:
        def __init__(self):
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 2

    records, result = walk(paged([1], [2], [3], [4]), cancel=Stop())

    assert result.stopped is Stopped.CANCELLED
    assert records == [1, 2]


# ── a truncated read says so ──────────────────────────────────────────────


def test_a_truncated_walk_is_never_reported_as_complete():
    """The failure that matters most: indistinguishable from a full read is
    how a checkpoint moves past records nobody fetched."""
    _, result = walk(paged([1, 2], [3, 4]), max_records=2)

    assert not result.complete
    assert result.truncated


def test_a_looping_walk_is_not_called_truncated_either():
    """Truncated means "there is more, we stopped". Looping means "the
    provider is broken". Both are incomplete and they are not the same news."""
    _, result = walk(paged([1], [2], cursors=["a", "a"]))

    assert not result.complete
    assert not result.truncated


def test_a_truncated_walk_says_where_to_pick_up():
    _, result = walk(paged([1, 2], [3, 4], [5]), max_records=2)

    assert result.resume_cursor, "the next pass must not start at the beginning"


@pytest.mark.parametrize("stopped", list(Stopped))
def test_every_ending_can_be_explained_to_a_person(stopped):
    from chitragupta.connectors.pagination import Walk

    sentence = Walk(stopped=stopped, pages=2, records=5).why("Acme")

    if stopped is Stopped.EXHAUSTED:
        assert sentence == "", "nothing to say about a complete read"
    else:
        assert sentence and "Acme" in sentence


# ── committing as you go, which is what makes a walk resumable ────────────


def test_each_page_is_handed_over_before_the_next_is_requested():
    """A caller that ingests inside the callback has committed each page
    before the next request goes out, so a crash costs one page rather than
    the pass. That is the whole difference between `resumable` and not."""
    seen: list[tuple[list, int]] = []

    walk(paged([1, 2], [3, 4], [5]),
         on_page=lambda records, w: seen.append((list(records), w.pages)))

    assert seen == [([1, 2], 1), ([3, 4], 2), ([5], 3)]


def test_an_empty_page_is_not_handed_over():
    """A callback that commits would otherwise checkpoint nothing as
    something."""
    seen = []

    walk(paged([1], [], [2]), on_page=lambda r, w: seen.append(r))

    assert seen == [[1], [2]]


# ── reading a cursor out of a payload ─────────────────────────────────────


@pytest.mark.parametrize("payload,expected", [
    ({"nextPageToken": "abc"}, "abc"),
    ({"next_cursor": "abc"}, "abc"),
    ({"nextPageToken": None}, ""),
    ({"nextPageToken": ""}, ""),
    ({"nextPageToken": False}, ""),
    ({}, ""),
    ("not a dict", ""),
])
def test_the_four_spellings_of_no_more_pages(payload, expected):
    assert cursor_of(payload, "nextPageToken", "next_cursor") == expected


def test_page_zero_is_a_page_not_an_ending():
    """A page-number provider legitimately says 0 for the first page. Reading
    that as "finished" silently drops the whole dataset."""
    assert cursor_of({"next_page": 0}, "next_page") == "0"


def test_a_cursor_of_the_wrong_shape_is_refused_not_coerced():
    """Saying so beats forcing a dict into a cursor that cannot work."""
    assert cursor_of({"next": {"token": "x"}}, "next") == ""
    assert cursor_of({"next": ["x"]}, "next") == ""


def test_an_integer_cursor_is_read_as_one():
    assert cursor_of({"page": 4}, "page") == "4"


def test_the_first_key_that_has_a_value_wins():
    assert cursor_of({"a": None, "b": "yes"}, "a", "b") == "yes"


# ── link headers, which is all GitHub offers ──────────────────────────────


def test_the_next_link_is_found_among_the_others():
    header = ('<https://api.github.test/x?page=1>; rel="prev", '
              '<https://api.github.test/x?page=3>; rel="next", '
              '<https://api.github.test/x?page=9>; rel="last"')

    assert link_header_next(header) == "https://api.github.test/x?page=3"


def test_a_last_page_has_no_next_link():
    header = '<https://api.github.test/x?page=1>; rel="first"'

    assert link_header_next(header) == ""


@pytest.mark.parametrize("header", ["", "garbage", "<no-rel>", None])
def test_a_link_header_we_cannot_read_ends_the_walk(header):
    assert link_header_next(header) == ""


def test_unquoted_and_oddly_spaced_relations_are_still_read():
    """Real servers emit both spellings; a parser that takes only one stops
    paginating against half of them."""
    assert link_header_next("<https://a.test/2>;rel=next") == "https://a.test/2"
