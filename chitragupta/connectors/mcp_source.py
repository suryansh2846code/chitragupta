"""Connectors backed by an MCP server.

This is the answer to breadth. Writing a connector by hand is right for the six
sources the brain is actually built from and hopeless for the long tail, and the
alternative everyone reaches for — a hosted broker like Composio — is the exact
trade this product exists to refuse: it puts a third party's cloud between the
user's mail and the user's machine, and its consent screen carries the broker's
name instead of the vendor's. Turnstone made that trade; `TURNSTONE-TEARDOWN.md`
records what it cost them.

A local MCP server has none of that. It is a subprocess. It talks to its own
vendor over the user's own credential, the data lands on the user's disk, and
the sign-in it runs is the vendor's own — which is why **we register no OAuth
client and need no verification**, the same reason a "Sign in with Claude"
button works through the Claude CLI.

To the user this is a connector. The acronym never reaches the UI, exactly as
"vendor CLI" never reaches the sign-in card.

**What this deliberately does not promise.** MCP tools are shaped to answer one
question for a model, not to page a mailbox into a database. A server exposing
`list_messages` can seed a brain; one exposing only `search_messages` cannot,
and must say so rather than sync nothing and report success. `classify_tools()`
is that distinction, and it is drawn from the one signal that actually means it:
whether a tool can be called with no arguments.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..log import get_logger, suppressed
from .base import Connector, SyncResult

log = get_logger(__name__)

#: How long any single MCP conversation may take. A server that hangs must not
#: hang the sync — the thread it runs on is one the UI also needs.
#: How many of a server's listings one sync will read. A bound rather than a
#: preference: each is a round trip, and a server with forty of them would
#: turn one sync into a minutes-long conversation.
MAX_HARVEST_TOOLS = 12

CALL_TIMEOUT_SECONDS = 45.0
LIST_TIMEOUT_SECONDS = 20.0

#: A sign-in the user is actively completing gets the whole consent window, not
#: the twenty seconds a background probe gets. Kept in step with
#: `mcp_auth.CONSENT_TIMEOUT_SECONDS`, which is what the waiter uses.
CONSENT_TIMEOUT_SECONDS = 310.0

#: Tool-name stems that mean "give me the records". Checked as a fallback to
#: the argument test below, never instead of it: a name is a hint, a required
#: `query` parameter is proof.
_BULK_HINTS = ("list", "recent", "all", "fetch", "export", "read", "get_many",
               "history", "items", "entries")

#: Stems that mean "this only looks". Used to decide a tool is a **read** —
#: which is the direction that now needs evidence, see `_is_write`.
_READ_STEMS = ("list", "get", "read", "search", "find", "query", "fetch",
               "view", "show", "describe", "lookup", "recent", "history",
               "export", "count", "summar", "stat", "info", "detail",
               "browse", "diff", "resolve", "check", "inspect", "whoami",
               # Asking is not acting. `ask_question` on a documentation server
               # landed in the write pile, which would have put an approval
               # card in front of every question a reference connector exists
               # to answer. Each of these produces an answer and changes
               # nothing — kept deliberately short, because the point of
               # failing closed is lost if it grows to cover every verb.
               "ask", "explain", "analyz", "analys", "compare", "suggest",
               "recommend", "estimate", "calculate", "translate", "preview")

#: Words a tool named for its *contents* rather than its verb is made of —
#: `entries`, `recent_items`, `my_issues`. A name built only from these has no
#: verb in it at all, and a tool with no verb does not act on anything.
#:
#: Matched as whole underscore-separated segments, never as substrings. A
#: substring test put `clear_all` in the read pile, because "all" is in it.
_LISTING_WORDS = frozenset((
    "entries", "items", "records", "messages", "results", "rows", "values",
    "history", "recent", "feed", "inbox", "threads", "events", "documents",
    "docs", "pages", "issues", "channels", "files", "folders", "notes",
    "tasks", "projects", "repos", "repositories", "users", "members",
    "my", "all", "mine", "open", "active", "current", "latest",
))

#: Arguments a listing tool may require without ceasing to be a listing tool.
_PAGING_ARGS = {"cursor", "page", "page_token", "offset", "start", "limit",
                "count", "max_results", "per_page", "page_size", "after"}


def secret_key(server_id: str, var: str) -> str:
    """Where one server's one environment value is kept.

    The same shape `custom_api.py` uses for its token, for the same reason: a
    connector's credential belongs in the Keychain, not in a JSON file that a
    backup or a synced home directory will happily carry off the machine.
    """
    return f"mcp:{server_id}:{var}"


@dataclass
class MCPServerSpec:
    """How to reach one server, and what to read out of it.

    A server is either a **local subprocess** (`transport="stdio"`) or the
    **vendor's own endpoint** (`transport="http"`). The second is not a step
    away from local-first: the data still travels vendor → this machine with
    nothing in between, and unlike a community npm package it does not run
    third-party code as the user with access to their tokens.
    """

    id: str
    name: str
    #: "stdio" — we launch it. "http" — the vendor already runs it.
    transport: str = "stdio"
    #: stdio only.
    command: str = ""
    args: list[str] = field(default_factory=list)
    #: http only. The MCP endpoint, e.g. https://mcp.example.com/mcp
    url: str = ""
    #: How a remote server is authenticated.
    #:   "oauth" — the vendor's own consent screen, registered dynamically.
    #:   "token" — a key the user pastes, sent as `Authorization: Bearer …`.
    #:   "none"  — open endpoint.
    #: Two mechanisms rather than one because the vendors genuinely split:
    #: Linear, Notion and Sentry run a full OAuth server; GitHub's takes the
    #: personal access token the user already has.
    auth: str = "oauth"
    #: For `auth="token"`, which stored value carries the key.
    token_key: str = ""
    #: **Names** of the environment variables this server needs. The values are
    #: in the Keychain under `secret_key()`; this list is what the UI asks for
    #: and what `resolved_env()` looks up. Nothing secret is stored here, which
    #: is what makes `as_dict()` safe to return from the API.
    env_keys: list[str] = field(default_factory=list)
    #: Tool to call for records. Empty means "work it out from the server".
    sync_tool: str = ""
    #: Field in each record to use as a title, if the server returns objects.
    title_field: str = ""
    #: Tools this connector may use at all. Empty means "every readable tool".
    #: Least privilege is only real if the user can narrow it, and they can only
    #: narrow what they were shown — `mcp_catalog.describe()` is that list.
    allowed_tools: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> MCPServerSpec:
        # A spec written before secrets moved to the Keychain carries `env`
        # inline. Read it here rather than anywhere else, so exactly one
        # function in the app has to know the old shape existed.
        legacy = dict(raw.get("env") or {})
        keys = list(raw.get("env_keys") or []) or sorted(legacy)
        return cls(
            id=raw["id"], name=raw.get("name") or raw["id"],
            transport=(raw.get("transport") or "stdio").strip().lower(),
            command=raw.get("command") or "",
            args=list(raw.get("args") or []),
            url=raw.get("url") or "",
            auth=(raw.get("auth") or "oauth").strip().lower(),
            token_key=raw.get("token_key") or "",
            env_keys=keys,
            sync_tool=raw.get("sync_tool") or "",
            title_field=raw.get("title_field") or "",
            allowed_tools=list(raw.get("allowed_tools") or []),
        )

    def as_dict(self) -> dict[str, Any]:
        """The spec as stored and as served. Carries no secret, by construction."""
        return {"id": self.id, "name": self.name, "transport": self.transport,
                "command": self.command, "args": self.args, "url": self.url,
                "auth": self.auth, "token_key": self.token_key,
                "env_keys": self.env_keys, "sync_tool": self.sync_tool,
                "title_field": self.title_field,
                "allowed_tools": self.allowed_tools}

    @property
    def is_remote(self) -> bool:
        return self.transport == "http"

    @property
    def uses_oauth(self) -> bool:
        return self.is_remote and self.auth == "oauth"

    def bearer(self) -> str:
        """The pasted key for a `auth="token"` server, from the Keychain."""
        if self.auth != "token":
            return ""
        key = self.token_key or (self.env_keys[0] if self.env_keys else "")
        if not key:
            return ""
        return get_settings().get_secret(secret_key(self.id, key)) or ""

    def permits(self, tool: str) -> bool:
        """May this connector use that tool? Empty allow-list means anything."""
        return not self.allowed_tools or tool in self.allowed_tools

    def resolved_env(self) -> dict[str, str]:
        """This server's environment, read from the Keychain at spawn time.

        A name with no stored value is left out rather than passed as an empty
        string: a server told its token is `""` reports an auth error, and a
        server told nothing reports that it needs one.
        """
        out: dict[str, str] = {}
        for var in self.env_keys:
            value = get_settings().get_secret(secret_key(self.id, var))
            if value:
                out[var] = value
        return out

    def missing_env(self) -> list[str]:
        """Names this server needs that have no value stored yet."""
        have = self.resolved_env()
        return [v for v in self.env_keys if v not in have]


# ── the spec store ──────────────────────────────────────────────────────────


def _specs_path():
    return get_settings().home / "mcp_servers.json"


def _load() -> dict[str, dict]:
    with suppressed("reading the saved MCP connector list"):
        return json.loads(_specs_path().read_text())
    return {}


def _save(specs: dict[str, dict]) -> None:
    path = _specs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(specs, indent=2))


def _migrated(raw: dict) -> dict:
    """Move a legacy inline `env` into the Keychain, once, on first read.

    The old shape kept tokens in `mcp_servers.json` in plain text, and the same
    file is returned by `GET /api/connectors` — so leaving one in place is both
    a credential on disk and a credential on the wire. Rewriting on read means
    an existing install is repaired by opening Connectors, with nothing for the
    user to do and nothing for them to lose.
    """
    env = dict(raw.get("env") or {})
    if not env:
        return raw
    settings = get_settings()
    for var, value in env.items():
        if value:
            settings.set_secret(secret_key(raw["id"], var), value)
    cleaned = dict(raw)
    cleaned.pop("env", None)
    cleaned["env_keys"] = sorted(env)
    log.info("moved %d stored value(s) for MCP connector %s into the Keychain",
             len(env), raw["id"])
    return cleaned


def list_servers() -> list[MCPServerSpec]:
    specs = _load()
    migrated = {k: _migrated(v) for k, v in specs.items()}
    if migrated != specs:
        _save(migrated)
    return [MCPServerSpec.from_dict(v) for v in migrated.values()]


def get_server(server_id: str) -> MCPServerSpec | None:
    specs = _load()
    raw = specs.get(server_id)
    if not raw:
        return None
    cleaned = _migrated(raw)
    if cleaned != raw:
        specs[server_id] = cleaned
        _save(specs)
    return MCPServerSpec.from_dict(cleaned)


def set_server_env(server_id: str, env: dict[str, str]) -> None:
    """Store this server's environment values, and remember their names.

    Values go to the Keychain; only the names reach the spec file. A blank
    value clears the stored one rather than saving an empty string.
    """
    settings = get_settings()
    for var, value in (env or {}).items():
        settings.set_secret(secret_key(server_id, var), (value or "").strip() or None)
    specs = _load()
    raw = specs.get(server_id)
    if raw is None:
        return
    names = sorted(set(raw.get("env_keys") or []) | set(env or {}))
    raw["env_keys"] = names
    raw.pop("env", None)
    specs[server_id] = raw
    _save(specs)


def _forget_tools() -> None:
    """Drop the agent-facing tool cache after the saved servers change.

    `mcp_tools.list_tools()` is TTL-cached because the agent loop asks on every
    turn, so without this a server the user just added stays invisible — and a
    tool they just disallowed stays callable — for the length of the TTL. Late
    import: `mcp_tools` is built on this module.
    """
    with suppressed("flushing the MCP tool cache"):
        from .mcp_tools import invalidate

        invalidate()


def upsert_server(spec: MCPServerSpec) -> MCPServerSpec:
    specs = _load()
    specs[spec.id] = spec.as_dict()
    _save(specs)
    _forget_tools()
    return spec


def delete_server(server_id: str) -> bool:
    """Forget a server, and every credential it was given.

    Leaving the Keychain entries behind would mean re-adding a connector
    silently reusing a token the user believed they had removed.
    """
    specs = _load()
    if server_id not in specs:
        return False
    spec = MCPServerSpec.from_dict(specs[server_id])
    settings = get_settings()
    for var in spec.env_keys:
        with suppressed("clearing a removed connector's stored value"):
            settings.set_secret(secret_key(server_id, var), None)
    with suppressed("clearing a removed connector's sign-in"):
        from .mcp_auth import forget_tokens

        forget_tokens(server_id)
    del specs[server_id]
    _save(specs)
    _forget_tools()
    return True


# ── what a server can actually do ───────────────────────────────────────────


@dataclass
class ToolKinds:
    """What the tools a server exposes are good for.

    `bulk` can seed a brain; `query` can only answer a question asked of it;
    `write` changes something at the vendor and is never called by a sync.
    """

    bulk: list[str] = field(default_factory=list)
    query: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)
    #: The server's tool objects as published, kept so a caller that needs a
    #: description or an argument schema does not have to ask a second time.
    #: A confirmation card and a model proposing an action both need them.
    tools: list[Any] = field(default_factory=list)

    @property
    def can_sync(self) -> bool:
        return bool(self.bulk)

    @property
    def readable(self) -> list[str]:
        """Everything an agent may call without asking. Reads, in both shapes."""
        return [*self.bulk, *self.query]

    def kind_of(self, tool: str) -> str:
        """`"bulk"` | `"query"` | `"write"` | `""` — what this server calls it."""
        if tool in self.write:
            return "write"
        if tool in self.bulk:
            return "bulk"
        if tool in self.query:
            return "query"
        return ""

    def why_not(self, label: str) -> str:
        """The sentence shown where the user is looking when a sync is not on."""
        if self.query:
            return (f"{label} can answer questions but cannot list its records, "
                    "so it is searched on demand instead of being synced.")
        return f"{label} exposes no readable tools."


#: Bulk tools that list the SERVER, not the user.
#:
#: A server's own configuration is listable in exactly the way its content is,
#: and both answer `list_*`. Ingested, it fills the brain with tool manifests
#: and label palettes — which recall then has to rank against the user's real
#: work, forever.
#:
#: Matched on whole words in the tool's own name, so `list_issue_labels` is
#: skipped and `list_issues` is not. This is a shape, not a vendor list: no
#: entry here names Notion or Linear, and a server nobody has seen gets the
#: same treatment.
_SERVER_METADATA = frozenset({
    "skill", "skills", "template", "templates", "label", "labels",
    "pipeline", "pipelines", "status", "statuses", "access", "tool", "tools",
    "diff", "diffs", "session", "sessions", "agent", "agents",
})


#: Lines that are structure rather than description. A vendor writes its tool
#: docs in markdown, and the first line is very often a heading.
NOT_PROSE = ("#", "---", "===", "```", "|", "* ", "- ")


def first_sentence(text: str) -> str:
    """The first line of a tool's description that actually says something.

    This used to be `split("\n")[0]`, and every one of Notion's write tools
    opens with `## Overview` — so each was described as "## Overview", which
    looks like a description and carries nothing. A model given a tool's name
    and no working description has to invent how to call it, and that is
    exactly what happened: it reached for `in_trash`, which is real in Notion's
    web API and is not a parameter of that tool.

    Lives here rather than in `agents/prompt.py`, where it was written, because
    the manifest needs the same answer and a second copy of this rule would
    drift — the copy that drifts always being the one you are reading. `agents`
    may import `connectors`; the reverse is what the direction forbids.
    """
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(NOT_PROSE):
            continue
        sentence = line.split(". ")[0].strip().rstrip(".")
        if len(sentence) > 4:
            return sentence
    return ""


def is_server_metadata(tool: str) -> bool:
    """Does this tool list the server's own furniture rather than the user's?"""
    head = (tool or "").lower().split("__")[-1]
    words = {w for w in re.split(r"[^a-z0-9]+", head) if w}
    return bool(words & _SERVER_METADATA)


def _required(schema: dict | None) -> set[str]:
    return set((schema or {}).get("required") or [])


def _is_write(name: str, annotations: Any, schema: dict | None = None) -> bool:
    """Is this tool allowed to run without the user being asked first?

    **A tool is a write unless it proves otherwise.** This used to run the other
    way — a tool was a read unless its name contained one of fourteen stems —
    and that is a denylist guarding the one path in the app that changes
    somebody else's account. `merge_pull_request`, `execute_sql`,
    `revoke_token`, `approve`, `invite`, `publish` and `rename` all match none
    of those stems, so all of them were handed to a model as reads and callable
    mid-turn, on text a stranger wrote into the user's mailbox.

    The order of evidence, strongest first:

    1. The server's own `readOnlyHint` / `destructiveHint`. A declaration beats
       any guess we could make about a name we did not choose.
    2. A recognised read stem (`list_`, `search_`, `get_`…). A vendor naming a
       mutation `search_` is possible; a denylist missing a verb nobody thought
       of is routine.
    3. Otherwise: a write. It collects an approval card.

    The cost is that an unannotated, oddly-named read needs one tap. That is the
    direction `/CLAUDE.md` already requires of everything outbound.
    """
    hint = getattr(annotations, "read_only_hint", None)
    if hint is True:
        return False
    if getattr(annotations, "destructive_hint", None) is True:
        return True

    lowered = name.lower()
    # A leading verb is the reliable part of a tool name — `get_issue` reads,
    # `issue_delete` does not read merely because it starts with a noun.
    head = lowered.split("__")[-1]
    if any(head.startswith(stem) for stem in _READ_STEMS):
        return False
    # A listing named for its contents rather than its verb (`entries`,
    # `recent_items`) is still a listing — but only when every word in it names
    # contents, and only when it takes no arguments to act on. Deliberately
    # *not* "no arguments means read": `reset`, `clear_all` and `disconnect`
    # take none either, and each is a destructive call nobody saw coming.
    segments = [seg for seg in re.split(r"[^a-z0-9]+", head) if seg]
    if segments and all(seg in _LISTING_WORDS for seg in segments):
        if schema is None or not (_required(schema) - _PAGING_ARGS):
            return False
    return True


#: Verbs whose effect cannot be taken back, or cannot be seen afterwards.
#:
#: Used for ONE thing: deciding which connector tools a user may put on a
#: standing allow-list. Every write still collects an approval card by default;
#: this only says which ones can never be promoted past that.
#:
#: A denylist, which `_is_write` above argues against — and the difference is
#: what it is guarding. There the denylist was the *only* control, so a verb
#: nobody thought of became a tool the model could call unasked. Here the
#: control is an explicit per-tool grant the user typed a name into; this list
#: only removes the worst verbs from being grantable at all. A verb missing
#: from it is still approved every single time until the user deliberately
#: allows that exact tool on that exact server.
_IRREVERSIBLE_STEMS = (
    "delete", "destroy", "drop", "purge", "remove", "erase", "wipe", "trash",
    "revoke", "disable", "deactivate", "reset", "clear", "truncate",
    "merge", "publish", "deploy", "release", "transfer", "pay", "charge",
    "refund", "cancel", "close", "archive", "ban", "kick", "uninstall",
)


def schema_of(kinds: ToolKinds, tool: str) -> dict | None:
    """The input schema a server published for one of its tools."""
    for published in kinds.tools:
        if getattr(published, "name", "") != tool:
            continue
        raw = (getattr(published, "input_schema", None)
               or getattr(published, "inputSchema", None))
        return raw if isinstance(raw, dict) else None
    return None


def argument_problem(schema: dict | None, arguments: dict) -> str:
    """Why this call cannot work, in the server's own words — or "".

    **Checked here rather than discovered at the vendor.** A confirmation card
    for `notion-update-page` went out with `command: "update_attributes"`, the
    user approved it, and Notion refused: that argument is one of exactly six
    words and `update_attributes` is not among them. Everything needed to know
    that was already in the schema sitting in `ToolKinds.tools`.

    Deliberately narrow. Only two things are checked — a required argument that
    is absent, and a value outside a set the server itself closed — because
    both are facts the server stated, and a home-grown JSON Schema validator
    would start refusing calls that would have worked.
    """
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties")
    props = props if isinstance(props, dict) else {}
    missing = [name for name in (schema.get("required") or [])
               if isinstance(name, str) and name not in (arguments or {})]
    if missing:
        return (f"it needs {_and_list(missing)}, which "
                f"{'was' if len(missing) == 1 else 'were'} not given.")
    for name, value in (arguments or {}).items():
        allowed = (props.get(name) or {}).get("enum") if isinstance(
            props.get(name), dict) else None
        if isinstance(allowed, list) and allowed and value not in allowed:
            choices = _and_list([str(v) for v in allowed], join="or")
            return f"`{name}` has to be {choices} — not `{value}`."
    return ""


def _and_list(items: list[str], join: str = "and") -> str:
    if len(items) == 1:
        return f"`{items[0]}`"
    return ", ".join(f"`{i}`" for i in items[:-1]) + f" {join} `{items[-1]}`"


def is_irreversible(name: str) -> bool:
    """Would allowing this tool once mean allowing something unrecoverable?

    Asked of the *verb*, the same way `_is_write` asks: a leading verb is the
    reliable part of a tool name, and `issue_delete` is not a read merely
    because it begins with a noun — so both ends are checked.
    """
    head = (name or "").lower().split("__")[-1]
    segments = [seg for seg in re.split(r"[^a-z0-9]+", head) if seg]
    return any(seg.startswith(stem)
               for seg in segments for stem in _IRREVERSIBLE_STEMS)


def classify_tools(tools: list[Any]) -> ToolKinds:
    """Sort a server's tools into what a sync may use.

    The test that matters is **can this be called with no arguments** — a tool
    that requires a `query` is a search box, and calling it with a guess would
    invent data rather than read it. Names are a tiebreak, never the rule: a
    server naming its lister `entries` is common, and a server naming a search
    `list_matching` is not rare either.
    """
    kinds = ToolKinds(tools=list(tools))
    for tool in tools:
        name = getattr(tool, "name", "")
        if not name:
            continue
        raw = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
        schema = raw if isinstance(raw, dict) else None
        if _is_write(name, getattr(tool, "annotations", None), schema):
            kinds.write.append(name)
            continue
        required = _required(schema)
        callable_bare = not (required - _PAGING_ARGS)
        if callable_bare:
            # Reaches here only having already been judged a read, so a
            # zero-argument one is a listing: there is nothing else it could be.
            kinds.bulk.append(name)
        else:
            kinds.query.append(name)
    return kinds


# ── talking to a server ─────────────────────────────────────────────────────


#: Where a GUI-launched app has to look for `npx`, `uvx` and friends.
#: A `.app` opened from the Dock inherits `/usr/bin:/bin:/usr/sbin:/sbin` and
#: nothing else, so a perfectly well installed Node is invisible to it — the
#: same stripped-PATH problem `models/claude_code.py` already solves, arriving
#: through a different door. Reusing that helper rather than a second list.
_EXTRA_BIN_DIRS = [
    "/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/local/sbin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".bun" / "bin"),
    str(Path.home() / ".cargo" / "bin"),
    str(Path.home() / ".volta" / "bin"),
    str(Path.home() / ".nvm" / "versions" / "node"),
]


def launch_path() -> str:
    """PATH to look for a server's command on, and to hand the child."""
    from ..models.cli_login import augmented_path

    return augmented_path(_EXTRA_BIN_DIRS)


