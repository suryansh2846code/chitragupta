"""One account, with an identity of its own, and the lifecycle it moves through.

`provider == account` was baked into storage: `connector_state` in
`core/db.py` is `PRIMARY KEY (connector)`, and `google_auth._token_path()` is
one file. So a user with a personal and a work Gmail had one Gmail, whichever
they signed into last, and no amount of care above that layer could change it.

A `Connection` is the identity everything else references — credentials,
checkpoints, provenance, permissions, health. It is a **stable id**, not the
account address: an address can be renamed at the vendor, and a key that
changes underneath the rows that point at it is not a key.

    Provider (google)
    └── Connection gmail:a1b2      account: me@personal.test
    └── Connection gmail:c3d4      account: me@work.test

## The lifecycle, and the two transitions that matter

    CONNECTING → AUTHENTICATED → (EXPIRED → AUTHENTICATED)
                             ↘ REAUTH_REQUIRED → CONNECTING
                             ↘ REVOKED
                             ↘ ERROR
    any → DISCONNECTED

* **`EXPIRED` is not `REAUTH_REQUIRED`.** An expired access token with a live
  refresh token is a thing this app fixes silently; a refresh that fails is a
  thing only the user can fix. Collapsing them means either bothering the user
  for something automatic, or silently retrying something that will never work
  — and the second is the loop that locks accounts out.
* **A 403 never moves a connection to `REAUTH_REQUIRED`.** A scope problem
  survives signing in again, so the OAuth round trip teaches the user the app
  is broken. `errors.ConnectorError.needs_reauth` is authentication only, and
  that is the whole reason it exists as a separate property.

## What this does not hold

**No credentials.** They live where they already live — the Keychain via
`settings.set_secret`, `google_token.json`, the Telegram session. This holds
the *state* of a credential, which is a different thing and is safe to show on
a screen. `docs/CONNECTOR-PLATFORM.md` §6.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..log import get_logger
from .db import connection as _db
from .db import row_to_dict

log = get_logger(__name__)


class AuthState(StrEnum):
    """Where one connection's credential stands."""

    #: Nothing set up, or the user disconnected it. The resting state.
    DISCONNECTED = "disconnected"
    #: A sign-in is in flight. Distinguishable from disconnected so the UI can
    #: show progress and offer Cancel — "anything the user starts, they can
    #: stop" applies to sign-in first.
    CONNECTING = "connecting"
    AUTHENTICATED = "authenticated"
    #: The access token has run out and a refresh has not been tried yet.
    #: Ours to fix, silently.
    EXPIRED = "expired"
    #: The refresh failed, or there was never a refresh token. Only the user
    #: can fix this, and they must be told.
    REAUTH_REQUIRED = "reauth_required"
    #: The user (or an admin) took access away at the vendor. Not an error and
    #: not something to retry: re-asking is the app arguing with a decision.
    REVOKED = "revoked"
    #: Something else went wrong that is not about the credential.
    ERROR = "error"


#: States in which a sync may run at all. Everything else pauses the
#: connection's jobs, which is the point: a connector that keeps syncing
#: against a dead credential produces one identical error every thirty minutes
#: and turns a fixable problem into background noise.
RUNNABLE = frozenset({AuthState.AUTHENTICATED, AuthState.EXPIRED})

#: States the user has to act on. The UI shows these; the others are ours.
NEEDS_THE_USER = frozenset({AuthState.REAUTH_REQUIRED, AuthState.REVOKED})

