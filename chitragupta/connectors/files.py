"""Local files connector — ingest a folder of text/markdown/code docs."""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, SyncStrategy

TEXT_EXT = {
    ".md", ".markdown", ".txt", ".rst", ".org",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".html", ".css", ".sh", ".java", ".go", ".rs", ".c", ".cpp", ".sql",
}
# Only prose feeds the knowledge graph; code is still stored + searchable, but
# extracting entities from source produces junk (TitleCase identifiers).
# Defined in `core/chunk.py` — a statement about content, and read by
# `brain` too. Re-exported here so `from .files import PROSE_EXT` keeps
# working; see `docs/ARCHITECTURE.md` §3 rule 2.
from ..core.chunk import PROSE_EXT

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "out", ".next", ".nuxt", "target", "coverage",
    "vendor", ".cache", ".turbo", ".parcel-cache", "bower_components",
    ".pytest_cache", ".mypy_cache", ".gradle", ".idea", ".vscode",
    "site-packages", "migrations", ".terraform",
}
# generated / lock / minified files that are noise in a knowledge base
IGNORE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "composer.lock", "cargo.lock", "go.sum",
}
def _is_junk_file(name: str) -> bool:
    n = name.lower()
    if n in IGNORE_FILES:
        return True
    return n.endswith((".min.js", ".min.css", ".map", ".bundle.js", ".lock"))

def _mtime(path: Path) -> float:
    """A file that vanished mid-scan sorts as brand new, so the walk does not
    crash on it and the next pass simply does not see it."""
    try:
        return path.stat().st_mtime
    except OSError:
        return float("inf")


MAX_BYTES = 2_000_000
# guardrail default; overridable via CHITRAGUPTA_MAX_FILES for large corpora


class FilesConnector(Connector):
    #: The folder this run is indexing, so the watermark helpers can key on it.
    _current_root: str | None = None

    name = "files"
    runs_on_device = True
    label = "Local Files"
    # Driven by remembered folders rather than the class, because a folder
    # the user has never pointed at is not a source. The scheduler re-indexes
    # `synced_paths()` directly.
    auto_sync = False
    incremental = True
    always_available = True
    #: Nothing to sign in to — the folder is already on this Mac. `LOCAL`
    #: rather than `NONE` because the two are different facts: this needs no
    #: credential *and* needs the user to have pointed at a folder.
    auth_method = AuthMethod.LOCAL
    sync_strategy = SyncStrategy.TIMESTAMP
    #: Read-only, and the whole product promise rests on it: nothing here can
    #: change a file on the user's disk.
    capabilities = caps("read:file", "read:folder")
    limits = Limits(concurrency=1, page_size=0, records_per_sync=5000)

    def sync(self, *, path: str = "", recursive: bool = True,
             exts: list[str] | None = None, since: str | None = None,
             limit: int | None = None, full_history: bool = False,
             cancel=None, progress=None, **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        root = Path(path).expanduser()
        # Stamped before the walk, so a file written while this runs is picked
        # up next time rather than landing just behind the new watermark.
        started = self.now()
        self._current_root = str(root)
        if not root.exists():
            result.errors.append(f"path not found: {root}")
            result.detail = "path not found"
            return self._finish(result)

        allow = {e if e.startswith(".") else f".{e}" for e in (exts or [])} or TEXT_EXT
        files = self._walk(root, recursive, allow)
        from ..config import get_settings
        max_files = get_settings().max_files
        if len(files) > max_files:
            result.errors.append(
                f"{len(files)} files found — that's a lot. Refusing to ingest more "
                f"than {max_files} at once. Point at a smaller/more specific folder, "
                "or raise the limit with CHITRAGUPTA_MAX_FILES.")
            result.detail = f"too many files ({len(files)}) under {root}"
            return self._finish(result)
        from ..brain import get_brain
        scanned = len(files)
        # Re-indexing a folder every 30 minutes re-reads and re-hashes every
        # file in it. `st_mtime` is the cheap answer, and dedup still catches
        # anything the overlap re-offers.
        cutoff = self._cutoff(since, full_history=full_history)
        if cutoff is not None:
            files = [f for f in files if _mtime(f) >= cutoff]

        # Route through the brain so the knowledge graph is built too.
        # fast=True → offline heuristic extraction, so bulk imports stay quick.
        # Only prose builds the graph; code is stored + searchable but skipped.
        #
        # The ingest runs inside `each_guarded`: the read was already guarded
        # but the ingest was not, so a single file that broke extraction
        # propagated straight out of sync() — past the connector's own error
        # handling, with no detail for the user and the rest of the folder
        # never scanned (H2).
        def ingest(fp) -> int:
            if fp.stat().st_size > MAX_BYTES:
                return 0
            text = fp.read_text(encoding="utf-8", errors="ignore")
            out = get_brain().ingest(
                text, source=self.name, kind="doc", title=fp.name,
                uri=str(fp), fast=True,
                build_graph=fp.suffix.lower() in PROSE_EXT,
            )
            return out["memories"]

        self.each_guarded(files[:limit] if limit else files, result, ingest,
                          cancel=cancel, progress=progress)
        result.detail = result.detail or (
            f"{len(files)} changed of {scanned} files under {root}"
            if cutoff is not None else f"scanned {scanned} files under {root}")
        self._remember_path(str(root))
        # Per folder, and only when the pass actually finished — a watermark
        # saved from a cancelled walk would skip everything it never reached.
        if not result.cancelled and not result.errors:
            self._set_folder_watermark(str(root), started)
        return self._finish(result)

    def _cutoff(self, since: str | None, *, full_history: bool) -> float | None:
        """When this folder was last indexed, as a POSIX timestamp.

        `connector_state.cursor` already holds the list of folders the user
        pointed at, so the watermark lives in the meta table keyed per folder —
        two folders are re-indexed on their own schedules, and one added today
        does not inherit the other's watermark and skip its own contents.
        """
        if full_history:
            return None
        stamp = since or self._folder_watermark(self._current_root)
        if not stamp:
            return None
        try:
            parsed = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()

    def _watermark_key(self, root: str) -> str:
        return f"files_watermark:{root}"

    def _folder_watermark(self, root: str | None) -> str | None:
        if not root:
            return None
        return self.store.get_meta(self._watermark_key(root))

    def _set_folder_watermark(self, root: str, stamp: str) -> None:
        self.store.set_meta(self._watermark_key(root), stamp)

    def _remember_path(self, path: str) -> None:
        """Record synced folders so background sync can re-index them."""
        import json
        state = self.store.get_connector_state(self.name) or {}
        try:
            paths = set(json.loads(state.get("cursor") or "[]"))
        except Exception:
            paths = set()
        paths.add(path)
        self.store.set_connector_state(
            self.name, cursor=json.dumps(sorted(paths)), status="ok")

    @classmethod
    def synced_paths(cls, store) -> list[str]:
        import json
        state = store.get_connector_state(cls.name) or {}
        try:
            return json.loads(state.get("cursor") or "[]")
        except Exception:
            return []

    def _walk(self, root: Path, recursive: bool, allow: set[str]) -> list[Path]:
        if root.is_file():
            return [root]
        out: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            for fn in filenames:
                if Path(fn).suffix.lower() in allow and not _is_junk_file(fn):
                    out.append(Path(dirpath) / fn)
            if not recursive:
                break
        return out