def resolve_command(command: str) -> str | None:
    """The absolute path to `command`, or None if this Mac does not have it.

    Resolved before spawning rather than left to the child's own lookup, so
    "Node is not installed" and "Node is installed somewhere the Dock cannot
    see" stop being the same error message.
    """
    import shutil

    if not command:
        return None
    if "/" in command:
        return command if Path(command).exists() else None
    found = shutil.which(command, path=launch_path())
    if found:
        return found
    for directory in _EXTRA_BIN_DIRS:
        candidate = Path(directory) / command
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


async def _stdio_session(spec: MCPServerSpec):
    """A local subprocess, launched on a PATH the Dock cannot strip."""
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    resolved = resolve_command(spec.command)
    if resolved is None:
        raise FileNotFoundError(
            f"{spec.command}: command not found")
    env = spec.resolved_env()
    # The SDK merges this over its own safe-to-inherit set, so naming PATH here
    # widens the child's lookup without discarding the rest of the environment.
    env.setdefault("PATH", launch_path())
    return stdio_client(StdioServerParameters(
        command=resolved, args=list(spec.args), env=env))


async def _http_session(spec: MCPServerSpec, *, interactive: bool = False):
    """The vendor's own endpoint, over HTTPS, with the user's own credential.

    No third party sits in this path: it is the same trust boundary the user
    accepted when they made an account with that vendor. Where the vendor runs
    an OAuth server, authentication is OAuth 2.1 with Dynamic Client
    Registration, so **Chitragupta registers no OAuth client** — the property the
    stdio path was chosen for, kept, without running somebody else's code as
    the user. Where the vendor takes a key instead, it is the key the user
    already has, sent as a bearer and stored in the Keychain.
    """
    from mcp.client.streamable_http import (
        create_mcp_http_client,
        streamable_http_client,
    )

    headers: dict[str, str] = {}
    auth = None
    if spec.uses_oauth:
        from .mcp_auth import auth_provider

        auth = auth_provider(spec, interactive=interactive)
    else:
        token = spec.bearer()
        if token:
            headers["Authorization"] = f"Bearer {token}"

    # The transport takes a configured client rather than credentials, so the
    # auth lives on the client we hand it. `create_mcp_http_client` is the
    # SDK's own constructor and carries its timeouts — building a bare
    # `AsyncClient` here would quietly drop the 300s read window a streaming
    # server needs.
    return streamable_http_client(
        spec.url,
        http_client=create_mcp_http_client(headers=headers or None, auth=auth))


