"""Backup and restore for the whole Chitragupta home.

A sibling of `brain/`, `models/` and `connectors/`: it may read `core/` and the
leaf utilities, and **nothing above it**. In particular it does not import
`brain/` — re-running migrations and re-embedding after a restore belongs to
the caller, not here. See [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)
§3.

What may and may not travel is decided by `core/exclusions.py`, which is a rule
about the boundary of the user's own data and therefore lives in `core/` beside
`redact.py`, not in here.

**The modules are `writer` and `reader`, not `pack` and `restore`**, because
the package exports functions by those names: `from .pack import pack` rebinds
`archive.pack` from the module to the function, so whichever a caller meant
they got the other one. Naming the modules for what they are leaves the verbs
free for the API.

Design and rationale: [`docs/ACCOUNTS-DESIGN.md`](../../docs/ACCOUNTS-DESIGN.md).
"""
from __future__ import annotations

from .crypto import (
    ArchiveError,
    CorruptArchiveError,
    Header,
    WrongSecretError,
    new_recovery_code,
    normalise_recovery_code,
)
from .identity import Keyring
from .reader import Inspection, inspect, restore
from .writer import Packed, pack, stage

__all__ = [
    "ArchiveError",
    "CorruptArchiveError",
    "Header",
    "Inspection",
    "Keyring",
    "Packed",
    "WrongSecretError",
    "inspect",
    "new_recovery_code",
    "normalise_recovery_code",
    "pack",
    "restore",
    "stage",
]
