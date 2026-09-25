"""iMessage connector — ingests recent messages from the local macOS chat.db.

Fully local, no auth — but macOS requires the running app (Terminal/Python) to
have Full Disk Access to read ~/Library/Messages/chat.db.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import permissions
from .base import Connector, SyncResult

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
# Apple stores message dates as ns since 2001-01-01; convert to epoch seconds.
APPLE_EPOCH = 978307200


class IMessageConnector(Connector):
    name = "imessage"
    runs_on_device = True
    label = "iMessage"
    auto_sync = True
    # Deliberately NOT incremental. A thread is folded into a single memory, so
    # fetching "only new messages" would store a fragment of a conversation
    # rather than the conversation — worse than re-reading a bounded window of a
    # local SQLite file, which is cheap. The 90-day window is the bound.
    incremental = False
    platforms = ("darwin",)

    def is_configured(self) -> tuple[bool, str]:
        if not CHAT_DB.exists():
            return False, "no Messages database (macOS only)"
        try:
            con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
            con.execute("SELECT 1 FROM message LIMIT 1")
            con.close()
            self.fix = None
            return True, ""
        except Exception:
            # The database is there and we cannot read it, which on macOS means
            # exactly one thing. Said in the user's words, with the button the
            # UI pairs with `fix` — not "grant access to your terminal", which
            # names something a shipped .app does not have.
            self.fix = permissions.FULL_DISK_ACCESS
            return False, permissions.full_disk_access_reason("Messages")

    def sync(self, *, days: int = 90, max_messages: int = 1500,
             min_thread: int = 3, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        ready, reason = self.is_configured()
        if not ready:
            result.errors.append(reason)
            return self._finish(result)
        try:
            con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            cutoff_ns = (self._now_epoch() - days * 86400 - APPLE_EPOCH) * 1_000_000_000
            rows = con.execute(
                """
                SELECT h.id AS handle, m.text AS text, m.is_from_me AS mine,
                       m.date AS date
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.text IS NOT NULL AND m.text != '' AND m.date > ?
                ORDER BY m.date ASC
                LIMIT ?
                """,
                (cutoff_ns, max_messages * 3),
            ).fetchall()
            con.close()

            # group into per-contact threads, keep meaningful ones
            threads: dict[str, list[str]] = defaultdict(list)
            for r in rows:
                who = "Me" if r["mine"] else (r["handle"] or "Them")
                threads[r["handle"] or "unknown"].append(f"{who}: {r['text']}")

            from ..brain import get_brain
            brain = get_brain()
            def ingest(entry) -> int:
                handle, msgs = entry
                if len(msgs) < min_thread:
                    return 0
                text = f"iMessage thread with {handle}:\n" + "\n".join(msgs[-60:])
                out = brain.ingest(
                    text, source=self.name, kind="message",
                    title=f"Messages with {handle}", fast=True,
                    metadata={"handle": handle, "count": len(msgs)},
                )
                return out["memories"]

            self.each_guarded(list(threads.items()), result, ingest,
                              cancel=cancel, progress=progress)
            result.detail = result.detail or (
                f"{len(threads)} conversations, last {days}d")
        except Exception as exc:
            result.errors.append(str(exc))
            result.detail = "sync failed"
        return self._finish(result)

    def _now_epoch(self) -> int:
        import time
        return int(time.time())
