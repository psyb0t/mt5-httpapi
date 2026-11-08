"""A stand-in for the telethon-plus sidecar, served on a real socket.

It speaks the parts of telethon-plus the signal copier uses, in the shapes
docker-telethon-plus v0.7.1 sends them (app/server.py, app/tools.py,
app/updates.py there):

- GET /api/entities?chat=... -> {"id": <bare id>, "type": "Channel", ...}
- GET /api/messages?chat=...&limit=N -> newest first, with a bare chat_id
- GET /healthz
- /ws/updates?token=... -> one JSON event per frame, chat_id marked
  (-100<id>); a wrong token refuses the handshake with 403
- Authorization: Bearer <auth key> on every /api route, 401 otherwise

Tests post messages with ``post`` (stored for polls and pushed to every
connected websocket) and can drop every websocket with ``drop_websockets``.
Every value is made up (TST6).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response

MARKED_CHANNEL_OFFSET = 1_000_000_000_000


class MockTelethonPlus:
    def __init__(self, chats: dict[str, int], auth_key: str = "") -> None:
        self.chats = chats
        self.auth_key = auth_key
        # The key /ws/updates checks; tests change it to refuse only the
        # websocket while the API keeps answering.
        self.ws_auth_key = auth_key
        self.messages: dict[int, list[dict[str, Any]]] = {chat_id: [] for chat_id in chats.values()}
        self.requests: list[str] = []
        self.websockets: set[ServerConnection] = set()
        self.connections = 0
        self.refused_websockets = 0
        self.refuse_api = False
        self._server: Server | None = None
        self._next_id = 100

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"http://{host}:{port}"

    async def start(self) -> None:
        self._server = await serve(self._ws, "127.0.0.1", 0, process_request=self._http)

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    def _json(self, connection: ServerConnection, status: HTTPStatus, body: Any) -> Response:
        response = connection.respond(status, json.dumps(body))
        response.headers["Content-Type"] = "application/json"
        return response

    def _http(self, connection: ServerConnection, request: Request) -> Response | None:
        parts = urlsplit(request.path)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        self.requests.append(parts.path)
        if parts.path == "/ws/updates":
            if self.ws_auth_key and query.get("token", "") != self.ws_auth_key:
                self.refused_websockets += 1
                return connection.respond(HTTPStatus.FORBIDDEN, "")
            return None
        if parts.path == "/healthz":
            return self._json(connection, HTTPStatus.OK, {"status": "ok"})
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if self.auth_key and token != self.auth_key:
            return self._json(connection, HTTPStatus.UNAUTHORIZED, {"detail": "unauthorized"})
        if self.refuse_api:
            return self._json(connection, HTTPStatus.BAD_GATEWAY, {"detail": "telegram error"})
        chat_id = self.chats.get(query.get("chat", ""))
        if chat_id is None:
            return self._json(connection, HTTPStatus.BAD_REQUEST, {"detail": "cannot resolve chat"})
        if parts.path == "/api/entities":
            return self._json(connection, HTTPStatus.OK, {"id": chat_id, "type": "Channel", "title": "Test channel"})
        if parts.path == "/api/messages":
            limit = int(query.get("limit", "20"))
            newest_first = list(reversed(self.messages[chat_id]))[:limit]
            return self._json(connection, HTTPStatus.OK, newest_first)
        return self._json(connection, HTTPStatus.NOT_FOUND, {"detail": "Not Found"})

    async def _ws(self, connection: ServerConnection) -> None:
        self.connections += 1
        self.websockets.add(connection)
        try:
            await connection.wait_closed()
        finally:
            self.websockets.discard(connection)

    async def post(self, chat: str, text: str, push: bool = True) -> int:
        """Add a message to the chat; push it live unless ``push`` is False
        (as when telethon-plus dropped the subscriber)."""
        chat_id = self.chats[chat]
        self._next_id += 1
        date = datetime.now(timezone.utc).isoformat()
        self.messages[chat_id].append(
            {"id": self._next_id, "date": date, "chat_id": chat_id, "text": text, "out": False}
        )
        if push:
            event = {
                "type": "NewMessage",
                "message": {
                    "id": self._next_id,
                    "date": date,
                    "text": text,
                    "out": False,
                    "chat_id": -(MARKED_CHANNEL_OFFSET + chat_id),
                },
                "chat_id": -(MARKED_CHANNEL_OFFSET + chat_id),
            }
            for connection in list(self.websockets):
                await connection.send(json.dumps(event))
        return self._next_id

    async def drop_websockets(self) -> None:
        for connection in list(self.websockets):
            await connection.close()

    async def wait_for_websocket(self, connections: int = 1, timeout: float = 5.0) -> None:
        async def _wait() -> None:
            while self.connections < connections or not self.websockets:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_wait(), timeout)
