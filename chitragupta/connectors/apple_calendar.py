"""Apple Calendar connector — reads events off disk, NO OAuth.

macOS Calendar stores each event as a local .ics file under ~/Library/Calendars.
Read them directly (Full Disk Access) — no Google/iCloud auth needed.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import permissions
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, SyncStrategy


def _epoch_or_none(stamp: str | None) -> float | None:
    """An ISO watermark as a POSIX timestamp, for comparing against `st_mtime`."""
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


CAL_ROOT = Path.home() / "Library" / "Calendars"


def _unfold(text: str) -> str:
    # ICS folds long lines with CRLF + space/tab
    return re.sub(r"\r?\n[ \t]", "", text)


def _fmt_dt(raw: str) -> str:
    # 20260901T150000Z or 20260901 → ISO-ish date
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", raw)
    if not m:
        return raw
    y, mo, d, h, mi = m.groups()
    return f"{y}-{mo}-{d}" + (f"T{h}:{mi}" if h else "")


def parse_ics(text: str) -> dict | None:
    text = _unfold(text)
    fields = {}
    for key in ("SUMMARY", "LOCATION", "DESCRIPTION"):
        m = re.search(rf"^{key}(?:;[^:]*)?:(.*)$", text, re.M)
        if m:
            fields[key] = m.group(1).strip().replace("\\,", ",").replace("\\n", " ")
    ds = re.search(r"^DTSTART(?:;[^:]*)?:(.*)$", text, re.M)
    de = re.search(r"^DTEND(?:;[^:]*)?:(.*)$", text, re.M)
    if not fields.get("SUMMARY"):
        return None
    return {"summary": fields["SUMMARY"],
            "start": _fmt_dt(ds.group(1).strip()) if ds else "",
            "end": _fmt_dt(de.group(1).strip()) if de else "",
            "location": fields.get("LOCATION", ""),
            "description": fields.get("DESCRIPTION", "")}


class AppleCalendarConnector(Connector):
    name = "apple_calendar"
    runs_on_device = True
    label = "Apple Calendar"
    auto_sync = True
    incremental = True
    platforms = ("darwin",)
    auth_method = AuthMethod.LOCAL
    sync_strategy = SyncStrategy.TIMESTAMP
    #: Reads the local `.ics` store. Writes go through `gcal`, which is the
    #: account the invitations actually come from.
    capabilities = caps("read:event", "read:calendar")
    limits = Limits(concurrency=1, records_per_sync=500)

    def is_configured(self) -> tuple[bool, str]:
        if not CAL_ROOT.exists():
            return False, "no Apple Calendar data (add a calendar in the Calendar app)"
        try:
            next(CAL_ROOT.rglob("*.ics"), None)
            self.fix = None
            return True, ""
        except PermissionError:
            self.fix = permissions.FULL_DISK_ACCESS
            return False, permissions.full_disk_access_reason("Calendar")

    def sync(self, *, max_events: int = 500, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        ready, reason = self.is_configured()
        if not ready:
            result.errors.append(reason)
            return self._finish(result)
        started = self.now()
        resume = since if since is not None else self.since(full_history=full_history)
        cutoff = _epoch_or_none(resume)
        max_events = limit or max_events
        try:
            from ..brain import get_brain
            brain = get_brain()
            files = list(CAL_ROOT.rglob("*.ics"))
            scanned = len(files)
            if cutoff is not None:
                files = [f for f in files if f.stat().st_mtime >= cutoff]

            # The whole per-event body runs under `each_guarded`, not just the
            # parse: an ingest that throws used to abort the pass, losing every
            # event after it while the ones before stayed committed — a brain
            # that looks populated and is silently half a calendar (H2).
            def ingest(fp) -> int:
                ev = parse_ics(fp.read_text(errors="ignore"))
                if not ev:
                    return 0
                text = (f"Event: {ev['summary']}\nWhen: {ev['start']}"
                        + (f" → {ev['end']}" if ev["end"] else "")
                        + (f"\nWhere: {ev['location']}" if ev["location"] else "")
                        + (f"\n\n{ev['description']}" if ev["description"] else ""))
                out = brain.ingest(
                    text, source=self.name, kind="event",
                    title=ev["summary"], fast=True,
                    event_date=(ev["start"][:10] if ev["start"] else None))
                return out["memories"]

            self.each_guarded(files[:max_events], result, ingest,
                              cancel=cancel, progress=progress)
            result.detail = result.detail or (
                f"{len(files)} changed of {scanned} local events"
                if cutoff is not None else f"scanned {scanned} local events")
            result.cursor = started
        except Exception as exc:
            result.errors.append(str(exc))
            result.detail = "sync failed"
        return self._finish(result)
