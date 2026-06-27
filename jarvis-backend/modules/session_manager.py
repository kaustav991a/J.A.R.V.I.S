"""
session_manager.py — Concurrent Session Scoping (Hardening)
===========================================================

J.A.R.V.I.S. now has more than one mouth and more than one ear. The React HUD at
the desk talks to him over a WebSocket; the Telegram gateway talks to him from a
phone. Historically the backend leaned on module-global singletons — `active_user`
and a single broadcast `send_ui_update` — which meant a Telegram message could
clobber the desk user's identity, and a reply meant for the phone could be spoken
aloud at the desk. That is "crossing the streams."

This module introduces two things:

1. `OutputChannel` — a tiny transport-agnostic interface. Every inbound command
   carries the channel it arrived on, and every response is delivered back through
   THAT channel only. The HUD never hears a Telegram answer; Telegram never
   triggers the desk speakers.

2. `Session` / `SessionManager` — context scoped to a channel id (the
   `websocket_id` or the `telegram_user_id`), so concurrent conversations keep
   their own `user`, their own pending-decision slot, and their own output sink.

`COMMAND_LOCK` serialises the *shared* ActionEngine across channels. The engine is
a process-wide singleton with GUI focus state and pending-decision slots that are
not safe to mutate from two coroutines at once, so any path that drives the engine
acquires this lock first. Per-session scoping handles *routing*; this lock handles
*shared hardware/engine state*.
"""

from __future__ import annotations

import abc
import asyncio
import datetime
from typing import Any, Awaitable, Callable, Optional


# Serialises access to the shared ActionEngine (GUI focus, pending decisions,
# trace ring). Held only for the duration of a single engine action so the two
# channels interleave fairly rather than racing engine state.
COMMAND_LOCK = asyncio.Lock()


class OutputChannel(abc.ABC):
    """A destination J.A.R.V.I.S. can speak/reply to.

    Concrete channels: the HUD WebSocket (LocalHudChannel) and Telegram
    (TelegramChannel, defined in telegram_bot.py). The command pipeline only
    ever touches these three methods, so a reply can never leak to the wrong
    transport.
    """

    #: short tag for logs/telemetry, e.g. "hud" or "telegram"
    kind: str = "generic"

    def __init__(self, channel_id: str, user: str = "KAUSTAV") -> None:
        self.channel_id = channel_id
        self.user = user

    @abc.abstractmethod
    async def reply(self, text: str) -> None:
        """Deliver the final, user-facing answer on this channel."""

    async def notify(self, status: str, message: str = "") -> None:
        """Optional progress/status ping (HUD animations, typing indicator).

        Channels that have no concept of intermediate status (or want to stay
        quiet) may leave this as a no-op.
        """
        return None

    async def send_document(self, path: str, caption: str = "") -> bool:
        """Deliver a file on this channel. Not all channels support files."""
        return False


class CallbackChannel(OutputChannel):
    """An OutputChannel backed by plain async callbacks.

    Lets callers wire any transport in without subclassing — used by main.py to
    expose the HUD as a channel and by the Telegram bot to bridge into the shared
    command core.
    """

    def __init__(
        self,
        channel_id: str,
        reply_fn: Callable[[str], Awaitable[None]],
        *,
        user: str = "KAUSTAV",
        kind: str = "generic",
        notify_fn: Optional[Callable[[str, str], Awaitable[None]]] = None,
        document_fn: Optional[Callable[[str, str], Awaitable[bool]]] = None,
    ) -> None:
        super().__init__(channel_id, user)
        self.kind = kind
        self._reply_fn = reply_fn
        self._notify_fn = notify_fn
        self._document_fn = document_fn

    async def reply(self, text: str) -> None:
        if text and text.strip():
            await self._reply_fn(text)

    async def notify(self, status: str, message: str = "") -> None:
        if self._notify_fn is not None:
            try:
                await self._notify_fn(status, message)
            except Exception:
                pass

    async def send_document(self, path: str, caption: str = "") -> bool:
        if self._document_fn is not None:
            return await self._document_fn(path, caption)
        return False


class Session:
    """Per-channel conversation context.

    Keyed by channel id (websocket id or telegram user id). Holds the identity in
    play on that channel and any channel-local pending state, so two channels can
    be mid-conversation without stepping on each other.
    """

    def __init__(self, channel: OutputChannel) -> None:
        self.channel = channel
        self.created_at = datetime.datetime.now()
        self.last_active = self.created_at
        # Channel-local pending decision (e.g. a Telegram governance confirm),
        # kept OUT of the shared engine slots so a desk decision and a phone
        # decision never resolve each other.
        self.pending: dict[str, Any] = {}

    @property
    def id(self) -> str:
        return self.channel.channel_id

    @property
    def user(self) -> str:
        return self.channel.user

    def touch(self) -> None:
        self.last_active = datetime.datetime.now()


class SessionManager:
    """Registry of live sessions, keyed by channel id."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, channel: OutputChannel) -> Session:
        async with self._lock:
            sess = self._sessions.get(channel.channel_id)
            if sess is None:
                sess = Session(channel)
                self._sessions[channel.channel_id] = sess
            else:
                # Refresh the channel binding (a reconnecting socket reuses the id).
                sess.channel = channel
            sess.touch()
            return sess

    async def remove(self, channel_id: str) -> None:
        async with self._lock:
            self._sessions.pop(channel_id, None)

    def get(self, channel_id: str) -> Optional[Session]:
        return self._sessions.get(channel_id)

    def active_count(self) -> int:
        return len(self._sessions)


# Process-wide registry.
SESSIONS = SessionManager()
