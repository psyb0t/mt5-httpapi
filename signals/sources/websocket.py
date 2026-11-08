"""Any websocket feed that sends one signal per frame.

A frame is the message text itself, or a JSON object when ``text_field`` is
set. ``id_field`` names the frame's message id; an id that does not fit a
client_order_id is hashed, and a frame without one gets an id from its text
and arrival time. A websocket has no history to catch up from, so frames sent
while the connection is down are lost.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from signals.config import Source
from signals.constants import (
    MESSAGE_ID_HASH_CHARS,
    MESSAGE_ID_RE,
    RECONNECT_MAX_SECONDS,
    RECONNECT_MIN_SECONDS,
)
from signals.model import Message
from signals.sources import Emit

logger = logging.getLogger(__name__)


def _hash_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:MESSAGE_ID_HASH_CHARS]


def frame_message(source: Source, raw: str | bytes) -> Message | None:
    """The frame as a Message, or None when it carries no text."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    frame_id: str | None = None
    if source.text_field:
        try:
            payload = json.loads(raw)
        except ValueError:
            logger.warning("websocket frame is not JSON", extra={"source": source.name})
            return None
        if not isinstance(payload, dict):
            return None
        text = payload.get(source.text_field)
        if not isinstance(text, str):
            logger.warning(
                "websocket frame has no text field",
                extra={"source": source.name, "field": source.text_field},
            )
            return None
        if source.id_field and payload.get(source.id_field) is not None:
            frame_id = str(payload[source.id_field])
    else:
        text = raw

    if frame_id is None:
        message_id = _hash_id(f"{text}\0{time.time_ns()}")
    elif MESSAGE_ID_RE.match(frame_id):
        message_id = frame_id
    else:
        message_id = _hash_id(frame_id)
    return Message(source=source.name, id=message_id, time=datetime.now(timezone.utc), text=text)


class WebsocketSource:
    def __init__(self, source: Source, emit: Emit, connect: Any = ws_connect) -> None:
        self._source = source
        self._emit = emit
        self._connect = connect

    async def run(self) -> None:
        delay = RECONNECT_MIN_SECONDS
        while True:
            try:
                async with self._connect(
                    self._source.url,
                    additional_headers=self._source.headers or None,
                ) as websocket:
                    logger.info("websocket source connected", extra={"source": self._source.name})
                    delay = RECONNECT_MIN_SECONDS
                    async for raw in websocket:
                        message = frame_message(self._source, raw)
                        if message is not None:
                            await self._emit(message)
            except (OSError, TimeoutError, WebSocketException) as err:
                logger.warning(
                    "websocket source disconnected, reconnecting",
                    extra={
                        "source": self._source.name,
                        "reason": str(err) or type(err).__name__,
                        "retry_seconds": delay,
                    },
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)
