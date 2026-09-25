"""What an automation *is*, as data.

"Whenever X happens, do Y, but only if Z." Three fields and a pile of policy:

    trigger      X — normalised to an `Event`, matched by a registered trigger
    conditions   Z — deterministic first, semantic only where it must be
    goal         Y — what the user wants true afterwards, in their words
    policy       everything about *how hard to try and when to stop*

Policy is separated from the goal on purpose. "Draft a reply to my client" is
the goal; "retry three times, never send without asking, give up after five
minutes" is not something a user should have to say, and is exactly what has to
be written down somewhere durable rather than implied by the code path taken.

An `Automation` is a frozen view over a `routines` row. The row is the truth;
this is how the engine reads it without every caller doing `json.loads`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

#: A run that has not finished in this long is over. The ceiling exists because
#: an agent turn can hang on a connector, and an automation that is "running"
#: for a day is indistinguishable from one that is broken.
DEFAULT_MAX_DURATION_SECONDS = 600

#: How many side-effecting actions one run may cause. A plan that wants more
#: than this is not a plan, it is a loop that found a way out.
DEFAULT_MAX_ACTIONS = 12

#: Model turns per run, counted across the planning turn and any follow-ups.
DEFAULT_MAX_MODEL_CALLS = 8

DEFAULT_MAX_ATTEMPTS = 3


class Concurrency:
    """What to do when a run is asked for while one is already going.

    `ONE_ACTIVE_RUN` is the default and is the safest useful answer. Parallel is
    only correct when the automation causes no side effects that could collide,
    and that is a claim about the automation the user has to make deliberately.

    `COALESCE` is for noisy triggers: ten emails arriving in a burst should
    produce one run that sees ten, not ten runs that each see one. It is not the
    default because it silently changes what the automation observes.
    """

    ALLOW_PARALLEL = "allow_parallel"
    ONE_ACTIVE_RUN = "one_active_run"
    QUEUE = "queue_runs"
    COALESCE = "coalesce_events"

    ALL = (ALLOW_PARALLEL, ONE_ACTIVE_RUN, QUEUE, COALESCE)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded, and bounded in wall-clock as well as in count.

    Exponential with a ceiling: a connector that is down stays down for minutes,
    and retrying every second for three attempts answers a different question
    from the one being asked.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: float = 30.0
    backoff_multiplier: float = 3.0
    max_backoff_seconds: float = 900.0

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before attempt number `attempt` (1-based)."""
        if attempt <= 1:
            return 0.0
        raw = self.backoff_seconds * (self.backoff_multiplier ** (attempt - 2))
        return float(min(raw, self.max_backoff_seconds))


@dataclass(frozen=True)
class Limits:
    """Ceilings a run spends against. Exceeding one is BLOCKED, not an error.

    A limit hit is the system working. Reporting it as a failure teaches the
    user to ignore failures.
    """

    max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS
    max_actions: int = DEFAULT_MAX_ACTIONS
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    #: How many runs may descend from one original cause before the chain is
    #: refused. Bounds a *fan* — twenty automations each triggering one more is
    #: not deep and is still a runaway — where `Event.depth` bounds a chain.
    max_lineage_runs: int = 20


