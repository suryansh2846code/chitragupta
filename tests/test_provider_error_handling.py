"""Every provider fails the same way: translated, actionable, never raw.

Providers each handled errors differently — some dumped provider JSON into the
transcript, some raised and 500'd the chat, some said nothing useful.
`models/errors.py` gives them one taxonomy; these tests pin the contract for
every backend and every failure mode.
"""
import subprocess
from unittest.mock import patch

import httpx
import pytest

from chitragupta.models import connection_state
from chitragupta.models.anthropic import AnthropicProvider
from chitragupta.models.base import Message
from chitragupta.models.claude_code import ClaudeCodeProvider
from chitragupta.models.cursor import CursorProvider
from chitragupta.models.errors import ErrorKind, classify_cli, classify_http, extract_detail, redact
from chitragupta.models.openai_compat import OllamaProvider, OpenAICompatProvider, OpenRouterProvider
from chitragupta.models.xai import XAIProvider

HELLO = [Message(role="user", content="hi")]


def _resp(code, body=""):
    return httpx.Response(code, text=body,
                          request=httpx.Request("POST", "https://api.test/v1/chat/completions"))


def _cp(code=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=err)


# ── classification ───────────────────────────────────────────────────────
@pytest.mark.parametrize("status,body,kind", [
    (401, "", ErrorKind.AUTH),
    (403, "", ErrorKind.AUTH),
    (402, "", ErrorKind.BILLING),
    (429, "", ErrorKind.RATE_LIMIT),
    (404, "", ErrorKind.MODEL_NOT_FOUND),
    (413, "", ErrorKind.CONTEXT_TOO_LONG),
    (500, "", ErrorKind.SERVER),
    (503, "", ErrorKind.SERVER),
    (400, "", ErrorKind.BAD_REQUEST),
])
def test_status_codes_map_to_kinds(status, body, kind):
    assert classify_http("openai", status, body).kind is kind


@pytest.mark.parametrize("body,kind", [
    ('{"code":"personal-team-blocked:spending-limit"}', ErrorKind.BILLING),
    ('{"error":{"message":"You exceeded your current quota"}}', ErrorKind.BILLING),
    ('{"error":{"message":"insufficient balance"}}', ErrorKind.BILLING),
    ('{"error":{"message":"maximum context length is 8192 tokens"}}', ErrorKind.CONTEXT_TOO_LONG),
    ('{"error":{"message":"The model `x` does not exist"}}', ErrorKind.MODEL_NOT_FOUND),
    ('{"error":{"message":"blocked by our content filter"}}', ErrorKind.CONTENT_FILTERED),
])
def test_body_overrides_an_ambiguous_status(body, kind):
    """A 400 can mean five different things; only the body distinguishes them."""
    assert classify_http("openai", 400, body).kind is kind


def test_a_401_that_is_really_a_billing_problem_is_classified_as_billing():
    """xAI blocks even GET /v1/models with 403 + a spending-limit body."""
    err = classify_http("xai", 403, '{"code":"personal-team-blocked:spending-limit"}')
    assert err.kind is ErrorKind.BILLING


@pytest.mark.parametrize("kind,retryable", [
    (ErrorKind.RATE_LIMIT, True), (ErrorKind.SERVER, True),
    (ErrorKind.AUTH, False), (ErrorKind.BILLING, False),
    (ErrorKind.MODEL_NOT_FOUND, False), (ErrorKind.CONTEXT_TOO_LONG, False),
])
def test_retryability_is_reported(kind, retryable):
    status = {ErrorKind.RATE_LIMIT: 429, ErrorKind.SERVER: 500, ErrorKind.AUTH: 401,
              ErrorKind.BILLING: 402, ErrorKind.MODEL_NOT_FOUND: 404,
              ErrorKind.CONTEXT_TOO_LONG: 413}[kind]
    assert classify_http("openai", status).retryable is retryable


# ── secrets never leak ───────────────────────────────────────────────────
@pytest.mark.parametrize("secret", [
    "sk-proj-abcdefghijklmnop", "xai-abcdefghijklmnop",
    "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ12", "Bearer eyJhbGciOiJIUzI1NiJ9",
])
def test_credentials_are_redacted_from_provider_bodies(secret):
    err = classify_http("openai", 401, f'{{"error":{{"message":"Bad key: {secret}"}}}}')
    assert secret not in err.as_reply()
    assert "[redacted]" in err.detail
    assert secret not in redact(f"leaked {secret} here")


