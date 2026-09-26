"""One description per connector of how to run it without a network.

The point of putting these in a registry rather than in each test file is that
the generic suites — contract, crash isolation, idempotency, redaction — iterate
over it. A connector added later gets all four for free, and a connector that
cannot be faked here is a connector nobody can test, which is worth finding out
at the time it is written rather than after it ships.

Each entry builds a connector wired to `n` fabricated records and says how many
memories a clean sync of those records should produce. The connector's own
`sync()` runs for real: its parsing, its dedup, its writes.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .conftest import write_emlx

# ── fake transports ──────────────────────────────────────────────────────────


class _Exec:
    """A google-api-client call object: `.execute()` returns a canned payload."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def execute(self) -> Any:
        if callable(self._payload):
            return self._payload()
        return self._payload


class FakeGmailService:
    def __init__(self, messages: list[dict]) -> None:
        self._by_id = {m["id"]: m for m in messages}
        self._ids = [{"id": m["id"]} for m in messages]

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **_):
        return _Exec({"messages": self._ids})

    def get(self, *, id, **_):
        return _Exec(self._by_id[id])


class FakeCalendarService:
    """A Calendar that pages, and that can report a cancellation.

    It used to answer every request with the whole list, which meant the
    connector's own one-page read looked complete — so the bug where a calendar
    with more than 250 events silently lost the rest was invisible here too. A
    fake that cannot page cannot catch a connector that does not.
    """

    def __init__(self, events: list[dict], *, page_size: int = 2500) -> None:
        self._events = events
        self._page_size = page_size
        self.listed = 0

    def events(self):
        return self

    def list(self, **kw):
        self.listed += 1
        token = kw.get("pageToken")
        start = int(token) if token else 0
        size = min(self._page_size, kw.get("maxResults") or self._page_size)
        page = self._events[start:start + size]
        # `showDeleted` is honoured, because the connector passing it is the
        # whole mechanism by which a cancellation becomes visible: a fake that
        # ignored it would let a connector that forgot it still pass.
        if not kw.get("showDeleted"):
            page = [ev for ev in page if ev.get("status") != "cancelled"]
        payload: dict = {"items": page}
        if start + size < len(self._events):
            payload["nextPageToken"] = str(start + size)
        return _Exec(payload)


class FakeHTTPResponse:
    def __init__(self, payload: Any, url: str = "https://api.example.com/items"
                 ) -> None:
        self._body = json.dumps(payload).encode()
        self._url = url

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        """Where the request actually landed.

        Modelled because the real object has it and a caller now reads it:
        `outbound.check_landing` re-checks the final URL, since `urlopen`
        follows redirects and a `302 → http://127.0.0.1` lands somewhere the
        destination check refused.
        """
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def install_google(monkeypatch, fake_module, service) -> None:
    """Make the lazy `googleapiclient` import resolve, and skip real consent."""
    fake_module("googleapiclient.discovery", build=lambda *a, **k: service)
    for mod in ("gmail", "gcal", "gdrive"):
        target = f"chitragupta.connectors.{mod}"
        monkeypatch.setattr(f"{target}.get_credentials", lambda **_: object(),
                            raising=False)


def install_urlopen(monkeypatch, payload) -> None:
    """Answer every `urllib.request.urlopen` with one JSON body."""
    import urllib.request

    def _urlopen(request, *a, **k):
        # The landing URL is the one that was asked for: these fakes never
        # redirect, and inventing one would make `check_landing` pass against
        # an address no test chose.
        asked = getattr(request, "full_url", None) or str(request)
        return FakeHTTPResponse(payload, url=asked)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)


# ── per-connector builders ───────────────────────────────────────────────────


