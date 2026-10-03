"""Connector tools an agent may call in the middle of a turn.

The thirteen built-in tools are known at import time. A connector's tools are
not: they arrive when the user adds a server, they differ per install, and the
same server can expose different tools after an update. So an agent's stored
tool list cannot name them — a list written today would name tools that do not
exist until tomorrow, and `build_tools()` would silently drop them forever.

An agent therefore opts *in to the category*, with `SENTINEL` in its tool list,
and the concrete tools are resolved at the moment the turn starts. Naming one
explicitly still works, for a user who narrows a custom agent to a single tool.

**Read-only, with no exception.** A tool whose `writes` flag is set is never
offered to a model here, at any effort, under any agent. Connector writes reach
the user through `mcp_action`, which is in `permissions.NEVER_UNATTENDED` and
goes through propose → confirm, and the reason is recorded in `permissions.py`:
a write tool belongs to somebody else's server, so there is no field we could
read a recipient out of and therefore nothing an allow-list could check. The
filter here is the same decision applied one layer earlier — a model cannot
propose what it was never handed.

The supplying half lives in `chitragupta/connectors/mcp_tools.py` and is owned by
the Connectors lane. It is imported by name at call time rather than at import
time, because an agent turn must still run on a checkout where that module has
not landed yet — and because a missing connector layer is "no extra tools", not
a broken agent.
"""
from __future__ import annotations

import importlib
import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from ..log import get_logger, suppressed
from ..models.base import Tool

log = get_logger(__name__)

#: What an agent's tool list says to mean "whatever the user's connectors can
#: read". A category, not an enumeration — see the module docstring.
SENTINEL = "mcp"

#: The module the Connectors lane supplies. Resolved by name so this file has no
#: import-time dependency on it.
SUPPLIER = "chitragupta.connectors.mcp_tools"

#: How long a discovered tool list is reused. Listing tools starts every
#: configured server as a subprocess, and a turn can resolve names several times
#: (once to build the list, once per call to validate and run it). Short enough
#: that a server added mid-session shows up on the next question.
CACHE_SECONDS = 20.0

#: Connector calls running at once, across the whole process. The agent loop's
#: parallel width (6 at High) is sized for SQLite reads; these are subprocesses
#: that can each hold tens of megabytes and take seconds, so they get their own,
#: tighter ceiling instead of shrinking the width for the cheap tools too.
MAX_CONCURRENT_CALLS = 3

#: A tool's answer is read by a model, so it is charged for. A server that
#: returns its whole mailbox must not spend the turn's context on one call.
#: A backstop, not the ceiling. The supplier bounds its own results and says so
#: in a sentence naming what it held back, which is what lets a model narrow its
#: question instead of assuming it saw everything. Cutting below that ceiling
#: deletes that sentence and replaces it with a vaguer one, so this sits
#: deliberately *above* it and only catches a supplier that bounds nothing.
MAX_RESULT_CHARS = 12000

_gate = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)
_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "defs": None, "rows": None}


def _supplier() -> Any | None:
    """The connectors-side module, or None if this checkout has no MCP layer."""
    try:
        return importlib.import_module(SUPPLIER)
    except ModuleNotFoundError:
        return None


def clear_cache() -> None:
    """Forget the discovered tools — after a server is added, and in tests."""
    with _lock:
        _cache.update(at=0.0, defs=None, rows=None)


# ── discovery ────────────────────────────────────────────────────────────────

def _readable() -> list[Any]:
    """Every tool the user's connectors expose that only reads.

    Failures are swallowed rather than raised: a server that will not start is
    a connector problem the Connectors surface reports, and it must not be the
    reason an agent cannot answer a question about the user's email.
    """
    supplier = _supplier()
    if supplier is None:
        return []
    refs: list[Any] = []
    with suppressed("listing the tools the user's connectors expose"):
        refs = list(supplier.list_tools() or [])
    return [r for r in refs if not getattr(r, "writes", False)]


def _model_safe(qualified: str, taken: dict[str, Tool]) -> str:
    """A tool name every provider will accept.

    Providers constrain tool names (`[A-Za-z0-9_-]`, length-capped) and a
    qualified name is built by someone else, so it can carry a `:` or a `.`.
    A name the provider rejects fails the whole request, not just the tool.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", qualified).strip("_")[:60] or "connector_tool"
    if safe not in taken:
        return safe
    for n in range(2, 100):
        candidate = f"{safe}_{n}"
        if candidate not in taken:
            return candidate
    return safe


def _label(ref: Any) -> str:
    return (str(getattr(ref, "server_label", "") or "").strip()
            or str(getattr(ref, "server_id", "") or "").strip()
            or "a connector")


def _describe(ref: Any) -> str:
    """What the model is told the tool does, and whose data it reaches.

    The connector's name is part of it: a model choosing between `search` and
    `search_2` picks by coin toss, and one choosing between "search Notion" and
    "search Linear" picks correctly.
    """
    label = _label(ref)
    described = str(getattr(ref, "description", "") or "").strip()
    if not described:
        described = f"Run the {getattr(ref, 'tool', '') or 'connector'} tool."
    return f"{described} (read-only, from the user's {label} connector)"


def _schema(ref: Any) -> dict[str, Any]:
    params = getattr(ref, "parameters", None)
    if isinstance(params, dict) and params:
        return params
    return {"type": "object", "properties": {}}


def _as_text(answer: Any, label: str) -> str:
    """Whatever the server answered with, as something a model can read."""
    if answer is None:
        return f"{label} returned nothing."
    if isinstance(answer, str):
        text = answer.strip()
    elif isinstance(answer, (dict, list)):
        text = json.dumps(answer, ensure_ascii=False, indent=2, default=str)
    else:
        text = str(answer).strip()
    if not text:
        return f"{label} returned nothing."
    if len(text) > MAX_RESULT_CHARS:
        return text[:MAX_RESULT_CHARS] + f"\n\n[…truncated; {label} returned more]"
    return text


def _explain(exc: BaseException, label: str) -> str:
    """The connector layer's sentence for a failure, never the traceback."""
    with suppressed("explaining an MCP tool failure"):
        from ..connectors.mcp_errors import explain
        return explain(exc, label)
    return f"{label} could not be reached just now."


