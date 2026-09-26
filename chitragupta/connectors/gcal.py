"""Google Calendar connector — ingests recent + upcoming events (read-only)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..log import get_logger, suppressed
from . import engine
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, PaginationStrategy
from .engine import Record
from .google_auth import get_credentials, google_ready
from .pagination import Page, cursor_of
from .provenance import SourceRef

log = get_logger(__name__)


def _shift_end(before: dict, new_start: str) -> str:
    """The new finish time, keeping the meeting the length it already was.

    "Move it to Friday afternoon" is a statement about when it begins. Reading
    it as "and end at the time it used to end" turns a one-hour call into a
    three-day one, or into a negative-length event Google refuses outright.
    """
    old_start = ((before.get("start") or {}).get("dateTime") or "")
    old_end = ((before.get("end") or {}).get("dateTime") or "")
    with suppressed("keeping an event the length it already was"):
        started = datetime.fromisoformat(old_start)
        ended = datetime.fromisoformat(old_end)
        moved = datetime.fromisoformat(new_start)
        return (moved + (ended - started)).isoformat()
    # No readable old pair — an all-day event, or a stored value we cannot
    # parse. An hour is the ordinary meeting and the card shows it either way.
    return (datetime.fromisoformat(new_start) + timedelta(hours=1)).isoformat() \
        if _parsable(new_start) else new_start


def _parsable(stamp: str) -> bool:
    with suppressed("checking a datetime is readable"):
        datetime.fromisoformat(stamp)
        return True
    return False


class GoogleCalendarConnector(Connector):
    name = "gcal"
    label = "Google Calendar"
    provider = "google"
    auto_sync = True
    auth_method = AuthMethod.OAUTH2
    pagination = PaginationStrategy.NEXT_TOKEN
    #: Per-page checkpoints. The window slides with today, so a resumed
    #: pass may find its stored page token stale — which the engine
    #: recovers from by reading the window again rather than failing
    #: forever.
    resumable = True
    required_scopes = (
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    )
    #: `cancel` and `delete` are both here and are not the same verb. Cancelling
    #: tells everybody on the event; deleting removes our copy. Both are
    #: destructive — neither has an inverse that is the same event — and
    #: `actions.py` makes the same distinction in its two RED entries.
    #:
    #: `read:calendar` is `free_busy`: it reads other people's availability,
    #: which is a reach into Google beyond the user's own events.
    capabilities = caps(
        "read:event", "read:calendar",
        "create:event", "update:event",
        "cancel:event", "delete:event",
    )
    limits = Limits(requests=300, per_seconds=60.0, concurrency=3,
                    page_size=250, records_per_sync=500)
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
        service, problem = self._client(interactive)
        if service is None:
            return {"ok": False, "error": problem}
        try:
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

    #: Google's own word for "email everybody about this". Named because the
    #: default is `none`, and a meeting silently moved is worse than one not
    #: moved at all: the organiser believes it is settled and every attendee is
    #: still holding the old slot.
    NOTIFY = "all"

    def _client(self, interactive: bool = False) -> tuple[Any, str]:
        """`(service, problem)` — one place the Calendar client is built.

        Returns the problem rather than swallowing it, because the two ways
        this fails need different sentences: a missing SDK is a developer
        install and a missing token is a user reconnecting, and "not
        connected" told to the first is a wrong answer.
        """
        try:
            from googleapiclient.discovery import build
        except ImportError:
            return None, "pip install .[gdrive] for Calendar"
        with suppressed("building the Google Calendar client"):
            creds = get_credentials(interactive=interactive)
            return build("calendar", "v3", credentials=creds,
                         cache_discovery=False), ""
        return None, "Google Calendar is not connected."

    def free_busy(self, emails: list[str], start: str, end: str,
                  interactive: bool = False) -> dict:
        """When each person is busy — and who we could not see at all.

        **The two must never be merged.** Google answers an unreadable calendar
        with an empty `busy` list and an `errors` entry beside it, so a caller
        that reads only `busy` sees somebody with no meetings all week. That is
        how an agent proposes Tuesday at 10 "because Rahul is free" when the
        truth is that Rahul's calendar is not shared with this account and
        nobody has any idea whether he is free.

        Sharing is the norm inside one Workspace domain and the exception
        outside it, so the unreadable case is the common one for anybody the
        user actually needs to arrange something with.

        Returns `{"busy": {email: [(from, to), …]}, "unreadable": [email, …]}`.
        """
        wanted = [e for e in dict.fromkeys(emails or []) if e]
        if not wanted:
            return {"busy": {}, "unreadable": []}
        service, problem = self._client(interactive)
        if service is None:
            log.debug("free/busy unavailable: %s", problem)
            return {"busy": {}, "unreadable": wanted}
        try:
            answer = service.freebusy().query(body={
                "timeMin": start, "timeMax": end,
                "items": [{"id": e} for e in wanted]}).execute()
        except Exception as exc:
            log.debug("free/busy query failed: %s", exc)
            return {"busy": {}, "unreadable": wanted}

        calendars = answer.get("calendars") or {}
        busy: dict[str, list[tuple[str, str]]] = {}
        unreadable: list[str] = []
        for email in wanted:
            entry = calendars.get(email)
            if entry is None or entry.get("errors"):
                unreadable.append(email)
                continue
            busy[email] = [(b.get("start", ""), b.get("end", ""))
                           for b in (entry.get("busy") or [])]
        return {"busy": busy, "unreadable": unreadable}

    def get_event(self, event_id: str, interactive: bool = False) -> dict:
        """One event as Google holds it, for showing or for putting back.

        `update_event` keeps what this returns so the change can be undone —
        a diff is only reversible if somebody wrote down the other side of it.
        """
        if not event_id:
            return {}
        service, problem = self._client(interactive)
        if service is None:
            log.debug("calendar unavailable: %s", problem)
            return {}
        with suppressed("reading a calendar event"):
            return service.events().get(calendarId="primary",
                                        eventId=event_id).execute() or {}
        return {}

    def update_event(self, event_id: str, *, title: str = "", start: str = "",
                     end: str = "", description: str | None = None,
                     location: str | None = None,
                     attendees: list[str] | None = None,
                     interactive: bool = False) -> dict:
        """Change an existing event (WRITE). Only after user confirmation.

        A **patch**, not a replace: an event carries conferencing links,
        recurrence, reminders and a dozen fields nobody named on the card, and
        sending a body built from the four things the user mentioned would
        quietly drop the Meet link off their client call.

        Moving the start without an end moves the end with it, keeping the
        meeting the same length. "Move it to Friday afternoon" is about when it
        begins; nobody means "and make it run until the old finish time".
        """
        if not event_id:
            return {"ok": False, "error": "no event to change"}
        service, problem = self._client(interactive)
        if service is None:
            return {"ok": False, "error": problem}

        before = self.get_event(event_id)
        if not before:
            return {"ok": False, "error": "That event is no longer there."}

        body: dict[str, Any] = {}
        if title:
            body["summary"] = title
        if description is not None:
            body["description"] = description
        if location is not None:
            body["location"] = location
        if attendees is not None:
            body["attendees"] = [{"email": a} for a in attendees]
        if start:
            body["start"] = {"dateTime": start}
            body["end"] = {"dateTime": end or _shift_end(before, start)}
        elif end:
            body["end"] = {"dateTime": end}
        if not body:
            return {"ok": False, "error": "nothing was asked to change"}

        try:
            after = service.events().patch(
                calendarId="primary", eventId=event_id, body=body,
                sendUpdates=self.NOTIFY).execute()
        except Exception as exc:
            return self._refusal(exc)
        return {"ok": True, "id": after.get("id", event_id),
                "before": before, "link": after.get("htmlLink", ""),
                "detail": f"“{after.get('summary') or 'Event'}” updated"
                          + (f" — now {start}" if start else "")}

    def restore_event(self, event_id: str, before: dict,
                      interactive: bool = False) -> dict:
        """Put an event back the way `update_event` found it.

        Patches back only the fields that were changed, from the snapshot taken
        before the change — so an undo cannot itself drop something nobody
        touched.
        """
        if not event_id or not before:
            return {"ok": False, "error": "there is nothing to put back"}
        service, problem = self._client(interactive)
        if service is None:
            return {"ok": False, "error": problem}
        body = {k: before[k] for k in
                ("summary", "description", "location", "start", "end",
                 "attendees") if k in before}
        try:
            service.events().patch(calendarId="primary", eventId=event_id,
                                   body=body, sendUpdates=self.NOTIFY).execute()
        except Exception as exc:
            return self._refusal(exc)
        return {"ok": True, "detail": "Put the event back as it was"}

    def cancel_event(self, event_id: str, interactive: bool = False) -> dict:
        """Cancel an event and tell the attendees (WRITE).

        Distinct from `delete_event`, which exists to undo a `create_event`
        nobody has seen yet and deliberately says nothing to anybody. This one
        is the user cancelling a real meeting, so it notifies — a cancellation
        no attendee hears about leaves them all holding the slot.
        """
        if not event_id:
            return {"ok": False, "error": "no event to cancel"}
        service, problem = self._client(interactive)
        if service is None:
            return {"ok": False, "error": problem}
        before = self.get_event(event_id)
        try:
            service.events().delete(calendarId="primary", eventId=event_id,
                                    sendUpdates=self.NOTIFY).execute()
        except Exception as exc:
            m = str(exc)
            if "410" in m or "404" in m or "deleted" in m.lower():
                return {"ok": True, "detail": "That event was already gone"}
            return self._refusal(exc)
        who = len(before.get("attendees") or [])
        return {"ok": True, "id": event_id,
                "detail": f"“{before.get('summary') or 'Event'}” cancelled"
                          + (f" — {who} attendee(s) told" if who else "")}

    @staticmethod
    def _refusal(exc: Exception) -> dict:
        m = str(exc)
        if "insufficient" in m.lower() or "scope" in m.lower() or "403" in m:
            return {"ok": False, "error": "Calendar needs re-authorization to "
                    "change events. Reconnect Google and approve the calendar "
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
        service, problem = self._client(interactive)
        if service is None:
            return {"ok": False, "error": problem}
        try:
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
        service, _ = self._client(interactive)
        if service is None:
            return {"verified": False}
        try:
            ev = service.events().get(calendarId="primary",
                                      eventId=event_id).execute()
        except Exception:
            return {"verified": False}
        if (ev.get("status") or "") == "cancelled":
            return {"verified": False}
        return {"verified": True, "at": (ev.get("start") or {}).get("dateTime", ""),
                "link": ev.get("htmlLink", "")}

    # ── the pass ─────────────────────────────────────────────────────────
    #
    # Calendar is the **third** shape on the shared engine, and the one that
    # needs no `hydrate`: `events.list` answers with whole events — title,
    # start, end, location, attendees, description — so the listing *is* the
    # content. Gmail and Drive both pay a second request per record; this one
    # does not, and saying so is worth more than adding a stage that would only
    # copy.
    #
    # Two things it gets that it did not have:
    #
    # * **More than one page.** It asked for `maxResults=250` and read whatever
    #   came back, so a calendar with more than 250 events across the ±360-day
    #   window silently lost the rest — no error, no mention, just fewer events
    #   than the user has.
    # * **A cancelled meeting stops being current.** Cancellations were
    #   invisible: the event simply stayed in the brain and agents kept citing
    #   it. Google says so explicitly with `status: "cancelled"` once
    #   `showDeleted` is on, which is a fact rather than the inference a sweep
    #   would need.
    #
    # And what it deliberately keeps: `incremental = False`. The window is
    # already bounded around today, so a watermark would only hide edits to
    # events that have not moved in time — which is most edits.

    def _window(self, *, days_back: int, days_ahead: int) -> tuple[str, str]:
        now = datetime.now(UTC)
        return ((now - timedelta(days=days_back)).isoformat(),
                (now + timedelta(days=days_ahead)).isoformat())

    def _page(self, service: Any, time_min: str, time_max: str, size: int,
              cursor: str) -> Page:
        """One page of events, complete enough to store as they are."""
        listing = (service.events().list(
            calendarId="primary", timeMin=time_min, timeMax=time_max,
            maxResults=min(2500, max(1, size)), pageToken=cursor or None,
            # Recurring series expanded into instances, so each instance has its
            # own id and its own time — which is what a person means by "my
            # events", and what makes the identity stable per occurrence.
            singleEvents=True, orderBy="startTime",
            # Cancellations arrive as events with `status: "cancelled"` rather
            # than as absences. Without this a cancelled meeting is simply
            # missing, and absence is not something a bounded window may read as
            # deletion.
            showDeleted=True,
        ).execute())
        return Page(
            records=[self._record(ev) for ev in listing.get("items", []) or []
                     if ev.get("id")],
            next_cursor=cursor_of(listing, "nextPageToken"))

    def _record(self, ev: dict) -> Record:
        summary = ev.get("summary", "(no title)")
        start = (ev.get("start", {}).get("dateTime")
                 or ev.get("start", {}).get("date", ""))
        end = (ev.get("end", {}).get("dateTime")
               or ev.get("end", {}).get("date", ""))
        where = ev.get("location", "")
        attendees = ", ".join(a.get("email", "")
                              for a in ev.get("attendees", []) or [])
        description = (ev.get("description", "") or "")[:1000]
        text = (f"Event: {summary}\nWhen: {start} → {end}"
                + (f"\nWhere: {where}" if where else "")
                + (f"\nWith: {attendees}" if attendees else "")
                + (f"\n\n{description}" if description else ""))
        return Record(
            external_id=str(ev["id"]),
            text=text, title=summary,
            url=ev.get("htmlLink") or "",
            # An event is mutable — moved, renamed, re-invited — so the change
            # detector is Google's own `updated`, not the id.
            source_updated_at=ev.get("updated") or "",
            fingerprint=ev.get("updated") or "",
            deleted=(ev.get("status") == "cancelled"),
            extra={"start": start, "end": end,
                   "attendees": attendees, "location": where},
            raw=ev)

    def _ingest(self, record: Record, source: SourceRef) -> str:
        from ..brain import get_brain

        kwargs = source.ingest_kwargs()
        start = str(record.extra.get("start") or "")
        # The date of an event is when it *happens*, which is not when it was
        # last edited — so the start overrides what provenance derived from
        # `updated`. Getting this wrong files next month's meeting under today.
        if start:
            kwargs["event_date"] = start[:10]
            kwargs["event_time"] = start
        out = get_brain().ingest(record.text, kind="event", title=record.title,
                                 fast=True, **kwargs)
        ids = out.get("memory_ids") or []
        return str(ids[0]) if ids else ""

    def sync(self, *, days_back: int = 180, days_ahead: int = 180,
             max_results: int = 250, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None,
             interactive: bool = True, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        try:
            from googleapiclient.discovery import build  # lazy
        except ImportError:
            result.errors.append(
                "pip install .[gdrive] to use the Calendar connector")
            return self._finish(result)

        try:
            creds = get_credentials(interactive=interactive)
            service = build("calendar", "v3", credentials=creds,
                            cache_discovery=False)
        except Exception as exc:
            # Signing in is something the user does; a failed sync is something
            # the app retries. Reporting one as the other sends them nowhere
            # useful.
            from .errors import classify_exception
            problem = classify_exception(self.name, exc, label=self.label)
            result.errors.append(problem.message)
            result.detail = "not signed in"
            return self._finish(result)

        time_min, time_max = self._window(days_back=days_back,
                                          days_ahead=days_ahead)
        budget = limit or max_results
        outcome = engine.run(
            engine.Plan(
                connector=self.name, manifest=self.manifest(),
                resource_type="event",
                fetch=lambda cursor: self._page(service, time_min, time_max,
                                                budget, cursor),
                # **No hydrate.** The listing carries whole events; a stage here
                # would fetch what we already have.
                ingest=self._ingest,
                connection_id=self.connection().id,
                budget=budget,
                # **Never sweeps**, and it has no need to: cancellations arrive
                # as `status: "cancelled"` above. Sweeping a ±180-day window
                # would tombstone every event that merely aged out of it, which
                # is every event, eventually.
                sweeps_deletions=False),
            cancel=cancel, progress=progress, full_history=full_history)

        outcome.detail = (f"{outcome.detail} "
                          f"({days_back}d back, {days_ahead}d ahead)")
        return self._finish(outcome)
