"""Continuous background sync — keeps the brain current on a timer.

A daemon thread periodically re-syncs every *ready* connector (no browser),
mirroring Turnstone's "continuously updates". Re-syncs are cheap because the
store skips content it already has (no re-embedding of unchanged items).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from .config import get_settings
from .connectors import REGISTRY, get_connector
from .connectors.files import FilesConnector
from .log import suppressed

log = logging.getLogger("chitragupta.scheduler")

def _auto_connectors() -> list[str]:
    """Connectors the background loop re-runs, derived from the classes.

    This was a hand-maintained list of five names, and the other six were
    excluded by nothing more than not being in it — GitHub and Linear are
    token-backed sources a user connects and which then never refreshed again.
    A `auto_sync` flag on the class puts the decision where a reader of the
    connector can see it, and makes a new connector state its own intent.
    """
    return [name for name, cls in REGISTRY.items()
            if getattr(cls, "auto_sync", False) and cls.supported_here()]


def _custom_apps() -> list[dict]:
    """User-defined REST sources, which live outside REGISTRY (one per app).

    They were never refreshed on the timer at all: a user could connect one and
    watch it go stale forever with no way to tell why.
    """
    from .connectors.custom_api import list_apps

    with suppressed("listing the user's custom apps"):
        return list_apps()
    return []


def _mcp_servers() -> list[str]:
    """Ids of the MCP-backed connectors the user has configured."""
    from .connectors.mcp_source import list_servers

    with suppressed("listing the user's MCP connectors"):
        return [s.id for s in list_servers()]
    return []


class Scheduler:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sync_lock = threading.Lock()
        self._cancel = threading.Event()
        self.last_run: str | None = None
        self.last_result: dict[str, Any] = {}
        self.running = False
        self.syncing = False

    def cancel_sync(self) -> bool:
        """Ask an in-flight sync to stop. Cooperative: the sweep checks between
        sources, so the current source finishes but no further ones start.
        Returns True if a sync was actually running."""
        if self.syncing:
            self._cancel.set()
            return True
        return False

    # ── one sweep over all ready connectors ──────────────────────────────
    def sync_all(self, interactive: bool = False) -> dict[str, Any]:
        if not self._sync_lock.acquire(blocking=False):
            return {"skipped": "a sync is already running"}
        try:
            self._cancel.clear()
            self.syncing = True
            return self._sync_all(interactive)
        finally:
            self.syncing = False
            self._cancel.clear()
            self._sync_lock.release()

    def _sync_all(self, interactive: bool) -> dict[str, Any]:
        from .core.store import get_store
        store = get_store()
        summary: dict[str, Any] = {}

        # app-based connectors
        for name in _auto_connectors():
            if self._cancel.is_set():
                summary["_cancelled"] = True
                break
            cls = REGISTRY.get(name)
            if not cls:
                continue
            inst = cls()
            ready, _ = inst.is_configured()
            if not ready:
                continue
            try:
                # The cancel token goes *into* the connector. Checking it only
                # here meant cancelling mid-Gmail still waited for every
                # remaining message before the loop got another look.
                res = inst.sync(interactive=interactive, cancel=self._cancel)
                summary[name] = {"added": res.added, "errors": res.errors[:1]}
                if res.cancelled:
                    summary["_cancelled"] = True
            except Exception as exc:
                summary[name] = {"added": 0, "errors": [str(exc)[:120]]}

        # local files: re-index remembered folders. Driven by what the user
        # actually pointed at rather than by the class, which is why
        # FilesConnector.auto_sync is False.
        for path in FilesConnector.synced_paths(store):
            if self._cancel.is_set():
                summary["_cancelled"] = True
                break
            try:
                res = get_connector("files").sync(path=path, cancel=self._cancel)
                key = f"files:{path.split('/')[-1]}"
                summary[key] = {"added": res.added, "errors": res.errors[:1]}
            except Exception as exc:
                summary[f"files:{path}"] = {"added": 0, "errors": [str(exc)[:120]]}

        # MCP-backed connectors: one per server the user configured, so they
        # live outside REGISTRY for the same reason custom apps do.
        for server_id in _mcp_servers():
            if self._cancel.is_set():
                summary["_cancelled"] = True
                break
            try:
                from .connectors.mcp_source import MCPConnector

                conn = get_connector(f"mcp:{server_id}")
                # A connector whose tools only answer questions has nothing to
                # pull in ahead of time. Syncing it anyway records the same
                # "cannot list its records" error every half hour, which turns
                # a working connector into a permanent red mark.
                #
                # The concrete type, not the base: `status()` belongs to the
                # MCP connector, and a duck-typed call would silently skip any
                # future connector that happened to grow the name.
                if isinstance(conn, MCPConnector):
                    ready, _reason, can_sync = conn.status()
                    if not ready or not can_sync:
                        continue
                res = conn.sync(interactive=interactive, cancel=self._cancel)
                summary[f"mcp:{server_id}"] = {"added": res.added,
                                               "errors": res.errors[:1]}
            except Exception as exc:
                summary[f"mcp:{server_id}"] = {"added": 0,
                                               "errors": [str(exc)[:120]]}

        # custom apps are registered outside REGISTRY, one per user definition
        for app in _custom_apps():
            if self._cancel.is_set():
                summary["_cancelled"] = True
                break
            try:
                res = get_connector(f"custom:{app['id']}").sync(
                    interactive=interactive, cancel=self._cancel)
                summary[f"custom:{app['id']}"] = {
                    "added": res.added, "errors": res.errors[:1]}
            except Exception as exc:
                summary[f"custom:{app['id']}"] = {"added": 0,
                                                  "errors": [str(exc)[:120]]}

        # self-heal: never leave duplicates behind (no manual dedup needed)
        removed = store.dedupe()
        if removed:
            summary["_deduped"] = removed

        # enrich the knowledge graph from everything just synced (LLM-first,
        # incremental) — this is what makes the graph rich from ANY connector.
        try:
            # Free, automatic heuristic pass keeps the graph populated from every
            # source. The richer LLM enrichment is opt-in (the "Enrich with AI"
            # button) so we never spend model tokens without the user asking.
            from .brain import get_brain
            got = get_brain().enrich_until_done(fast=True, max_batches=12)
            if got.get("entities") or got.get("facts"):
                summary["_graph"] = {k: got[k] for k in ("entities", "facts", "remaining")}
        except Exception:
            log.exception("graph enrichment after sync failed")

        # What the sync produced becomes events; the engine decides what they
        # start. This used to be `sweep(new_email_count=…)`, which could only
        # say "some mail arrived" — no identity, so a redelivered message was
        # indistinguishable from a new one, and no payload, so a condition had
        # nothing to read. See `docs/AUTOMATION.md`.
        try:
            from .automation import engine
            engine.after_sync(summary)
        except Exception:
            log.exception("turning a sync into automation events failed")

        # Then the clock, for schedule- and interval-triggered automations, and
        # to pick up anything a previous process left mid-flight.
        try:
            from .automation import engine
            engine.tick()
        except Exception:
            log.exception("the automation tick after sync failed")

        self.last_run = datetime.now(UTC).isoformat()
        self.last_result = summary
        return summary

    # ── background loop ──────────────────────────────────────────────────
    def _loop(self, stop: threading.Event) -> None:
        """One background pass-maker. `stop` is *this thread's* token.

        It is a parameter rather than `self._stop` so that a thread can only
        ever be stopped by the token it was started with — see `start()`.
        """
        settings = get_settings()
        interval = max(1, settings.sync_interval_minutes) * 60
        # startup self-heal: auto-migrate derived data (re-embed / rebuild graph
        # when the code version changed) and dedupe — all automatic, no CLI.
        try:
            from .brain import get_brain
            m = get_brain().run_migrations()
            if m:
                print(f"[chitragupta] auto-migrated: {m}")
            get_brain().store.dedupe()
        except Exception as exc:
            import sys
            print(f"[chitragupta] startup migration skipped: {exc}", file=sys.stderr)
        # small initial delay, then an immediate first sync (no manual CLI needed)
        if stop.wait(20):
            return
        next_sync = 0.0
        while not stop.is_set():
            # fire due reminders often (every minute); sync on the longer interval
            self._fire_reminders()
            # Every minute, not every sync. A schedule set for 08:00 should
            # fire at 08:00 rather than whenever the next connector sync
            # happens to land, and a run left waiting on an approval that was
            # answered five minutes ago should carry on now.
            try:
                from .automation import engine
                engine.tick()
            except Exception:
                log.exception("the automation tick failed")
            if time.time() >= next_sync:
                try:
                    self.sync_all(interactive=False)
                except Exception:
                    log.exception("background sync_all failed")
                next_sync = time.time() + interval
            if stop.wait(60):
                return

    def _fire_reminders(self) -> None:
        """Everything whose time has come. **The scheduler runs nothing itself.**

        It used to. A due reminder was a `desktop_notify` call from here and a
        due scheduled action was a bare `actions.run_now` — so the one code path
        that executed with nobody present was also the one with no record, no
        idempotency and nowhere for a failure to go. A scheduled email that
        failed showed a notification and vanished.

        Now this decides *when* and the automation engine decides *what*:
        `fire_due` turns each due row into a run, and `tick` fires
        schedule-triggered automations and resumes anything left in flight.
        Both go through the same executor, the same permission gate and the
        same history as every other automation.
        """
        try:
            from .automation import engine
            engine.fire_due()
        except Exception:
            log.exception("firing due reminders and scheduled actions failed")
        try:
            from .automation import engine
            engine.tick()
        except Exception:
            log.exception("the automation tick failed")

    def start(self) -> None:
        settings = get_settings()
        if not settings.sync_enabled or self.running:
            return
        # A fresh token per thread. `_stop` used to be one `Event` for the life
        # of the process, and `stop()` set it forever: the next `start()` — a
        # `--dev` reload, or the shutdown hook in `api/app.py` followed by
        # anything restarting us — spawned a thread that returned immediately
        # from its first `stop.wait(20)`. `running` read True, the UI showed a
        # healthy scheduler, and nothing ever synced again. Exactly the silent
        # failure this module is prone to.
        #
        # Clearing the old event instead would have un-stopped a previous thread
        # still inside its 60-second wait, leaving two loops on one timer. A new
        # event cannot: the old thread keeps a reference to the old, set one and
        # exits on its next check, without anyone having to wait for it here.
        self._stop = threading.Event()
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, args=(self._stop,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.running = False


_scheduler = Scheduler()


def get_scheduler() -> Scheduler:
    return _scheduler
