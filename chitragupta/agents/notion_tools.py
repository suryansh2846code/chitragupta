"""Finding a Notion page, so that writing to one is possible at all.

`notion_append` and `notion_create_page` address a page by uuid. The sync has
stored that uuid in every memory's metadata since it was written, and nothing
ever showed it to an agent — so an agent asked to *"add Dishu to the Mera
store page"* could recall the page, quote it back, and had no way to name it.

That is the third time this exact gap has appeared here. `list_mail` had it
(an agent could describe a message and not archive it) and `calendar_lookup`
had it (could describe a meeting and not move it). Both were fixed the same
way and this is that fix again: **show the id you already have.**

The failure it produces is worth naming, because it does not look like a
missing id. A model that cannot do the job does not say "I have no way to
address that page" — it produces a *reason*, and the reason is whatever sounds
plausible. The one observed here was an invented authorization step, telling
the user to go and approve Notion in claude.ai's settings: a place that has
nothing to do with this app, for a permission that does not exist. The system
prompt forbids exactly that sentence in as many words, and it was said anyway.
**A capability gap becomes a plausible lie, not an error message.**

Live rather than out of the brain, because a page made since the last sync is
the one somebody is most likely to mean, and because Notion's own search
already ranks by relevance.
"""
from __future__ import annotations

from ..log import get_logger
from .results import ToolResult

log = get_logger(__name__)

#: A page list nobody finishes reading has not helped anybody choose.
DEFAULT_LIMIT = 10


def _notion():
    from ..connectors import get_connector

    return get_connector("notion")


def notion_pages(about: str = "", limit: int = DEFAULT_LIMIT) -> ToolResult:
    """Notion pages this integration can reach, with the id to write to one."""
    connector = _notion()
    if connector is None:                                  # pragma: no cover
        return ToolResult.failed("Notion is not available on this machine.")

    ready, why = connector.is_configured()
    if not ready:
        # The true answer, and the one the model should repeat rather than
        # inventing a nicer-sounding one. Notion is set up in THIS app.
        return ToolResult.failed(
            f"Notion is not connected: {why}. It is added under Connectors in "
            f"Chitragupta — there is no authorization to do anywhere else.")

    found = connector.search_pages(about=about, limit=limit)
    if not found.get("ok"):
        return ToolResult.failed(str(found.get("error") or "Notion refused that."))

    pages = found.get("pages") or []
    if not pages:
        # Two very different things, and the difference is actionable: an
        # integration sees only pages somebody connected it to, so "no match"
        # is usually "not shared with it yet" rather than "does not exist".
        return ToolResult(
            (f"No Notion page matches “{about}”." if about
             else "This integration cannot see any Notion pages.")
            + " Notion only shows an integration the pages it has been added "
              "to — in Notion, open the page → ⋯ → Connections → Add.")

    lines = [f"{len(pages)} Notion page(s):"]
    for page in pages:
        lines.append(f"- {page['title']}"
                     + (f"  · edited {page['edited']}" if page.get("edited") else "")
                     + f"\n  page_id={page['id']}")
    lines.append("\nUse the page_id to add to one. Never guess a page id.")
    return ToolResult("\n".join(lines))
