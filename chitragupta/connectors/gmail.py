"""Gmail connector — ingests recent email context (read-only)."""
from __future__ import annotations

import base64
import re
from dataclasses import replace
from datetime import UTC
from typing import Any

from ..log import get_logger
from . import engine
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, PaginationStrategy, SyncStrategy
from .engine import Record
from .google_auth import get_credentials, google_ready
from .pagination import Page, cursor_of
from .provenance import SourceRef

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


def _safe_filename(name: object) -> str:
    """An attachment name that cannot become a second header.

    `x.pdf"\\r\\nBcc: attacker@evil.test` in a `Content-Disposition` is the
    classic header injection, and this name reaches us from a model reasoning
    about somebody else's email.

    Python's `add_header` does refuse it — by raising `HeaderParseError` when
    the message is serialised, several frames away from anything that could
    say what went wrong. That is a guard, not an answer: the user sees a
    traceback string where a sentence should be. So the name is cleaned here,
    where the reason is visible, and the library's refusal stays as the
    backstop it should have been all along.

    Directories go too. The name is a label on a part, not a path, and
    `../../etc/passwd` as a filename is a suggestion to whatever opens it.
    """
    text = str(name or "").replace("\\", "/")
    text = text.split("/")[-1]
    text = "".join(c for c in text if c.isprintable() and c not in '"\r\n')
    return text.strip() or "attachment"


