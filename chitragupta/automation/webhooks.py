"""Turning a provider's HTTP callback into an `Event`, and nothing more.

**There is one event pipeline and this is not a second one.** A webhook ends up
in exactly the same place a connector sync does:

    provider → verify signature → normalise → engine.ingest → router → run

Everything after `normalise` already existed. What is here is the part that is
specific to being called over HTTP by somebody else: proving the caller is who
they claim, and deriving an identity stable enough that the same delivery twice
is recognised as one event.

**Verifiers are a registry, keyed by provider**, for the same reason triggers
are: `github` and `stripe` sign differently, and neither belongs in the router.
Adding a provider is a `Verifier` and a dictionary entry.

The rule that shapes the rest: **a provider that supports signing must be
signed.** Falling back to "unsigned is fine if we cannot find a secret" turns
the whole endpoint into an open door that anyone who knows the URL can push
events through — and those events start automations that send email.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..core.events import Event
from ..core.provenance import Trust
from ..log import get_logger

log = get_logger(__name__)

#: Largest body accepted. A webhook is a notification, not a file transfer, and
#: an unbounded one is a way to make the app allocate as much memory as the
#: caller likes before anything has authenticated them.
MAX_BODY_BYTES = 256 * 1024

#: How far out of date a signed timestamp may be. Bounds replay: a valid
#: signature captured today must not still be usable next week.
MAX_CLOCK_SKEW = timedelta(minutes=5)


class WebhookError(Exception):
    """Refused, with the status the caller should get and a reason for the log.

    The *caller* gets the status and a bare word; the reason stays here. Telling
    an unauthenticated caller which part of their signature was wrong is helping
    them fix it.
    """

    def __init__(self, status: int, public: str, reason: str = "") -> None:
        super().__init__(reason or public)
        self.status = status
        self.public = public
        self.reason = reason or public


@dataclass(frozen=True)
class Delivery:
    """One HTTP callback, before anything has been believed about it."""

    provider: str
    body: bytes
    headers: dict[str, str]

    def header(self, name: str) -> str:
        """Case-insensitively, because HTTP header case is not meaningful and
        every provider picks a different one."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return str(value)
        return ""


class Verifier(Protocol):
    """How one provider proves a delivery came from them."""

    provider: str
    #: Does this provider sign at all? A `False` here is a statement about the
    #: provider, and the only thing that may let an unsigned delivery through.
    signs: bool

    def secret_key(self) -> str: ...

    def check(self, delivery: Delivery, secret: str) -> None:
        """Raise `WebhookError` if the delivery is not authentic."""

    def normalise(self, delivery: Delivery, payload: dict) -> Event:
        """The provider's payload as an `Event`. No decisions, just shape."""


_REGISTRY: dict[str, Verifier] = {}


def register(verifier: Verifier) -> Verifier:
    _REGISTRY[verifier.provider] = verifier
    return verifier


def known() -> list[str]:
    return sorted(_REGISTRY)


def get(provider: str) -> Verifier | None:
    return _REGISTRY.get(provider)


def _event_time(raw: Any) -> str:
    """A provider's timestamp, or now.

    Providers send seconds, milliseconds and ISO strings, and a webhook whose
    time is unreadable must still be processed — the timestamp is metadata, not
    the event. Falling back to now is right: it is when *we* learned of it.
    """
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
        seconds = float(raw)
        if seconds > 1e11:                    # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return datetime.now(UTC).isoformat()
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC).isoformat()
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).isoformat()
    return datetime.now(UTC).isoformat()


def _fresh_enough(stamp: str) -> bool:
    """Is a signed timestamp recent enough to not be a replay?"""
    try:
        seconds = float(stamp)
    except (TypeError, ValueError):
        return False
    when = datetime.fromtimestamp(seconds, UTC)
    return abs(datetime.now(UTC) - when) <= MAX_CLOCK_SKEW


