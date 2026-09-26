"""Telegram — the user's real conversations, over Telegram's own client API.

Not the Bot API. A bot only sees chats it was added to, and the user is asking
about the conversations *they* are in. This is MTProto, the same protocol every
third-party Telegram client uses, with the user's own `api_id` from
my.telegram.org. Sanctioned, documented, and the reason Telegram is first in
`docs/MESSAGING.md`.

The session, the loop and the three-step sign-in live in `telegram_auth.py`.
This file is only the two things Chitragupta wants: conversations read into the
brain, and the live read/send an agent uses.
"""
from __future__ import annotations

from typing import Any

from ..log import get_logger, suppressed
from ..messaging import Chat, Message
from . import telegram_auth
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, SyncStrategy

log = get_logger(__name__)

#: Deliberately modest. A Telegram account can have thousands of dialogs and
#: tens of thousands of messages in one of them; an agent handed all of it
#: summarises the first page and calls that the answer.
CHAT_LIMIT = 50
HISTORY_LIMIT = 50
SYNC_PER_CHAT = 30


class TelegramConnector(Connector):
    name = "telegram"
    label = "Telegram"
    auto_sync = True
    incremental = True
    #: Not an API key: MTProto establishes a session with the user's own
    #: account, and the thing stored is that session rather than a token the
    #: user could have pasted. `AuthMethod` keeps them apart because the
    #: disconnect and re-auth paths are genuinely different.
    auth_method = AuthMethod.SESSION
    sync_strategy = SyncStrategy.TIMESTAMP
    capabilities = caps("read:message", "read:chat", "send:message")
    limits = Limits(requests=30, per_seconds=60.0, concurrency=1,
                    page_size=100, records_per_sync=400)
    #: No `secret_field`: this needs an API id, an API hash, a phone number and
    #: a code, which is a flow rather than a box. The endpoints that drive it
    #: are in `api/routes/connectors.py`.

    def is_configured(self) -> tuple[bool, str]:
        state = telegram_auth.status()
        if not state["configured"]:
            return False, telegram_auth.NEEDS_CREDENTIALS
        if not state["authorized"]:
            return False, telegram_auth.NEEDS_SIGN_IN
        return True, ""

    # ── the messaging contract ───────────────────────────────────────────
    def chats(self, limit: int = 30) -> list[Chat]:
        conn = telegram_auth.client()
        if conn is None:
            raise RuntimeError(self.is_configured()[1])

        async def _read():
            out: list[Chat] = []
            async for dialog in conn.iter_dialogs(limit=min(CHAT_LIMIT, max(1, limit))):
                out.append(Chat(
                    id=str(dialog.id),
                    name=str(dialog.name or "Telegram chat"),
                    kind=("channel" if dialog.is_channel
                          else "group" if dialog.is_group else "dm"),
                    unread=int(dialog.unread_count or 0)))
            return out

        return telegram_auth.run(_read())

    def history(self, chat_id: str, limit: int = 50) -> list[Message]:
        conn = telegram_auth.client()
        if conn is None:
            raise RuntimeError(self.is_configured()[1])

        async def _read():
            entity = await conn.get_entity(_as_peer(chat_id))
            out: list[Message] = []
            async for msg in conn.iter_messages(
                    entity, limit=min(HISTORY_LIMIT, max(1, limit))):
                sender = await _name_of(msg)
                out.append(Message(
                    id=str(msg.id),
                    sender=sender,
                    at=msg.date.isoformat() if msg.date else "",
                    text=str(msg.message or _placeholder(msg)),
                    outgoing=bool(msg.out)))
            # Telethon yields newest first. Everything above promises the
            # opposite, because a conversation read backwards is answered
            # backwards.
            return list(reversed(out))

        return telegram_auth.run(_read())

    def send(self, chat_id: str, text: str) -> dict:
        """Send as the user (WRITE). Only ever after a confirmation."""
        if not str(text or "").strip():
            return {"ok": False, "error": "There is nothing to send."}
        conn = telegram_auth.client()
        if conn is None:
            return {"ok": False, "error": self.is_configured()[1]}

        async def _send():
            entity = await conn.get_entity(_as_peer(chat_id))
            sent = await conn.send_message(entity, text)
            return {"ok": True, "id": str(sent.id),
                    "detail": "Message sent on Telegram"}

        try:
            return telegram_auth.run(_send())
        except Exception as exc:
            return {"ok": False, "error": f"Telegram refused: {exc}"[:200]}

    # ── ingest ───────────────────────────────────────────────────────────
    def sync(self, *, since: str | None = None, limit: int | None = None,
             full_history: bool = False, cancel=None, progress=None,
             **_: Any) -> SyncResult:
        result = SyncResult(connector=self.name)
        ready, reason = self.is_configured()
        if not ready:
            result.errors.append(reason)
            return self._finish(result)

        started = self.now()
        try:
            dialogs = self.chats(limit=limit or 30)
        except Exception as exc:
            result.errors.append(str(exc)[:200])
            result.detail = "sync failed"
            return self._finish(result)

        from ..brain import get_brain
        brain = get_brain()

        def ingest(chat: Chat) -> int:
            stored = 0
            with suppressed("reading one Telegram conversation"):
                for message in self.history(chat.id, limit=SYNC_PER_CHAT):
                    if not message.text.strip():
                        continue
                    out = brain.ingest(
                        f"Telegram {chat.kind} {chat.name}\n"
                        f"{message.sender} on {message.at}:\n{message.text}",
                        source=self.name, kind="message", fast=True,
                        title=f"{chat.name} — {message.sender}")
                    stored += out["memories"]
            return stored

        self.each_guarded(dialogs, result, ingest, cancel=cancel, progress=progress)
        result.detail = result.detail or f"{len(dialogs)} conversations"
        result.cursor = started
        return self._finish(result)


def _as_peer(chat_id: str) -> Any:
    """Telegram ids are integers, and arrive here as strings.

    A username (`@dana`) is passed through untouched — it is the one form a
    person can actually type, and refusing it would mean the only way to name a
    chat is an id nobody can read.
    """
    raw = str(chat_id or "").strip()
    if raw.startswith("@"):
        return raw
    with suppressed("reading a Telegram chat id"):
        return int(raw)
    return raw


async def _name_of(message: Any) -> str:
    """Who sent it, as a person would say it."""
    with suppressed("naming a Telegram sender"):
        sender = await message.get_sender()
        if sender is None:
            return "someone"
        name = " ".join(filter(None, [getattr(sender, "first_name", ""),
                                      getattr(sender, "last_name", "")])).strip()
        return (name or getattr(sender, "username", "")
                or getattr(sender, "title", "") or "someone")
    return "someone"


def _placeholder(message: Any) -> str:
    """What to say about a message that is not text.

    An empty string would read as an empty message, and "they sent nothing" is
    a different and wrong statement from "they sent a photo".
    """
    if getattr(message, "photo", None):
        return "(photo)"
    if getattr(message, "voice", None):
        return "(voice note)"
    if getattr(message, "video", None):
        return "(video)"
    if getattr(message, "document", None):
        return "(file)"
    if getattr(message, "sticker", None):
        return "(sticker)"
    return ""
