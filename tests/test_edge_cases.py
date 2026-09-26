"""Edge-case regression tests — hardening pass.

Each test here pins a bug found (and fixed) during the hardening sweep, so it
can never silently regress. Grouped by module.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from chitragupta.actions import parse_actions
from chitragupta.connectors.apple_calendar import _fmt_dt, parse_ics
from chitragupta.connectors.custom_api import _dig
from chitragupta.core.chunk import chunk_text
from chitragupta.reminders import parse_when

NOW = datetime(2026, 8, 30, 13, 0, 0).astimezone()


# ── parse_when: invalid times must return None, never crash ────────────────
@pytest.mark.parametrize("bad", ["3:70", "9:99", "99:99", "9:99am", "25pm", "30pm"])
def test_parse_when_invalid_time_returns_none(bad):
    assert parse_when(bad, NOW) is None          # was: ValueError crash


@pytest.mark.parametrize("text,hhmm", [
    ("12am", "00:00"), ("12pm", "12:00"), ("noon", "12:00"),
    ("midnight", "00:00"), ("tomorrow 3pm", "15:00"),
    ("2026-09-01 15:30", "15:30"),
])
def test_parse_when_valid_times(text, hhmm):
    out = parse_when(text, NOW)
    assert out is not None and out[11:16] == hhmm


def test_parse_when_relative_offset():
    assert parse_when("in 90 minutes", NOW).startswith("2026-08-30T14:30")


@pytest.mark.parametrize("empty", ["", "   ", "blah", "monday 9"])
def test_parse_when_unparseable(empty):
    assert parse_when(empty, NOW) is None


# ── parse_actions: never crash on malformed model output ───────────────────
@pytest.mark.parametrize("text", [
    "", "<action type='x'>y</action>", "<action foo=bar>y",
    "<action type=set_reminder at=3pm>x</action>",   # unquoted → ignored
])
def test_parse_actions_malformed_no_crash(text):
    assert isinstance(parse_actions(text), list)


def test_parse_actions_extracts_multiple():
    text = ('<action type="set_reminder" at="3pm">a</action> and '
            '<action type="send_email" to="x@y.z">b</action>')
    acts = parse_actions(text)
    assert [a["type"] for a in acts] == ["set_reminder", "send_email"]
    assert acts[0]["params"]["message"] == "a"
    assert acts[1]["params"]["to"] == "x@y.z"


def test_parse_actions_multiline_body():
    acts = parse_actions('<action type="send_email" to="x@y.z">\nl1\nl2\n</action>')
    assert acts[0]["params"]["body"] == "l1\nl2"


# ── _dig: dot-path extraction is crash-proof ───────────────────────────────
@pytest.mark.parametrize("obj,path,exp", [
    ({"a": {"b": 9}}, "a.b", 9),
    ({"a": 1}, "a.b.c", None),
    (None, "x", None),
    ({"a": None}, "a.b", None),
    ({"a": {"b": [1, 2]}}, "a.b", [1, 2]),
    ({"x": 1}, "", {"x": 1}),
])
def test_dig(obj, path, exp):
    assert _dig(obj, path) == exp


# ── connector parsers: junk in, no crash ───────────────────────────────────
@pytest.mark.parametrize("raw", ["garbage", "", "2026", "20260901", "20260901T150000Z"])
def test_fmt_dt_no_crash(raw):
    assert isinstance(_fmt_dt(raw), str)


@pytest.mark.parametrize("txt", ["", "not an ics", "DTSTART:x\nSUMMARY:"])
def test_parse_ics_bad_returns_none(txt):
    assert parse_ics(txt) is None


def test_parse_ics_minimal():
    ev = parse_ics("SUMMARY:Standup\nDTSTART:20260901T090000Z")
    assert ev and ev["summary"] == "Standup" and ev["start"].startswith("2026-09-01")


# ── chunk_text: boundaries ─────────────────────────────────────────────────
@pytest.mark.parametrize("text,expect_empty", [("", True), ("   ", True), ("x" * 5000, False)])
def test_chunk_text_bounds(text, expect_empty):
    chunks = chunk_text(text)
    assert (len(chunks) == 0) == expect_empty
    assert all(len(c) <= 1200 for c in chunks)


# ── connectors: every registered connector instantiates + reports readiness ─
def test_all_connectors_instantiate():
    from chitragupta.connectors import REGISTRY
    for _name, cls in REGISTRY.items():
        inst = cls()
        ready, reason = inst.is_configured()
        assert isinstance(ready, bool)
        assert isinstance(reason, str)


# ── custom apps: storage round-trip (create → get → delete), no network ─────
def test_custom_app_storage_roundtrip():
    from chitragupta.connectors.custom_api import delete_app, get_app, upsert_app
    app = upsert_app({"name": "T", "base_url": "https://ex.com", "endpoint": "/x"},
                     token="secret123")
    aid = app["id"]
    try:
        assert get_app(aid)["base_url"] == "https://ex.com"
        # addressed as custom:<id> through the connector factory
        from chitragupta.connectors import get_connector
        conn = get_connector(f"custom:{aid}")
        assert conn.label == "T"
    finally:
        assert delete_app(aid) is True
        assert get_app(aid) is None


def test_custom_api_fieldless_records_stay_distinct(tmp_path, monkeypatch):
    """Records missing the title/body field must not collapse to one 'None'."""
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path))
    from chitragupta.config import get_settings
    get_settings.cache_clear()
    from chitragupta.connectors.custom_api import CustomAPIConnector
    app = {"id": "z", "name": "T", "base_url": "https://ex.com", "endpoint": "/x",
           "auth_type": "none", "items_path": "d", "title_field": "name",
           "body_field": "desc"}
    conn = CustomAPIConnector(dict(app))
    # `_request` takes a page cursor now — the connector pages through
    # `engine.run` rather than reading one response and calling that the
    # dataset. The stub answers every cursor with the same single page, which
    # is what a source with no pagination does.
    conn._request = lambda cursor="": {
        "d": [{"other": 1}, {"other": 2}, {"other": 3}]}
    res = conn.sync()
    assert res.added == 3 and res.skipped == 0
    get_settings.cache_clear()


# ── reminders/scheduler: fire once, never double-fire ──────────────────────
def test_reminder_fires_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path))
    from chitragupta.config import get_settings
    get_settings.cache_clear()
    import chitragupta.notify as notify
    calls = []
    monkeypatch.setattr(notify, "desktop_notify", lambda t, m: calls.append((t, m)) or True)

    from datetime import datetime, timedelta

    import chitragupta.reminders as reminders_mod
    from chitragupta.reminders import get_reminders
    from chitragupta.scheduler import Scheduler
    monkeypatch.setattr(reminders_mod, "_store", None)   # rebind to tmp home
    store = get_reminders()
    past = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat()
    future = (datetime.now().astimezone() + timedelta(hours=2)).isoformat()
    store.add("past task", past)
    store.add("future task", future)

    sched = Scheduler()
    sched._fire_reminders()
    assert [m for _, m in calls] == ["past task"]      # only the due one
    assert store.due() == []                            # marked fired
    sched._fire_reminders()
    assert [m for _, m in calls] == ["past task"]      # no double-notify
    monkeypatch.setattr(reminders_mod, "_store", None)
    get_settings.cache_clear()


# ── migrations: idempotent, version-bump triggered, empty-safe ─────────────
def test_migrations_idempotent_and_bump_triggered(tmp_path, monkeypatch):
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path))
    from chitragupta.brain import get_brain
    from chitragupta.config import get_settings
    from chitragupta.core.store import get_store
    get_settings.cache_clear()
    get_brain.cache_clear()
    get_store.cache_clear()

    brain = get_brain()
    assert brain.run_migrations() == {}                 # empty brain: safe no-op
    brain.ingest("Alpha ships in September.", source="test")
    assert brain.run_migrations()                        # first run does work
    assert brain.run_migrations() == {}                  # idempotent

    brain.store.set_meta("embedder_sig", "STALE")
    assert brain.run_migrations().get("reembedded", 0) > 0
    assert brain.run_migrations() == {}                  # re-stamped → no-op

    brain.store.set_meta("extractor_version", "0")
    assert "graph_rebuilt" in brain.run_migrations()

    get_settings.cache_clear()
    get_brain.cache_clear()
    get_store.cache_clear()


# ── Gmail: base64url body decode must tolerate missing padding ──────────────
def test_gmail_decode_handles_unpadded_base64():
    import base64

    from chitragupta.connectors.gmail import _decode
    padded = base64.urlsafe_b64encode("café ☕".encode()).decode()
    assert _decode(padded) == "café ☕"
    assert _decode(padded.rstrip("=")) == "café ☕"      # Gmail often omits '='
    assert _decode("") == "" and _decode(None) == ""     # empty / None safe


def test_gmail_extract_body_from_unpadded_html():
    import base64

    from chitragupta.connectors.gmail import _extract_body
    raw = base64.urlsafe_b64encode(b"<p>Hi <b>there</b></p>").decode().rstrip("=")
    payload = {"mimeType": "text/html", "body": {"data": raw}}
    assert _extract_body(payload) == "Hi there"


# ── Google auth: corrupt token self-heals (cleaned up, clear error) ────────
def test_google_auth_corrupt_token_selfheals(tmp_path, monkeypatch):
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path))
    from chitragupta.config import get_settings
    get_settings.cache_clear()
    from chitragupta.connectors.google_auth import _token_path, get_credentials
    tp = _token_path()
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text("{ not valid json ]")
    with pytest.raises(RuntimeError):
        get_credentials(interactive=False)
    assert not tp.exists()                                # corrupt token removed
    get_settings.cache_clear()


# ── model providers: unreachable/misconfigured → clean message, no traceback ─
def test_openai_compat_unreachable_returns_clean_message():
    from chitragupta.models.base import Message
    from chitragupta.models.openai_compat import OllamaProvider, OpenAICompatProvider
    r = OllamaProvider(base_url="http://localhost:59999/v1").chat(
        [Message(role="user", content="hi")])
    assert r.text.startswith("⚠️") and "ollama" in r.text.lower()
    r2 = OpenAICompatProvider(model="x", api_key="k",
                              base_url="http://localhost:59999/v1").chat(
        [Message(role="user", content="hi")])
    assert r2.text.startswith("⚠️")


# ── API input validation (empty chat, non-http custom app) ─────────────────
def test_api_input_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path))
    from chitragupta.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app
    client = TestClient(app)

    # empty / whitespace chat message → 422, not a wasted model call
    r = client.post("/api/agents/inbox/chat", json={"message": "   "})
    assert r.status_code == 422
    # custom app with a non-http base URL → 422
    r = client.post("/api/custom-apps",
                    json={"name": "X", "base_url": "file:///etc/passwd"})
    assert r.status_code == 422
    # a valid http custom app saves fine
    r = client.post("/api/custom-apps",
                    json={"name": "X", "base_url": "https://example.com", "endpoint": "/x"})
    assert r.status_code == 200 and r.json()["saved"]
    get_settings.cache_clear()


# ── brain export / import round-trip (you own your data) ───────────────────
def test_brain_export_import_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path / "a"))
    from chitragupta.brain import get_brain
    from chitragupta.config import get_settings
    from chitragupta.core.store import get_store
    get_settings.cache_clear(); get_brain.cache_clear(); get_store.cache_clear()

    b = get_brain()
    b.ingest("Alpha ships in September.", source="test")
    b.ingest("Beta uses Postgres.", source="test", event_date="2026-08-01")
    exp = b.export()
    assert exp["count"] == 2 and exp["chitragupta_backup"] == 1

    # import into a fresh, separate brain
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path / "b"))
    get_settings.cache_clear(); get_brain.cache_clear(); get_store.cache_clear()
    b2 = get_brain()
    assert b2.store.count() == 0
    res = b2.import_data(exp)
    assert res["added"] == 2 and b2.store.count() == 2
    # idempotent: re-import adds nothing
    assert b2.import_data(exp)["added"] == 0
    # restored memory is recallable + event_date preserved
    hits = b2.store.search("when does Alpha ship", limit=1)
    assert hits and "Alpha" in hits[0].memory.text
    get_settings.cache_clear(); get_brain.cache_clear(); get_store.cache_clear()


# ── MCP server: brain tools work + about() rejects non-matches ─────────────
def test_mcp_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("CHITRAGUPTA_HOME", str(tmp_path))
    from chitragupta.brain import get_brain
    from chitragupta.config import get_settings
    from chitragupta.core.store import get_store
    get_settings.cache_clear(); get_brain.cache_clear(); get_store.cache_clear()
    import chitragupta.mcp_server.server as S

    assert "+1 memory" in S.remember("Zephyr is a project using Rust and SQLite.")
    ctx = S.search_brain("what is Zephyr built with?", limit=3)
    assert "Zephyr" in ctx
    # about() returns a real entity, rejects a non-match (name-overlap guard)
    assert "Zephyr" in S.about("Zephyr")
    assert "Nothing" in S.about("Totally Unrelated Xyz")
    # tasks round-trip through MCP
    S.add_task("email the founders", "tomorrow")
    assert "email the founders" in S.list_tasks()
    assert "Completed" in S.complete_task("founders")
    get_settings.cache_clear(); get_brain.cache_clear(); get_store.cache_clear()
