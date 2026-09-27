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
from . import engine, telegram_auth
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, SyncStrategy
from .engine import Record
from .pagination import Page
from .provenance import SourceRef
from .resources import fingerprint

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
    #: **False, and now honestly so.** `sync()` accepted `since` and never
    #: used it, so this said True while every pass re-read every message in
    #: every conversation. Telethon does filter by date, but by walking
    #: backwards from an offset — a different traversal, not a parameter to
    #: add. Identity dedup covers the repeat pass instead.
    incremental = False
    #: Not an API key: MTProto establishes a session with the user's own
    #: account, and the thing stored is that session rather than a token the
    #: user could have pasted. `AuthMethod` keeps them apart because the
    #: disconnect and re-auth paths are genuinely different.
    auth_method = AuthMethod.SESSION
    sync_strategy = SyncStrategy.FULL
    #: Per-conversation checkpoints: a pass stopped at the twentieth chat
    #: resumes there rather than at the first.
    resumable = True
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
    #
    # The same two-level shape as Slack: conversations, then messages inside
    # each. A *page* is one conversation and a *record* is one message, so a
    # checkpoint lands per conversation and a pass interrupted at the twentieth
    # chat resumes there.
    #
    # No `hydrate`: `history` already answers with whole messages. The
    # fingerprint is a digest of the text, so a message edited after we read it
    # is read again — free here, because the text arrives with the listing.

    def _record(self, chat: Chat, message: Message) -> Record:
        return Record(
            # Scoped to the conversation: a Telegram message id is unique within
            # a chat, not across them.
            external_id=f"{chat.id}:{message.id}",
            text=(f"Telegram {chat.kind} {chat.name}\n"
                  f"{message.sender} on {message.at}:\n{message.text}"),
            title=f"{chat.name} — {message.sender}",
            source_updated_at=message.at,
            fingerprint=fingerprint(message.text),
            extra={"chat": chat.name, "chat_id": chat.id, "chat_kind": chat.kind,
                   "sender": message.sender, "outgoing": message.outgoing},
            raw=message)

    def _page(self, chats: list[Chat], cursor: str) -> Page:
        """One conversation's worth of messages.

        The conversation list is resolved once for the pass and walked by index.
        Re-listing per page would mean another round trip through MTProto for
        every chat, and the session is one connection shared with everything
        else this connector does.
        """
        index = int(cursor or 0)
        if index >= len(chats):
            return Page(records=[], next_cursor="")
        chat = chats[index]
        records: list[Record] = []
        try:
            for message in self.history(chat.id, limit=SYNC_PER_CHAT):
                if message.text.strip():
                    records.append(self._record(chat, message))
        except Exception as exc:
            # One unreadable conversation is not the pass failing. Logged rather
            # than silently dropped, so it is findable afterwards.
            log.debug("telegram: could not read %s: %s", chat.name, exc)
        nxt = str(index + 1) if index + 1 < len(chats) else ""
        return Page(records=records, next_cursor=nxt)

    def _ingest(self, record: Record, source: SourceRef) -> str:
        from ..brain import get_brain

        out = get_brain().ingest(record.text, kind="message", fast=True,
                                 title=record.title, **source.ingest_kwargs())
        ids = out.get("memory_ids") or []
        return str(ids[0]) if ids else ""

    def sync(self, *, since: str | None = None, limit: int | None = None,
             full_history: bool = False, cancel=None, progress=None,
             **_: Any) -> SyncResult:
        """One pass: every conversation, recent messages in each.

        **No watermark, and that is now what the connector says.** `since` was
        accepted and ignored while `incremental` claimed True — so the manifest
        promised a filtered second pass that never happened. Telethon does filter
        by date, but through `offset_date` walking *backwards*, which is a
        different traversal rather than a parameter to add here. Identity dedup
        carries the repeat pass instead: an unchanged message costs one index
        lookup, which is the thing the watermark was wanted for.
        """
        result = SyncResult(connector=self.name)
        ready, reason = self.is_configured()
        if not ready:
            result.errors.append(reason)
            return self._finish(result)

        started = self.now()
        try:
            chats = self.chats(limit=limit or CHAT_LIMIT)
        except Exception as exc:
            from .errors import classify_exception
            problem = classify_exception(self.name, exc, label=self.label)
            result.errors.append(problem.message)
            result.detail = "sync failed"
            return self._finish(result)

        outcome = engine.run(
            engine.Plan(
                connector=self.name, manifest=self.manifest(),
                resource_type="message",
                fetch=lambda cursor: self._page(chats, cursor),
                ingest=self._ingest,
                connection_id=self.connection().id,
                # **Never sweeps.** A pass reads the recent end of each
                # conversation, which is a window — sweeping it would tombstone
                # every message older than `SYNC_PER_CHAT`.
                sweeps_deletions=False),
            cancel=cancel, progress=progress, full_history=full_history)

        outcome.detail = f"{len(chats)} conversations — {outcome.detail}"
        outcome.cursor = started
        return self._finish(outcome)


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
