"""Who can vouch for a user, as data rather than as code.

One class and one registry, the same shape as
`connectors/__init__.py::REGISTRY` — because the lesson that table already
learnt applies exactly here: *"a hand-maintained list of five names, and the
other six were excluded by nothing more than not being in it."* Adding Apple or
Microsoft must be an entry here plus a client id, never an edit to the sign-in
flow, the verifier, the routes or the screen.

**All three are declared now, and only the buildable ones are available.** That
is deliberate: a provider that is absent from the registry is indistinguishable
from one nobody has thought about, while a provider that is *present and
unavailable* carries the reason with it. The screen draws Apple with the
`data-soon` lock the rest of the app uses for exactly this.

## The one thing that differs between them, and it is not cosmetic

| | loopback? | why |
|---|---|---|
| Google | **yes** | RFC 8252 native-app flow, any `127.0.0.1` port |
| Microsoft | **yes** | same; `http://localhost` is a registered native redirect |
| Apple | **no** | requires a registered `https://` Return URL on a verified domain |

`loopback` is therefore the field that decides whether a provider can work with
no server of ours. Google and Microsoft can ship today. Apple cannot, and no
amount of code here changes that — see
[`docs/ACCOUNTS-DESIGN.md`](../../docs/ACCOUNTS-DESIGN.md) §2.

## Two more differences worth knowing before adding one

* **The subject is not always `sub`.** Google and Apple use it; Microsoft's
  stable identity is `tid` + `oid` together, because an `oid` is only unique
  within a tenant. `subject_claims` is that, and `subject_of` joins them.
* **The issuer is not always a fixed string.** Microsoft's `iss` contains the
  tenant id, so an exact-match set cannot express it. `issuer_regex` is for
  that, and `accepts_issuer` checks both — an issuer that matches neither is
  refused, because "accept any issuer" is the same as not checking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Why a provider cannot be used yet. Shown to the user verbatim, so it says
#: what is missing rather than "not supported".
NEEDS_SERVER = ("{label} sign-in needs a web address of ours that it can send "
                "you back to, which Chitragupta does not have yet. Google "
                "sign-in works today.")
NEEDS_CLIENT = ("This build has no {label} sign-in client configured, so "
                "signing in with {label} is not available.")


@dataclass(frozen=True)
class Provider:
    """Everything the flow, the verifier and the screen need about one issuer.

    Frozen and data-only: no provider carries behaviour, because the moment one
    does, the next one has a reason to carry different behaviour and the flow
    grows a branch per provider. That is the `providerId === "x"` chain
    `/CLAUDE.md` forbids on render paths, one layer down.
    """
    id: str
    label: str
    scopes: tuple[str, ...]
    auth_uri: str
    token_uri: str
    discovery_url: str
    #: The `Settings` field holding this provider's client id.
    client_setting: str
    #: Can the native loopback+PKCE flow be used? False means a server is
    #: required and this provider cannot be offered yet.
    loopback: bool
    #: Exact issuer strings. Both spellings where a provider uses two.
    issuers: frozenset[str] = frozenset()
    #: For an issuer that embeds a tenant id and so cannot be an exact match.
    issuer_regex: str = ""
    #: Which claims make up a stable identity, in order.
    subject_claims: tuple[str, ...] = ("sub",)
    #: Extra authorize-URL parameters this provider needs.
    extra_auth_params: dict[str, str] = field(default_factory=dict)

    def accepts_issuer(self, issuer: Any) -> bool:
        """Is this `iss` one this provider is allowed to speak as?

        Exact matches first, then the pattern. Neither matching is a refusal —
        there is deliberately no "accept anything" branch, because that is the
        same as not checking the issuer at all.
        """
        if not isinstance(issuer, str) or not issuer:
            return False
        if issuer in self.issuers:
            return True
        return bool(self.issuer_regex and re.fullmatch(self.issuer_regex, issuer))

    def subject_of(self, claims: dict[str, Any]) -> str:
        """The stable identity in these claims, or "" if it is not all there.

        Joined with `:` when a provider needs more than one claim, so a
        Microsoft identity is `tenant:object` and cannot collide with a Google
        `sub`. Missing any part gives "" rather than a partial id — half a
        subject would match the wrong person.
        """
        parts: list[str] = []
        for name in self.subject_claims:
            value = claims.get(name)
            if not value:
                return ""
            parts.append(str(value))
        return ":".join(parts)


GOOGLE = Provider(
    id="google",
    label="Google",
    scopes=("openid", "email", "profile"),
    auth_uri="https://accounts.google.com/o/oauth2/v2/auth",
    token_uri="https://oauth2.googleapis.com/token",
    discovery_url="https://accounts.google.com/.well-known/openid-configuration",
    client_setting="account_client_id",
    loopback=True,
    issuers=frozenset({"https://accounts.google.com", "accounts.google.com"}),
    extra_auth_params={
        # Ask which account rather than silently taking whoever the browser
        # remembers.
        "prompt": "select_account",
        # Without this there is no refresh token, and the session dies the
        # first time the access token expires.
        "access_type": "offline",
    },
)

MICROSOFT = Provider(
    id="microsoft",
    label="Microsoft",
    # `offline_access` is Microsoft's spelling of "give me a refresh token";
    # there is no `access_type` parameter.
    scopes=("openid", "email", "profile", "offline_access"),
    auth_uri="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_uri="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    discovery_url=("https://login.microsoftonline.com/common/v2.0/"
                   ".well-known/openid-configuration"),
    client_setting="account_microsoft_client_id",
    # Microsoft registers `http://localhost` as a native redirect, so the same
    # loopback flow works. Buildable the day a client id exists.
    loopback=True,
    # The tenant id is inside the issuer, so this cannot be an exact match.
    issuer_regex=r"https://login\.microsoftonline\.com/[0-9a-fA-F-]{36}/v2\.0",
    # An `oid` is unique only within a tenant, so the pair is the identity.
    subject_claims=("tid", "oid"),
    extra_auth_params={"prompt": "select_account"},
)

APPLE = Provider(
    id="apple",
    label="Apple",
    scopes=("openid", "email", "name"),
    auth_uri="https://appleid.apple.com/auth/authorize",
    token_uri="https://appleid.apple.com/auth/token",
    discovery_url="https://appleid.apple.com/.well-known/openid-configuration",
    client_setting="account_apple_client_id",
    # **The reason Apple is not available.** It requires a registered https
    # Return URL on a verified domain and refuses loopback, so it cannot be
    # done without a server. Declared anyway so the screen can say that
    # instead of leaving a user wondering why Apple is missing.
    loopback=False,
    issuers=frozenset({"https://appleid.apple.com"}),
    extra_auth_params={
        # Apple returns the name and email only on the FIRST authorization and
        # only in a form POST, which is a second reason it needs a server.
        "response_mode": "form_post",
    },
)

#: Every provider, in the order a screen should offer them.
REGISTRY: dict[str, Provider] = {p.id: p for p in (GOOGLE, MICROSOFT, APPLE)}

#: What a user gets if they never choose.
DEFAULT = GOOGLE.id


def get(provider_id: str | None) -> Provider:
    """One provider by id. Raises `KeyError` naming the real options.

    *"A refusal always names the real options — 'no' without them is an error
    the next attempt repeats"* (`core/CLAUDE.md`).
    """
    found = REGISTRY.get((provider_id or DEFAULT).strip().lower())
    if found is None:
        raise KeyError(f"unknown sign-in provider {provider_id!r}. "
                       f"known: {sorted(REGISTRY)}")
    return found
