"""Gmail connector — ingests recent email context (read-only)."""
from __future__ import annotations

import base64
import re
from datetime import UTC
from typing import Any

from ..log import get_logger
from .base import Connector, SyncResult
from .google_auth import get_credentials, google_ready

log = get_logger(__name__)

#: Gmail's own cap on one `batchModify`. Named so the limit is visible rather
#: than discovered as a 400 from Google.
BATCH_LIMIT = 1000


def _decode(data: str) -> str:
    # Gmail returns base64url body data that MAY omit '=' padding, which
    # urlsafe_b64decode rejects ("Incorrect padding"). Re-pad before decoding
    # so a common, valid message doesn't crash the whole sync.
    data = data or ""
    data += "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def html_to_text(html: str) -> str:
    """Strip HTML/CSS/scripts from an email body → readable plain text."""
    html = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)                 # remaining tags
    # decode a few common entities
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&zwnj;", "")):
        html = html.replace(a, b)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n\s*\n+", "\n\n", html)
    return html.strip()


def _looks_html(t: str) -> bool:
    return bool(re.search(r"<[a-z/][^>]*>", t) or
                re.search(r"\{[^{}]*(margin|padding|font|px|color)[^{}]*\}", t))


def _extract_body(payload: dict) -> str:
    """Best readable body: prefer text/plain, else strip text/html."""
    plain, html = _walk_body(payload)
    body = plain.strip() or html_to_text(html)
    # safety net: some senders stuff HTML/CSS into the "plain" part
    if _looks_html(body):
        body = html_to_text(body)
    return body


def _walk_body(payload: dict) -> tuple[str, str]:
    """Return (text/plain, text/html) found anywhere in the MIME tree."""
    plain, html = "", ""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if data:
        if mime == "text/plain":
            plain += _decode(data)
        elif mime == "text/html":
            html += _decode(data)
    for part in payload.get("parts", []) or []:
        p, h = _walk_body(part)
        plain += p
        html += h
    return plain, html


def _to_epoch(stamp: str) -> int:
    """An ISO watermark as whole seconds, for Gmail's `after:` operator."""
    from datetime import datetime

    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


