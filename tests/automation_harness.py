"""A whole automation engine with no model, no network and no connector.

Every dependency the executor has is a callable, so this replaces all of them
with recordings. That is what makes the interesting tests *deterministic*: a
crash between claiming an effect and recording it, an approval answered four
steps later, a verification that fails twice and then passes — none of those
can be tested against a real provider, and all of them are one line here.

The fakes are deliberately faithful about the things that matter: `FakeGate`
fails closed on an unknown action exactly as `permissions.check` does, and
`FakeWorld` records side effects so a duplicate is an assertion rather than an
inspection.
"""
from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chitragupta.automation.executor import Deps, Executor, Verdict
from chitragupta.automation.model import Automation, Policy
from chitragupta.core import automation_store as store
from chitragupta.core import events as event_log
from chitragupta.core.events import Event


def fresh_store() -> Path:
    """Point both durable stores at a database of this test's own."""
    path = Path(tempfile.mkdtemp()) / "automation.db"
    store.reset_for_tests(path)
    event_log.reset_for_tests(path)
    return path


class Clock:
    """A clock the test moves. Real time makes retry tests slow and flaky."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 28, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: Any) -> None:
        self.now += timedelta(**kw)


@dataclass
class FakeWorld:
    """The outside. Records every side effect so duplicates are assertable."""

    effects: list[tuple[str, dict]] = field(default_factory=list)
    #: `action_type -> result`. Anything unlisted succeeds.
    results: dict[str, dict] = field(default_factory=dict)
    #: Action types that raise instead of returning.
    raises: set[str] = field(default_factory=set)
    #: Verification answers. `None` means the action is unverifiable.
    verifications: dict[str, Any] = field(default_factory=dict)
    #: Raised by `perform` once, then cleared — a crash mid-action.
    crash_after_claim: str = ""

    def perform(self, action_type: str, params: dict) -> dict:
        self.effects.append((action_type, dict(params)))
        if action_type == self.crash_after_claim:
            self.crash_after_claim = ""
            raise RuntimeError("process died mid-action")
        if action_type in self.raises:
            raise RuntimeError(f"{action_type} exploded")
        return dict(self.results.get(action_type, {"ok": True}))

    def verify(self, action_type: str, params: dict, result: dict) -> dict | None:
        if action_type not in self.verifications:
            return {"verified": True, "at": "2026-09-28T09:00:00+00:00"}
        answer = self.verifications[action_type]
        if answer is None:
            return None
        if callable(answer):
            return answer(params, result)
        return dict(answer)

    def count(self, action_type: str) -> int:
        return sum(1 for a, _ in self.effects if a == action_type)


@dataclass
class FakeGate:
    """The permission answer, with the same fail-closed shape as the real one."""

    known: set[str] = field(default_factory=lambda: {
        "create_task", "send_email", "create_draft", "create_event",
        "message_send", "set_reminder"})
    #: Action types that need approval.
    blocked: set[str] = field(default_factory=lambda: {"send_email"})
    reason: str = "that address is not on your allow-list"

    def exists(self, action_type: str) -> bool:
        return action_type in self.known

    def __call__(self, action_type: str, params: dict) -> Verdict:
        if action_type not in self.known:
            return Verdict(False, "That is not an action Chitragupta knows.")
        if action_type in self.blocked:
            return Verdict(False, self.reason,
                           tuple(str(params.get("to") or "someone").split(",")))
        return Verdict(True)


@dataclass
class FakeApprovals:
    """Queued actions, and a way for a test to be the human.

    **`approve` performs the action**, because the real one does:
    `agents/approvals.approve` calls `actions.run_now` and the executor then
    records the outcome rather than running it a second time. A fake that only
    flipped a status made that whole branch invisible — a test could approve an
    email and assert nothing about whether it was sent, and the one duplicate
    this engine could still produce (the approval layer and the executor both
    acting) was unassertable. `build_deps` wires `perform` to the same
    `FakeWorld` the executor uses, so the count is across both paths.
    """

    queued: dict[str, dict] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    #: Set by `build_deps` to `FakeWorld.perform`.
    perform: Callable[[str, dict], dict] | None = None
    _n: int = 0

    def queue(self, **kw: Any) -> str:
        self._n += 1
        approval_id = f"approval-{self._n}"
        self.queued[approval_id] = dict(kw)
        self.states[approval_id] = "pending"
        return approval_id

    def state(self, approval_id: str) -> str:
        return self.states.get(approval_id, "missing")

    def approve(self, approval_id: str) -> dict:
        if self.states.get(approval_id) != "pending":
            # What the real one does: a row that is no longer pending is
            # refused, which is what makes a second tap harmless. A fake that
            # performed twice would have made that guard untestable.
            return {"ok": False, "error": f"Already {self.states.get(approval_id)}."}
        self.states[approval_id] = "approved"
        row = self.queued.get(approval_id) or {}
        if self.perform is None or not row:
            return {"ok": True}
        return self.perform(str(row.get("action_type") or ""),
                            dict(row.get("params") or {}))

    def reject(self, approval_id: str) -> None:
        self.states[approval_id] = "rejected"


@dataclass
class FakeAgent:
    """A scripted agent turn. `replies` is consumed one per plan."""

    replies: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    raises: bool = False
    default: str = "Nothing to do."

    def __call__(self, agent_id: str, prompt: str) -> tuple[str, int]:
        self.prompts.append(prompt)
        if self.raises:
            raise RuntimeError("the model is unreachable")
        reply = self.replies.pop(0) if self.replies else self.default
        return reply, 1


def action_tag(action_type: str, **params: Any) -> str:
    """The `<action>` shape a real agent emits, so the real parser is used."""
    attrs = " ".join(f'{k}="{v}"' for k, v in params.items())
    return f'<action type="{action_type}" {attrs}></action>'


def build_deps(*, agent: FakeAgent | None = None, world: FakeWorld | None = None,
               gate: FakeGate | None = None,
               approvals: FakeApprovals | None = None,
               clock: Clock | None = None,
               judge: Callable[..., tuple[bool, float, str]] | None = None,
               emit: Callable[[Event], Any] | None = None,
               verify: bool = True) -> tuple[Deps, dict[str, Any]]:
    """Wire a full `Deps` out of fakes, returning it and the fakes themselves."""
    from chitragupta.actions import parse_actions

    agent = agent or FakeAgent()
    world = world or FakeWorld()
    gate = gate or FakeGate()
    approvals = approvals or FakeApprovals()
    # The human's tap runs the action, exactly as `approvals.approve` does.
    approvals.perform = world.perform
    clock = clock or Clock()
    notices: list[tuple[str, str]] = []

    deps = Deps(
        plan=agent,
        parse_actions=parse_actions,
        gate=gate,
        perform=world.perform,
        queue_approval=lambda **kw: approvals.queue(**kw),
        approval_state=approvals.state,
        action_exists=gate.exists,
        verify=world.verify if verify else None,
        judge=judge,
        recall=None,
        emit=emit,
        notify=lambda t, b: notices.append((t, b)),
        now=clock,
    )
    return deps, {"agent": agent, "world": world, "gate": gate,
                  "approvals": approvals, "clock": clock, "notices": notices}


def automation(**kw: Any) -> Automation:
    """An automation with sensible defaults, overridable field by field."""
    base: dict[str, Any] = {
        "id": "auto-1", "name": "Test automation", "agent_id": "personal",
        "instruction": "Do the thing.", "goal": "the thing is done",
        "trigger": {"type": "event", "kind": "email.received", "source": "gmail"},
        "conditions": [], "policy": Policy(),
    }
    base.update(kw)
    return Automation(**base)


def start_run(auto: Automation, event: Event | None = None) -> dict:
    """Create a PENDING run the way the router would."""
    event = event or Event(kind="email.received", source="gmail",
                           external_id="msg-1", subject="Hello",
                           data={"from": "ana@acme.com", "subject": "Hello"})
    return store.create_run(auto.id, automation_name=auto.name,
                            trigger=event.as_dict(),
                            max_attempts=auto.policy.retry.max_attempts,
                            correlation_id=event.correlation_id)


def drive(auto: Automation, deps: Deps, event: Event | None = None) -> dict:
    """Start a run and advance it to wherever it stops."""
    run = start_run(auto, event)
    Executor(deps).advance(run["id"], auto)
    return store.get_run(run["id"]) or {}
