"""The agents themselves: listing them, talking to them, and binding a model to one."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...agents import cancellation, list_agents, run_turn
from ...agents.agent import AgentMemory
from ...config import get_settings
from ...log import get_logger
from ..concurrency import calls_a_model, probes_a_provider
from ..schemas import ChatIn

log = get_logger(__name__)
router = APIRouter()


@router.get("/api/agents")
def agents():
    from ...agents.grants import reaches_connectors
    from ...agents.presets import PRESETS
    mem = AgentMemory()
    return {"agents": [
        {"id": a.id, "name": a.name, "role": a.role, "tools": a.tools,
         "custom": a.id not in PRESETS,
         # Whether this agent can reach the user's connectors. Derived here so
         # the rule — the sentinel, or a connector tool by name — exists once
         # rather than being re-inferred by every caller from `tools`.
         "reaches_connectors": reaches_connectors(a),
         "model_provider": a.model_provider,
         "model_name": a.model_name,
         "messages": len(mem.history(a.id, limit=1000))}
        for a in list_agents()
    ]}


# ── how hard the agents try ──────────────────────────────────────────────────
class EffortIn(BaseModel):
    level: str


@router.get("/api/agents/effort")
def get_agent_effort():
    """The available levels and which one is in force."""
    from ...agents.effort import describe_levels, saved_effort

    return {"current": saved_effort(), "levels": describe_levels()}


@router.post("/api/agents/effort")
def set_agent_effort(body: EffortIn):
    from ...agents.effort import set_saved_effort

    try:
        return {"current": set_saved_effort(body.level)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


# ── who an unattended agent may act on ───────────────────────────────────────
class PermissionIn(BaseModel):
    value: str
    note: str = ""
    #: Which allow-list this goes on. Defaults to email so an older caller
    #: keeps working; the approval card now sends the one the server named.
    kind: str = ""


@router.get("/api/agents/permissions")
def list_action_permissions():
    """Recipients the user has allowed unattended agents to reach.

    **Every list, not just the email one.** They were filtered to
    `email_recipient` here, so a chat grant was stored, honoured by the gate,
    and invisible on the screen that exists to review and revoke them — a
    standing permission nobody can see is not one anybody agreed to keep.
    """
    from ...agents.permissions import all_permissions

    return {"permissions": all_permissions()}


@router.post("/api/agents/permissions")
def grant_action_permission(body: PermissionIn):
    from ...agents.permissions import EMAIL_RECIPIENT, grant

    try:
        return grant(body.value, kind=body.kind or EMAIL_RECIPIENT,
                     note=body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.delete("/api/agents/permissions/{value}")
def revoke_action_permission(value: str, kind: str = ""):
    from ...agents.permissions import EMAIL_RECIPIENT, revoke

    return {"revoked": revoke(value, kind=kind or EMAIL_RECIPIENT)}


# ── actions an unattended agent wanted to take ───────────────────────────────
@router.get("/api/agents/approvals")
def list_approvals(include_decided: bool = False):
    from ...agents import approvals

    return {"approvals": approvals.history() if include_decided else approvals.pending()}


@router.post("/api/agents/approvals/{approval_id}/approve")
def approve_action(approval_id: str):
    from ...agents import approvals

    return approvals.approve(approval_id)


@router.post("/api/agents/approvals/{approval_id}/reject")
def reject_action(approval_id: str):
    from ...agents import approvals

    return approvals.reject(approval_id)


@router.get("/api/agents/evaluate")
def evaluate_agents():
    """Score what the agents can do, right now, on this machine.

    Runs the real loop against a scripted model — no network, no keys, no
    spend — so the answer is about this build rather than about whichever model
    happens to be connected.
    """
    from ...agents.evaluation import run

    return run().as_dict()


@router.get("/api/agents/{agent_id}/history")
def history(agent_id: str):
    return {"history": AgentMemory().history(agent_id, limit=100)}


class AgentModelIn(BaseModel):
    provider: str
    model: str | None = None


@router.get("/api/agents/{agent_id}/model")
def get_agent_model_endpoint(agent_id: str):
    from ...agents.agent_models import get_agent_model
    from ...agents.presets import get_agent
    try:
        get_agent(agent_id)          # existence check; raises KeyError below
    except KeyError:
        raise HTTPException(404, f"unknown agent '{agent_id}'") from None
    prov, model = get_agent_model(agent_id)
    s = get_settings()

    from ...models.entitlements import is_provider_connected
    if prov is not None:
        is_prov_conn, _, _ = is_provider_connected(prov)
        if not is_prov_conn:
            from ...agents.agent_models import clear_agent_model
            clear_agent_model(agent_id)
            prov, model = None, None

    is_override = prov is not None
    effective_provider = prov or s.model_provider or "cursor"
    if prov is not None:
        if model:
            effective_model = model
        else:
            from ...models.registry import MODEL_CATALOG
            cat: dict[str, Any] = MODEL_CATALOG.get(prov, {})
            effective_model = cat.get("default_model") or ""
    else:
        effective_model = model or s.model_name or ""
    is_conn, _, _ = is_provider_connected(effective_provider)
    return {
        "agent_id": agent_id,
        "provider": effective_provider,
        "model": effective_model,
        "configured_provider": prov,
        "configured_model": model,
        "is_override": is_override,
        "is_connected": is_conn,
    }


@router.post("/api/agents/{agent_id}/model")
@router.put("/api/agents/{agent_id}/model")
def set_agent_model_endpoint(agent_id: str, body: AgentModelIn):
    from ...agents.agent_models import set_agent_model
    from ...agents.presets import get_agent
    try:
        get_agent(agent_id)
    except KeyError:
        raise HTTPException(404, f"unknown agent '{agent_id}'") from None

    from ...models.entitlements import evaluate_model_entitlement, is_provider_connected
    is_conn, user_plan, _ = is_provider_connected(body.provider)
    if not is_conn:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{body.provider}' is not connected (locked). Please connect it in Models & Accounts first."
        )

    is_auto = not body.model or body.model.lower() == "auto"
    if is_auto:
        from ...models.discovery import get_discovered_models
        discovered, _ = get_discovered_models(body.provider, force_refresh=True)
        unlocked = [m for m in discovered if not m.get("locked")]
        if not unlocked:
            raise HTTPException(
                status_code=400,
                detail=f"All models for provider '{body.provider}' are locked on your current plan."
            )
    else:
        locked, plan_req = evaluate_model_entitlement(
            body.provider, body.model, is_connected=is_conn, user_plan=user_plan
        )
        if locked:
            req_msg = f"Requires {plan_req}." if plan_req else "Locked on current plan."
            raise HTTPException(
                status_code=400,
                detail=f"Model '{body.model}' is not supported on your {user_plan or 'current'} plan. {req_msg}"
            )

    from ...models.registry import clear_provider_cache
    result = set_agent_model(agent_id, body.provider, body.model)
    clear_provider_cache()  # invalidate cached provider instances so new model takes effect
    return result


@router.delete("/api/agents/{agent_id}/model")
def clear_agent_model_endpoint(agent_id: str):
    from ...agents.agent_models import clear_agent_model
    from ...agents.presets import get_agent
    try:
        get_agent(agent_id)
    except KeyError:
        raise HTTPException(404, f"unknown agent '{agent_id}'") from None
    cleared = clear_agent_model(agent_id)
    return {"cleared": cleared}


class NewAgent(BaseModel):
    name: str
    role: str = ""
    system_prompt: str = ""
    #: `None` means the caller expressed no opinion and wants the standard set;
    #: `[]` means they deliberately want none. `CustomAgentStore.create` is
    #: built on exactly that distinction, and declaring a `[]` default here
    #: erased it — the API could never send `None`, so an agent built from the
    #: builder without touching the tool list was created with no tools at all.
    #: See docs/development/agent-tool-grants.md §1 (decision D).
    tools: list[str] | None = None
    recall_sources: list[str] | None = None


@router.get("/api/agents/tools")
def available_tools():
    from ...agents.tools import TOOL_CATEGORIES, describe_tools
    # Each row keeps `name` and `description` exactly as before and adds
    # `source` ("builtin" | "mcp") and `connector`, so the agent builder can
    # group the user's own connectors instead of listing their tools as if they
    # shipped with the app. `label` is one word and `category` is its heading.
    #
    # `categories` is the ORDER they read in, not a list of what exists — the
    # rows already say that. It is here because the layer that decided "Your
    # Mac" comes last is this one, and a consumer sorting them itself would be
    # re-deciding it alphabetically.
    return {"tools": describe_tools(), "categories": list(TOOL_CATEGORIES)}


@router.get("/api/agents/connector-gaps")
@probes_a_provider
def connector_gaps():
    """Who cannot use the connectors the user has — the "I just connected
    something, who can't see it?" question.

    Reads. Never grants: connector access is a permission the user set, and an
    agent that already exists was configured by somebody who did not tick this
    box. See docs/development/agent-tool-grants.md §5.
    """
    from ...agents.grants import connector_gaps as gaps

    return gaps()


class ConnectorGrantIn(BaseModel):
    """Letting one agent reach one connector without asking again."""

    connector: str
    #: Only "always" is stored. "once" lives for a single turn and arrives with
    #: the message instead — storing it would turn "just this time" into
    #: something the user has to remember to undo.
    scope: str = "always"


@router.get("/api/agents/{agent_id}/connectors")
@probes_a_provider
def agent_connectors(agent_id: str):
    """What this agent may reach, and what it would have to ask for."""
    from ...agents.connector_grants import describe, first_party_labels
    from ...agents.mcp_tools import labels_by_id

    out = describe(agent_id)
    # Ids decide permission; labels are what a person reads. Both, so the
    # caller never has to guess one from the other.
    # Both kinds of connector, so the `@` picker offers everything the gate
    # can actually refuse. A connector that is enforced and not offerable is a
    # dead end the user has no way out of.
    out["labels"] = {**first_party_labels(), **labels_by_id()}
    return out


@router.post("/api/agents/{agent_id}/connectors")
def grant_agent_connector(agent_id: str, body: ConnectorGrantIn):
    from ...agents.connector_grants import allow_always

    if body.scope != "always":
        raise HTTPException(
            400, "Only an always-allow is stored. A one-time grant belongs on "
                 "the message it applies to.")
    try:
        return allow_always(agent_id, body.connector)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.delete("/api/agents/{agent_id}/connectors/{connector}")
def revoke_agent_connector(agent_id: str, connector: str):
    from ...agents.connector_grants import always_allowed, revoke

    return {"revoked": revoke(agent_id, connector),
            "allowed": always_allowed(agent_id)}


@router.get("/api/agents/{agent_id}/tools")
@probes_a_provider
def agent_tools(agent_id: str):
    """What THIS agent has, what it could have, and for anything it cannot, why.

    On the probing lane because a connector that is contributing nothing is
    asked why — only the ones already failing cost that, which is exactly when
    the answer is the thing the user needs.
    """
    from ...agents.grants import for_agent

    try:
        return for_agent(agent_id)
    except KeyError:
        raise HTTPException(404, f"unknown agent '{agent_id}'") from None


class AgentTools(BaseModel):
    """The whole tool list for an agent, not a delta.

    A delta would need the client and the server to agree on what the list was
    a moment ago, and the screen that sends this can have been open while a
    connector was added. Sending the whole list makes the last writer win,
    which is the behaviour a person expects from a row of switches.
    """

    tools: list[str] = Field(default_factory=list, max_length=200)


@router.patch("/api/agents/{agent_id}/tools")
def set_agent_tools(agent_id: str, body: AgentTools):
    """Change what one agent may use, from the next question onwards.

    Recorded as an override rather than an edit, so a preset keeps its shipped
    definition and a later release can still improve it. See
    `agents/tool_overrides.py` for why the names are not validated against the
    live catalog here.
    """
    from ...agents.presets import get_agent
    from ...agents.tool_overrides import get_tool_overrides

    try:
        get_agent(agent_id)
    except KeyError:
        raise HTTPException(404, f"unknown agent '{agent_id}'") from None

    get_tool_overrides().set(agent_id, body.tools)
    # Return the agent as it now is, so the client renders what was actually
    # stored rather than what it hoped it sent.
    agent = get_agent(agent_id)
    return {"id": agent.id, "name": agent.name, "tools": agent.tools}


@router.post("/api/agents/custom")
def create_agent(body: NewAgent):
    from ...agents.custom import get_custom_store
    a = get_custom_store().create(body.name, body.role, body.system_prompt,
                                  body.tools, body.recall_sources)
    return {"id": a.id, "name": a.name, "role": a.role}


@router.delete("/api/agents/custom/{agent_id}")
def delete_agent(agent_id: str):
    from ...agents.custom import get_custom_store
    if not get_custom_store().delete(agent_id):
        raise HTTPException(404, "not a custom agent")
    AgentMemory().clear(agent_id)
    return {"deleted": agent_id}


def _turn_images(body: ChatIn) -> list:
    """Validate the attachments, or 422 with a message meant for the user.

    A bad attachment is the user's to fix, so it is refused at the boundary
    with the reason — not carried inward to fail somewhere less legible.
    """
    from ...models.images import ImageError, parse_many
    if not getattr(body, "images", None):
        return []
    try:
        return parse_many([i.data_url for i in body.images],
                          names=[i.name for i in body.images])
    except ImageError as exc:
        raise HTTPException(422, str(exc)) from None


class RosterIn(BaseModel):
    """A library template the user is taking on, or putting back."""

    template_id: str


@router.get("/api/agents/library")
def agent_library():
    """Every agent Chitragupta offers, and which ones this user has.

    `missing` per template is what keeps the library honest: a card can say
    "connect Gmail first" rather than offering an agent that will disappoint.
    """
    from ...agents.library import CATEGORIES, describe

    return {"categories": list(CATEGORIES), "templates": describe()}


@router.post("/api/agents/roster")
def add_agent_to_roster(body: RosterIn):
    from ...agents.library import add_to_roster

    try:
        return {"roster": add_to_roster(body.template_id)}
    except KeyError:
        raise HTTPException(404, "That agent is not in the library.") from None


@router.delete("/api/agents/roster/{template_id}")
def remove_agent_from_roster(template_id: str):
    """Take an agent out of the sidebar. Its conversation is kept."""
    from ...agents.library import remove_from_roster

    return {"roster": remove_from_roster(template_id)}


class FolderIn(BaseModel):
    """A folder the user is opening to their agents, or closing again."""

    path: str


@router.get("/api/agents/folders")
def list_agent_folders():
    """Which folders agents can work in. Empty until the user picks one."""
    from ...agents import file_tools

    return {"folders": file_tools.granted_roots()}


@router.post("/api/agents/folders")
def grant_agent_folder(body: FolderIn):
    """Open a folder to agents.

    The grant IS the consent, so this is the only way anything on disk becomes
    reachable — there are no default grants and no implicit ones.
    """
    from ...agents import file_tools

    try:
        return file_tools.grant_folder(body.path)
    except ValueError as exc:
        # The message is written for a person, so it is passed through rather
        # than replaced with something about paths.
        raise HTTPException(400, str(exc)) from None


@router.delete("/api/agents/folders")
def revoke_agent_folder(path: str):
    from ...agents import file_tools

    return {"ok": file_tools.revoke_folder(path),
            "folders": file_tools.granted_roots()}


@router.post("/api/agents/turns/{turn_id}/stop")
async def stop_turn(turn_id: str):
    """Stop a running turn.

    `async` and doing no work, deliberately: `/chat` runs on the bounded
    `@calls_a_model` lane, so when every slot is busy — which is precisely when
    somebody wants to press Stop — a handler that needed a worker thread would
    queue behind the very turns it is meant to end.

    Always reports success. Stop is idempotent, it is allowed to arrive before
    the turn registers, and a turn that has already finished is also "stopped"
    as far as the person who pressed the button is concerned. Telling them
    otherwise would be reporting our bookkeeping as their problem.
    """
    cancellation.cancel(turn_id)
    return {"ok": True, "turn_id": turn_id}


@router.post("/api/agents/{agent_id}/chat")
@calls_a_model
def chat(agent_id: str, body: ChatIn):
    message = (body.message or "").strip()
    images = _turn_images(body)
    # An image on its own is a complete question ("what is this?"), so an empty
    # message is only empty when nothing came with it.
    if not message and not images:
        raise HTTPException(422, "message is empty")
    stop = cancellation.begin(body.turn_id)
    try:
        result = run_turn(agent_id, message, provider_name=body.provider,
                          model_name=body.model, effort=body.effort,
                          images=images, cancel=stop,
                          connectors=body.connectors)
    except KeyError:
        raise HTTPException(404, f"unknown agent '{agent_id}'") from None
    except Exception as exc:  # never 500 the chat — return a readable message
        return {"agent_id": agent_id, "provider": "", "model": "", "trace": [],
                "reply": f"⚠️ Something went wrong: {str(exc)[:200]}"}
    finally:
        cancellation.end(body.turn_id)
    return result.as_dict()


@router.post("/api/agents/{agent_id}/chat/stream")
async def chat_stream(agent_id: str, body: ChatIn):
    """The same turn as `/chat`, delivered as it happens.

    Server-Sent Events rather than a WebSocket: the flow is one-way, it survives
    a proxy that only speaks HTTP, and the browser reconnects on its own.

    The turn itself is blocking — a model call, then tools, then another model
    call — so it runs on the `MODEL_CALLS` lane and pushes each event back to
    the event loop as it happens. Running it inline would hold a worker thread
    for the whole conversation, which is the starvation this server already
    learned about once.

    Every event is a JSON object with a `type`: `token` as text is written,
    `tool_call` / `tool_result` as work happens, `plan` when the agent revises
    it, and exactly one `done` carrying the authoritative result. A client
    should render `token`s as a preview and replace them with `done`'s reply —
    the streamed text is what the model said on the way, and `done.reply` is
    what was stored.
    """
    import anyio
    from fastapi.responses import StreamingResponse

    from ...agents import run_turn
    from ..concurrency import MODEL_CALLS

    message = (body.message or "").strip()
    images = _turn_images(body)
    if not message and not images:
        raise HTTPException(400, "message is required")

    send, receive = anyio.create_memory_object_stream(max_buffer_size=512)

    stop = cancellation.begin(body.turn_id)

    def _work(push) -> None:
        try:
            result = run_turn(agent_id, message, provider_name=body.provider,
                              model_name=body.model, effort=body.effort,
                              images=images, cancel=stop,
                              connectors=body.connectors, on_event=push)
            push({"type": "done", "result": result.as_dict()})
        except KeyError:
            push({"type": "error", "message": f"unknown agent '{agent_id}'"})
        except Exception as exc:                       # never break the stream
            log.debug("streamed turn failed: %s", exc)
            push({"type": "error", "message": str(exc)[:300]})
        finally:
            cancellation.end(body.turn_id)

    async def _pump() -> None:
        async with send:
            def push(event: dict) -> None:
                anyio.from_thread.run(send.send, event)

            await anyio.to_thread.run_sync(lambda: _work(push),
                                           limiter=MODEL_CALLS)

    async def _events():
        async with anyio.create_task_group() as tg:
            tg.start_soon(_pump)
            async for event in receive:
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/agents/{agent_id}/clear")
def clear(agent_id: str):
    AgentMemory().clear(agent_id)
    return {"cleared": agent_id}






