"""The workspace around the agents: actions, routines, reminders, tasks, and the pages themselves."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...log import get_logger
from ..assets import WEB
from ..concurrency import calls_a_model

log = get_logger(__name__)
router = APIRouter()


@router.post("/api/open-browser")
def open_browser_endpoint(payload: dict):
    import webbrowser

    from ..security import is_safe_external_url

    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    # Only ever a web page. Without this the endpoint hands any installed app's
    # custom scheme — or a local file — to the system opener.
    if not is_safe_external_url(url):
        raise HTTPException(400, "Only http:// and https:// links can be opened.")
    try:
        webbrowser.open(url)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(500, f"Failed to open browser: {exc}") from exc


@router.get("/signin-hud")
def signin_hud_page():
    """The floating sign-in card (desktop app only)."""
    return FileResponse(WEB / "signin_hud.html")


class HudNoteIn(BaseModel):
    """What the page decided to do with a sign-in click."""
    provider: str = ""
    branch: str = ""
    detail: str = ""


@router.post("/api/hud/note")
def hud_note(body: HudNoteIn):
    """The page reports which sign-in branch it took.

    Only the frontend knows whether the floating card was skipped because the
    provider was already connected, or asked for and refused. Without that, the
    two are indistinguishable from the backend.
    """
    from ... import hud

    hud.note("branch", provider=body.provider, branch=body.branch,
             detail=body.detail[:200])
    return {"ok": True}


@router.get("/api/hud/diagnostics")
def hud_diagnostics():
    """Whether the floating sign-in window exists, and what the last click did."""
    from ... import hud
    from ...models import login_processes

    return {**hud.diagnostics(), "login_processes": login_processes.alive()}


# ── actions (execute only after explicit user confirmation) ───────────────
class ActionIn(BaseModel):
    type: str
    params: dict[str, Any] = {}


@router.post("/api/actions/execute")
@calls_a_model
def execute_action(body: ActionIn):
    """Run an action the user just approved, and tell the agent what happened.

    The result used to go only to the card, so the agent that proposed it never
    learned whether it worked — and could not fix its own call when it did not.
    It is recorded into the conversation now, and a FAILURE gets the agent one
    turn to answer for it. Nothing about the gate changes: this runs because a
    person pressed Confirm.

    On the model lane because that follow-up turn is a model call.
    """
    from ...actions import execute
    from ...agents.outcomes import settle

    params = body.params or {}
    result = execute(body.type, params)
    agent_id = str(params.get("agent_id") or "").strip()
    if not agent_id:
        return result
    return settle(agent_id, body.type, params, result)


class UndoIn(BaseModel):
    log_id: str


@router.post("/api/actions/undo")
def undo_action(body: UndoIn):
    """Take back a logged action, where taking it back is possible.

    Addressed by log entry rather than by what was proposed: the inverse needs
    the *result* — the calendar id Google handed back, the reminder row we
    wrote — and a caller reconstructing the call from the card does not have it.
    """
    from ...actions import undo

    return undo((body.log_id or "").strip())


@router.get("/api/actions/catalog")
def actions_catalog():
    """Every action's fields, risk tier and whether it can be undone.

    The card renders itself from this. The frontend used to keep its own idea
    of which actions were correctable (`EDITABLE = { log_workout: true }`),
    which was a second copy of a fact this registry already held.
    """
    from ...actions import catalog

    return {"actions": catalog()}


@router.get("/api/actions/log")
def actions_log(limit: int = 50, action_type: str = "", since: str = ""):
    """What the agents actually did — newest first.

    Approval is consent before; this is the record after, and it is what makes
    letting an agent act unattended a reasonable thing to agree to.
    """
    from ... import action_log

    return {"entries": action_log.recent(limit, action_type=action_type,
                                         since=since)}


@router.get("/api/actions/log/summary")
def actions_log_summary(days: int = 7):
    """Counts rather than rows — "what did you do this week?"."""
    from ... import action_log

    return action_log.summarise(days)


class NewRoutine(BaseModel):
    name: str
    agent_id: str = "personal"
    trigger: str = "new_email"          # new_email | schedule
    instruction: str
    interval_min: int = 60


class EditRoutine(BaseModel):
    """A partial edit. Every field is optional — sending only what changed is
    the difference between "rename this" and "replace this with what my form
    happened to be holding"."""

    name: str | None = None
    agent_id: str | None = None
    trigger: str | None = None
    instruction: str | None = None
    interval_min: int | None = None


class NewReminder(BaseModel):
    message: str
    #: ISO datetime. The client owns the clock here: the user picked a wall
    #: time in their own timezone, and re-deriving it server-side is how a
    #: 9am reminder becomes a 2pm one.
    fire_at: str
    agent_id: str | None = None


class EditReminder(BaseModel):
    message: str | None = None
    fire_at: str | None = None


@router.get("/api/routines")
def list_routines():
    from ...routines import get_routines
    return {"routines": get_routines().list()}


