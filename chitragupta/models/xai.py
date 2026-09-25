"""xAI Grok provider via the OpenAI-compatible developer API.

IMPORTANT — a Grok subscription is not an API key. `api.x.ai` is xAI's
**developer** API, billed against credits bought at console.x.ai. A SuperGrok /
Grok subscription covers grok.com and the mobile apps and grants no credits
here, so signing in with Grok authenticates successfully and then fails every
request with 402 `personal-team-blocked:spending-limit`. Those are separate
products with independent billing.

So an OAuth sign-in alone cannot run inference: this provider needs an
XAI_API_KEY from a console.x.ai account that has credits.

Model ids come from live discovery against the user's own key.
"""
from __future__ import annotations

import os

from ..log import suppressed
from .base import DEFAULT_MAX_OUTPUT, ChatResult, _saved_key
from .openai_compat import OpenAICompatProvider


class XAIProvider(OpenAICompatProvider):
    name = "xai"
    default_base = "https://api.x.ai/v1"
    default_model = "grok-4.6"
    key_env = "XAI_API_KEY"
    key_required = True

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        self._oauth_only = False
        self._cli = None
        if api_key is not None:
            resolved_key = api_key
        else:
            resolved_key = (
                os.environ.get("XAI_API_KEY")
                or _saved_key("XAI_API_KEY")
                or ""
            )
            if not resolved_key:
                with suppressed("from .xai_auth import get_xai_access_token …"):
                    from .xai_auth import get_xai_access_token
                    tok = get_xai_access_token()
                    if tok:
                        # Authenticates, but has no developer-API credits.
                        self._oauth_only = True
        super().__init__(model=model, api_key=resolved_key, base_url=base_url)

    # An OAuth token authenticates but carries no developer-API credits.
    _SUBSCRIPTION_ONLY = (
        "Your Grok sign-in doesn't include xAI developer API credits — a "
        "SuperGrok subscription covers grok.com, not api.x.ai. Add credits at "
        "console.x.ai and paste an XAI_API_KEY in Models & Accounts."
    )

    def is_ready(self) -> tuple[bool, str]:
        if self.api_key:
            return True, ""
        backend = self._subscription_backend()
        if backend is not None:
            return backend.is_ready()
        if self._oauth_only:
            return False, self._SUBSCRIPTION_ONLY
        from .grok_cli import INSTALL_HINT
        return False, f"set XAI_API_KEY, or run Grok on your subscription — {INSTALL_HINT}"

    def _subscription_backend(self):
        """xAI's own CLI, which is how a SuperGrok subscription runs."""
        if self._cli is None:
            from .grok_cli import GrokCliProvider, find_grok_cli
            if not find_grok_cli():
                return None
            self._cli = GrokCliProvider(model=self.model)
        return self._cli

    def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=DEFAULT_MAX_OUTPUT):
        if not self.api_key:
            # Subscription, not an API key — api.x.ai would 402.
            backend = self._subscription_backend()
            if backend is not None:
                return backend.chat(messages, tools=tools, temperature=temperature,
                                    max_tokens=max_tokens)
            from .grok_cli import INSTALL_HINT
            return ChatResult(text=(
                f"⚠️ No xAI API key, and {INSTALL_HINT[0].lower()}{INSTALL_HINT[1:]}"))
        return super().chat(messages, tools=tools, temperature=temperature,
                            max_tokens=max_tokens)

    def _refine_error(self, err):
        """Name the actual cause: a Grok sign-in and API credits are not the
        same product, so a generic 'out of credits' would mislead."""
        from .errors import ErrorKind

        if err.kind is ErrorKind.BILLING:
            err.message = (self._SUBSCRIPTION_ONLY if self._oauth_only else
                           "Your xAI account is out of developer API credits. "
                           "Add credits at console.x.ai.")
        return err
