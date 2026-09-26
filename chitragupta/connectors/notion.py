"""Notion connector — ingests pages the integration can access."""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..core.chunk import chunk_text
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, PaginationStrategy


def _rich_text(items: list[dict]) -> str:
    return "".join(i.get("plain_text", "") for i in items or [])


def _block_text(block: dict) -> str:
    btype = block.get("type", "")
    data = block.get(btype, {})
    if isinstance(data, dict) and "rich_text" in data:
        text = _rich_text(data["rich_text"])
        if btype.startswith("heading"):
            return f"\n{text}\n"
        if btype == "to_do":
            check = "x" if data.get("checked") else " "
            return f"- [{check}] {text}"
        if "list_item" in btype:
            return f"- {text}"
        return text
    return ""


class NotionConnector(Connector):
    name = "notion"
    label = "Notion"
    #: The vendor ships an MCP server. See `Connector.prefer_mcp`.
    prefer_mcp = "notion"
    auto_sync = True
    # Notion search has no "changed since" filter we can rely on; paging the
    # whole result set is the only honest option.
    incremental = False
    auth_method = AuthMethod.API_KEY
    #: Notion pages the whole result set within one sync (`start_cursor`), and
    #: carries no watermark across runs — which is why `incremental` is False
    #: and the pagination strategy is still declared.
    pagination = PaginationStrategy.CURSOR
    #: Reads only — the writes stood down with `prefer_mcp`. See `github.py`.
    capabilities = caps("read:page", "search:page")
    limits = Limits(requests=180, per_seconds=60.0, concurrency=2,
                    page_size=50, records_per_sync=300)
    secret_field = {
        "key": "NOTION_TOKEN",
        "label": "Notion integration secret",
        "placeholder": "ntn_… (or secret_…)",
        "help_url": "https://www.notion.so/my-integrations",
        "steps": [
            "Open notion.so/my-integrations → <b>New integration</b> (Internal), "
            "then copy the <b>Internal Integration Secret</b>.",
            "Paste it below and click Save.",
            "In Notion, open each page/database you want Chitragupta to read → "
            "<b>⋯ → Connections → Add</b> your integration.",
        ],
    }

    def is_configured(self) -> tuple[bool, str]:
        if get_settings().notion_token:
            return True, ""
        return False, "click setup to paste your Notion integration secret"

    def sync(self, *, page_size: int = 50, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        token = get_settings().notion_token
        if not token:
            result.errors.append("NOTION_TOKEN not set")
            return self._finish(result)
        try:
            from notion_client import Client  # lazy
        except ImportError:
            result.errors.append("pip install .[notion] to use the Notion connector")
            return self._finish(result)

        try:
            notion = Client(auth=token)
            search = notion.search(
                filter={"property": "object", "value": "page"},
                page_size=page_size,
            )
            pages = search.get("results", [])

            def ingest(page) -> int:
                title = self._page_title(page)
                text = self._page_text(notion, page["id"])
                if not text.strip():
                    return 0
                stored = 0
                for i, chunk in enumerate(chunk_text(text)):
                    mem = self.store.add(
                        text=chunk,
                        source=self.name,
                        kind="doc",
                        title=title if i == 0 else f"{title} (part {i + 1})",
                        uri=page.get("url"),
                        metadata={"page_id": page["id"], "chunk": i},
                    )
                    if mem:
                        stored += 1
                return stored

            self.each_guarded(pages[:limit] if limit else pages, result, ingest,
                              cancel=cancel, progress=progress)
            result.detail = result.detail or f"{len(pages)} pages"
        except Exception as exc:
            result.errors.append(str(exc))
            result.detail = "sync failed"
        return self._finish(result)

    def _page_title(self, page: dict) -> str:
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                return _rich_text(prop["title"]) or "Untitled"
        return "Untitled"

    def _page_text(self, notion, page_id: str) -> str:
        lines: list[str] = []
        cursor = None
        while True:
            resp = notion.blocks.children.list(
                block_id=page_id, start_cursor=cursor, page_size=100
            )
            for block in resp.get("results", []):
                t = _block_text(block)
                if t:
                    lines.append(t)
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return "\n".join(lines)
