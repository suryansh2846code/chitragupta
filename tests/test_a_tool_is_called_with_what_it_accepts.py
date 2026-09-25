"""A connector tool is told what it accepts, and refused when it is not given it.

Reported from a card the user approved and watched fail.

`notion-update-page` takes a required `command`, and it is **one of exactly six
words**. The model was handed the argument list as `command*, page_id*, …` —
names only — so it filled `command` in from whatever it remembered of Notion's
public API, chose `update_attributes`, and invented an `in_trash` flag to go
with it. The confirmation card rendered, the user approved it, the request went
to Notion, and Notion refused it.

Nothing was broken. Everything needed to prevent it was already held: the
server publishes the enum in its own schema, and `ToolKinds.tools` keeps that
schema. It was simply never passed on, and never checked.

Two fixes, and the order matters. **Telling the model** is the one that stops
the wrong call being proposed at all. **Checking before we send** is what keeps
an approved card from spending a round trip to be told something we already
knew — and it answers in the server's own vocabulary, so the reply names the
six words rather than repeating that something went wrong.

The third fact this turned up is not a bug and has no fix: **Notion's server
publishes no way to delete a page.** Not one of its 45 tools deletes, trashes,
archives or removes anything. An agent asked to delete a page can only say so.
"""
from __future__ import annotations

import pytest

from chitragupta.agents.prompt import _argument_names
from chitragupta.connectors.mcp_source import (
    ToolKinds,
    argument_problem,
    schema_of,
)

#: `notion-update-page`, as Notion actually publishes it.
COMMANDS = ["update_properties", "update_content", "replace_content",
            "insert_content", "apply_template", "update_verification"]
SCHEMA = {
    "required": ["page_id", "command"],
    "properties": {
        "page_id": {"type": "string"},
        "command": {"type": "string", "enum": COMMANDS},
        "content": {"type": "string"},
        "verification_status": {"type": "string",
                                "enum": ["verified", "unverified"]},
    },
}


class _Tool:
    def __init__(self, name, schema):
        self.name, self.inputSchema = name, schema


# ── what the model is told ────────────────────────────────────────────────


def test_a_closed_set_is_spelled_out_for_the_model():
    """The fix for the reported case. `command*` alone is an invitation to
    remember an API; the six words are the answer."""
    line = _argument_names(type("R", (), {"parameters": SCHEMA})())

    assert "command*=" + "|".join(COMMANDS) in line


def test_free_text_arguments_are_left_alone():
    """Listing the *type* of every argument would drown the six that matter."""
    line = _argument_names(type("R", (), {"parameters": SCHEMA})())

    assert "page_id*," in line or line.endswith("page_id*")
    assert "page_id*=" not in line


def test_a_second_closed_set_on_the_same_tool_is_shown_too():
    line = _argument_names(type("R", (), {"parameters": SCHEMA})())

    assert "verification_status=verified|unverified" in line


def test_a_very_long_set_says_how_many_more():
    """A hundred-value enum would be a system prompt of its own."""
    many = {"properties": {"x": {"enum": [f"v{i}" for i in range(20)]}}}
    line = _argument_names(type("R", (), {"parameters": many})())

    assert "|+12" in line, line


# ── what is refused before it is sent ─────────────────────────────────────


def test_the_call_that_failed_is_refused_here_instead():
    problem = argument_problem(
        SCHEMA, {"page_id": "3bddf1be", "command": "update_attributes"})

    assert "update_attributes" in problem
    for word in COMMANDS:
        assert word in problem, "the reply has to name what IS allowed"


def test_a_missing_required_argument_says_which():
    assert "page_id" in argument_problem(SCHEMA, {"command": "update_content"})


def test_a_correct_call_is_not_refused():
    assert argument_problem(
        SCHEMA, {"page_id": "p", "command": "update_content"}) == ""


def test_an_argument_the_server_left_open_is_never_refused():
    """Only what the server itself closed. A home-grown validator would start
    refusing calls that would have worked, which is worse than the bug."""
    assert argument_problem(
        SCHEMA, {"page_id": "p", "command": "update_content",
                 "content": "anything at all", "unknown_extra": 7}) == ""


@pytest.mark.parametrize("schema", [None, {}, {"properties": "not a dict"}])
def test_a_server_that_publishes_no_schema_is_not_second_guessed(schema):
    """Most of the useful ones do. A server that does not still gets its call
    made — silence is not a refusal."""
    assert argument_problem(schema, {"whatever": 1}) == ""


def test_the_schema_is_found_on_the_tool_the_server_named():
    kinds = ToolKinds(tools=[_Tool("other", {"x": 1}),
                             _Tool("notion-update-page", SCHEMA)])

    assert schema_of(kinds, "notion-update-page") == SCHEMA
    assert schema_of(kinds, "nothing-like-it") is None