async def _converse(spec: MCPServerSpec, action, *, interactive: bool = False):
    """Open a session, hand it to `action`, and always close what we opened.

    `interactive` travels all the way down to the OAuth provider, because the
    decision it controls — may this open a browser? — belongs to the caller at
    the top (a Connect button, or a background probe) and to nothing in
    between.
    """
    from mcp import ClientSession

    opener = (await _http_session(spec, interactive=interactive)
              if spec.is_remote else await _stdio_session(spec))
    async with opener as streams:
        # stdio yields (read, write); the HTTP transport adds a session-id
        # getter as a third member. Taking the first two keeps one call site.
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await action(session)


def converse(spec: MCPServerSpec, action, timeout: float | None = None, *,
             interactive: bool = False):
    """`_converse` from synchronous code, with a ceiling on the whole exchange.

    Connectors run in a worker thread (see `api/concurrency.py`), so there is no
    running loop here to clash with — `anyio.run` owns one for the call and
    tears it down with the subprocess.

    The bound covers spawn, handshake, call **and** teardown, because a server
    that hangs while starting never reaches a call and so is invisible to
    `call_tool`'s `read_timeout_seconds`. Without it, one wedged server holds a
    slot in the six-wide probe lane (`api/concurrency.py`) for the life of the
    process, and six of them freeze sign-in as well. `mcp_tools._talk()` had
    this right one layer up; it lives here now so every caller inherits it.
    """
    import anyio

    ceiling = LIST_TIMEOUT_SECONDS if timeout is None else timeout

    async def guarded():
        with anyio.fail_after(ceiling):
            return await _converse(spec, action, interactive=interactive)

    return anyio.run(guarded)


