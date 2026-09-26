"""What a connector can do, in a vocabulary the permission layer can reason in.

A connector used to describe itself with four booleans — `auto_sync`,
`incremental`, `runs_on_device`, `platforms` — every one of them about
*mechanics*. Nothing anywhere said that `gmail` can send an email and cannot
delete one, so the only way to find out what a connector's write surface was
was to read its methods and hope `actions.py` had found the same ones.

A capability is a **verb on a resource**, never an app name:

    read:email      create:draft     send:email
    read:event      create:event     delete:event
    read:repository create:issue     merge:pull_request

`send:email` means the same thing whether Gmail, Apple Mail or somebody else's
MCP server is behind it, which is the whole point — the gate can decide about
it without knowing which. *"Gmail"* is not a capability; it is an app, and an
app is not something a person can consent to.

**Unknown fails closed.** `parse()` on a string nobody recognises raises. It is
tempting to read an unparseable capability as a read — it is the harmless one —
and that is exactly the mistake: a connector that can name its own capabilities
could then name an arbitrary write in a way that classifies as harmless.

## The four tiers, and why four

`Access` answers what *kind* of permission this needs, and the tiers are the
four genuinely different answers:

* `READ` — answers a question. Reaches nobody, changes nothing.
* `WRITE` — changes something at the vendor, and the change has an inverse.
* `OUTBOUND` — reaches a person who is not the user.
* `DESTRUCTIVE` — no inverse exists.

`OUTBOUND` is separate from `WRITE` because the product already treats them
differently and is right to: `create_draft` writes to the user's own mailbox and
reaches nobody, so it is GREEN and an unattended agent may prepare one
overnight; `send_email` reaches a person, so it is AMBER and needs a permitted
recipient. Collapsing the two would make the draft need an approval it does not
need, or the send skip one it does.

## This is a ceiling, not the gate

The gate is still `agents/permissions.check()` reading `ActionSpec.risk`, and
that does not change. What this adds is a **floor underneath it**: an action
that declares `delete:event` may not be registered at GREEN. One test asserts
it (`tests/connectors/test_capability_floor.py`), which is what stops a write
being quietly registered as something harmless.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Verb(StrEnum):
    """What is being done. Closed set: a verb nobody recognises is refused."""

    READ = "read"
    SEARCH = "search"
    DOWNLOAD = "download"

    CREATE = "create"
    UPDATE = "update"
    UPLOAD = "upload"
    #: Move out of view without destroying — archiving mail, trashing a file.
    #: A write rather than destructive because every one of them here has an
    #: inverse the product actually implements (`_undo_triage`, `_undo_drive_doc`).
    ARCHIVE = "archive"
    UNSHARE = "unshare"

    #: Reaches a person who is not the user.
    SEND = "send"
    SHARE = "share"
    #: Invites, reschedules, or tells everybody already on something.
    NOTIFY = "notify"

    #: No inverse.
    DELETE = "delete"
    CANCEL = "cancel"
    MERGE = "merge"


class Resource(StrEnum):
    """What is being acted on.

    Provider-shaped rather than lowest-common-denominator: a `pull_request` is
    not a `task`, and flattening them would lose the only thing that makes the
    capability legible to the person granting it.
    """

    EMAIL = "email"
    DRAFT = "draft"
    THREAD = "thread"
    LABEL = "label"

    EVENT = "event"
    CALENDAR = "calendar"

    FILE = "file"
    DOCUMENT = "document"
    FOLDER = "folder"

    MESSAGE = "message"
    CHAT = "chat"
    CHANNEL = "channel"
    CONTACT = "contact"

    REPOSITORY = "repository"
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    COMMENT = "comment"

    PAGE = "page"
    TASK = "task"
    NOTE = "note"

    #: A number over time. Kept distinct because `metrics.py` owns these and
    #: the brain deliberately does not — see `/CLAUDE.md`, Measurements.
    MEASUREMENT = "measurement"

    #: Something a server returned that we can classify by tier but not by
    #: kind. An MCP server publishes `merge_pull_request` and `create_page`
    #: from one endpoint; we know which tier each is and genuinely do not know
    #: what the second one *is*. Saying `record` is honest; guessing is not.
    RECORD = "record"

    USER = "user"


class Access(StrEnum):
    """How much permission a capability needs. Ordered: later is stronger."""

    READ = "read"
    WRITE = "write"
    OUTBOUND = "outbound"
    DESTRUCTIVE = "destructive"


#: Strength order, so "at least this tier" is a comparison rather than a set of
#: hand-written cases that has to be updated in two places.
_STRENGTH: dict[Access, int] = {
    Access.READ: 0,
    Access.WRITE: 1,
    Access.OUTBOUND: 2,
    Access.DESTRUCTIVE: 3,
}


def at_least(tier: Access, floor: Access) -> bool:
    """Is `tier` at or above `floor`?"""
    return _STRENGTH[tier] >= _STRENGTH[floor]


#: Verb → the tier it needs. Exhaustive over `Verb` by construction, and
#: `test_every_verb_has_a_tier` fails if a verb is added without one — the
#: alternative is a `.get(verb, Access.READ)` that classifies a new destructive
#: verb as harmless on the day somebody adds it.
_TIER: dict[Verb, Access] = {
    Verb.READ: Access.READ,
    Verb.SEARCH: Access.READ,
    Verb.DOWNLOAD: Access.READ,

    Verb.CREATE: Access.WRITE,
    Verb.UPDATE: Access.WRITE,
    Verb.UPLOAD: Access.WRITE,
    Verb.ARCHIVE: Access.WRITE,
    Verb.UNSHARE: Access.WRITE,

    Verb.SEND: Access.OUTBOUND,
    Verb.SHARE: Access.OUTBOUND,
    Verb.NOTIFY: Access.OUTBOUND,

    Verb.DELETE: Access.DESTRUCTIVE,
    Verb.CANCEL: Access.DESTRUCTIVE,
    Verb.MERGE: Access.DESTRUCTIVE,
}


class UnknownCapabilityError(ValueError):
    """A capability string nobody recognises.

    Raised rather than resolved to a default, because every available default
    is wrong: reading it as `read` lets an unrecognised write through as
    harmless, and reading it as `destructive` means one typo in a manifest
    takes a working connector offline with an error about a feature nobody
    asked for.
    """


_SPELLING = re.compile(r"^([a-z_]+):([a-z_]+)$")


@dataclass(frozen=True, order=True)
class Capability:
    """One thing a connector can do."""

    verb: Verb
    resource: Resource

    def __str__(self) -> str:
        return f"{self.verb.value}:{self.resource.value}"

    @property
    def access(self) -> Access:
        return _TIER[self.verb]

    @property
    def reads_only(self) -> bool:
        return self.access is Access.READ

    @property
    def is_destructive(self) -> bool:
        return self.access is Access.DESTRUCTIVE

    @property
    def reaches_someone(self) -> bool:
        """Does doing this put something in front of a person who is not the
        user? The question `agents/permissions.py` asks about a recipient."""
        return self.access is Access.OUTBOUND

    def as_dict(self) -> dict[str, str]:
        return {"capability": str(self), "verb": self.verb.value,
                "resource": self.resource.value, "access": self.access.value}


def parse(raw: str) -> Capability:
    """`"send:email"` → `Capability(Verb.SEND, Resource.EMAIL)`.

    Fails closed on anything else. See `UnknownCapabilityError`.
    """
    match = _SPELLING.match((raw or "").strip().lower())
    if not match:
        raise UnknownCapabilityError(
            f"{raw!r} is not a capability — expected 'verb:resource'")
    verb_raw, resource_raw = match.groups()
    try:
        verb = Verb(verb_raw)
    except ValueError:
        raise UnknownCapabilityError(f"unknown verb {verb_raw!r} in {raw!r}") from None
    try:
        resource = Resource(resource_raw)
    except ValueError:
        raise UnknownCapabilityError(
            f"unknown resource {resource_raw!r} in {raw!r}") from None
    return Capability(verb, resource)


def parse_all(raws: object) -> frozenset[Capability]:
    """Every capability in an iterable of strings, or nothing at all.

    All-or-nothing on purpose: a partially-parsed set would silently narrow a
    connector's declared surface, and a connector whose `send:email` was
    dropped by a typo reads as one that cannot send — which is a control that
    stops working with no error.
    """
    if isinstance(raws, str):
        raise UnknownCapabilityError(
            "a capability set is a list of strings, not one string")
    try:
        items = list(raws)  # type: ignore[call-overload]
    except TypeError:
        raise UnknownCapabilityError(f"{raws!r} is not a list of capabilities") from None
    return frozenset(parse(item) for item in items)


def strongest(caps: object) -> Access:
    """The strongest tier in a set — what the *connector* can do at worst.

    An empty set is `READ`: a connector that declares no capability cannot do
    anything, and "cannot do anything" is not stronger than reading.
    """
    tiers = [c.access for c in caps]  # type: ignore[attr-defined]
    return max(tiers, key=lambda t: _STRENGTH[t]) if tiers else Access.READ


def writes_in(caps: object) -> frozenset[Capability]:
    """Everything in `caps` that changes something. The set a user is
    consenting to when they connect a source, as opposed to the whole list."""
    return frozenset(c for c in caps if not c.reads_only)  # type: ignore[attr-defined]


#: Shorthand so a connector declaration reads like a sentence:
#:     capabilities = caps("read:email", "search:email", "send:email")
def caps(*raws: str) -> frozenset[Capability]:
    return parse_all(raws)
