"""Manual notes connector — direct user-entered facts/preferences."""
from __future__ import annotations

from typing import Any

from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, SyncStrategy


class NotesConnector(Connector):
    name = "notes"
    runs_on_device = True
    label = "Manual Notes"
    auto_sync = False         # written by hand; there is nothing to poll
    always_available = True
    auth_method = AuthMethod.LOCAL
    sync_strategy = SyncStrategy.MANUAL
    #: The note is the user's own text arriving in their own brain. There is no
    #: external system here at all, which is why there is no write capability:
    #: nothing this connector does changes anything outside this Mac.
    capabilities = caps("read:note")

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
