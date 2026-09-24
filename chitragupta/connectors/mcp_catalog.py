"""The connectors we offer, and the ones we tell the truth about instead.

Two jobs, and the second matters as much as the first.

**Offering one safely.** An MCP server is third-party code that runs as the user
and reads their accounts, so it gets the rules `models/cli_manager.py` already
established for vendor CLIs: a **pinned version**, never `@latest` — an
unpinned package is a supply chain that changes under the user without anyone
deciding — and **verified before it is enabled**, by actually starting it and
reading its tool list, because a spec that parses proves nothing about a server
that will not boot.

**Refusing one honestly.** Some sources cannot be read by anybody, and a UI that
simply omits them teaches the user that the app is missing features. LinkedIn is
the case that matters: its User Agreement §8.2 bans automated access, feed and
member data sit behind a partner programme individual developers cannot join,
and every community server is a scraper — after the late-2025 crackdown, one
built on a browser fork made to evade bot detection. Shipping that would put a
**ban on the user's account, not ours**. Composio's LinkedIn toolkit is
write-only for exactly the same reason, so this is a platform limit and not a
gap in our approach. `BLOCKED` carries the sentence to show instead, which is
"never show a control that cannot work" applied to a whole source.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from ..log import suppressed
from .mcp_source import (
    MCPServerSpec,
    probe,
    secret_key,
    set_server_env,
    upsert_server,
)


@dataclass(frozen=True)
class NeededValue:
    """One thing the user has to supply before a connector can work."""

    #: For env: the variable name. For args: a stable id used by the form only.
    name: str
    #: What to show above the field.
    label: str
    #: What to say under it. Never the variable name again.
    help: str
    #: "secret" masks the field; "path" offers a folder; "text" is plain.
    kind: str = "secret"


@dataclass(frozen=True)
class CatalogEntry:
    """A connector the user can add.

    Two shapes, because the sources genuinely split two ways.

    **Remote** (`transport="http"`) is the vendor's own endpoint. Nothing is
    downloaded, nothing third-party runs as the user, and there is no package
    to rot — the failure mode that emptied this catalog the first time. Where
    the vendor runs an OAuth server we register dynamically (RFC 7591), so the
    consent screen carries *their* name and Chitragupta still owns no OAuth
    client. Where they take a key instead, it is the key the user already has.

    **Local** (`transport="stdio"`) is a subprocess, for sources with no remote
    endpoint. Versions are pinned: an unpinned package is a supply chain that
    changes under the user without anyone deciding.
    """

    id: str
    name: str
    #: Which shelf this sits on in the browser. A flat list of two dozen
    #: connectors is a wall; the same list in six groups is a decision. Kept on
    #: the entry rather than in the UI so the two cannot disagree about where
    #: something belongs.
    category: str = "Other"
    transport: str = "stdio"
    #: stdio: how it is launched. The version is part of the package spec.
    command: str = ""
    args: tuple[str, ...] = ()
    #: http: where it lives, and how it authenticates.
    url: str = ""
    auth: str = "oauth"                 # oauth | token | none
    #: Values the user supplies. `needs_env` become environment variables (or,
    #: for `auth="token"`, the bearer); `needs_args` are appended to `args`.
    needs_env: tuple[NeededValue, ...] = ()
    needs_args: tuple[NeededValue, ...] = ()
    #: First-party means the vendor publishes it. Community servers are offered
    #: with that stated, never silently mixed in.
    first_party: bool = True
    #: The built-in connector that reaches this same source, if one does.
    #:
    #: The other half of `Connector.prefer_mcp`, and the half that was missing.
    #: `prefer_mcp` stops the *built-in* being offered where a server wins;
    #: nothing stopped the *catalog* offering a server where the built-in wins,
    #: so GitHub sat connected on the Connectors screen while **Add a
    #: connector** offered to connect GitHub again — the same two-routes-one-app
    #: screenshot that `docs/REACHING-AN-APP.md` was written for, arriving from
    #: the other direction.
    #:
    #: Declared here rather than matched on id, because `filesystem` is our
    #: `files` and a string comparison would quietly stop finding the pair.
    same_as: str = ""
    notes: str = ""

    @property
    def is_remote(self) -> bool:
        return self.transport == "http"

    @property
    def pinned(self) -> bool:
        """Is every package reference version-locked?

        A remote server has no package, so there is nothing to pin and nothing
        that can change under the user — it is pinned by construction.
        """
        if self.is_remote:
            return True
        for arg in self.args:
            if arg.startswith("-"):
                continue
            if arg.endswith(("@latest", "@*")):
                return False
            if "@" in arg.lstrip("@").replace("/", "") or "==" in arg:
                return True
        return False

    def to_spec(self, *, args: dict[str, str] | None = None) -> MCPServerSpec:
        """The spec this entry becomes, with the user's answers filled in.

        Positional arguments are appended in declared order. Nothing secret is
        placed here — values go to the Keychain through `set_server_env`.
        """
        # `resolve()` on a path the user chose, because macOS makes /tmp a
        # symlink to /private/tmp and a server that realpaths its allowed
        # directory then rejects every write under the name the user gave it.
        extra = []
        for value in self.needs_args:
            raw = ((args or {}).get(value.name) or "").strip()
            if raw and value.kind == "path":
                with suppressed("resolving a chosen folder"):
                    raw = str(Path(raw).expanduser().resolve())
            extra.append(raw)
        token_key = (self.needs_env[0].name
                     if self.auth == "token" and self.needs_env else "")
        return MCPServerSpec(
            id=self.id, name=self.name, transport=self.transport,
            command=self.command,
            args=[*self.args, *[a for a in extra if a]],
            url=self.url, auth=self.auth, token_key=token_key,
            env_keys=[v.name for v in self.needs_env],
        )


@dataclass(frozen=True)
class BlockedSource:
    """A source we will not offer, and the reason to show where it would be."""

    id: str
    name: str
    reason: str


#: The shelves, in the order they are shown. Explicit rather than derived from
#: the entries, because "the order categories happen to appear in the tuple" is
#: not a decision anybody made, and the first shelf is the one most people need.
CATEGORIES: tuple[str, ...] = (
    "Work & tasks",
    "Notes, files & meetings",
    "Design & web",
    "Code & infrastructure",
    "Money & customers",
    "Reference",
)

#: Vetted connectors.
#:
#: Every remote endpoint here was checked to exist and to answer an
#: unauthenticated call with an OAuth challenge before being listed — the same
#: rule `/CLAUDE.md` sets for model ids, for the same reason. The catalog this
#: replaces listed a package that had never existed on npm and two that were
#: deprecated upstream, and every row of it failed.
CATALOG: tuple[CatalogEntry, ...] = (
    # ── Work & tasks ─────────────────────────────────────────────
    CatalogEntry(
        id="linear", name="Linear", category="Work & tasks",
        transport="http", url="https://mcp.linear.app/mcp",
        same_as="linear",
        notes="Issues, projects and cycles. Reading and, with your approval, creating or updating issues.",
    ),
    CatalogEntry(
        id="asana", name="Asana", category="Work & tasks",
        transport="http", url="https://mcp.asana.com/sse",
        notes="Tasks and projects in your workspaces.",
    ),
    CatalogEntry(
        id="atlassian", name="Jira & Confluence", category="Work & tasks",
        transport="http", url="https://mcp.atlassian.com/v1/sse",
        notes="Atlassian's own server, covering both Jira issues and Confluence pages.",
    ),
    CatalogEntry(
        id="todoist", name="Todoist", category="Work & tasks",
        transport="http", url="https://ai.todoist.net/mcp",
        notes="Your tasks, projects and labels.",
    ),
    CatalogEntry(
        id="clickup", name="ClickUp", category="Work & tasks",
        transport="http", url="https://mcp.clickup.com/mcp",
        notes="Tasks, docs and spaces in your workspace.",
    ),
    CatalogEntry(
        id="zapier", name="Zapier", category="Work & tasks",
        transport="http", url="https://mcp.zapier.com/api/mcp/mcp",
        notes="The automations you have already built, callable by your agents.",
    ),

    # ── Notes, files & meetings ──────────────────────────────────
    CatalogEntry(
        id="notion", name="Notion", category="Notes, files & meetings",
        transport="http", url="https://mcp.notion.com/mcp",
        same_as="notion",
        notes="Pages and databases you have access to.",
    ),
    CatalogEntry(
        id="fireflies", name="Fireflies", category="Notes, files & meetings",
        transport="http", url="https://api.fireflies.ai/mcp",
        notes="Transcripts and summaries of the meetings it recorded.",
    ),
    CatalogEntry(
        id="airtable", name="Airtable", category="Notes, files & meetings",
        transport="http", url="https://mcp.airtable.com/mcp",
        notes="Bases, tables and records you can reach.",
    ),

    # ── Design & web ─────────────────────────────────────────────
    CatalogEntry(
        id="figma", name="Figma", category="Design & web",
        transport="http", url="https://mcp.figma.com/mcp",
        notes="Design files and the components in them.",
    ),
    CatalogEntry(
        id="canva", name="Canva", category="Design & web",
        transport="http", url="https://mcp.canva.com/mcp",
        notes="Your designs, folders and brand assets.",
    ),
    CatalogEntry(
        id="webflow", name="Webflow", category="Design & web",
        transport="http", url="https://mcp.webflow.com/sse",
        notes="Sites, pages and CMS collections.",
    ),

    # ── Code & infrastructure ────────────────────────────────────
    CatalogEntry(
        id="github", name="GitHub", category="Code & infrastructure",
        transport="http", url="https://api.githubcopilot.com/mcp/",
        auth="token", same_as="github",
        needs_env=(NeededValue(
            "GITHUB_TOKEN", "Personal access token",
            "Create one at github.com → Settings → Developer settings. It only needs access to the repositories you want Chitragupta to see."),),
        notes="GitHub's own hosted server. Issues, pull requests, code and discussions.",
    ),
    CatalogEntry(
        id="sentry", name="Sentry", category="Code & infrastructure",
        transport="http", url="https://mcp.sentry.dev/mcp",
        notes="Issues and events for the projects in your organisation.",
    ),
    CatalogEntry(
        id="vercel", name="Vercel", category="Code & infrastructure",
        transport="http", url="https://mcp.vercel.com",
        notes="Projects, deployments and their logs.",
    ),
    CatalogEntry(
        id="cloudflare", name="Cloudflare", category="Code & infrastructure",
        transport="http", url="https://observability.mcp.cloudflare.com/mcp",
        notes="Workers, logs and analytics for your account.",
    ),
    CatalogEntry(
        id="neon", name="Neon", category="Code & infrastructure",
        transport="http", url="https://mcp.neon.tech/mcp",
        notes="Postgres databases, branches and queries.",
    ),
    CatalogEntry(
        id="datadog", name="Datadog", category="Code & infrastructure",
        transport="http", url="https://mcp.datadoghq.com/api/unstable/mcp-server/mcp",
        notes="Monitors, dashboards and incidents.",
    ),

    # ── Money & customers ────────────────────────────────────────
    CatalogEntry(
        id="stripe", name="Stripe", category="Money & customers",
        transport="http", url="https://mcp.stripe.com",
        notes="Customers, payments and subscriptions. Anything that moves money always asks you first.",
    ),
    CatalogEntry(
        id="paypal", name="PayPal", category="Money & customers",
        transport="http", url="https://mcp.paypal.com/mcp",
        notes="Invoices, orders and transactions.",
    ),
    CatalogEntry(
        id="square", name="Square", category="Money & customers",
        transport="http", url="https://mcp.squareup.com/mcp",
        notes="Payments, catalogue and customers.",
    ),
    CatalogEntry(
        id="intercom", name="Intercom", category="Money & customers",
        transport="http", url="https://mcp.intercom.com/mcp",
        notes="Conversations, contacts and help articles.",
    ),

    # ── Reference ────────────────────────────────────────────────
    # Open endpoints: no account, no sign-in, nothing to store. Worth
    # offering because an agent that can look a library up stops
    # guessing at an API it half-remembers.
    CatalogEntry(
        id="deepwiki", name="DeepWiki", category="Reference",
        transport="http", url="https://mcp.deepwiki.com/mcp",
        auth="none",
        notes="Ask questions about any public GitHub repository. No sign-in needed.",
    ),
    CatalogEntry(
        id="context7", name="Context7", category="Reference",
        transport="http", url="https://mcp.context7.com/mcp",
        auth="none",
        notes="Up-to-date documentation for thousands of libraries. No sign-in needed.",
    ),

    # ── this Mac ─────────────────────────────────────────────────
    CatalogEntry(
        id="filesystem", name="A folder on this Mac",
        category="Notes, files & meetings", transport="stdio", auth="none",
        command="npx", args=("-y", "@modelcontextprotocol/server-filesystem@0.6.2"),
        same_as="files",
        needs_args=(NeededValue(
            "root", "Folder", "The folder this connector may read. Nothing "
            "outside it is reachable.", kind="path"),),
        notes="The built-in Local Files connector is usually the better "
              "choice; this exists for parity and needs Node installed.",
    ),
)

#: Sources that cannot be read by anyone, with the sentence to show instead.
#: Each of these is a platform decision, not a limitation of this approach — a
#: hosted broker hits exactly the same wall.
BLOCKED: tuple[BlockedSource, ...] = (
    BlockedSource(
        "linkedin", "LinkedIn",
        "LinkedIn does not allow apps to read your feed, connections or "
        "messages — its terms ban automated access, and the tools that claim "
        "to do it work by evading detection and can get your account banned. "
        "No app can offer this, including the ones that say they do.",
    ),
    BlockedSource(
        "whatsapp", "WhatsApp",
        "WhatsApp has no API for reading your personal chats. Messages on this "
        "Mac can be read through the iMessage connector instead.",
    ),
    BlockedSource(
        "instagram", "Instagram",
        "Instagram's API covers business accounts only, and does not expose a "
        "personal feed or direct messages to any app.",
    ),
)

BY_ID = {entry.id: entry for entry in CATALOG}
BLOCKED_BY_ID = {source.id: source for source in BLOCKED}


def unpinned() -> list[str]:
    """Catalog ids whose packages are not version-locked.

    Exposed so a test can assert the list is empty rather than a reviewer
    having to notice an `@latest` in a diff.
    """
    return [entry.id for entry in CATALOG if not entry.pinned]


def add_from_catalog(entry_id: str, env: dict[str, str] | None = None,
                     args: dict[str, str] | None = None
                     ) -> tuple[MCPServerSpec | None, str]:
    """Set a catalog connector up, and never leave a broken one behind.

    Two paths, because verification means different things.

    A connector we can reach *now* — a local server, or a remote one taking a
    key — is **probed before it is saved**, and nothing is written if it will
    not answer. That is the vendor-CLI rule: a download that fails verification
    is never linked.

    A connector behind the vendor's own sign-in cannot be probed first: there is
    nothing to probe until consent exists. So the spec is saved and the sign-in
    started, and until it finishes `is_configured()` reports "needs you to sign
    in" — which is a connector visibly waiting on the user, not a "Connected"
    badge with nothing behind it.
    """
    entry = BY_ID.get(entry_id)
    if entry is None:
        blocked_source = BLOCKED_BY_ID.get(entry_id)
        if blocked_source is not None:
            return None, blocked_source.reason
        return None, "That connector is not in the catalog."

    missing_args = [v.label for v in entry.needs_args
                    if not ((args or {}).get(v.name) or "").strip()]
    if missing_args:
        return None, (f"{entry.name} still needs "
                      f"{', '.join(missing_args).lower()} before it can connect.")
    # Named by their labels, not their variable names: `GITHUB_TOKEN` is an
    # internal, and the label is the words that were above the box they left
    # empty. Same rule as `needs_args` directly above.
    missing_env = [v.label for v in entry.needs_env if not (env or {}).get(v.name)]
    if missing_env:
        return None, (f"{entry.name} still needs "
                      f"{', '.join(missing_env).lower()} before it can connect.")

    spec = entry.to_spec(args=args)

    if entry.is_remote and entry.auth == "oauth":
        # Save first, then consent. Nothing here claims the connector works;
        # `is_configured()` is what decides that, and it will say "needs you to
        # sign in" until the vendor says otherwise.
        upsert_server(spec)
        set_server_env(spec.id, env or {})
        from .mcp_auth import begin

        begin(spec)
        return spec, ""

    # Reachable now, so prove it now. The values have to be stored before the
    # probe — a server is started with its credentials or not at all — and are
    # cleared again if it does not answer, so a failed add leaves nothing.
    _stage_env(spec, env or {})
    kinds, reason = probe(spec)
    if kinds is None:
        _clear_env(spec, env or {})
        return None, reason
    if not kinds.readable and not kinds.write:
        _clear_env(spec, env or {})
        return None, kinds.why_not(entry.name)

    upsert_server(spec)
    return spec, ""


def _stage_env(spec: MCPServerSpec, env: dict[str, str]) -> None:
    """Store a candidate's values so it can be started for verification."""
    settings = get_settings()
    for name, value in env.items():
        if value:
            settings.set_secret(secret_key(spec.id, name), value.strip())


