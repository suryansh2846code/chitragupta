"""A connector describes what it can do, not how many tools it has.

A server answers `tools/list` with a flat array — GitHub's is 45 long — and a
screen built straight on it says *"this connector has 45 tools"*. That is a
count. Nobody can consent to a count, and the question a person is actually
answering is **what can this reach, and what can it change**.

    GitHub reads 26 things, changes 16, 2 need care.

Everything in that sentence was already in what the server published. Three
judgements make it true rather than merely shorter, and each is a test below:

* **Irreversible is its own tier.** `merge_pull_request` and `delete_file` are
  not louder writes — `permissions` already refuses to allow-list them, and a
  manifest that buried them among ordinary changes would describe a gentler
  app than the one that ships.
* **A server's own furniture is not "things about you".** A connector that
  lists its templates and labels does not read 31 things about the user, and a
  count inflated by configuration is the count all over again.
* **Ours is labelled ours.** MCP publishes no rate limits, so the ceilings
  shown are this app's and say so. A limit we impose is not a promise the
  vendor made, and blurring the two is how a manifest starts lying.

What is deliberately **absent** matters as much: MCP has no standard for OAuth
scopes, so there is no scopes field. What a vendor granted lives on their
consent screen and is invisible here; "unknown" in a field nobody can fill is
worse than not asking the question.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors.mcp_manifest import Capability, manifest_of
from chitragupta.connectors.mcp_source import MCPServerSpec, classify_tools


class _Tool:
    def __init__(self, name, description="", required=(), read_only=None):
        self.name, self.description = name, description
        self.inputSchema = {"type": "object", "required": list(required),
                            "properties": {r: {"type": "string"}
                                           for r in required}}
        self.annotations = (type("A", (), {"readOnlyHint": read_only})()
                            if read_only is not None else None)


def build(tools, **spec_kw):
    spec = MCPServerSpec(id="demo", name="Demo", **spec_kw)
    return manifest_of(spec, classify_tools(tools))


# ── the sentence ──────────────────────────────────────────────────────────


def test_the_headline_is_a_decision_not_an_inventory():
    man = build([_Tool("list_issues", read_only=True),
                 _Tool("search_issues", required=["q"], read_only=True),
                 _Tool("create_issue", required=["title"]),
                 _Tool("delete_file", required=["path"])])

    assert man.headline == "Demo reads 2 things, changes 1, 1 needs care."


def test_a_read_only_connector_says_it_changes_nothing():
    """The most reassuring thing a manifest can say, and it has to be said
    rather than inferred from the absence of a section."""
    man = build([_Tool("list_pages", read_only=True)])

    assert "changes nothing" in man.headline


def test_one_of_a_thing_is_not_called_two():
    man = build([_Tool("list_pages", read_only=True)])

    assert "reads 1 thing," in man.headline


# ── irreversible is its own tier ──────────────────────────────────────────


@pytest.mark.parametrize("tool", ["delete_file", "merge_pull_request"])
def test_a_verb_with_no_inverse_is_separated_from_ordinary_writes(tool):
    """`connectors/CLAUDE.md` names `merge_pull_request` as the case that
    proved `_is_write` had to exist. It must not now be filed as routine."""
    man = build([_Tool(tool, required=["x"])])

    assert [c.tool for c in man.needs_care] == [tool]
    assert man.changes == []


def test_an_ordinary_write_is_not_promoted_to_needing_care():
    """Marking everything dangerous is the same as marking nothing."""
    man = build([_Tool("add_issue_comment", required=["body"])])

    assert [c.tool for c in man.changes] == ["add_issue_comment"]
    assert man.needs_care == []


# ── the server's own furniture ────────────────────────────────────────────


def test_configuration_listings_do_not_inflate_the_count():
    """`list_issue_labels` is how the server is set up; `list_issues` is the
    user's work. They differ by one word and the headline must not."""
    man = build([_Tool("list_issues", read_only=True),
                 _Tool("list_issue_labels", read_only=True),
                 _Tool("list_templates", read_only=True)])

    assert "reads 1 thing," in man.headline
    assert len(man.reads) == 3, "still listed, just not counted as yours"
    assert sum(1 for c in man.reads if c.is_furniture) == 2


