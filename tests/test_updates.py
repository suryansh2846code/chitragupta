"""The update check — what it tells the server, and what it refuses to.

This is the first thing in Chitragupta that talks to a server of ours, so the
test that matters most is `test_a_check_sends_only_these_fields`: it pins the
request to the three values the server needs in order to pick an appcast entry,
and fails if a fourth ever appears.

The rest covers the two things that would quietly break it: version comparison
done as strings (`"0.10.0" < "0.9.0"` is true for strings, so the tenth release
would stop offering updates), and a failed check overwriting a real answer from
yesterday.
"""
from __future__ import annotations

import json
import time

import pytest
from starlette.testclient import TestClient

from chitragupta import updates
from chitragupta.api.app import app
from chitragupta.config import get_settings

FEED = "https://example.invalid/appcast.json"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A home of this test's own, and no feed unless a test asks for one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(get_settings(), "home", home)
    monkeypatch.setattr(get_settings(), "update_feed", "")
    monkeypatch.setattr(get_settings(), "update_check", True)
    return home


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "update_feed", FEED)
    return FEED


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _serve(monkeypatch, payload, status=200):
    """Stand in for the feed, and record what was asked of it."""
    seen: list[dict] = []

    def fake_get(url, params=None, timeout=None, follow_redirects=None):
        seen.append({"url": url, "params": dict(params or {})})
        return _Response(payload, status)

    import httpx
    monkeypatch.setattr(httpx, "get", fake_get)
    return seen


def _boom(monkeypatch):
    def fake_get(*_a, **_kw):
        raise OSError("no network")

    import httpx
    monkeypatch.setattr(httpx, "get", fake_get)


# ── what leaves the machine ─────────────────────────────────────────────────

def test_a_check_sends_only_these_fields(monkeypatch, configured):
    """The exhaustive list, and the reason each one is allowed: the server needs
    it to decide *which* update applies.

    No install id, no device id, nothing persistent — which is what lets this
    run without an identifier at all. See the module docstring in
    `chitragupta/updates.py`.
    """
    seen = _serve(monkeypatch, {"releases": []})
    updates.check(force=True)

    assert len(seen) == 1
    assert set(seen[0]["params"]) == {"app", "os", "arch"}
    assert set(seen[0]["params"]) == set(updates.SENT_FIELDS)


def test_nothing_identifying_is_in_the_payload(configured):
    """A guard against the field somebody adds later without thinking.

    Written as a denylist of the words such a field would be called, because
    the allowlist above only fails if the *set* changes — and both should.
    """
    payload = updates.request_payload()
    blob = json.dumps(list(payload)).lower()
    for banned in ("id", "uuid", "user", "email", "device", "install",
                   "machine", "serial", "token", "session", "memor",
                   "connector", "provider", "agent", "locale", "name"):
        assert banned not in blob, f"{banned!r} appears in {sorted(payload)!r}"


def test_an_unconfigured_build_never_asks_anybody(monkeypatch):
    """No feed means no request at all — not a request to an empty URL."""
    seen = _serve(monkeypatch, {"releases": []})
    answer = updates.check(force=True)

    assert seen == [], "a build with no feed must not make a request"
    assert answer["configured"] is False
    assert answer["update"] is None
    assert "no update feed" in answer["reason"]


def test_a_user_who_turns_it_off_is_not_asked_again(monkeypatch, configured):
    seen = _serve(monkeypatch, {"releases": []})
    updates.set_enabled(False)

    assert updates.enabled() is False
    assert updates.due() is False
    # The automatic path must respect it; a forced press is the user asking.
    updates.check(force=False)
    assert seen == []


def test_turning_it_off_is_remembered_on_disk(configured, isolated):
    """The preference is in `updates.json`, not in Settings — which is read
    from the environment and would forget it on the next start."""
    updates.set_enabled(False)
    saved = json.loads((isolated / updates.STATE_FILE).read_text())
    assert saved["enabled"] is False
    assert updates.enabled() is False


# ── comparing versions ──────────────────────────────────────────────────────