def _clear_env(spec: MCPServerSpec, env: dict[str, str]) -> None:
    """Undo `_stage_env` after a failed verification. A rejected key is not kept."""
    settings = get_settings()
    for name in env:
        with suppressed("clearing a rejected connector's value"):
            settings.set_secret(secret_key(spec.id, name), None)


def _needed(values: tuple[NeededValue, ...]) -> list[dict]:
    return [{"name": v.name, "label": v.label, "help": v.help, "kind": v.kind}
            for v in values]


def describe(entry_id: str) -> dict:
    """What a connector would be allowed to do, before it is enabled.

    Least privilege needs the user to be able to see the tools first — an
    install button that says only "Connect" is asking for consent to something
    nobody has been shown.

    For a server behind the vendor's sign-in there is nothing to show yet, and
    saying so is better than an empty list that reads as "this does nothing":
    `needs_auth` tells the UI to offer Connect and describe the permissions
    afterwards, on the connector's own screen.
    """
    entry = BY_ID.get(entry_id)
    if entry is None:
        return {"id": entry_id, "available": False,
                "reason": (BLOCKED_BY_ID[entry_id].reason
                           if entry_id in BLOCKED_BY_ID
                           else "That connector is not in the catalog."),
                "needs_auth": False}

    base = {
        "id": entry.id,
        "name": entry.name,
        "first_party": entry.first_party,
        "notes": entry.notes,
        "remote": entry.is_remote,
        "auth": entry.auth,
        "needs_env": _needed(entry.needs_env),
        "needs_args": _needed(entry.needs_args),
    }

    if entry.is_remote and entry.auth == "oauth":
        return {**base, "available": True, "needs_auth": True, "reason": "",
                "reads": [], "writes": [], "can_sync": False}

    if entry.needs_env or entry.needs_args:
        # Nothing to probe with until the user has answered. Offering the form
        # is the honest state; probing an unconfigured spec would only produce
        # an error that says the user has not filled the form in yet.
        return {**base, "available": True, "needs_auth": False, "reason": "",
                "reads": [], "writes": [], "can_sync": False}

    kinds, reason = probe(entry.to_spec())
    return {
        **base,
        "available": kinds is not None,
        "needs_auth": False,
        "reason": reason,
        "reads": list(kinds.readable) if kinds else [],
        "writes": list(kinds.write) if kinds else [],
        "can_sync": bool(kinds and kinds.can_sync),
    }
