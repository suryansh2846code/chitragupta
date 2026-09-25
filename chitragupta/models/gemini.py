"""Google Gemini provider via official OpenAI-compatible endpoint.

Google provides an official OpenAI-compatible chat completions endpoint for Gemini:
https://generativelanguage.googleapis.com/v1beta/openai/

Supports tool/function calling, system messages, and usage reporting.
Model ids come from live discovery against the user's own key.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from ..log import suppressed
from .base import DEFAULT_MAX_OUTPUT, ChatResult, ToolCall, _saved_key
from .connections import ConnectionStatus, get_connection
from .errors import ErrorKind, ProviderError, classify_exception, classify_http
from .openai_compat import OpenAICompatProvider


class GeminiCredentialSource(StrEnum):
    API_KEY = "api_key"
    NONE = "none"


class GeminiErrorCode(StrEnum):
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN = "UNKNOWN"


@dataclass
class GeminiCredential:
    source: GeminiCredentialSource
    secret: str | None = None
    valid: bool = False
    scopes: list[str] = field(default_factory=list)
    email: str | None = None
    account_name: str | None = None
    error_reason: str | None = None

    def __repr__(self) -> str:
        # Never leak secret
        return (f"GeminiCredential(source={self.source.value}, valid={self.valid}, "
                f"email={self.email!r}, error_reason={self.error_reason!r})")


def has_gemini_scope(scopes: list[str] | None) -> bool:
    """Check whether granted OAuth scopes include Gemini generative capabilities (legacy)."""
    if not scopes:
        return False
    return any(
        any(g in s.lower() for g in ("generative-language", "cloud-platform", "gemini"))
        for s in scopes
    )


def resolve_gemini_credentials(api_key: str | None = None,
                               force_source: GeminiCredentialSource | None = None) -> GeminiCredential:
    """Resolve Gemini credentials exclusively via API key.

    Gemini is an API key provider (GEMINI_API_KEY, GOOGLE_API_KEY, or saved secret).
    OAuth is not used for Gemini model inference.
    """
    conn = get_connection("gemini")
    if conn.connection_status == ConnectionStatus.DISCONNECTED:
        return GeminiCredential(
            source=GeminiCredentialSource.NONE,
            valid=False,
            error_reason="Disconnected by user",
        )

    # 1. If explicit api_key argument is passed, it takes absolute precedence
    if api_key is not None:
        key_clean = api_key.strip()
        if not key_clean:
            return GeminiCredential(
                source=GeminiCredentialSource.NONE,
                valid=False,
                error_reason="API key cannot be empty.",
            )
        return GeminiCredential(
            source=GeminiCredentialSource.API_KEY,
            secret=key_clean,
            valid=True,
            email=conn.email or "API Key User",
        )

    # 2. Resolve candidate API key from settings / env / saved keys
    candidate_key = None
    from ..config import get_settings
    try:
        settings = get_settings()
        candidate_key = (
            settings.get_secret("GEMINI_API_KEY")
            or settings.get_secret("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or _saved_key("GEMINI_API_KEY")
            or _saved_key("GOOGLE_API_KEY")
            or ""
        ).strip() or None
    except Exception:
        candidate_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or _saved_key("GEMINI_API_KEY")
            or _saved_key("GOOGLE_API_KEY")
            or ""
        ).strip() or None

    if candidate_key:
        return GeminiCredential(
            source=GeminiCredentialSource.API_KEY,
            secret=candidate_key,
            valid=True,
            email=conn.email or "API Key User",
        )

    return GeminiCredential(
        source=GeminiCredentialSource.NONE,
        valid=False,
        error_reason="Gemini is not connected. Enter a GEMINI_API_KEY in Models & Accounts.",
    )


def classify_gemini_error(status_code: int, response_text: str | None = None, model: str = "") -> tuple[GeminiErrorCode, str]:
    """Return structured (GeminiErrorCode, user_facing_message) without leaking secrets."""
    # Attempt to extract safe error message from Google's structured response
    clean_detail = ""
    if response_text:
        with suppressed("err_json = json.loads(response_text) …"):
            err_json = json.loads(response_text)
            err_obj = {}
            if isinstance(err_json, dict):
                err_obj = err_json.get("error", {})
            elif isinstance(err_json, list) and len(err_json) > 0 and isinstance(err_json[0], dict):
                err_obj = err_json[0].get("error", err_json[0])
            if isinstance(err_obj, dict):
                raw_msg = err_obj.get("message") or ""
                # Strip out sensitive query params or keys if reflected
                clean_detail = re.sub(r'key=[A-Za-z0-9_\-]+', 'key=REDACTED', raw_msg)
                clean_detail = re.sub(r'Bearer\s+[A-Za-z0-9_\-\.]+', 'Bearer REDACTED', clean_detail)

    if response_text and not clean_detail:
        clean_detail = re.sub(r'key=[A-Za-z0-9_\-]+', 'key=REDACTED', response_text)
        clean_detail = re.sub(r'Bearer\s+[A-Za-z0-9_\-\.]+', 'Bearer REDACTED', clean_detail)

    if status_code == 401:
        msg = "Your Gemini API credential is invalid or expired. Check GEMINI_API_KEY."
        if clean_detail:
            msg += f" ({clean_detail[:120]})"
        return (
            GeminiErrorCode.AUTHENTICATION_FAILED,
            msg,
        )

    if status_code == 403:
        if clean_detail and any(k in clean_detail.lower() for k in ("disabled", "not been used", "has not been used")):
            return (
                GeminiErrorCode.PERMISSION_DENIED,
                f"Gemini API (Generative Language API) is disabled or not enabled for this project. Enable it in Google AI Studio or Cloud Console. ({clean_detail[:120]})",
            )
        if clean_detail and "location" in clean_detail.lower():
            return (
                GeminiErrorCode.PERMISSION_DENIED,
                f"User location or region is not supported by Gemini API for this project. ({clean_detail[:120]})",
            )
        msg = "Permission denied: Gemini authentication succeeded, but this credential does not have permission to use the requested Gemini API/model."
        if clean_detail:
            msg += f" ({clean_detail[:120]})"
        return (
            GeminiErrorCode.PERMISSION_DENIED,
            msg,
        )

    if status_code == 404:
        return (
            GeminiErrorCode.MODEL_NOT_FOUND,
            f"The selected Gemini model '{model or 'unknown'}' is not found or has been deprecated. Please choose an active model (e.g. gemini-2.5-flash) from the model selector.",
        )

    if status_code == 429:
        return (
            GeminiErrorCode.RATE_LIMITED,
            "Gemini quota or rate limit exceeded. Check your plan in Google AI Studio or wait a moment and retry.",
        )

    if 500 <= status_code < 600:
        return (
            GeminiErrorCode.SERVER_ERROR,
            f"Google Gemini temporary server error ({status_code}). Try again shortly.",
        )

    if status_code == 0:
        return (
            GeminiErrorCode.NETWORK_ERROR,
            f"Google Gemini network connection error or request timed out: {clean_detail or 'Connection failed'}",
        )

    return (
        GeminiErrorCode.INVALID_REQUEST,
        f"Gemini request failed ({status_code})." + (f" {clean_detail[:120]}" if clean_detail else ""),
    )


class GeminiProvider(OpenAICompatProvider):
    name = "gemini"
    default_base = "https://generativelanguage.googleapis.com/v1beta/openai"
    default_model = "gemini-3.6-flash"
    key_env = "GEMINI_API_KEY"
    key_required = True

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        raw_model = model or os.environ.get(
            f"{self.name.upper()}_MODEL", self.default_model
        )
        if not raw_model or not raw_model.lower().startswith("gemini"):
            raw_model = self.default_model
        self.model = raw_model
        self.base_url = (base_url or os.environ.get(
            f"{self.name.upper()}_BASE_URL", self.default_base
        )).rstrip("/")
        self._explicit_api_key_passed = (api_key is not None)
        self._explicit_api_key = api_key
        # Resolve credential explicitly
        cred = resolve_gemini_credentials(api_key=api_key)
        self.credential = cred
        self.api_key = cred.secret if cred.valid else ""

    def is_ready(self) -> tuple[bool, str]:
        effective_key = self._explicit_api_key if self._explicit_api_key_passed else (self.api_key or None)
        cred = resolve_gemini_credentials(api_key=effective_key)
        self.credential = cred
        if cred.valid:
            return True, ""
        return False, cred.error_reason or "set GEMINI_API_KEY to enable Gemini"

    def _refine_error(self, err):
        """Keep Gemini's own wording for credential problems — it names the API
        key and AI Studio, which is more actionable than the generic text."""
        if err.kind in (ErrorKind.AUTH, ErrorKind.MODEL_NOT_ENTITLED) and err.status:
            _code, msg = classify_gemini_error(err.status, err.detail, model=self.model)
            if msg:
                err.message = msg
        return err

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        effective_key = self._explicit_api_key if self._explicit_api_key_passed else (self.api_key or None)
        cred = resolve_gemini_credentials(api_key=effective_key)
        self.credential = cred
        if not cred.valid or not cred.secret:
            return ChatResult(
                text=f"⚠️ {cred.error_reason or 'Gemini is not connected. Please provide a GEMINI_API_KEY in Models & Accounts.'}"
            )

        payload = {
            "model": self.model,
            "messages": self._to_openai(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [{
                "type": "function",
                "function": {
                    "name": t.name, "description": t.description,
                    "parameters": t.parameters,
                },
            } for t in tools]

        headers = {
            "content-type": "application/json",
            "Authorization": f"Bearer {cred.secret}",
        }

        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=payload, timeout=120,
            )
            # Automatic deprecation migration: Google regularly sunsets models (e.g. 2.5-flash -> 3.6-flash).
            # If Google explicitly recommends a replacement model in the 404 response, retry once with it.
            if resp.status_code == 404 and "gemini-" in resp.text:
                mig_match = re.search(r'use\s+models/(gemini-[0-9\.]+(?:-[a-z0-9\-]+)?)', resp.text)
                if mig_match:
                    new_model = mig_match.group(1)
                    payload["model"] = new_model
                    resp = httpx.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers, json=payload, timeout=120,
                    )
                    self.model = new_model
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Shared taxonomy first: it recognises quota, context-length and
            # content-filter failures, which Gemini's own codes did not cover.
            err = classify_http("gemini", exc.response.status_code, exc.response.text,
                                model=self.model, key_env=self.key_env)
            return ChatResult(text=self._refine_error(err).as_reply())
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            err = classify_exception("gemini", exc, model=self.model,
                                     base_url=self.base_url)
            return ChatResult(text=err.as_reply())

        try:
            data = resp.json()
            choice = data["choices"][0]["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return ChatResult(text=ProviderError(
                ErrorKind.BAD_REQUEST, "gemini", model=self.model,
                message="Unexpected response from Gemini. Try again or switch models."
            ).as_reply())

        calls = []
        for tc in choice.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(
                id=tc.get("id", str(uuid.uuid4())),
                name=tc["function"]["name"], arguments=args,
                extra_content=tc.get("extra_content"),
            ))
        usage = data.get("usage") or {}
        return ChatResult(
            text=choice.get("content") or "",
            tool_calls=calls,
            raw=data,
            finish_reason=data["choices"][0].get("finish_reason", "stop"),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )
