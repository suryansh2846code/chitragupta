"""Walking a provider's pages without walking them forever.

Four connectors page and each hand-rolled it: `notion.py` threads
`start_cursor`, `gmail.py` threads `pageToken`, `gdrive.py` threads
`nextPageToken` with its own `while len(files) < max_results`, and
`custom_api.py` does not page at all — it reads whatever one request returns
and calls that the dataset.

None of them guard the thing that actually goes wrong. A paginated read is an
unbounded loop controlled by a remote party, and there are exactly three ways
it ends badly:

* **A cursor that repeats.** A provider bug, a proxy, or a filter that changes
  underneath the walk, and the loop fetches page 3 forever. Nothing here caught
  it; the symptom is a sync that never finishes and a brain filling with
  duplicates that dedup absorbs silently.
* **A dataset larger than anyone expected.** A Drive with 400,000 files is not
  a bug, and reading all of it on a laptop is.
* **A next-token that is not a token.** `null`, `""`, `0`, a dict, the same
  page object again. Each of those is falsy or unusable in a different way, and
  `if token:` is right for two of them.

So the rule here is that every walk is bounded three ways — **pages, records,
and repeated cursors** — and says which bound it hit. A truncated read that
does not say it was truncated is the one failure mode worse than stopping: it
is indistinguishable from a complete one.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..log import get_logger
from .contract import PaginationStrategy

log = get_logger(__name__)

#: No provider legitimately needs more than this to answer one sync, and a walk
#: that wants to is a walk that has gone wrong. Deliberately generous — at the
#: default page size of 100 this is 100,000 records, well past every
#: `records_per_sync` any connector declares.
MAX_PAGES = 1000


class Stopped(StrEnum):
    """Why a walk ended. Always reported, never inferred from the count."""

    #: The provider said there is no next page. The only complete answer.
    EXHAUSTED = "exhausted"
    RECORD_LIMIT = "record_limit"
    PAGE_LIMIT = "page_limit"
    #: The same cursor came back twice.
    LOOPED = "looped"
    CANCELLED = "cancelled"
    #: The provider handed back something that is not a usable cursor.
    BAD_CURSOR = "bad_cursor"


@dataclass
class Walk:
    """What one paginated read did. Returned beside the records, never instead.

    `complete` is the field callers actually want and it is deliberately not
    `stopped == EXHAUSTED` written out at each call site: a checkpoint may only
    be advanced past a page set that genuinely ended, and getting that
    comparison wrong in one of four places is how a watermark moves past
    records nobody fetched.
    """

    pages: int = 0
    records: int = 0
    stopped: Stopped = Stopped.EXHAUSTED
    #: The cursor to resume from, when the walk did not finish. Empty when it
    #: did — resuming a finished walk would re-read its last page forever.
    resume_cursor: str = ""
    cursors_seen: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.stopped is Stopped.EXHAUSTED

    @property
    def truncated(self) -> bool:
        return self.stopped in (Stopped.RECORD_LIMIT, Stopped.PAGE_LIMIT)

    def why(self, label: str) -> str:
        """One sentence a person can read, or "" when nothing needs saying."""
        if self.complete:
            return ""
        if self.stopped is Stopped.RECORD_LIMIT:
            return (f"Read the first {self.records} from {label}. The rest "
                    f"will come in on the next sync.")
        if self.stopped is Stopped.PAGE_LIMIT:
            return (f"Stopped after {self.pages} pages of {label} — that is "
                    f"more than one sync should take.")
        if self.stopped is Stopped.LOOPED:
            return f"{label} kept returning the same page, so this stopped."
        if self.stopped is Stopped.BAD_CURSOR:
            return f"{label} returned a page marker we could not use."
        return f"Stopped reading {label}."

    def as_dict(self) -> dict[str, Any]:
        return {"pages": self.pages, "records": self.records,
                "stopped": self.stopped.value, "complete": self.complete,
                "truncated": self.truncated,
                "resume_cursor": self.resume_cursor}


@dataclass(frozen=True)
class Page:
    """One provider response, in the two parts a walk needs.

    `next_cursor` is whatever the provider uses — a token, a page number
    rendered as a string, a URL from a `Link` header. It is a string here
    because the *walk* does not care which, and a union type would push the
    provider's choice into every caller.
    """

    records: list[Any]
    next_cursor: str = ""


def cursor_of(payload: Any, *keys: str) -> str:
    """The next cursor out of a provider payload, or "" if there is not one.

    Handles the four shapes that are all spelled "no more pages": absent,
    `null`, `""`, and `false`. `0` is deliberately **not** one of them — a
    page-number provider legitimately says `0` for the first page, and reading
    that as "finished" is an off-by-one that silently drops the whole dataset.
    """
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if value is None or value is False or value == "":
            continue
        if isinstance(value, str | int):
            return str(value)
        # A dict or a list here is a provider whose shape we guessed wrong.
        # Saying so beats coercing it into a cursor that cannot work.
        log.debug("pagination: %r is not a usable cursor (%s)", key, type(value))
        return ""
    return ""


def link_header_next(header: str) -> str:
    """The `rel="next"` URL out of an RFC-8288 `Link` header, or "".

    GitHub's pagination is this and nothing else — there is no token in the
    body — which is why it is worth a parser rather than a regex at the one
    call site. Deliberately narrow: it reads the relation and the URL, and
    ignores every other parameter rather than trying to model the whole RFC.
    """
    for part in (header or "").split(","):
        segments = part.split(";")
        url = segments[0].strip()
        if not (url.startswith("<") and url.endswith(">")):
            continue
        for attribute in segments[1:]:
            name, _, value = attribute.partition("=")
            if name.strip().lower() == "rel" and \
                    value.strip().strip('"\'').lower() == "next":
                return url[1:-1]
    return ""


def walk(
    fetch: Callable[[str], Page],
    *,
    start: str = "",
    max_records: int = 0,
    max_pages: int = MAX_PAGES,
    cancel: Any = None,
    on_page: Callable[[list[Any], Walk], None] | None = None,
) -> tuple[list[Any], Walk]:
    """Page through `fetch` until it ends, or until a bound says stop.

    `fetch(cursor)` returns a `Page`; the first call gets `start`.

    **`on_page` is how a walk becomes resumable.** A caller that ingests inside
    the callback has committed each page before the next is requested, so a
    crash costs one page rather than the pass — which is the whole difference
    between `resumable=True` and `resumable=False` on a connector. A caller
    that only wants the list can ignore it and read the return value; that is
    the non-resumable shape, and it is honest about being one.
    """
    collected: list[Any] = []
    result = Walk()
    seen: set[str] = set()
    cursor = start

    while True:
        if cancel is not None and cancel.is_set():
            result.stopped = Stopped.CANCELLED
            result.resume_cursor = cursor
            return collected, result

        if result.pages >= max_pages:
            result.stopped = Stopped.PAGE_LIMIT
            result.resume_cursor = cursor
            return collected, result

        page = fetch(cursor)
        result.pages += 1
        records = list(page.records or [])

        if max_records > 0 and result.records + len(records) > max_records:
            records = records[:max_records - result.records]

        result.records += len(records)
        collected.extend(records)
        # **Set before `on_page`, not only when the walk ends.** A caller that
        # checkpoints inside the callback is recording *where to pick up next*,
        # and this is the only moment that value is known. Populating it only
        # at the end meant every mid-walk checkpoint stored an empty cursor —
        # so a crash on page two resumed from page one, which is exactly the
        # restart-from-zero this whole design exists to avoid.
        result.resume_cursor = page.next_cursor
        if on_page is not None and records:
            on_page(records, result)

        if max_records > 0 and result.records >= max_records:
            result.stopped = Stopped.RECORD_LIMIT
            # The cursor we would have used next, so the next pass picks up
            # where this stopped rather than at the beginning.
            result.resume_cursor = page.next_cursor
            return collected, result

        nxt = (page.next_cursor or "").strip()
        if not nxt:
            result.stopped = Stopped.EXHAUSTED
            result.resume_cursor = ""
            return collected, result

        if nxt in seen or nxt == cursor:
            # The loop guard. A provider that hands back a cursor it already
            # gave us is one we stop asking, and we resume from it rather than
            # from the start: it is the last position we know to be real.
            result.stopped = Stopped.LOOPED
            result.resume_cursor = nxt
            log.debug("pagination: %r repeated after %d pages", nxt, result.pages)
            return collected, result

        seen.add(nxt)
        result.cursors_seen.append(nxt)
        cursor = nxt


def pages(fetch: Callable[[str], Page], **kwargs: Any) -> Iterator[list[Any]]:
    """`walk`, one page at a time, for a caller that never wants the whole set
    in memory. The backpressure shape — see `engine.py`."""
    buffer: list[list[Any]] = []

    def collect(records: list[Any], _walk: Walk) -> None:
        buffer.append(records)

    # `walk` drives; this drains what each page produced. Not a generator over
    # `walk` itself, because `walk` owns the bounds and inverting it would put
    # the loop guard in the caller.
    walk(fetch, on_page=collect, **kwargs)
    yield from buffer


#: Which `cursor_of` keys each strategy usually means. Advisory — a connector
#: passes its own keys — but it puts the four spellings in one place rather
#: than in four connectors.
KEYS: dict[PaginationStrategy, tuple[str, ...]] = {
    PaginationStrategy.CURSOR: ("next_cursor", "cursor", "start_cursor"),
    PaginationStrategy.NEXT_TOKEN: ("nextPageToken", "next_page_token",
                                    "pageToken", "next_token"),
    PaginationStrategy.PAGE_NUMBER: ("next_page", "page"),
    PaginationStrategy.LINK_HEADER: (),
    PaginationStrategy.NONE: (),
}
