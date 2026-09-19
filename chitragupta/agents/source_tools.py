"""Reaching the user's own sources, not just Gmail.

Eleven connectors are registered and `gmail_search` was the only connector tool.
So Personal — described to the user as a chief of staff — had no way to look at
a calendar, and no agent could refresh a stale source. "Check my mail now" was
not a thing anyone could ask for.

Three tools, and the shape of each is chosen to be honest about what it can do:

* `calendar_lookup` reads the calendar the user has already synced, bounded by a
  date range, and says plainly when the calendar is not connected or has nothing
  in that window. It does not pretend to a live fetch it may not be able to make.
* `sync_source` refreshes one source on request, over the uniform
  `Connector.sync()` every connector implements — so it works for sources added
  after this was written.
* `search_source` replaces `gmail_search` and separates the two arguments that
  tool conflated: a source-native `filter` for the fetch, and natural language
  `about` for the recall. `gmail_search` passed its Gmail-syntax query straight
  into an embedding index, where `newer_than:30d` means nothing at all.
"""
from __future__ import annotations

from ..brain import get_brain
from ..core.dateparse import parse_date_range
from ..log import get_logger, suppressed
from .results import ToolResult

log = get_logger(__name__)

#: Calendar entries read back in one answer. A month of a busy calendar would
#: spend the whole turn's context on one call.
MAX_EVENTS = 30

#: Sources whose data is calendar-shaped, in the order we would rather read them.
CALENDAR_SOURCES = ("gcal", "apple_calendar")


def _connector(name: str):
    from ..connectors import get_connector
    return get_connector(name)


def _configured(name: str) -> tuple[bool, str]:
    try:
        return _connector(name).is_configured()
    except KeyError:
        return False, f"'{name}' is not a source this app knows about"
    except Exception as exc:                       # pragma: no cover - defensive
        return False, str(exc)[:120]


def known_sources() -> list[str]:
    from ..connectors import REGISTRY
    return sorted(REGISTRY)


# ── calendar ─────────────────────────────────────────────────────────────
def calendar_lookup(when: str = "today") -> ToolResult:
    """What is on the user's calendar over a period.

    Reads the synced calendar rather than the network: a tool call that waits on
    an OAuth refresh and 250 events is not something to put in the middle of a
    turn. `sync_source("gcal")` is there for when the answer needs to be fresh,
    and this says so when it finds nothing.
    """
    phrase = (when or "today").strip()
    window = parse_date_range(phrase)
    if not window:
        return ToolResult.failed(
            f"I could not read '{phrase}' as a date or period. Try 'today', "
            "'tomorrow', 'this week', or a date like 2026-09-15.")
    start, end = window

    connected = [s for s in CALENDAR_SOURCES if _configured(s)[0]]
    hits = get_brain().store.search(
        "calendar event meeting appointment",
        limit=MAX_EVENTS, date_start=start, date_end=end,
    )
    events = [h for h in hits if (h.memory.source or "") in CALENDAR_SOURCES]

    if not events:
        if not connected:
            return ToolResult(
                "No calendar is connected yet, so there is nothing to look at. "
                "The Connectors panel can link Google Calendar or Apple Calendar.")
        span = start if start == end else f"{start} to {end}"
        return ToolResult(
            f"Nothing is on the calendar for {span}. If that looks wrong, the "
            "calendar may not have synced recently.")

    span = start if start == end else f"{start} to {end}"
    lines = [f"Calendar, {span}:"]
    movable = 0
    for hit in events[:MAX_EVENTS]:
        text = " ".join((hit.memory.text or "").split())
        # The id the sync already stored. Without it an agent asked to move a
        # meeting can describe the meeting and cannot address it — the same
        # gap `list_mail` had, and the same fix: show what you already know.
        # Only Google's ids are usable; Apple Calendar is read-only here, so a
        # row with no id simply does not carry one rather than carrying a
        # useless one.
        event_id = ""
        with suppressed("reading the id of a synced calendar event"):
            event_id = str((hit.memory.metadata or {}).get("event_id") or "")
        lines.append(f"- {text[:220]}"
                     + (f"\n  id={event_id}" if event_id else ""))
        movable += 1 if event_id else 0
    if movable:
        lines.append("\nUse the id to move or cancel one. Never guess an id.")
    return ToolResult("\n".join(lines))


# ── refreshing ───────────────────────────────────────────────────────────
def sync_source(source: str) -> ToolResult:
    """Pull anything new from one of the user's sources, right now."""
    name = (source or "").strip().lower()
    if not name:
        return ToolResult.failed(
            f"Say which source to refresh. Known: {', '.join(known_sources())}.")

    ready, why = _configured(name)
    if not ready:
        return ToolResult.failed(
            f"'{name}' is not connected: {why}. The user can link it in the "
            "Connectors panel.")

    try:
        # interactive=False: a sync in the middle of a turn must never open a
        # browser and wait for someone who is looking at a chat window.
        result = _connector(name).sync(interactive=False)
    except Exception as exc:
        return ToolResult.failed(f"Could not refresh {name}: {str(exc)[:160]}")

    if result.errors:
        return ToolResult.failed(f"{name} reported: {result.errors[0][:160]}")
    added = getattr(result, "added", 0) or 0
    if not added:
        return ToolResult(f"{name} is already up to date — nothing new.")
    return ToolResult(f"Refreshed {name}: {added} new item(s) are now in the brain.")


# ── searching one source ─────────────────────────────────────────────────
def search_source(source: str, about: str, filter: str = "",
                  max_results: int = 10) -> ToolResult:
    """Fetch from one source, then read what came back.

    `filter` is the source's own query language, which only the source
    understands; `about` is what the user actually wants, in words, which is what
    the brain's recall understands. `gmail_search` ran one string through both
    and fed `newer_than:30d` to an embedding index.
    """
    name = (source or "").strip().lower()
    question = (about or "").strip()
    if not name:
        return ToolResult.failed(
            f"Say which source to search. Known: {', '.join(known_sources())}.")
    if not question:
        return ToolResult.failed(
            "Say what you are looking for, in words — not in the source's query "
            "syntax. Put that in `filter` instead.")

    ready, why = _configured(name)
    if not ready:
        return ToolResult.failed(f"'{name}' is not connected: {why}.")

    fetched = 0
    with suppressed("refreshing a source before searching it"):
        kwargs: dict = {"interactive": False, "max_results": int(max_results or 10)}
        if filter:
            kwargs["query"] = filter
        result = _connector(name).sync(**kwargs)
        if result.errors:
            log.debug("%s sync during search reported: %s", name, result.errors[0])
        fetched = getattr(result, "added", 0) or 0

    recalled = get_brain().recall(question, limit=max(3, int(max_results or 10)),
                                 prefer=[name])
    if not recalled["context"]:
        return ToolResult(
            f"Nothing in {name} matches '{question}'"
            + (f" (fetched {fetched} new item(s) first)." if fetched else "."))
    head = f"[{fetched} new item(s) fetched from {name}]\n" if fetched else ""
    return ToolResult(head + recalled["context"])
