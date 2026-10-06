"""Making an account, signing into it, and linking a second provider to it.

**Sign-in is never required**, so every path here is additive: a user who never
touches it loses nothing, and `GET /state` works before any client is
configured.

**Starting a sign-in runs in the probe lane** (`@probes_a_provider`): it opens a
loopback listener and waits up to three minutes for a browser round trip, which
is exactly the cost class `api/concurrency.py` exists to keep off the shared
worker pool.

**The attempt lives on the server, never in the page.** The PKCE verifier is the
one value that must not travel: handing it to the browser so it could be sent
back would defeat the thing it is there for. So `/begin` keeps it in module
state, returns only the URL to open, and `/finish` matches the callback against
it.

**No provider is named in this module.** The id arrives in the request and
`account.providers.get` resolves it, so Apple and Microsoft need no route of
their own — the same rule `automation/` holds about connectors, one layer over.
"""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...account import oauth, providers, session, store, tokens
from ...log import get_logger
from ..concurrency import probes_a_provider

log = get_logger(__name__)
router = APIRouter()

#: The one sign-in in flight. One at a time: a second attempt would overwrite
#: the first's verifier and make both unredeemable, and there is no reason for a
#: person to be signing in twice at once.
_lock = threading.Lock()
_attempt: oauth.Attempt | None = None
#: Whether the attempt in flight is adding a provider to an account the user is
#: already signed into, rather than signing in. Kept beside the attempt because
#: it is a property of *this* attempt, and reading it from the request at
#: `/finish` would let a page turn a sign-in into a link after the fact.
_linking = False


class BeginIn(BaseModel):
    provider: str = Field(default=providers.DEFAULT, max_length=32)
    #: Add this provider to the account already signed in, rather than signing
    #: in. Refused unless somebody *is* signed in — see `/begin`.
    link: bool = False


class UnlinkIn(BaseModel):
    provider: str = Field(min_length=1, max_length=32)


def _provider_rows() -> list[dict[str, Any]]:
    """Every provider, with whether it can be used and why not.

    All of them, not only the usable ones: a provider absent from the list is
    indistinguishable from one nobody thought about, while one that is present
    and unavailable carries its reason. The screen draws those with the
    `data-soon` lock the rest of the app uses.
    """
    account = store.load()
    linked = set(account.providers) if account else set()
    rows = []
    for one in providers.REGISTRY.values():
        usable, why = oauth.is_available(one)
        rows.append({
            "id": one.id,
            "label": one.label,
            "available": usable,
            "reason": why,
            "scopes": list(one.scopes),
            "linked": one.id in linked,
        })
    return rows


@router.get("/api/account/state")
def account_state() -> dict[str, Any]:
    """The account, who is signed in, and which providers can be used.

    Cheap, and called on every Account screen open. The account and the session
    are reported separately because they can disagree in a way the user needs
    to see — an account restored from a backup, with nobody signed in yet.
    """
    answer = session.state()
    answer["providers"] = _provider_rows()
    answer["signing_in_optional"] = True
    # Said out loud: the whole point of this being separate from the Gmail
    # connector is something the user has to be able to see.
    answer["asks_for"] = "your name and email address"
    return answer


@router.post("/api/account/signin/begin")
def account_signin_begin(body: BeginIn | None = None) -> dict[str, Any]:
    """Mint an attempt and hand back the URL to open in the browser."""
    global _attempt, _linking
    body = body or BeginIn()
    try:
        provider = providers.get(body.provider)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    usable, why = oauth.is_available(provider)
    if not usable:
        raise HTTPException(status_code=400, detail=why)

    if body.link and not session.signed_in():
        # Linking is two demonstrations: that the user holds the existing
        # account, and that they hold the new identity. Without the first it is
        # a merge on somebody's word, which is the takeover `store` documents.
        raise HTTPException(
            status_code=409,
            detail="Sign in first, then link another way of signing in.")

    with _lock:
        _attempt = oauth.begin(provider)
        _linking = bool(body.link)
        return {"url": _attempt.authorize_url, "port": _attempt.port,
                "provider": provider.id, "linking": _linking}


@router.post("/api/account/signin/finish")
@probes_a_provider
def account_signin_finish() -> dict[str, Any]:
    """Wait for the browser, verify, and create or sign into the account.

    Blocking, in the probe lane, for up to `oauth.TIMEOUT`. The page calls it
    straight after `/begin` and shows what it is waiting for.
    """
    global _attempt, _linking
    with _lock:
        attempt, linking = _attempt, _linking
    if attempt is None:
        raise HTTPException(status_code=409, detail="no sign-in was started")

    try:
        code = oauth.wait_for_code(attempt)
        who = oauth.exchange(attempt, code, link_to_existing=linking)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=408,
            detail="The sign-in was not finished. Try again.") from exc
    except store.AlreadyAnAccountError as exc:
        # Not a dead end: the page is told which account exists and can offer
        # to link instead of leaving the user with nothing to do.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except tokens.TokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # Includes the provider's own `access_denied`, which is the user
        # deciding rather than anything being wrong.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        with _lock:
            # The verifier has done its job either way and must not linger.
            _attempt, _linking = None, False

    answer = account_state()
    answer["user"] = who.as_dict()
    return answer


@router.post("/api/account/signin/cancel")
def account_signin_cancel() -> dict[str, Any]:
    """Drop an attempt in flight.

    CLAUDE.md: *anything the user starts, they can stop.* The loopback listener
    times out on its own, so this is about the verifier — an abandoned attempt
    should not stay redeemable.
    """
    global _attempt, _linking
    with _lock:
        had_one = _attempt is not None
        _attempt, _linking = None, False
    return {"cancelled": had_one}


@router.post("/api/account/unlink")
def account_unlink(body: UnlinkIn) -> dict[str, Any]:
    """Remove one way of signing in from the account.

    Refuses to remove the last one: an account nobody can sign into is not an
    unlink, it is a deletion, and that is `/delete` which says so.
    """
    if not session.signed_in():
        raise HTTPException(status_code=409,
                            detail="Sign in first to change your account.")
    try:
        store.unlink(body.provider.strip().lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return account_state()


@router.post("/api/account/signout")
def account_signout() -> dict[str, Any]:
    """Sign out, and keep the account.

    Deliberately narrow: it does not touch `google_token.json`, a provider key,
    the account record, or one row of the brain. A user signing out of the app
    has not asked to disconnect their mail or to lose their account.
    """
    session.forget()
    return account_state()


@router.post("/api/account/delete")
def account_delete() -> dict[str, Any]:
    """Forget the account on this Mac entirely.

    Still narrow, and worth being explicit about because the word is alarming:
    this removes **our** record and session. The brain, every agent, every
    connector and every backup stay exactly as they are — an account was never
    what made them work.
    """
    session.delete_account()
    return account_state()
