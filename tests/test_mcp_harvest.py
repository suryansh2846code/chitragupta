"""What a connected MCP server puts into the brain.

**The bug this file exists for was found on a real install and nothing had
reported it.** `MCPConnector.sync()` called exactly one tool — `sync_tool`, or
failing that `permitted[0]`, which is whichever sorted first. On the machine
where it was found that was:

    Linear  →  list_agent_skills        (the server's own configuration)
    Notion  →  notion-get-tool-access   (the server's own configuration)

So both syncs ran, both ingested records, both reported success — and the
brain filled with tool manifests while the issues and pages it exists to hold
were never fetched at all. A sync that works and syncs the wrong thing has no
symptom: recall simply never has the answer, and the user concludes the app
does not know about their Notion.

Two rules come out of it, and both are about *shape* rather than vendors:

* **Harvest every listing that carries the user's content**, not one. A server
  with sixteen listings has sixteen kinds of thing worth remembering, and no
  reason to prefer the alphabetical first.
* **A server's own furniture is not content.** Configuration lists as readily
  as work does, and both answer `list_*`. Ingested, it competes with the real
  answers in recall forever.

Nothing below names Notion or Linear in the code under test — a server nobody
has seen gets the same treatment.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors import mcp_source


# ── telling content from furniture ───────────────────────────────────────
@pytest.mark.parametrize("tool", [
    "list_issues", "list_projects", "list_documents", "list_comments",
    "notion-list-private-pages", "notion-list-recent-pages",
    "list_users", "list_teams", "get_notifications",
])
def test_the_users_own_work_is_harvested(tool):
    assert not mcp_source.is_server_metadata(tool)


@pytest.mark.parametrize("tool", [
    "list_agent_skills", "notion-get-tool-access", "list_issue_labels",
    "list_project_labels", "list_release_pipelines", "list_templates",
    "list_issue_statuses", "notion-search-skills", "list_diffs",
])
def test_the_servers_own_furniture_is_not(tool):
    """Each of these lists how the server is configured. In the brain it is
    noise that recall must rank against real work, permanently."""
    assert mcp_source.is_server_metadata(tool)


def test_a_label_listing_is_skipped_and_an_issue_listing_is_not():
    """Whole words, not substrings. `list_issue_labels` is furniture and
    `list_issues` is the thing itself, and they differ by one word."""
    assert mcp_source.is_server_metadata("list_issue_labels")
    assert not mcp_source.is_server_metadata("list_issues")


def test_the_rule_names_no_vendor():
    """A per-vendor map would be a `providerId === "x"` chain for sync, which
    `/CLAUDE.md` forbids on render paths for the same reason it is wrong here:
    the next server nobody has seen gets nothing."""
    import inspect

    blob = (inspect.getsource(mcp_source.is_server_metadata)
            + " ".join(mcp_source._SERVER_METADATA)).lower()
    for vendor in ("notion", "linear", "github", "slack", "jira"):
        assert vendor not in blob


# ── how much it will read ────────────────────────────────────────────────
def test_the_number_of_listings_is_bounded():
    """Each listing is a round trip. A server with forty would turn one sync
    into a minutes-long conversation."""
    assert 1 < mcp_source.MAX_HARVEST_TOOLS <= 20


def test_the_record_budget_is_per_server_not_per_listing():
    """Sixteen listings at 200 apiece is a brain full of one connector. The
    budget is shared, which is a property of the loop rather than of any one
    call — checked at the source because there is no seam to observe it."""
    import inspect

    body = inspect.getsource(mcp_source.MCPConnector.sync)
    assert "budget = max_items" in body
    assert "budget -= len(taken)" in body
    assert "if budget <= 0:" in body


# ── what survives a listing that fails ───────────────────────────────────
def test_one_failing_listing_does_not_take_the_others_down():
    """A server can refuse one tool and serve the rest. Reported, not
    swallowed — and the sync still gets everything else."""
    import inspect

    body = inspect.getsource(mcp_source.MCPConnector.sync)
    assert "could not read" in body
    assert "continue" in body


def test_each_record_remembers_which_listing_it_came_from():
    """Without it, every record from one server is indistinguishable from
    every other and there is no way to retire one listing's worth of content
    when it turns out to be noise — which is exactly what happened here."""
    import inspect

    body = inspect.getsource(mcp_source.MCPConnector.sync)
    assert '"mcp_tool"' in body
    assert '"server"' in body


# ── a named choice still wins ────────────────────────────────────────────
def test_a_server_told_which_tool_to_sync_is_obeyed():
    """`sync_tool` is somebody having decided. The discovery below it is only
    for when nobody has."""
    import inspect

    body = inspect.getsource(mcp_source.MCPConnector.sync)
    said = body.index("if self.spec.sync_tool:")
    guessed = body.index("is_server_metadata(t)")
    assert said < guessed, "discovery is consulted before the stated choice"


def test_a_server_whose_listings_all_look_like_furniture_still_syncs():
    """Better the server's own listing than nothing: the alternative is a
    connector that reports "nothing to store" forever and looks broken."""
    import inspect

    body = inspect.getsource(mcp_source.MCPConnector.sync)
    assert "if not chosen and permitted:" in body