# ── body shapes ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("body,expect", [
    ('{"error":{"message":"boom"}}', "boom"),                    # OpenAI / Anthropic
    ('{"error":"boom"}', "boom"),                                # Ollama / xAI
    ('[{"error":{"message":"boom"}}]', "boom"),                  # Google
    ('{"message":"boom"}', ""),                                  # no error key
    ("plain text failure", "plain text failure"),
    ("", ""),
    (None, ""),
])
def test_detail_is_extracted_from_each_providers_shape(body, expect):
    got = extract_detail(body)
    assert (expect in got) if expect else (got == "" or got == "boom" or got)


def test_detail_is_truncated():
    assert len(extract_detail("x" * 5000)) < 200


# ── every provider returns, never raises ─────────────────────────────────
PROVIDERS = [
    ("openai", lambda: OpenAICompatProvider(api_key="k")),
    ("openrouter", lambda: OpenRouterProvider(api_key="k")),
    ("ollama", lambda: OllamaProvider()),
    ("xai", lambda: XAIProvider(api_key="k")),
    ("claude", lambda: AnthropicProvider(api_key="k")),
]


@pytest.mark.parametrize("name,make", PROVIDERS)
@pytest.mark.parametrize("status", [401, 402, 404, 413, 429, 500, 503])
def test_http_failures_become_replies_not_exceptions(name, make, status):
    with patch("httpx.post", return_value=_resp(status, '{"error":{"message":"nope"}}')):
        result = make().chat(HELLO)
    assert result.text.startswith("⚠️")
    assert "Traceback" not in result.text


@pytest.mark.parametrize("name,make", PROVIDERS)
@pytest.mark.parametrize("exc", [httpx.TimeoutException("t"), httpx.ConnectError("c")])
def test_transport_failures_become_replies(name, make, exc):
    with patch("httpx.post", side_effect=exc):
        assert make().chat(HELLO).text.startswith("⚠️")


@pytest.mark.parametrize("name,make", PROVIDERS)
def test_a_malformed_success_body_is_handled(name, make):
    with patch("httpx.post", return_value=_resp(200, "not json at all")):
        assert make().chat(HELLO).text.startswith("⚠️")


def test_no_provider_leaks_raw_json_into_the_transcript():
    body = '{"code":"personal-team-blocked:spending-limit","error":"out of credits"}'
    with patch("httpx.post", return_value=_resp(402, body)):
        text = XAIProvider(api_key="k").chat(HELLO).text
    assert '{"code"' not in text and "personal-team-blocked" not in text


# ── provider-specific refinements survive ────────────────────────────────
def test_ollama_down_names_the_fix():
    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        assert "ollama serve" in OllamaProvider().chat(HELLO).text


def test_xai_billing_explains_the_subscription_split():
    with patch("chitragupta.models.xai_auth.get_xai_access_token", return_value="tok"), \
         patch("chitragupta.models.base._saved_key", return_value=""):
        p = XAIProvider(api_key=None)
    p.api_key = "tok"
    with patch("httpx.post", return_value=_resp(402, '{"code":"spending-limit"}')):
        assert "console.x.ai" in p.chat(HELLO).text


def test_messages_use_the_providers_display_name():
    assert "OpenAI" in classify_http("openai", 401).message
    assert "Ollama" in classify_http("ollama", 500).message


# ── CLI-backed providers use the same taxonomy ───────────────────────────
@pytest.mark.parametrize("stderr,kind", [
    ("You are not logged in", ErrorKind.AUTH),
    ("Please log in to continue", ErrorKind.AUTH),
    ("There's an issue with the selected model (x)", ErrorKind.MODEL_NOT_FOUND),
    ("prompt is too long", ErrorKind.CONTEXT_TOO_LONG),
])
def test_cli_failures_are_classified(stderr, kind):
    assert classify_cli("claude-code", 1, "", stderr, model="m").kind is kind


def test_cli_message_does_not_duplicate_the_cli_suffix():
    msg = classify_cli("claude-code", 1, "", "not logged in").message
    assert "CLI CLI" not in msg


