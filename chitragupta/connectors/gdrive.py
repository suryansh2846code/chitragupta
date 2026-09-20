"""Google Drive connector — ingests Docs / text / PDF files (read-only)."""
from __future__ import annotations

import io
from typing import Any

from ..core.chunk import chunk_text
from .base import Connector, SyncResult
from .google_auth import get_credentials, google_ready


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
    auto_sync = True
    incremental = True

    def is_configured(self) -> tuple[bool, str]:
        return google_ready()

    def sync(self, *, query: str | None = None, max_results: int | None = None,
             since: str | None = None, limit: int | None = None,
             full_history: bool = False, cancel=None, progress=None,
             interactive: bool = True, **_: Any) -> SyncResult:
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
        max_results = max_results or get_settings().drive_max

        try:
            creds = get_credentials(interactive=interactive)
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            all_types = [EXPORT_AS_TEXT, PDF_TYPE, DOCX_TYPE, PPTX_TYPE,
                         GSLIDES, *PLAIN_TYPES]
            mime_filter = (
                "(" + " or ".join(f"mimeType='{m}'" for m in all_types)
                + ")"
            )
            q = query or f"{mime_filter} and trashed=false"
            if not query and resume:
                # Drive can filter server-side, which is strictly better than
                # listing every file and discarding it locally: the existing
                # modified-date check below still runs, it just has far less to
                # do. RFC-3339 with a 'Z', which is what the API expects.
                q += f" and modifiedTime > '{_rfc3339(resume)}'"
            # paginate + include Shared-with-me and Shared Drives
            files: list[dict] = []
            page_token = None
            while len(files) < max_results:
                listing = (
                    service.files()
                    .list(q=q, pageSize=min(100, max_results - len(files)),
                          pageToken=page_token,
                          includeItemsFromAllDrives=True, supportsAllDrives=True,
                          corpora="allDrives",
                          fields="nextPageToken,files(id,name,mimeType,webViewLink,"
                                 "modifiedTime,owners(displayName))")
                    .execute()
                )
                files += listing.get("files", [])
                page_token = listing.get("nextPageToken")
                if not page_token:
                    break
            # skip files we already have at the same modified date — no re-download
            import json as _json
            existing = set()
            for row in self.store._conn.execute(
                "SELECT metadata, event_date FROM memories WHERE source=?", (self.name,)):
                try:
                    fid = _json.loads(row["metadata"] or "{}").get("file_id")
                except Exception:
                    fid = None
                if fid:
                    existing.add((fid, row["event_date"]))

            for f in files:
                sig = (f["id"], (f.get("modifiedTime") or "")[:10] or None)
                if sig in existing:
                    result.skipped += 1
                    continue
                try:
                    text = self._read_file(service, f, MediaIoBaseDownload)
                except Exception as exc:
                    result.errors.append(f"{f['name']}: {exc}")
                    continue
                if not text.strip():
                    result.skipped += 1
                    continue
                event_date = (f.get("modifiedTime") or "")[:10] or None
                owner = ""
                if f.get("owners"):
                    owner = f["owners"][0].get("displayName", "")
                for i, chunk in enumerate(chunk_text(text)):
                    mem = self.store.add(
                        text=chunk,
                        source=self.name,
                        kind="doc",
                        title=f["name"] if i == 0 else f"{f['name']} (part {i + 1})",
                        uri=f.get("webViewLink"),
                        event_date=event_date,
                        metadata={"file_id": f["id"], "chunk": i, "owner": owner},
                    )
                    if mem:
                        result.added += 1
                    else:
                        result.skipped += 1
            result.detail = f"{len(files)} files"
        except Exception as exc:
            result.errors.append(str(exc))
            result.detail = "sync failed"
        result.cursor = started
        return self._finish(result)

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
