"""Which automations care about an event — a registry, not an `if/elif`.

The shape this replaces:

    if r["trigger"] == "daily":        ...
    elif r["trigger"] == "schedule":   ...
    elif r["trigger"] == "new_email" and new_email_count > 0: ...

Every new connector event meant another branch **inside the engine**. This
module inverts that: a trigger is an object that answers "does this event match
this spec", registered by name. `EventTrigger` then covers every connector event
there will ever be — a Gmail message, a GitHub push, a webhook, a file change
and one automation finishing are all `Event`s with different `kind`s, and none
of them needs code here.

What is left as real types is the small set that is **not** a plain event match:
time. A schedule has to answer "am I due?" against a clock and a timezone, and
that is a different question from "does this event look like mine".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.events import Event
from ..log import get_logger, suppressed

log = get_logger(__name__)

#: The tick every schedule-shaped trigger is evaluated against. The scheduler
#: emits it; nothing else should.
TICK = "schedule.tick"

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Match:
    """Does this event start this automation, and what did it mean?"""

    matched: bool
    #: Why not, for the run history. Empty when matched.
    detail: str = ""


NO = Match(False)
YES = Match(True)


class Trigger(Protocol):
    """One way an automation can start.

    `type` is what appears in the stored spec. `matches` is asked once per
    enabled automation per event, so it must be cheap and must not do IO.
    """

    type: str

    def matches(self, spec: dict[str, Any], event: Event, *,
                automation: Any, now: datetime) -> Match: ...

    def next_due(self, spec: dict[str, Any], *, last_run: str,
                 now: datetime) -> datetime | None:
        """When this will next fire, or None if it is not time-based."""
        ...


_REGISTRY: dict[str, Trigger] = {}


def register(trigger: Trigger) -> Trigger:
    """Add a trigger type. Returns it, so it can be used as a decorator."""
    _REGISTRY[trigger.type] = trigger
    return trigger


def get(type_name: str) -> Trigger | None:
    return _REGISTRY.get(type_name)


def known() -> list[str]:
    return sorted(_REGISTRY)


#: `type -> (the sentence a person reads, the spec keys it asks for)`.
#:
#: Here rather than in the frontend for the same reason as the condition
#: labels: the builder has to ask for exactly the keys `matches` reads, and a
#: form that asks for a key no trigger reads is a setting the user chose that
#: does nothing.
_TRIGGER_FORM: dict[str, tuple[str, tuple[str, ...]]] = {
    "event": ("When something happens", ("kind", "source")),
    "schedule": ("At a time of day", ("at_time", "days", "timezone")),
    "interval": ("Every so often", ("interval_min",)),
    "manual": ("Only when I ask", ()),
}


def describe() -> list[dict[str, Any]]:
    """Every trigger, with the questions it needs answered.

    An unlisted trigger still appears — with no fields — rather than being
    hidden: a build that has a trigger the form does not describe should say so,
    not quietly offer a shorter list than the engine supports.
    """
    out = []
    for name in known():
        label, fields = _TRIGGER_FORM.get(name, (name, ()))
        out.append({"type": name, "label": label, "fields": list(fields)})
    return out


def matches(automation: Any, event: Event, *, now: datetime | None = None) -> Match:
    """Does `event` start `automation`?

    An unknown trigger type does **not** match. That is the fail-closed answer:
    a spec naming a trigger this build does not have is a spec we cannot
    evaluate, and "run it anyway" is the one wrong response.
    """
    spec = automation.trigger or {}
    type_name = str(spec.get("type") or "")
    trigger = _REGISTRY.get(type_name)
    if trigger is None:
        return Match(False, f"unknown trigger type '{type_name}'")
    now = now or datetime.now(UTC)
    try:
        return trigger.matches(spec, event, automation=automation, now=now)
    except Exception as exc:                        # pragma: no cover - defensive
        log.debug("trigger %s raised: %s", type_name, exc)
        return Match(False, f"trigger error: {str(exc)[:80]}")


def next_due(automation: Any, *, now: datetime | None = None) -> datetime | None:
    spec = automation.trigger or {}
    trigger = _REGISTRY.get(str(spec.get("type") or ""))
    if trigger is None:
        return None
    with suppressed("computing an automation's next run"):
        return trigger.next_due(spec, last_run=automation.last_run or "",
                                now=now or datetime.now(UTC))
    return None


def _zone(spec: dict[str, Any]) -> Any:
    """The automation's timezone, or the machine's.

    Stored per automation because "every Monday at 9" means nine where the user
    is, and a user who travels does not expect their morning brief to move. An
    unknown zone name falls back to local rather than raising — a typo in a
    timezone must not stop an automation from ever running again.
    """
    name = str(spec.get("timezone") or "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        log.debug("unknown timezone %r on a trigger; using local", name)
        return None


def _local(now: datetime, spec: dict[str, Any]) -> datetime:
    zone = _zone(spec)
    return now.astimezone(zone) if zone else now.astimezone()


def _at(event: Event, now: datetime) -> datetime:
    """When to evaluate a time-based trigger: the tick's own moment.

    A tick *is* a clock reading, so a tick delivered late must be judged at the
    time it is for, not the time it arrived. Without this a queue that fell
    behind would evaluate yesterday's tick against today, and a schedule would
    fire for the wrong day.

    Falls back to `now` for an event with no timestamp, which is what a
    hand-built one has.
    """
    stamped = _parse(event.occurred_at)
    return stamped or now


# ── the trigger types ──────────────────────────────────────────────────────

class EventTrigger:
    """Match a normalised event by kind, source and simple field equality.

    **This is the extension point.** A new connector event needs no code here:
    it emits `Event(kind="issue.opened", source="linear", data={...})` and an
    automation stores `{"type": "event", "kind": "issue.opened",
    "source": "linear"}`.

    `where` is deliberately only equality on a dotted path. Anything richer is
    a *condition*, which is a different stage with a different budget — trigger
    matching runs once per enabled automation per event and must stay cheap.
    """

    type = "event"

    def matches(self, spec: dict[str, Any], event: Event, *,
                automation: Any, now: datetime) -> Match:
        want_kind = str(spec.get("kind") or "")
        if not want_kind:
            return Match(False, "event trigger has no kind")
        if event.kind != want_kind:
            return NO
        want_source = str(spec.get("source") or "")
        if want_source and event.source != want_source:
            return NO
        where = spec.get("where")
        if isinstance(where, dict):
            for path, expected in where.items():
                if _dig(event.data, str(path)) != expected:
                    return Match(False, f"{path} is not {expected!r}")
        return YES

    def next_due(self, spec: dict[str, Any], *, last_run: str,
                 now: datetime) -> datetime | None:
        return None


class ScheduleTrigger:
    """A wall-clock time, on chosen days, in the automation's timezone.

    Three things it has to get right, and the third is the one that bites:

    * **The day.** `weekday()` is Monday-zero, which is why `WEEKDAYS` is in
      that order — a name maps to the index by position.
    * **Local wall clock, not UTC.** "8am" means eight in the morning where the
      user is. Building the target in UTC moves it by the offset, and again
      twice a year.
    * **Late is better than never.** A laptop asleep at 08:00 and opened at
      11:00 still fires, because the user wanted the morning brief and did not
      get one. It fires *once*: `last_run` at or after today's target is what
      says today has been answered.

    That last rule is also the missed-run policy, and it is deliberately
    "catch up once, not once per missed occurrence". A machine off for a week
    should produce one brief, not seven.
    """

    type = "schedule"

    def matches(self, spec: dict[str, Any], event: Event, *,
                automation: Any, now: datetime) -> Match:
        if event.kind != TICK:
            return NO
        at_time = str(spec.get("at_time") or "")
        if not at_time:
            return Match(False, "schedule trigger has no time")
        try:
            hour, minute = (int(x) for x in at_time.split(":"))
        except ValueError:
            return Match(False, f"unreadable time '{at_time}'")

        here = _local(_at(event, now), spec)
        days = str(spec.get("days") or "")
        if days and WEEKDAYS[here.weekday()] not in days.split(","):
            return NO

        target = here.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if here < target:
            return NO
        last = _parse(automation.last_run)
        if last is not None and last >= target.astimezone(UTC):
            return Match(False, "already ran for this occurrence")
        return YES

    def next_due(self, spec: dict[str, Any], *, last_run: str,
                 now: datetime) -> datetime | None:
        at_time = str(spec.get("at_time") or "")
        try:
            hour, minute = (int(x) for x in at_time.split(":"))
        except ValueError:
            return None
        here = _local(now, spec)
        days = str(spec.get("days") or "")
        allowed = days.split(",") if days else list(WEEKDAYS)
        for ahead in range(8):
            day = here + timedelta(days=ahead)
            if WEEKDAYS[day.weekday()] not in allowed:
                continue
            target = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target > here:
                return target.astimezone(UTC)
        return None


class IntervalTrigger:
    """Every N minutes since the last run."""

    type = "interval"

    def matches(self, spec: dict[str, Any], event: Event, *,
                automation: Any, now: datetime) -> Match:
        if event.kind != TICK:
            return NO
        minutes = int(spec.get("interval_min") or 0)
        if minutes <= 0:
            return Match(False, "interval trigger has no interval")
        last = _parse(automation.last_run)
        if last is None:
            return YES
        if last + timedelta(minutes=minutes) <= _at(event, now):
            return YES
        return NO

    def next_due(self, spec: dict[str, Any], *, last_run: str,
                 now: datetime) -> datetime | None:
        minutes = int(spec.get("interval_min") or 0)
        if minutes <= 0:
            return None
        last = _parse(last_run)
        return (last + timedelta(minutes=minutes)) if last else now


class ManualTrigger:
    """Only when a person or the API asks for it.

    Matches `automation.requested`, and only when the event names this
    automation — otherwise every manual automation would start whenever any one
    of them was asked for.
    """

    type = "manual"

    def matches(self, spec: dict[str, Any], event: Event, *,
                automation: Any, now: datetime) -> Match:
        if event.kind != "automation.requested":
            return NO
        target = str(event.data.get("automation_id") or "")
        return YES if target == automation.id else NO

    def next_due(self, spec: dict[str, Any], *, last_run: str,
                 now: datetime) -> datetime | None:
        return None


def _dig(data: Any, path: str) -> Any:
    """`a.b.c` out of nested dicts. Missing is None, never an exception."""
    node = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _parse(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


register(EventTrigger())
register(ScheduleTrigger())
register(IntervalTrigger())
register(ManualTrigger())