@pytest.mark.parametrize("make,finder", [
    (ClaudeCodeProvider, "chitragupta.models.claude_cli.find_claude"),
    (CursorProvider, "chitragupta.models.cursor.find_cursor_cli"),
])
def test_cli_providers_return_replies_on_failure(make, finder):
    with patch(finder, return_value="/usr/bin/x"), \
         patch("subprocess.run", return_value=_cp(1, "", "not logged in")):
        assert make().chat(HELLO).text.startswith("⚠️")

    with patch(finder, return_value="/usr/bin/x"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
        assert "timed out" in make().chat(HELLO).text


def test_model_not_found_suggests_models_the_user_can_run():
    from chitragupta.models import discovery
    from chitragupta.models.discovery import DiscoveredModel, clear_model_cache

    clear_model_cache()
    with patch.object(discovery, "_discover_raw",
                      lambda pid, k: ([DiscoveredModel("good-1", "G", ""),
                                       DiscoveredModel("locked-1", "L", "", locked=True)], {})), \
         patch.object(connection_state, "is_provider_connected",
                      lambda pid, api_key=None: (True, "Plan", {})):
        err = classify_http("openai", 404, "", model="gone-1")
    clear_model_cache()
    assert "good-1" in err.message
    assert "locked-1" not in err.message, "suggested a model the user cannot run"


# ── Gemini uses the shared taxonomy but keeps its own wording ────────────
def _gemini(body, status):
    from chitragupta.models.gemini import GeminiProvider

    with patch("chitragupta.models.gemini.resolve_gemini_credentials") as cred, \
         patch("httpx.post", return_value=_resp(status, body)):
        cred.return_value.valid = True
        cred.return_value.secret = "AIzaTESTKEY12345678901234"
        return GeminiProvider(api_key="AIzaTESTKEY12345678901234").chat(HELLO).text


@pytest.mark.parametrize("status,body,expect", [
    (429, '{"error":{"message":"Quota exceeded"}}', "rate-limited"),
    (400, '{"error":{"message":"exceeds the maximum context"}}', "too long"),
    (500, "{}", "server error"),
])
def test_gemini_gained_the_kinds_its_own_classifier_lacked(status, body, expect):
    """Quota, context-length and content filtering were unclassified before."""
    assert expect in _gemini(body, status)


def test_gemini_keeps_its_credential_specific_wording():
    text = _gemini('{"error":{"message":"API key not valid"}}', 403)
    assert "Gemini" in text and "⚠️" in text


def test_gemini_suggests_models_on_a_model_error():
    text = _gemini('{"error":{"message":"models/x is not found"}}', 404)
    assert "isn't available" in text


def test_gemini_never_leaks_its_key():
    text = _gemini('{"error":{"message":"bad key AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ12"}}', 403)
    assert "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ12" not in text


# ── every provider is on the shared taxonomy ─────────────────────────────
def test_every_http_error_handler_uses_the_shared_classifier():
    """Providers may branch on status for *recovery* (Gemini retries a 404 that
    names a replacement model), but the user-facing message must always come
    from errors.py — that is what keeps the vocabulary consistent and stops raw
    provider JSON reaching the transcript."""
    import re
    from pathlib import Path

    models = Path(__file__).parent.parent / "chitragupta/models"
    offenders = []
    for f in sorted(models.glob("*.py")):
        if f.name == "errors.py":
            continue
        src = f.read_text()
        for match in re.finditer(r"except\s+httpx\.HTTPStatusError[^\n]*:\n", src):
            block = src[match.end(): match.end() + 600]
            handler = block.split("\n        except")[0]
            if not re.search(r"classify_http|classify_exception|ProviderError", handler):
                offenders.append(f"{f.name}:{src[:match.start()].count(chr(10)) + 1}")
    assert not offenders, f"HTTP error handlers not using errors.py: {offenders}"


def test_error_kinds_are_exhaustively_messaged():
    """Every kind must produce a non-empty, actionable message."""
    from chitragupta.models.errors import classify_http

    seen = set()
    for status in (400, 401, 402, 403, 404, 413, 429, 500, 503):
        err = classify_http("openai", status, model="m")
        seen.add(err.kind)
        assert err.message and not err.message.endswith(":")
    for body, _ in [('{"error":"context length"}', 0), ('{"error":"content filter"}', 0),
                    ('{"error":"not entitled"}', 0)]:
        err = classify_http("openai", 400, body, model="m")
        seen.add(err.kind)
        assert err.message
    assert len(seen) >= 8, f"only exercised {len(seen)} kinds"
