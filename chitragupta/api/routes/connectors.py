"""Connected sources, the custom-app builder, and Google's own sign-in."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...brain import get_brain
from ...config import get_settings
from ...connectors import REGISTRY, get_connector
from ...log import get_logger, suppressed
from ..concurrency import probes_a_provider
from ..schemas import SecretIn

log = get_logger(__name__)
router = APIRouter()


class SyncIn(BaseModel):
    params: dict[str, Any] = {}


@router.get("/api/connectors")
@probes_a_provider
def connectors():
    brain = get_brain()
    state = brain.store.all_connector_state()
    out = []
    # Servers the user has added, so a built-in can stand down for the one
    # that supersedes it.
    from ...connectors.mcp_source import list_servers as _mcp_servers

    added = {str(getattr(s, "id", "")).lower() for s in _mcp_servers()}

    for name, cls in REGISTRY.items():
        if not cls.supported_here():
            continue                      # hide macOS-only connectors off macOS
        inst = cls()
        ready, reason = inst.is_configured()
        # **A source is offered one way, never two.**
        #
        # Two rules, and the difference between them is whether somebody has
        # already chosen. Both exist because the screen listed Notion twice —
        # built-in with a *Connect* button, custom source with a green
        # CONNECTED badge — and an agent proposed a write down the route the
        # user had not set up.
        #
        # RETIRED: the vendor ships a server that does this better, so the
        # built-in is not offered at all. See `Connector.prefer_mcp`.
        if cls.prefer_mcp and not ready:
            continue
        # DEDUPED: whatever we think is better, the user has added a server
        # for this source. Showing an unconfigured built-in beside it is
        # offering a second way to connect a thing already connected.
        #
        # Only when the built-in is NOT set up: one that is working is one
        # they can see, and hiding it would take away state they still own —
        # including, for Gmail and Calendar, the connector every mail and
        # diary action in this app is built on.
        if not ready and name.lower() in added:
            continue
        # `fix` is additive and usually None. It names a refusal the user can
        # clear themselves — read straight after `is_configured()`, which is
        # when the connector sets it — so the card can offer the button instead
        # of only describing the problem.
        out.append({"name": name, "label": cls.label, "ready": ready,
                    "reason": reason, "fix": inst.fix,
                    "always_available": cls.always_available,
                    "secret_field": cls.secret_field, "custom": False,
                    "state": state.get(name)})
    # user-defined custom API apps
    from ...connectors.custom_api import CustomAPIConnector, list_apps
    for app in list_apps():
        inst = CustomAPIConnector(app)
        ready, reason = inst.is_configured()
        out.append({"name": inst.name, "label": inst.label, "ready": ready,
                    "reason": reason, "always_available": False,
                    "secret_field": None, "custom": True, "config": app,
                    "state": state.get(inst.name)})
    # MCP-backed connectors, one per server the user added. `is_configured()`
    # starts the server, which is the only honest test of "can this run" — but
    # it is also why this endpoint is in the probe lane.
    from ...connectors.mcp_source import MCPConnector, list_servers
    for spec in list_servers():
        inst = MCPConnector(spec)
        ready, reason, can_sync = inst.status()
        out.append({"name": inst.name, "label": inst.label, "ready": ready,
                    "reason": reason, "always_available": False,
                    "secret_field": None, "custom": False, "mcp": True,
                    # A connector that answers questions but cannot list its
                    # records is working. The row has to say so without
                    # offering a Sync button that could only ever fail.
                    "can_sync": can_sync,
                    "config": spec.as_dict(), "state": state.get(inst.name)})
    return {"connectors": out}


# ── the connector catalog (MCP-backed sources) ────────────────────────────


@router.get("/api/connectors/catalog")
def connector_catalog() -> dict[str, Any]:
    """What can be added, and what cannot — with the reason either way.

    Blocked sources are returned rather than omitted: a grid that silently
    lacks LinkedIn teaches the user the app is missing a feature, when the
    truth is that no app can offer it. "Never show a control that cannot work"
    means saying so where the control would have been.
    """
    from ...connectors.mcp_catalog import BLOCKED, CATALOG, CATEGORIES, _needed
    from ...connectors.mcp_source import list_servers

    added = {spec.id for spec in list_servers()}
    return {
        "available": [{"id": e.id, "name": e.name, "notes": e.notes,
                       "first_party": e.first_party, "added": e.id in added,
                       "remote": e.is_remote, "auth": e.auth,
                       "category": e.category,
                       "needs_env": _needed(e.needs_env),
                       "needs_args": _needed(e.needs_args)}
                      for e in CATALOG],
        "blocked": [{"id": b.id, "name": b.name, "reason": b.reason}
                    for b in BLOCKED],
        # The shelf order, sent rather than hardcoded in the page: the
        # catalog decides what belongs where, and two lists that can
        # disagree eventually will.
        "categories": list(CATEGORIES),
    }


class CatalogAddIn(BaseModel):
    """What the user answered on the connector's setup form.

    `env` holds credentials (they go straight to the Keychain and are never
    echoed back); `args` holds plain settings a local server needs positionally,
    such as the folder it may read.
    """

    env: dict[str, str] = {}
    args: dict[str, str] = {}


class MCPServerIn(BaseModel):
    """A server the user is adding by hand.

    The catalog covers the vetted sources; this is the escape hatch for a
    server we have not listed, which is most of them. Verified exactly the same
    way — probed before it is saved — because "the user typed it" is not
    evidence that it runs.
    """

    id: str
    name: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    url: str = ""
    auth: str = "oauth"
    env: dict[str, str] = {}


class MCPPermissionsIn(BaseModel):
    """Which of a server's tools this connector may use at all.

    An empty list means "everything it exposes", which is the default and what
    every existing connector has. Narrowing is the point: least privilege that
    the user cannot actually set is a claim, not a control.
    """

    allowed_tools: list[str] = []


@router.post("/api/connectors/catalog/{entry_id}")
@probes_a_provider
def connector_catalog_add(entry_id: str, body: CatalogAddIn) -> dict[str, Any]:
    """Add a catalog connector, after verifying it actually runs.

    Returns `ok: False` with a reason rather than raising, so the form can say
    what happened in place instead of showing a failed request.
    """
    from ...connectors.mcp_catalog import add_from_catalog

    spec, reason = add_from_catalog(entry_id, body.env, body.args)
    if spec is None:
        return {"ok": False, "error": reason}
    # A connector behind the vendor's sign-in is saved before it works, so the
    # form has to know whether to wait on a browser or go straight to syncing.
    return {"ok": True, "name": f"mcp:{spec.id}", "label": spec.name,
            "server_id": spec.id, "signing_in": spec.uses_oauth}


@router.get("/api/connectors/catalog/{entry_id}/permissions")
@probes_a_provider
def connector_permissions(entry_id: str) -> dict[str, Any]:
    """What this connector would be able to read and change, before enabling.

    Consent to something nobody has been shown is not consent.
    """
    from ...connectors.mcp_catalog import describe

    return describe(entry_id)


@router.delete("/api/connectors/mcp/{server_id}")
def connector_mcp_delete(server_id: str) -> dict[str, Any]:
    from ...connectors.mcp_source import delete_server

    return {"ok": delete_server(server_id)}


@router.post("/api/connectors/mcp")
@probes_a_provider
def connector_mcp_add(body: MCPServerIn) -> dict[str, Any]:
    """Add a server the catalog does not list, verified before it is saved."""
    from ...connectors.mcp_source import (
        MCPServerSpec,
        get_server,
        probe,
        set_server_env,
        upsert_server,
    )

    server_id = (body.id or "").strip().lower().replace(" ", "-")
    if not server_id:
        return {"ok": False, "error": "Give this connector a short name."}
    if get_server(server_id) is not None:
        return {"ok": False,
                "error": f"A connector called “{server_id}” already exists."}
    if body.transport == "http":
        if not body.url.lower().startswith("https://"):
            return {"ok": False,
                    "error": "A remote connector needs an https:// address."}
    elif not body.command.strip():
        return {"ok": False, "error": "Say which program runs this connector."}

    spec = MCPServerSpec(
        id=server_id, name=(body.name or server_id).strip(),
        transport=body.transport, command=body.command.strip(),
        args=[a for a in body.args if a], url=body.url.strip(),
        auth=body.auth, token_key=(sorted(body.env)[0] if body.env else ""),
        env_keys=sorted(body.env))

    if spec.uses_oauth:
        upsert_server(spec)
        set_server_env(spec.id, body.env)
        from ...connectors.mcp_auth import begin

        begin(spec)
        return {"ok": True, "name": f"mcp:{spec.id}", "label": spec.name,
                "server_id": spec.id, "signing_in": True}

    from ...connectors.mcp_catalog import _clear_env, _stage_env

    _stage_env(spec, body.env)
    kinds, reason = probe(spec)
    if kinds is None:
        _clear_env(spec, body.env)
        return {"ok": False, "error": reason}
    upsert_server(spec)
    return {"ok": True, "name": f"mcp:{spec.id}", "label": spec.name,
            "server_id": spec.id, "signing_in": False}


@router.get("/api/connectors/mcp/{server_id}/tools")
@probes_a_provider
def connector_mcp_tools(server_id: str) -> dict[str, Any]:
    """Everything this connector exposes, and which of them it may use.

    Asks the server rather than the cache, because this is the screen where the
    user decides what to switch off — a stale list here would show them a
    choice about a tool that no longer exists and hide one that does.
    """
    from ...connectors.mcp_source import get_server, probe

    spec = get_server(server_id)
    if spec is None:
        raise HTTPException(404, "That connector is not set up.")
    kinds, reason = probe(spec)
    if kinds is None:
        return {"ok": False, "error": reason, "tools": [],
                "allowed_tools": spec.allowed_tools}
    writes = set(kinds.write)
    rows = []
    for tool in kinds.tools:
        name = getattr(tool, "name", "")
        if not name:
            continue
        rows.append({
            "name": name,
            "writes": name in writes,
            "description": (getattr(tool, "description", "") or "").strip()[:150],
        })
    return {"ok": True, "tools": rows, "allowed_tools": spec.allowed_tools}


@router.patch("/api/connectors/mcp/{server_id}")
def connector_mcp_permissions(server_id: str, body: MCPPermissionsIn
                              ) -> dict[str, Any]:
    """Narrow what this connector may use.

    `invalidate()` on save, so a tool the user just switched off stops being
    offered to the model on the next turn rather than at the end of a five
    minute cache — the same lie as a Connected badge with no credential.
    """
    from ...connectors.mcp_source import get_server, upsert_server

    spec = get_server(server_id)
    if spec is None:
        raise HTTPException(404, "That connector is not set up.")
    spec.allowed_tools = [t for t in body.allowed_tools if t]
    upsert_server(spec)
    return {"ok": True, "allowed_tools": spec.allowed_tools}


@router.post("/api/connectors/mcp/{server_id}/auth")
def connector_mcp_auth_start(server_id: str) -> dict[str, Any]:
    """Open the vendor's own sign-in for a remote connector.

    Returns immediately rather than waiting: the browser trip is the user's,
    and a handler that blocked on it would hold a worker thread for as long as
    they took to find their password.
    """
    from ...connectors.mcp_auth import begin
    from ...connectors.mcp_source import get_server

    spec = get_server(server_id)
    if spec is None:
        raise HTTPException(404, "That connector is not set up.")
    if not spec.uses_oauth:
        return {"started": False,
                "detail": f"{spec.name} does not sign in this way."}
    return begin(spec)


@router.get("/api/connectors/mcp/{server_id}/auth")
def connector_mcp_auth_status(server_id: str) -> dict[str, Any]:
    """How a sign-in is going. Survives a refresh; never carries a token."""
    from ...connectors.mcp_auth import status

    return status(server_id)


@router.delete("/api/connectors/mcp/{server_id}/auth")
def connector_mcp_auth_cancel(server_id: str) -> dict[str, Any]:
    """Stop a sign-in. Anything the user starts, they can stop."""
    from ...connectors.mcp_auth import cancel

    return {"cancelled": cancel(server_id)}


# ── custom apps (connect any REST app, no code) ───────────────────────────
class CustomAppIn(BaseModel):
    id: str | None = None
    name: str = "Custom app"
    base_url: str = ""
    endpoint: str = ""
    auth_type: str = "none"      # none | bearer | header | query
    auth_name: str = ""
    token: str | None = None
    items_path: str = ""
    title_field: str = ""
    body_field: str = ""


@router.get("/api/custom-apps")
def custom_apps():
    from ...connectors.custom_api import list_apps
    return {"apps": list_apps()}


@router.post("/api/custom-apps")
def save_custom_app(body: CustomAppIn):
    from ...connectors.custom_api import upsert_app
    url = (body.base_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "base URL must start with http:// or https://")
    cfg = body.model_dump()
    token = cfg.pop("token", None)
    app = upsert_app(cfg, token=token)
    return {"saved": True, "app": app, "name": f"custom:{app['id']}"}


@router.delete("/api/custom-apps/{app_id}")
def delete_custom_app(app_id: str):
    from ...connectors.custom_api import delete_app
    return {"deleted": delete_app(app_id)}


@router.post("/api/connectors/{name}/secret")
def save_connector_secret(name: str, body: SecretIn):
    """Save (or clear) a connector's single-token secret from the UI —
    no .env editing. Written to ~/Library/Chitragupta/secrets.json (chmod 600)."""
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise HTTPException(404, f"unknown connector '{name}'") from None
    field = cls.secret_field
    if not field:
        raise HTTPException(400, f"'{name}' does not use a token secret")
    get_settings().set_secret(field["key"], body.value)
    ready, reason = cls().is_configured()
    return {"saved": True, "ready": ready, "reason": reason}


@router.post("/api/connectors/{name}/sync")
@probes_a_provider
def sync(name: str, body: SyncIn):
    try:
        conn = get_connector(name)
    except KeyError:
        raise HTTPException(404, f"unknown connector '{name}'") from None
    # route ingestion through the brain so the graph is built too
    res = conn.sync(**body.params)
    return res.as_dict()


# ── Google sign-in (bundled client → no per-user Cloud setup) ─────────────
class MCPActionIn(BaseModel):
    """A connector write, either confirmed on screen or waiting for approval.

    `confirmed` is carried explicitly rather than implied by the request: a
    connector action changes something in the user's account, and "they called
    the endpoint" is not the same as "they were shown what it would do".

    Unconfirmed no longer means *refused*. It means **queued** — held in the
    same list as every other action waiting on the user, with the same
    notification. Refusing was the weaker half of two approval systems: it had
    nowhere to put a request made while nobody was watching, so a routine that
    wanted to act simply failed and forgot.
    """

    tool: str
    arguments: dict[str, Any] = {}
    confirmed: bool = False
    agent_id: str = ""


def _mcp(server_id: str):
    """The MCP connector for this id, or a 404.

    Returns the concrete type rather than the `Connector` base: `perform()` is
    the one path in the whole connector layer that changes something at a
    vendor, and a route that reached it through a duck-typed base could be
    pointed at anything that happened to grow the method.
    """
    from ...connectors.mcp_source import MCPConnector

    try:
        conn = get_connector(f"mcp:{server_id}")
    except KeyError:
        raise HTTPException(404, "That connector is not set up.") from None
    if not isinstance(conn, MCPConnector):       # pragma: no cover - unreachable
        raise HTTPException(404, "That connector is not set up.")
    return conn


@router.get("/api/connectors/mcp/{server_id}/actions")
@probes_a_provider
def mcp_actions(server_id: str) -> dict[str, Any]:
    """What this connector could change, so a card can ask before it does."""
    return {"actions": _mcp(server_id).available_actions()}


@router.post("/api/connectors/mcp/{server_id}/action")
@probes_a_provider
def mcp_action(server_id: str, body: MCPActionIn) -> dict[str, Any]:
    """Run a confirmed action, or queue it for approval.

    Both paths go through `actions.REGISTRY["mcp_action"]`, so an approval
    granted later runs exactly what a confirmation now would.
    """
    from ...actions import run_now
    from ...agents.approvals import run_or_queue

    conn = _mcp(server_id)
    params = {"server_id": server_id, "connector": conn.label,
              "tool": body.tool, "arguments": body.arguments}
    if body.confirmed:
        # The user is looking at it and clicked. That is a stronger signal than
        # any stored list, and is why interactive chat does not queue either.
        return run_now("mcp_action", params)
    return run_or_queue("mcp_action", params, agent_id=body.agent_id)


@router.get("/api/google/status")
@probes_a_provider
def google_status():
    from ...connectors.google_auth import _token_path, connected_email, granted_services
    connected = _token_path().exists()
    account = connected_email(fetch=connected) if connected else None
    return {"client_configured": get_settings().google_client_secrets is not None,
            "connected": connected,
            "account": account,
            "services": granted_services()}


# ── Telegram: credentials, then a three-step sign-in ─────────────────────
#
# Not a `secret_field`, because this is not one box. It is an API id and hash
# the user fetches once from my.telegram.org, then a phone number, then the
# code Telegram sends, then a password if the account has two-factor on.
#
# Every step returns `{ok, error}` in the same shape, so the card driving it
# never has to tell them apart. `needs_password: true` is the only branch, and
# it is a step rather than a failure.
#
# Contract: docs/development/telegram.md
class TelegramCredentialsIn(BaseModel):
    api_id: str
    api_hash: str


class TelegramPhoneIn(BaseModel):
    phone: str


class TelegramCodeIn(BaseModel):
    code: str


class TelegramPasswordIn(BaseModel):
    password: str


@router.get("/api/telegram/status")
@probes_a_provider
def telegram_status():
    """Where the user is in setting Telegram up."""
    from ...connectors.telegram_auth import status

    return status()


@router.post("/api/telegram/credentials")
def telegram_credentials(body: TelegramCredentialsIn):
    from ...connectors.telegram_auth import save_credentials

    return save_credentials(body.api_id, body.api_hash)


@router.post("/api/telegram/login")
@probes_a_provider
def telegram_login(body: TelegramPhoneIn):
    """Ask Telegram to send a login code to the user's number."""
    from ...connectors.telegram_auth import start_login

    return start_login(body.phone)


