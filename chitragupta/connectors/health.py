"""Is this connector working, answered without making it do any work.

`connector_state.status` held `"ok"` or `"error"`, written by `_finish` at the
end of a sync. Two words, and neither of them is any of the things a user
actually needs to be told:

* *signed in, but the token expires tomorrow*
* *signed in, and Slack is rate-limiting us*
* *working, and the last successful sync was nine days ago*
* *paused, on purpose, by you*
* *syncing right now*

Each of those leads somewhere different, and `"error"` leads nowhere.

## A health check is cheap or it is not a health check

The Connectors page calls one per row on load. `MCPConnector.is_configured()`
starts a subprocess to answer it, which is honest — *"does this work"* is only
truly answerable by using it — and is why that endpoint lives in the probe lane.
This module is the other question: **what do we already know**, from state that
was written by work that already happened.

So `of()` performs no I/O at all. It reads the connection, the checkpoints and
the gate. `probe()` is the one that may reach out, it is opt-in, and it is the
narrow four-part question — credentials valid, provider reachable, scopes
present, configuration usable — never a sync.

## Staleness is relative to the connector's own schedule

*"Last synced two hours ago"* is alarming for a mailbox and unremarkable for a
file the user exported. So the threshold is derived from the sync interval and
whether the connector is scheduled at all: a manual source is never stale,
because nothing was supposed to happen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..log import get_logger
from .connections import AuthState, Connection
from .contract import ConnectorManifest
from .sync_state import SyncState

log = get_logger(__name__)

#: How many missed intervals before a scheduled connector reads as stale.
#: Three rather than one: a sync that was cancelled, or that the machine slept
#: through, is not a problem, and a badge that goes amber every time a laptop
#: closes is a badge people learn to ignore.
STALE_AFTER_INTERVALS = 3


class HealthState(StrEnum):
    """Ordered worst-last, so the worst of several is `max()`."""

    HEALTHY = "healthy"
    SYNCING = "syncing"
    #: Working, but not as well as it should — stale, or partially truncated.
    DEGRADED = "degraded"
    RATE_LIMITED = "rate_limited"
    #: The user has to do something.
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"
    #: Not set up, or switched off. Not a problem, and not "healthy" either.
    DISCONNECTED = "disconnected"


#: Severity for `worst()`. `DISCONNECTED` is deliberately **not** the worst:
#: a connector the user has not set up is not a fault, and sorting it above a
#: real error would bury the real error.
_SEVERITY: dict[HealthState, int] = {
    HealthState.HEALTHY: 0,
    HealthState.SYNCING: 1,
    HealthState.DISCONNECTED: 2,
    HealthState.DEGRADED: 3,
    HealthState.RATE_LIMITED: 4,
    HealthState.AUTH_REQUIRED: 5,
    HealthState.ERROR: 6,
}


def worst(states: Any) -> HealthState:
    found = list(states)
    return max(found, key=lambda s: _SEVERITY[s]) if found else \
        HealthState.DISCONNECTED


@dataclass
class Health:
    """One connection's health, machine-readable and sayable."""

    connection_id: str
    connector: str
    state: HealthState = HealthState.DISCONNECTED
    #: One sentence, in the user's terms, naming what to do about it.
    says: str = ""
    last_success: str = ""
    last_attempt: str = ""
    items: int = 0
    #: What the next pass will be driven by — a timer, an event, or nothing.
    next_sync: str = ""
    errors: list[str] = field(default_factory=list)
    #: The manifest's own account label, so a row can name which account.
    account: str = ""

    @property
    def ok(self) -> bool:
        return self.state in (HealthState.HEALTHY, HealthState.SYNCING)

    @property
    def needs_the_user(self) -> bool:
        return self.state is HealthState.AUTH_REQUIRED

    def as_dict(self) -> dict[str, Any]:
        return {"connection_id": self.connection_id,
                "connector": self.connector, "account": self.account,
                "state": self.state.value, "says": self.says, "ok": self.ok,
                "needs_the_user": self.needs_the_user,
                "last_success": self.last_success,
                "last_attempt": self.last_attempt, "items": self.items,
                "next_sync": self.next_sync, "errors": list(self.errors)}


def _age_minutes(stamp: str, *, now: datetime | None = None) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, ((now or datetime.now(UTC)) - when).total_seconds() / 60.0)


