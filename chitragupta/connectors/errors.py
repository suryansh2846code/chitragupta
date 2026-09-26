"""One error taxonomy for every external system.

Before this, a connector failure was `result.errors.append(str(exc))`. Fifteen
connectors, fifteen vocabularies: `custom_api.py` said `"API error 500"`,
`slack.py` said `"Slack is rate-limiting us"`, `gmail.py` handed the user a
`googleapiclient` `HttpError` repr, and nothing anywhere could answer the only
two questions a caller actually has — **should I try again, and should I say
something to the user?**

`models/errors.py` solved exactly this problem for LLM providers and is a
**sibling package** this one may not import (`docs/ARCHITECTURE.md` §3.2). It
is also a different vocabulary: `CONTEXT_TOO_LONG` and `MODEL_NOT_ENTITLED` are
not things GitHub says, and `CONFLICT` and `NOT_FOUND` are not things a model
provider says. Two taxonomies because there are genuinely two, not because the
import was inconvenient — and the rule they share is the one worth sharing:
**translate, never dump.**

Classify once, at the boundary:

    err = classify_http("gmail", resp.status, body, headers=resp.headers)
    if err.retryable:
        ...

## What the agent is allowed to see

The vendor's own sentence about the user's own request is the actionable part
and is kept (`mcp_source._refusal` established that). Everything that could
carry a credential is stripped first, by `core/redact.py` plus the two shapes
that only exist at this boundary: a token in a URL's query string, and an
`Authorization` header echoed back in an error body. `detail` is the only field
that carries provider text and it is redacted on construction, so there is no
path that builds one and forgets.
"""
from __future__ import annotations

import re
import socket
import ssl
import urllib.error
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from ..core.redact import redact

#: Provider detail is quoted to the user and to the agent, so it is bounded.
#: A vendor that returns a 4KB HTML error page is not owed a paragraph of the
#: transcript.
MAX_DETAIL = 200


class ConnectorErrorKind(StrEnum):
    """What went wrong, in terms every connector shares."""

    #: The credential is missing, invalid, or expired. Refreshing may fix it.
    AUTHENTICATION = "authentication"
    #: The credential is fine and does not carry the permission. Refreshing
    #: cannot fix it, and retrying is how a 403 becomes a rate-limit.
    AUTHORIZATION = "authorization"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    VALIDATION = "validation"
    NETWORK = "network"
    TIMEOUT = "timeout"
    #: The vendor broke, not us.
    PROVIDER = "provider"

    #: The two fallbacks, for when the shape is known and the cause is not.
    #: Every caller reads `.retryable` rather than testing for these — they
    #: exist so that "we do not know what this was, but it is worth another
    #: go" is sayable, which is different from `UNKNOWN`.
    TRANSIENT = "transient"
    PERMANENT = "permanent"

    UNKNOWN = "unknown"


#: Kinds worth trying again *unchanged*. Everything else needs something to
#: change first — a refreshed token, a corrected request, a different scope.
#:
#: `AUTHENTICATION` is deliberately **not** here even though a refresh often
#: fixes it: retrying the same expired credential is the loop that locks
#: accounts out. `auth.py` refreshes and then calls again, which is one attempt
#: with a *different* credential, not a retry.
_RETRYABLE = frozenset({
    ConnectorErrorKind.RATE_LIMITED,
    ConnectorErrorKind.NETWORK,
    ConnectorErrorKind.TIMEOUT,
    ConnectorErrorKind.PROVIDER,
    ConnectorErrorKind.TRANSIENT,
})

#: Kinds that mean the local record of a thing is now wrong. A 404 on a
#: resource we hold is not a failure to be retried — it is the provider telling
#: us the thing is gone, and `events.py` turns it into a tombstone.
_MEANS_GONE = frozenset({ConnectorErrorKind.NOT_FOUND})