@pytest.mark.parametrize("newer,older", [
    ("0.2.0", "0.1.0"),
    ("1.0.0", "0.9.9"),
    ("0.10.0", "0.9.0"),        # the one string comparison gets wrong
    ("0.1.1", "0.1.0"),
    ("1.2.10", "1.2.9"),
    ("2.0", "1.9.9"),
])
def test_a_newer_version_is_recognised(newer, older):
    assert updates.is_newer(newer, older)
    assert not updates.is_newer(older, newer)


@pytest.mark.parametrize("a,b", [("1.2.0", "1.2"), ("1.0", "1.0.0"),
                                 ("0.1.0", "0.1.0")])
def test_equal_versions_are_not_newer_either_way(a, b):
    assert not updates.is_newer(a, b)
    assert not updates.is_newer(b, a)


def test_a_prerelease_compares_as_its_release():
    assert updates.parse_version("1.2.0-beta.1") == (1, 2, 0)
    assert updates.parse_version("v0.3.0") == (0, 3, 0)


def test_rubbish_does_not_raise():
    assert updates.parse_version("") == (0,)
    assert updates.parse_version("not a version") == (0,)


# ── reading the feed ────────────────────────────────────────────────────────

def test_a_newer_release_is_offered_with_where_to_get_it(monkeypatch, configured):
    _serve(monkeypatch, {"releases": [
        {"version": "9.9.9", "url": "https://example.invalid/x.dmg",
         "notes": "Fixes a thing", "published": "2026-10-06"}]})
    answer = updates.check(force=True)

    assert answer["update"]["version"] == "9.9.9"
    assert answer["update"]["url"].endswith(".dmg")
    assert answer["checked"] is True
    assert answer["reason"] == ""


def test_an_older_release_in_the_feed_is_ignored(monkeypatch, configured):
    _serve(monkeypatch, {"releases": [{"version": "0.0.1"}]})
    assert updates.check(force=True)["update"] is None


def test_the_newest_wins_even_if_the_feed_is_out_of_order(monkeypatch,
                                                          configured):
    """An appcast written by hand will one day be out of order, and the user
    should still be offered the newest build."""
    _serve(monkeypatch, {"releases": [
        {"version": "9.0.0"}, {"version": "9.9.9"}, {"version": "9.5.0"}]})
    assert updates.check(force=True)["update"]["version"] == "9.9.9"


def test_a_bare_list_feed_works_too(monkeypatch, configured):
    _serve(monkeypatch, [{"version": "9.9.9"}])
    assert updates.check(force=True)["update"]["version"] == "9.9.9"


def test_junk_in_the_feed_is_skipped_not_fatal(monkeypatch, configured):
    _serve(monkeypatch, {"releases": ["nonsense", 7, None, {},
                                      {"version": "9.9.9"}]})
    assert updates.check(force=True)["update"]["version"] == "9.9.9"


def test_an_empty_feed_is_not_an_error(monkeypatch, configured):
    _serve(monkeypatch, {"releases": []})
    answer = updates.check(force=True)
    assert answer["update"] is None
    assert answer["reason"] == ""


# ── failing ─────────────────────────────────────────────────────────────────

def test_no_network_says_so_without_nagging(monkeypatch, configured):
    _boom(monkeypatch)
    answer = updates.check(force=True)
    assert answer["update"] is None
    assert "Could not reach" in answer["reason"]


def test_a_failed_check_does_not_forget_yesterdays_answer(monkeypatch,
                                                          configured):
    """A machine that goes offline must not stop offering an update it already
    found — the failure is ours, and the release is still out there."""
    _serve(monkeypatch, {"releases": [{"version": "9.9.9"}]})
    assert updates.check(force=True)["update"]["version"] == "9.9.9"

    _boom(monkeypatch)
    after = updates.check(force=True)
    assert after["update"]["version"] == "9.9.9", \
        "a failed check overwrote a real answer"
    assert "Could not reach" in after["reason"]


def test_an_http_error_is_a_reason_not_a_crash(monkeypatch, configured):
    _serve(monkeypatch, {"releases": []}, status=503)
    answer = updates.check(force=True)
    assert "Could not reach" in answer["reason"]