def _caller(qualified: str, label: str) -> Callable[..., str]:
    def call(**arguments: Any) -> str:
        supplier = _supplier()
        if supplier is None:                      # pragma: no cover - defensive
            return f"{label} is not available right now."
        with _gate:
            try:
                answer = supplier.call_tool(qualified, dict(arguments))
            except Exception as exc:
                log.debug("MCP tool %s failed: %s", qualified, exc)
                return _explain(exc, label)
        return _as_text(answer, label)

    return call


def _build() -> tuple[dict[str, Tool], list[dict[str, str]]]:
    """Discover once, and produce both shapes the runtime needs from it.

    The API row and the model-facing tool must describe the same thing, so they
    are built from the same pass over the same refs rather than from two.
    """
    built: dict[str, Tool] = {}
    rows: list[dict[str, str]] = []
    for ref in _readable():
        qualified = str(getattr(ref, "qualified_name", "") or "").strip()
        if not qualified:
            continue
        label = _label(ref)
        name = _model_safe(qualified, built)
        built[name] = Tool(name=name, description=_describe(ref),
                           parameters=_schema(ref),
                           handler=_caller(qualified, label))
        rows.append({"name": name, "description": built[name].description,
                     "label": str(getattr(ref, "tool", "") or name),
                     "source": "mcp", "connector": label,
                     # **Read, and said so rather than left blank.** Every tool
                     # that reaches this list passed `_readable()`, so a write
                     # cannot be here at all — but the panel groups its switches
                     # by `access`, and a row carrying none fell into no tier
                     # and got no switch. Twenty-five Notion tools, one at a
                     # time, is the wall that screen exists to remove. The fact
                     # belongs here, where the filter that makes it true is.
                     "access": "read",
                     # The id, beside the label. Permission is decided against
                     # the id — a label is what a person reads and a user can
                     # rename, and a rename must not change who may use what.
                     "connector_id": str(getattr(ref, "server_id", "") or "").lower()})
    return built, rows


def _resolved() -> tuple[dict[str, Tool], list[dict[str, str]]]:
    now = time.monotonic()
    with _lock:
        defs, rows = _cache["defs"], _cache["rows"]
        if defs is not None and (now - float(_cache["at"])) < CACHE_SECONDS:
            return defs, rows
    defs, rows = _build()
    with _lock:
        _cache.update(at=time.monotonic(), defs=defs, rows=rows)
    return defs, rows


def definitions() -> dict[str, Tool]:
    """Every readable connector tool, by the name the model sees."""
    return dict(_resolved()[0])


# ── what the rest of the runtime asks for ────────────────────────────────────

def available() -> list[Tool]:
    """The tools an agent that opted in gets this turn."""
    return list(definitions().values())


def lookup(name: str) -> Tool | None:
    """Resolve one name, for validation and execution. None if it is not ours."""
    if not name or name == SENTINEL:
        return None
    return definitions().get(name)


def connector_of(tool_name: str) -> str:
    """Which connector a tool belongs to, or "" if it is not a connector tool.

    By id, not label. Built from the same pass that built the tool, so the
    answer cannot drift from what actually runs.
    """
    if not tool_name or tool_name == SENTINEL:
        return ""
    _defs, rows = _resolved()
    for row in rows:
        if row.get("name") == tool_name:
            return row.get("connector_id", "")
    return ""


def connector_ids() -> list[str]:
    """Every connector currently offering an agent something, by id."""
    _defs, rows = _resolved()
    seen: dict[str, None] = {}
    for row in rows:
        cid = row.get("connector_id") or ""
        if cid:
            seen.setdefault(cid, None)
    return list(seen)


def labels_by_id() -> dict[str, str]:
    """Connector id → the name a person reads. For cards and pickers."""
    _defs, rows = _resolved()
    return {r.get("connector_id", ""): r.get("connector", "")
            for r in rows if r.get("connector_id")}


def describe() -> list[dict[str, str]]:
    """Rows for `GET /api/agents/tools`.

    The sentinel is only offered when there is something behind it: a control
    that cannot do anything reads as a broken app, so an install with no
    connectors simply does not see it.
    """
    _defs, rows = _resolved()
    if not rows:
        return []
    return [{
        "name": SENTINEL,
        # `name` is the value an agent stores; `label` is the only part a
        # person reads. They are different strings on purpose: the sentinel's
        # name is our internal switch, and rendering it verbatim put the
        # protocol's acronym on screen as though it were a skill.
        "label": "Everything my connectors can read",
        "description": ("Everything the user's connectors can read. Stays "
                        "correct as connectors are added or removed."),
        # Not "mcp": this row grants the whole category rather than naming one
        # connector's tool, so a consumer that groups by connector must be able
        # to tell it apart instead of inventing a group to put it in.
        "source": "category",
        "connector": "",
        # The same reason as the rows below it: this grants what the user's
        # connectors can READ and nothing else, so it belongs under the card's
        # read switch rather than in no tier at all.
        "access": "read",
    }, *[dict(r) for r in rows]]
