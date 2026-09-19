"""Google Calendar connector — ingests recent + upcoming events (read-only)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .base import Connector, SyncResult
from .google_auth import get_credentials, google_ready


class GoogleCalendarConnector(Connector):
    name = "gcal"
    label = "Google Calendar"
    auto_sync = True
    # The window is already bounded around today, so a watermark would only
    # hide edits to events that have not moved in time.
    incremental = False

    def is_configured(self) -> tuple[bool, str]:
        return google_ready()

    def create_event(self, title: str, start: str, end: str | None = None,
                     description: str = "", attendees: list[str] | None = None,
                     interactive: bool = False) -> dict:
        """Create a calendar event (WRITE). Only after user confirmation.
        `start`/`end` are ISO datetimes; if end is missing, defaults to +1h."""
        try:
            from datetime import datetime, timedelta

            from googleapiclient.discovery import build
        except ImportError:
            return {"ok": False, "error": "pip install .[gdrive] for Calendar"}
        try:
            creds = get_credentials(interactive=interactive)
            service = build("calendar", "v3", credentials=creds,
                            cache_discovery=False)
            if not end:
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    end = (dt + timedelta(hours=1)).isoformat()
                except Exception:
                    end = start
            body = {
                "summary": title,
                "description": description,
                "start": {"dateTime": start},
                "end": {"dateTime": end},
            }
            if attendees:
                body["attendees"] = [{"email": a} for a in attendees]
            ev = service.events().insert(calendarId="primary", body=body).execute()
            return {"ok": True, "id": ev.get("id"),
                    "detail": f"Event '{title}' created",
                    "link": ev.get("htmlLink")}
        except Exception as exc:
            m = str(exc)
            if "insufficient" in m.lower() or "scope" in m.lower() or "403" in m:
                return {"ok": False, "error": "Calendar needs re-authorization to "
                        "create events. Reconnect Google and approve the calendar "
                        "permission, then try again.", "reauth": True}
            return {"ok": False, "error": m[:200]}

    def delete_event(self, event_id: str, interactive: bool = False) -> dict:
        """Remove an event (WRITE). The inverse of `create_event`, and nothing
        more than that.

        Here so that creating an event is *undoable*. A calendar invite is the
        action most worth taking back — it has already emailed every attendee
        by the time anyone notices the date is wrong, and until now the only
        remedy was to open Calendar and do it by hand.

        Google treats deleting an already-deleted event as an error; that is
        reported as success, because the user asked for it to be gone and it
        is gone.
        """
        if not event_id:
            return {"ok": False, "error": "no event to remove"}
        try:
            from googleapiclient.discovery import build
        except ImportError:
            return {"ok": False, "error": "pip install .[gdrive] for Calendar"}
        try:
            creds = get_credentials(interactive=interactive)
            service = build("calendar", "v3", credentials=creds,
                            cache_discovery=False)
            service.events().delete(calendarId="primary",
                                    eventId=event_id).execute()
            return {"ok": True, "detail": "Event removed"}
        except Exception as exc:
            m = str(exc)
            if "410" in m or "deleted" in m.lower() or "404" in m:
                return {"ok": True, "detail": "Event was already gone"}
            return {"ok": False, "error": m[:200]}

    def event_exists(self, event_id: str, interactive: bool = False) -> dict:
        """Read an event back, so "created" is checked rather than assumed."""
        if not event_id:
            return {"verified": False}
        try:
            from googleapiclient.discovery import build
        except ImportError:
            return {"verified": False}
        try:
            creds = get_credentials(interactive=interactive)
            service = build("calendar", "v3", credentials=creds,
                            cache_discovery=False)
            ev = service.events().get(calendarId="primary",
                                      eventId=event_id).execute()
        except Exception:
            return {"verified": False}
        if (ev.get("status") or "") == "cancelled":
            return {"verified": False}
        return {"verified": True, "at": (ev.get("start") or {}).get("dateTime", ""),
                "link": ev.get("htmlLink", "")}

    def sync(self, *, days_back: int = 180, days_ahead: int = 180,
             max_results: int = 250, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None,
             interactive: bool = True, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        try:
            from googleapiclient.discovery import build  # lazy
        except ImportError:
            result.errors.append("pip install .[gdrive] to use the Calendar connector")
            return self._finish(result)

        try:
            creds = get_credentials(interactive=interactive)
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            now = datetime.now(UTC)
            time_min = (now - timedelta(days=days_back)).isoformat()
            time_max = (now + timedelta(days=days_ahead)).isoformat()
            events = (
                service.events().list(
                    calendarId="primary", timeMin=time_min, timeMax=time_max,
                    maxResults=max_results, singleEvents=True, orderBy="startTime",
                ).execute()
            ).get("items", [])
            from ..brain import get_brain
            brain = get_brain()

            def ingest(ev) -> int:
                summary = ev.get("summary", "(no title)")
                start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
                end = ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date", "")
                where = ev.get("location", "")
                attendees = ", ".join(
                    a.get("email", "") for a in ev.get("attendees", []) or [])
                desc = (ev.get("description", "") or "")[:1000]
                text = (f"Event: {summary}\nWhen: {start} → {end}"
                        + (f"\nWhere: {where}" if where else "")
                        + (f"\nWith: {attendees}" if attendees else "")
                        + (f"\n\n{desc}" if desc else ""))
                out = brain.ingest(
                    text, source=self.name, kind="event", title=summary,
                    uri=ev.get("htmlLink"), fast=True,
                    event_date=(start[:10] if start else None),
                    metadata={"start": start, "event_id": ev.get("id")},
                )
                return out["memories"]

            self.each_guarded(events[:limit] if limit else events, result, ingest,
                              cancel=cancel, progress=progress)
            result.detail = result.detail or (
                f"{len(events)} events ({days_back}d back, {days_ahead}d ahead)")
        except Exception as exc:
            result.errors.append(str(exc))
            result.detail = "sync failed"
        return self._finish(result)
