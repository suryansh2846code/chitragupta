"""A connector the user points at an exported file.

Two of the health sources reachable from a Mac are not APIs at all. Apple Health
has none — HealthKit is iOS-only. Google Fit's was deprecated in May 2024, closed
to new signups the same day, and is being shut down; its replacements are
Health Connect, which is Android-only and on-device, and the Google Health API,
which is Fitbit's. Neither is a thing a macOS app can call.

What both platforms do offer is an export the user asks for and receives as a
file. Reading a file they handed us is the most local-first shape there is:
nothing leaves the machine and no account is involved.

The two importers differ only in how they parse. Everything around the parse is
the same and was written twice before this existed — remembering which file was
chosen, saying something useful when it has moved, turning refusals into
something the user can read, and not doubling a year of data on the second
import. That is the part that drifts when it is copied, so it lives here and
each source implements one method.

Adding a third — Strava, Cronometer, a Fitbit takeout — is a `_read`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..log import get_logger
from ..metrics import log_many
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, SyncStrategy

log = get_logger(__name__)


class ExportConnector(Connector):
    """Reads measurements out of a file the user exported and chose."""

    #: How to get the export, in the user's terms. Shown when nothing is set up
    #: yet — a connector that only says "not configured" is a dead end.
    SETUP_HINT = "choose your export file"
    #: What to say when the file is real but is not the export we expected.
    NOT_OURS = "That file is not the export this connector reads."

    #: The file only changes when the user exports a new one. Re-parsing it
    #: every thirty minutes to find nothing new is the opposite of what the
    #: background loop is for.
    auto_sync = False
    incremental = False
    #: A file the user exported and handed us. No account, nothing leaves.
    auth_method = AuthMethod.LOCAL
    sync_strategy = SyncStrategy.MANUAL
    #: **`measurement`, not `record`.** These write to `../metrics.py` and never
    #: to the brain — tens of thousands of readings as memories would cost every
    #: agent a second of recall per turn, forever (`/CLAUDE.md`, Measurements).
    #: The resource being its own word is what keeps that boundary legible from
    #: the manifest rather than only from the code.
    capabilities = caps("read:measurement")

    def is_configured(self) -> tuple[bool, str]:
        return (True, "") if self._remembered() else (False, self.SETUP_HINT)

    # ── which file ───────────────────────────────────────────────────────
    def _remembered(self) -> str:
        state = self.store.get_connector_state(self.name) or {}
        path = str(state.get("cursor") or "")
        return path if path and Path(path).exists() else ""

    def _remember(self, path: str) -> None:
        self.store.set_connector_state(self.name, cursor=str(path))

    # ── the pass ─────────────────────────────────────────────────────────
    def sync(self, *, path: str | None = None, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)

        chosen = str(path or "").strip() or self._remembered()
        if not chosen:
            result.errors.append(self.SETUP_HINT)
            return self._finish(result)

        source = Path(chosen).expanduser()
        if not source.exists():
            result.errors.append(
                "That export is no longer where it was. Choose it again, or "
                "export a fresh one.")
            return self._finish(result)

        try:
            readings, stopped = self._read(source, cancel=cancel, progress=progress)
        except ValueError as exc:
            # Raised by a `_read` that looked inside and found the wrong thing.
            result.errors.append(str(exc) or self.NOT_OURS)
            return self._finish(result)
        except Exception as exc:
            result.errors.append(f"The export could not be read: {str(exc)[:160]}")
            return self._finish(result)

        if stopped:
            result.cancelled = True
            result.detail = "stopped"
            return self._finish(result)

        if not readings:
            result.detail = "nothing recognised"
            result.errors.append(
                "No measurements we track were in that export. It may be from "
                "a device that records nothing yet.")
            return self._finish(result)

        outcome = log_many(readings, source=self.name)
        added = outcome["stored"]
        result.added = added
        result.skipped = max(0, len(readings) - added)
        result.detail = (f"{added} new reading(s) from {len(readings)} in the "
                         f"export")
        # A reading we could not read is named, not dropped in silence. An
        # import that quietly stored two thirds of the file would look exactly
        # like one that worked.
        for why, count in outcome["refused"].items():
            result.errors.append(f"{count} reading(s) skipped - {why}")
        self._remember(str(source))
        return self._finish(result)

    def _read(self, source: Path, *, cancel=None,
              progress=None) -> tuple[list[dict], bool]:
        """Every reading in the file, and whether we were stopped partway.

        Raise `ValueError` with a sentence for the user when the file is not
        the export this connector reads.
        """
        raise NotImplementedError
