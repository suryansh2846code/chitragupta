"""What is waiting on the *user* — the other direction from `followup_tools`.

`awaiting_reply` answers *"who owes me an answer"*. This answers the mirror
question, *"who am I keeping waiting"*, and they are not the same list read
backwards: one is built from commitments the app recorded, the other from an
inbox nobody curated.

The failure this exists to prevent is the embarrassing one, and it is exactly
the follow-up failure with the roles swapped: **drafting a reply to a thread
the user already answered.** An inbox message is not an unanswered message.
Mail stays in the inbox after you reply to it, so anything built on `list_mail`
alone will confidently prepare an answer to a conversation that finished on
Tuesday — and unlike a stale follow-up, this one is a draft sitting in the
user's Drafts folder with their name on it, one keystroke from going out.

So a thread is only listed here when the **last message in it is from somebody
else**. That is `thread_reply_state`, the same primitive follow-ups use, read
the other way round: `answered=True` means somebody else spoke last, which
closes a follow-up and opens one of these.

Three states, not two, for the same reason the follow-up side has three:

* somebody else wrote last → **waiting on you**
* the user wrote last → nothing owed, and it is left out
* the thread could not be read → **could not check**, said out loud

"Could not check" must never quietly become "nothing waiting". A Gmail hiccup
that reads as an empty list tells the user they are on top of their inbox,
which is the one wrong answer that stops them looking.

**What is filtered, and why it says so.** Newsletters and notifications are
excluded using Gmail's *own* categories rather than a guess about the word
"unsubscribe" — the classification is already there and is better than
anything this file could invent. But a filter nobody can see is a filter
nobody can correct, so the result names what it skipped.
"""
from __future__ import annotations

from ..log import get_logger, suppressed
from .results import ToolResult

log = get_logger(__name__)

#: Below this, "you haven't replied" is not a finding — it is the same day.
#: A person who read something an hour ago and has not answered yet is not
#: behind on it, and telling them so is the app being anxious on their behalf.
DEFAULT_MIN_DAYS = 1

#: How many threads to verify. Each costs one Gmail read, so this is a real
#: cost and not a display limit — and whatever it cuts off is reported, because
#: a truncated list that looks complete is worse than a short one that says so.
DEFAULT_LIMIT = 12

#: Gmail's own buckets for mail that is not addressed to a person. Excluded
#: from the search rather than filtered afterwards, so they never occupy one
#: of the `limit` slots that a real conversation could have had.
_BULK = ("-category:promotions -category:social "
         "-category:updates -category:forums")

#: `-from:me` drops the user's own sent copies. `-in:chats` drops Hangouts
#: rows, which have thread ids that behave differently and are not email.
_QUERY = f"in:inbox -from:me -in:chats {_BULK}"


def _gmail():
    from ..connectors import get_connector

    with suppressed("looking for a connected Gmail"):
        gmail = get_connector("gmail")
        if gmail is not None and gmail.is_configured()[0]:
            return gmail
    return None


def _sender_name(raw: str) -> str:
    """The part of a `From` header a person would say out loud.

    `"Rahul Mehta <rahul@work.test>"` → `Rahul Mehta`. Falls back to the
    address, because a name we cannot find is not a reason to show nothing.
    """
    raw = (raw or "").strip()
    if "<" in raw:
        name = raw.split("<", 1)[0].strip().strip('"').strip()
        if name:
            return name
        return raw.split("<", 1)[1].rstrip(">").strip()
    return raw


def needs_reply(min_days: int = DEFAULT_MIN_DAYS,
                limit: int = DEFAULT_LIMIT) -> ToolResult:
    """Conversations where somebody is waiting on the user to answer.

    Reads the inbox, then checks each candidate thread to see who wrote last.
    Only threads whose newest message is from somebody else are listed — a
    thread the user has already answered is not waiting on them, whatever is
    still sitting in their inbox.
    """
    min_days = max(0, int(min_days or 0))
    limit = max(1, min(40, int(limit or DEFAULT_LIMIT)))

    gmail = _gmail()
    if gmail is None:
        return ToolResult(
            "Gmail is not connected, so I cannot see what is waiting on you.")

    listing = gmail.list_inbox(query=_QUERY, max_results=limit * 2)
    if not listing.get("ok"):
        # The read failed outright. Say that, rather than "nothing is waiting".
        return ToolResult(
            "I could not read your inbox just now, so I do not know what is "
            "waiting on you — please try again in a moment.")

    # One row per conversation. The inbox can hold several messages from the
    # same thread and they are one thing to answer, not three.
    threads: dict[str, dict] = {}
    for row in listing.get("messages") or []:
        thread_id = row.get("thread_id") or ""
        if thread_id and thread_id not in threads:
            threads[thread_id] = row

    if not threads:
        return ToolResult("Nothing in your inbox is waiting on a reply.")

    considered = list(threads.items())[:limit]
    dropped = len(threads) - len(considered)

    waiting: list[str] = []
    unknown: list[str] = []
    too_recent = 0
    already_answered = 0

    for thread_id, row in considered:
        state = {"answered": None, "waiting_days": 0, "last_from": ""}
        with suppressed("checking who wrote last in a thread"):
            state = gmail.thread_reply_state(thread_id)

        answered = state.get("answered")
        if answered is None:
            unknown.append(
                f"- \"{row.get('subject') or '(no subject)'}\" — could not "
                f"read that thread (thread {thread_id})")
            continue
        if answered is False:
            # WE wrote last. Nothing is owed; this is the follow-up list's
            # business, not ours.
            already_answered += 1
            continue

        days = int(state.get("waiting_days") or 0)
        if days < min_days:
            too_recent += 1
            continue

        who = _sender_name(state.get("last_from") or row.get("from") or "")
        subject = row.get("subject") or "(no subject)"
        snippet = (row.get("snippet") or "").strip()
        line = (f"- {who} — \"{subject}\", {days} day(s) waiting  "
                f"(thread {thread_id})")
        if snippet:
            line += f"\n    {snippet[:160]}"
        waiting.append(line)

    parts: list[str] = []
    if waiting:
        parts.append("WAITING ON YOU — they wrote last and you have not "
                     "answered:\n" + "\n".join(waiting))
    if unknown:
        # Never folded into "nothing waiting". See the module docstring.
        parts.append("COULD NOT CHECK — I do not know whether these are "
                     "answered, so do not assume either way:\n"
                     + "\n".join(unknown))

    if not parts:
        if already_answered or too_recent:
            settled = []
            if already_answered:
                settled.append(f"{already_answered} you have already answered")
            if too_recent:
                settled.append(f"{too_recent} that arrived too recently to "
                               f"count as waiting")
            return ToolResult(
                "Nothing is waiting on a reply — "
                + " and ".join(settled) + ".")
        return ToolResult("Nothing in your inbox is waiting on a reply.")

    tail = []
    if already_answered:
        tail.append(f"{already_answered} already answered")
    if too_recent:
        tail.append(f"{too_recent} newer than {min_days} day(s)")
    if dropped:
        # A cap that hides what it cut off reads as a complete list.
        tail.append(f"{dropped} more not checked (limit {limit})")
    if tail:
        parts.append("Not listed: " + ", ".join(tail) + ".")
    parts.append("Newsletters, promotions and notifications were left out.")

    return ToolResult("\n\n".join(parts))
