"""Manual notes connector — direct user-entered facts/preferences."""
from __future__ import annotations

from typing import Any

from .base import Connector, SyncResult


class NotesConnector(Connector):
    name = "notes"
    runs_on_device = True
    label = "Manual Notes"
    auto_sync = False         # written by hand; there is nothing to poll
    always_available = True

    def sync(self, *, text: str = "", title: str | None = None,
             kind: str = "note", tags: list[str] | None = None,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        """One note per call. Still honours cancel and progress, because a
        connector that opts out of half the contract is one the generic suites
        cannot check — and the next single-record connector would copy it."""
        result = SyncResult(connector=self.name)
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            result.detail = "cancelled"
            return self._finish(result)
        mem = self.store.add(
            text=text, source=self.name, kind=kind, title=title, tags=tags or []
        )
        if mem:
            result.added = 1
            result.detail = f"stored note {mem.id[:8]}"
        else:
            result.skipped = 1
            result.detail = "empty or duplicate note"
        if progress is not None:
            progress(1, 1, self.label)
        return self._finish(result)
