"""Custom API connector — let any user connect any REST app, no code.

A user describes an app in the UI (base URL, endpoint, auth, and which JSON
fields become the title/body). Definitions live in ~/Library/Chitragupta/
custom_apps.json; the auth token (if any) lives in the chmod-600 secrets store.
Chitragupta fetches the endpoint, walks to the list of items, and ingests each
into the brain — so custom apps behave exactly like the built-in connectors.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from ..config import get_settings
from . import engine
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, PaginationStrategy
from .engine import Record
from .outbound import check_landing, check_url
from .pagination import Page, cursor_of
from .provenance import SourceRef
from .resources import fingerprint

_APPS_FILE = "custom_apps.json"


# ── definition storage ────────────────────────────────────────────────────
def _apps_path():
    return get_settings().home / _APPS_FILE


def _load() -> dict[str, dict]:
    try:
        return json.loads(_apps_path().read_text())
    except Exception:
        return {}


def _save(apps: dict[str, dict]) -> None:
    get_settings().ensure_home()
    _apps_path().write_text(json.dumps(apps, indent=2))


def _secret_key(app_id: str) -> str:
    return f"CUSTOM_{app_id}_TOKEN"


def list_apps() -> list[dict]:
    return list(_load().values())


def get_app(app_id: str) -> dict | None:
    return _load().get(app_id)


def upsert_app(cfg: dict, token: str | None = None) -> dict:
    """Create or update a custom app. Returns the stored (token-free) config."""
    apps = _load()
    app_id = cfg.get("id") or f"{_slug(cfg.get('name', 'app'))}-{uuid.uuid4().hex[:6]}"
    stored = {
        "id": app_id,
        "name": cfg.get("name") or "Custom app",
        "base_url": (cfg.get("base_url") or "").rstrip("/"),
        "endpoint": cfg.get("endpoint") or "",
        "auth_type": cfg.get("auth_type") or "none",   # none|bearer|header|query
        "auth_name": cfg.get("auth_name") or "",       # header/query param name
        "items_path": cfg.get("items_path") or "",     # dot-path to the list
        "title_field": cfg.get("title_field") or "",
        "body_field": cfg.get("body_field") or "",
    }
    apps[app_id] = stored
    _save(apps)
    if token is not None:
        get_settings().set_secret(_secret_key(app_id), token)
    return stored


def delete_app(app_id: str) -> bool:
    apps = _load()
    if app_id not in apps:
        return False
    del apps[app_id]
    _save(apps)
    get_settings().set_secret(_secret_key(app_id), "")   # clear token too
    return True


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "app"


def _dig(obj: Any, path: str) -> Any:
    """Walk a dot-path (e.g. 'data.items') into nested dicts."""
    if not path:
        return obj
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


# ── the connector ─────────────────────────────────────────────────────────
class CustomAPIConnector(Connector):
    """Instantiated per custom-app definition (name = 'custom:<id>')."""

    auto_sync = True
    # A generic REST endpoint has no agreed "changed since" parameter, so the
    # whole list is re-read and dedup does the rest.
    incremental = False
    resumable = True
    pagination = PaginationStrategy.CURSOR
    auth_method = AuthMethod.API_KEY
    #: **Read-only, by construction and not by convention.** A declarative
    #: connector a user writes in a form must not be a way to describe an
    #: arbitrary side effect: there is no write path here, so there is no write
    #: capability, and `capability_floor` has nothing to let through.
    #: `docs/CONNECTOR-PLATFORM.md` — custom APIs may not bypass the gate.
    capabilities = caps("read:record")
    limits = Limits(requests=60, per_seconds=60.0, concurrency=1,
                    page_size=100, records_per_sync=300)

    #: Where in the whole listing the current page starts. Only used by the
    #: position fallback in `_identity`, and reset per pass so a resumed sync
    #: does not renumber what the first attempt already stored.
    _offset: int = 0

    def __init__(self, app: dict, store=None) -> None:
        super().__init__(store)
        self.app = app
        self.name = f"custom:{app['id']}"
        self.label = app.get("name") or "Custom app"

    def is_configured(self) -> tuple[bool, str]:
        if not self.app.get("base_url"):
            return False, "click setup to finish configuring this app"
        if self.app.get("auth_type", "none") != "none" and \
                not get_settings().get_secret(_secret_key(self.app["id"])):
            return False, "click setup to add this app's token"
        return True, ""

    def _url(self, cursor: str = "") -> str:
        """The address for one page, checked before anything is sent.

        **The scheme check is not enough on its own**, and was all there was:
        `http://127.0.0.1:<port>/api/brain/reset` is an `http://` URL, and the
        port the desktop app binds is discoverable. `outbound.check_url` is the
        rest of that answer — see its module note for why loopback and
        link-local are refused while the rest of the LAN is not.
        """
        app = self.app
        url = app["base_url"] + (app.get("endpoint") or "")
        if cursor:
            # A page marker the provider handed back. An absolute URL is used
            # as-is (a `Link` header or a `next` field usually is one); a bare
            # token is appended as the parameter the user named.
            if cursor.lower().startswith(("http://", "https://")):
                url = cursor
            else:
                sep = "&" if "?" in url else "?"
                name = app.get("page_param") or "page"
                url += f"{sep}{urllib.parse.quote(name)}=" \
                       f"{urllib.parse.quote(cursor)}"
        return check_url(url)

    def _request(self, cursor: str = "") -> Any:
        app = self.app
        url = self._url(cursor)
        headers = {"Accept": "application/json", "User-Agent": "Chitragupta"}
        token = get_settings().get_secret(_secret_key(app["id"]))
        atype = app.get("auth_type", "none")
        if token and atype == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif token and atype == "header":
            headers[app.get("auth_name") or "Authorization"] = token
        elif token and atype == "query":
            sep = "&" if "?" in url else "?"
            url += f"{sep}{urllib.parse.quote(app.get('auth_name') or 'api_key')}=" \
                   f"{urllib.parse.quote(token)}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            # **Where it landed, not where it was sent.** A server answering
            # `302 → http://127.0.0.1:8765` has redirected us onto the loopback
            # the check above exists to refuse, and `urlopen` follows redirects
            # by default. The rule is `docs/BROWSER.md`'s, applied here.
            check_landing(resp.geturl())
            return json.loads(resp.read())

    # ── the pass ─────────────────────────────────────────────────────────

    def _record(self, item: Any, index: int) -> Record:
        """One JSON object as something the engine can carry.

        The three field lookups are the user's own `title_field`,
        `body_field` and `id_field`, and the None-guard on each is load-bearing
        and predates this: a missing field is `None`, `str(None)` is `"None"`
        which is truthy, so a `or` fallback collapses every field-less record
        into one identical body that dedup then throws away.
        """
        if not isinstance(item, dict):
            item = {"value": item}
        title_field = self.app.get("title_field")
        body_field = self.app.get("body_field")

        found_title = _dig(item, title_field) if title_field else None
        found_body = _dig(item, body_field) if body_field else None
        title = str(found_title) if found_title is not None else self.label
        body = (str(found_body) if found_body is not None
                else json.dumps(item, ensure_ascii=False)[:2000])

        return Record(
            external_id=self._identity(item, title, index),
            text=f"{self.label} — {title}\n\n{body}",
            title=title,
            url=str(_dig(item, self.app.get("url_field") or "") or ""),
            source_updated_at=str(
                _dig(item, self.app.get("updated_field") or "") or ""),
            # The change detector is the whole record: a generic REST endpoint
            # publishes no `updated_at` we can rely on, so "did this change" is
            # "does it serialise differently".
            fingerprint=fingerprint(json.dumps(item, sort_keys=True,
                                               default=str)),
            extra={"app": self.app["id"]}, raw=item)

    def _identity(self, item: dict, title: str, index: int) -> str:
        """The vendor's own id for this record.

        **A position is the fallback and it is a poor one**, so it says so: a
        record identified by where it appeared in a list changes identity the
        moment the list reorders, which turns one updated record into one
        tombstone and one new memory. `id_field` is what a user should set, and
        the Custom app form asks for it.
        """
        named = self.app.get("id_field")
        if named:
            found = _dig(item, named)
            if found is not None and str(found).strip():
                return str(found)
        for guess in ("id", "uuid", "key", "number", "slug"):
            found = item.get(guess)
            if found is not None and str(found).strip():
                return str(found)
        return f"{title[:60]}#{index}"

    def _page(self, cursor: str) -> Page:
        """One request, as a page of records."""
        data = self._request(cursor)
        items = _dig(data, self.app.get("items_path", ""))
        if isinstance(items, dict):        # a single object → a list of one
            items = [items]
        if not isinstance(items, list):
            raise ValueError(
                "no list found — check 'items path' (e.g. data.results)")
        base = self._offset
        self._offset += len(items)
        records = [self._record(item, base + n) for n, item in enumerate(items)]
        next_cursor = ""
        if isinstance(data, dict):
            next_cursor = cursor_of(data, *(
                (self.app.get("next_field"),) if self.app.get("next_field")
                else ("next", "next_page", "next_cursor", "nextPageToken")))
        return Page(records=records, next_cursor=next_cursor)

    def sync(self, *, max_items: int = 300, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        """One pass, through the shared engine.

        What this connector used to do by hand and no longer does: catch its
        own HTTP errors into two sentences (`errors.py` classifies every
        status), read exactly one page and call it the dataset
        (`pagination.walk` pages with a loop guard), hold every record in a
        list before ingesting any (`engine` commits per page), and ingest with
        no identity at all (`resources` supersedes rather than accumulating).
        """
        ready, reason = self.is_configured()
        if not ready:
            result = SyncResult(connector=self.name)
            result.errors.append(reason)
            return self._finish(result)

        self._offset = 0
        # `_finish` still runs, so `connector_state` keeps being written and
        # every existing reader of it — the Connectors endpoint, the UI, the
        # scheduler summary — carries on working unchanged. Additive first:
        # the new tables are beside the old row, not instead of it.
        return self._finish(engine.run(
            engine.Plan(
                connector=self.name, manifest=self.manifest(),
                resource_type="record", fetch=self._page,
                ingest=self._ingest,
                connection_id=self.connection().id,
                budget=limit or max_items,
                # **Never sweeps.** A custom endpoint is whatever the user
                # pointed at — a search, a filtered feed, one page of a
                # changelog — and nothing tells us it is a complete
                # enumeration. Sweeping a filtered listing tombstones
                # everything outside the filter.
                sweeps_deletions=False),
            cancel=cancel, progress=progress, full_history=full_history))

    def _ingest(self, record: Record, source: SourceRef) -> str:
        from ..brain import get_brain

        out = get_brain().ingest(record.text, kind="record",
                                 title=record.title, fast=True,
                                 **source.ingest_kwargs())
        ids = out.get("memory_ids") or []
        return str(ids[0]) if ids else ""