def receive(delivery: Delivery) -> Event:
    """Authenticate, validate and normalise. **Raises or returns an Event.**

    Deliberately does not ingest: this module's job ends at a normalised event,
    and handing it to `engine.ingest` is the route's, so the two can be tested
    apart and so there is no way for a provider quirk to reach the engine.
    """
    verifier = _REGISTRY.get(delivery.provider)
    if verifier is None:
        raise WebhookError(404, "unknown provider",
                           f"no verifier registered for {delivery.provider!r}")

    if len(delivery.body) > MAX_BODY_BYTES:
        raise WebhookError(413, "payload too large",
                           f"{len(delivery.body)} bytes from "
                           f"{delivery.provider}")

    secret = ""
    if verifier.signs:
        from ..config import get_settings
        secret = str(get_settings().get_secret(verifier.secret_key()) or "")
        if not secret:
            # **Not** "let it through because we have no secret". An endpoint
            # that degrades to unauthenticated when misconfigured is an open
            # door, and the events it accepts start automations that send mail.
            raise WebhookError(
                503, "webhooks not configured",
                f"no {verifier.secret_key()} saved; refusing unsigned "
                f"{delivery.provider} deliveries")
        verifier.check(delivery, secret)

    try:
        payload = json.loads(delivery.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookError(400, "malformed payload", str(exc)) from None
    if not isinstance(payload, dict):
        raise WebhookError(400, "malformed payload", "payload is not an object")

    event = verifier.normalise(delivery, payload)
    if not event.kind:
        raise WebhookError(422, "unknown event type",
                           f"{delivery.provider} sent no recognisable type")
    return event


# ── providers ──────────────────────────────────────────────────────────────

class GitHubVerifier:
    """GitHub's `X-Hub-Signature-256`: HMAC-SHA256 of the raw body."""

    provider = "github"
    signs = True

    def secret_key(self) -> str:
        return "GITHUB_WEBHOOK_SECRET"

    def check(self, delivery: Delivery, secret: str) -> None:
        sent = delivery.header("X-Hub-Signature-256")
        if not sent:
            raise WebhookError(401, "unsigned", "no X-Hub-Signature-256 header")
        expected = "sha256=" + hmac.new(
            secret.encode(), delivery.body, hashlib.sha256).hexdigest()
        # Constant-time: a plain `==` leaks how much of the digest matched, one
        # byte at a time, to a caller who can retry.
        if not hmac.compare_digest(sent, expected):
            raise WebhookError(401, "bad signature",
                               "the HMAC did not match the body")

    def normalise(self, delivery: Delivery, payload: dict) -> Event:
        kind = delivery.header("X-GitHub-Event") or ""
        repository = str((payload.get("repository") or {}).get("full_name") or "")
        return Event(
            kind=f"github.{kind}" if kind else "",
            source="github",
            # GitHub's own delivery id. The dedup key, and the reason a retry
            # after our slow 200 does not start a second run.
            external_id=delivery.header("X-GitHub-Delivery"),
            subject=f"{kind} on {repository}"[:200],
            # A commit message, an issue title and a PR body are all written by
            # whoever opened them, including people outside the repository.
            trust=Trust.UNTRUSTED_CONTENT,
            occurred_at=_event_time(
                (payload.get("head_commit") or {}).get("timestamp")),
            data={
                "repository": repository,
                "action": str(payload.get("action") or ""),
                "sender": str((payload.get("sender") or {}).get("login") or ""),
                "ref": str(payload.get("ref") or ""),
                "title": str((payload.get("issue") or payload.get(
                    "pull_request") or {}).get("title") or "")[:200],
                "body": str((payload.get("issue") or payload.get(
                    "pull_request") or {}).get("body") or "")[:4000],
                "url": str((payload.get("issue") or payload.get(
                    "pull_request") or {}).get("html_url") or ""),
            })


class StripeVerifier:
    """Stripe's `Stripe-Signature`: `t=…,v1=…` over `timestamp.body`.

    Included as the second provider on purpose. One verifier proves nothing
    about whether the abstraction fits; a second one with a genuinely different
    scheme — a signed timestamp, a compound header — is what shows the seam is
    in the right place.
    """

    provider = "stripe"
    signs = True

    def secret_key(self) -> str:
        return "STRIPE_WEBHOOK_SECRET"

    def check(self, delivery: Delivery, secret: str) -> None:
        header = delivery.header("Stripe-Signature")
        if not header:
            raise WebhookError(401, "unsigned", "no Stripe-Signature header")
        parts = dict(
            piece.split("=", 1) for piece in header.split(",")
            if "=" in piece)
        stamp, sent = parts.get("t", ""), parts.get("v1", "")
        if not (stamp and sent):
            raise WebhookError(401, "bad signature", "malformed Stripe-Signature")
        if not _fresh_enough(stamp):
            # A valid signature is valid forever without this. Bounding it is
            # what stops a captured delivery being replayed next week.
            raise WebhookError(401, "stale signature",
                               f"timestamp {stamp} is outside the skew window")
        signed = f"{stamp}.".encode() + delivery.body
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sent, expected):
            raise WebhookError(401, "bad signature",
                               "the HMAC did not match the body")

    def normalise(self, delivery: Delivery, payload: dict) -> Event:
        kind = str(payload.get("type") or "")
        return Event(
            kind=f"stripe.{kind}" if kind else "",
            source="stripe",
            external_id=str(payload.get("id") or ""),
            subject=kind[:200],
            trust=Trust.CONNECTED_SOURCE,
            occurred_at=_event_time(payload.get("created")),
            data={
                "type": kind,
                "livemode": bool(payload.get("livemode")),
                "object": str(((payload.get("data") or {}).get("object")
                               or {}).get("object") or ""),
                "amount": ((payload.get("data") or {}).get("object")
                           or {}).get("amount"),
            })


register(GitHubVerifier())
register(StripeVerifier())