@dataclass(frozen=True)
class RawTool:
    """A tool as the server described it, when the SDK refuses to model it.

    Shaped to be indistinguishable from the SDK's own object at every place we
    touch one — `name`, `description`, `input_schema`, `annotations` — because
    the whole layer reads tools with `getattr` and must not learn there are two
    kinds.
    """

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    annotations: Any = None


def _tolerant_tools(payload: dict) -> list[RawTool]:
    """Tools from a raw `tools/list` reply, with the schema taken as given.

    A tool's `inputSchema` is JSON Schema written by somebody else. We pass it
    straight to a model provider and otherwise only read `required` out of it,
    so a schema that omits a field the SDK's model insists on is still
    perfectly usable to us.
    """
    out: list[RawTool] = []
    for item in payload.get("tools") or []:
        name = (item or {}).get("name") or ""
        if not name:
            continue
        schema = item.get("inputSchema") or item.get("input_schema") or {}
        if isinstance(schema, dict) and "type" not in schema:
            # The one repair worth making: every provider requires a top-level
            # type, and "object" is the only thing an MCP argument set can be.
            schema = {**schema, "type": "object"}
        out.append(RawTool(
            name=name,
            description=(item.get("description") or "").strip(),
            input_schema=schema if isinstance(schema, dict) else {},
            annotations=_Annotations(item.get("annotations") or {}),
        ))
    return out


