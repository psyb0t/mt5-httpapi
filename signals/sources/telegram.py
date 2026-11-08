"""Telegram channels and groups, read through the telethon-plus sidecar.

New messages arrive live over telethon-plus's /ws/updates. A catch-up poll of
GET /api/messages per source, every ``poll_seconds``, covers what the
websocket missed: telethon-plus drops a subscriber whose buffer fills, and
nothing arrives while this service or the websocket is down. After every
(re)connect all sources are polled once.

On a source's first start its recent messages are recorded as seen instead
of traded, so adding a channel never fires its backlog.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from signals.config import Settings, Source
from signals.constants import (
    BEARER_PREFIX,
    HEADER_AUTHORIZATION,
    MARKED_CHANNEL_OFFSET,
    POLL_MESSAGES,
    RECONNECT_MAX_SECONDS,
    RECONNECT_MIN_SECONDS,
    TELETHON_TIMEOUT_SECONDS,
)
from signals.model import Message
from signals.sources import Emit
from signals.state import State

logger = logging.getLogger(__name__)

EVENT_NEW_MESSAGE = "NewMessage"


def bare_peer_id(value: Any) -> int | None:
    """A chat id without Telethon's marking (-100 for channels, - for basic
    groups), the form GET /api/entities and GET /api/messages report."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        number = -number
        if number > MARKED_CHANNEL_OFFSET:
            number -= MARKED_CHANNEL_OFFSET
    return number


def _time(value: Any) -> datetime:
    """A message date from telethon-plus (ISO 8601), or now if it has none."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


class TelegramHub:
    """One websocket to telethon-plus shared by every telegram source, plus
    one poll loop per source."""

    def __init__(
        self,
        settings: Settings,
        sources: list[Source],
        state: State,
        emit: Emit,
        transport: httpx.AsyncBaseTransport | None = None,
        connect: Any = ws_connect,
    ) -> None:
        self._settings = settings
        self._sources = sources
        self._state = state
        self._emit = emit
        self._connect = connect
        headers = (
            {HEADER_AUTHORIZATION: f"{BEARER_PREFIX}{settings.telethon_auth_key}"}
            if settings.telethon_auth_key
            else {}
        )
        self._http = httpx.AsyncClient(
            base_url=settings.telethon_url,
            headers=headers,
            timeout=TELETHON_TIMEOUT_SECONDS,
            transport=transport,
        )
        self._by_chat: dict[int, Source] = {}
        # Sources whose first poll has succeeded. Only that first poll may
        # record a backlog as seen; asking the state on every poll would keep
        # a channel that was empty at startup in first-start mode, and drop
        # the messages that reach us only through a poll.
        self._baselined: set[str] = set()

    async def aclose(self) -> None:
        await self._http.aclose()

    def websocket_url(self) -> str:
        base = self._settings.telethon_url
        if base.startswith("https://"):
            base = "wss://" + base.removeprefix("https://")
        elif base.startswith("http://"):
            base = "ws://" + base.removeprefix("http://")
        url = f"{base}/ws/updates"
        if self._settings.telethon_auth_key:
            url += "?" + urlencode({"token": self._settings.telethon_auth_key})
        return url

    async def resolve(self) -> None:
        """Map each source's chat to its numeric id, retrying until
        telethon-plus answers (it may still be starting)."""
        for source in self._sources:
            delay = RECONNECT_MIN_SECONDS
            while True:
                try:
                    response = await self._http.get("/api/entities", params={"chat": source.chat})
                    response.raise_for_status()
                    chat_id = bare_peer_id(response.json().get("id"))
                    if chat_id is None:
                        raise ValueError("entity has no id")
                    self._by_chat[chat_id] = source
                    logger.info(
                        "telegram chat resolved",
                        extra={"source": source.name, "chat_id": chat_id},
                    )
                    break
                except (httpx.HTTPError, ValueError) as err:
                    logger.warning(
                        "cannot resolve telegram chat, retrying",
                        extra={"source": source.name, "reason": str(err), "retry_seconds": delay},
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_SECONDS)

    async def poll(self, source: Source) -> None:
        """Read the source's latest messages and hand over the ones not seen
        yet, oldest first."""
        try:
            response = await self._http.get(
                "/api/messages",
                params={"chat": source.chat, "limit": POLL_MESSAGES},
            )
            response.raise_for_status()
            items = response.json()
        except (httpx.HTTPError, ValueError) as err:
            logger.warning(
                "telegram poll failed",
                extra={"source": source.name, "reason": str(err)},
            )
            return
        if not isinstance(items, list):
            return
        first_start = source.name not in self._baselined and not self._state.has_any(source.name)
        self._baselined.add(source.name)
        messages = sorted(
            (
                self._message(source, item)
                for item in items
                if isinstance(item, dict) and item.get("id") is not None
            ),
            key=lambda message: int(message.id),
        )
        if first_start:
            for message in messages:
                self._state.mark_seen(source.name, message.id)
            logger.info(
                "first start, existing messages recorded as seen and not traded",
                extra={"source": source.name, "messages": len(messages)},
            )
            return
        for message in messages:
            await self._emit(message)

    def _message(self, source: Source, item: dict[str, Any]) -> Message:
        return Message(
            source=source.name,
            id=str(int(item["id"])),
            time=_time(item.get("date")),
            text=str(item.get("text") or ""),
        )

    async def on_event(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except ValueError:
            return
        if not isinstance(payload, dict) or payload.get("type") != EVENT_NEW_MESSAGE:
            return
        message = payload.get("message")
        if not isinstance(message, dict) or message.get("id") is None:
            return
        chat_id = bare_peer_id(payload.get("chat_id", message.get("chat_id")))
        source = self._by_chat.get(chat_id) if chat_id is not None else None
        if source is None:
            return
        await self._emit(self._message(source, message))

    async def _listen(self) -> None:
        delay = RECONNECT_MIN_SECONDS
        while True:
            try:
                async with self._connect(self.websocket_url()) as websocket:
                    logger.info("telegram updates connected")
                    delay = RECONNECT_MIN_SECONDS
                    for source in self._sources:
                        await self.poll(source)
                    async for raw in websocket:
                        await self.on_event(raw)
            except (OSError, TimeoutError, WebSocketException) as err:
                logger.warning(
                    "telegram updates disconnected, reconnecting",
                    extra={"reason": str(err) or type(err).__name__, "retry_seconds": delay},
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)

    async def _poll_forever(self, source: Source) -> None:
        while True:
            await asyncio.sleep(source.poll_seconds)
            await self.poll(source)

    async def run(self) -> None:
        await self.resolve()
        for source in self._sources:
            await self.poll(source)
        await asyncio.gather(
            self._listen(),
            *(self._poll_forever(source) for source in self._sources),
        )