#: What each state says, in the user's terms. One sentence, naming the thing to
#: do — a status word on its own is a diagnosis without a treatment.
SENTENCE: dict[AuthState, str] = {
    AuthState.DISCONNECTED: "Not connected.",
    AuthState.CONNECTING: "Signing in…",
    AuthState.AUTHENTICATED: "Connected.",
    AuthState.EXPIRED: "Refreshing access…",
    AuthState.REAUTH_REQUIRED: "Sign in again to keep this up to date.",
    AuthState.REVOKED: "Access was removed. Reconnect to start again.",
    AuthState.ERROR: "Something went wrong. Try reconnecting.",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Connection:
    """One account on one connector."""

    id: str
    connector: str
    provider: str = ""
    #: What the vendor calls this account — an address, a workspace, a handle.
    #: Display only: the *key* is `id`, because an address can be renamed.
    account: str = ""
    #: What the user calls it. "Work", "Personal". Their word beats ours.
    label: str = ""
    auth_state: AuthState = AuthState.DISCONNECTED
    #: Why, when the state needs a why. Scrubbed before it is stored.
    auth_detail: str = ""
    scopes: list[str] = field(default_factory=list)
    version: int = 1
    paused: bool = False
    created_at: str = ""
    updated_at: str = ""

    @property
    def runnable(self) -> bool:
        """May a sync run against this connection right now?"""
        return not self.paused and self.auth_state in RUNNABLE

    @property
    def needs_the_user(self) -> bool:
        return self.auth_state in NEEDS_THE_USER

    @property
    def display(self) -> str:
        """What to put on the row. The user's own label wins, then the vendor's
        account, then the connector — never an internal id."""
        return self.label or self.account or self.connector

    def missing_scopes(self, required: tuple[str, ...] | list[str]) -> list[str]:
        """Required scopes this connection was not granted.

        Only meaningful when the vendor tells us what was granted — an empty
        `scopes` means *we do not know*, not *none*, so nothing is reported
        missing. Guessing would put a warning on a working connector, which is
        the failure mode `/CLAUDE.md` calls a control that cannot work.
        """
        if not self.scopes:
            return []
        have = set(self.scopes)
        return [s for s in required if s not in have]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "connector": self.connector,
                "provider": self.provider, "account": self.account,
                "label": self.label, "display": self.display,
                "auth_state": self.auth_state.value,
                "auth_detail": self.auth_detail,
                "says": SENTENCE[self.auth_state],
                "scopes": list(self.scopes), "version": self.version,
                "paused": self.paused, "runnable": self.runnable,
                "needs_the_user": self.needs_the_user,
                "created_at": self.created_at, "updated_at": self.updated_at}


def _from_row(row: Any) -> Connection:
    data = row_to_dict(row)
    scopes: list[str] = []
    try:
        parsed = json.loads(data.get("scopes") or "[]")
        scopes = [str(s) for s in parsed] if isinstance(parsed, list) else []
    except ValueError:
        # A scopes blob we cannot read is a scopes blob we ignore. Reporting
        # "no scopes" would be wrong in the other direction — `missing_scopes`
        # already treats empty as "we do not know".
        log.debug("connection %s has unreadable scopes", data.get("id"))
    state = AuthState.DISCONNECTED
    try:
        state = AuthState(data.get("auth_state") or "disconnected")
    except ValueError:
        # A state written by a newer version. Resting state rather than a
        # crash, and the user can reconnect.
        log.debug("connection %s has an unknown auth state %r",
                  data.get("id"), data.get("auth_state"))
    return Connection(
        id=data["id"], connector=data["connector"],
        provider=data.get("provider") or "", account=data.get("account") or "",
        label=data.get("label") or "", auth_state=state,
        auth_detail=data.get("auth_detail") or "", scopes=scopes,
        version=int(data.get("version") or 1),
        paused=bool(data.get("paused")),
        created_at=data.get("created_at") or "",
        updated_at=data.get("updated_at") or "")


def new_id(connector: str) -> str:
    """A stable id that is not the account address.

    Prefixed with the connector so a row is legible in a database browser and
    in a log line, and suffixed with randomness so renaming the account at the
    vendor does not orphan every checkpoint pointing at it.
    """
    return f"{connector}:{uuid.uuid4().hex[:8]}"