def _gmail(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.gmail import GmailConnector

    messages = [{
        "id": f"msg{i}",
        "internalDate": str(1_756_000_000_000 + i * 86_400_000),
        "snippet": f"Shipping update number {i} for the launch review.",
        "payload": {"headers": [{"name": "Subject", "value": f"Launch note {i}"},
                                {"name": "From", "value": f"person{i}@example.com"}],
                    "mimeType": "text/plain",
                    "body": {}},
    } for i in range(n)]
    install_google(monkeypatch, fake_module, FakeGmailService(messages))
    return GmailConnector()


def _gcal(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.gcal import GoogleCalendarConnector

    events = [{
        "id": f"evt{i}",
        "summary": f"Design review {i}",
        "start": {"dateTime": f"2026-09-0{i + 1}T10:00:00Z"},
        "end": {"dateTime": f"2026-09-0{i + 1}T11:00:00Z"},
        "location": "Room 4",
        "description": f"Agenda item {i}: walk through the connector plan.",
    } for i in range(n)]
    install_google(monkeypatch, fake_module, FakeCalendarService(events))
    return GoogleCalendarConnector()


def _github(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.github import GitHubConnector

    items = [{
        "number": i,
        "title": f"Connector crash on empty body {i}",
        "state": "open",
        "user": {"login": "someone"},
        "body": f"Steps to reproduce, case {i}. The sync aborts on the bad item.",
        "html_url": f"https://github.com/acme/repo/issues/{i}",
        "repository": {"full_name": "acme/repo"},
    } for i in range(n)]
    _set_secret("GITHUB_TOKEN", "ghp_testtoken")
    install_urlopen(monkeypatch, items)
    return GitHubConnector()


def _linear(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.linear import LinearConnector

    nodes = [{
        "identifier": f"ENG-{i}",
        "title": f"Wire the MCP connector {i}",
        "description": f"Long description for issue {i} about the sync contract.",
        "state": {"name": "In Progress"},
        "priorityLabel": "High",
        "team": {"name": "Core"},
        "assignee": {"name": "Someone"},
        "updatedAt": "2026-09-01T10:00:00Z",
    } for i in range(n)]
    _set_secret("LINEAR_API_KEY", "lin_api_testtoken")
    install_urlopen(monkeypatch, {"data": {"issues": {"nodes": nodes}}})
    return LinearConnector()


def _notion(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.notion import NotionConnector

    pages = [{
        "id": f"page{i}",
        "url": f"https://notion.so/page{i}",
        "properties": {"Name": {"type": "title",
                                "title": [{"plain_text": f"Spec {i}"}]}},
    } for i in range(n)]

    class FakeBlocks:
        class children:  # noqa: N801 — mirrors the SDK's own attribute
            # path, `notion.blocks.children.list(...)`; renaming it would
            # stop the connector's real call from resolving.
            @staticmethod
            def list(*, block_id, **_):
                return {"results": [{"type": "paragraph", "paragraph": {"rich_text": [
                    {"plain_text": f"Body text for {block_id} describing the plan."}]}}],
                    "has_more": False}

    class FakeClient:
        def __init__(self, **_): self.blocks = FakeBlocks()
        def search(self, **_): return {"results": pages}

    _set_secret("NOTION_TOKEN", "ntn_testtoken")
    fake_module("notion_client", Client=FakeClient)
    return NotionConnector()


def _files(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.files import FilesConnector

    folder = tmp_path / "docs"
    folder.mkdir(exist_ok=True)
    for i in range(n):
        (folder / f"note{i}.md").write_text(
            f"# Note {i}\n\nThe connector plan for source number {i}, written out "
            "in prose so the graph extractor has something to chew on.\n")
    conn = FilesConnector()
    conn._test_kwargs = {"path": str(folder)}
    return conn


def _notes(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.notes import NotesConnector

    conn = NotesConnector()
    conn._test_kwargs = {"text": "Dev's birthday is on the 31st of August."}
    conn._test_expected = 1          # notes ingest exactly one record per call
    return conn


def _imessage(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors import imessage

    db = tmp_path / "chat.db"
    con = sqlite3.connect(db)
    con.executescript("""
        DROP TABLE IF EXISTS handle;
        DROP TABLE IF EXISTS message;
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, handle_id INTEGER,
                              text TEXT, is_from_me INTEGER, date INTEGER);
    """)
    recent = (int(time.time()) - imessage.APPLE_EPOCH) * 1_000_000_000
    for h in range(n):
        con.execute("INSERT INTO handle (ROWID, id) VALUES (?,?)",
                    (h + 1, f"+155500000{h}"))
        for m in range(4):           # min_thread is 3, so 4 keeps every thread
            con.execute(
                "INSERT INTO message (handle_id, text, is_from_me, date) "
                "VALUES (?,?,?,?)",
                (h + 1, f"Message {m} in thread {h} about the release.",
                 m % 2, recent + m))
    con.commit()
    con.close()
    monkeypatch.setattr(imessage, "CHAT_DB", db)
    return imessage.IMessageConnector()


def _apple_mail(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors import apple_mail

    root = tmp_path / "Mail"
    (root / "V10" / "Inbox.mbox").mkdir(parents=True, exist_ok=True)
    for i in range(n):
        write_emlx(root / "V10" / "Inbox.mbox" / f"{i}.emlx",
                   sender=f"person{i}@example.com", subject=f"Local mail {i}",
                   body=f"Body of the locally stored message number {i}.")
    monkeypatch.setattr(apple_mail, "MAIL_ROOT", root)
    return apple_mail.AppleMailConnector()


def _apple_calendar(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors import apple_calendar

    root = tmp_path / "Calendars"
    root.mkdir(exist_ok=True)
    for i in range(n):
        (root / f"event{i}.ics").write_text(
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
            f"SUMMARY:Local event {i}\r\n"
            f"DTSTART:2026090{i + 1}T100000Z\r\nDTEND:2026090{i + 1}T110000Z\r\n"
            f"LOCATION:Room {i}\r\n"
            f"DESCRIPTION:Notes for local calendar event {i}.\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")
    monkeypatch.setattr(apple_calendar, "CAL_ROOT", root)
    return apple_calendar.AppleCalendarConnector()


def _custom_api(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.custom_api import CustomAPIConnector

    records = [{"id": i, "name": f"Record {i}",
                "note": f"Free-text body for custom record {i}."} for i in range(n)]
    install_urlopen(monkeypatch, {"data": {"results": records}})
    return CustomAPIConnector({
        "id": "testapp", "name": "Test App",
        "base_url": "https://api.example.com", "endpoint": "/items",
        "items_path": "data.results", "title_field": "name", "body_field": "note",
        "auth_type": "none",
    })


class FakeDriveService:
    """A Drive service that lists files and exports their text.

    Drive had **no fake at all**, which is why none of the generic suites ran
    against it and why its coverage sat at 15%: the registry at the bottom of
    this file is what hands a connector the contract, crash-isolation,
    idempotency and redaction tests, and one missing from it silently gets none
    of them.

    Only the Google-native export path is modelled. The byte-download path — PDF,
    docx, pptx — goes through the vendor's `MediaIoBaseDownload`, and a
    hand-rolled stand-in for that would be a test of the fake.
    """

    def __init__(self, files: list[dict], *, page_size: int = 100) -> None:
        self._files = files
        self._page_size = page_size
        #: Counted, because the point of the migration is how *few* of these a
        #: second pass makes.
        self.listed = 0
        self.exported = 0

    def files(self):
        return self

    def list(self, **kw):
        self.listed += 1
        token = kw.get("pageToken")
        start = int(token) if token else 0
        size = min(self._page_size, kw.get("pageSize") or self._page_size)
        page = self._files[start:start + size]
        payload: dict = {"files": page}
        if start + size < len(self._files):
            payload["nextPageToken"] = str(start + size)
        return _Exec(payload)

    # `fileId` and `mimeType` are Google's own keyword names, not ours: the
    # connector calls `files().export(fileId=..., mimeType=...)`, so a fake that
    # renamed them would not be called at all.
    def export(self, *, fileId, mimeType="text/plain", **kw):  # noqa: N803
        self.exported += 1
        found = next((f for f in self._files if f["id"] == fileId), None)
        if found is None:
            raise KeyError(fileId)
        return _Exec(found.get("_text", f"Body of {fileId}").encode())

    def get_media(self, **kw):       # pragma: no cover - see the class note
        raise NotImplementedError(
            "the byte-download path needs the vendor's MediaIoBaseDownload")


def drive_files(n: int) -> list[dict]:
    """`n` Google Docs, two of which were edited on the same day.

    The same-day pair is deliberate: the check this connector used to make
    compared `modifiedTime[:10]`, so a document edited twice in one day read as
    unchanged the second time.
    """
    from chitragupta.connectors.gdrive import EXPORT_AS_TEXT

    return [{
        "id": f"file{i}",
        "name": f"Launch plan {i}",
        "mimeType": EXPORT_AS_TEXT,
        "webViewLink": f"https://docs.google.test/d/file{i}",
        "modifiedTime": f"2026-09-20T{i % 24:02d}:30:00.000Z",
        "owners": [{"displayName": "Someone"}],
        "_text": f"The launch plan, revision {i}. Shipping in October.",
    } for i in range(n)]


def _gdrive(monkeypatch, fake_module, tmp_path, n):
    from chitragupta.connectors.gdrive import GoogleDriveConnector

    service = FakeDriveService(drive_files(n))
    install_google(monkeypatch, fake_module, service)
    # The lazy `from googleapiclient.http import MediaIoBaseDownload` has to
    # resolve even though no fixture here downloads bytes.
    fake_module("googleapiclient.http", MediaIoBaseDownload=_NoDownloads)
    connector = GoogleDriveConnector()
    connector.fake_service = service          # so a test can count the calls
    return connector


class _NoDownloads:
    """Stands in for `MediaIoBaseDownload` and is never reached by these
    fixtures. Raising rather than returning empty bytes: a fixture that got here
    would be silently reading nothing, and a test asserting on nothing passes."""

    def __init__(self, *a, **kw) -> None:
        raise NotImplementedError("no fixture here downloads bytes")


def _set_secret(key: str, value: str) -> None:
    from chitragupta.config import get_settings

    get_settings().set_secret(key, value)


# ── the registry ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConnectorFake:
    name: str
    build: Callable[..., Any]
    #: Memories a clean sync of `n` records should add. Most connectors are 1:1;
    #: iMessage folds a whole thread into one memory, notes takes one at a time.
    expected: Callable[[int], int] = lambda n: n
    #: False where the source cannot carry arbitrary text (so the redaction
    #: probe has nowhere to hide a secret).
    carries_free_text: bool = True


FAKES: tuple[ConnectorFake, ...] = (
    ConnectorFake("gmail", _gmail),
    ConnectorFake("gcal", _gcal),
    ConnectorFake("github", _github),
    ConnectorFake("linear", _linear),
    ConnectorFake("notion", _notion),
    ConnectorFake("files", _files),
    ConnectorFake("notes", _notes, expected=lambda n: 1),
    ConnectorFake("imessage", _imessage),
    ConnectorFake("apple_mail", _apple_mail),
    ConnectorFake("apple_calendar", _apple_calendar),
    # Drive was absent from this registry, so none of the generic suites
    # ran against it. Registering it is what gives it the contract,
    # crash-isolation, idempotency and redaction tests.
    ConnectorFake("gdrive", _gdrive),
    ConnectorFake("custom:testapp", _custom_api),
)

BY_NAME = {f.name: f for f in FAKES}


def build(fake: ConnectorFake, monkeypatch, fake_module, tmp_path, n: int):
    """Construct the connector and the keyword arguments its `sync()` wants."""
    conn = fake.build(monkeypatch, fake_module, tmp_path, n)
    return conn, getattr(conn, "_test_kwargs", {})


def sync(conn, kwargs: dict | None = None):
    """Run a connector the way the scheduler does: never interactively."""
    return conn.sync(interactive=False, **(kwargs or {}))
