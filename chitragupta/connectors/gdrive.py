"""Google Drive connector — ingests Docs / text / PDF files (read-only)."""
from __future__ import annotations

import io
from dataclasses import replace
from typing import Any

from ..core.chunk import chunk_text
from . import engine
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, PaginationStrategy, SyncStrategy
from .engine import Record
from .google_auth import get_credentials, google_ready
from .pagination import Page, cursor_of
from .provenance import SourceRef


# Which Drive mime types we know how to read, and how.
def _rfc3339(stamp: str) -> str:
    """An ISO watermark in the exact shape Drive's query language wants."""
    from datetime import UTC, datetime

    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


EXPORT_AS_TEXT = "application/vnd.google-apps.document"
PLAIN_TYPES = {"text/plain", "text/markdown", "text/csv"}
PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
# Google-native Slides/Sheets export to text too
GSLIDES = "application/vnd.google-apps.presentation"


class GoogleDriveConnector(Connector):
    name = "gdrive"
    label = "Google Drive"
    provider = "google"
    auto_sync = True
    incremental = True
    #: Per-page checkpoints: a pass that dies on file 800 resumes there.
    resumable = True
    auth_method = AuthMethod.OAUTH2
    sync_strategy = SyncStrategy.TIMESTAMP
    pagination = PaginationStrategy.NEXT_TOKEN
    #: `drive.file`, not `drive` — writes reach only files this app created.
    #: See the note in `google_auth.SCOPES`.
    required_scopes = (
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",
    )
    #: `trash_doc` is `archive:file`, not `delete:file`: Drive's bin is a place
    #: things come back from, and `_undo_drive_doc` is that inverse. Calling it
    #: destructive would make an action that IS reversible ask as though it
    #: were not.
    capabilities = caps(
        "read:file", "search:file", "download:file",
        "create:document", "archive:file",
        "share:document", "unshare:document",
    )
    limits = Limits(requests=600, per_seconds=60.0, concurrency=3,
                    page_size=100, records_per_sync=300)

    def is_configured(self) -> tuple[bool, str]:
        return google_ready()

    # ── the pass ─────────────────────────────────────────────────────────
    #
    # Drive is the second connector on the shared engine and it is the same
    # shape as Gmail: the listing carries names and modified times, and the
    # *content* is a separate download per file. This connector already knew
    # that and worked around it by hand — it built a set of
    # `(file_id, modifiedTime)` out of the whole memories table on every pass:
    #
    #     for row in self.store._conn.execute(
    #         "SELECT metadata, event_date FROM memories WHERE source=?", ...)
    #
    # which is a full scan of every Drive memory to answer a question a table
    # now answers in 6 µs, and which compared only the *date* — so a file edited
    # twice in one day read as unchanged.
    #
    # Two things are genuinely different from Gmail:
    #
    # * **A file is mutable.** So the fingerprint is `modifiedTime`, not the id.
    # * **One file becomes several memories.** A long document is chunked, so
    #   `_ingest` answers with a list and the identity table records all of them.
    #   Recording only the first is what would leave the rest orphaned when
    #   somebody asks to delete what Drive imported.

    #: Drive mime types this connector can read, as a query fragment.
    def _mime_filter(self) -> str:
        kinds = [EXPORT_AS_TEXT, PDF_TYPE, DOCX_TYPE, PPTX_TYPE, GSLIDES,
                 *PLAIN_TYPES]
        return "(" + " or ".join(f"mimeType='{m}'" for m in kinds) + ")"

    def _query(self, *, query: str | None, resume: str | None) -> str:
        """The search this pass runs — the **window**, in Gmail's sense.

        `_finish` only moves the watermark on a clean pass, so a pass that failed
        halfway rebuilds this identical query and the page token stored against
        it still means something.
        """
        if query:
            return query
        q = f"{self._mime_filter()} and trashed=false"
        if resume:
            # Filtered at Drive rather than locally, which is strictly better
            # than listing everything and discarding it here. RFC-3339 with a
            # 'Z', which is what the API expects.
            q += f" and modifiedTime > '{_rfc3339(resume)}'"
        return q

    def _page(self, service: Any, query: str, size: int, cursor: str) -> Page:
        """One page of the listing. Names and modified times, no content."""
        listing = (
            service.files()
            .list(q=query, pageSize=min(100, max(1, size)),
                  pageToken=cursor or None,
                  # Shared-with-me and Shared Drives included: a document
                  # somebody sent the user is exactly the one they will ask
                  # about.
                  includeItemsFromAllDrives=True, supportsAllDrives=True,
                  corpora="allDrives",
                  fields="nextPageToken,files(id,name,mimeType,webViewLink,"
                         "modifiedTime,owners(displayName))")
            .execute())
        records = []
        for f in listing.get("files", []) or []:
            if not f.get("id"):
                continue
            owners = f.get("owners") or []
            records.append(Record(
                external_id=str(f["id"]),
                title=f.get("name") or "",
                url=f.get("webViewLink") or "",
                source_updated_at=f.get("modifiedTime") or "",
                # **The full timestamp, not its date.** The hand-rolled check
                # this replaces compared `modifiedTime[:10]`, so a document
                # edited twice in one day was read as unchanged the second time.
                fingerprint=f.get("modifiedTime") or "",
                extra={"owner": owners[0].get("displayName", "") if owners
                       else "", "mime_type": f.get("mimeType") or ""},
                raw=f))
        return Page(records=records,
                    next_cursor=cursor_of(listing, "nextPageToken"))

    def _hydrate(self, service: Any, downloader_cls: Any,
                 record: Record) -> Record:
        """The download, made only for a file we are keeping.

        This is the expensive half — an export, or a chunked byte download of a
        PDF — and the whole reason `Plan.hydrate` exists. Before the engine it
        ran for every file in the window on every pass.
        """
        return replace(record, text=self._read_file(service, record.raw,
                                                    downloader_cls))

    def _ingest(self, record: Record, source: SourceRef) -> list[str]:
        """One file into the brain, as however many chunks it takes.

        Returns every id, so *delete what Drive imported* can actually remove a
        long document rather than its first page.
        """
        kwargs = source.ingest_kwargs()
        metadata = kwargs.pop("metadata", {})
        stored: list[str] = []
        for index, chunk in enumerate(chunk_text(record.text)):
            mem = self.store.add(
                text=chunk, kind="doc",
                title=record.title if index == 0
                      else f"{record.title} (part {index + 1})",
                # The chunk number rides beside the provenance rather than
                # inside it: which piece of a document this is belongs to the
                # record, not to where the record came from.
                metadata={**metadata, "chunk": index},
                **kwargs)
            if mem:
                stored.append(mem.id)
        return stored

    def sync(self, *, query: str | None = None, max_results: int | None = None,
             since: str | None = None, limit: int | None = None,
             full_history: bool = False, cancel=None, progress=None,
             interactive: bool = True, **_: Any) -> SyncResult:
        """One pass, through the shared engine.

        What this no longer does by hand: page every file into one list before
        reading any of it, scan the entire memories table to work out what it
        already has, compare modified *dates* rather than times, turn a
        `googleapiclient` error into `str(exc)`, and lose the whole pass when a
        download fails partway.
        """
        result = SyncResult(connector=self.name)
        started = self.now()
        resume = since if since is not None else self.since(full_history=full_history)
        try:
            from googleapiclient.discovery import build  # lazy
            from googleapiclient.http import MediaIoBaseDownload
        except ImportError:
            result.errors.append("pip install .[gdrive] to use the Drive connector")
            return self._finish(result)

        from ..config import get_settings
        budget = limit or max_results or get_settings().drive_max

        try:
            creds = get_credentials(interactive=interactive)
            service = build("drive", "v3", credentials=creds,
                            cache_discovery=False)
        except Exception as exc:
            # Sign-in failed, which is not a sync failure to retry — it is
            # something the user has to do. Classified so the row says which.
            from .errors import classify_exception
            problem = classify_exception(self.name, exc, label=self.label)
            result.errors.append(problem.message)
            result.detail = "not signed in"
            return self._finish(result)

        search = self._query(query=query, resume=resume)
        outcome = engine.run(
            engine.Plan(
                connector=self.name, manifest=self.manifest(),
                resource_type="file",
                fetch=lambda cursor: self._page(service, search, budget, cursor),
                hydrate=lambda record: self._hydrate(
                    service, MediaIoBaseDownload, record),
                ingest=self._ingest,
                connection_id=self.connection().id,
                budget=budget,
                # **Never sweeps.** The query filters by mime type and is
                # bounded by `drive_max`, so "not in these results" covers every
                # file this connector cannot read as well as every one past the
                # budget. Sweeping it would tombstone most of a Drive.
                sweeps_deletions=False),
            cancel=cancel, progress=progress, full_history=full_history)

        outcome.cursor = started
        return self._finish(outcome)

    def search_and_ingest(self, terms: str, max_files: int = 5,
                          interactive: bool = False) -> list[str]:
        """Live, targeted Drive search (name + full-text, across My Drive + shared)
        that ingests matching files ON DEMAND. Powers lazy loading: fetch a file
        only when the user asks for it, instead of syncing everything up front.
        Returns the titles of files it ingested."""
        terms = (terms or "").strip().replace("'", "")
        if not terms:
            return []
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
        except ImportError:
            return []
        try:
            creds = get_credentials(interactive=interactive)
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            q = (f"(name contains '{terms}' or fullText contains '{terms}') "
                 "and trashed=false and mimeType != "
                 "'application/vnd.google-apps.folder'")
            files = service.files().list(
                q=q, pageSize=max_files, includeItemsFromAllDrives=True,
                supportsAllDrives=True, corpora="allDrives",
                fields="files(id,name,mimeType,webViewLink,modifiedTime,"
                       "owners(displayName))").execute().get("files", [])
            from ..brain import get_brain
            brain = get_brain()
            # file_ids already in the brain → don't re-download, just report found
            import json as _json
            have = set()
            for row in self.store._conn.execute(
                "SELECT metadata FROM memories WHERE source=?", (self.name,)):
                try:
                    fid = _json.loads(row["metadata"] or "{}").get("file_id")
                except Exception:
                    fid = None
                if fid:
                    have.add(fid)
            found = []
            for f in files:
                found.append(f["name"])
                if f["id"] in have:
                    continue                     # already ingested — skip download
                try:
                    text = self._read_file(service, f, MediaIoBaseDownload)
                except Exception:
                    continue
                if not text.strip():
                    continue
                ev = (f.get("modifiedTime") or "")[:10] or None
                owner = (f.get("owners") or [{}])[0].get("displayName", "")
                brain.ingest(text, source=self.name, kind="doc", title=f["name"],
                             uri=f.get("webViewLink"), fast=True, event_date=ev,
                             metadata={"file_id": f["id"], "owner": owner,
                                       "on_demand": True})
            return found
        except Exception:
            return []

    def _read_file(self, service, f: dict, downloader_cls) -> str:
        mime = f["mimeType"]
        # Google-native docs/slides export straight to text
        if mime in (EXPORT_AS_TEXT, GSLIDES):
            data = service.files().export(
                fileId=f["id"], mimeType="text/plain"
            ).execute()
            return data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data

        buf = io.BytesIO()
        request = service.files().get_media(fileId=f["id"], supportsAllDrives=True)
        downloader = downloader_cls(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        raw = buf.getvalue()
        if mime == PDF_TYPE:
            return self._pdf_text(raw)
        if mime == DOCX_TYPE:
            return self._docx_text(raw)
        if mime == PPTX_TYPE:
            return self._pptx_text(raw)
        return raw.decode("utf-8", errors="ignore")

    def _pdf_text(self, raw: bytes) -> str:
        try:
            from pypdf import PdfReader  # lazy
        except ImportError:
            return ""
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    def _docx_text(self, raw: bytes) -> str:
        try:
            from docx import Document  # python-docx, lazy
        except ImportError:
            return ""
        doc = Document(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)

    def _pptx_text(self, raw: bytes) -> str:
        try:
            from pptx import Presentation  # python-pptx, lazy
        except ImportError:
            return ""
        prs = Presentation(io.BytesIO(raw))
        parts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    parts.append(shape.text_frame.text)
        return "\n".join(parts)

    # ── writes ───────────────────────────────────────────────────────────
    #
    # Named methods, never part of `sync()`. Everything here runs under
    # `drive.file`, which reaches only documents this app itself created —
    # so a write cannot touch anything that was already in the user's Drive,
    # and `share` cannot hand out a file we did not make.

    def _service(self, interactive: bool = False):
        """(service, problem). Never raises — a missing scope is a sentence."""
        from .google_auth import NEEDS_DRIVE_SCOPE, may_write_drive

        ready, why = google_ready()
        if not ready:
            return None, (why or "Google Drive is not connected.")
        if not may_write_drive():
            # Asked before anything is proposed, so the user never approves a
            # card that cannot work — the `gmail.modify` lesson.
            return None, NEEDS_DRIVE_SCOPE
        try:
            from googleapiclient.discovery import build  # lazy

            creds = get_credentials(interactive=interactive)
            if creds is None:
                return None, "Google Drive is not connected."
            return build("drive", "v3", credentials=creds,
                         cache_discovery=False), ""
        except Exception as exc:                       # pragma: no cover
            return None, str(exc)[:160]

    @staticmethod
    def _refusal(exc: Exception) -> dict:
        said = str(exc)
        if "403" in said or "insufficient" in said.lower():
            from .google_auth import NEEDS_DRIVE_SCOPE

            return {"ok": False, "reauth": True, "error": NEEDS_DRIVE_SCOPE}
        if "404" in said:
            return {"ok": False, "error":
                    "Drive cannot see that document. Chitragupta can only "
                    "reach files it created itself."}
        return {"ok": False, "error": said[:200]}

    def create_doc(self, title: str, text: str = "") -> dict:
        """Create a Google Doc in the user's Drive (WRITE).

        Reaches nobody: it lands in their own Drive and no one else can see
        it until they share it. That is why the action is green, and it is
        the same argument `create_draft` makes about the Drafts folder.
        """
        title = (title or "").strip()
        if not title:
            return {"ok": False, "error": "A document needs a title."}

        service, problem = self._service()
        if service is None:
            return {"ok": False, "error": problem}

        try:
            from googleapiclient.http import MediaIoBaseUpload  # lazy

            body = {"name": title, "mimeType": EXPORT_AS_TEXT}
            media = MediaIoBaseUpload(
                io.BytesIO((text or "").encode("utf-8")),
                mimetype="text/plain", resumable=False)
            # Uploaded as text/plain and converted on the way in, which is how
            # Drive turns a body into a real Doc rather than an attached file.
            made = service.files().create(
                body=body, media_body=media,
                fields="id, name, webViewLink").execute()
        except Exception as exc:
            return self._refusal(exc)

        file_id = str((made or {}).get("id") or "")
        if not file_id:
            return {"ok": False, "error": "Drive did not create that document."}
        return {"ok": True, "id": file_id,
                "url": str((made or {}).get("webViewLink") or ""),
                "detail": f"Created “{title}” in your Drive"}

    def trash_doc(self, file_id: str) -> dict:
        """Move a document we created to the Drive bin (WRITE).

        The inverse of `create_doc`. Trashed rather than deleted: Drive keeps
        it for thirty days and the user can put it back, which is what makes
        this an honest undo instead of a destructive one.
        """
        if not file_id:
            return {"ok": False, "error": "That document cannot be found."}
        service, problem = self._service()
        if service is None:
            return {"ok": False, "error": problem}
        try:
            service.files().update(fileId=file_id, body={"trashed": True}).execute()
        except Exception as exc:
            return self._refusal(exc)
        return {"ok": True, "detail": "Moved it to your Drive bin"}

    def doc_exists(self, file_id: str) -> dict:
        """Read one back, for rung 5."""
        if not file_id:
            return {"ok": False}
        service, _ = self._service()
        if service is None:
            return {"ok": False}
        try:
            found = service.files().get(
                fileId=file_id, fields="id, trashed, webViewLink").execute()
        except Exception:
            return {"ok": False}
        return {"ok": not (found or {}).get("trashed", False),
                "url": str((found or {}).get("webViewLink") or "")}

    def share(self, file_id: str, email: str = "", role: str = "reader",
              anyone: bool = False) -> dict:
        """Give somebody access to a document we created (WRITE).

        `anyone=True` is a public link and is deliberately a different kind of
        decision — see `actions.py`, which refuses to let a standing grant
        cover it. A named person can be allow-listed; "everybody on the
        internet" is not a recipient anybody can put on a list.
        """
        if not file_id:
            return {"ok": False, "error": "Which document?"}
        role = (role or "reader").strip().lower()
        if role not in ("reader", "commenter", "writer"):
            return {"ok": False, "error":
                    f"“{role}” is not a kind of access. It is reader, "
                    f"commenter or writer."}

        service, problem = self._service()
        if service is None:
            return {"ok": False, "error": problem}

        if anyone:
            permission = {"type": "anyone", "role": role}
            who = "anyone with the link"
        else:
            email = (email or "").strip()
            if not email:
                return {"ok": False, "error": "Share it with whom?"}
            permission = {"type": "user", "role": role, "emailAddress": email}
            who = email

        try:
            made = service.permissions().create(
                fileId=file_id, body=permission,
                # False: Drive's own notification mail is the one thing the
                # user did not ask us to send, and it goes out in their name.
                sendNotificationEmail=False,
                fields="id").execute()
        except Exception as exc:
            return self._refusal(exc)

        permission_id = str((made or {}).get("id") or "")
        if not permission_id:
            return {"ok": False, "error": "Drive did not share that."}
        return {"ok": True, "id": permission_id, "shared_with": who,
                "detail": f"Shared with {who} as {role}"}

    def sharing_state(self, file_id: str, permission_id: str = "",
                      email: str = "") -> dict:
        """Is this document actually shared, and with whom? (READ)

        For verification. `permissions().create` returning an id says Drive
        accepted the request; it does not say the grant is on the file — and
        "I shared it with your client" is exactly the sentence that has to be
        true rather than plausible.

        Matched on the permission id first and the address second: the id is
        exact, and the address is what survives if Drive reissued the grant.
        """
        if not file_id:
            return {"verified": False, "detail": "no document id"}
        service, problem = self._service()
        if service is None:
            return {"verified": False, "detail": problem}
        try:
            listed = service.permissions().list(
                fileId=file_id,
                fields="permissions(id,type,role,emailAddress)").execute()
        except Exception as exc:
            # A read that failed is not a share that failed. Saying "not
            # verified" with the reason is honest; saying "not shared" is not.
            return {"verified": False, "detail": f"could not read sharing: {exc}"}

        wanted = (email or "").strip().lower()
        for entry in (listed or {}).get("permissions") or []:
            if permission_id and str(entry.get("id") or "") == permission_id:
                return {"verified": True, "role": entry.get("role", "")}
            if wanted and str(entry.get("emailAddress") or "").lower() == wanted:
                return {"verified": True, "role": entry.get("role", "")}
            if not wanted and not permission_id and entry.get("type") == "anyone":
                return {"verified": True, "role": entry.get("role", "")}
        return {"verified": False, "detail": "that grant is not on the document"}

    def unshare(self, file_id: str, permission_id: str) -> dict:
        """Take access back (WRITE) — the inverse of `share`.

        Honest about what it is: the document may already have been opened,
        and nothing here un-reads it. What this restores is future access.
        """
        if not (file_id and permission_id):
            return {"ok": False, "error": "That share cannot be found."}
        service, problem = self._service()
        if service is None:
            return {"ok": False, "error": problem}
        try:
            service.permissions().delete(
                fileId=file_id, permissionId=permission_id).execute()
        except Exception as exc:
            return self._refusal(exc)
        return {"ok": True,
                "detail": "Access removed — they may already have opened it"}
