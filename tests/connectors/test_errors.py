"""Every external system fails in the same vocabulary, and none of them leak.

Two questions a caller has about a failure, and before this taxonomy neither
was answerable: **should I try again**, and **what do I tell the user**.

The third property is the one with teeth: a `ConnectorError` is the thing that
reaches the agent, the transcript and the log, so it is also the last place a
credential could escape. `scrub` runs on construction rather than at the call
sites, and the tests below go through `classify_*` for exactly that reason — a
redaction that only works when someone remembers to call it is not one.
"""
from __future__ import annotations

import socket
import ssl
import urllib.error
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest

from chitragupta.connectors.errors import (
    ConnectorErrorKind,
    classify_exception,
    classify_http,
    parse_retry_after,
    scrub,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def http_error(code, body=b"", headers=None):
    return urllib.error.HTTPError(
        "https://api.example.test/things", code, "nope",
        headers or {}, BytesIO(body))


# ── the taxonomy ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("status,kind", [
    (400, ConnectorErrorKind.VALIDATION),
    (401, ConnectorErrorKind.AUTHENTICATION),
    (403, ConnectorErrorKind.AUTHORIZATION),
    (404, ConnectorErrorKind.NOT_FOUND),
    (409, ConnectorErrorKind.CONFLICT),
    (410, ConnectorErrorKind.NOT_FOUND),
    (422, ConnectorErrorKind.VALIDATION),
    (429, ConnectorErrorKind.RATE_LIMITED),
    (500, ConnectorErrorKind.PROVIDER),
    (502, ConnectorErrorKind.PROVIDER),
    (503, ConnectorErrorKind.PROVIDER),
])
def test_a_status_classifies_the_same_way_for_every_vendor(status, kind):
    assert classify_http("gmail", status).kind is kind


def test_an_unrecognised_4xx_is_permanent_not_unknown():
    """418 is not something to retry. `PERMANENT` says that; `UNKNOWN` would
    leave the retry decision to whoever read it next."""
    error = classify_http("gmail", 418)

    assert error.kind is ConnectorErrorKind.PERMANENT
    assert not error.retryable


# ── retryability, which is the whole point ────────────────────────────────


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_the_failures_that_pass_on_their_own_are_retryable(status):
    assert classify_http("slack", status).retryable


@pytest.mark.parametrize("status,why", [
    (400, "the request is wrong; sending it again does not fix it"),
    (403, "the same credential will be refused identically"),
    (404, "the thing is gone; that is news, not a failure"),
    (409, "the state moved; retrying blindly overwrites somebody"),
])
def test_the_failures_that_need_something_to_change_are_not(status, why):
    assert not classify_http("github", status).retryable, why


def test_an_expired_credential_is_not_retried_it_is_reauthenticated():
    """Retrying the same expired credential is the loop that locks accounts
    out. A refresh is a different credential, which is `connections.py`'s job."""
    error = classify_http("gmail", 401)

    assert not error.retryable
    assert error.needs_reauth


def test_a_scope_problem_does_not_send_the_user_round_oauth_again():
    """Signing in again produces the same credential with the same
    permissions. Offering the loop teaches the user the app is broken."""
    error = classify_http("gdrive", 403)

    assert not error.needs_reauth
    assert error.kind is ConnectorErrorKind.AUTHORIZATION


def test_a_deleted_resource_is_a_fact_to_record():
    """Not a failure to repeat — `events.py` turns this into a tombstone
    rather than leaving a deleted file in the brain forever."""
    error = classify_http("gdrive", 404, resource="file:abc")

    assert error.means_gone
    assert error.resource == "file:abc"
    assert not classify_http("gdrive", 500).means_gone


# ── Retry-After ───────────────────────────────────────────────────────────


def test_a_retry_after_in_seconds_becomes_an_instant():
    """Stored as a moment, not the provider's words: "120" means nothing once
    it has been written down, and an HTTP-date means nothing in another
    timezone."""
    when = parse_retry_after("120", now=NOW)

    assert datetime.fromisoformat(when) == NOW + timedelta(seconds=120)


def test_a_retry_after_as_a_date_is_understood_too():
    when = parse_retry_after("Sat, 26 Sep 2026 12:05:00 GMT", now=NOW)

    assert datetime.fromisoformat(when) == NOW + timedelta(minutes=5)


