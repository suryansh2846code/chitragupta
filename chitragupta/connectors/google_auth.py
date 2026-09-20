"""Shared Google OAuth (Desktop flow) for Gmail + Drive connectors.

Tokens are cached under CHITRAGUPTA_HOME so the browser consent only happens once.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import get_settings
from ..log import suppressed

# Read scopes + narrow WRITE scopes for confirmed actions (send email, create
# event, triage the inbox). Reading never modifies data; writes only run after
# explicit user confirmation.
#
# `gmail.send` can only send — it cannot touch a message that already exists.
# That is why an agent could compose mail for a year and still not archive
# anything: archiving, labelling and marking read are all *modifications of an
# existing message*, and Google puts every one of them behind `gmail.modify`.
#
# `gmail.modify` is the narrowest scope that allows them. It does not permit
# permanent deletion — that is the full-mailbox scope, which we do not ask for
# and do not want. Trashing is possible under it; we deliberately expose no
# tool that does (see `docs/development/mail-triage.md`).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive.readonly",
    # `drive.file`, NOT `drive`. It grants access only to files this app
    # itself created — so a document we write can be read back, shared and
    # trashed, and the rest of the user's Drive stays as unreachable for
    # writing as it was. The full `drive` scope would have bought nothing the
    # 18 jobs need and asked for everything.
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def _token_path() -> Path:
    return get_settings().home / "google_token.json"


def get_credentials(interactive: bool = True):
    """Return valid Google credentials, running the consent flow if needed.

    Self-healing: a corrupt token file, or a refresh token that has been
    revoked/expired (e.g. the 7-day expiry of Google 'Testing'-mode apps), is
    dropped and re-consented instead of failing every sync forever."""
    from google.auth.exceptions import RefreshError  # lazy
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    settings = get_settings()
    token_path = _token_path()
    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            token_path.unlink(missing_ok=True)     # corrupt token → re-consent
            creds = None

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            return creds
        except RefreshError:
            # refresh token revoked/expired → drop it and fall through to consent
            token_path.unlink(missing_ok=True)
            creds = None

    if not interactive:
        raise RuntimeError(
            "Google sign-in expired or missing. Open Chitragupta and reconnect "
            "Google (Connectors → Sign in with Google) to re-authorize.")

    secrets = settings.google_client_secrets
    if not secrets or not Path(secrets).exists():
        raise RuntimeError(
            "Missing Google OAuth client secrets. Create a Desktop OAuth client in "
            "Google Cloud Console and set GOOGLE_CLIENT_SECRETS to its JSON path "
            f"(or drop it at {settings.home / 'google_client_secret.json'})."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json())
    return creds


_SCOPE_SERVICES = [("gmail", "Gmail"), ("drive", "Drive"), ("calendar", "Calendar")]


def granted_services() -> list[str]:
    """Friendly names of the services the stored token is authorized for."""
    if not _token_path().exists():
        return []
    try:
        scopes = json.loads(_token_path().read_text()).get("scopes", [])
    except Exception:
        return []
    return [label for key, label in _SCOPE_SERVICES
            if any(key in s for s in scopes)]


def granted_scopes() -> list[str]:
    """The exact scope strings the stored token carries."""
    if not _token_path().exists():
        return []
    with suppressed("reading the scopes Google granted"):
        return list(json.loads(_token_path().read_text()).get("scopes", []))
    return []


def may_modify_mail() -> bool:
    """Can we change an existing message — archive, label, mark read?

    Not derivable from "is Gmail connected". A token issued before we asked for
    `gmail.modify` carries read and send and nothing else, so every triage call
    it makes comes back 403. Asked *before* proposing anything, so the user is
    told to reconnect instead of approving a card that cannot work.
    """
    return any("gmail.modify" in s for s in granted_scopes())


#: What to tell the user when the token predates the modify scope. Named once
#: because the connector, the action and the agent's prompt all say it.
NEEDS_MODIFY_SCOPE = (
    "Gmail is connected for reading and sending, but not for changing messages. "
    "Reconnect Google under Connectors and approve the extra permission, then "
    "this will work."
)


def may_write_drive() -> bool:
    """Can we create a document in the user's Drive?

    Not derivable from "is Drive connected". A token issued before we asked
    for `drive.file` carries read and nothing else, so every create it makes
    comes back 403. Asked BEFORE proposing anything, exactly as
    `may_modify_mail` is, so the user is told to reconnect rather than
    approving a card that cannot work.
    """
    return any("drive.file" in s for s in granted_scopes())


#: What to tell the user when the token predates the drive.file scope. Named
#: once because the connector, the action and the agent's prompt all say it.
NEEDS_DRIVE_SCOPE = (
    "Google Drive is connected for reading, but not for creating documents. "
    "Reconnect Google under Connectors and approve the extra permission, then "
    "this will work."
)


def _account_path() -> Path:
    return get_settings().home / "google_account.json"


def connected_email(fetch: bool = True) -> str | None:
    """The signed-in Google address. Cached locally; fetched once via Gmail
    getProfile when connected (no extra scope needed)."""
    ap = _account_path()
    if ap.exists():
        with suppressed("return json.loads(ap.read_text()).get('email')"):
            return json.loads(ap.read_text()).get("email")
    if not fetch or not _token_path().exists():
        return None
    try:
        from googleapiclient.discovery import build
        creds = get_credentials(interactive=False)
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        email = svc.users().getProfile(userId="me").execute().get("emailAddress")
        if email:
            ap.write_text(json.dumps({"email": email}))
        return email
    except Exception:
        return None


def disconnect() -> None:
    """Sign out of Google: remove the local token + cached account."""
    for p in (_token_path(), _account_path()):
        with suppressed("p.unlink(missing_ok=True)"):
            p.unlink(missing_ok=True)


def google_ready() -> tuple[bool, str]:
    settings = get_settings()
    if _token_path().exists():
        return True, ""
    if settings.google_client_secrets and Path(settings.google_client_secrets).exists():
        return True, "will prompt for consent on first sync"
    return False, "no Google OAuth client secrets configured"
