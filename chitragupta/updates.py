"""Telling the user a newer version exists — and nothing else about them.

Two problems, one request. [`docs/DISTRIBUTION.md`](../docs/DISTRIBUTION.md):

> **Updates.** A `.dmg` has no update mechanism. Every new version is a fresh
> download unless something like Sparkle is added. Worth deciding early: the
> in-app "check for updates" affordance is much easier to add before there are
> users on old versions.

So: users are stranded on whatever version they first installed, and there is
no way to ship them a security fix. That is the problem worth fixing. The
*second* problem it happens to fix is that nothing in this app has ever been
able to answer "how many people use this" — see
[`docs/AUTH-ANALYSIS.md`](../docs/AUTH-ANALYSIS.md) §4.

**The counting falls out of the feature, and needs no identifier.** This is the
design decision to understand before changing anything here:

* The check runs **at most once a day per install** (`CHECK_INTERVAL`), and the
  last-checked time is kept on disk so a relaunch does not re-ask.
* It therefore sends **no install id, no device id, no cookie, nothing
  persistent**. One request a day per install means request volume *is* an
  active-install count, which is the number a product decision wants, with
  nothing that could profile anybody or follow them between versions.

What the request carries is the exhaustive list, and the reason each field is
there is that the server needs it to answer *which* update applies:

    app version · macOS version · architecture

That is all. No brain content, no connector names, no model provider, no
locale, no screen size, no identifier. A field that the server does not need in
order to choose an appcast entry does not belong in the request, and the test
suite pins that list.

**It is a notification, not an auto-updater.** Nothing here downloads or
installs anything: real Sparkle needs an EdDSA signing key and changes to
`scripts/build-dmg.sh`, and a half-built auto-updater that replaces a signed,
notarised app is worse than a link. This says "0.2.0 is out, here is where to
get it" and stops.

**Off by default in the sense that matters.** `UPDATE_FEED` ships empty, so a
build with no feed configured never makes a request at all and the UI says so
rather than offering a button that 404s. When a feed is configured the check is
on and the user can turn it off — and the README's *"no telemetry"* line has to
change in the same release, because a daily request to our server is a thing a
privacy-first product has to disclose rather than argue about.
"""
from __future__ import annotations

import json
import platform
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .config import get_settings
from .log import get_logger, suppressed

log = get_logger(__name__)

#: One a day. This number is the whole privacy argument: it is what makes
#: request volume an install count, so lowering it buys nothing and costs the
#: property that lets this run without an identifier.
CHECK_INTERVAL = 24 * 60 * 60

#: Where the last check is remembered, so a relaunch does not re-ask.
STATE_FILE = "updates.json"

#: How long to wait on the feed. Short: nobody is waiting on this, and a slow
#: server must not make the Settings screen feel broken.
TIMEOUT = 6.0

#: The exhaustive list of what a check sends. Pinned by
#: `tests/test_updates.py::test_a_check_sends_only_these_fields`.
SENT_FIELDS = ("app", "os", "arch")

_VERSION_PART = re.compile(r"^(\d+)")


def feed_url() -> str:
    """The appcast to ask, or "" when this build has none configured."""
    return (get_settings().update_feed or "").strip()


def is_configured() -> bool:
    return bool(feed_url())


def enabled() -> bool:
    """Whether the user has left the check on. Meaningless with no feed.

    The user's answer lives in `updates.json` beside the last-checked time,
    not in `Settings`: settings are read from the environment and a `.env`, so
    assigning one would last until the process restarted and no longer. The
    shipped default is `update_check`, which an operator can flip for a whole
    build; a `false` the user wrote always wins over it.
    """
    if not is_configured():
        return False
    saved = _read_state().get("enabled")
    if saved is None:
        return bool(get_settings().update_check)
    return bool(saved)


def current_version() -> str:
    from . import __version__
    return str(__version__)


# ── comparing versions ──────────────────────────────────────────────────────

def parse_version(raw: str) -> tuple[int, ...]:
    """`"0.10.2"` → `(0, 10, 2)`, tolerantly.

    String comparison is the obvious thing and it is wrong in the one case that
    matters: `"0.10.0" < "0.9.0"` is true for strings and false for versions,
    so the tenth release would stop offering updates.

    **Parsing stops at the first component that is not purely numeric**, so
    `1.2.0-beta.1` is `(1, 2, 0)`. Reading every component instead gave
    `(1, 2, 0, 1)`, which compares *above* `1.2.0` padded to `(1, 2, 0, 0)` —
    so a prerelease was offered as an upgrade over the release it precedes. A
    prerelease is treated as its release rather than guessing an ordering we do
    not publish.
    """
    out: list[int] = []
    for part in str(raw or "").strip().lstrip("vV").split("."):
        match = _VERSION_PART.match(part)
        out.append(int(match.group(1)) if match else 0)
        if not part.isdigit():          # `0-beta`, `3rc1`, `x` — nothing after
            break
    return tuple(out) or (0,)


def is_newer(candidate: str, than: str) -> bool:
    """Is `candidate` a version worth telling the user about?

    Padded to equal length so `1.2` and `1.2.0` compare equal rather than one
    being mysteriously older than the other.
    """
    a, b = parse_version(candidate), parse_version(than)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# ── remembering when we last asked ──────────────────────────────────────────