def test_catching_up_clears_a_stale_offer(monkeypatch, configured):
    """Once the running build IS the newest, the offer has to go — otherwise
    the app tells a freshly-updated user to update."""
    _serve(monkeypatch, {"releases": [{"version": "9.9.9"}]})
    updates.check(force=True)
    assert updates.state()["update"] is not None

    _serve(monkeypatch, {"releases": [{"version": "0.0.1"}]})
    updates.check(force=True)
    assert updates.state()["update"] is None


# ── once a day, which is the privacy argument ───────────────────────────────

def test_the_automatic_check_is_once_a_day(monkeypatch, configured):
    """Request volume is only an install count because of this.

    One request a day per install is what lets the check carry no identifier
    and still answer "how many people use this".
    """
    seen = _serve(monkeypatch, {"releases": []})
    assert updates.due() is True
    updates.check(force=False)
    assert len(seen) == 1

    assert updates.due() is False
    updates.check(force=False)
    assert len(seen) == 1, "a second check the same day must not be made"

    # A day later it is due again.
    assert updates.due(now=time.time() + updates.CHECK_INTERVAL + 1) is True


def test_a_user_pressing_check_is_never_rate_limited(monkeypatch, configured):
    """The limit keeps the *automatic* check honest; it must not refuse a
    person who asked."""
    seen = _serve(monkeypatch, {"releases": []})
    updates.check(force=True)
    updates.check(force=True)
    assert len(seen) == 2


# ── the endpoints ───────────────────────────────────────────────────────────

def test_state_is_readable_on_an_unconfigured_build(client):
    body = client.get("/api/updates/state").json()
    assert body["configured"] is False
    assert body["enabled"] is False
    assert body["current"]
    assert body["sends"] == list(updates.SENT_FIELDS)
    assert body["interval_hours"] == 24


def test_state_publishes_what_a_check_would_send(client, configured):
    """The screen has to be able to tell the user, in the UI, exactly what
    leaves their Mac. It cannot do that if it has to guess."""
    body = client.get("/api/updates/state").json()
    assert body["configured"] is True
    assert body["feed"] == FEED
    assert body["sends"] == ["app", "os", "arch"]


def test_the_check_endpoint_reports_an_update(client, monkeypatch, configured):
    _serve(monkeypatch, {"releases": [
        {"version": "9.9.9", "url": "https://example.invalid/x.dmg"}]})
    body = client.post("/api/updates/check").json()
    assert body["update"]["version"] == "9.9.9"


def test_the_check_endpoint_does_not_500_when_the_feed_is_down(
        client, monkeypatch, configured):
    """A version check is the least important thing this app does and must not
    be able to break a screen."""
    _boom(monkeypatch)
    response = client.post("/api/updates/check")
    assert response.status_code == 200
    assert "Could not reach" in response.json()["reason"]


def test_the_setting_round_trips_through_the_endpoint(client, configured):
    off = client.post("/api/updates/settings", json={"enabled": False}).json()
    assert off["enabled"] is False
    assert client.get("/api/updates/state").json()["enabled"] is False

    on = client.post("/api/updates/settings", json={"enabled": True}).json()
    assert on["enabled"] is True
    assert client.get("/api/updates/state").json()["enabled"] is True


def test_a_prerelease_is_never_offered_over_its_own_release():
    """The regression for a version parse that read every component.

    `1.2.0-beta.1` parsed to `(1, 2, 0, 1)`, which compares above `1.2.0`
    padded to `(1, 2, 0, 0)` — so a user on the release was told a beta they
    had already moved past was an upgrade. Parsing now stops at the first
    component that is not purely numeric.
    """
    assert not updates.is_newer("1.2.0-beta.1", "1.2.0")
    assert not updates.is_newer("1.2.0-rc.9", "1.2.0")
    assert not updates.is_newer("2.0.0-alpha.7", "2.0.0")
    # and a real later release still wins over a prerelease of the older one
    assert updates.is_newer("1.3.0", "1.2.0-beta.1")