def _said(state: HealthState, *, label: str, age: float | None,
          detail: str) -> str:
    """One sentence naming the thing to do. A status word alone is a diagnosis
    with no treatment, which is the failure `connector_state.status` was."""
    if state is HealthState.HEALTHY:
        return "Up to date."
    if state is HealthState.SYNCING:
        return "Syncing now."
    if state is HealthState.DISCONNECTED:
        return "Not connected."
    if state is HealthState.AUTH_REQUIRED:
        return f"Sign in to {label} again to keep this up to date."
    if state is HealthState.RATE_LIMITED:
        return f"{label} is asking us to slow down. This will pick up on its own."
    if state is HealthState.DEGRADED:
        if age is not None:
            hours = int(age // 60)
            when = f"{hours} hour{'s' if hours != 1 else ''}" if hours else \
                   f"{int(age)} minutes"
            return f"Last updated {when} ago. Trying again on the next pass."
        return "Has not finished a sync yet."
    return detail or f"{label} could not be reached. Try syncing again."


def of(connection: Connection, manifest: ConnectorManifest,
       states: list[SyncState], *, interval_minutes: int = 30,
       rate_limited: bool = False, now: datetime | None = None) -> Health:
    """This connection's health, from state that already exists. **No I/O.**

    Everything read here was written by work that already happened, which is
    what makes it safe to call once per row on a page load — the thing
    `is_configured()` is not.
    """
    label = manifest.display_name or connection.connector
    latest_success = max((s.last_success for s in states), default="")
    latest_attempt = max((s.last_attempt for s in states), default="")
    items = sum(s.items_processed for s in states)
    errors = [s.error for s in states if s.error]

    state = _state_of(connection, states, errors=errors,
                      rate_limited=rate_limited,
                      last_success=latest_success,
                      scheduled=manifest.sync.scheduled,
                      interval_minutes=interval_minutes, now=now)

    return Health(
        connection_id=connection.id, connector=connection.connector,
        account=connection.display, state=state,
        says=_said(state, label=label,
                   age=_age_minutes(latest_success, now=now),
                   detail=connection.auth_detail or (errors[0] if errors else "")),
        last_success=latest_success, last_attempt=latest_attempt, items=items,
        next_sync=_next_sync(manifest, connection),
        errors=errors[:3])


def _state_of(connection: Connection, states: list[SyncState], *,
              errors: list[str], rate_limited: bool, last_success: str,
              scheduled: bool, interval_minutes: int,
              now: datetime | None) -> HealthState:
    """The state, decided in the order the answers matter.

    Auth first, because nothing below it can be true while the credential is
    dead — a rate-limit badge on a connector that is actually signed out sends
    the user to wait for something that will never happen.
    """
    if connection.needs_the_user:
        return HealthState.AUTH_REQUIRED
    if connection.auth_state is AuthState.DISCONNECTED or connection.paused:
        return HealthState.DISCONNECTED
    if connection.auth_state is AuthState.ERROR:
        return HealthState.ERROR
    if any(s.in_flight for s in states):
        return HealthState.SYNCING
    if rate_limited:
        return HealthState.RATE_LIMITED
    if errors:
        return HealthState.ERROR
    if not last_success:
        # Never finished one. Degraded rather than error: a connector that was
        # connected a minute ago and has not run yet is not broken.
        return HealthState.DEGRADED
    if scheduled:
        age = _age_minutes(last_success, now=now)
        if age is not None and age > max(1, interval_minutes) * STALE_AFTER_INTERVALS:
            return HealthState.DEGRADED
    return HealthState.HEALTHY


def _next_sync(manifest: ConnectorManifest, connection: Connection) -> str:
    """What will drive the next pass, in the user's terms.

    Says "when you ask" for a manual source rather than leaving it blank: a
    blank next-sync on a row that is working reads as a connector that has
    stopped, which is the opposite of the truth.
    """
    if connection.paused:
        return "paused"
    if not connection.runnable:
        return "after you sign in again"
    from .contract import EventSource
    if manifest.events.source is EventSource.WEBHOOK:
        return "when something changes"
    if manifest.sync.scheduled:
        return "on the sync timer"
    return "when you ask"


@dataclass(frozen=True)
class Probe:
    """The four-part answer a live check gives. Never a sync."""

    reachable: bool
    credentials_valid: bool
    #: Required scopes the connection does not have. Empty when all are held —
    #: **and also empty when the vendor did not tell us**, which is why
    #: `Connection.missing_scopes` treats unknown as unknown rather than as
    #: none. A warning on a working connector is a control that cannot work.
    missing_scopes: list[str] = field(default_factory=list)
    configuration_valid: bool = True
    detail: str = ""

    @property
    def ok(self) -> bool:
        return (self.reachable and self.credentials_valid
                and self.configuration_valid and not self.missing_scopes)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reachable": self.reachable,
                "credentials_valid": self.credentials_valid,
                "missing_scopes": list(self.missing_scopes),
                "configuration_valid": self.configuration_valid,
                "detail": self.detail}


def probe(connector: Any, connection: Connection,
          manifest: ConnectorManifest) -> Probe:
    """Ask the connector whether it is actually working, cheaply.

    Prefers the connector's own `check_health()` where it has one
    (`SupportsHealthCheck`), because only the connector knows which of its
    calls is the cheap one. Falls back to `is_configured()`, which every
    connector has — and which for MCP costs a process start, so this is not
    called on a page load without the user asking.
    """
    from .errors import ConnectorError, scrub

    missing = connection.missing_scopes(manifest.required_scopes)
    checker = getattr(connector, "check_health", None)
    try:
        if callable(checker):
            found = checker()
            if isinstance(found, Probe):
                return found
            ready, reason = bool(found), ""
        else:
            ready, reason = connector.is_configured()
    except ConnectorError as exc:
        return Probe(reachable=not exc.needs_reauth,
                     credentials_valid=not exc.needs_reauth,
                     missing_scopes=missing, detail=exc.message)
    except Exception as exc:
        # A connector that throws from its own readiness check is a connector
        # that is not working, which is the answer we were asking for. It must
        # never be the answer to whether the *app* is working.
        log.debug("%s: health probe raised", connection.connector, exc_info=True)
        return Probe(reachable=False, credentials_valid=False,
                     missing_scopes=missing, detail=scrub(str(exc))[:200])

    return Probe(reachable=ready, credentials_valid=ready,
                 missing_scopes=missing, configuration_valid=ready,
                 detail="" if ready else reason)
