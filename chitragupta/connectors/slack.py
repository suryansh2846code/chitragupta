"""Slack — the user's own DMs and channels, over the official Web API.

One pasted **user** token (`xoxp-…`), not a bot token. The difference is the
whole point: a bot sees the channels it was invited to, and the user is asking
about the conversations *they* are in. A user token acts as them, reads what
they can read, and posts as them.

Everything here is documented, supported and rate-limited by Slack. There is no
protocol reverse-engineering and no ban risk, which is exactly why Slack is
second on the list in `docs/MESSAGING.md` and WhatsApp is not on it at all.

Built on `httpx`, already a base dependency — a messaging app that needs no new
package is a messaging app that cannot break the build.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..config import get_settings
from ..log import get_logger, suppressed
from ..messaging import Chat, Message
from . import engine
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, SyncStrategy
from .engine import Record
from .errors import ConnectorError, classify_http
from .limits import gate_for
from .pagination import Page
from .provenance import SourceRef
from .resources import fingerprint
from .retry import RetryPolicy, with_retries

log = get_logger(__name__)

API = "https://slack.com/api"

#: Slack caps a page at 1000 and gets slow well before that. These are the
#: numbers a person actually wants: enough conversation to judge by, not the
#: whole archive.
CHAT_PAGE = 200
HISTORY_PAGE = 200
#: Conversations one pass walks. Slack's limit is per method per second, so
#: this is also how many paced requests a pass costs.
CHAT_LIMIT = 30
#: Slack says `ratelimited` with a 200 and a `Retry-After`. Three attempts
#: is enough to ride out a burst without the pass sitting still for a
#: minute, which is time the Stop button would spend being ignored.
RETRY = RetryPolicy(attempts=3, base_seconds=1.0, max_seconds=8.0,
                    total_seconds=30.0)
SYNC_PER_CHAT = 40

#: Slack's own words for a conversation, in the user's.
_KINDS = {"im": "dm", "mpim": "group", "private_channel": "channel",
          "public_channel": "channel"}

#: What Slack says when the token is missing a scope. Worth translating,
#: because "missing_scope" on screen tells a person nothing they can act on.
_SCOPE_ERRORS = {"missing_scope", "not_allowed_token_type", "no_permission"}


class SlackConnector(Connector):
    name = "slack"
    label = "Slack"
    auto_sync = True
    #: True, and now actually true: `sync()` passes the watermark to Slack as
    #: `conversations.history(oldest=…)`. It accepted `since` and ignored it
    #: before, so this claimed a filtered second pass that never happened.
    incremental = True
    #: Per-conversation checkpoints: a pass stopped at the twentieth channel
    #: resumes there rather than at the first.
    resumable = True
    auth_method = AuthMethod.API_KEY
    sync_strategy = SyncStrategy.TIMESTAMP
    #: Slack's own token scopes, which the pasted token either carries or does
    #: not. A health check reads them back rather than discovering a missing
    #: one when a read fails.
    required_scopes = ("channels:history", "channels:read", "users:read",
                       "chat:write")
    #: `send:message` is the only write, and it is outbound: a Slack message
    #: reaches a person. It is judged against `CHAT_RECIPIENT`, never the email
    #: list — a chat id means nothing outside the app it came from.
    capabilities = caps("read:message", "read:chat", "read:contact",
                        "send:message")
    limits = Limits(requests=50, per_seconds=60.0, concurrency=2,
                    page_size=200, records_per_sync=400)
    secret_field = {
        "key": "SLACK_USER_TOKEN",
        "label": "Slack user token",
        "placeholder": "xoxp-…",
        "help_url": "https://api.slack.com/apps",
        "steps": [
            "Open <b>api.slack.com/apps</b> → <b>Create New App</b> → "
            "<b>From scratch</b>, and pick your workspace.",
            "Under <b>OAuth &amp; Permissions</b> → <b>User Token Scopes</b>, add "
            "<b>channels:read</b>, <b>channels:history</b>, <b>groups:read</b>, "
            "<b>groups:history</b>, <b>im:read</b>, <b>im:history</b>, "
            "<b>users:read</b> and <b>chat:write</b>.",
            "Click <b>Install to Workspace</b>, then copy the "
            "<b>User</b> OAuth Token — it starts with <code>xoxp-</code>.",
            "Paste it below and click Save.",
        ],
    }

    # ── credentials ──────────────────────────────────────────────────────
    def is_configured(self) -> tuple[bool, str]:
        if get_settings().get_secret("SLACK_USER_TOKEN"):
            return True, ""
        return False, "click setup to paste your Slack user token"

    def _token(self) -> str:
        return str(get_settings().get_secret("SLACK_USER_TOKEN") or "")

    def _call(self, method: str, **params: Any) -> dict:
        """One Web API call, with Slack's `ok: false` turned into our shape.

        Slack answers 200 with `{"ok": false, "error": "…"}` rather than an HTTP
        error, so a caller that only checks the status code believes every call
        worked. That is worth exactly one wrapper.

        **Paced and retried**, which it was not. Slack allows roughly one request
        a second per method; a sync makes one listing call plus one per
        conversation, in a burst, and had neither a gap between them nor a second
        attempt. A `ratelimited` answer became *"Slack is rate-limiting us"*,
        which `history` then swallowed — so the pass reported success with
        conversations quietly missing. The gate spaces the burst and the retry
        waits out the 429 instead of losing the conversation.
        """
        import httpx

        token = self._token()
        if not token:
            return {"ok": False, "error": "Slack is not connected."}

        def once() -> dict:
            with gate_for(self.name, self.limits)():
                response = httpx.post(
                    f"{API}/{method}",
                    headers={"Authorization": f"Bearer {token}"},
                    data={k: v for k, v in params.items() if v is not None},
                    timeout=self.limits.seconds_per_request)
            body = response.json()
            if body.get("ok"):
                return dict(body)
            code = str(body.get("error") or "")
            if code == "ratelimited":
                # Raised rather than returned, so `with_retries` can see it —
                # Slack says this with a 200, so nothing in the HTTP layer knows.
                # `Retry-After` is on the response and is honoured when present.
                raise classify_http(
                    self.name, 429, code, label=self.label,
                    headers=getattr(response, "headers", None))
            return {"ok": False, "error": self._explain(code)}

        try:
            return with_retries(once, connector=self.name, label=self.label,
                                policy=RETRY)
        except ConnectorError as exc:
            return {"ok": False, "error": exc.message}
        except Exception as exc:
            return {"ok": False, "error": f"Slack could not be reached: {exc}"}

    @staticmethod
    def _explain(code: str) -> str:
        """Slack's error code, as something a person can act on."""
        if code in _SCOPE_ERRORS:
            return ("This Slack token is missing a permission. Re-install the "
                    "app with the scopes listed under Setup, then paste the new "
                    "token.")
        if code in ("invalid_auth", "token_revoked", "account_inactive"):
            return "This Slack token is no longer valid — paste a new one."
        if code == "ratelimited":
            return "Slack is rate-limiting us. Try again in a minute."
        if code == "channel_not_found":
            return "That conversation does not exist, or this token cannot see it."
        return f"Slack refused: {code or 'no reason given'}."

    # ── who people are ───────────────────────────────────────────────────
    def _people(self) -> dict[str, str]:
        """Slack user id → display name, fetched once per connector instance.

        Every message carries `U024BE7LH` and nothing else. Without this an
        agent reads a conversation between two opaque ids and cannot tell the
        user who said what — which is most of what reading a conversation is
        for.
        """
        cached = getattr(self, "_people_cache", None)
        if cached is not None:
            return cached
        names: dict[str, str] = {}
        body = self._call("users.list", limit=CHAT_PAGE)
        for person in body.get("members", []) or []:
            profile = person.get("profile") or {}
            names[str(person.get("id"))] = str(
                profile.get("display_name") or profile.get("real_name")
                or person.get("name") or person.get("id"))
        self._people_cache = names
        return names

    def _me(self) -> str:
        cached = getattr(self, "_me_cache", None)
        if cached is not None:
            return cached
        self._me_cache = str(self._call("auth.test").get("user_id") or "")
        return self._me_cache

    # ── the messaging contract ───────────────────────────────────────────
    def chats(self, limit: int = 30) -> list[Chat]:
        body = self._call(
            "users.conversations", limit=min(CHAT_PAGE, max(1, limit)),
            types="public_channel,private_channel,mpim,im",
            exclude_archived="true")
        if not body.get("ok"):
            raise RuntimeError(body.get("error") or "Slack could not be read.")

        people = self._people()
        out: list[Chat] = []
        for room in body.get("channels", []) or []:
            if room.get("is_im"):
                name = people.get(str(room.get("user")), "Direct message")
                kind = "dm"
            else:
                name = f"#{room.get('name') or 'channel'}"
                kind = _KINDS.get(
                    "private_channel" if room.get("is_private") else "public_channel",
                    "channel")
                if room.get("is_mpim"):
                    kind = "group"
            out.append(Chat(id=str(room.get("id")), name=name, kind=kind))
        return out[:limit]

    def history(self, chat_id: str, limit: int = 50,
                oldest: str = "") -> list[Message]:
        """Recent messages, oldest first.

        `oldest` is a Slack timestamp and is **only** passed by the sync: an
        agent reading a conversation wants the recent end of it, not whatever has
        arrived since a watermark. Filtering at Slack rather than here is what
        makes the second pass of a quiet workspace cost almost nothing.
        """
        body = self._call("conversations.history", channel=chat_id,
                          oldest=oldest or None,
                          limit=min(HISTORY_PAGE, max(1, limit)))
        if not body.get("ok"):
            raise RuntimeError(body.get("error") or "That conversation could not be read.")

        people, me = self._people(), self._me()
        out: list[Message] = []
        for raw in body.get("messages", []) or []:
            who = str(raw.get("user") or raw.get("bot_id") or "")
            out.append(Message(
                id=str(raw.get("ts") or ""),
                sender=people.get(who, who or "someone"),
                at=_when(raw.get("ts")),
                text=str(raw.get("text") or ""),
                outgoing=bool(me and who == me)))
        # Slack answers newest first, because that is what a chat window wants.
        # Everything above this layer promises oldest first.
        return list(reversed(out))

    def send(self, chat_id: str, text: str) -> dict:
        """Post as the user (WRITE). Only ever after a confirmation."""
        if not str(text or "").strip():
            return {"ok": False, "error": "There is nothing to send."}
        body = self._call("chat.postMessage", channel=chat_id, text=text)
        if not body.get("ok"):
            return {"ok": False, "error": body.get("error")}
        return {"ok": True, "id": str(body.get("ts") or ""),
                "detail": "Message sent on Slack"}

    # ── ingest ───────────────────────────────────────────────────────────
    #
    # Slack is the **fourth** shape on the shared engine, and the first that is
    # two levels deep: conversations, then messages inside each. So a *page* is
    # one conversation and a *record* is one message — which means a checkpoint
    # lands per conversation, and a pass interrupted at the twentieth channel
    # resumes at the twentieth rather than at the first.
    #
    # Unlike mail and documents it needs no `hydrate`: `history` already answers
    # with whole messages. What it does need is pacing, and that is the real fix
    # here — see `_call`.
    #
    # The fingerprint is a digest of the message text, not its id. A message
    # *can* be edited, and unlike a mailbox the text arrives with the listing —
    # so catching an edit costs nothing, where in Gmail it would have cost a
    # request per message.

    def _record(self, room: Chat, message: Message) -> Record:
        return Record(
            # Scoped to the conversation: a Slack `ts` is unique within a
            # channel and not across them.
            external_id=f"{room.id}:{message.id}",
            text=(f"Slack {room.kind} {room.name}\n"
                  f"{message.sender} on {message.at}:\n{message.text}"),
            title=f"{room.name} — {message.sender}",
            source_updated_at=message.at,
            # An edit changes the text and nothing else we can see, and the text
            # is already in hand. Free here; a request per message in Gmail.
            fingerprint=fingerprint(message.text),
            extra={"chat": room.name, "chat_id": room.id, "chat_kind": room.kind,
                   "sender": message.sender, "outgoing": message.outgoing},
            raw=message)

    def _page(self, rooms: list[Chat], oldest: str, cursor: str) -> Page:
        """One conversation's worth of messages.

        The conversation list is resolved once for the pass and walked by index,
        rather than re-listed per page — which would be one extra request per
        conversation against the tightest rate limit in the app.
        """
        index = int(cursor or 0)
        if index >= len(rooms):
            return Page(records=[], next_cursor="")
        room = rooms[index]
        records: list[Record] = []
        try:
            for message in self.history(room.id, limit=SYNC_PER_CHAT,
                                        oldest=oldest):
                if message.text.strip():
                    records.append(self._record(room, message))
        except Exception as exc:
            # One unreadable conversation — a private channel the token lost
            # access to — is not the pass failing. Logged rather than suppressed
            # silently, so it is findable.
            log.debug("slack: could not read %s: %s", room.name, exc)
        nxt = str(index + 1) if index + 1 < len(rooms) else ""
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

        `since` is honoured now. It was accepted and ignored — so `incremental`
        said True while every pass re-read every message in every conversation
        and leaned on the content hash to throw them away.
        """
        result = SyncResult(connector=self.name)
        ready, reason = self.is_configured()
        if not ready:
            result.errors.append(reason)
            return self._finish(result)

        started = self.now()
        resume = since if since is not None else self.since(
            full_history=full_history)
        try:
            rooms = self.chats(limit=limit or CHAT_LIMIT)
        except Exception as exc:
            from .errors import classify_exception
            problem = classify_exception(self.name, exc, label=self.label)
            result.errors.append(problem.message)
            result.detail = "sync failed"
            return self._finish(result)

        oldest = _as_slack_ts(resume) if resume else ""
        outcome = engine.run(
            engine.Plan(
                connector=self.name, manifest=self.manifest(),
                resource_type="message",
                fetch=lambda cursor: self._page(rooms, oldest, cursor),
                ingest=self._ingest,
                connection_id=self.connection().id,
                # **Never sweeps.** A pass reads the recent end of each
                # conversation, which is a window and not an enumeration —
                # sweeping it would tombstone every message older than
                # `SYNC_PER_CHAT`, which is nearly all of them.
                sweeps_deletions=False),
            cancel=cancel, progress=progress, full_history=full_history)

        outcome.detail = f"{len(rooms)} conversations — {outcome.detail}"
        outcome.cursor = started
        return self._finish(outcome)


def _as_slack_ts(stamp: str) -> str:
    """An ISO watermark as the `"1700000000.000000"` Slack filters on.

    Empty when it cannot be read, which a caller must treat as "no filter" —
    never as "nothing newer". Returning a bad timestamp would ask Slack for
    messages after the epoch, or after nothing at all.
    """
    from datetime import datetime as _dt

    with suppressed("reading a watermark for Slack"):
        parsed = _dt.fromisoformat(stamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return f"{parsed.timestamp():.6f}"
    return ""


def _when(ts: Any) -> str:
    """Slack's `"1700000000.000300"` as ISO 8601."""
    with suppressed("reading a Slack timestamp"):
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    return ""