def test_an_absurd_retry_after_is_capped_not_obeyed_literally():
    """A vendor asking for a week is a vendor we stop asking. The cap is on
    what we store — the next scheduled pass is still the retry."""
    when = parse_retry_after("604800", now=NOW)

    assert datetime.fromisoformat(when) <= NOW + timedelta(hours=1)


@pytest.mark.parametrize("raw", ["", None, "soon", "-5", "not a date"])
def test_a_retry_after_we_cannot_read_is_not_invented(raw):
    """Inventing a number is worse than backing off on our own schedule."""
    assert parse_retry_after(raw, now=NOW) == ""


def test_a_rate_limit_carries_the_moment_it_lifts():
    error = classify_http("slack", 429, headers={"Retry-After": "30"})

    assert error.retry_at
    assert error.retryable


def test_a_400_does_not_pick_up_a_retry_after():
    """Only the kinds that mean "come back later" read the header. A
    validation error carrying a retry time would invite exactly the retry the
    classification exists to prevent."""
    error = classify_http("slack", 400, headers={"Retry-After": "30"})

    assert error.retry_at == ""


# ── translate, never dump ─────────────────────────────────────────────────


def test_the_message_never_contains_a_status_code():
    """"Never surface an internal" — a user reading "API error 503" learns
    nothing they can act on."""
    for status in (400, 401, 403, 404, 429, 500):
        message = classify_http("gmail", status, label="Gmail").message
        assert str(status) not in message
        assert message.strip()


def test_the_message_names_the_connector_the_user_knows():
    assert "Gmail" in classify_http("gmail", 401, label="Gmail").message


def test_the_vendors_own_sentence_is_kept_as_detail():
    """The actionable part of a refusal is what the vendor said about the
    user's own request — `mcp_source._refusal` established that."""
    body = b'{"error": {"message": "Label name already exists"}}'

    error = classify_http("gmail", 409, body.decode())

    assert error.detail == "Label name already exists"


@pytest.mark.parametrize("body,expected", [
    ('{"message": "flat"}', "flat"),
    ('{"errors": [{"detail": "in a list"}]}', "in a list"),
    ('{"error_description": "oauth style"}', "oauth style"),
    ('{"data": {"error": {"title": "nested twice"}}}', "nested twice"),
    ("just text", "just text"),
])
def test_the_sentence_is_found_wherever_the_vendor_put_it(body, expected):
    assert classify_http("x", 400, body).detail == expected


def test_a_body_that_is_not_json_costs_a_detail_not_a_classification():
    error = classify_http("x", 500, "<html><body>502 Bad Gateway</body></html>")

    assert error.kind is ConnectorErrorKind.PROVIDER
    assert error.detail


def test_a_self_referential_payload_cannot_spin():
    """Bounded depth, so a provider payload that nests forever is a missing
    detail rather than a hung sync."""
    deep = '{"error":' * 40 + '{"message": "x"}' + "}" * 40

    assert classify_http("x", 400, deep).detail


def test_a_detail_is_one_bounded_line():
    """A vendor that returns a 4KB HTML error page is not owed a paragraph of
    the transcript."""
    error = classify_http("x", 500, "word " * 500)

    assert len(error.detail) <= 200
    assert "\n" not in error.detail


# ── nothing leaks ─────────────────────────────────────────────────────────


#: Shapes `core/redact.py` recognises, written so none of them can read as a
#: live credential. The Slack one is the reason this note exists: spelled the
#: way a real token is laid out — `xoxb-<digits>-<alnum>` — GitHub's push
#: protection rejects the whole branch, which is the scanner doing its job. It
#: still has to match the redactor's own pattern (`xox[baprs]-` then ten or
#: more of `[A-Za-z0-9-]`), or this test would pass for the wrong reason: a
#: fixture nothing recognises is redacted trivially and proves nothing.
CREDENTIAL_SHAPES = [
    "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "xoxb-EXAMPLE-NOT-A-REAL-TOKEN",
    "ntn_abcdefghijklmnopqrstuvwxyz123456",
    "lin_api_abcdefghijklmnopqrstuvwxyz12",
    "sk-abcdefghijklmnopqrstuvwxyz0123456789",
]