class _Annotations:
    """`readOnlyHint` / `destructiveHint` from a raw reply, read the SDK's way."""

    def __init__(self, raw: dict) -> None:
        self.read_only_hint = raw.get("readOnlyHint")
        self.destructive_hint = raw.get("destructiveHint")


class _Block:
    """One content block from a raw reply, read the way the SDK's is."""

    def __init__(self, raw: dict) -> None:
        self.type = raw.get("type") or "text"
        self.text = raw.get("text")


class RawResult:
    """A tool result taken as published, when the SDK's model refuses it.

    Carries exactly the three things this layer reads — `is_error`,
    `structured_content`, `content[].text` — under the SDK's own attribute
    names, so `_records()` cannot tell the difference.
    """

    def __init__(self, raw: dict) -> None:
        self.is_error = bool(raw.get("isError"))
        self.structured_content = raw.get("structuredContent")
        self.content = [_Block(b) for b in (raw.get("content") or [])
                        if isinstance(b, dict)]


async def call_tool_in(session, tool: str, arguments: dict,
                       read_deadline: float) -> Any:
    """Run one tool, tolerating a server the SDK's model will not validate.

    `session.call_tool()` validates the *result* against the tool's declared
    output schema, and to do that it re-lists the tools — so a server whose
    schema the SDK rejects fails here too, **after the tool has already run**.
    That is the worst possible shape for a write: the mail is sent, the issue
    is created, and the caller is told it failed.

    So the fallback is not an optimisation. It is what stops a successful
    write being reported as an error and retried.
    """
    from pydantic import ValidationError

    try:
        return await session.call_tool(tool, dict(arguments or {}),
                                       read_timeout_seconds=read_deadline)
    except ValidationError as exc:
        log.debug("tool result failed strict validation, reading it as "
                  "published: %s", exc)

    raw = await session._dispatcher.send_raw_request(
        "tools/call", {"name": tool, "arguments": dict(arguments or {})})
    return RawResult(raw)


async def list_tools_in(session) -> list[Any]:
    """The tools a live session reports. One place, so every caller agrees.

    Falls back to the raw reply when the SDK's model rejects it. This is not
    hypothetical: the MCP Python SDK requires `inputSchema.type`, and servers
    built against older SDKs — including the reference filesystem server —
    publish a schema without one. Strict validation turns every one of those
    into "did not respond as expected", which is a working connector reported
    as broken over a field we do not even read.

    Only the shape is relaxed. Nothing here trusts the server more than before:
    the tools still go through `classify_tools()`, and a write is still a write.
    """
    from pydantic import ValidationError

    try:
        listed = await session.list_tools()
        return list(listed.tools)
    except ValidationError as exc:
        log.debug("server's tool list failed strict validation, "
                  "reading it as published: %s", exc)

    raw = await session._dispatcher.send_raw_request("tools/list", {})
    return _tolerant_tools(raw)