# ── ours is labelled ours ─────────────────────────────────────────────────


def test_the_limits_say_who_imposes_them():
    man = build([_Tool("list_pages", read_only=True)])

    assert man.bounds["imposed_by"] == "Chitragupta"
    assert man.bounds["records_per_sync"] > 0
    assert man.bounds["seconds_per_call"] > 0


def test_there_is_no_field_pretending_to_know_oauth_scopes():
    """MCP standardises none, and a field full of "unknown" is worse than an
    honest silence. What IS knowable is reported instead: which tools the user
    left switched on here."""
    man = build([_Tool("list_pages", read_only=True)])
    payload = man.as_dict()

    assert "scopes" not in payload
    assert payload["restricted"] is False


def test_switching_tools_off_is_reported_as_a_restriction():
    man = build([_Tool("list_pages", read_only=True), _Tool("create_page")],
                allowed_tools=["list_pages"])

    assert man.restricted is True
    assert [c.permitted for c in man.reads] == [True]
    assert [c.permitted for c in man.changes] == [False]


# ── what it never invents ─────────────────────────────────────────────────


def test_a_tool_with_no_description_gets_no_description():
    """A sentence we wrote about somebody else's verb would read as
    authoritative and be a guess."""
    man = build([_Tool("mystery_tool", required=["x"])])

    assert man.changes[0].summary == ""


def test_the_servers_own_first_sentence_is_used_verbatim():
    man = build([_Tool("list_issues", "# Heading\nLists issues in a repo. More.",
                       read_only=True)])

    assert man.reads[0].summary == "Lists issues in a repo"


def test_the_required_arguments_come_from_the_server():
    man = build([_Tool("create_issue", required=["owner", "repo", "title"])])

    assert man.changes[0].needs == ["owner", "repo", "title"]


def test_a_capability_knows_furniture_on_its_own():
    assert Capability("list_templates", "read", "", True).is_furniture
    assert not Capability("list_issues", "read", "", True).is_furniture


# ── the two vocabularies join ─────────────────────────────────────────────


def test_a_tool_reports_itself_in_the_app_wide_vocabulary():
    """So an MCP write passes through the *same* gate as a first-party one,
    rather than down a parallel path of its own. `actions.mcp_action` declares
    `update:record`; these are what it has to sit inside."""
    man = build([_Tool("list_issues", read_only=True),
                 _Tool("create_issue", required=["title"]),
                 _Tool("delete_file", required=["path"])])

    assert str(man.reads[0].capability) == "read:record"
    assert str(man.changes[0].capability) == "update:record"
    assert str(man.needs_care[0].capability) == "delete:record"


def test_care_is_destructive_in_the_shared_vocabulary_too():
    """The tier has to survive the translation, or the join is decorative."""
    from chitragupta.connectors.capability import Access

    man = build([_Tool("merge_pull_request", required=["x"]),
                 _Tool("add_issue_comment", required=["body"]),
                 _Tool("list_issues", read_only=True)])

    assert man.needs_care[0].access is Access.DESTRUCTIVE
    assert man.changes[0].access is Access.WRITE
    assert man.reads[0].access is Access.READ


def test_the_capability_crosses_the_wire():
    man = build([_Tool("create_issue", required=["title"])])

    row = man.as_dict()["changes"][0]

    assert row["capability"] == "update:record"
    assert row["access"] == "write"


def test_every_tool_the_manifest_lists_sits_inside_what_the_connector_declares():
    """The ceiling. A manifest that named a capability `MCPConnector` does not
    declare would be a write the floor check could never see."""
    from chitragupta.connectors.mcp_source import MCPConnector

    man = build([_Tool("list_issues", read_only=True),
                 _Tool("create_issue", required=["t"]),
                 _Tool("delete_file", required=["p"])])

    for cap in [*man.reads, *man.changes, *man.needs_care]:
        assert cap.capability in MCPConnector.capabilities
