"""Moved to `core/redact.py`. Re-exported here so existing imports work.

A rule about what may be written to the database belongs in the layer that owns
the database. Keeping the implementation here meant `core/store.py` imported
`brain` to enforce it — see `docs/ARCHITECTURE.md` §6.2, now closed.
"""
from __future__ import annotations

from ...core.redact import is_sensitive, redact

__all__ = ["is_sensitive", "redact"]
