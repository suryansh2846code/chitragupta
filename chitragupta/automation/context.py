"""What the automation is allowed to know, and where each part came from.

Two failures this exists to prevent, and they pull in opposite directions.

**Too much.** `brain.recall()` on every run of every automation is the cost
model of a system nobody can afford to leave on. Context here is *bounded* —
a fixed number of memories, a fixed number of prior runs, truncated bodies —
and the bound is a number in this file rather than an emergent property of how
much happened to match.

**Too trusting.** The old routine pasted email bodies straight into the prompt:

    ctx = "NEW EMAIL(S) that just arrived:\\n" + ...

That is the injection surface. A stranger writes the text, it lands in the
model's context indistinguishable from ours, and the next paragraph can say
whatever it likes about what the automation is for. Every piece of context here
carries a `Provenance`, and anything at or below `FENCED_AT` is wrapped with a
fence it cannot close from the inside.

The snapshot is a **dict, serialised into the run**. That is what makes it
auditable: a user asking "why did it do that" gets the exact text the model saw,
months later, without re-running anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.events import Event
from ..core.provenance import APP, Provenance, Trust, looks_like_injection, wrap
from ..log import get_logger, suppressed

log = get_logger(__name__)

#: Memories pulled per run. Small: an automation has a *specific* job, and the
#: brain's job here is to name the people and projects in the trigger, not to
#: reconstruct the user's life.
MAX_MEMORIES = 6

#: Previous runs shown, so the agent can see "I already emailed them on Monday".
MAX_PRIOR_RUNS = 3

#: Characters of any single untrusted body. An email thread can be a megabyte;
#: what an automation needs is the top of it.
MAX_BODY_CHARS = 2_000

#: Total characters of context. A hard ceiling, applied after assembly, so one
#: enormous section cannot crowd out the others silently.
MAX_TOTAL_CHARS = 12_000


@dataclass(frozen=True)
class Piece:
    """One labelled, attributed chunk of what the agent will be told."""

    title: str
    body: str
    provenance: Provenance

    def rendered(self) -> str:
        body = wrap(self.body, self.provenance)
        return f"## {self.title}\n{body}" if body else ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body[:MAX_BODY_CHARS],
            "source": self.provenance.source,
            "trust": int(self.provenance.trust),
            "fenced": self.provenance.fenced,
            "reference": self.provenance.reference,
        }


@dataclass
class Snapshot:
    """Everything the run may know, and the record of how it was built."""

    pieces: list[Piece] = field(default_factory=list)
    #: Flat, machine-readable facts the *condition* engine reads. Never fenced,
    #: because conditions are code — they compare values, they do not read
    #: prose — and never shown to a model as instructions either.
    facts: dict[str, Any] = field(default_factory=dict)
    #: Sections dropped to stay inside the budget, named so a user can see that
    #: the automation was working from less than everything.
    truncated: list[str] = field(default_factory=list)
    #: Untrusted pieces that contained a phrase whose only job is to redirect an
    #: agent. Recorded, not acted on: the fence is what makes it fail, and this
    #: is what makes the attempt visible.
    injection_attempts: list[str] = field(default_factory=list)

    def add(self, title: str, body: str, provenance: Provenance) -> None:
        text = (body or "").strip()
        if not text:
            return
        if len(text) > MAX_BODY_CHARS:
            text = text[:MAX_BODY_CHARS] + "\n[…truncated]"
            self.truncated.append(title)
        if provenance.fenced and looks_like_injection(text):
            self.injection_attempts.append(
                f"{title} ({provenance.reference or provenance.source})")
            log.info("automation context: injection-shaped text in %s from %s",
                     title, provenance.source)
        self.pieces.append(Piece(title, text, provenance))

    def rendered(self) -> str:
        """The context as the agent sees it, inside the total budget."""
        out: list[str] = []
        used = 0
        for piece in self.pieces:
            text = piece.rendered()
            if not text:
                continue
            if used + len(text) > MAX_TOTAL_CHARS:
                self.truncated.append(piece.title)
                continue
            out.append(text)
            used += len(text)
        return "\n\n".join(out)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pieces": [p.as_dict() for p in self.pieces],
            "facts": self.facts,
            "truncated": sorted(set(self.truncated)),
            "injection_attempts": self.injection_attempts,
            "chars": len(self.rendered()),
        }


def _event_provenance(event: Event) -> Provenance:
    return Provenance(
        source=event.source,
        trust=event.trust,
        label=f"{event.kind.upper().replace('.', ' ')} CONTENT",
        reference=event.external_id or event.subject,
    )


def build(automation: Any, event: Event, *, recall: Any = None,
          prior_runs: list[dict] | None = None,
          tasks: list[dict] | None = None) -> Snapshot:
    """Assemble the snapshot for one run.

    `recall` and the lists are injected rather than imported. Two reasons, and
    the second is the one that matters: it keeps this module free of `brain/`
    and of the stores, so a test can build a context with no database — and the
    caller, which already knows whether the brain is even available, decides
    whether to pay for a recall at all.
    """
    snap = Snapshot()

    # 1. The goal. Ours, and the only thing that may express intent.
    snap.add("What this automation is for",
             automation.stated_goal, APP)

    # 2. The trigger, as structure first. Conditions read this; the model sees
    #    it too, but as a labelled fact rather than as prose to follow.
    snap.facts["event"] = dict(event.data)
    snap.facts["event_kind"] = event.kind
    snap.facts["event_source"] = event.source
    snap.facts["automation"] = {
        "id": automation.id, "name": automation.name,
        "last_run": automation.last_run,
    }

    summary = _describe_event(event)
    if summary:
        snap.add(f"What happened ({event.kind})", summary, _event_provenance(event))

    # 3. Free text from the event — the part a stranger wrote. Separated from
    #    the structured summary above so the fence wraps exactly it.
    for key in ("body", "text", "content", "message", "description"):
        value = event.data.get(key)
        if isinstance(value, str) and value.strip():
            snap.add(f"{key.title()} of the {event.kind.split('.')[0]}",
                     value, _event_provenance(event))
            break

    # 4. The brain, bounded. Queried on the event's *subject line*, not its
    #    body: the body is untrusted text, and using it as a search query lets
    #    a stranger choose which of the user's memories get loaded.
    if recall is not None:
        query = (event.subject or automation.stated_goal or "")[:200]
        with suppressed("recalling context for an automation run"):
            hits = recall(query, limit=MAX_MEMORIES) or []
            lines = []
            for hit in hits[:MAX_MEMORIES]:
                text = hit.get("text") if isinstance(hit, dict) else str(hit)
                if text:
                    lines.append(f"- {str(text)[:240]}")
            if lines:
                snap.add("What Chitragupta already knows", "\n".join(lines),
                         Provenance("brain", Trust.TRUSTED_APP_DATA,
                                    "BRAIN", "recall"))

    # 5. What this automation did last time — how an agent avoids doing it
    #    again. Outcomes only, never the old context: a run that carried the
    #    previous run's context would grow without bound.
    if prior_runs:
        lines = []
        for run in prior_runs[:MAX_PRIOR_RUNS]:
            when = str(run.get("finished_at") or run.get("created_at") or "")[:19]
            state = run.get("state", "")
            outcome = str(run.get("outcome") or run.get("reason") or "")[:160]
            lines.append(f"- {when} · {state} · {outcome}")
        snap.add("Previous runs of this automation", "\n".join(lines), APP)
        snap.facts["prior_run_states"] = [r.get("state") for r in prior_runs]

    if tasks:
        lines = [f"- {str(t.get('title', ''))[:120]}" for t in tasks[:10]]
        snap.add("Open tasks", "\n".join(lines), APP)
        snap.facts["open_task_count"] = len(tasks)

    return snap


def _describe_event(event: Event) -> str:
    """The event's structured fields as a short list.

    Values are stringified and clipped. This is `CONNECTED_SOURCE`-shaped data —
    the *shape* is ours, the values are theirs — so it is fenced along with
    everything else from that source rather than being treated as safe because
    we chose the keys.
    """
    skip = {"body", "text", "content", "message", "description"}
    lines = []
    for key, value in (event.data or {}).items():
        if key in skip or value in (None, "", [], {}):
            continue
        rendered = ", ".join(str(v) for v in value) if isinstance(value, list) \
            else str(value)
        lines.append(f"- {key}: {rendered[:200]}")
    return "\n".join(lines[:20])