def _state_path():
    return get_settings().home / STATE_FILE


def _read_state() -> dict[str, Any]:
    with suppressed("reading the update state"):
        return json.loads(_state_path().read_text())
    return {}


def _write_state(data: dict[str, Any]) -> None:
    with suppressed("writing the update state"):
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))


def due(now: float | None = None) -> bool:
    """Is a check due? False when it is off, unconfigured, or asked today."""
    if not enabled():
        return False
    last = float(_read_state().get("checked_at") or 0)
    return (now or time.time()) - last >= CHECK_INTERVAL


# ── what we send ────────────────────────────────────────────────────────────

def request_payload() -> dict[str, str]:
    """Exactly what a check tells the server. See the module docstring.

    Built as a function rather than inlined so a test can read it and fail if a
    field ever appears that the server does not need in order to pick an
    appcast entry.
    """
    return {
        "app": current_version(),
        "os": platform.mac_ver()[0] or platform.release(),
        "arch": platform.machine(),
    }


# ── the answer ──────────────────────────────────────────────────────────────

@dataclass
class Release:
    """One entry from the appcast."""
    version: str
    url: str = ""
    notes: str = ""
    published: str = ""
    critical: bool = False


@dataclass
class Outcome:
    """What a check concluded, in the shape the UI needs.

    `reason` is for a person. A check that failed says why in a sentence; it
    never shows a spinner that stops, and it never nags — a machine with no
    network is not a machine with a problem to report.
    """
    current: str
    configured: bool
    checked: bool = False
    update: Release | None = None
    reason: str = ""
    checked_at: float = 0.0
    sent: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "configured": self.configured,
            "enabled": enabled(),
            "checked": self.checked,
            "reason": self.reason,
            "checked_at": self.checked_at,
            "sent": self.sent,
            "update": None if self.update is None else {
                "version": self.update.version,
                "url": self.update.url,
                "notes": self.update.notes,
                "published": self.update.published,
                "critical": self.update.critical,
            },
        }


def _pick(entries: list[dict[str, Any]], current: str) -> Release | None:
    """The newest entry that beats `current`, or None.

    Highest wins rather than first, because an appcast written by hand will one
    day be out of order and the user should still be offered the newest build
    rather than whichever line happens to come first.
    """
    best: Release | None = None
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        version = str(raw.get("version") or "").strip()
        if not version or not is_newer(version, current):
            continue
        found = Release(
            version=version,
            url=str(raw.get("url") or ""),
            notes=str(raw.get("notes") or ""),
            published=str(raw.get("published") or ""),
            critical=bool(raw.get("critical")),
        )
        if best is None or is_newer(found.version, best.version):
            best = found
    return best


def state() -> dict[str, Any]:
    """What is known without asking anybody. Safe to call on every screen open."""
    saved = _read_state()
    out = Outcome(
        current=current_version(),
        configured=is_configured(),
        checked=bool(saved.get("checked_at")),
        checked_at=float(saved.get("checked_at") or 0),
        reason=str(saved.get("reason") or ""),
    )
    latest = saved.get("latest")
    if isinstance(latest, dict) and latest.get("version") \
            and is_newer(str(latest["version"]), out.current):
        out.update = Release(
            version=str(latest["version"]), url=str(latest.get("url") or ""),
            notes=str(latest.get("notes") or ""),
            published=str(latest.get("published") or ""),
            critical=bool(latest.get("critical")))
    return out.as_dict()


def check(force: bool = False) -> dict[str, Any]:
    """Ask the feed whether there is a newer build.

    Returns the same shape as `state()`. Never raises: a version check is the
    least important thing this app does, and it must not be able to break a
    screen or a launch.
    """
    current = current_version()
    if not is_configured():
        return Outcome(current=current, configured=False,
                       reason="This build has no update feed.").as_dict()
    if not force and not due():
        return state()

    payload = request_payload()
    try:
        import httpx

        response = httpx.get(feed_url(), params=payload, timeout=TIMEOUT,
                             follow_redirects=True)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        # Recorded but not raised, and deliberately not stored as `latest` —
        # a failed check must not overwrite a real answer from yesterday.
        reason = "Could not reach the update server."
        log.info("update check failed: %s", exc)
        saved = _read_state()
        saved["reason"] = reason
        _write_state(saved)
        answer = state()
        answer["reason"] = reason
        return answer

    entries = body.get("releases") if isinstance(body, dict) else body
    found = _pick(entries if isinstance(entries, list) else [], current)

    now = time.time()
    saved = _read_state()
    saved["checked_at"] = now
    saved["reason"] = ""
    # Only a real release is remembered. `None` clears a stale one, so a build
    # that has caught up stops offering an update it already is.
    saved["latest"] = None if found is None else {
        "version": found.version, "url": found.url, "notes": found.notes,
        "published": found.published, "critical": found.critical,
    }
    _write_state(saved)

    return Outcome(current=current, configured=True, checked=True,
                   update=found, checked_at=now, sent=payload).as_dict()


def set_enabled(on: bool) -> dict[str, Any]:
    """Turn the daily check on or off. The user's call, and it sticks."""
    saved = _read_state()
    saved["enabled"] = bool(on)
    _write_state(saved)
    log.info("update check turned %s by the user", "on" if on else "off")
    return state()