@router.post("/api/routines")
def create_routine(body: NewRoutine):
    from ...routines import get_routines
    return get_routines().create(body.name, body.agent_id, body.trigger,
                                 body.instruction, body.interval_min)


@router.patch("/api/routines/{rid}")
def edit_routine(rid: str, body: EditRoutine):
    from ...routines import get_routines
    out = get_routines().update(rid, **body.model_dump(exclude_none=True))
    if out is None:
        raise HTTPException(404, "no such routine")
    return out


@router.post("/api/routines/{rid}/toggle")
def toggle_routine(rid: str, on: bool = True):
    from ...routines import get_routines
    get_routines().toggle(rid, on)
    return {"ok": True}


@router.delete("/api/routines/{rid}")
def delete_routine(rid: str):
    from ...routines import get_routines
    return {"deleted": get_routines().delete(rid)}


@router.get("/api/reminders")
def list_reminders():
    import json as _json

    from ...reminders import get_reminders
    from ...scheduled import get_scheduled
    items = [{"kind": "reminder", "id": r["id"], "label": r["message"],
              "fire_at": r["fire_at"], "agent_id": r.get("agent_id")}
             for r in get_reminders().upcoming()]
    for a in get_scheduled().upcoming():
        p = _json.loads(a["params"] or "{}")
        label = (f"Send email to {p.get('to', '')}" if a["type"] == "send_email"
                 else f"Create event: {p.get('title', '')}")
        items.append({"kind": "action", "id": a["id"], "label": "⏳ " + label,
                      "fire_at": a["fire_at"], "agent_id": a.get("agent_id")})
    items.sort(key=lambda x: x["fire_at"])
    return {"reminders": items}


@router.post("/api/reminders")
def create_reminder(body: NewReminder):
    from ...reminders import get_reminders
    if not body.message.strip():
        raise HTTPException(422, "a reminder needs something to say")
    return get_reminders().add(body.message, body.fire_at, body.agent_id)


@router.patch("/api/reminders/{rid}")
def edit_reminder(rid: str, body: EditReminder):
    """Reword or move a reminder.

    Only reminders. The same list carries scheduled ACTIONS — a queued email,
    a calendar event — and those are not free text with a time attached; a
    half-edited action is worse than one the user cancels and re-asks for.
    """
    from ...reminders import get_reminders
    out = get_reminders().update(rid, body.message, body.fire_at)
    if out is None:
        raise HTTPException(404, "no such reminder, or it has already fired")
    return out


@router.delete("/api/reminders/{rid}")
def delete_reminder(rid: str):
    from ...reminders import get_reminders
    from ...scheduled import get_scheduled
    ok = get_reminders().delete(rid) or get_scheduled().delete(rid)
    return {"deleted": ok}


# ── tasks ─────────────────────────────────────────────────────────────────
class TaskIn(BaseModel):
    title: str
    due: str | None = None


@router.get("/api/tasks")
def list_tasks(when: str | None = None, include_done: bool = False):
    from ...tasks import get_tasks
    ts = get_tasks()
    return {"tasks": ts.list(when=when, include_done=include_done),
            "stats": ts.stats()}


@router.post("/api/tasks")
def add_task(body: TaskIn):
    from ...tasks import get_tasks
    return get_tasks().add(body.title, body.due)


@router.post("/api/tasks/{tid}/complete")
def complete_task(tid: str):
    from ...tasks import get_tasks
    t = get_tasks().complete(tid)
    if not t:
        raise HTTPException(404, "no task matched")
    return t


@router.delete("/api/tasks/{tid}")
def delete_task(tid: str):
    from ...tasks import get_tasks
    if not get_tasks().delete(tid):
        raise HTTPException(404, "no task matched")
    return {"deleted": tid}


# ── local filesystem browser (for the folder picker) ─────────────────────
@router.get("/api/fs/browse")
def fs_browse(path: str | None = None):
    """List subdirectories of a path so the UI can offer a native-feeling
    folder picker for the files connector. Read-only, dirs only."""
    base = Path(path).expanduser() if path else Path.home()
    try:
        base = base.resolve()
    except Exception:
        base = Path.home()
    if not base.exists() or not base.is_dir():
        base = Path.home()

    dirs, ingestible = [], 0
    try:
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith(".") or entry.name in {
                "node_modules", "__pycache__", ".venv", "venv"}:
                continue
            if entry.is_dir():
                dirs.append(entry.name)
            elif entry.suffix.lower() in {
                ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".json",
                ".yaml", ".yml", ".html", ".css", ".rst"}:
                ingestible += 1
    except PermissionError:
        pass
    return {
        "path": str(base),
        "parent": str(base.parent) if base.parent != base else None,
        "home": str(Path.home()),
        "dirs": dirs,
        "ingestible_here": ingestible,
    }


# ── dashboard ─────────────────────────────────────────────────────────────
@router.get("/")
def index():
    return FileResponse(WEB / "index.html")


@router.get("/onboarding")
def onboarding():
    return FileResponse(WEB / "onboarding.html")
