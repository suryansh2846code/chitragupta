"""Who is signed in, and the one licence this Mac holds.

Stored in the **macOS Keychain**, not a file: it holds a refresh token, which is
a live credential for the user's Google account. The brain is a plaintext SQLite
beside it and that is a deliberate trade, but a credential is not, and the rest
of the app already puts those here.

**Identity is keyed on `(provider, subject)`, never on email.**
[`docs/ACCOUNTS-DESIGN.md`](../../docs/ACCOUNTS-DESIGN.md) §2 records why: an
email address is something a provider may let go of and reissue, so matching on
one is how an account is taken over. Google's `sub` is opaque and permanent. The
email is kept for *showing* the user who they are and for nothing else.

**One Mac per licence**, which the user chose — and one honest caveat. Nothing
here can enforce it: there is no server, so the device id below is a record, not
a gate. It is what a licensing server would check *later*, and it exists now so
the id is already stable when one arrives rather than being minted after people
have installed. Pretending otherwise would be the "control that cannot work"
this codebase keeps warning about.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings
from ..log import get_logger
from . import store
from .providers import Provider

log = get_logger(__name__)

#: One Keychain entry for the whole session.
SESSION_SECRET = "ACCOUNT_SESSION"

#: This install's id. Random, local, and **not** derived from any hardware
#: serial: a hardware id follows a person across reinstalls and across apps,
#: which is tracking rather than licensing. A random id can be reset by the user
#: deleting the app, which is the correct amount of permanence.
DEVICE_SECRET = "ACCOUNT_DEVICE_ID"


@dataclass
class Identity:
    """The signed-in user, as the UI needs them.

    No tokens on this object. `who()` hands it to an endpoint and an endpoint
    hands it to a page, and a refresh token has no business on either journey.
    """
    provider: str
    subject: str
    email: str = ""
    name: str = ""
    picture: str = ""
    email_verified: bool = False
    signed_in_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            # The id is deliberately absent. A page has no use for it, and it is
            # the one field that is stable enough to correlate people by.
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "email_verified": self.email_verified,
            "signed_in_at": self.signed_in_at,
        }


@dataclass
class Session:
    """What is actually stored: the identity plus the credential."""
    identity: Identity
    refresh_token: str = ""
    access_token: str = ""
    expires_at: float = 0.0
    scopes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "v": 1,
            "provider": self.identity.provider,
            "sub": self.identity.subject,
            "email": self.identity.email,
            "name": self.identity.name,
            "picture": self.identity.picture,
            "email_verified": self.identity.email_verified,
            "signed_in_at": self.identity.signed_in_at,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "scopes": list(self.scopes),
        })

    @classmethod
    def from_json(cls, raw: str) -> Session | None:
        try:
            data = json.loads(raw)
            who_it_is = Identity(
                provider=str(data["provider"]),
                subject=str(data["sub"]),
                email=str(data.get("email") or ""),
                name=str(data.get("name") or ""),
                picture=str(data.get("picture") or ""),
                email_verified=bool(data.get("email_verified")),
                signed_in_at=float(data.get("signed_in_at") or 0),
            )
        except Exception:
            log.warning("the stored account session is unreadable — treating "
                        "nobody as signed in")
            return None
        if not who_it_is.subject:
            return None
        return cls(
            identity=who_it_is,
            refresh_token=str(data.get("refresh_token") or ""),
            access_token=str(data.get("access_token") or ""),
            expires_at=float(data.get("expires_at") or 0),
            scopes=[str(s) for s in (data.get("scopes") or [])],
        )


def session() -> Session | None:
    raw = get_settings().get_secret(SESSION_SECRET)
    return Session.from_json(raw) if raw else None


def remember(found: Session) -> None:
    get_settings().set_secret(SESSION_SECRET, found.to_json())
    log.info("signed in to Chitragupta as %s", found.identity.provider)


def who() -> Identity | None:
    """The signed-in user, or None. **Never raises**, and never blocks.

    Every caller is allowed to ignore the answer: nothing local depends on it.
    """
    found = session()
    return found.identity if found else None


def signed_in() -> bool:
    return session() is not None


def forget() -> None:
    """Sign out of Chitragupta.

    Deliberately narrow. It drops *our* session and touches nothing else — not
    `google_token.json`, not a provider key, not one row of the brain. CLAUDE.md:
    *"Credentials are independent: removing a key must not sign the user out"*,
    and the inverse is just as true. A user signing out of the app has not asked
    to disconnect their mail.
    """
    get_settings().set_secret(SESSION_SECRET, None)
    log.info("signed out of Chitragupta")


def delete_account() -> None:
    """Sign out **and** forget the account record.

    Separate from `forget` because they are different things a user means:
    signing out keeps the account so the next sign-in returns to it, while
    this is "there is no account on this Mac any more". Neither touches the
    brain, the agents or one connector.
    """
    forget()
    store.forget()


def device_id() -> str:
    """This install's id, minted once and kept.

    Random rather than derived from a hardware serial — see `DEVICE_SECRET`.
    """
    settings = get_settings()
    existing = settings.get_secret(DEVICE_SECRET)
    if existing and len(existing) >= 16:
        return existing
    fresh = secrets.token_urlsafe(16)
    settings.set_secret(DEVICE_SECRET, fresh)
    log.info("minted this install's device id")
    return fresh


def state() -> dict[str, Any]:
    """What the Account screen needs. Safe to call before anything is set up.

    Reports the **account** and the **session** separately, because they can
    disagree in a way the user needs to see: an account exists on this Mac
    (restored from a backup, or signed out of) while nobody is currently
    signed in.
    """
    current = who()
    account = store.load()
    return {
        "signed_in": current is not None,
        "user": current.as_dict() if current else None,
        "has_account": account is not None,
        "account": account.as_dict() if account else None,
        "device_id": device_id(),
        # Said in the API rather than only in a doc, because a page that claims
        # a limit is enforced when it is not is the dishonest version of this.
        "devices_per_licence": 1,
        "enforced": False,
        "skippable": True,
    }


def adopt(provider: Provider, claims: dict[str, Any], *,
          refresh_token: str = "", access_token: str = "",
          expires_in: float = 0.0,
          scopes: list[str] | None = None,
          link_to_existing: bool = False) -> Identity:
    """Store a session from **already verified** claims, and the account with it.

    It takes claims rather than a raw token on purpose: verification lives in
    `tokens.verify`, and a function that accepted a token here would be a second
    place that had to remember to check the signature.

    **The subject comes from the provider**, never from `claims["sub"]` directly
    — Microsoft's stable identity is `tid` + `oid`, and reading `sub` for every
    provider is how two tenants' users end up sharing an id.

    `link_to_existing` is the signed-in user explicitly adding a second
    provider. Without it, an unknown identity on a Mac that already has an
    account raises `store.AlreadyAnAccountError` rather than being merged —
    silently merging on a matching email is the account-takeover vector
    `store` documents.
    """
    subject = provider.subject_of(claims)
    if not subject:
        raise ValueError(
            f"a verified {provider.label} ID token must carry "
            f"{' and '.join(provider.subject_claims)}")

    email = str(claims.get("email") or "")
    # Apple and Microsoft spell the display name differently, and Apple sends
    # it only on a first authorization — so a missing one is normal, not an
    # error, and the email stands in.
    name = str(claims.get("name") or claims.get("given_name") or "")

    if link_to_existing:
        store.link(provider.id, subject, email=email, name=name)
    else:
        # Raises AlreadyAnAccountError when a different identity signs in on a
        # Mac that already has an account; the route turns that into an offer
        # to link rather than a dead end.
        store.adopt(provider.id, subject, email=email, name=name)

    found = Session(
        identity=Identity(
            provider=provider.id,
            subject=subject,
            email=email,
            name=name,
            picture=str(claims.get("picture") or ""),
            email_verified=bool(claims.get("email_verified")),
            signed_in_at=time.time(),
        ),
        refresh_token=refresh_token,
        access_token=access_token,
        expires_at=(time.time() + expires_in) if expires_in else 0.0,
        scopes=list(scopes or []),
    )
    remember(found)
    return found.identity