def probe(spec: MCPServerSpec, *, want_tools: bool = False,
          interactive: bool = False) -> tuple[ToolKinds | None, str]:
    """What can this server do, or why can we not tell?

    Returns `(None, reason)` rather than raising, because the caller is a UI
    that must say what happened in the place the user is looking. `want_tools`
    is accepted for readability at the call site — the tool objects are always
    carried now, since we have them in hand and asking twice costs a process.
    """
    from .mcp_errors import explain

    try:
        timeout = (CONSENT_TIMEOUT_SECONDS if interactive and spec.uses_oauth
                   else None)
        return classify_tools(converse(spec, list_tools_in, timeout,
                                       interactive=interactive)), ""
    except Exception as exc:
        log.debug("MCP probe failed for %s: %s", spec.id, exc)
        if spec.is_remote:
            reason = _remote_reason(spec)
            if reason:
                return None, reason
        return None, explain(exc, spec.name)


def _remote_reason(spec: MCPServerSpec) -> str:
    """Why a remote server refused us, when the protocol error will not say.

    An HTTP 401 reaches the caller as "Server returned an error response", so
    without this a revoked token and a server outage read the same — and the
    first has an obvious next step while the second does not.
    """
    from .mcp_auth import challenge

    if not challenge(spec.url):
        return ""
    if spec.auth == "token":
        return (f"{spec.name} did not accept that key. Check it in Connectors "
                "and paste a current one.")
    return (f"{spec.name} needs you to sign in. Open it in Connectors and "
            f"choose Connect — the sign-in happens on {spec.name}'s own site.")


#: Keeps a server's complaint to something a person will read. Long enough for
#: a real sentence, short enough that a wall of JSON does not become the card.
MAX_REFUSAL_CHARS = 300


#: Nearby tool names offered when the asked-for one does not exist. Enough to
#: point at the real capability, few enough to stay a sentence.
MAX_SUGGESTIONS = 5


def _no_such_tool(spec: Any, label: str, tool: str) -> str:
    """The tool is not there — so say what IS, rather than only that it is not.

    This used to be "Notion has no `x` to run." and stop, which reads as a
    glitch rather than as a limit. The honest version names the limit and the
    nearest real capabilities, because an agent that learns the connector
    simply cannot do this can tell the user so instead of guessing at a
    different spelling of the same wrong call.
    """
    stem = re.split(r"[-_]", str(tool or ""))[-1].lower()
    nearby: list[str] = []
    with suppressed("listing what a connector can do instead"):
        kinds, _why = probe(spec)
        if kinds is not None:
            every = sorted(set(list(kinds.readable or []) + list(kinds.write or [])))
            nearby = [t for t in every if stem and stem in t.lower()][:MAX_SUGGESTIONS]
            if not nearby:
                nearby = every[:MAX_SUGGESTIONS]
    if nearby:
        return (f"{label} has no `{tool}`. It is not something this connector "
                f"can do. The nearest things it does offer are: "
                + ", ".join(f"`{t}`" for t in nearby) + ".")
    return (f"{label} has no `{tool}`, and this connector does not offer "
            "anything like it.")


def _refusal(answer: Any, label: str) -> str:
    """Why the connector said no, in its own words where it gave any.

    Falls back to the flat sentence when the server returned nothing readable —
    an empty quotation is worse than a plain statement.
    """
    parts: list[str] = []
    for block in getattr(answer, "content", None) or []:
        text = str(getattr(block, "text", "") or "").strip()
        if text:
            parts.append(text)
    said = " ".join(parts).strip()
    if not said:
        return f"{label} could not complete that action."
    if len(said) > MAX_REFUSAL_CHARS:
        said = said[:MAX_REFUSAL_CHARS].rstrip() + "…"
    return f"{label} refused that: {said}"


def _block_text(block: Any) -> str:
    """The words in one reply block, wherever the server put them.

    **A block is not always a `text` block.** MCP lets a tool answer with an
    *embedded resource*, and the content then hangs off `block.resource.text`
    rather than `block.text`. GitHub's `get_file_contents` does exactly that:
    block one is the sentence "successfully downloaded text file (SHA: …)",
    block two is the whole file. Reading only `.text` kept the sentence and
    threw the file away — so an agent asked to improve a README was handed a
    SHA, could not see a word of it, and correctly refused to overwrite what
    it could not read.

    The same omission ran through `sync()`, so any server answering in
    resources contributed nothing to the brain but status lines.
    """
    text = getattr(block, "text", None)
    if isinstance(text, str) and text:
        return text
    resource = getattr(block, "resource", None)
    if resource is not None:
        inner = getattr(resource, "text", None)
        if isinstance(inner, str) and inner:
            return inner
        # A binary resource is base64 in `blob`. Decoding it would hand a model
        # a wall of noise, so it is named rather than inlined.
        if getattr(resource, "blob", None):
            uri = getattr(resource, "uri", "") or "a file"
            mime = getattr(resource, "mime_type", None) or getattr(
                resource, "mimeType", "") or "binary"
            return f"[{mime} content at {uri}, not shown]"
    return ""


def _records(result: Any) -> list[Any]:
    """Pull records out of whatever shape the tool answered with.

    Servers answer in three shapes and all three are common: structured JSON,
    a JSON document inside a text block, or plain prose. The last is still
    worth keeping — it is what the user would have read.
    """
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, list):
        return structured
    if isinstance(structured, dict):
        found = _list_inside(structured)
        if found is not None:
            return found
        # A tool that returns a JSON *string* arrives double-wrapped: the SDK
        # puts the string under `result`, and the records are inside the string.
        # Falling through to the content blocks below is what unwraps it, and
        # is why this does not simply store the wrapper as one record.
        if not _only_a_string(structured):
            return [structured]

    out: list[Any] = []
    for block in getattr(result, "content", None) or []:
        text = _block_text(block)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            out.append(text)
            continue
        if isinstance(parsed, list):
            out.extend(parsed)
        elif isinstance(parsed, dict):
            nested = next((parsed[k] for k in
                           ("items", "results", "records", "data", "entries")
                           if isinstance(parsed.get(k), list)), None)
            out.extend(nested if nested is not None else [parsed])
        else:
            out.append(text)
    return out


