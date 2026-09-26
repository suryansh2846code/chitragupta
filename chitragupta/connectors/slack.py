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
from .base import Connector, SyncResult
from .capability import caps
from .contract import AuthMethod, Limits, SyncStrategy

log = get_logger(__name__)

API = "https://slack.com/api"

#: Slack caps a page at 1000 and gets slow well before that. These are the
#: numbers a person actually wants: enough conversation to judge by, not the
#: whole archive.
CHAT_PAGE = 200
HISTORY_PAGE = 200
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
    incremental = True
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
        """
        import httpx

        token = self._token()
        if not token:
            return {"ok": False, "error": "Slack is not connected."}
        try:
            response = httpx.post(
                f"{API}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                data={k: v for k, v in params.items() if v is not None},
                timeout=30)
            body = response.json()
        except Exception as exc:
            return {"ok": False, "error": f"Slack could not be reached: {exc}"}

        if body.get("ok"):
            return body
        return {"ok": False, "error": self._explain(str(body.get("error") or "")) }

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

    def history(self, chat_id: str, limit: int = 50) -> list[Message]:
        body = self._call("conversations.history", channel=chat_id,
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
            rooms = self.chats(limit=limit or 30)
        except Exception as exc:
            result.errors.append(str(exc)[:200])
            result.detail = "sync failed"
            return self._finish(result)

        from ..brain import get_brain
        brain = get_brain()

        def ingest(room: Chat) -> int:
            stored = 0
            with suppressed("reading one Slack conversation"):
                for message in self.history(room.id, limit=SYNC_PER_CHAT):
                    if not message.text.strip():
                        continue
                    out = brain.ingest(
                        f"Slack {room.kind} {room.name}\n"
                        f"{message.sender} on {message.at}:\n{message.text}",
                        source=self.name, kind="message", fast=True,
                        title=f"{room.name} — {message.sender}")
                    stored += out["memories"]
            return stored

        self.each_guarded(rooms, result, ingest, cancel=cancel, progress=progress)
        result.detail = result.detail or f"{len(rooms)} conversations"
        result.cursor = started
        return self._finish(result)


def _when(ts: Any) -> str:
    """Slack's `"1700000000.000300"` as ISO 8601."""
    with suppressed("reading a Slack timestamp"):
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    return ""
