"""What may leave this machine, and what may never.

An archive of the user's brain is the first thing Chitragupta has ever built
that *leaves the machine* — onto another disk, another Mac, or (later) storage
somebody else hosts. That makes "which files may be copied out" a rule, and a
rule written twice is a rule that drifts.

**It lives in `core/` for the same reason `redact.py` does.** `redact` is a rule
about what may be *written to* the database; this is a rule about what may be
*read out of* the home directory. Both are rules about the boundary of the
user's own data, both have to be readable by every layer without importing one,
and neither may depend on anything above `core/`.

Deliberately stdlib only — no settings, no logging. `archive/` is what touches
the disk.

**Unknown fails closed.** A file nobody classified is NOT archived. The
tempting default is the other way round — copy everything, exclude the secrets
we thought of — and it is wrong for the one case that matters: a credential
written by a connector built after this module. `connectors/telegram_auth.py`
already writes an MTProto session that is *full access to the user's Telegram
account*, and `browser/chromium.py` keeps a profile with live cookies for every
site the user signed into. The next connector will write something just as
dangerous, and it must be invisible here until somebody declares it.

**Databases are the one pattern-based exception**, because the opposite failure
is also real: the shipped export covered memories and nothing else, so custom
agents, permissions, automations and a year of health measurements were lost on
a new Mac. A `*.db` in the home is ours, and none of them hold a credential —
verified: `connector_connections` stores `auth_state` and `scopes`,
`provider_connections` stores a `credential_reference`, and the secrets
themselves live in the macOS Keychain. So a database added later is archived
automatically, and `tests/test_exclusions.py` pins the known set so a *new* one
is a deliberate decision rather than a silent inclusion.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

#: Tier 0 — never archived, never uploaded, not even encrypted. Each maps to
#: why, because a refusal the user cannot understand reads as a bug.
#:
#: The cost of this list is the reconnect step after a restore: one OAuth tap
#: per connector, and provider keys re-entered. That is deliberate. The
#: alternative is an archive that, if it ever leaked, would hand someone else
#: live access to the user's mail, files, calendar and chats.
NEVER_ARCHIVE: dict[str, str] = {
    "secrets.json": "connector and provider secrets",
    "google_token.json": "Google OAuth refresh token (Gmail, Drive, Calendar)",
    "google_client_secret.json": "Google OAuth client secret",
    "google_account.json": "Google account identity from the OAuth flow",
    "chatgpt_token.json": "ChatGPT session",
    "xai_token.json": "xAI session",
    "telegram": "Telegram MTProto session — full access to the account",
    "browser": "browser profile: live cookies for every connected site",
}

#: Derived or machine-local. Excluded because restoring them is wrong or
#: pointless, not because they are dangerous.
DERIVED: dict[str, str] = {
    ".port": "the saved webview port — user state bound to THIS machine",
    "logs": "logs",
    "brain-export": "a projection of the brain, regenerated on demand",
    "agent-workspace": "scratch space for the vendor CLIs",
    "agent-binaries": "downloaded vendor CLIs, re-fetched on demand",
    "bin": "downloaded vendor CLIs, re-fetched on demand",
}

#: Archived by pattern. Databases only — see the module docstring.
ARCHIVED_PATTERNS: tuple[str, ...] = ("*.db",)

#: SQLite's sidecars, matched by pattern because their names follow the
#: database's. **Derived, and dangerous to carry either way.**
#:
#: Every database here runs in WAL mode (`core/db.py`: `PRAGMA
#: journal_mode=WAL`), so a live home always has `<name>.db-wal` and
#: `<name>.db-shm` beside each file. They must not be archived — `VACUUM INTO`
#: already produces a fully checkpointed standalone database, so the snapshot
#: is complete without them — and they must not be left in place across a
#: restore either.
#:
#: That second half was a shipped bug, found by running the real server rather
#: than a fixture: a restore replaced `chitragupta.db` and left the old
#: `-wal` next to it, and SQLite then applied the stale log on the next open.
#: Measured — a fresh read after the restore returned **the old rows**. The
#: app said "Restored", the user still had their old brain, and nothing
#: anywhere reported a failure. `reader._place` now clears them.
SIDECAR_PATTERNS: tuple[str, ...] = ("*.db-wal", "*.db-shm", "*.db-journal")

#: Archived by name. Each one is user configuration worth carrying to a new
#: Mac, and each needs a declared sanitiser below if it can hold a credential.
ARCHIVED_FILES: dict[str, str] = {
    "mcp_servers.json": "which MCP servers the user configured",
    # The account record: its id, when it was made, and which sign-ins are
    # linked to it. **No token** — those are in the Keychain and are Tier 0 —
    # so restoring it means a new Mac is still the same account and the user
    # signs in again rather than starting over. See `account/store.py`.
    "account.json": "your Chitragupta account and its linked sign-ins",
}

#: Archived directories — user content an agent wrote, not our scratch space.
ARCHIVED_DIRS: dict[str, str] = {
    "agents": "agent profile folders and their files",
}

#: Keys stripped from an archived JSON file before it is packed.
#:
#: `mcp_servers.json` earns its entry: tokens used to be stored inline in it,
#: and `mcp_source._migrated` moves them to the Keychain **on read** — so an
#: install where the user never opened the Connectors screen still has them on
#: disk. Archiving that file verbatim would copy a credential out of a machine.
#: Stripping the key is better than excluding the file, because the rest of it
#: is configuration the user would otherwise have to rebuild by hand.
JSON_STRIP_KEYS: dict[str, tuple[str, ...]] = {
    "mcp_servers.json": ("env",),
}

#: The databases this app is known to create. Not used to decide anything —
#: `ARCHIVED_PATTERNS` does that — but pinned by a test, so adding a database
#: without thinking about recovery fails rather than passing quietly.
KNOWN_DATABASES: frozenset[str] = frozenset({
    "chitragupta.db",   # memories, entities, relations, open_loops, meta
    "brain.db",         # canonical: claims, evidence, candidates, events
    "agents.db",        # agents, personas, avatars, grants, permissions, cards
    "actions.db",       # the action log
    "tasks.db",         # tasks
    "metrics.db",       # measurements and lifts
    "reminders.db",     # reminders and scheduled actions
    "messages.db",      # inter-agent messages
    "connectors.db",    # connections, sync state, events, resources
    "routines.db",      # routines
    "automation.db",    # automation runs, steps, claims, seen events
})

ARCHIVE = "archive"
REFUSE = "refuse"
SKIP = "skip"


@dataclass(frozen=True)
class Verdict:
    """What to do with one entry in the home directory, and why.

    `reason` is written for a person: it is what the reconnect checklist and
    the "what is in my backup" screen both show.
    """
    action: str          # ARCHIVE | REFUSE | SKIP
    reason: str

    @property
    def archived(self) -> bool:
        return self.action == ARCHIVE

    @property
    def refused(self) -> bool:
        """Tier 0 — withheld because exporting it would be dangerous."""
        return self.action == REFUSE


def classify(name: str, *, is_dir: bool = False) -> Verdict:
    """Decide the fate of one entry in the Chitragupta home.

    `name` is a single path component, never a path — this module does not know
    where the home is and must not learn.

    Order matters: Tier 0 is checked first, so a credential file can never be
    rescued into an archive by also matching a pattern.
    """
    if name in NEVER_ARCHIVE:
        return Verdict(REFUSE, NEVER_ARCHIVE[name])
    if name in DERIVED:
        return Verdict(SKIP, DERIVED[name])
    if is_sidecar(name):
        return Verdict(SKIP, "a SQLite write-ahead log — rebuilt, never copied")

    if is_dir:
        if name in ARCHIVED_DIRS:
            return Verdict(ARCHIVE, ARCHIVED_DIRS[name])
        return Verdict(SKIP, "an unrecognised directory — not archived")

    if name in ARCHIVED_FILES:
        return Verdict(ARCHIVE, ARCHIVED_FILES[name])
    if any(fnmatch(name, pat) for pat in ARCHIVED_PATTERNS):
        return Verdict(ARCHIVE, "a Chitragupta database")

    # Unknown fails closed. See the module docstring: the file this branch
    # exists for is the credential written by a connector built after it.
    return Verdict(SKIP, "an unrecognised file — not archived")


def is_sidecar(name: str) -> bool:
    """Is this one of SQLite's `-wal` / `-shm` / `-journal` companions?

    Used twice, which is why it is a function: to keep them out of an archive,
    and to clear a stale one out of the way on restore. See
    `SIDECAR_PATTERNS` for what leaving one behind cost.
    """
    return any(fnmatch(name, pat) for pat in SIDECAR_PATTERNS)


def strip_keys_for(name: str) -> tuple[str, ...]:
    """Top-level JSON keys to remove from `name` before packing it."""
    return JSON_STRIP_KEYS.get(name, ())


def refusals() -> dict[str, str]:
    """Everything withheld from an archive, as {name: why}.

    The restore flow turns this into the reconnect checklist, so the user is
    told what they have to re-authorise instead of discovering it when an
    agent fails.
    """
    return dict(NEVER_ARCHIVE)