def ensure(connector: str, *, account: str = "", provider: str = "",
           label: str = "", version: int = 1) -> Connection:
    """The connection for this connector and account, creating it if needed.

    Idempotent on `(connector, account)`, which is what makes it safe to call
    at the top of every sync: the existing connectors have one account each and
    an empty `account` is that case, so they get exactly one row forever.
    """
    conn = _db()
    found = conn.execute(
        "SELECT * FROM connector_connections WHERE connector=? AND account=?",
        (connector, account)).fetchone()
    if found is not None:
        return _from_row(found)

    now = _now()
    made = Connection(id=new_id(connector), connector=connector,
                      provider=provider or connector, account=account,
                      label=label, version=version,
                      created_at=now, updated_at=now)
    conn.execute(
        """INSERT INTO connector_connections
             (id, connector, provider, account, label, auth_state, auth_detail,
              scopes, version, paused, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (made.id, made.connector, made.provider, made.account, made.label,
         made.auth_state.value, "", "[]", made.version, 0, now, now))
    conn.commit()
    return made


def get(connection_id: str) -> Connection | None:
    row = _db().execute("SELECT * FROM connector_connections WHERE id=?",
                        (connection_id,)).fetchone()
    return _from_row(row) if row is not None else None


def for_connector(connector: str) -> list[Connection]:
    """Every account on one connector, oldest first."""
    rows = _db().execute(
        "SELECT * FROM connector_connections WHERE connector=? "
        "ORDER BY created_at", (connector,)).fetchall()
    return [_from_row(r) for r in rows]


def all_connections() -> list[Connection]:
    rows = _db().execute(
        "SELECT * FROM connector_connections ORDER BY connector, created_at"
    ).fetchall()
    return [_from_row(r) for r in rows]


def _set(connection_id: str, **columns: Any) -> Connection | None:
    if not columns:
        return get(connection_id)
    columns["updated_at"] = _now()
    assignments = ", ".join(f"{name}=?" for name in columns)
    conn = _db()
    conn.execute(f"UPDATE connector_connections SET {assignments} WHERE id=?",
                 (*columns.values(), connection_id))
    conn.commit()
    return get(connection_id)


def transition(connection_id: str, state: AuthState, *, detail: str = "",
               scopes: list[str] | None = None) -> Connection | None:
    """Move a connection to a new auth state.

    `detail` is scrubbed on the way in, because it is the field a vendor's own
    refusal lands in and it is shown on a screen. A credential reaching here
    would be a credential on the Connectors page.
    """
    from .errors import scrub

    columns: dict[str, Any] = {"auth_state": state.value,
                               "auth_detail": scrub(detail)[:300]}
    if scopes is not None:
        columns["scopes"] = json.dumps(sorted(set(scopes)))
    return _set(connection_id, **columns)


def on_error(connection_id: str, error: Any) -> Connection | None:
    """Apply what one failure means for the credential, and nothing more.

    The whole point is what it does *not* do. A 403 is a scope problem and
    leaves the state alone: signing in again produces the same credential with
    the same permissions, so offering the loop teaches the user the app is
    broken. A network blip is not about the credential at all.
    """
    if getattr(error, "needs_reauth", False):
        return transition(connection_id, AuthState.REAUTH_REQUIRED,
                          detail=getattr(error, "message", ""))
    return get(connection_id)


def pause(connection_id: str, *, paused: bool = True) -> Connection | None:
    """Stop (or resume) this connection's scheduled work, without signing out.

    A distinct control from disconnecting, and the distinction is the product
    one `docs/CONNECTOR-PLATFORM.md` §13 insists on: pausing keeps the
    credential and the indexed data and stops the traffic; disconnecting gives
    the credential back. Neither deletes what was already learned.
    """
    return _set(connection_id, paused=1 if paused else 0)


def disconnect(connection_id: str, *, detail: str = "") -> Connection | None:
    """Give the credential back and stop everything scheduled.

    **Does not delete indexed data.** That is a separate, explicit act — a user
    disconnecting Gmail has not asked to forget every email they ever read, and
    conflating the two makes disconnect a button nobody dares press.
    """
    return transition(connection_id, AuthState.DISCONNECTED, detail=detail,
                      scopes=[])


def forget(connection_id: str) -> bool:
    """Remove the connection row and everything keyed to it.

    The row, its checkpoints, its resource identities and its events. Not the
    memories: those are the user's, and `brain` owns forgetting them.
    """
    conn = _db()
    existed = conn.execute("SELECT 1 FROM connector_connections WHERE id=?",
                           (connection_id,)).fetchone() is not None
    for table in ("connector_sync_state", "connector_resources",
                  "connector_events"):
        conn.execute(f"DELETE FROM {table} WHERE connection_id=?",
                     (connection_id,))
    conn.execute("DELETE FROM connector_connections WHERE id=?",
                 (connection_id,))
    conn.commit()
    return existed
