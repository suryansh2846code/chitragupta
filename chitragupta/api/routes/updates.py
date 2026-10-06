"""Is there a newer version, and may we ask?

Three literal paths, so nothing here can shadow or be shadowed by another
router's parameterised routes — see the note in `routes/__init__.py`.

`/check` reaches the network, so it runs in the **probe** lane: it is the same
cost class as asking a provider what models an account can run, and
`api/concurrency.py` exists because a handler that blocks for seconds without a
lane takes the window with it. `/state` and `/settings` touch one small file
and stay ordinary handlers.

**Nothing here decides what the request contains.** `updates.request_payload`
owns that, and `tests/test_updates.py` pins it — a route that assembled its own
payload would be the second place the answer lived, and the one that drifts is
the one nobody reads.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ... import updates
from ..concurrency import probes_a_provider

router = APIRouter()


class UpdateSettingIn(BaseModel):
    enabled: bool


@router.get("/api/updates/state")
def updates_state() -> dict[str, Any]:
    """What is already known, without asking anybody.

    Cheap on purpose: the Settings screen calls it on every open, and a screen
    that made a network request to render would be the 2.47s stall
    `api/concurrency.py` was written about.
    """
    answer = updates.state()
    answer["interval_hours"] = updates.CHECK_INTERVAL // 3600
    answer["due"] = updates.due()
    answer["sends"] = list(updates.SENT_FIELDS)
    answer["feed"] = updates.feed_url()
    return answer


@router.post("/api/updates/check")
@probes_a_provider
def updates_check(force: bool = True) -> dict[str, Any]:
    """Ask the feed now.

    `force` defaults to true because the only caller is a user pressing Check —
    the daily rate limit exists to keep the *automatic* check honest about
    being once a day, not to refuse somebody who asked. Never raises: a version
    check is the least important thing this app does and must not be able to
    break a screen.
    """
    answer = updates.check(force=force)
    answer["sends"] = list(updates.SENT_FIELDS)
    return answer


@router.post("/api/updates/settings")
def updates_settings(body: UpdateSettingIn) -> dict[str, Any]:
    """Turn the daily check off, or back on. The user's decision, and it sticks."""
    answer = updates.set_enabled(body.enabled)
    answer["sends"] = list(updates.SENT_FIELDS)
    return answer