def _received_at(msg: dict) -> str:
    """When Gmail says the message arrived, as an ISO instant.

    Empty when `internalDate` is missing or unreadable — which a caller must
    treat as "no date", never as "today". Dating a message by when we read it
    makes every message from a first sync look like it arrived this morning.
    """
    raw = msg.get("internalDate")
    if not raw:
        return ""
    from datetime import datetime
    try:
        return datetime.fromtimestamp(int(raw) / 1000, UTC).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


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
    provider = "google"
    auto_sync = True
    incremental = True
    #: Per-page checkpoints: a pass that dies at message 1,900 resumes there.
    resumable = True
    auth_method = AuthMethod.OAUTH2
    sync_strategy = SyncStrategy.TIMESTAMP
    pagination = PaginationStrategy.NEXT_TOKEN
    #: The scopes in `google_auth.SCOPES` this connector actually spends. Named
    #: so a health check can say *"signed in, but not allowed to send"* rather
    #: than letting the first send fail at the vendor, in front of the user.
    required_scopes = (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    )
    #: Every write here is a method on this class, reachable only through a
    #: confirmed action in `actions.py::REGISTRY`. `delete:draft` is the one
    #: destructive verb, and it is destructive about something nobody has seen
    #: — which is why `create_draft` can be GREEN while this cannot.
    capabilities = caps(
        "read:email", "search:email", "read:thread",
        "create:draft", "delete:draft",
        "archive:email", "create:label",
        "send:email",
    )
    limits = Limits(requests=240, per_seconds=60.0, concurrency=3,
                    page_size=100, records_per_sync=600)

    def is_configured(self) -> tuple[bool, str]:
        return google_ready()

    # ── the pass ─────────────────────────────────────────────────────────
    #
    # Gmail is the connector the engine's `hydrate` stage exists for.
    # `messages.list` answers with ids and thread ids — no subject, no sender,
    # no date — so the body is a second request *per message*. Reading every
    # body and letting a content hash throw the duplicates away meant 600
    # requests every half hour to keep almost nothing.
    #
    # Now the keep-or-skip decision is made from the id alone, before any body
    # is fetched. Which works here for a reason specific to email: **a message
    # is immutable.** Nobody edits a received email, so once we have read one,
    # the id is sufficient to know we still have it. That is why the fingerprint
    # below is the id itself rather than a digest of anything.
    #
    # Labels *do* change, and that is deliberately not treated as the message
    # changing: an archived email is the same email, and folding labels into the
    # fingerprint would re-read and re-store the entire mailbox every time the
    # user tidied their inbox.

    def _query(self, *, resume: str | None, full_history: bool) -> str:
        """The search this pass runs.

        Kept as its own method because it is the **window**, and the window is
        what makes resuming safe: `_finish` only moves the watermark on a clean
        pass, so a pass that failed halfway rebuilds the identical query and the
        page token stored against it is still good.
        """
        from ..config import get_settings

        if full_history:
            return "-in:spam -in:trash"
        if resume:
            # Gmail's `after:` takes whole seconds and is inclusive-ish, so the
            # overlap `since()` already applied is what keeps a message sent in
            # the same second from slipping through.
            return f"after:{_to_epoch(resume)} -in:spam -in:trash"
        return (f"newer_than:{get_settings().gmail_recent_days}d "
                f"-in:spam -in:trash")

    def _page(self, service: Any, query: str, size: int, cursor: str) -> Page:
        """One page of the listing — ids only, which is all Gmail offers here."""
        listing = (service.users().messages()
                   .list(userId="me", q=query, pageToken=cursor or None,
                         maxResults=min(500, max(1, size))).execute())
        return Page(
            records=[Record(external_id=str(m["id"]),
                            # The id *is* the change detector: a received email
                            # is never edited. See the note above.
                            fingerprint=str(m["id"]),
                            extra={"thread_id": m.get("threadId", "")})
                     for m in listing.get("messages", []) or []
                     if m.get("id")],
            next_cursor=cursor_of(listing, "nextPageToken"))

    def _hydrate(self, service: Any, record: Record) -> Record:
        """The second request, made only for a message we are keeping."""
        msg = (service.users().messages()
               .get(userId="me", id=record.external_id, format="full").execute())
        headers = {h["name"].lower(): h.get("value", "")
                   for h in msg.get("payload", {}).get("headers", [])
                   if h.get("name")}
        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "")
        body = _extract_body(msg.get("payload", {})).strip() or msg.get("snippet", "")
        return replace(
            record,
            text=(f"From: {sender}\nSubject: {subject}\n\n{body[:4000]}"
                  if body else ""),
            title=subject,
            url=f"https://mail.google.com/mail/#all/{record.external_id}",
            source_updated_at=_received_at(msg),
            extra={**record.extra, "from": sender})

    def _ingest(self, record: Record, source: SourceRef) -> str:
        """One message into the brain, with where it came from attached.

        `store.add` rather than `brain.ingest`, which is what this connector has
        always done: a mailbox is bulk, and the background enricher picks the
        rows up from `graphed=0` without paying for a per-message extraction.
        """
        mem = self.store.add(text=record.text, kind="email", title=record.title,
                             **source.ingest_kwargs())
        return mem.id if mem else ""

    def sync(self, *, query: str | None = None, max_results: int | None = None,
             since: str | None = None, limit: int | None = None,
             full_history: bool = False, cancel=None, progress=None,
             interactive: bool = True, **_: Any) -> SyncResult:
        """Bounded by default: recent mail. `full_history=True` is the escape
        hatch that reads the whole archive.

        What this no longer does by hand: page the listing into one list before
        touching any of it, fetch every body to find out which ones are new,
        turn a `googleapiclient` error into `str(exc)`, and lose the whole pass
        when the connection drops at message 1,900.
        """
        from ..config import get_settings

        result = SyncResult(connector=self.name)
        service = self._service(result, interactive)
        if service is None:
            return self._finish(result)

        s = get_settings()
        # Stamped before any fetch: mail that arrives *while* this runs must be
        # caught by the next pass, not fall just behind the new watermark.
        started = self.now()
        resume = since if since is not None else self.since(full_history=full_history)
        search = query or self._query(resume=resume, full_history=full_history)
        budget = (limit or max_results
                  or (s.gmail_max if full_history else s.gmail_recent_max))

        outcome = engine.run(
            engine.Plan(
                connector=self.name, manifest=self.manifest(),
                resource_type="email",
                fetch=lambda cursor: self._page(service, search, budget, cursor),
                hydrate=lambda record: self._hydrate(service, record),
                ingest=self._ingest,
                connection_id=self.connection().id,
                budget=budget,
                # **Never sweeps.** The query is a *window* — `after:` or
                # `newer_than:90d` — not an enumeration of the mailbox. Sweeping
                # it would tombstone every message older than the window, which
                # is almost all of them.
                sweeps_deletions=False),
            cancel=cancel, progress=progress, full_history=full_history)

        scope = ("all mail" if full_history else "new mail" if resume
                 else f"last {s.gmail_recent_days}d")
        outcome.detail = f"{scope} — {outcome.detail}"
        # The watermark still lives here, in `connector_state`, and still moves
        # only on a clean pass. That is what keeps a resumed pass rebuilding the
        # same query, which is what keeps its stored page token valid.
        outcome.cursor = started
        return self._finish(outcome)

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

    # ── composing ────────────────────────────────────────────────────────
    #
    # One builder for every outbound message, because a draft, a send, a reply
    # and a forward differ only in which headers they carry and where the
    # result is posted. Four `MIMEText` blocks would be four places to fix the
    # day one of them needs an attachment — which is the day that arrived.

    @staticmethod
    def _compose(to: str, subject: str, body: str, *,
                 cc: str = "", attachments: list | None = None,
                 headers: dict[str, str] | None = None) -> str:
        """A base64url message, ready for `send` or `drafts.create`.

        `MIMEText` alone cannot carry a file, and `MIMEMultipart` around a
        single part is a heavier message for no reason — so the shape follows
        the content rather than being one or the other always.
        """
        import base64
        from email.mime.text import MIMEText

        if attachments:
            from email import encoders
            from email.mime.base import MIMEBase
            from email.mime.multipart import MIMEMultipart

            msg: Any = MIMEMultipart()
            msg.attach(MIMEText(body))
            for item in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(item["data"])
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment",
                                filename=_safe_filename(item.get("name")))
                msg.attach(part)
        else:
            msg = MIMEText(body)

        if to:
            msg["to"] = to
        if cc:
            msg["cc"] = cc
        msg["subject"] = subject
        for key, value in (headers or {}).items():
            if value:
                msg[key] = value
        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    @staticmethod
    def _send_refusal(exc: Exception) -> dict:
        """Google's permission refusal, translated into the thing to do."""
        m = str(exc)
        if "insufficient" in m.lower() or "scope" in m.lower() or "403" in m:
            return {"ok": False, "error": "Gmail needs re-authorization to "
                    "SEND. Reconnect Google (Connectors → Gmail → Reconnect) "
                    "and approve the send permission, then try again.",
                    "reauth": True}
        return {"ok": False, "error": m[:200]}

    def send_email(self, to: str, subject: str, body: str,
                   interactive: bool = False, *, cc: str = "",
                   attachments: list | None = None,
                   thread_id: str = "", headers: dict | None = None) -> dict:
        """Send an email (WRITE). Only ever called after user confirmation."""
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected"}
        try:
            raw = self._compose(to, subject, body, cc=cc,
                                attachments=attachments, headers=headers)
            payload: dict[str, Any] = {"raw": raw}
            if thread_id:
                # Without this Gmail files the reply as a new conversation, and
                # the recipient sees an orphan beside the thread it answers.
                payload["threadId"] = thread_id
            sent = service.users().messages().send(
                userId="me", body=payload).execute()
            return {"ok": True, "id": sent.get("id"),
                    "thread_id": sent.get("threadId", thread_id),
                    "detail": f"Email sent to {to}"}
        except Exception as exc:
            return self._send_refusal(exc)

    def create_draft(self, to: str, subject: str, body: str,
                     interactive: bool = False, *, cc: str = "",
                     attachments: list | None = None,
                     thread_id: str = "", headers: dict | None = None) -> dict:
        """Put a message in Drafts (WRITE) without sending it.

        The rung the product skipped. Every other outbound path here is
        send-or-nothing, which makes *preparing* an email cost the same
        approval as sending one — so an agent that could have filled the
        drafts folder overnight instead queued a decision for the morning.

        A draft reaches nobody, so it needs no permitted recipient and no
        allow-list; `actions.REGISTRY` declares it GREEN for that reason. The
        `gmail.modify` scope already covers `drafts.create`, so this asks for
        nothing the user has not already granted.
        """
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected"}
        try:
            raw = self._compose(to, subject, body, cc=cc,
                                attachments=attachments, headers=headers)
            message: dict[str, Any] = {"raw": raw}
            if thread_id:
                message["threadId"] = thread_id
            made = service.users().drafts().create(
                userId="me", body={"message": message}).execute()
            where = f" to {to}" if to else ""
            return {"ok": True, "id": made.get("id"),
                    "message_id": (made.get("message") or {}).get("id", ""),
                    "thread_id": (made.get("message") or {}).get("threadId", thread_id),
                    "detail": f"Draft saved{where}"}
        except Exception as exc:
            return self._maybe_scope_error(exc, {})

    def draft_exists(self, draft_id: str, interactive: bool = False) -> dict:
        """Read a draft back, so "saved" is checked rather than assumed."""
        if not draft_id:
            return {"verified": False}
        service = self._service(None, interactive)
        if service is None:
            return {"verified": False}
        try:
            got = service.users().drafts().get(
                userId="me", id=draft_id, format="minimal").execute()
        except Exception as exc:                       # pragma: no cover - network
            log.debug("could not verify draft %s: %s", draft_id, exc)
            return {"verified": False}
        return {"verified": bool(got.get("id"))}

    def delete_draft(self, draft_id: str, interactive: bool = False) -> dict:
        """Discard a draft. The inverse of `create_draft`, and nothing more.

        Gmail treats deleting an absent draft as an error; that is reported as
        success, because the user asked for it to be gone and it is gone.
        """
        if not draft_id:
            return {"ok": False, "error": "no draft to discard"}
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected"}
        try:
            service.users().drafts().delete(userId="me", id=draft_id).execute()
            return {"ok": True, "detail": "Draft discarded"}
        except Exception as exc:
            m = str(exc)
            if "404" in m or "notFound" in m:
                return {"ok": True, "detail": "That draft was already gone"}
            return self._maybe_scope_error(exc, {})

    def thread_reply_state(self, thread_id: str,
                           interactive: bool = False) -> dict:
        """Has anybody answered this thread since we last wrote in it?

        The missing half of a follow-up. An open loop saying "waiting on Rahul"
        stays open forever, because nothing in this app ever looked to see
        whether Rahul replied — so the user gets chased about a thing that was
        settled a week ago, which is worse than not being chased at all.

        "We" is the signed-in Google address. Comparing against that rather
        than against a list of our sent ids means a reply sent from the user's
        phone counts as them answering too, which is what a person means by
        "did I get a reply".

        Returns `{answered, last_from, last_at, waiting_days}`. Never raises:
        an unreadable thread is reported as unknown, not as unanswered — the
        difference matters, because unanswered is what triggers a chase.
        """
        blank = {"answered": None, "last_from": "", "last_at": "",
                 "waiting_days": 0}
        if not thread_id:
            return blank
        service = self._service(None, interactive)
        if service is None:
            return blank
        try:
            thread = service.users().threads().get(
                userId="me", id=thread_id, format="metadata",
                metadataHeaders=["From", "Date"]).execute()
        except Exception as exc:
            log.debug("could not read thread %s: %s", thread_id, exc)
            return blank

        messages = thread.get("messages") or []
        if not messages:
            return blank
        last = messages[-1]
        found = {h.get("name", "").lower(): h.get("value", "")
                 for h in (last.get("payload") or {}).get("headers", [])}
        sender = found.get("from", "")

        from .google_auth import connected_email
        mine = (connected_email(fetch=False) or "").lower()
        # Substring rather than equality: `From` is "Dana <dana@x.test>", and
        # an address is the part of it that identifies anybody.
        answered = bool(mine) and mine not in sender.lower()

        waiting = 0
        stamp = last.get("internalDate")
        if stamp:
            from datetime import datetime
            when = datetime.fromtimestamp(int(stamp) / 1000, tz=UTC)
            waiting = max(0, (datetime.now(UTC) - when).days)
        return {"answered": answered, "last_from": sender,
                "last_at": str(stamp or ""), "waiting_days": waiting}

    def thread_headers(self, thread_id: str, interactive: bool = False) -> dict:
        """What a reply to this thread has to carry to land inside it.

        `threadId` alone is enough for *Gmail* to file the reply correctly, and
        not enough for anybody else: a recipient on another mail client threads
        on `In-Reply-To` and `References`, and without them the reply arrives as
        a new conversation next to the one it answers.

        Reads the LAST message in the thread, because that is what is being
        replied to — the first one is where the conversation started, which is
        a different message and usually the wrong `In-Reply-To`.
        """
        if not thread_id:
            return {}
        service = self._service(None, interactive)
        if service is None:
            return {}
        try:
            thread = service.users().threads().get(
                userId="me", id=thread_id, format="metadata",
                metadataHeaders=["Message-ID", "References", "Subject", "From"],
            ).execute()
        except Exception as exc:
            log.debug("could not read thread %s: %s", thread_id, exc)
            return {}
        messages = thread.get("messages") or []
        if not messages:
            return {}
        last = messages[-1]
        found = {h.get("name", "").lower(): h.get("value", "")
                 for h in (last.get("payload") or {}).get("headers", [])}
        message_id = found.get("message-id", "")
        references = " ".join(
            x for x in (found.get("references", ""), message_id) if x).strip()
        return {"in_reply_to": message_id, "references": references,
                "subject": found.get("subject", ""), "from": found.get("from", "")}

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

    def message_labels(self, ids: list[str],
                       interactive: bool = False) -> dict:
        """Which labels each message carries now (READ).

        For verifying `mail_triage`. `batchModify` returning 200 says Gmail
        accepted the batch; it does not say the twelve emails left the inbox —
        and "archived twelve" is a claim about the user's mailbox, which they
        will notice is wrong before we do.

        `format="minimal"` fetches label ids and nothing else: a verification
        pass must not re-download the bodies it just archived.
        """
        wanted = [str(i).strip() for i in (ids or []) if str(i).strip()]
        if not wanted:
            return {"ok": False, "error": "No messages to read."}
        service = self._service(None, interactive)
        if service is None:
            return {"ok": False, "error": "Gmail not connected"}
        out: dict[str, list[str]] = {}
        for message_id in wanted[:BATCH_LIMIT]:
            try:
                got = service.users().messages().get(
                    userId="me", id=message_id, format="minimal").execute()
            except Exception:
                # One unreadable message is not a failed verification of the
                # rest. It is recorded as unknown, and the caller decides.
                continue
            out[message_id] = list((got or {}).get("labelIds") or [])
        return {"ok": True, "labels": out}

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
