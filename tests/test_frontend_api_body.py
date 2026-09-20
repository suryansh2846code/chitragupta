"""`api()` sends a JSON body that a FastAPI route can actually read.

Found while wiring a task row to `/api/open-browser`. `fetch` stringifies
whatever it is handed, so this:

    api("/api/open-browser", { method: "POST", body: { url } })

put the literal text `[object Object]` on the wire with no content type, and
the endpoint answered **422**. Verified against the real app, not reasoned
about.

It had been live at both sign-in buttons in `providers.js` since they were
written, and nothing showed it: the caller wraps the request in a `try` whose
`catch` falls back to `window.open`. The button worked every time, down the
path nobody meant to take — so the native open never happened and the failure
had no symptom.

The fix is in `api()` rather than at the call sites, because two forms of the
same call cannot both be right and the wrong one is the one that reads
better. Twenty-odd existing call sites spell out `JSON.stringify` and a
header; they must keep working untouched, which is most of what this file
checks.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"


@pytest.fixture(scope="module")
def sent():
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/api_body.mjs"), str(WEB / "app.js")],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    rows = json.loads(proc.stdout)["seen"]
    return {row["url"]: row for row in rows}


def test_a_plain_object_body_is_encoded_as_json(sent):
    """The bug. Without this the body is the seven-character string
    "[object Object]" and the route 422s."""
    row = sent["/plain-object"]

    assert row["bodyType"] == "string"
    assert json.loads(row["body"]) == {"url": "https://x.test/a"}
    assert row["contentType"] == "application/json"


def test_a_body_that_was_already_stringified_is_untouched(sent):
    """Most of the app writes it out by hand. Double-encoding would turn a
    JSON object into a JSON *string* and break every one of them."""
    row = sent["/already-a-string"]

    assert json.loads(row["body"]) == {"url": "https://x.test/b"}
    assert row["contentType"] == "application/json"


def test_a_request_with_no_body_gains_neither_body_nor_header(sent):
    """A bare POST is how most of the action endpoints are called."""
    row = sent["/no-body"]

    assert row["body"] is None
    assert row["contentType"] is None


def test_a_bare_get_still_works(sent):
    assert sent["/no-options"]["body"] is None


def test_form_data_is_left_alone(sent):
    """The browser sets its own multipart boundary. Encoding it as JSON, or
    stamping a content type on it, breaks the upload."""
    row = sent["/form"]

    assert row["contentType"] is None
    assert row["bodyType"] == "object"


def test_an_explicit_content_type_wins(sent):
    """A caller that named a type meant it."""
    assert sent["/explicit-type"]["contentType"] == "text/plain"
