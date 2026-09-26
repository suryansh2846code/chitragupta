"""The HTTP surface, grouped by what it is for.

These were one 1500-line module holding 105 routes, which is past the size where
a change can be made without reading the whole file. The split is by subject
rather than by verb, so the routes that move together live together.

**Registration order is load-bearing.** FastAPI matches in the order routes are
added, and a literal path registered after a parameterised one that also matches
is unreachable — `/api/brain/canonical/review` behind `/api/brain/canonical/{section}`
would quietly become a request for a section called "review". Conflicting pairs
are kept inside a single module, where their relative order is visible, rather
than spread across modules where it depends on the include order below.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    agents,
    brain,
    browser,
    connector_health,
    connectors,
    diagnostics,
    providers,
    sync,
    workspace,
)

#: Every router, in the order they are mounted.
ALL_ROUTERS: tuple[APIRouter, ...] = (
    sync.router,
    agents.router,
    brain.router,
    providers.router,
    # **Before `connectors`**, and the order is load-bearing per the note
    # above: this module's paths are literal (`/health`, `/manifest`,
    # `/diagnostics`) where `connectors` owns the parameterised
    # `/{name}/…` family. Mounted the other way, a future `GET
    # /api/connectors/{name}` would swallow `GET /api/connectors/health` and
    # the screen would ask for a connector called "health".
    connector_health.router,
    connectors.router,
    workspace.router,
    diagnostics.router,
    browser.router,
)

__all__ = ["ALL_ROUTERS"]
