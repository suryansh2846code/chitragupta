"""The master key that makes every backup on this Mac one set.

Without this module each backup would mint its own master key, and therefore
its own recovery code — so a user who backed up weekly would be handed a new
32-character code to keep safe every Monday, and the one they wrote down in
January would open exactly one January file. Unusable, and the kind of thing
that reads as "the app is broken" rather than "the app is careful".

So the master key is generated once, on the first backup, and kept in the
**macOS Keychain** — encrypted at rest by the OS, and Tier 0 by definition: it
is the one secret that must never travel with the thing it protects. One
passphrase and one recovery code then open every backup this machine has ever
written.

**Restoring on a new Mac is where it comes back.** There is no Keychain entry
there, so the master key is unwrapped out of the archive with the user's
passphrase or recovery code and then stored — which is what makes the *next*
backup from the new machine part of the same set rather than starting over.

`config` is a leaf, so reading the Keychain from here does not point an arrow
the wrong way ([`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §3).
"""
from __future__ import annotations

import base64

from ..config import get_settings
from ..log import get_logger
from . import crypto

log = get_logger(__name__)

#: Keychain key. Prefixed like the rest of the app's secrets so it is visible
#: in Keychain Access as something Chitragupta owns.
MASTER_KEY_SECRET = "ARCHIVE_MASTER_KEY"

#: The master key wrapped under the user's recovery code.
#:
#: **Not a secret**, and keeping it is what makes the recovery code work on
#: more than one backup. The first version stored only the key, so every later
#: backup wrapped it under a *freshly generated* code that was never shown to
#: anybody — the header advertised a `recovery` unlock method that nothing on
#: earth could use, and the code the user wrote down opened only their first
#: file. Storing the wrap instead of the code fixes that and reveals nothing:
#: it is the master key encrypted under a key derived from the code, it already
#: travels in plaintext inside every archive header, and without the code it is
#: inert.
RECOVERY_WRAP_SECRET = "ARCHIVE_RECOVERY_WRAP"

#: Set once the user has been shown their recovery code and confirmed they
#: saved it. Not a secret — it is a fact about what the user has seen — but it
#: lives beside the key so the two can never disagree about whether setup
#: finished.
CODE_ACKNOWLEDGED_SECRET = "ARCHIVE_RECOVERY_ACKNOWLEDGED"


def master_key() -> bytes | None:
    """This machine's backup master key, or None if backup was never set up."""
    raw = get_settings().get_secret(MASTER_KEY_SECRET)
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:
        log.warning("the stored backup master key is unreadable — treating "
                    "backup as not yet set up")
        return None
    if len(key) != crypto.KEY_BYTES:
        log.warning("the stored backup master key is the wrong length — "
                    "treating backup as not yet set up")
        return None
    return key


def remember_master_key(key: bytes, recovery_wrap: str | None = None) -> None:
    """Store a master key, and the recovery wrap that belongs with it.

    The two are written together because they are only meaningful as a pair: a
    wrap made against a different master key cannot unwrap this one, so storing
    one without the other is how an archive ends up advertising an unlock
    method that does not work.
    """
    if len(key) != crypto.KEY_BYTES:
        raise ValueError("a master key must be exactly 32 bytes")
    settings = get_settings()
    settings.set_secret(MASTER_KEY_SECRET,
                        base64.b64encode(key).decode("ascii"))
    if recovery_wrap:
        settings.set_secret(RECOVERY_WRAP_SECRET, recovery_wrap)


def recovery_wrap() -> str | None:
    """The stored wrap of the master key under the user's recovery code."""
    return get_settings().get_secret(RECOVERY_WRAP_SECRET)


def is_set_up() -> bool:
    return master_key() is not None


def recovery_code_acknowledged() -> bool:
    """Has the user confirmed they saved their recovery code?

    Backup is deliberately not considered finished until they have. The code
    cannot be shown again — `crypto.new_recovery_code` generates it and nothing
    stores it — so a user who closed the dialog without reading it has one door
    to their backups instead of two, and does not know it.
    """
    return bool(get_settings().get_secret(CODE_ACKNOWLEDGED_SECRET))


def acknowledge_recovery_code() -> None:
    get_settings().set_secret(CODE_ACKNOWLEDGED_SECRET, "1")


def begin_setup() -> tuple[bytes, str, str]:
    """A new master key, the one recovery code for it, and that code's wrap.

    Returns `(key, code, wrap)` and stores the **key and the wrap** — never the
    code. The code is the caller's to show once and then forget: holding it
    would mean a secret that defeats the user's own passphrase sitting in our
    storage, which is the opposite of what it is for. The wrap is kept because
    it is not a secret and every future backup needs it; see
    `RECOVERY_WRAP_SECRET`.
    """
    key = crypto.new_key()
    code = crypto.new_recovery_code()
    wrap = crypto.wrap(crypto.derive_from_recovery_code(code), key,
                       crypto.WRAP_RECOVERY)
    remember_master_key(key, wrap)
    get_settings().set_secret(CODE_ACKNOWLEDGED_SECRET, None)
    log.info("backup set up: a new master key is stored in the Keychain")
    return key, code, wrap


def forget() -> None:
    """Remove the master key from this machine.

    Does **not** touch any backup file. An existing backup stays openable with
    the passphrase or recovery code it was written with — which is the point:
    this clears the convenience, never the recovery.
    """
    settings = get_settings()
    settings.set_secret(MASTER_KEY_SECRET, None)
    settings.set_secret(RECOVERY_WRAP_SECRET, None)
    settings.set_secret(CODE_ACKNOWLEDGED_SECRET, None)
    log.info("backup master key removed from this machine")
