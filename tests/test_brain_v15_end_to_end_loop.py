"""End-to-End Regression and Complete Loop Test for Brain v1.5 (Section 60 & 61).

Tests the full closed loop:
USER MESSAGE -> BRAIN RECALL -> AGENT -> RESPONSE -> BRAIN INGESTION ->
ENTITY ENRICHMENT -> GRAPH -> NEXT QUERY -> CORRECT RECALL

Also validates:
- Connector repeated sync idempotency
- Secret redaction in agent & ingestion loop
- Historical change & contradiction resolution
- Open loop tracking throughout the interaction
"""
import pytest

from chitragupta.agents.runtime import run_turn
from chitragupta.config import get_settings
from chitragupta.core.models import MemoryStatus, MemoryType, OpenLoopPriority


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """A brain of this test's own — including its STORE.

    `get_brain` and `get_store` are cached separately, and this cleared only
    the first. So a "clean" brain here was handed the singleton store built
    against the real home, and every test in this file has in fact been
    writing into the shared one. Nothing failed, because these tests assert
    that a specific memory comes back from `recall()` — which it did, while
    the shared store stayed sparse enough for it to rank.

    It stopped being sparse. The fix is the isolation the fixture always
    claimed: recall ranking is not something a test should be able to lose by
    running after somebody else.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "home", tmp_path)
    monkeypatch.setattr(settings, "model_provider", "mock")
    monkeypatch.setattr(settings, "model_name", "mock-v1")
    from chitragupta.brain.brain import get_brain
    from chitragupta.core.store import get_store

    get_brain.cache_clear()
    get_store.cache_clear()
    yield tmp_path
    get_brain.cache_clear()
    get_store.cache_clear()


def test_end_to_end_agent_brain_loop(clean_env):
    """Verify Section 60:
    User message -> recall -> response -> ingestion -> enrichment -> next recall
    """
    # 1. User tells agent a durable personal fact with entities
    turn1 = run_turn("personal", "I am building TURNOVER with my collaborator Ekta.")
    assert turn1.reply is not None
    assert any(s.name == "auto_learn" for s in turn1.trace)

    # Learning from a turn is deliberately off the critical path — the reply
    # arrives, then the brain catches up. Wait for it before reading the brain;
    # the loop being tested is the same one, just not blocking the answer.
    from chitragupta.agents import background
    assert background.wait_for_idle(timeout=30), "background learning did not finish"

    # 2. Verify Brain ingested the fact and created entities in graph
    from chitragupta.brain import get_brain
    b = get_brain()
    memories = b.store.list()
    assert len(memories) >= 1
    assert any("turnover" in m.text.lower() for m in memories)

    # Check entities in Graph
    entities = b.list_entities()
    assert len(entities) >= 1

    # 3. Next query asks about what the user is working on
    recalled = b.recall("What project am I building?")
    assert "context" in recalled
    assert len(recalled["memory_hits"]) >= 1
    top_hit = recalled["memory_hits"][0]
    assert "turnover" in top_hit["text"].lower() or "turnover" in recalled["context"].lower()
    assert top_hit["explanation"] is not None
    assert top_hit["status"] == MemoryStatus.ACTIVE.value


def test_connector_sync_idempotency(clean_env):
    """Verify Section 61:
    Same connector sync runs multiple times -> zero duplicate explosion.
    """
    from chitragupta.brain import get_brain
    b = get_brain()

    # Sync message 1 from Gmail
    r1 = b.ingest(
        "Quarterly budget review meeting scheduled with Finance team on Friday.",
        source="gmail",
        source_id="msg_98765",
        title="Budget Review",
        kind="event",
    )
    assert r1["memories"] == 1

    # Sync exact same message again 5 times (simulating scheduled poll)
    for _ in range(5):
        r_dup = b.ingest(
            "Quarterly budget review meeting scheduled with Finance team on Friday.",
            source="gmail",
            source_id="msg_98765",
            title="Budget Review",
            kind="event",
        )
        assert r_dup["memories"] == 0  # Deduplicated

    # Total memories in store should be exactly 1
    gmail_mems = [m for m in b.store.list() if m.source == "gmail"]
    assert len(gmail_mems) == 1


def test_secret_redaction_in_loop(clean_env):
    """Verify Section 43/44:
    Secrets do not enter long-term semantic memory in plain text.
    """
    from chitragupta.brain import get_brain
    b = get_brain()

    res = b.remember(
        "Connecting to database with API key sk-live-1234567890abcdef1234567890 and secret password=SuperSecret999!",
        title="DB Creds",
        source="manual",
    )
    m = b.store.get(res["id"])
    assert "sk-live-1234567890abcdef1234567890" not in m.text
    assert "[REDACTED_KEY]" in m.text or "[REDACTED_SECRET]" in m.text
    assert "SuperSecret999" not in m.text
    assert "[REDACTED_PASSWORD]" in m.text or "[REDACTED_SECRET]" in m.text


def test_historical_preference_contradiction_flow(clean_env):
    """Verify Section 61:
    User changes preference:
    - new preference is active
    - old preference is preserved as superseded
    - history survives
    """
    from chitragupta.brain import get_brain
    b = get_brain()

    # Old preference
    m1 = b.remember(
        "I use React for frontend development.",
        memory_type=MemoryType.PREFERENCE.value,
        valid_from="2025-01-01",
        valid_until="2026-01-01",
    )

    # User announces change
    m2 = b.remember(
        "I am moving away from React and now using Vanilla JS.",
        memory_type=MemoryType.PREFERENCE.value,
        valid_from="2026-01-01",
    )

    # Detect contradiction
    conflicts = b.detect_contradictions()
    assert len(conflicts) >= 1

    # Resolve contradiction: supersede m1 with m2
    b.resolve_contradiction(
        active_id=m2["id"],
        superseded_id=m1["id"],
        reason="User switched stack in 2026",
    )

    # Verify both records survived in DB
    m1_updated = b.store.get(m1["id"])
    m2_updated = b.store.get(m2["id"])

    assert m1_updated is not None
    assert m1_updated.status == MemoryStatus.SUPERSEDED.value
    assert m1_updated.valid_until is not None

    assert m2_updated is not None
    assert m2_updated.status == MemoryStatus.ACTIVE.value

    # Recall current preference
    rec = b.recall("What is my frontend stack?")
    # Active hit should rank above or equal with higher status confidence
    active_hits = [h for h in rec["memory_hits"] if h["id"] == m2["id"]]
    assert len(active_hits) > 0


def test_open_loops_full_lifecycle(clean_env):
    """Verify Section 17 & 18:
    Open loop creation, recall integration, and completion.
    """
    from chitragupta.brain import get_brain
    b = get_brain()

    loop = b.create_open_loop(
        "Prepare staging deployment checklist for TURNOVER",
        priority=OpenLoopPriority.URGENT.value,
        related_project="TURNOVER",
        due_at="2026-09-12T12:00:00Z",
    )
    assert loop["status"] == "open"

    # Recall should package open loops into prompt context
    recalled = b.recall("What are our current open tasks for TURNOVER?")
    assert "ACTIVE OPEN LOOPS & COMMITMENTS" in recalled["context"]
    assert "Prepare staging deployment checklist" in recalled["context"]

    # Mark completed
    completed = b.complete_open_loop(loop["id"])
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None

    # Recalled active loops should no longer include completed
    active_loops = b.get_open_loops(status="open")
    assert not any(l["id"] == loop["id"] for l in active_loops)
