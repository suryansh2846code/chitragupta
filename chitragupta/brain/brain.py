"""The Brain: one continuously-updated knowledge base shared by all agents.

Combines a vector memory store (raw recallable chunks) with a knowledge graph
(entities + facts). Ingestion writes both; recall fuses both into a compact,
ready-to-inject context block — so any agent, on any model, starts already
knowing the user.
"""
from __future__ import annotations

from datetime import UTC
from typing import Any

from ..core.chunk import chunk_text
from ..core.once import once
from ..core.store import MemoryStore, get_store
from ..log import suppressed
from . import extract as extractor
from .graph import ENTITY_TYPES, GraphStore

_CHARS_PER_TOKEN = 4


class Brain:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or get_store()
        self.graph = GraphStore(self.store)
        import threading
        self._enrich_lock = threading.Lock()
        self._enrich_state: dict[str, Any] = {
            "running": False, "stop": False, "processed": 0, "entities": 0,
            "facts": 0, "tokens_in": 0, "tokens_out": 0, "estimated": False,
            "provider": None, "found": [], "started": None, "remaining": 0}

    # ── ingestion ────────────────────────────────────────────────────────
    def ingest(self, text: str, *, source: str = "manual", kind: str = "note",
               title: str | None = None, uri: str | None = None,
               build_graph: bool = True, fast: bool = False,
               event_date: str | None = None, metadata: dict | None = None,
               tags=None,
               # Brain v1.5 parameters
               memory_type: str | None = None,
               source_id: str | None = None,
               event_time: str | None = None,
               valid_from: str | None = None,
               valid_until: str | None = None,
               importance: float | None = None,
               confidence: float | None = None,
               reinforcement_count: int = 0,
               status: str = "active",
               extraction_method: str = "direct",
               supersedes_id: str | None = None,
               evidence: str | None = None) -> dict[str, Any]:
        """Ingest text into both the vector store and the knowledge graph.

        `fast=True` uses offline heuristic extraction only (no per-chunk LLM
        call) — used for bulk connector syncs so importing a whole folder stays
        quick instead of hitting the model hundreds of times.
        """
        added_mem = 0
        entities = 0
        facts = 0
        graphed_ids: list[str] = []
        added_ids: list[str] = []
        for i, chunk in enumerate(chunk_text(text)):
            mem = self.store.add(
                text=chunk, source=source, kind=kind,
                title=title if i == 0 else f"{title} (part {i+1})" if title else None,
                uri=uri, tags=tags or [], event_date=event_date,
                metadata=metadata,
                memory_type=memory_type,
                source_id=source_id,
                event_time=event_time,
                valid_from=valid_from,
                valid_until=valid_until,
                importance=importance,
                confidence=confidence,
                reinforcement_count=reinforcement_count,
                status=status,
                extraction_method=extraction_method,
                supersedes_id=supersedes_id,
                evidence=evidence,
            )
            if not mem:
                continue
            added_mem += 1
            added_ids.append(mem.id)
            if build_graph:
                # immediate heuristic graph for curated/interactive content; mark
                # done so the background LLM enricher doesn't double-count it.
                e, f = self._graph_from(extractor.clean_for_extraction(chunk), mem.id, fast=fast)
                entities += e
                facts += f
                graphed_ids.append(mem.id)
        if graphed_ids:
            self.store.mark_graphed(graphed_ids)
        return {"memories": added_mem, "entities": entities, "facts": facts, "memory_ids": added_ids}

    # ── background enrichment: turn raw memories into a rich graph ───────────
    def enrich(self, limit: int = 40, provider_name: str | None = None,
               fast: bool = False, model_name: str | None = None) -> dict[str, Any]:
        """Process up to `limit` un-graphed memories into the knowledge graph.
        Uses the connected LLM when available (precise, works for ANY source —
        Gmail, Drive, Notion, custom apps…), batching short memories into one call
        to keep it cheap; falls back to the offline heuristic. Incremental: call
        repeatedly (the scheduler does) until `remaining` is 0."""
        use_llm = False
        if not fast:
            try:
                from ..config import get_settings
                from ..models.registry import get_provider
                p = get_provider(provider_name or get_settings().model_provider, model_name)
                use_llm = p.name != "mock" and p.is_ready()[0]
            except Exception:
                use_llm = False
        # heuristic (auto/free) only touches untouched memories → graphed=1; the LLM
        # pass works the whole queue (graphed<2) so it can upgrade heuristic results.
        level = 2 if use_llm else 1
        below = 2 if use_llm else 1
        # Cap bulk sources to recent-N only for the (costly) LLM pass; the free
        # heuristic pass still drains everything so nothing is silently orphaned.
        cap = self.enrich_cap() if use_llm else 0
        mems = self.store.list_ungraphed(limit=limit, below=below,
                                         cap=cap, full=self._FULL_SOURCES)
        if not mems:
            return {"processed": 0, "entities": 0, "facts": 0,
                    "remaining": self._queue_count(),
                    "tokens_in": 0, "tokens_out": 0, "tokens": 0,
                    "tokens_estimated": False, "provider": None,
                    "found": [], "sources": {}}
        ents = facts = 0
        tok_in = tok_out = 0
        est = False
        prov = None
        done: list[str] = []
        found: list[dict] = []          # names/types found this call (for live UI)
        sources: dict = {}

        def _collect(data, mem):
            for e in data.get("entities", []):
                if e.get("name") and extractor.is_good_entity(e["name"]):
                    # Constrain the type here, not only in `upsert_entity`.
                    # This list is sent straight to the UI for the live "found"
                    # chips, where it lands in a class attribute — so an
                    # extraction model that returned markup instead of a type
                    # would be writing markup into the page. `upsert_entity`
                    # applies the same allowlist; this path skipped it.
                    raw_type = str(e.get("type", "thing") or "thing").lower()
                    found.append({"name": extractor._clean_name(e["name"]),
                                  "type": raw_type if raw_type in ENTITY_TYPES else "thing"})
            if mem is not None:
                sources[mem.source] = sources.get(mem.source, 0) + 1

        if use_llm:
            batch: list[str] = []
            batch_ids: list[str] = []
            batch_mems: list = []
            blen = 0

            def flush():
                nonlocal ents, facts, blen, tok_in, tok_out, est, prov
                if not batch:
                    return
                combined = "\n\n---\n\n".join(batch)
                data = extractor.extract_llm(combined, provider_name, model_name) \
                    or extractor.extract_heuristic(combined)
                u = data.get("_usage")
                if u:
                    tok_in += u["in"]; tok_out += u["out"]
                    est = est or u["est"]; prov = u["provider"]
                _collect(data, None)
                for bm in batch_mems:
                    sources[bm.source] = sources.get(bm.source, 0) + 1
                e, f = self._apply_graph(data, batch_ids[0])
                ents += e
                facts += f
                done.extend(batch_ids)
                batch.clear(); batch_ids.clear(); batch_mems.clear()
                blen = 0

            for m in mems:
                ct = extractor.clean_for_extraction(m.text)
                if not extractor.is_graphable(ct):
                    done.append(m.id)          # nothing to graph — mark handled
                    continue
                if batch and blen + len(ct) > 7000:   # bigger batches → fewer LLM calls
                    flush()
                batch.append(ct); batch_ids.append(m.id); batch_mems.append(m)
                blen += len(ct)
            flush()
        else:
            # Heuristic auto pass: only extract from HIGH-SIGNAL sources (calendar,
            # notes, notion, agent, prose files). Bulk Gmail/Drive is left for the
            # LLM (extracting it heuristically just makes doc-heading junk). Non-
            # high-signal memories are still marked done so they don't re-queue here.
            for m in mems:
                if self._auto_graphable(m):
                    ct = extractor.clean_for_extraction(m.text)
                    if extractor.is_graphable(ct):
                        data = extractor.extract_heuristic(ct)
                        _collect(data, m)
                        e, f = self._apply_graph(data, m.id)
                        ents += e
                        facts += f
                done.append(m.id)

        self.store.mark_graphed(done, level=level)
        # de-dup found names, keep the most recent handful for the live feed
        seen: set[str] = set()
        uniq: list[dict[str, Any]] = []
        for f in reversed(found):
            k = f["name"].lower()
            if k and k not in seen:
                seen.add(k); uniq.append(f)
            if len(uniq) >= 12:
                break
        return {"processed": len(done), "entities": ents, "facts": facts,
                "remaining": self._queue_count(),
                "tokens_in": tok_in, "tokens_out": tok_out,
                "tokens": tok_in + tok_out, "tokens_estimated": est, "provider": prov,
                "found": uniq, "sources": sources}

    def _auto_graphable(self, mem) -> bool:
        """High-signal sources the free heuristic may graph (short/structured/curated).
        Bulk Gmail (HTML) and Drive docs are excluded — only the LLM graphs those."""
        if mem.source in {"notes", "agent", "manual", "notion", "gcal"}:
            return True
        if mem.uri and mem.uri.lower().endswith(_prose_suffixes()):
            return True
        return mem.kind in {"note", "fact"}

    # High-signal sources are small/curated → always enriched in full. Bulk sources
    # (Gmail, Drive, iMessage, Slack…) are capped to their most-recent N per source so
    # a first-time user isn't billed to LLM-enrich thousands of old items. General:
    # any source not in this set is treated as bulk and capped. Cap is user-tunable.
    _FULL_SOURCES = ("notes", "agent", "manual", "notion", "gcal")
    ENRICH_CAP_DEFAULT = 100

    def enrich_cap(self) -> int:
        """Most-recent items per BULK source to LLM-enrich (0 = unlimited)."""
        try:
            v = self.store.get_meta("enrich_cap")
            return max(0, int(v)) if v is not None else self.ENRICH_CAP_DEFAULT
        except Exception:
            return self.ENRICH_CAP_DEFAULT

    def set_enrich_cap(self, n: int) -> dict[str, Any]:
        n = max(0, int(n))
        self.store.set_meta("enrich_cap", str(n))
        return {"cap": n, "remaining": self._queue_count()}

    def _queue_count(self) -> int:
        """Size of the LLM-enrich queue with the recent-N-per-bulk-source cap applied."""
        return self.store.count_ungraphed(
            below=2, cap=self.enrich_cap(), full=self._FULL_SOURCES)

    def _graph_from(self, text: str, mem_id: str, fast: bool = False) -> tuple[int, int]:
        data = (extractor.extract_heuristic(text) if fast
                else extractor.extract(text))
        return self._apply_graph(data, mem_id)

    def _apply_graph(self, data: dict, mem_id: str) -> tuple[int, int]:
        name_to_id: dict[str, str] = {}
        for ent in data.get("entities", []):
            eid = self.graph.upsert_entity(
                ent.get("name", ""), type=ent.get("type", "thing"),
                summary=ent.get("summary", ""),
            )
            if eid:
                name_to_id[ent["name"]] = eid
        n_facts = 0
        for fact in data.get("facts", []):
            subj = name_to_id.get(fact.get("subject", ""))
            if not subj:
                # create the subject entity on the fly if referenced
                if fact.get("subject"):
                    subj = self.graph.upsert_entity(fact["subject"])
                    name_to_id[fact["subject"]] = subj
            obj = name_to_id.get(fact.get("object") or "")
            self.graph.add_relation(
                subj or "", fact.get("predicate", "related_to"), obj,
                fact.get("fact", ""), source_mem=mem_id,
            )
            if subj:
                n_facts += 1
        return len(name_to_id), n_facts

    # ── recall ───────────────────────────────────────────────────────────
    # keyword → (source, human label) for "what's in my X" overview queries
    _SOURCE_KW = [
        (("inbox", "email", "emails", "mail", "mails", "gmail"), "gmail", "Gmail"),
        (("drive", "google drive", "document", "documents", "docs"), "gdrive", "Google Drive"),
        (("calendar", "event", "events", "meeting", "meetings", "schedule"), "gcal", "Calendar"),
        (("message", "messages", "imessage", "texts"), "imessage", "iMessage"),
        (("notion",), "notion", "Notion"),
    ]
    _OVERVIEW_RE = None

    def _overview(self, query: str):
        """If the query asks 'what's in / summarize / list my <source>', return
        (source, label, listing-block); else None. Lists the items of that source
        so the agent can answer overview questions semantic search can't."""
        import re
        if self._OVERVIEW_RE is None:
            Brain._OVERVIEW_RE = re.compile(
                r"\b(what'?s?\s+in|summar|list|show me|overview|everything|"
                r"all (my|the)|what do i have|do i have (any|some)|contents? of|"
                r"how many|go through|run through)\b", re.I)
        q = query.lower()
        if not self._OVERVIEW_RE.search(q):
            return None
        for kws, src, label in self._SOURCE_KW:
            if any(re.search(rf"\b{re.escape(k)}\b", q) for k in kws):
                # DISTINCT files/emails — group by uri (or title) so multi-chunk
                # PDFs count once, not once per chunk.
                rows = self.store._conn.execute(
                    "SELECT title, uri, MAX(event_date) AS d FROM memories "
                    "WHERE source=? GROUP BY COALESCE(uri, title) "
                    "ORDER BY d DESC, MAX(created_at) DESC LIMIT 200",
                    (src,),
                ).fetchall()
                seen, titles = set(), []
                for r in rows:
                    base = (r["title"] or "").split(" (part")[0].strip()
                    if base and base.lower() not in seen:
                        seen.add(base.lower())
                        titles.append((r["d"], base))
                if not titles:
                    return (src, label, f"You have NO {label} items in the brain yet.")
                shown = titles[:60]
                lines = [f"- {t}" + (f"  ({d})" if d else "") for d, t in shown]
                more = f"\n…and {len(titles) - len(shown)} more" if len(titles) > len(shown) else ""
                block = (f"OVERVIEW — the user's {label} in the brain "
                         f"({len(titles)} items):\n" + "\n".join(lines) + more)
                return (src, label, block)
        return None

    # detect "find/get a document" intent for on-demand Drive fetch
    _FIND_RE = None
    _DOC_RE = None
    _FIND_STOP = {
        "find", "get", "pull", "fetch", "open", "show", "locate", "bring", "give",
        "me", "my", "the", "a", "an", "of", "in", "from", "on", "for", "please",
        "can", "you", "do", "have", "is", "there", "where", "wheres", "search",
        "file", "files", "doc", "docs", "document", "documents", "pdf", "note",
        "notes", "sheet", "slides", "slide", "presentation", "drive", "google",
        "folder", "and", "all", "any", "some", "this", "that", "year", "notess",
    }

    def _find_terms(self, query: str):
        import re
        if self._FIND_RE is None:
            Brain._FIND_RE = re.compile(
                r"\b(find|get|pull|fetch|open|show|locate|bring|where'?s?|"
                r"do you have|is there|search)\b", re.I)
            Brain._DOC_RE = re.compile(
                r"\b(file|files|doc|docs|document|documents|pdf|notes?|resume|cv|"
                r"sheet|slides?|presentation|drive|email|emails|mail|mails|inbox|"
                r"message|messages)\b", re.I)
        if not (self._FIND_RE.search(query) and self._DOC_RE.search(query)):
            return None
        words = [w for w in re.findall(r"[a-z0-9@.]{2,}", query.lower())
                 if w not in self._FIND_STOP]
        return " ".join(words[:5]) if words else None

    def _maybe_fetch(self, query: str):
        """On-demand fetch: if the user asks for a specific email/document not in
        the brain, live-search the right source (Gmail or Drive) and ingest it.
        Returns (fetched_titles, source)."""
        import re
        terms = self._find_terms(query)
        if not terms:
            return [], ""
        from ..connectors import REGISTRY, get_connector
        # route by what the user mentioned
        if re.search(r"\b(email|emails|inbox|mail|mails|gmail|sent|from)\b",
                     query.lower()):
            src, kw = "gmail", "max_results"
        else:
            src, kw = "gdrive", "max_files"
        cls = REGISTRY.get(src)
        if not cls or not cls().is_configured()[0]:
            return [], ""
        conn = get_connector(src)
        # Duck-typed, like every connector capability: `search_and_ingest` lives
        # on the sources that support live search, not on the base class. Asking
        # first turns "this source cannot do that" into an empty result instead
        # of an AttributeError in the middle of an agent turn.
        search = getattr(conn, "search_and_ingest", None)
        if not callable(search):
            return [], ""
        fetched = search(terms, **{kw: 8 if src == "gmail" else 5})
        return fetched, src

    def _escape_hatch(self, query: str):
        """For all-history/aggregate questions, note that only a recent window is
        indexed and offer to sync the full archive; trigger it if the user asks."""
        import re
        ql = query.lower()
        if not re.search(r"\b(email|emails|inbox|mail|mails)\b", ql):
            return ""
        # explicit request → kick off a full-history sync in the background
        if re.search(r"\bsync\b.*\b(all|full|entire|everything)\b", ql) or \
           re.search(r"\b(all|full|entire)\b.*\b(email|mail|inbox|archive)\b.*\bsync\b", ql):
            import threading

            from ..connectors import get_connector
            threading.Thread(
                target=lambda: get_connector("gmail").sync(full_history=True),
                daemon=True).start()
            return ("[Started syncing the user's FULL email archive in the "
                    "background — tell them it's indexing and will be ready shortly.]\n")
        # aggregate/all-history intent → offer the escape hatch
        if re.search(r"\b(all my|entire|whole|every|all[- ]time|top senders?|"
                     r"how many|last year|this year|overall|in total)\b", ql):
            return ("[NOTE: for speed, only recent mail (~last 90 days) is indexed. "
                    "This is an all-history/aggregate question, so answer from what's "
                    "here and OFFER to sync the full archive — the user can say "
                    "'sync all my email' or use the Connectors panel.]\n")
        return ""

    def recall(self, query: str, *, limit: int = 8, max_tokens: int = 1400,
               source: str | None = None, prefer: list[str] | None = None,
               include_superseded: bool | None = None,
               include_open_loops: bool = True) -> dict[str, Any]:
        """Fuse graph + vector recall + open loops + canonical into an injectable context block.

        If the query references a date/range ("emails on July 14", "last week"),
        recall is filtered to items whose real event_date falls in that range.
        Also lazily fetches a requested document from Drive if it isn't yet in
        the brain, then includes it.
        """
        fetched, fetched_src = self._maybe_fetch(query)   # on-demand Gmail/Drive
        hatch_note = self._escape_hatch(query)            # all-history escape hatch

        from ..core.dateparse import parse_date_range
        dr = parse_date_range(query)
        date_start, date_end = dr if dr else (None, None)

        # "what's in my drive/inbox/calendar" → a listing, which semantic search
        # can't produce. Inject an overview of that source and prefer it.
        ov = self._overview(query)
        overview_block = ""
        if ov:
            ov_src, _ov_label, overview_block = ov
            prefer = list(set((prefer or []) + [ov_src]))

        # a dated query usually wants MORE of that day's items, so widen the window
        eff_limit = 25 if dr else limit
        hits = self.store.search(
            query, limit=eff_limit, source=source, prefer=prefer,
            date_start=date_start, date_end=date_end,
            include_superseded=include_superseded)
        ents = self.graph.match_entities(query, limit=4)

        graph_lines: list[str] = []
        for e in ents:
            facts = self.graph.facts_for(e["id"], limit=4)
            head = f"• {e['name']} ({e['type']})"
            if e["summary"]:
                head += f": {e['summary']}"
            graph_lines.append(head)
            graph_lines += [f"    - {f}" for f in facts]

        # Open loops: include active open commitments relevant to query or active projects
        matching_loops = []
        open_loop_lines: list[str] = []
        if include_open_loops:
            ql = query.lower()
            if any(w in ql for w in ("completed", "finished")):
                target_status = "completed"
            elif any(w in ql for w in ("cancelled", "canceled", "abandoned")):
                target_status = "cancelled"
            elif any(w in ql for w in ("stale", "dormant")):
                target_status = "stale"
            else:
                target_status = "active"

            open_loops = self.store.list_open_loops(status=target_status, limit=15)
            q_words = [w.lower() for w in query.split() if len(w) > 3]
            is_task_query = any(k in ql for k in ("task", "pending", "loop", "todo", "waiting", "blocked", "open", "commit", "next", "what to do", "completed", "cancelled", "stale"))
            for loop in open_loops:
                desc_l = loop.description.lower()
                proj_l = (loop.related_project or "").lower()
                if is_task_query or any(w in desc_l or w in proj_l for w in q_words) or loop.priority in ("urgent", "high"):
                    matching_loops.append(loop)
            if not matching_loops and open_loops and target_status == "active":
                matching_loops = open_loops[:3]
            open_loop_lines = [l.as_context() for l in matching_loops[:5]]

        # For a dated query the user usually wants an OVERVIEW of that period, so
        # render each item compactly (so all of the day's emails fit) and widen
        # the budget; otherwise keep full-context blocks for depth.
        compact = dr is not None
        budget = max_tokens * (3 if compact else 1) * _CHARS_PER_TOKEN
        blocks: list[str] = []
        used = 0
        kept = []
        for h in hits:
            if compact:
                m = h.memory
                head = (m.title or m.text[:60]).strip()
                snippet = " ".join(m.text.split())[:200]
                block = f"• [{m.event_date or m.source}] {head} — {snippet}"
            else:
                block = h.memory.as_context()
            if used + len(block) > budget and blocks:
                break
            blocks.append(block)
            used += len(block)
            kept.append(h)

        parts = []
        # Canonical, evidence-backed facts lead the context — they win over raw
        # memories. Never let a curation failure break plain retrieval.
        canon = None
        try:
            from .canonical import get_canonical
            canon = get_canonical().recall_block(query)
        except Exception:
            canon = None
        if canon and canon.get("block"):
            parts.append(canon["block"])
        if overview_block:                       # source listing next
            parts.append(overview_block)
        if open_loop_lines and not compact:
            parts.append("ACTIVE OPEN LOOPS & COMMITMENTS:\n" + "\n".join(open_loop_lines))
        if graph_lines and not compact:
            parts.append("KNOWN ENTITIES & FACTS:\n" + "\n".join(graph_lines))
        if blocks:
            label = ("ITEMS IN THAT DATE RANGE:" if compact else "RELEVANT MEMORIES:")
            joiner = "\n" if compact else "\n\n---\n\n"
            parts.append(label + "\n" + joiner.join(blocks))
        context = ""
        fetch_note = ""
        if fetched:
            where = "Gmail" if fetched_src == "gmail" else "Drive"
            fetch_note = (f"[Just fetched from {where} on demand: "
                          + ", ".join(fetched[:8]) + "]\n")
        fetch_note += hatch_note
        date_note = ""
        if dr:
            date_note = (f"[Date filter applied: showing only items dated "
                         f"{date_start}"
                         + (f" to {date_end}" if date_end != date_start else "")
                         + (". Nothing in the brain matches that date."
                            if not kept else ".") + "]\n")
        if parts or date_note or fetch_note:
            context = (
                "Context recalled from the user's personal Chitragupta brain. "
                "Use it to act without asking them to repeat themselves.\n\n"
                + fetch_note + date_note
                + "\n\n".join(parts)
            )
        return {
            "context": context,
            "memory_hits": [
                {
                    "score": h.score,
                    "explanation": h.explanation.model_dump() if h.explanation else None,
                    **h.memory.model_dump(),
                }
                for h in kept
            ],
            "entities": ents,
            "open_loops": [l.model_dump() for l in matching_loops],
            "date_range": dr,
            "fetched": fetched,
            "canonical": canon,
        }

    # ── Brain v1.5 API Primitives ─────────────────────────────────────────
    def remember(self, text: str, *, title: str | None = None,
                 memory_type: str = "semantic", importance: float = 0.8,
                 confidence: float = 0.95, valid_from: str | None = None,
                 evidence: str | None = None, **kw) -> dict[str, Any]:
        """Explicit entry point to store high-confidence user knowledge."""
        source = kw.pop("source", "manual")
        res = self.ingest(
            text, source=source, kind="fact", title=title,
            memory_type=memory_type, importance=importance, confidence=confidence,
            valid_from=valid_from, extraction_method="user_statement",
            evidence=evidence or "explicitly stated by user", **kw
        )
        if res.get("memory_ids"):
            res["id"] = res["memory_ids"][0]
        with suppressed("from .canonical import get_canonical …"):
            from .canonical import get_canonical
            cres = get_canonical().remember(text)
            res["canonical"] = cres
        return res

    def forget(self, memory_id: str, *, soft: bool = True) -> bool:
        """Forget memory. soft=True marks as 'retracted' to preserve provenance."""
        return self.store.delete(memory_id, soft=soft)

    def update(self, memory_id: str, **fields: Any) -> Any:
        return self.store.update(memory_id, **fields)

    def reinforce(self, memory_id: str, count: int = 1) -> bool:
        return self.store.reinforce(memory_id, count=count)

    def supersede(self, old_id: str, new_text_or_id: str, **kwargs) -> dict[str, Any]:
        """Supersede older knowledge with newer knowledge, keeping historical record."""
        old_mem = self.store.get(old_id)
        if not old_mem:
            return {"ok": False, "error": "old memory not found"}

        if self.store.get(new_text_or_id):
            self.store.supersede(old_id, new_text_or_id)
            return {"ok": True, "old_id": old_id, "new_id": new_text_or_id}

        ing = self.ingest(
            new_text_or_id,
            source=kwargs.get("source", old_mem.source),
            kind=kwargs.get("kind", old_mem.kind),
            memory_type=kwargs.get("memory_type", old_mem.memory_type),
            supersedes_id=old_id,
            confidence=kwargs.get("confidence", 0.9),
            importance=kwargs.get("importance", old_mem.importance),
        )
        new_hits = self.store.search(new_text_or_id[:60], limit=1)
        new_id = new_hits[0].memory.id if new_hits else None
        if new_id:
            self.store.supersede(old_id, new_id)
            return {"ok": True, "old_id": old_id, "new_id": new_id, "ingested": ing}
        return {"ok": False, "error": "failed to ingest replacement"}

    def get_entity(self, entity_id: str) -> dict | None:
        return self.graph.get_entity(entity_id)

    def list_entities(self, limit: int = 30) -> list[dict]:
        return self.graph.top_entities(limit=limit)

    def get_related(self, entity_id: str, limit: int = 10) -> list[dict]:
        return self.graph.get_related(entity_id, limit=limit)

    def create_open_loop(self, description: str, *, status: str = "open",
                         priority: str = "medium", due_at: str | None = None,
                         related_project: str | None = None,
                         related_entities: list[str] | None = None,
                         confidence: float = 0.8, source: str = "manual",
                         metadata: dict | None = None) -> dict[str, Any]:
        loop = self.store.add_open_loop(
            description, status=status, priority=priority, due_at=due_at,
            related_project=related_project, related_entities=related_entities,
            confidence=confidence, source=source, metadata=metadata,
        )
        return loop.model_dump() if loop else {}

    def get_open_loops(self, *, status: str | None = "open",
                       related_project: str | None = None,
                       limit: int = 50) -> list[dict[str, Any]]:
        loops = self.store.list_open_loops(status=status, related_project=related_project, limit=limit)
        return [l.model_dump() for l in loops]

    def complete_open_loop(self, loop_id: str) -> dict[str, Any] | None:
        done = self.store.complete_open_loop(loop_id)
        return done.model_dump() if done else None

    def update_open_loop(self, loop_id: str, **fields) -> dict[str, Any] | None:
        updated = self.store.update_open_loop(loop_id, **fields)
        return updated.model_dump() if updated else None

    def inspect_memory(self, memory_id: str) -> dict[str, Any] | None:
        mem = self.store.get(memory_id)
        if not mem:
            return None
        d = mem.model_dump()
        d["activation_score"] = mem.compute_activation()
        d["has_embedding"] = bool(self.store._conn.execute(
            "SELECT 1 FROM memories WHERE id=? AND embedding IS NOT NULL", (memory_id,)).fetchone())
        return d

    def explain_memory(self, memory_id: str, query: str = "") -> dict[str, Any]:
        mem = self.store.get(memory_id)
        if not mem:
            return {"error": "memory not found"}
        hits = self.store.search(query or mem.text[:40], limit=20, include_superseded=True)
        match = next((h for h in hits if h.memory.id == memory_id), None)
        if match and match.explanation:
            return {
                "memory_id": memory_id,
                "score": match.score,
                "explanation": match.explanation.model_dump(),
                "activation": mem.compute_activation(),
            }
        return {
            "memory_id": memory_id,
            "status": mem.status,
            "activation": mem.compute_activation(),
            "importance": mem.importance,
            "confidence": mem.confidence,
            "reinforcement_count": mem.reinforcement_count,
        }

    def detect_contradictions(self) -> list[dict[str, Any]]:
        from .contradiction import detect_conflicts
        mems = self.store.list(status="active", limit=500)
        return detect_conflicts(mems)

    def resolve_contradiction(self, conflict: dict[str, Any] | None = None, *,
                              active_id: str | None = None, superseded_id: str | None = None,
                              reason: str = "manual_resolution", auto_supersede: bool = True) -> dict[str, Any]:
        if active_id and superseded_id:
            self.store.supersede(old_memory_id=superseded_id, new_memory_id=active_id)
            return {"status": "resolved", "action": "superseded", "active_id": active_id, "superseded_id": superseded_id, "reason": reason}
        if conflict:
            from .contradiction import resolve_conflict
            return resolve_conflict(self.store, conflict, auto_supersede=auto_supersede)
        return {"status": "noop"}

    def evaluate_quality(self, cases: list[dict] | None = None) -> dict[str, Any]:
        """Measure recall precision, provenance, and temporal correctness."""
        if not cases:
            cases = [
                {"query": "what do I prefer?", "expected_words": ["prefer", "like", "use"]},
                {"query": "what project am I building?", "expected_words": ["project", "build", "worker"]},
            ]
        total = len(cases)
        matched = 0
        provenance_present = 0
        total_hits = 0
        for c in cases:
            rec = self.recall(c["query"], limit=5)
            hits = rec.get("memory_hits", [])
            total_hits += len(hits)
            for h in hits:
                if h.get("source") or h.get("evidence"):
                    provenance_present += 1
            text_block = rec["context"].lower()
            if any(w in text_block for w in c.get("expected_words", [])):
                matched += 1

        return {
            "cases_evaluated": total,
            "recall_precision": round(matched / total, 2) if total else 1.0,
            "provenance_coverage": round(provenance_present / total_hits, 2) if total_hits else 1.0,
            "total_hits_inspected": total_hits,
        }

    def rebuild_graph(self) -> dict[str, Any]:
        """Wipe the knowledge graph and queue every memory for re-extraction with
        the current extractor. The enricher (LLM-first) refills it — call enrich()
        repeatedly, or let the scheduler do it in the background. Memories untouched."""
        with self.store._lock:
            self.store._conn.execute("DELETE FROM relations")
            self.store._conn.execute("DELETE FROM entities")
            self.store._conn.commit()
        self.store.reset_graphed()
        return {"reset": True, "remaining": self._queue_count(),
                "entities": 0, "facts": 0}

    def prune(self) -> dict[str, Any]:
        """Delete graph entities that fail the current quality filter (and their
        facts). Keeps all good LLM/heuristic work — just sweeps out junk like common
        words captured as entities. Cheap; safe to run after every enrichment."""
        return self.graph.prune_noise()

    def enrich_until_done(self, provider_name: str | None = None,
                          max_batches: int = 200, fast: bool = False,
                          model_name: str | None = None) -> dict[str, Any]:
        """Drain the enrichment queue (bounded). Safe to run in a background thread.
        `fast=True` uses the free offline heuristic (auto/background); the default
        uses the connected LLM for a rich, precise graph (on-demand)."""
        total = {"processed": 0, "entities": 0, "facts": 0, "remaining": 0,
                 "tokens_in": 0, "tokens_out": 0, "tokens": 0}
        for _ in range(max_batches):
            r = self.enrich(limit=40, provider_name=provider_name, fast=fast,
                            model_name=model_name)
            for k in ("processed", "entities", "facts", "tokens_in", "tokens_out", "tokens"):
                total[k] += r.get(k, 0)
            total["remaining"] = r["remaining"]
            if r["processed"] == 0 or r["remaining"] == 0:
                break
        return total

    # ── server-side enrichment job (survives frontend refresh) ──────────────
    def start_enrich(self, provider_name: str | None = None,
                     model_name: str | None = None) -> dict[str, Any]:
        import threading
        with self._enrich_lock:
            if self._enrich_state["running"]:
                return self.enrich_status()
            import time
            self._enrich_state.update({
                "running": True, "stop": False, "processed": 0, "entities": 0,
                "facts": 0, "tokens_in": 0, "tokens_out": 0, "estimated": False,
                "provider": None, "found": [], "started": time.time(),
                "remaining": self._queue_count()})
        threading.Thread(target=self._enrich_loop,
                         args=(provider_name, model_name), daemon=True).start()
        return self.enrich_status()

    def _enrich_loop(self, provider_name: str | None,
                     model_name: str | None = None) -> None:
        try:
            while not self._enrich_state["stop"]:
                r = self.enrich(limit=8, provider_name=provider_name,
                                model_name=model_name)
                with self._enrich_lock:
                    s = self._enrich_state
                    s["processed"] += r["processed"]; s["entities"] += r["entities"]
                    s["facts"] += r["facts"]; s["tokens_in"] += r["tokens_in"]
                    s["tokens_out"] += r["tokens_out"]; s["remaining"] = r["remaining"]
                    s["estimated"] = s["estimated"] or r["tokens_estimated"]
                    if r.get("provider"):
                        s["provider"] = r["provider"]
                    if r.get("found"):
                        s["found"] = r["found"]
                if r["processed"] == 0 or r["remaining"] == 0:
                    break
        finally:
            # sweep any junk entities the pass may have added before finishing
            with suppressed("self.prune()"):
                self.prune()
            with self._enrich_lock:
                self._enrich_state["running"] = False
                self._enrich_state["stop"] = False

    def stop_enrich(self) -> dict[str, Any]:
        with self._enrich_lock:
            if self._enrich_state["running"]:
                self._enrich_state["stop"] = True
        return self.enrich_status()

    def enrich_status(self) -> dict[str, Any]:
        import time
        with self._enrich_lock:
            s = dict(self._enrich_state)
        s["remaining"] = self._queue_count()
        s["cap"] = self.enrich_cap()
        s["tokens"] = s["tokens_in"] + s["tokens_out"]
        s["elapsed"] = (time.time() - s["started"]) if s["started"] else 0
        s.pop("stop", None)
        return s

    def reset(self) -> dict[str, Any]:
        """Wipe ALL brain data — every memory, entity, relation and the remembered
        connector sync state. Irreversible. Used by onboarding's 'start from zero'
        so the user rebuilds their brain from scratch."""
        before = self.store.count()
        with self.store._lock:
            for tbl in ("relations", "entities", "connector_state", "memories"):
                with suppressed("self.store._conn.execute(f'DELETE FROM {tbl}')"):
                    self.store._conn.execute(f"DELETE FROM {tbl}")
            self.store._conn.commit()
        # invalidate the in-memory vector cache so recall reflects the wipe
        self.store._dirty = True
        self.store._vecs = None
        self.store._ids = []
        return {"reset": True, "removed_memories": before}

    def run_migrations(self) -> dict[str, Any]:
        """Auto-migrate derived data when the code version changed — so every
        user's brain upgrades itself on startup, with no manual re-embed/rebuild.
        Idempotent: a no-op when versions already match."""
        from . import extract as extractor
        store = self.store
        done: dict[str, Any] = {}

        if store.count() == 0:
            return done

        # 1) embeddings: re-embed everything if the embedder (model/dim) changed
        cur_sig = store.embedder_signature()
        if store.get_meta("embedder_sig") != cur_sig:
            done["reembedded"] = store.reembed_all()
            store.set_meta("embedder_sig", cur_sig)

        # 2) knowledge graph: rebuild if the extractor version changed, then do a
        # free heuristic pass so the graph is populated (LLM enrichment is opt-in).
        if store.get_meta("extractor_version") != extractor.EXTRACTOR_VERSION:
            self.rebuild_graph()
            g = self.enrich_until_done(fast=True, max_batches=1000)
            done["graph_rebuilt"] = g.get("entities")
            store.set_meta("extractor_version", extractor.EXTRACTOR_VERSION)

        return done

    def export(self) -> dict[str, Any]:
        """A portable snapshot of the whole brain — the user owns their data and
        can back it up or move it to another machine."""
        from datetime import datetime
        mems = self.store.export_all()
        return {
            "chitragupta_backup": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "count": len(mems),
            "memories": mems,
        }

    def import_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Restore memories from an export. Dedup makes it idempotent (re-importing
        the same backup adds nothing), and the graph is rebuilt afterward."""
        mems = (data or {}).get("memories", [])
        if not isinstance(mems, list):
            return {"added": 0, "skipped": 0, "error": "no 'memories' list found"}
        added = 0
        for m in mems:
            if not isinstance(m, dict) or not (m.get("text") or "").strip():
                continue
            saved = self.store.add(
                text=m["text"], source=m.get("source") or "import",
                kind=m.get("kind") or "note", title=m.get("title"),
                uri=m.get("uri"), tags=m.get("tags") or [],
                event_date=m.get("event_date"), metadata=m.get("metadata") or {},
            )
            if saved:
                added += 1
        g = self.rebuild_graph() if added else {"entities": 0}
        return {"added": added, "skipped": len(mems) - added,
                "entities": g.get("entities", 0)}

    def reembed(self) -> dict[str, Any]:
        """Recompute all vectors (memories + entities) with the current embedder.
        Run after switching CHITRAGUPTA_EMBEDDING_PROVIDER."""
        n = self.store.reembed_all()
        g = self.rebuild_graph()   # re-extracts + re-embeds entities too
        return {"reembedded_memories": n, "graph_entities": g["entities"]}

    def stats(self) -> dict[str, Any]:
        s = self.store.stats()
        s["graph"] = self.graph.stats()
        return s


@once
def _prose_suffixes() -> tuple[str, ...]:
    """The suffixes the files connector treats as prose, as a TUPLE.

    `self._PROSE_EXT` was read here and defined nowhere, so every heuristic
    enrichment pass raised `AttributeError` on the first memory carrying a uri —
    caught and logged by the scheduler, which is why it ran unnoticed after
    every sync while the graph quietly stayed empty.

    A tuple and not the connector's set: `str.endswith` takes a str or a tuple
    and raises TypeError on a set. Converted once here rather than per memory in
    the enrichment loop, and imported from the connector so the list of what
    counts as prose is written down once.
    """
    from ..core.chunk import PROSE_EXT
    return tuple(sorted(PROSE_EXT))


@once
def get_brain() -> Brain:
    return Brain()