#: Keys servers put their records under. `result` is what the MCP SDK itself
#: uses when a tool returns a bare list, which is the most common case of all
#: and the one a hand-written key list is most likely to miss.
_RECORD_KEYS = ("result", "items", "results", "records", "data", "entries",
                "messages", "rows", "values")


def _list_inside(payload: dict) -> list | None:
    """The list of records inside a wrapper object, if there is one.

    Falls back to "a single key holding a list" rather than giving up, because
    servers name that key whatever suits their domain (`issues`, `pages`,
    `commits`) and no fixed list will ever cover them all.
    """
    for key in _RECORD_KEYS:
        if isinstance(payload.get(key), list):
            return payload[key]
    lists = [v for v in payload.values() if isinstance(v, list)]
    if len(lists) == 1 and len(payload) == 1:
        return lists[0]
    return None


def _only_a_string(payload: dict) -> bool:
    """Is this wrapper nothing but a single string value?"""
    return len(payload) == 1 and isinstance(next(iter(payload.values())), str)


def _as_text(record: Any, *, title_field: str, label: str) -> tuple[str, str]:
    """A record as (title, text) for the brain."""
    if isinstance(record, str):
        first = record.strip().split("\n", 1)[0]
        return (first[:120] or label), record
    if isinstance(record, dict):
        title = ""
        if title_field and record.get(title_field) is not None:
            title = str(record[title_field])
        else:
            for key in ("title", "name", "subject", "summary", "id"):
                if record.get(key) is not None:
                    title = str(record[key])
                    break
        body = record.get("text") or record.get("body") or record.get("content")
        if not isinstance(body, str):
            body = json.dumps(record, ensure_ascii=False, indent=2)[:4000]
        return (title or label), f"{label} — {title or ''}\n\n{body}".strip()
    return label, f"{label}\n\n{record}"


# ── the connector ───────────────────────────────────────────────────────────


