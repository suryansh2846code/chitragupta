"""The account — a record that outlives any one sign-in.

**A session and an account are not the same thing**, and conflating them is the
bug this module exists to prevent. A session is "a browser hop happened and here
is a refresh token"; it is a credential, it expires, and it belongs in the
Keychain. An account is "this person is a user of Chitragupta": it has an id we
minted, a creation date, and a list of identities that can *grow* — which is how
Apple gets added later to an account that started with Google.

So they are stored apart, and the split follows the Tier model the backups use
([`docs/ACCOUNTS-DESIGN.md`](../../docs/ACCOUNTS-DESIGN.md) §3):

| | where | travels in a backup? |
|---|---|---|
| refresh / access tokens | Keychain (`session.py`) | **never** — Tier 0 |
| the account record | `account.json` in the home | **yes** — so a restored Mac is still your account |

That second row is the point of having a record at all. Restore onto a new Mac
and it is the same account with the same id and the same linked identities; you
sign in again to get a token, and nothing had to be set up twice.

## Linking, and the rule that is a security rule

**Identities are keyed on `(provider, subject)` and accounts are never
auto-linked by email.** If somebody signs in with Google as `a@example.com` and
later with Microsoft as `a@example.com`, those are two accounts until the user
— *already signed in* — explicitly links the second. Matching on email is an
account-takeover vector: a provider that does not verify address ownership lets
an attacker register the victim's address and inherit their account. Apple's
private relay addresses make email matching meaningless anyway.

The cost is a real support case — *"I signed in with Microsoft and my agents are
gone"* — and it is handled in the UI, never by weakening the rule.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..log import get_logger, suppressed

log = get_logger(__name__)

#: Archivable on purpose — see the table above. Declared in
#: `core/exclusions.py`, because that module fails closed and an undeclared
#: file would be silently left out of every backup.
ACCOUNT_FILE = "account.json"

FORMAT = 1


@dataclass(frozen=True)
class LinkedIdentity:
    """One provider's claim about who this is.

    `subject` is the provider's own stable id — never an email. `email` is kept
    for showing the user which account is which and for nothing else.
    """
    provider: str
    subject: str
    email: str = ""
    name: str = ""
    linked_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.subject}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            # The subject is deliberately absent: a page has no use for it and
            # it is the one field stable enough to correlate people by.
            "email": self.email,
            "name": self.name,
            "linked_at": self.linked_at,
        }


@dataclass
class Account:
    """A user of Chitragupta.

    `id` is ours, random, and minted once. Deliberately **not** the provider's
    subject: an account outlives the identity it was created from, and the
    whole point of linking is that it can be reached by more than one.
    """
    id: str
    created_at: float
    identities: list[LinkedIdentity] = field(default_factory=list)
    display_name: str = ""

    def find(self, provider: str, subject: str) -> LinkedIdentity | None:
        for one in self.identities:
            if one.provider == provider and one.subject == subject:
                return one
        return None

    @property
    def providers(self) -> list[str]:
        return [one.provider for one in self.identities]

    @property
    def primary(self) -> LinkedIdentity | None:
        """The identity the account was created with, for showing a name."""
        return self.identities[0] if self.identities else None

    def as_dict(self) -> dict[str, Any]:
        best = self.primary
        return {
            "id": self.id,
            "created_at": self.created_at,
            "display_name": self.display_name or (best.name if best else ""),
            "email": best.email if best else "",
            "identities": [one.as_dict() for one in self.identities],
            "providers": self.providers,
        }

    def to_json(self) -> str:
        return json.dumps({
            "v": FORMAT,
            "id": self.id,
            "created_at": self.created_at,
            "display_name": self.display_name,
            "identities": [
                {"provider": one.provider, "subject": one.subject,
                 "email": one.email, "name": one.name,
                 "linked_at": one.linked_at}
                for one in self.identities
            ],
        }, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> Account | None:
        try:
            data = json.loads(raw)
            found = cls(
                id=str(data["id"]),
                created_at=float(data.get("created_at") or 0),
                display_name=str(data.get("display_name") or ""),
                identities=[
                    LinkedIdentity(
                        provider=str(one["provider"]),
                        subject=str(one["subject"]),
                        email=str(one.get("email") or ""),
                        name=str(one.get("name") or ""),
                        linked_at=float(one.get("linked_at") or 0),
                    )
                    for one in (data.get("identities") or [])
                    if one.get("provider") and one.get("subject")
                ],
            )
        except Exception:
            log.warning("the stored account record is unreadable — treating "
                        "this Mac as having no account")
            return None
        return found if found.id else None


def _path() -> Path:
    return get_settings().home / ACCOUNT_FILE


def load() -> Account | None:
    """The account on this Mac, or None. Never raises."""
    with suppressed("reading the account record"):
        return Account.from_json(_path().read_text())
    return None


def save(account: Account) -> None:
    with suppressed("writing the account record"):
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(account.to_json())


def exists() -> bool:
    return load() is not None


def create(identity: LinkedIdentity) -> Account:
    """Make an account from the identity that just signed in.

    Called on a **first** sign-in only. The id is random rather than derived
    from the provider's subject, so that linking a second provider later does
    not make one of them the "real" one.
    """
    account = Account(
        id=secrets.token_urlsafe(16),
        created_at=time.time(),
        display_name=identity.name,
        identities=[identity],
    )
    save(account)
    log.info("created an account from a %s sign-in", identity.provider)
    return account


def adopt(provider: str, subject: str, *, email: str = "",
          name: str = "") -> tuple[Account, bool]:
    """Record a sign-in. Returns `(account, created)`.

    Three cases, and the third is the one with the rule in it:

    * **No account yet** → create one. `created` is True, so a caller can show
      a welcome rather than a silent sign-in.
    * **This identity is already on the account** → refresh its email and name
      (people change both) and carry on.
    * **An account exists and this identity is not on it** → the identity is
      added **only because the user was already signed in and asked to link**.
      This function is not where that decision is made; `link` is, and it says
      why. A bare sign-in with an unknown identity on a machine that already
      has an account is refused by the caller, never silently merged.
    """
    existing = load()
    fresh = LinkedIdentity(provider=provider, subject=subject, email=email,
                           name=name, linked_at=time.time())
    if existing is None:
        return create(fresh), True

    already = existing.find(provider, subject)
    if already is None:
        raise AlreadyAnAccountError(
            existing, fresh,
            f"This Mac already has an account. Sign in with "
            f"{existing.providers[0].title()} instead, or link "
            f"{provider.title()} to it from the Account screen.")

    # Same person, same provider: keep the newest email and name, because both
    # change and a stale one on screen looks like the wrong account.
    existing.identities = [
        fresh if one is already else one for one in existing.identities
    ]
    if not existing.display_name and fresh.name:
        existing.display_name = fresh.name
    save(existing)
    return existing, False


class AlreadyAnAccountError(Exception):
    """A different identity signed in on a Mac that already has an account.

    Carries both so the caller can say *which* account and offer to link,
    rather than refusing with nothing to act on.
    """

    def __init__(self, account: Account, offered: LinkedIdentity,
                 message: str) -> None:
        super().__init__(message)
        self.account = account
        self.offered = offered


def link(provider: str, subject: str, *, email: str = "",
         name: str = "") -> Account:
    """Add a second provider to the account that already exists.

    **Only ever called while the user is signed in**, which is the whole
    security argument: the user proved they hold the existing account, then
    proved they hold the new identity, so the link is two demonstrations rather
    than a guess about two matching email addresses. `routes/account.py`
    enforces the signed-in part; nothing here can.
    """
    account = load()
    if account is None:
        raise ValueError("there is no account to link to")
    if account.find(provider, subject) is not None:
        return account
    account.identities.append(
        LinkedIdentity(provider=provider, subject=subject, email=email,
                       name=name, linked_at=time.time()))
    save(account)
    log.info("linked a %s identity to the account", provider)
    return account


def unlink(provider: str) -> Account:
    """Remove a provider from the account.

    Refuses to remove the last one: an account with no identity cannot be
    signed into again, so that is not an unlink, it is a deletion — and
    deleting is `forget`, which says so.
    """
    account = load()
    if account is None:
        raise ValueError("there is no account")
    remaining = [one for one in account.identities if one.provider != provider]
    if not remaining:
        raise ValueError(
            "That is the only way into this account. Link another sign-in "
            "first, or delete the account.")
    account.identities = remaining
    save(account)
    log.info("unlinked a %s identity from the account", provider)
    return account


def forget() -> None:
    """Delete the account record from this Mac.

    Narrow on purpose: the brain, the agents and every connector stay exactly
    as they are. An account was never what made them work.
    """
    with suppressed("deleting the account record"):
        _path().unlink(missing_ok=True)
    log.info("account record removed from this Mac")
