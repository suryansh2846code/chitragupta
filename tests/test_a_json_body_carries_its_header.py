"""Every JSON body this frontend sends says it is JSON.

`core.js::_encodeBody` adds `Content-Type: application/json` for a body that
is an **object**, and passes a string through untouched — deliberately, so
FormData and Blob still work. The trap is that `JSON.stringify` produces a
string, so `body: JSON.stringify({...})` with no header goes out as
`text/plain` and FastAPI answers **422** for a payload that is perfectly valid
JSON.

This has now shipped twice. `_encodeBody` exists because of the first one, on
`/api/open-browser`, where the caller's own `catch` fell back to
`window.open` — so the button worked, by accident, down the path nobody meant
to take. The second was the Connectors screen's **Disconnect**: the
confirmation appeared, OK did nothing at all, and there was no message,
because that handler had no catch to hide it and no header to avoid it.

Reproduced against the real app before this was written:

    POST /api/connectors/github/secret  '{"value": "x"}'  no header  -> 422
    POST /api/connectors/github/secret  '{"value": "x"}'  header     -> 200

A comment cannot catch the third one. This does: every call site that
stringifies a body must either be given an object instead, or spell the header
out itself.
"""
from __future__ import annotations

import pathlib
import re

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "chitragupta" / "web"

#: `body: JSON.stringify(` and whatever follows, up to the end of the call.
_STRINGIFIED = re.compile(r"body:\s*JSON\.stringify\(")


def _call_sites(source: str) -> list[tuple[int, str]]:
    """Each stringified body, with enough of its call around it to judge."""
    out = []
    for m in _STRINGIFIED.finditer(source):
        start = source.rfind("api(", max(0, m.start() - 600), m.start())
        if start == -1:
            start = max(0, m.start() - 400)
        line = source.count("\n", 0, m.start()) + 1
        out.append((line, source[start:m.end() + 300]))
    return out


@pytest.mark.parametrize(
    "name", sorted(p.name for p in WEB.glob("*.js")))
def test_no_stringified_body_goes_out_without_its_header(name):
    source = (WEB / name).read_text()
    for line, call in _call_sites(source):
        if name == "core.js" and "_encodeBody" in call:
            continue                      # the encoder itself, which sets it
        assert "Content-Type" in call, (
            f"{name}:{line} stringifies a JSON body without declaring it. "
            "The browser will label it text/plain and the endpoint will "
            "answer 422. Pass the object and let _encodeBody do it, or spell "
            "the header out.")


def test_the_encoder_still_only_helps_objects():
    """The behaviour this whole file is about. If `_encodeBody` ever started
    encoding strings too, the guard above would be checking nothing — so the
    assumption is pinned rather than assumed."""
    source = (WEB / "core.js").read_text()
    body = source[source.index("function _encodeBody"):]
    body = body[:body.index("\n}")]

    assert 'typeof o.body !== "object"' in body
    assert '"Content-Type": "application/json"' in body


def test_the_disconnect_button_sends_an_object():
    """The one that shipped broken. Named directly because a regression here
    is silent — the dialog still appears and OK still does nothing."""
    source = (WEB / "connectors.js").read_text()
    # The handler, not the markup — `data-cnoff` appears in both, and the
    # button's own HTML is the one that carries no fetch at all.
    call = source[source.index('querySelectorAll("[data-cnoff]")'):]
    call = call[:call.index("loadBrain()")]
    # Comments stripped first. The note explaining this bug naturally contains
    # the words the assertion looks for, and a test that reads its own
    # documentation as if it were code is a test that cannot be commented.
    code = "\n".join(re.sub(r"//.*$", "", line) for line in call.splitlines())

    assert "JSON.stringify" not in code
    assert 'body: { value: "" }' in code
    assert "catch" in code, "a failure here must say so, not look like nothing"
