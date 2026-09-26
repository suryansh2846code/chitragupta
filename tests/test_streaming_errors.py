"""When a streamed request fails, the user is told what the provider said.

`resp.raise_for_status()` builds its message **from the body**, and a response
being streamed has not read one — so calling it inside `httpx.stream(...)` on a
400 raises `ResponseNotRead: Attempted to access streaming response content,
without having called read()`.

That sentence is not a description of anything a user did. It replaced every
streaming failure in the app: a wrong model id, an expired sign-in and a rate
limit all arrived as the same complaint about `read()`, and none of them said
what had gone wrong. It reached a real user seven times in one afternoon, on an
automation that ran seven times and delivered nothing.

No network here. A transport that answers with the error the provider would
have sent is the whole fixture — and it is the only way to test this, because
the bug only appears on the failure path.
"""
from __future__ import annotations

import httpx
import pytest

from chitragupta.models.streaming import raise_for_status

#: What OpenAI actually says when the model id is wrong. The point of the fix
#: is that these words survive to the user.
REAL_ERROR = ('{"error": {"message": "The model `gpt-9` does not exist or you '
              'do not have access to it.", "type": "invalid_request_error"}}')


def streamed(status: int, body: str) -> httpx.Response:
    """A response in the state `httpx.stream` leaves one in: genuinely unread.

    The body is handed over as an **iterator**, which is what makes httpx treat
    it as a stream. Passing `text=` instead returns a response that is already
    read, and every test here then passes against the bug — which is exactly
    what happened on the first attempt at this file.
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, content=iter([body.encode()])))
    client = httpx.Client(transport=transport)
    request = client.build_request("POST", "https://example.test/v1/chat")
    return client.send(request, stream=True)


def test_the_providers_own_words_survive():
    resp = streamed(400, REAL_ERROR)
    with pytest.raises(httpx.HTTPStatusError) as raised:
        raise_for_status(resp)

    assert "gpt-9" in raised.value.response.text, (
        "the body was never read, so the error says nothing about the model")
    assert "do not have access" in raised.value.response.text


def test_the_httpx_internal_never_reaches_anybody():
    """The regression, named after the sentence the user actually saw."""
    resp = streamed(429, '{"error": {"message": "Rate limit reached."}}')
    try:
        raise_for_status(resp)
    except httpx.HTTPStatusError as exc:
        assert "without having called" not in str(exc)
        assert "Rate limit reached" in exc.response.text
    else:
        pytest.fail("a 429 did not raise")


def test_a_good_response_is_never_read():
    """The success path must not buffer the stream — reading it there would
    consume the very thing the caller is about to iterate.

    Asserted by watching for the call rather than by inspecting the response
    afterwards: a `Response` built with a body reads it during construction, so
    inspecting one would report it consumed whatever this code did.
    """
    resp = streamed(200, "data: hello\n\n")
    reads: list[bool] = []
    resp.read = lambda: reads.append(True) or b""    # after construction

    raise_for_status(resp)

    assert reads == [], "it buffered a response it was about to stream"


def test_a_body_that_cannot_be_read_still_raises():
    """A connection that dies mid-error must not turn into a *different*
    exception from the one every caller is catching. The body is a nicety; the
    failure is the message."""
    resp = streamed(500, "")

    def gone():
        raise httpx.ReadError("the connection went away")

    resp.read = gone

    with pytest.raises(httpx.HTTPStatusError):
        raise_for_status(resp)


# ── and every streaming call site uses it ──────────────────────────────────

@pytest.mark.parametrize("module", ["openai_compat", "anthropic", "chatgpt_auth"])
def test_no_streaming_call_site_raises_on_an_unread_body(module):
    """Three of them had the same bug, written three times. This is the check
    that there is not a fourth."""
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(f"chitragupta.models.{module}"))
    for block in source.split("httpx.stream(")[1:]:
        # Up to the end of the `with` body, which is where the mistake lives.
        head = block.split("\n\n")[0]
        assert "resp.raise_for_status()" not in head, (
            f"{module} raises on an unread streaming response")
