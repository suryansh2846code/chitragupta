"""A provider's callback, and everything about it that must be refused.

A webhook endpoint is the only door into this app that a stranger on the
internet can knock on, and what comes through it starts automations that send
email. So most of this file is about saying no: unsigned, badly signed, stale,
oversized, malformed, unknown.

No live provider anywhere. Signatures are computed with the same HMAC the
verifier checks, which is the only way to test "a valid signature is accepted"
without either a network or a hardcoded blob that rots.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from automation_harness import FakeAgent, build_deps, fresh_store

from chitragupta.automation import engine, webhooks
from chitragupta.automation.model import Automation, Policy
from chitragupta.automation.webhooks import Delivery, WebhookError
from chitragupta.core.provenance import Trust

SECRET = "shhh"


@pytest.fixture(autouse=True)
def _own_database(monkeypatch):
    """A webhook secret, and nothing else about settings changed.

    Overriding `get_secret` on the *real* settings rather than substituting the
    object: everything downstream of ingest — the routine store, the brain —
    reads `home` off the same instance, and a stub that answers one question
    breaks all of them.
    """
    fresh_store()
    from chitragupta.config import get_settings

    real = get_settings()
    monkeypatch.setattr(type(real), "get_secret",
                        lambda self, key: SECRET, raising=False)
    yield


def github(body: dict, *, signed: bool = True, event: str = "issues",
           delivery_id: str = "d-1", secret: str = SECRET) -> Delivery:
    raw = json.dumps(body).encode()
    headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery_id}
    if signed:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(
            secret.encode(), raw, hashlib.sha256).hexdigest()
    return Delivery(provider="github", body=raw, headers=headers)


def stripe(body: dict, *, stamp: float | None = None,
           secret: str = SECRET) -> Delivery:
    raw = json.dumps(body).encode()
    when = int(stamp if stamp is not None else time.time())
    digest = hmac.new(secret.encode(), f"{when}.".encode() + raw,
                      hashlib.sha256).hexdigest()
    return Delivery(provider="stripe", body=raw,
                    headers={"Stripe-Signature": f"t={when},v1={digest}"})


PAYLOAD = {"action": "opened", "repository": {"full_name": "acme/api"},
           "sender": {"login": "ana"},
           "issue": {"title": "It broke", "body": "here is why",
                     "html_url": "https://example.test/1"}}


# ── the signature ──────────────────────────────────────────────────────────

def test_a_valid_signature_is_accepted():
    event = webhooks.receive(github(PAYLOAD))
    assert event.kind == "github.issues"
    assert event.source == "github"
    assert event.data["repository"] == "acme/api"


def test_an_invalid_signature_is_refused():
    bad = github(PAYLOAD, secret="not-the-secret")
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(bad)
    assert refused.value.status == 401


def test_a_missing_signature_is_refused():
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(github(PAYLOAD, signed=False))
    assert refused.value.status == 401
    assert "unsigned" in refused.value.public


def test_a_tampered_body_is_refused():
    """The signature covers the body, which is the only reason it is worth
    having — a signature over the headers would authenticate nothing."""
    delivery = github(PAYLOAD)
    tampered = Delivery(provider="github", headers=delivery.headers,
                        body=delivery.body.replace(b"acme/api", b"evil/api"))
    with pytest.raises(WebhookError):
        webhooks.receive(tampered)


def test_the_caller_is_not_told_which_part_was_wrong():
    """Telling an unauthenticated caller how to fix their forgery is helping
    them. The reason goes to the log; the caller gets a word."""
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(github(PAYLOAD, secret="wrong"))
    assert refused.value.public == "bad signature"
    assert refused.value.reason != refused.value.public


def test_a_provider_with_no_secret_configured_refuses_rather_than_opening_up(
        monkeypatch):
    """The failure that would matter most. An endpoint that degrades to
    unauthenticated when misconfigured is an open door, and what comes through
    it starts automations that send mail."""
    from chitragupta.config import get_settings

    monkeypatch.setattr(type(get_settings()), "get_secret",
                        lambda self, key: "", raising=False)
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(github(PAYLOAD))
    assert refused.value.status == 503
    assert "not configured" in refused.value.public


def test_signature_comparison_is_constant_time():
    """A plain `==` leaks how much of the digest matched, one byte at a time,
    to a caller who can retry."""
    import inspect
    source = inspect.getsource(webhooks)
    assert "compare_digest" in source
    assert source.count("compare_digest") >= 2, "every verifier must use it"


# ── the payload ────────────────────────────────────────────────────────────

def test_a_malformed_payload_is_refused():
    delivery = Delivery(provider="github", body=b"{not json",
                        headers=github(PAYLOAD).headers)
    # Signed over different bytes, so re-sign the broken body to isolate the
    # JSON failure from the signature one.
    delivery = Delivery(
        provider="github", body=b"{not json",
        headers={**github(PAYLOAD).headers,
                 "X-Hub-Signature-256": "sha256=" + hmac.new(
                     SECRET.encode(), b"{not json", hashlib.sha256).hexdigest()})
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(delivery)
    assert refused.value.status == 400


def test_a_payload_that_is_not_an_object_is_refused():
    raw = b"[1,2,3]"
    delivery = Delivery(
        provider="github", body=raw,
        headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "d",
                 "X-Hub-Signature-256": "sha256=" + hmac.new(
                     SECRET.encode(), raw, hashlib.sha256).hexdigest()})
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(delivery)
    assert refused.value.status == 400


def test_an_oversized_payload_is_refused_before_it_is_parsed():
    """A webhook is a notification, not a file transfer. Unbounded means the
    caller decides how much memory we allocate before authenticating them."""
    huge = b"x" * (webhooks.MAX_BODY_BYTES + 1)
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(Delivery(provider="github", body=huge, headers={}))
    assert refused.value.status == 413


def test_an_unknown_event_type_is_refused():
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(github(PAYLOAD, event=""))
    assert refused.value.status == 422


def test_an_unknown_provider_is_refused():
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(Delivery(provider="nobody", body=b"{}", headers={}))
    assert refused.value.status == 404


# ── replay and time ────────────────────────────────────────────────────────

def test_a_stale_signed_timestamp_is_refused():
    """A valid signature is valid forever without this. Bounding it is what
    stops a captured delivery being replayed next week."""
    old = time.time() - 3600
    with pytest.raises(WebhookError) as refused:
        webhooks.receive(stripe({"id": "evt_1", "type": "charge.succeeded"},
                                stamp=old))
    assert refused.value.status == 401
    assert "stale" in refused.value.public


def test_a_fresh_signed_timestamp_is_accepted():
    event = webhooks.receive(stripe({"id": "evt_2", "type": "charge.succeeded"}))
    assert event.kind == "stripe.charge.succeeded"
    assert event.external_id == "evt_2"


@pytest.mark.parametrize("raw,expected_year", [
    (1790000000, 2026),               # seconds
    (1790000000000, 2026),            # milliseconds
    ("2026-09-28T09:00:00Z", 2026),   # ISO with Z
    ("2026-09-28T09:00:00+00:00", 2026),
])
def test_provider_timestamps_are_read_in_every_shape_they_arrive_in(
        raw, expected_year):
    assert webhooks._event_time(raw).startswith(str(expected_year))


def test_an_unreadable_timestamp_falls_back_to_now_rather_than_failing():
    """The timestamp is metadata, not the event. A webhook whose clock we
    cannot read must still be processed."""
    assert webhooks._event_time("last Tuesday")
    assert webhooks._event_time(None)
    assert webhooks._event_time(10**20)


def test_an_out_of_order_delivery_keeps_its_own_time():
    """Two deliveries arriving in the wrong order must each carry the time the
    provider stamped, not the time we happened to read them."""
    first = webhooks.receive(stripe({"id": "a", "type": "x", "created": 1790000000}))
    second = webhooks.receive(stripe({"id": "b", "type": "x", "created": 1780000000}))
    assert second.occurred_at < first.occurred_at


# ── deduplication, through the real ledger ─────────────────────────────────

def test_a_duplicate_delivery_starts_one_run():
    """A provider retrying because our 200 was slow must not run it twice."""
    auto = Automation(id="w1", name="Issue watch", agent_id="personal",
                      instruction="note it", goal="issues are noted",
                      trigger={"type": "event", "kind": "github.issues",
                               "source": "github"},
                      conditions=[], policy=Policy())
    deps, _ = build_deps(agent=FakeAgent(default="noted"))

    event = webhooks.receive(github(PAYLOAD, delivery_id="same-id"))
    first = engine.ingest(event, deps=deps, automations=[auto])

    again = webhooks.receive(github(PAYLOAD, delivery_id="same-id"))
    second = engine.ingest(again, deps=deps, automations=[auto])

    assert len(first["started"]) == 1
    assert second["duplicate"] is True and second["started"] == []


def test_the_dedup_key_is_the_providers_own_delivery_id():
    one = webhooks.receive(github(PAYLOAD, delivery_id="abc"))
    two = webhooks.receive(github(PAYLOAD, delivery_id="def"))
    assert one.dedup_key == "github:abc"
    assert one.dedup_key != two.dedup_key


def test_webhook_content_is_untrusted():
    """An issue body is written by whoever opened it, including people outside
    the repository."""
    assert webhooks.receive(github(PAYLOAD)).trust == Trust.UNTRUSTED_CONTENT


# ── the endpoint ───────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app
    return TestClient(app)


def _post(client, delivery: Delivery):
    return client.post(f"/api/webhooks/{delivery.provider}",
                       content=delivery.body, headers=delivery.headers)


def test_the_endpoint_accepts_a_signed_delivery(client):
    reply = _post(client, github(PAYLOAD, delivery_id="http-1"))
    assert reply.status_code == 200
    assert reply.json()["accepted"] is True


def test_the_endpoint_refuses_an_unsigned_delivery(client):
    assert _post(client, github(PAYLOAD, signed=False)).status_code == 401


def test_the_endpoint_reports_a_duplicate_as_accepted_not_as_an_error(client):
    """A provider retrying must be told "I have this". An error makes it retry
    again, forever."""
    _post(client, github(PAYLOAD, delivery_id="http-2"))
    second = _post(client, github(PAYLOAD, delivery_id="http-2"))
    assert second.status_code == 200
    assert second.json()["duplicate"] is True


def test_the_endpoint_refuses_an_unknown_provider(client):
    assert client.post("/api/webhooks/nobody", content=b"{}").status_code == 404


def test_there_is_no_second_pipeline():
    """The whole design claim: a webhook ends up in the same `engine.ingest` a
    connector sync and the scheduler use. A separate path would mean its own
    deduplication, its own concurrency rules and its own bugs."""
    import inspect

    from chitragupta.api.routes import automations
    source = inspect.getsource(automations.receive_webhook)
    assert "engine.ingest" in source
    assert "router.route" not in source, "the route must not route by itself"
    assert "Executor" not in source, "the route must not execute by itself"