_STATUS: dict[int, ConnectorErrorKind] = {
    400: ConnectorErrorKind.VALIDATION,
    401: ConnectorErrorKind.AUTHENTICATION,
    402: ConnectorErrorKind.AUTHORIZATION,
    403: ConnectorErrorKind.AUTHORIZATION,
    404: ConnectorErrorKind.NOT_FOUND,
    405: ConnectorErrorKind.VALIDATION,
    408: ConnectorErrorKind.TIMEOUT,
    409: ConnectorErrorKind.CONFLICT,
    410: ConnectorErrorKind.NOT_FOUND,
    413: ConnectorErrorKind.VALIDATION,
    422: ConnectorErrorKind.VALIDATION,
    429: ConnectorErrorKind.RATE_LIMITED,
}

#: What the user reads. One sentence, saying what happened and what to do —
#: never the status code, which is an internal (`/CLAUDE.md`, Never surface an
#: internal). `{label}` is the connector's display name.
_SENTENCE: dict[ConnectorErrorKind, str] = {
    ConnectorErrorKind.AUTHENTICATION:
        "{label} needs you to sign in again.",
    ConnectorErrorKind.AUTHORIZATION:
        "{label} did not allow that. Check what you granted it, then try again.",
    ConnectorErrorKind.RATE_LIMITED:
        "{label} is asking us to slow down. This will pick up on its own.",
    ConnectorErrorKind.NOT_FOUND:
        "That is no longer in {label}.",
    ConnectorErrorKind.CONFLICT:
        "{label} already changed that. Take another look before trying again.",
    ConnectorErrorKind.VALIDATION:
        "{label} could not accept that request.",
    ConnectorErrorKind.NETWORK:
        "Could not reach {label}. Check your connection.",
    ConnectorErrorKind.TIMEOUT:
        "{label} took too long to answer.",
    ConnectorErrorKind.PROVIDER:
        "{label} is having trouble at their end. This will pick up on its own.",
    ConnectorErrorKind.TRANSIENT:
        "{label} could not answer just now. This will pick up on its own.",
    ConnectorErrorKind.PERMANENT:
        "{label} could not do that.",
    ConnectorErrorKind.UNKNOWN:
        "Something went wrong reaching {label}.",
}


#: A token riding in a URL's query string. `custom_api.py` puts one there when
#: `auth_type == "query"`, and a failed request echoes the whole URL.
_URL_SECRET = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|token|key|secret|password|auth)=)"
    r"[^&\s\"']+", re.I)

#: A credential named and then assigned — an `Authorization` header quoted back
#: in an error body, or a bare `token=…` in a client library's exception text.
#:
#: `core/redact.py` covers `access_token`, `api key` and `secret` but not a bare
#: `token`, which is what `slack_sdk` and `notion-client` put in their error
#: strings. That gap is this boundary's to close: `redact()` is tuned for
#: *ingested content*, where over-matching costs a memory, and here it costs a
#: word in a log line.
_HEADER_SECRET = re.compile(
    r"((?:authorization|x-api-key|x-auth-token|private-token|bearer|"
    r"token|api[_-]?key|apikey|refresh[_-]?token|client[_-]?secret)"
    r"['\"]?\s*[:=]\s*['\"]?)[^\s,'\"}\]]+", re.I)


def scrub(text: str) -> str:
    """Everything a provider said, with anything that could be a credential out.

    Three passes, because they catch different things and no single pattern
    catches all three: `core/redact.py` knows the *shapes* of secrets (`ghp_…`,
    a JWT, a private key), and the two here know the *places* a secret sits at
    this boundary specifically — a query string we built, and a header the
    vendor echoed.

    Runs on construction of every `ConnectorError`, so there is no path that
    builds one and forgets.
    """
    if not text:
        return ""
    out = _URL_SECRET.sub(r"\1[REDACTED]", text)
    out = _HEADER_SECRET.sub(r"\1[REDACTED]", out)
    return redact(out)


def _tidy(text: str) -> str:
    """One line, bounded, no markup. What a person can read in a row."""
    flat = " ".join((text or "").split())
    return flat[:MAX_DETAIL]


