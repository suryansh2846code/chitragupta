"""The user's **account** — who they are to Chitragupta itself, not to a connector.

An account and a session are different things, and keeping them apart is the
shape of this package. A session is a browser hop and a refresh token: a
credential, it expires, it lives in the Keychain (`session.py`). An account is a
record that this person is a user of Chitragupta — an id we minted, when it was
made, and a list of linked identities that can **grow**, which is how Apple gets
added later to an account that began with Google (`store.py`). The account
travels in a backup; the credential never does.

Adding a provider is an entry in `providers.py` plus a client id. Nothing in the
flow, the verifier, the routes or the screen names one.

**This is the first identity the app has ever had of its own.** Everything named
"auth" before it belonged to somebody else: a Google token for reading Gmail, a
Claude subscription read off the CLI, a Notion integration secret. Those answer
*"may we reach this service"*. This answers *"who is using this app"*, which is
a different question with a different lifetime.

## It needs no server, and that is a fact about Google specifically

[`docs/ACCOUNTS-DESIGN.md`](../../docs/ACCOUNTS-DESIGN.md) §2 records that **Sign
in with Apple forbids loopback redirect URIs** — it requires registered `https://`
Return URLs on a verified domain, so Apple cannot be done without a backend.
Google does not: RFC 8252's native-app flow over a loopback redirect with PKCE is
exactly what it is for, and the app already uses it for connectors
(`connectors/google_auth.py`). So **Google-only sign-in is buildable with zero
backend**, and this package is that.

The consequence, stated plainly rather than discovered later: **local identity is
not enforceable entitlement.** The app verifies Google's ID token itself, so it
knows who signed in — but anyone who can edit the app can make it believe
anything. That is fine for what this is for (knowing the user, naming a backup
set, personalising a greeting) and **not** fine for gating payment. Enforcing a
paid tier needs a server, and there isn't one; see §8 of the design.

## Signing in is never required

CLAUDE.md: *"Assume nothing is installed and nothing is configured. First launch,
no keys, no CLIs, no accounts — it must still open and explain itself."*

So **nothing in this package is ever on the path of a local feature.** No turn,
no recall, no connector, no backup consults a session. If this whole package
failed to import, the app would work. `tests/test_account_signin.py` asserts that
rather than trusting it.

## It is not the Gmail connector, and must never become it

| | this | `connectors/google_auth.py` |
|---|---|---|
| scopes | `openid email profile` | `gmail.*`, `drive.*`, `calendar.*` |
| asks for | your name and email | reading and sending your mail |
| token | `account.json`, Keychain-backed | `google_token.json` |
| signing out | leaves Gmail connected | leaves you signed in |

Two tokens, two consents, two lifetimes. A first-run screen asking to read
somebody's mail is the thing they quit over, and CLAUDE.md's *"detection is not
consent"* cuts the other way too: proving the user *has* a Google account must
never mark Gmail connected.
"""
from __future__ import annotations

from .providers import REGISTRY, Provider
from .session import (
    Identity,
    delete_account,
    forget,
    signed_in,
    state,
    who,
)
from .store import Account, LinkedIdentity

__all__ = [
    "REGISTRY",
    "Account",
    "Identity",
    "LinkedIdentity",
    "Provider",
    "delete_account",
    "forget",
    "signed_in",
    "state",
    "who",
]
