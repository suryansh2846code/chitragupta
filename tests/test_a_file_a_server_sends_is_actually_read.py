"""A tool's reply is read wherever the server put it, not only in `.text`.

Reported from a transcript. An agent asked to improve a README said it had hit
a wall: *"the GitHub file-read only gave me the README's SHA, not its actual
text … I won't blindly overwrite it without knowing what's there."* That was
exactly the right call, and it was working from a reply we had gutted.

GitHub's `get_file_contents` answers with **two** blocks:

    [0] text      "successfully downloaded text file (SHA: 911fd1a…)"
    [1] resource  the entire file, on `block.resource.text`

MCP allows a tool to answer with an embedded resource, and `_records()` read
only `block.text`. So the sentence survived and the file was thrown away —
7,806 characters of README reduced to a status line and a hash.

The same omission ran through `sync()`, which shares this reader: any server
answering in resources had been contributing status lines to the brain and
nothing else. That is a second, silent half of `docs/AUDIT.md` A13.

Binary resources are named rather than inlined — base64 handed to a model is a
wall of noise, and saying what it is beats pretending it is prose.
"""
from __future__ import annotations

from chitragupta.connectors.mcp_source import _block_text, _records


class _Text:
    type = "text"

    def __init__(self, text): self.text = text


class _Resource:
    type = "resource"

    def __init__(self, **kw):
        self.text = None
        self.resource = type("R", (), kw)()


class _Reply:
    def __init__(self, *blocks):
        self.content = list(blocks)
        self.structured_content = None
        self.is_error = False


#: What GitHub actually sends back, shape for shape.
SHA_LINE = "successfully downloaded text file (SHA: 911fd1a)"
README = "# Chitragupta\n\nThe local-first AI workspace.\n"


def test_the_file_survives_the_read():
    """The reported case. Both blocks, and the one that matters is the second."""
    got = _records(_Reply(_Text(SHA_LINE), _Resource(text=README)))

    assert README in got, "the file was dropped and only the SHA kept"


def test_the_status_line_is_kept_too():
    """It carries the SHA, which is what a later write needs to not clobber."""
    got = _records(_Reply(_Text(SHA_LINE), _Resource(text=README)))

    assert any("911fd1a" in str(r) for r in got)


def test_a_plain_text_block_is_unchanged():
    """The common shape must not regress for the uncommon one."""
    assert _block_text(_Text("just prose")) == "just prose"


def test_a_resource_is_read_from_where_it_lives():
    assert _block_text(_Resource(text=README)) == README


def test_a_binary_resource_is_named_not_inlined():
    """base64 handed to a model is a wall of noise that costs real tokens and
    says nothing. What it is and where it is beats pretending it is prose."""
    got = _block_text(_Resource(blob="AAAA", uri="file:///x.png",
                                mime_type="image/png"))

    assert "AAAA" not in got
    assert "image/png" in got and "x.png" in got


def test_a_block_with_nothing_in_it_is_skipped():
    assert _block_text(_Text("")) == ""
    assert _block_text(_Resource()) == ""
    assert _records(_Reply(_Text(""), _Resource())) == []


def test_a_resource_only_reply_is_not_empty():
    """A server that answers with nothing but the file. Reading `.text` alone
    made this an empty result, which `sync()` reports as "returned nothing"."""
    assert _records(_Reply(_Resource(text=README))) == [README]