@router.post("/api/telegram/code")
@probes_a_provider
def telegram_code(body: TelegramCodeIn):
    """Finish signing in with the code Telegram sent."""
    from ...connectors.telegram_auth import submit_code

    return submit_code(body.code)


@router.post("/api/telegram/password")
@probes_a_provider
def telegram_password(body: TelegramPasswordIn):
    """The two-factor password, for accounts that have one."""
    from ...connectors.telegram_auth import submit_password

    return submit_password(body.password)


@router.post("/api/telegram/disconnect")
@probes_a_provider
def telegram_disconnect():
    """Sign out on Telegram's side as well as ours."""
    from ...connectors.telegram_auth import disconnect

    return disconnect()


@router.post("/api/google/disconnect")
def google_disconnect():
    from ...connectors.google_auth import disconnect
    disconnect()
    return {"disconnected": True}


# ── Google reconnect (re-consent with current scopes, from the UI) ────────
@router.post("/api/google/reconnect")
@probes_a_provider
def google_reconnect():
    import threading

    from ...connectors.google_auth import _token_path, connected_email, get_credentials
    tok = _token_path()
    if tok.exists():
        tok.unlink()

    def _run():
        with suppressed("get_credentials(interactive=True) …"):
            get_credentials(interactive=True)
            connected_email(fetch=True)

    # opens the Google consent browser on this machine; runs in the background
    threading.Thread(target=_run, daemon=True).start()
    return {"started": True,
            "detail": "A browser window is opening — approve the permissions."}
