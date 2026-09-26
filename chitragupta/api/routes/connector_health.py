"""Connector health, what each one can do, and the controls over the data.

Split from `connectors.py` rather than added to it, because the two answer
different questions at different costs. `GET /api/connectors` reaches out — it
calls `is_configured()` on every row, which starts a subprocess for every MCP
server, and that is why it sits in the probe lane. Everything here reads state
that already exists, so it is cheap enough for a page to poll.

The endpoints are the ones the product section of `/CLAUDE.md` demands and the
Connectors screen could not previously offer:

* **What data was imported, from which app, from which account, when?** —
  `/api/connectors/health` and `/api/connectors/{name}/data`.
* **What can this reach, and what can it change?** — `/api/connectors/manifest`,
  in the capability vocabulary rather than as a count of tools.
* **Can I stop it, and is stopping it the same as forgetting it?** — pause,
  resume, disconnect and delete-data are four separate routes, because they are
  four separate decisions and conflating any two makes one of them a button
  nobody dares press.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...config import get_settings
from ...connectors import REGISTRY, get_connector, observability, resources, sync_state
from ...connectors import connections as conns
from ...connectors import health as health_mod
from ...log import get_logger, suppressed

log = get_logger(__name__)
router = APIRouter()


def _connector(name: str):
    try:
        return get_connector(name)
    except KeyError as exc:
        raise HTTPException(404, "That connector is not one we know.") from exc


def _health_of(connector: Any, connection: Any) -> dict[str, Any]:
    from ...connectors import limits

    manifest = connector.manifest()
    gate = limits.gate_for(connector.name, manifest.limits)
    found = health_mod.of(
        connection, manifest,
        sync_state.for_connection(connection.id),
        interval_minutes=max(1, get_settings().sync_interval_minutes),
        rate_limited=gate.snapshot()["waiting_seconds"] > 0)
    return found.as_dict()


@router.get("/api/connectors/health")
def connector_health() -> dict[str, Any]:
    """Every connection's health, from state we already hold. **No I/O.**

    Safe to poll: the expensive version of this question is
    `GET /api/connectors`, which actually reaches each source and is in the
    probe lane for that reason.
    """
    rows: list[dict[str, Any]] = []
    for connection in conns.all_connections():
        cls = REGISTRY.get(connection.connector)
        try:
            connector = cls() if cls is not None \
                else get_connector(connection.connector)
        except Exception:
            # A connection whose connector has been removed — an MCP server the
            # user deleted, a custom app they forgot. It is still a row with
            # data behind it, and dropping it from this list would hide the
            # delete-data control that is the only way to clean it up.
            with suppressed("describing a connector that is no longer present"):
                rows.append({"connection_id": connection.id,
                             "connector": connection.connector,
                             "account": connection.display,
                             "state": "disconnected",
                             "says": "This source is no longer set up.",
                             "ok": False, "needs_the_user": False,
                             "items": 0, "errors": []})
            continue
        with suppressed("reading one connector's health"):
            rows.append(_health_of(connector, connection))
    return {"connectors": rows, "queue_depth": _queue_depth()}


def _queue_depth() -> int:
    from ...connectors import events

    with suppressed("reading the connector event queue depth"):
        return events.depth()
    return 0


@router.get("/api/connectors/manifest")
def connector_manifests() -> dict[str, Any]:
    """What each connector can do, in the capability vocabulary.

    *"Reads your email, can send as you"* rather than *"has 45 tools"*. A count
    is not something anybody can consent to — the argument `mcp_manifest.py`
    makes for MCP servers, applied to every connector.
    """
    out: list[dict[str, Any]] = []
    for _name, cls in sorted(REGISTRY.items()):
        if not cls.supported_here():
            continue
        with suppressed("describing one connector"):
            out.append(cls().manifest().as_dict())
    return {"connectors": out}


@router.get("/api/connectors/{name}/manifest")
def connector_manifest(name: str) -> dict[str, Any]:
    return _connector(name).manifest().as_dict()


@router.get("/api/connectors/{name}/data")
def connector_data(name: str) -> dict[str, Any]:
    """What this connector imported, from which account, and what became of it.

    The answer to *"what data was imported?"* — which had no answer before,
    because nothing tied a memory to the record it came from. Counts rather
    than contents: the contents are in the brain, and this is the control
    panel over them.
    """
    connector = _connector(name)
    rows: list[dict[str, Any]] = []
    for connection in conns.for_connector(connector.name):
        rows.append({
            "connection": connection.as_dict(),
            "counts": resources.counts(connection.id),
            "sync": [s.as_dict() for s in sync_state.for_connection(connection.id)],
        })
    return {"connector": connector.name, "label": connector.label,
            "accounts": rows}


class ConnectionAction(BaseModel):
    connection_id: str = ""


def _connection_for(name: str, connection_id: str) -> Any:
    """The named connection, or this connector's only one.

    The id is optional because every shipped connector has exactly one account
    and a UI should not have to know that. It stops being optional the moment
    a connector has two, and that is a refusal rather than a guess — picking
    one for the user is how a Disconnect button signs out the wrong account.
    """
    if connection_id:
        found = conns.get(connection_id)
        if found is None or found.connector != name:
            raise HTTPException(404, "That account is not connected.")
        return found
    accounts = conns.for_connector(name)
    if not accounts:
        raise HTTPException(404, "That source is not set up yet.")
    if len(accounts) > 1:
        raise HTTPException(
            400, "There is more than one account here — say which one.")
    return accounts[0]


@router.post("/api/connectors/{name}/pause")
def connector_pause(name: str, body: ConnectionAction) -> dict[str, Any]:
    """Stop the scheduled work, keep the credential and the data.

    Distinct from disconnecting on purpose: this is *"stop reading for now"*,
    and it is the control a user reaches for when a sync is noisy rather than
    when they want their account back.
    """
    found = _connection_for(name, body.connection_id)
    moved = conns.pause(found.id)
    return {"ok": True, "connection": moved.as_dict() if moved else None}


@router.post("/api/connectors/{name}/resume")
def connector_resume(name: str, body: ConnectionAction) -> dict[str, Any]:
    found = _connection_for(name, body.connection_id)
    moved = conns.pause(found.id, paused=False)
    return {"ok": True, "connection": moved.as_dict() if moved else None}


@router.post("/api/connectors/{name}/resync")
def connector_resync(name: str, body: ConnectionAction) -> dict[str, Any]:
    """Forget where we got to, so the next pass re-reads the window.

    **Does not delete anything.** Dedup absorbs the re-read, and deleting the
    user's data in order to refresh it would be a far larger promise than this
    button makes.
    """
    found = _connection_for(name, body.connection_id)
    sync_state.clear(found.id)
    return {"ok": True, "detail": "The next sync will read everything again."}


@router.delete("/api/connectors/{name}/data")
def connector_forget_data(name: str, connection_id: str = "") -> dict[str, Any]:
    """Delete what this connector imported, and keep the connection.

    The other half of the pair `/CLAUDE.md` insists on: disconnecting gives the
    credential back and keeps the data; this deletes the data and keeps the
    credential. Two controls because they are two decisions, and a single
    button that did both would be one nobody could predict.

    Only the memories this connector's records produced — which is answerable
    at all because `connector_resources` ties each memory to the record it came
    from. Before that table the only available implementation was *"delete
    everything whose source string looks right"*.
    """
    found = _connection_for(name, connection_id)
    ids = resources.memory_ids(found.id)
    removed = 0
    if ids:
        from ...brain import get_brain

        store = get_brain().store
        for memory_id in ids:
            with suppressed("deleting one memory a connector imported"):
                if store.delete(memory_id):
                    removed += 1
    sync_state.clear(found.id)
    return {"ok": True, "removed": removed,
            "detail": f"Removed {removed} item{'' if removed == 1 else 's'}. "
                      f"{found.display} is still connected."}


@router.get("/api/connectors/diagnostics")
def connector_diagnostics() -> dict[str, Any]:
    """Every number the connector layer keeps, in one call.

    One call rather than four, because a panel that makes four requests to
    render itself is the N+1 shape one level up.
    """
    return observability.snapshot()