@dataclass(frozen=True)
class ConnectorError(Exception):
    """One failure, classified.

    **Both a record and an exception**, which is a deliberate difference from
    `models/errors.ProviderError`. That one is never raised, because `chat()`
    is documented to return a `ChatResult` and never throw. A connector call is
    an ordinary function that can fail, and `retry.with_retries` has to raise
    *something* when it gives up — raising this rather than the vendor's own
    exception is what stops a `googleapiclient` repr reaching a user.

    Constructed through `classify_*` rather than directly, so `detail` cannot
    reach a caller unscrubbed.
    """

    kind: ConnectorErrorKind
    connector: str
    #: User-facing and actionable. Never carries a status code or an internal.
    message: str
    status: int | None = None
    #: Short, scrubbed provider detail. For the agent and the log, not the row.
    detail: str = ""
    #: When the provider said to come back, ISO-8601. "" when it did not say.
    retry_at: str = ""
    #: The thing that failed, where one request was about one thing.
    resource: str = ""

    @property
    def retryable(self) -> bool:
        return self.kind in _RETRYABLE

    @property
    def means_gone(self) -> bool:
        """Did the provider say the thing no longer exists?

        Not a failure to retry — a fact to record. `events.py` turns this into
        a tombstone rather than leaving a deleted file in the brain forever.
        """
        return self.kind in _MEANS_GONE

    @property
    def needs_reauth(self) -> bool:
        """Should the connection move to REAUTH_REQUIRED and its jobs pause?

        Authentication only. A 403 is a *scope* problem: signing in again
        produces the same credential with the same permissions, so sending the
        user round the OAuth loop teaches them the app is broken.
        """
        return self.kind is ConnectorErrorKind.AUTHENTICATION

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "connector": self.connector,
                "message": self.message, "status": self.status,
                "detail": self.detail, "retryable": self.retryable,
                "retry_at": self.retry_at, "resource": self.resource}

    def __str__(self) -> str:
        return self.message


def _build(kind: ConnectorErrorKind, connector: str, *, label: str = "",
           status: int | None = None, detail: str = "", retry_at: str = "",
           resource: str = "") -> ConnectorError:
    shown = label or connector or "this connector"
    return ConnectorError(
        kind=kind, connector=connector,
        message=_SENTENCE[kind].format(label=shown),
        status=status, detail=_tidy(scrub(detail)),
        retry_at=retry_at, resource=resource)