@pytest.mark.parametrize("secret", CREDENTIAL_SHAPES)
def test_a_token_in_a_provider_body_never_reaches_the_caller(secret):
    error = classify_http("x", 401, f'{{"message": "bad token {secret}"}}')

    assert secret not in error.detail
    assert secret not in error.message
    assert secret not in str(error.as_dict())


def test_a_token_in_a_query_string_is_stripped():
    """`custom_api.py` puts one there when `auth_type == "query"`, and a failed
    request echoes the whole URL back.

    The surrounding request is kept — an error that redacts itself into
    uselessness is one nobody can act on — but only up to the credential:
    `core/redact.py`'s generic rule swallows to the end of the token, and it is
    right to. Precision over recall is its stated trade, and the cost here is a
    query parameter in a log line rather than a live key in the brain.
    """
    body = "GET https://api.test/v1/items?api_key=SUPERSECRETVALUE&page=2 failed"

    error = classify_http("custom:acme", 500, body)

    assert "SUPERSECRETVALUE" not in error.detail
    assert "https://api.test/v1/items" in error.detail


def test_an_authorization_header_echoed_back_is_stripped():
    body = '{"message": "rejected", "sent": {"Authorization": "Bearer abc123def456"}}'

    error = classify_http("x", 401, body)

    assert "abc123def456" not in str(error.as_dict())


def test_scrub_is_applied_by_construction_not_by_convention():
    """Every field that can carry provider text goes through it, so there is
    no path that builds an error and forgets."""
    error = classify_exception(
        "x", ValueError("token=hunter2secretvalue x-api-key: abcdefghij"))

    assert "hunter2secretvalue" not in error.detail
    assert "abcdefghij" not in error.detail


def test_scrub_keeps_an_ordinary_sentence_intact():
    """Precision matters in both directions: an over-eager redactor makes
    every error useless."""
    assert scrub("Label name already exists") == "Label name already exists"


# ── exceptions ────────────────────────────────────────────────────────────


def test_an_http_error_is_classified_before_its_parent_url_error():
    """`HTTPError` subclasses `URLError`. Tested in the wrong order, every 429
    would be a network failure and retried on the wrong schedule."""
    error = classify_exception("slack", http_error(429))

    assert error.kind is ConnectorErrorKind.RATE_LIMITED


@pytest.mark.parametrize("exc,kind", [
    (TimeoutError("took too long"), ConnectorErrorKind.TIMEOUT),
    (socket.gaierror("name resolution"), ConnectorErrorKind.NETWORK),
    (ConnectionResetError("reset by peer"), ConnectorErrorKind.NETWORK),
    (urllib.error.URLError("unreachable"), ConnectorErrorKind.NETWORK),
])
def test_the_transport_failures_classify_as_transport_failures(exc, kind):
    assert classify_exception("x", exc).kind is kind


def test_a_certificate_failure_is_permanent_not_a_blip():
    """Retrying a certificate that does not verify is how a warning becomes a
    habit."""
    error = classify_exception("x", ssl.SSLError("certificate verify failed"))

    assert not error.retryable
    assert error.kind is ConnectorErrorKind.PERMANENT


def test_a_client_librarys_own_exception_is_read_for_its_status():
    """`googleapiclient` and `slack_sdk` raise their own types. The class name
    tells us nothing; the status it carries tells us everything."""
    class VendorError(Exception):
        status_code = 429

    assert classify_exception("x", VendorError("slow down")).kind \
        is ConnectorErrorKind.RATE_LIMITED


def test_an_exception_we_cannot_place_is_unknown_and_not_retried():
    """Unknown is not "probably transient" — a loop on an unclassifiable
    failure is a loop that never ends."""
    error = classify_exception("x", RuntimeError("something odd"))

    assert error.kind is ConnectorErrorKind.UNKNOWN
    assert not error.retryable


def test_a_body_that_cannot_be_read_still_classifies():
    """The status is the part that classifies. Failing here would turn a clean
    403 into an unknown."""
    class Unreadable(urllib.error.HTTPError):
        def read(self, *a, **kw):
            raise OSError("stream already consumed")

    error = classify_exception("x", Unreadable("u", 403, "no", {}, None))

    assert error.kind is ConnectorErrorKind.AUTHORIZATION