@dataclass(frozen=True)
class Policy:
    """Everything about how hard to try, and what may happen unattended."""

    retry: RetryPolicy = field(default_factory=RetryPolicy)
    limits: Limits = field(default_factory=Limits)
    concurrency: str = Concurrency.ONE_ACTIVE_RUN

    #: Require verification on actions whose spec can do it. On by default:
    #: "the API returned 200" is not "the event is in the calendar", and the
    #: whole point of an unattended system is that nobody is watching to notice.
    verify: bool = True

    #: What to do when an action is refused unattended. `queue` puts it in front
    #: of the user and **pauses the run**; `escalate` stops and explains.
    #: Never `skip` — an automation that quietly drops the thing it exists to do
    #: is worse than one that stops.
    on_blocked_action: str = "queue"

    #: Wait this long for a human before giving up on a paused run. Zero waits
    #: forever, which is right for "draft an email" and wrong for "book the
    #: table before it goes".
    approval_timeout_seconds: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "retry": {
                "max_attempts": self.retry.max_attempts,
                "backoff_seconds": self.retry.backoff_seconds,
                "backoff_multiplier": self.retry.backoff_multiplier,
                "max_backoff_seconds": self.retry.max_backoff_seconds,
            },
            "limits": {
                "max_duration_seconds": self.limits.max_duration_seconds,
                "max_actions": self.limits.max_actions,
                "max_model_calls": self.limits.max_model_calls,
                "max_lineage_runs": self.limits.max_lineage_runs,
            },
            "concurrency": self.concurrency,
            "verify": self.verify,
            "on_blocked_action": self.on_blocked_action,
            "approval_timeout_seconds": self.approval_timeout_seconds,
        }

    @staticmethod
    def from_dict(raw: Any) -> Policy:
        """Build a policy from stored JSON, defaulting every missing field.

        Tolerant on purpose: a policy written by an older version must not stop
        the automation from running, and a policy a user hand-edited badly
        should fall back to the safe value rather than raise inside the engine.
        """
        data: dict[str, Any] = raw if isinstance(raw, dict) else {}
        nested = data.get("retry")
        retry_raw: dict[str, Any] = nested if isinstance(nested, dict) else {}
        nested = data.get("limits")
        limits_raw: dict[str, Any] = nested if isinstance(nested, dict) else {}
        defaults = RetryPolicy()
        limit_defaults = Limits()

        def _num(source: dict, key: str, fallback: float) -> float:
            try:
                return float(source.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        concurrency = data.get("concurrency")
        if concurrency not in Concurrency.ALL:
            concurrency = Concurrency.ONE_ACTIVE_RUN
        blocked = data.get("on_blocked_action")
        if blocked not in ("queue", "escalate"):
            blocked = "queue"

        return Policy(
            retry=RetryPolicy(
                max_attempts=max(1, int(_num(retry_raw, "max_attempts",
                                             defaults.max_attempts))),
                backoff_seconds=_num(retry_raw, "backoff_seconds",
                                     defaults.backoff_seconds),
                backoff_multiplier=_num(retry_raw, "backoff_multiplier",
                                        defaults.backoff_multiplier),
                max_backoff_seconds=_num(retry_raw, "max_backoff_seconds",
                                         defaults.max_backoff_seconds),
            ),
            limits=Limits(
                max_duration_seconds=int(_num(limits_raw, "max_duration_seconds",
                                              limit_defaults.max_duration_seconds)),
                max_actions=int(_num(limits_raw, "max_actions",
                                     limit_defaults.max_actions)),
                max_model_calls=int(_num(limits_raw, "max_model_calls",
                                         limit_defaults.max_model_calls)),
                max_lineage_runs=int(_num(limits_raw, "max_lineage_runs",
                                          limit_defaults.max_lineage_runs)),
            ),
            concurrency=concurrency,
            verify=bool(data.get("verify", True)),
            on_blocked_action=blocked,
            approval_timeout_seconds=int(_num(data, "approval_timeout_seconds", 0)),
        )


@dataclass(frozen=True)
class Automation:
    """One automation, as the engine reads it."""

    id: str
    name: str
    agent_id: str
    instruction: str
    enabled: bool = True
    goal: str = ""
    owner: str = ""
    trigger: dict[str, Any] = field(default_factory=dict)
    conditions: list[dict[str, Any]] = field(default_factory=list)
    policy: Policy = field(default_factory=Policy)
    created_at: str = ""
    updated_at: str = ""
    last_run: str = ""
    next_run: str = ""

    @property
    def stated_goal(self) -> str:
        """What the user wants. Falls back to the instruction, which is what an
        automation created before goals existed has."""
        return self.goal or self.instruction

    def with_policy(self, **kw: Any) -> Automation:
        return replace(self, policy=replace(self.policy, **kw))

    @staticmethod
    def from_row(row: dict[str, Any]) -> Automation:
        """Read a `routines` row, old shape or new.

        **Back-compatibility is the point of this function.** A routine created
        before automations existed has `trigger='daily'`, `at_time`, `days` and
        `interval_min` and no `trigger_json` at all. It keeps working, because
        the legacy columns are read into a trigger spec here rather than being
        migrated in the database — a migration that rewrites a user's routines
        is a migration that can corrupt them.
        """
        def _json(key: str, default: Any) -> Any:
            raw = row.get(key)
            if isinstance(raw, (dict, list)):
                return raw
            if not raw:
                return default
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return default

        trigger = _json("trigger_json", {})
        if not trigger:
            trigger = _legacy_trigger(row)

        return Automation(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            agent_id=str(row.get("agent_id") or "personal"),
            instruction=str(row.get("instruction") or ""),
            enabled=bool(row.get("enabled", 1)),
            goal=str(row.get("goal") or ""),
            owner=str(row.get("owner") or ""),
            trigger=trigger,
            conditions=list(_json("conditions_json", [])),
            policy=Policy.from_dict(_json("policy_json", {})),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            last_run=str(row.get("last_run") or ""),
            next_run=str(row.get("next_run") or ""),
        )


def _legacy_trigger(row: dict[str, Any]) -> dict[str, Any]:
    """The trigger spec a pre-automation routine implies.

    Three shapes ever existed, and each maps onto exactly one registered
    trigger — which is the argument that the registry is the right abstraction
    rather than an abstraction invented for its own sake.
    """
    kind = str(row.get("trigger") or "").strip()
    if kind == "daily":
        return {"type": "schedule", "at_time": str(row.get("at_time") or ""),
                "days": str(row.get("days") or "")}
    if kind == "schedule":
        return {"type": "interval",
                "interval_min": int(row.get("interval_min") or 60)}
    if kind == "new_email":
        return {"type": "event", "kind": "email.received", "source": "gmail"}
    if kind == "manual":
        return {"type": "manual"}
    # An unrecognised legacy trigger becomes manual rather than something that
    # fires. A routine nobody can explain must not run unattended.
    return {"type": "manual"}
