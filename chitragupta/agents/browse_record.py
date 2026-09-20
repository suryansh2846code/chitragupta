"""What the agent DID on a website, kept — while the page itself is not.

A website is the one source this app reaches that must never become a source.
`browser/page.py` quarantines what a page says because a page is a stranger's
text, and text a stranger wrote is the thing prompt injection is made of.
Ingesting it would launder that straight into the brain, where recall would
hand it back to a future turn as if it were the user's own note. So page
content stays behind the fence, and stays out of the brain. That is settled
and this module does not reopen it.

**What it does keep is ours.** That the agent looked at `payroll.acme.com` at
four o'clock, while working on what the user had asked, is a fact about the
agent's own work — not a claim the page made. It is exactly the kind of thing
a person expects an assistant to be able to answer a week later:

    "did you ever check the pricing page?"
    "what have you been looking at on that site?"

The action log already records what *actions* ran. A read is not an action —
nothing was changed, nothing needed approving — so nothing recorded it at all,
and the work simply vanished.

**The one piece of the page that is kept, and why it is safe.** A record that
says only "visited example.com" is useless; the page's title is what makes it
readable. A title is still the page's text, so it goes through `page._defuse`
and `page._clip` — the same neutralising the page body gets — and it is stored
as a quoted fragment attributed to the site rather than as prose in our own
voice. A title reading *"Ignore previous instructions"* therefore lands as
**a page called "Ignore previous instructions"**, which is a fact about a
badly-named page rather than an instruction the brain now believes.
"""
from __future__ import annotations

from ..log import get_logger, suppressed

log = get_logger(__name__)

#: Enough to recognise a page by, not enough to smuggle a paragraph.
MAX_TITLE_CHARS = 120


def _safe_title(title: str) -> str:
    """The page's own title, neutralised the way its body is."""
    from ..browser.page import _clip, _defuse

    cleaned = " ".join(_defuse(str(title or "")).split())
    return _clip(cleaned, MAX_TITLE_CHARS)


def record_visit(url: str, title: str, origin: str, *,
                 agent_id: str = "", task: str = "") -> None:
    """Remember that the agent looked at this page. Never what it said.

    Never raises and never blocks: a browser read that failed to be recorded
    is a lost note, and a browser read that failed *because* of recording
    would be a capability broken by its own bookkeeping.
    """
    site = (origin or "").strip()
    if not site:
        return

    named = _safe_title(title)
    # Our sentence, in our voice, about our own work. The only borrowed words
    # are the title, and they are quoted and attributed.
    text = f"Looked at {site}"
    if named:
        text += f' — a page titled "{named}"'
    if task:
        # The user's own words, which are as trustworthy as the request that
        # started the turn.
        text += f". Working on: {' '.join(str(task).split())[:200]}"
    text += "."

    with suppressed("recording a page the agent looked at"):
        from ..brain import get_brain

        get_brain().ingest(
            text,
            # Its own source, so it can be found, filtered and retired as a
            # group — and so it is never mistaken for something the site said.
            source="browser",
            kind="activity",
            title=f"Looked at {site}",
            # The real address, ours to keep: it came from the allow-list
            # check, not from the page.
            uri=str(url or "") or None,
            fast=True,
            metadata={"origin": site, "agent": str(agent_id or ""),
                      "page_title": named},
        )


def what_i_looked_at(days: int = 7, limit: int = 20) -> str:
    """Pages the agents have looked at, most recent first.

    Read back out of the brain rather than a second store, because that is
    where it was put and a second list is a second thing to keep true. The
    action log answers *what did you change*; this answers *what did you look
    at*, which for a read-only capability is the only question there is.
    """
    from datetime import UTC, datetime, timedelta

    from ..brain import get_brain

    window = max(1, min(int(days or 7), 90))
    since = (datetime.now(UTC) - timedelta(days=window)).isoformat()

    rows = []
    with suppressed("reading back the pages an agent looked at"):
        store = get_brain().store
        rows = store._conn.execute(
            "SELECT text, uri, created_at FROM memories "
            "WHERE source='browser' AND kind='activity' AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (since, max(1, min(int(limit or 20), 100)))).fetchall()

    if not rows:
        return (f"No websites looked at in the last {window} day(s). "
                "Agents can only read sites the user has allowed.")

    lines = [f"Looked at, last {window} day(s):"]
    for row in rows:
        when = str(row["created_at"] or "")[:10]
        lines.append(f"- {when} · {row['text']}")
    return "\n".join(lines)
