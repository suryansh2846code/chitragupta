"""Notion connector — ingests pages the integration can access."""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..core.chunk import chunk_text
from .base import Connector, SyncResult


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
    auto_sync = True
    # Notion search has no "changed since" filter we can rely on; paging the
    # whole result set is the only honest option.
    incremental = False
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

    # ── writes ───────────────────────────────────────────────────────────
    #
    # Named methods, never part of `sync()`. Notion's own sharing model is the
    # outer gate here and it is a real one: an integration sees only pages
    # somebody explicitly connected it to, so "the agent could write anywhere
    # in my workspace" is not true even before our own allow-list.

    def _client(self) -> tuple[Any, str]:
        """(client, problem). Never raises — a missing token is a sentence."""
        token = get_settings().notion_token
        if not token:
            return None, "Notion is not connected."
        try:
            from notion_client import Client  # lazy
        except ImportError:                            # pragma: no cover
            return None, "The Notion library is not installed."
        try:
            return Client(auth=token), ""
        except Exception as exc:                       # pragma: no cover
            return None, str(exc)[:160]

    @staticmethod
    def _refusal(exc: Exception) -> dict:
        """Notion's "no", in the one sentence that says what to do about it.

        The 404 is the one worth spelling out: a page the integration has not
        been *connected to* is reported as missing, identically to a page that
        does not exist. Told "that page does not exist" a user goes looking
        for a typo; the actual fix is three clicks in Notion.
        """
        said = str(exc)
        if "401" in said or "unauthorized" in said.lower():
            return {"ok": False, "reauth": True, "error":
                    "Notion refused that — the integration secret is missing "
                    "or has been revoked. Paste a fresh one under Connectors."}
        if "404" in said or "not_found" in said.lower():
            return {"ok": False, "error":
                    "Notion cannot see that page. If it exists, open it in "
                    "Notion → ⋯ → Connections → Add, and this integration "
                    "will be able to reach it."}
        if "restricted" in said.lower() or "403" in said:
            return {"ok": False, "error":
                    "Notion says this integration may read that page but not "
                    "change it."}
        return {"ok": False, "error": said[:200]}

    @staticmethod
    def _paragraphs(text: str) -> list[dict]:
        """Plain text as Notion blocks, one per line.

        Notion rejects a rich-text run over 2000 characters with a validation
        error that names a JSON path, so long lines are split here rather than
        surfaced as an internal.
        """
        blocks: list[dict] = []
        for line in (text or "").split("\n"):
            for start in range(0, max(len(line), 1), 1900):
                chunk = line[start:start + 1900]
                blocks.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{
                        "type": "text", "text": {"content": chunk}}]},
                })
        return blocks

    def append_block(self, page_id: str, text: str) -> dict:
        """Add text to the end of a page (WRITE)."""
        page_id = (page_id or "").strip()
        text = (text or "").strip()
        if not page_id:
            return {"ok": False, "error": "Which Notion page?"}
        if not text:
            return {"ok": False, "error": "There is nothing to add."}

        notion, problem = self._client()
        if notion is None:
            return {"ok": False, "error": problem}
        try:
            made = notion.blocks.children.append(
                block_id=page_id, children=self._paragraphs(text))
        except Exception as exc:
            return self._refusal(exc)

        results = (made or {}).get("results") or []
        if not results:
            return {"ok": False, "error": "Notion did not add that."}
        # Every block id, so undo can remove exactly what we appended and
        # nothing that was already on the page.
        ids = [str(b.get("id") or "") for b in results if b.get("id")]
        return {"ok": True, "id": ids[0] if ids else "", "block_ids": ids,
                "detail": f"Added {len(ids)} paragraph(s) to that page"}

    def delete_blocks(self, block_ids: list[str]) -> dict:
        """Remove blocks we appended (WRITE) — the inverse of `append_block`.

        Notion's delete is a move to trash and is reversible in their UI, so
        this is a genuine undo rather than a destructive one.
        """
        ids = [b for b in (block_ids or []) if b]
        if not ids:
            return {"ok": False, "error": "There is nothing to remove."}
        notion, problem = self._client()
        if notion is None:
            return {"ok": False, "error": problem}
        removed = 0
        for block_id in ids:
            try:
                notion.blocks.delete(block_id=block_id)
                removed += 1
            except Exception as exc:
                # Partial is reported honestly rather than as success: the
                # user is told what is still on the page.
                if removed == 0:
                    return self._refusal(exc)
                return {"ok": True, "detail":
                        f"Removed {removed} of {len(ids)} — the rest are "
                        f"still on the page."}
        return {"ok": True, "detail": "Removed what I added"}

    def create_page(self, parent_id: str, title: str, text: str = "") -> dict:
        """Create a page under an existing one (WRITE).

        A parent is required. Notion has no "somewhere in the workspace", and
        an integration cannot create a top-level page — asking the user where
        it goes is the honest version of a choice we cannot make for them.
        """
        parent_id = (parent_id or "").strip()
        title = (title or "").strip()
        if not parent_id:
            return {"ok": False, "error":
                    "Which Notion page should this go under? Notion has no "
                    "loose pages — every new page lives inside another one."}
        if not title:
            return {"ok": False, "error": "A page needs a title."}

        notion, problem = self._client()
        if notion is None:
            return {"ok": False, "error": problem}
        try:
            made = notion.pages.create(
                parent={"page_id": parent_id},
                properties={"title": [{"type": "text",
                                       "text": {"content": title}}]},
                children=self._paragraphs(text) if text else [])
        except Exception as exc:
            return self._refusal(exc)

        page_id = str((made or {}).get("id") or "")
        if not page_id:
            return {"ok": False, "error": "Notion did not create that page."}
        return {"ok": True, "id": page_id,
                "url": str((made or {}).get("url") or ""),
                "detail": f"Created “{title}”"}

    def archive_page(self, page_id: str) -> dict:
        """Send a page to Notion's trash (WRITE) — the inverse of `create_page`."""
        if not page_id:
            return {"ok": False, "error": "That page cannot be found."}
        notion, problem = self._client()
        if notion is None:
            return {"ok": False, "error": problem}
        try:
            notion.pages.update(page_id=page_id, archived=True)
        except Exception as exc:
            return self._refusal(exc)
        return {"ok": True, "detail": "Moved it to Notion's trash"}

    def page_exists(self, page_id: str) -> dict:
        """Read one back, for rung 5."""
        if not page_id:
            return {"ok": False}
        notion, _ = self._client()
        if notion is None:
            return {"ok": False}
        try:
            page = notion.pages.retrieve(page_id=page_id)
        except Exception:
            return {"ok": False}
        return {"ok": not (page or {}).get("archived", False),
                "url": str((page or {}).get("url") or "")}