class MCPConnector(Connector):
    """One connector per configured server (name = 'mcp:<id>')."""

    auto_sync = True
    # A watermark would have to be a parameter the tool accepts, and there is no
    # agreed name for one across servers. Dedup carries the repeat pass.
    incremental = False

    def __init__(self, spec: MCPServerSpec, store=None) -> None:
        super().__init__(store)
        self.spec = spec
        self.name = f"mcp:{spec.id}"
        self.label = spec.name

    def status(self) -> tuple[bool, str, bool]:
        """(ready, reason, can_sync) from a single probe.

        Three facts the Connectors row needs and one process start to get them.
        `can_sync` is separate from `ready` on purpose: a server that can only
        answer questions is a **working** connector — that is what the agent
        tool path exists for — but offering it a Sync button would be offering
        a control that cannot work.
        """
        ready, reason = self._reachable()
        if not ready:
            return False, reason, False
        kinds, probe_reason = probe(self.spec)
        if kinds is None:
            return False, probe_reason, False
        if not kinds.readable and not kinds.write:
            return False, kinds.why_not(self.label), False
        return True, "", bool(kinds.can_sync or self.spec.sync_tool)

    def _reachable(self) -> tuple[bool, str]:
        """The cheap, local disqualifications — no process, no network."""
        if self.spec.is_remote:
            if not self.spec.url:
                return False, "this connector has no address to reach"
        elif not self.spec.command:
            return False, "this connector has no command to run"
        elif resolve_command(self.spec.command) is None:
            return False, (
                f"{self.label} could not be started — {self.spec.command} is "
                "missing on this Mac. Install it, then try connecting again.")
        missing = self.spec.missing_env()
        if missing:
            return False, (f"{self.label} still needs "
                           f"{', '.join(missing)} before it can connect.")
        return True, ""

    def is_configured(self) -> tuple[bool, str]:
        """A server that will not start must never read as Connected.

        This actually reaches it, because the only honest test of "does this
        work" is using it — a binary that was uninstalled, a credential that
        expired, a token the vendor revoked and a server that crashes on boot
        all look identical from the spec alone.

        The cheap, local disqualifications are checked first so the common
        failures do not cost a process start or a network round trip.
        """
        ready, reason, _ = self.status()
        return ready, reason

    # ── actions (writes) ────────────────────────────────────────────────

    def available_actions(self) -> list[dict[str, Any]]:
        """Write tools this connector is permitted to offer, for confirmation.

        Returned rather than executed: nothing here changes anything at the
        vendor. The list is what a confirmation card is built from, which is the
        only way a user can be shown what they are agreeing to — and now what
        an agent is shown too, so it can propose one.

        Each row carries the vendor's own description and argument schema. A
        model asked to propose `create_issue` with no schema is guessing at
        field names, and a user approving one with no description is approving
        a verb.
        """
        kinds, _ = probe(self.spec, want_tools=True)
        if kinds is None:
            return []
        by_name = {getattr(t, "name", ""): t for t in (kinds.tools or [])}
        rows: list[dict[str, Any]] = []
        for name in kinds.write:
            if not self.spec.permits(name):
                continue
            tool = by_name.get(name)
            raw = (getattr(tool, "input_schema", None)
                   or getattr(tool, "inputSchema", None)) if tool else None
            rows.append({
                "tool": name,
                "connector": self.name,
                "server_id": self.spec.id,
                "label": self.label,
                "description": (getattr(tool, "description", "") or "").strip(),
                "parameters": raw if isinstance(raw, dict)
                              else {"type": "object", "properties": {}},
            })
        return rows

    def perform(self, tool: str, arguments: dict[str, Any] | None = None, *,
                confirmed: bool = False) -> dict[str, Any]:
        """Run a write tool — only after the user has confirmed *this* action.

        `confirmed` is a required, explicit gate rather than a default, so a
        caller that forgets it fails closed. Connectors have been read-only by
        design (decision C1) and this is the first path that changes something
        at the vendor; the agent reaches it the same way it reaches sending an
        email — by proposing an action the user clicks to approve, never by
        calling it mid-turn.
        """
        from .mcp_errors import explain

        if not confirmed:
            return {"ok": False,
                    "error": "This action needs your confirmation first."}
        if not self.spec.permits(tool):
            return {"ok": False,
                    "error": f"{self.label} is not allowed to use `{tool}`."}

        async def _verify_and_call(session):
            """Check the tool exists and run it, inside one conversation.

            This used to probe and then call, which started the server twice
            and left a window between the two. Re-listing inside the session we
            already have costs one message and no extra process — the shape
            `mcp_tools.call_tool()` already uses for the same reason.
            """
            live = classify_tools(await list_tools_in(session))
            if not live.kind_of(tool):
                return ("missing", None)
            # The server already said what this tool accepts. Sending a call it
            # has to refuse costs a round trip and hands the user a vendor
            # error where a sentence would do.
            problem = argument_problem(schema_of(live, tool), arguments or {})
            if problem:
                return ("bad-arguments", problem)
            return ("ok", await call_tool_in(
                session, tool, arguments or {}, CALL_TIMEOUT_SECONDS))

        try:
            outcome, answer = converse(self.spec, _verify_and_call,
                                       timeout=CALL_TIMEOUT_SECONDS)
        except Exception as exc:
            return {"ok": False, "error": explain(exc, self.label)}
        if outcome == "missing":
            return {"ok": False, "error": _no_such_tool(self.spec, self.label, tool)}
        if outcome == "bad-arguments":
            return {"ok": False,
                    "error": f"{self.label} cannot run `{tool}` like that — {answer}"}
        if getattr(answer, "is_error", False):
            # The server said WHY, and this used to throw it away. So the user
            # was told only that it had not worked, the agent could not see the
            # reason either, and the best it could do was guess at the shape and
            # ask them to paste the error back in. A vendor's own sentence about
            # the user's own request is the actionable part, not an internal.
            return {"ok": False, "error": _refusal(answer, self.label)}
        records = _records(answer)
        detail = records[0] if len(records) == 1 else records
        return {"ok": True, "detail": detail}

    def sync(self, *, since: str | None = None, limit: int | None = None,
             full_history: bool = False, cancel=None, progress=None,
             interactive: bool = True, **_: Any) -> SyncResult:
        from .mcp_errors import explain

        result = SyncResult(connector=self.name)
        max_items = limit or 200

        try:
            async def _classify_and_read(session):
                """Work out what to call and call it — all of it, in one
                conversation.

                **This used to call exactly one tool: `sync_tool` or, failing
                that, `permitted[0]`.** Which is whichever sorted first. On a
                real install that was `list_agent_skills` for Linear and
                `notion-get-tool-access` for Notion — so the brain was filled
                with each server's own configuration while the issues and
                pages it exists to hold were never fetched at all. Nothing
                reported a problem, because a tool had been called and records
                had been ingested.

                So: every permitted bulk tool that carries the user's content,
                in the session already open. A named `sync_tool` still wins —
                that is somebody having decided — and metadata listings are
                skipped by shape rather than by name.
                """
                kinds = classify_tools(await list_tools_in(session))
                permitted = [t for t in kinds.bulk if self.spec.permits(t)]
                if self.spec.sync_tool:
                    chosen = ([self.spec.sync_tool]
                              if self.spec.permits(self.spec.sync_tool) else [])
                    if not chosen:
                        return (kinds, [self.spec.sync_tool], "forbidden")
                else:
                    chosen = [t for t in permitted if not is_server_metadata(t)]
                    # Everything looked like furniture. Better the server's
                    # own listing than nothing, and the detail line says which.
                    if not chosen and permitted:
                        chosen = permitted[:1]
                if not chosen:
                    return (kinds, [], None)

                answers = []
                for tool in chosen[:MAX_HARVEST_TOOLS]:
                    # A literal label, so it stays greppable — an f-string
                    # here is a suppression nobody can find later, which is
                    # what `test_failures_are_recorded.py` exists to stop.
                    with suppressed("reading one listing from an MCP server"):
                        answers.append((tool, await call_tool_in(
                            session, tool, {}, CALL_TIMEOUT_SECONDS)))
                return (kinds, chosen, answers)

            try:
                kinds, tools, answers = converse(
                    self.spec, _classify_and_read, timeout=CALL_TIMEOUT_SECONDS)
            except Exception as exc:
                result.errors.append(explain(exc, self.label))
                result.detail = "could not start"
                return self._finish(result)

            if answers == "forbidden":
                result.errors.append(
                    f"{self.label} is not allowed to use its "
                    f"`{tools[0]}` tool. Change what it may read in "
                    f"Connectors.")
                result.detail = "not permitted"
                return self._finish(result)
            if not tools:
                # Not an error — a real and permanent property of this server,
                # and saying so is the difference between "search-only" and
                # "broken". Silently reporting success with zero records is how
                # a user concludes the app does not work.
                result.detail = kinds.why_not(self.label)
                result.errors.append(result.detail)
                return self._finish(result)
            from ..brain import get_brain
            brain = get_brain()

            # Shared across tools, so one server's budget is a server's budget
            # rather than a budget each. Sixteen listings at 200 apiece is a
            # brain full of one connector.
            budget = max_items
            worked: list[str] = []
            for tool, answer in answers:
                if budget <= 0:
                    break
                if getattr(answer, "is_error", False):
                    # One tool refusing is not the sync failing. Said, not
                    # swallowed, and the others still run.
                    result.errors.append(
                        f"{self.label} could not read `{tool}`.")
                    continue
                records = _records(answer)
                if not records:
                    continue

                def ingest(record, _tool=tool) -> int:
                    title, text = _as_text(
                        record, title_field=self.spec.title_field,
                        label=self.label)
                    if not text.strip():
                        return 0
                    out = brain.ingest(
                        text, source=self.name, kind="record", title=title,
                        fast=True,
                        # Which listing it came from. Without it every record
                        # from one server is indistinguishable from every
                        # other, and there is no way to re-read or retire one
                        # tool's worth of content.
                        metadata={"mcp_tool": _tool, "server": self.spec.id})
                    return out["memories"]

                taken = records[:budget]
                budget -= len(taken)
                worked.append(tool)
                self.each_guarded(taken, result, ingest,
                                  cancel=cancel, progress=progress)

            result.detail = result.detail or (
                f"{result.added} record(s) from {self.label} "
                f"({len(worked)} listing(s))"
                if worked else f"{self.label} returned nothing to store")
        except Exception as exc:
            result.errors.append(explain(exc, self.label))
            result.detail = "sync failed"
        return self._finish(result)