def parse_retry_after(value: Any, *, now: datetime | None = None) -> str:
    """`Retry-After` as an instant, from either spelling the RFC allows.

    Returned as an ISO instant rather than the header's own words, for the
    reason `models/errors.retry_at` gives: *"120"* is meaningless once it has
    been stored, and an HTTP-date is meaningless to a reader in another
    timezone. An instant survives both.

    Anything unparseable returns "" — a provider that sent nonsense has not
    told us when to come back, and inventing a number is worse than backing off
    on our own schedule.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    moment = now or datetime.now(UTC)
    if raw.isdigit():
        seconds = int(raw)
        # A vendor asking us to wait a week is a vendor we stop asking. The cap
        # is on what we *store*: the connection still reports rate-limited, and
        # the next scheduled pass tries again rather than a sleeping thread.
        return (moment + timedelta(seconds=min(seconds, 3600))).isoformat()
    from email.utils import parsedate_to_datetime
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return ""
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.isoformat() if when > moment else ""


def _header(headers: Any, name: str) -> str:
    """One header, however the caller's object spells lookup.

    `http.client.HTTPMessage`, `requests`' `CaseInsensitiveDict` and a plain
    dict all reach this, and only the first two are case-insensitive.
    """
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is not None:
        found = getter(name) or getter(name.lower()) or getter(name.title())
        if found:
            return str(found)
    return ""


def classify_http(connector: str, status: int, body: str | None = None, *,
                  label: str = "", headers: Any = None,
                  resource: str = "") -> ConnectorError:
    """An HTTP response that was not a success, as a `ConnectorError`."""
    kind = _STATUS.get(status)
    if kind is None:
        kind = (ConnectorErrorKind.PROVIDER if status >= 500
                else ConnectorErrorKind.PERMANENT if status >= 400
                # A 3xx reaching here means a redirect nobody followed, and a
                # 2xx means the caller classified a success. Neither is
                # something to retry, and neither is something to guess about.
                else ConnectorErrorKind.UNKNOWN)
    retry_at = ""
    if kind in (ConnectorErrorKind.RATE_LIMITED, ConnectorErrorKind.PROVIDER):
        retry_at = (parse_retry_after(_header(headers, "Retry-After"))
                    or parse_retry_after(_header(headers, "X-RateLimit-Reset")))
    return _build(kind, connector, label=label, status=status,
                  detail=_detail_from(body), retry_at=retry_at,
                  resource=resource)


def classify_exception(connector: str, exc: BaseException, *, label: str = "",
                       resource: str = "") -> ConnectorError:
    """Whatever a client library raised, as a `ConnectorError`.

    Ordered most-specific first. `urllib.error.HTTPError` is a subclass of
    `URLError`, which is why it is tested before it — reversed, every 429 would
    have been classified as a network failure and retried on the wrong
    schedule.
    """
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            # A body we cannot read is a body we do without. The status is the
            # part that classifies, and failing here would turn a clean 403
            # into an unknown.
            body = str(getattr(exc, "reason", "") or "")
        return classify_http(connector, exc.code, body, label=label,
                             headers=getattr(exc, "headers", None),
                             resource=resource)
    if isinstance(exc, TimeoutError | socket.timeout):
        return _build(ConnectorErrorKind.TIMEOUT, connector, label=label,
                      detail=str(exc), resource=resource)
    if isinstance(exc, ssl.SSLError):
        # Not a network blip: a certificate that does not verify is a reason to
        # stop, and retrying it is how a warning becomes a habit.
        return _build(ConnectorErrorKind.PERMANENT, connector, label=label,
                      detail=f"secure connection failed: {exc}",
                      resource=resource)
    if isinstance(exc, urllib.error.URLError | ConnectionError | socket.gaierror):
        return _build(ConnectorErrorKind.NETWORK, connector, label=label,
                      detail=str(getattr(exc, "reason", "") or exc),
                      resource=resource)
    # A client library's own exception type, which we cannot enumerate. Its
    # *status*, if it carries one, is worth more than its class name.
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and 400 <= status < 600:
        return classify_http(connector, status, str(exc), label=label,
                             headers=getattr(exc, "headers", None),
                             resource=resource)
    return _build(ConnectorErrorKind.UNKNOWN, connector, label=label,
                  detail=str(exc), resource=resource)


def _detail_from(body: str | None) -> str:
    """The human-readable sentence out of a provider's error body.

    Providers disagree on shape and always have: `{"error": {"message": …}}`,
    `{"message": …}`, `{"errors": [{"detail": …}]}`, or plain text. Tried in
    order; the fallback is the body itself, which `scrub` and `_tidy` then make
    safe and short. Never raises — a body we cannot parse costs a detail, not a
    classification.
    """
    raw = (body or "").strip()
    if not raw:
        return ""
    if raw[:1] not in "{[":
        return raw
    import json
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw
    return _sentence_in(parsed) or raw


def _sentence_in(parsed: Any, depth: int = 0) -> str:
    """Walk the usual places a provider puts its message. Bounded depth, so a
    deeply nested or self-referential payload cannot spin."""
    if depth > 4:
        return ""
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, list):
        for item in parsed[:3]:
            found = _sentence_in(item, depth + 1)
            if found:
                return found
        return ""
    if isinstance(parsed, dict):
        for key in ("message", "detail", "error_description", "description",
                    "reason", "title"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value
        for key in ("error", "errors", "data"):
            if key in parsed:
                found = _sentence_in(parsed[key], depth + 1)
                if found:
                    return found
    return ""