class GmailConnector(Connector):
    name = "gmail"
    label = "Gmail"
    auto_sync = True
    incremental = True

    def is_configured(self) -> tuple[bool, str]:
        return google_ready()

    def sync(self, *, query: str | None = None, max_results: int | None = None,
             since: str | None = None, limit: int | None = None,
             full_history: bool = False, cancel=None, progress=None,
             interactive: bool = True, **_: Any) -> SyncResult:
        """Bounded by default: sync recent mail (fast, lean). Pass
        full_history=True to pull the whole archive (the escape hatch).

        On a second pass only mail newer than the last watermark is requested.
        The background loop runs every 30 minutes and the default window is 90
        days, so without this it re-downloaded up to 600 messages every half
        hour to discard nearly all of them on a content-hash collision.
        """
        result = SyncResult(connector=self.name)
        service = self._service(result, interactive)
        if service is None:
            return self._finish(result)

        from ..config import get_settings
        s = get_settings()
        # Stamped before any fetch: mail that arrives while this runs must be
        # caught by the next pass, not fall just behind the new watermark.
        started = self.now()
        resume = since if since is not None else self.since(full_history=full_history)
        if query is None:
            if full_history:
                query = "-in:spam -in:trash"
            elif resume:
                # Gmail's `after:` takes whole seconds and is inclusive-ish, so
                # the overlap `since()` already applied is what keeps a message
                # sent in the same second from slipping through.
                query = f"after:{_to_epoch(resume)} -in:spam -in:trash"
            else:
                query = f"newer_than:{s.gmail_recent_days}d -in:spam -in:trash"
        max_results = (limit or max_results
                       or (s.gmail_max if full_history else s.gmail_recent_max))
        try:
            messages = self._list(service, query, max_results)
            self.each_guarded(
                messages, result,
                lambda meta: 1 if self._ingest_message(service, meta) else 0,
                cancel=cancel, progress=progress)
            if full_history:
                scope = "all mail"
            elif resume:
                scope = "new mail"
            else:
                scope = f"last {s.gmail_recent_days}d"
            result.detail = result.detail or f"{scope}, {len(messages)} messages"
            result.cursor = started
        except Exception as exc:
            result.errors.append(str(exc))
            result.detail = "sync failed"
        return self._finish(result)

    def search_and_ingest(self, terms: str, max_results: int = 8,
                          interactive: bool = False) -> list[str]:
        """On-demand: live Gmail search (full-text + operators) that ingests
        matching messages so a specific/older email is fetched only when asked."""
        terms = (terms or "").strip()
        if not terms:
            return []
        service = self._service(None, interactive)
        if service is None:
            return []
        try:
            q = f"({terms}) -in:spam -in:trash"
            messages = self._list(service, q, max_results)
            found = []
            for meta in messages:
                title = self._ingest_message(service, meta, return_title=True)
                if title:
                    found.append(title)
            return found
        except Exception:
            return []

    # ── helpers ──────────────────────────────────────────────────────────
    def send_email(self, to: str, subject: str, body: str,
                   interactive: bool = False) -> dict:
        """Send an email (WRITE). Only ever called after user confirmation."""
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected"}
        try:
            import base64
            from email.mime.text import MIMEText
            msg = MIMEText(body)
            msg["to"] = to
            msg["subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            sent = service.users().messages().send(
                userId="me", body={"raw": raw}).execute()
            return {"ok": True, "id": sent.get("id"),
                    "detail": f"Email sent to {to}"}
        except Exception as exc:
            m = str(exc)
            if "insufficient" in m.lower() or "scope" in m.lower() or "403" in m:
                return {"ok": False, "error": "Gmail needs re-authorization to "
                        "SEND. Reconnect Google (Connectors → Gmail → Reconnect) "
                        "and approve the send permission, then try again.",
                        "reauth": True}
            return {"ok": False, "error": m[:200]}

    def message_sent_at(self, message_id: str, interactive: bool = False) -> dict:
        """When Gmail says this message actually went out.

        The verify rung. `send_email` returning `ok` means the API accepted the
        request, which is not the same claim as "it is in your Sent folder" —
        and the difference is exactly what a user wants to know when they are
        deciding whether to write the thing again. So the id is read back and
        Gmail's own `internalDate` is what the card reports.

        Never raises. A verification that fails is reported as unverified, not
        as a failed send: the mail may well have gone, and telling somebody
        their email failed when it did not is the worse error of the two.
        """
        if not message_id:
            return {"verified": False}
        service = self._service(None, interactive)
        if service is None:
            return {"verified": False}
        try:
            got = service.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["Date"]).execute()
        except Exception as exc:                       # pragma: no cover - network
            log.debug("could not verify message %s: %s", message_id, exc)
            return {"verified": False}
        stamp = got.get("internalDate")
        if not stamp:
            return {"verified": True, "at": ""}
        from datetime import datetime
        when = datetime.fromtimestamp(int(stamp) / 1000).astimezone()
        return {"verified": True, "at": when.isoformat(),
                "in_sent": "SENT" in (got.get("labelIds") or ["SENT"])}

    # ── addressing, reading and changing existing mail ───────────────────
    #
    # The three below are what an agent needs to *act* on an inbox rather than
    # only talk about it. `sync`/`search_and_ingest` pull text into the brain
    # and return prose, which is right for recall and useless for triage: prose
    # has no message id, so there is nothing to archive.

    def list_inbox(self, query: str = "in:inbox", max_results: int = 20,
                   interactive: bool = False) -> dict:
        """Messages matching `query`, WITH their ids. Read-only.

        The id is the whole point. Everything that changes a message addresses
        it by id, and until this existed an agent had no way to name one.
        """
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected", "messages": []}
        try:
            metas = self._list(service, query, max(1, min(100, max_results)))
            rows = []
            for meta in metas:
                msg = (service.users().messages()
                       .get(userId="me", id=meta["id"], format="metadata",
                            metadataHeaders=["From", "Subject", "Date"]).execute())
                headers = {h["name"].lower(): h.get("value", "")
                           for h in msg.get("payload", {}).get("headers", [])
                           if h.get("name")}
                labels = msg.get("labelIds", []) or []
                rows.append({
                    "id": msg.get("id", ""),
                    "thread_id": msg.get("threadId", ""),
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", "(no subject)"),
                    "date": headers.get("date", ""),
                    "snippet": msg.get("snippet", ""),
                    "unread": "UNREAD" in labels,
                    "starred": "STARRED" in labels,
                    "in_inbox": "INBOX" in labels,
                })
            return {"ok": True, "messages": rows}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200], "messages": []}

    def read_thread(self, thread_id: str, max_messages: int = 20,
                    interactive: bool = False) -> dict:
        """A whole conversation, oldest first, with each message's body.

        A search returns the messages that matched, scattered and out of order.
        A reply is only answerable against what came before it, so an agent
        reading one matched message is reading the end of an argument.
        """
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected", "messages": []}
        try:
            thread = (service.users().threads()
                      .get(userId="me", id=thread_id, format="full").execute())
            out = []
            for msg in (thread.get("messages") or [])[:max_messages]:
                headers = {h["name"].lower(): h.get("value", "")
                           for h in msg.get("payload", {}).get("headers", [])
                           if h.get("name")}
                body = _extract_body(msg.get("payload", {})).strip()
                out.append({
                    "id": msg.get("id", ""),
                    "from": headers.get("from", ""),
                    "to": headers.get("to", ""),
                    "date": headers.get("date", ""),
                    "subject": headers.get("subject", ""),
                    "body": body or msg.get("snippet", ""),
                })
            # Gmail returns a thread in order already; sorting by the internal
            # timestamp would re-derive it from a field we did not ask for.
            return {"ok": True, "thread_id": thread_id, "messages": out,
                    "count": len(thread.get("messages") or [])}
        except Exception as exc:
            return self._maybe_scope_error(exc, {"messages": []})

    def modify_messages(self, ids: list[str], add: list[str] | None = None,
                        remove: list[str] | None = None,
                        interactive: bool = False) -> dict:
        """Apply one label change to many messages at once (WRITE).

        `batchModify` is one request for the whole set, which is what makes
        "archive these twelve" a single approved act rather than twelve.
        """
        ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
        if not ids:
            return {"ok": False, "error": "No messages to change."}
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected"}
        try:
            (service.users().messages().batchModify(
                userId="me",
                body={"ids": ids[:BATCH_LIMIT],
                      "addLabelIds": list(add or []),
                      "removeLabelIds": list(remove or [])}).execute())
            return {"ok": True, "count": len(ids[:BATCH_LIMIT])}
        except Exception as exc:
            return self._maybe_scope_error(exc, {})

    def ensure_label(self, name: str, interactive: bool = False) -> dict:
        """The id of a user label, creating it if the user has none by that name."""
        wanted = (name or "").strip()
        if not wanted:
            return {"ok": False, "error": "A label needs a name."}
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected"}
        try:
            existing = (service.users().labels().list(userId="me").execute()
                        .get("labels", []))
            for label in existing:
                if str(label.get("name", "")).lower() == wanted.lower():
                    return {"ok": True, "id": label.get("id"), "created": False}
            made = (service.users().labels().create(
                userId="me",
                body={"name": wanted, "labelListVisibility": "labelShow",
                      "messageListVisibility": "show"}).execute())
            return {"ok": True, "id": made.get("id"), "created": True}
        except Exception as exc:
            return self._maybe_scope_error(exc, {})

    @staticmethod
    def _maybe_scope_error(exc: Exception, extra: dict) -> dict:
        """Translate Google's permission refusal into the one thing to do about it.

        A token issued before we asked for `gmail.modify` fails here and nowhere
        else, so this is where the user learns they need to reconnect — with
        `reauth` set, which the frontend already renders as a Reconnect button.
        """
        message = str(exc)
        lowered = message.lower()
        if "insufficient" in lowered or "scope" in lowered or "403" in message:
            from .google_auth import NEEDS_MODIFY_SCOPE
            return {"ok": False, "error": NEEDS_MODIFY_SCOPE, "reauth": True, **extra}
        return {"ok": False, "error": message[:200], **extra}

    def _service(self, result, interactive):
        try:
            from googleapiclient.discovery import build  # lazy
        except ImportError:
            if result is not None:
                result.errors.append("pip install .[gmail] to use Gmail")
            return None
        try:
            creds = get_credentials(interactive=interactive)
            return build("gmail", "v1", credentials=creds, cache_discovery=False)
        except Exception as exc:
            if result is not None:
                result.errors.append(str(exc))
            return None

    def _list(self, service, query, max_results) -> list[dict]:
        messages: list[dict] = []
        page_token = None
        while len(messages) < max_results:
            resp = (service.users().messages()
                    .list(userId="me", q=query, pageToken=page_token,
                          maxResults=min(500, max_results - len(messages))).execute())
            messages += resp.get("messages", [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return messages

    def _ingest_message(self, service, meta, return_title=False):
        msg = (service.users().messages()
               .get(userId="me", id=meta["id"], format="full").execute())
        headers = {h["name"].lower(): h.get("value", "")
                   for h in msg.get("payload", {}).get("headers", [])
                   if h.get("name")}
        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "")
        body = _extract_body(msg.get("payload", {})).strip()
        snippet = body or msg.get("snippet", "")
        if not snippet:
            return None if return_title else False
        text = f"From: {sender}\nSubject: {subject}\n\n{snippet[:4000]}"
        event_date = None
        try:
            if msg.get("internalDate"):
                from datetime import datetime
                event_date = datetime.fromtimestamp(
                    int(msg["internalDate"]) / 1000, UTC).date().isoformat()
        except (ValueError, TypeError, OSError):
            event_date = None       # bad internalDate → just omit the date
        mem = self.store.add(
            text=text, source=self.name, kind="email", title=subject,
            uri=f"https://mail.google.com/mail/#all/{meta['id']}",
            event_date=event_date,
            metadata={"from": sender, "message_id": meta["id"], "date": event_date})
        if return_title:
            return subject
        return bool(mem)
