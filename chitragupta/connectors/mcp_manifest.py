"""What a connector can do, as something a person can read.

A server answers `tools/list` with a flat array — GitHub's is 45 long, Notion's
is 45 — and every screen built straight on top of that says *"this connector
has 45 tools"*. That is a count, not a decision. Nobody can consent to a count.

The question a person is actually answering when they add a connector is
**what can this reach, and what can it change**, and everything needed to say
so is already in what the server published. This module turns the array into
that answer:

    Notion — reads 27 things, changes 16, and 2 need care every time.

Eight facts, because eight is what a person has to weigh. Four come from the
server, four from us, and the split is stated rather than blurred — a rate
limit we impose is not a promise the vendor made, and reading it as one is how
a manifest starts lying.

| from the server | what it publishes                                 |
|-----------------|---------------------------------------------------|
| reads           | tools that only answer questions                  |
| changes         | tools that alter something at the vendor          |
| needs care      | changes with no inverse — `is_irreversible`       |
| authentication  | how it signs in, and whether it has               |

| from us         | what this app decides                             |
|-----------------|---------------------------------------------------|
| permitted       | which of those tools the user left switched on    |
| bounds          | how much we will read in one pass, and how long   |
| verification    | first-party? version-pinned? answered just now?   |
| route           | the vendor's own endpoint, or a local subprocess  |

**Nothing here is invented.** MCP has no standard for OAuth scopes or rate
limits, so this does not have a field pretending to know them: what a vendor
granted lives on their consent screen, and saying "unknown" in eight places is
worse than not asking. What *is* known — the tools the user permitted here, and
the ceilings this app applies — is reported as ours, in our name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mcp_source import (
    CALL_TIMEOUT_SECONDS,
    MAX_HARVEST_TOOLS,
    MCPServerSpec,
    ToolKinds,
    first_sentence,
    is_irreversible,
    is_server_metadata,
)

#: Records one sync will take from a server, shared across its listings.
#: Mirrors `MCPConnector.sync`'s own default rather than re-deciding it.
SYNC_RECORD_BUDGET = 200


@dataclass(frozen=True)
class Capability:
    """One thing a connector can do, named the way a person would say it."""

    tool: str
    #: "read" · "change" · "care" — what this does, not what it is called.
    kind: str
    summary: str
    #: Is it switched on for this connector right now?
    permitted: bool
    #: Arguments the server says it cannot run without.
    needs: list[str] = field(default_factory=list)

    @property
    def is_furniture(self) -> bool:
        """A listing of the server's own configuration rather than the user's
        content. Counted separately so "reads 27 things" means 27 things that
        are *theirs*."""
        return is_server_metadata(self.tool)


@dataclass(frozen=True)
class Manifest:
    """Everything a person needs to decide about one connector."""

    server_id: str
    name: str
    #: From the server.
    reads: list[Capability] = field(default_factory=list)
    changes: list[Capability] = field(default_factory=list)
    needs_care: list[Capability] = field(default_factory=list)
    auth: str = "none"
    signed_in: bool = False
    #: From us.
    restricted: bool = False
    bounds: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    remote: bool = True

    @property
    def headline(self) -> str:
        """The sentence the whole module exists for.

        Furniture is excluded from the count: a server that lists its own
        templates and labels does not "read 31 things about you", and a number
        inflated by its own configuration is the count all over again.
        """
        real = [c for c in self.reads if not c.is_furniture]
        parts = [f"reads {len(real)} thing{'' if len(real) == 1 else 's'}"]
        if self.changes:
            parts.append(f"changes {len(self.changes)}")
        if self.needs_care:
            parts.append(f"{len(self.needs_care)} need"
                         f"{'s' if len(self.needs_care) == 1 else ''} care")
        if not self.changes and not self.needs_care:
            parts.append("changes nothing")
        return f"{self.name} " + ", ".join(parts) + "."

    def as_dict(self) -> dict[str, Any]:
        def rows(caps: list[Capability]) -> list[dict[str, Any]]:
            return [{"tool": c.tool, "kind": c.kind, "summary": c.summary,
                     "permitted": c.permitted, "needs": list(c.needs),
                     "furniture": c.is_furniture} for c in caps]

        return {
            "server_id": self.server_id, "name": self.name,
            "headline": self.headline,
            "reads": rows(self.reads), "changes": rows(self.changes),
            "needs_care": rows(self.needs_care),
            "auth": self.auth, "signed_in": self.signed_in,
            "restricted": self.restricted, "remote": self.remote,
            "bounds": dict(self.bounds), "verification": dict(self.verification),
        }


def _summary(tool: Any) -> str:
    """The server's own first sentence about a tool, or nothing.

    Never invented. A description we wrote for somebody else's verb would read
    as authoritative and be a guess.
    """
    return first_sentence(getattr(tool, "description", "") or "")[:160]


def _needs(tool: Any) -> list[str]:
    raw = (getattr(tool, "input_schema", None)
           or getattr(tool, "inputSchema", None))
    required = raw.get("required") if isinstance(raw, dict) else None
    return [str(n) for n in required] if isinstance(required, list) else []


def manifest_of(spec: MCPServerSpec, kinds: ToolKinds, *,
                signed_in: bool = True,
                verification: dict[str, Any] | None = None) -> Manifest:
    """Turn one server's published tool list into a manifest.

    `kinds` is passed in rather than probed for: the caller has just started
    the server to get it, and starting it twice to describe it once is the
    thing `mcp_tools`' TTL cache exists to avoid.
    """
    by_name = {getattr(t, "name", ""): t for t in kinds.tools}
    reads: list[Capability] = []
    changes: list[Capability] = []
    care: list[Capability] = []

    for name in [*kinds.readable, *kinds.write]:
        tool = by_name.get(name)
        writes = name in kinds.write
        # **Irreversible is its own tier, not a louder shade of "changes".**
        # `permissions` already refuses to allow-list these; a manifest that
        # buried them among ordinary writes would describe a gentler app than
        # the one that ships.
        kind = ("care" if writes and is_irreversible(name)
                else "change" if writes else "read")
        cap = Capability(tool=name, kind=kind, summary=_summary(tool),
                         permitted=spec.permits(name), needs=_needs(tool))
        (care if kind == "care" else changes if writes else reads).append(cap)

    return Manifest(
        server_id=spec.id, name=spec.name or spec.id,
        reads=reads, changes=changes, needs_care=care,
        auth=spec.auth, signed_in=signed_in,
        # The one scope this app can honestly report: not what the vendor
        # granted — that lives on their consent screen and is invisible here —
        # but which of these tools the user left switched on.
        restricted=bool(spec.allowed_tools),
        remote=spec.is_remote,
        bounds={
            "records_per_sync": SYNC_RECORD_BUDGET,
            "listings_per_sync": MAX_HARVEST_TOOLS,
            "seconds_per_call": int(CALL_TIMEOUT_SECONDS),
            # Said in our name. A ceiling this app applies is not a promise the
            # vendor made, and a manifest that blurs the two is one that will
            # eventually be quoted back as the vendor's.
            "imposed_by": "Chitragupta",
        },
        verification=dict(verification or {}),
    )
