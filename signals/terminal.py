"""REST calls to one terminal's API through nginx."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from signals.config import Target
from signals.constants import (
    BEARER_PREFIX,
    HEADER_AUTHORIZATION,
    HTTP_NOT_FOUND,
    TERMINAL_TIMEOUT_SECONDS,
)
from signals.errors import TerminalRejected, TerminalUnreachable

logger = logging.getLogger(__name__)

_MAX_ERROR_BODY_CHARS = 2000
_NOT_JSON = "response is not JSON"


class TerminalClient:
    """One pooled httpx client for every terminal; the target picks the path
    prefix."""

    def __init__(
        self,
        mt5_url: str,
        api_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {HEADER_AUTHORIZATION: f"{BEARER_PREFIX}{api_token}"} if api_token else {}
        self._mt5_url = mt5_url
        self._http = httpx.AsyncClient(
            timeout=TERMINAL_TIMEOUT_SECONDS,
            headers=headers,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(
        self,
        target: Target,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response:
        url = f"{self._mt5_url}/{target.broker}/{target.account}/{target.instance}{path}"
        # Route only: order bodies stay out of the logs (PY15).
        logger.debug(
            "calling terminal",
            extra={"terminal": target.terminal, "http_method": method, "path": path},
        )
        try:
            response = await self._http.request(method, url, json=body)
        except httpx.HTTPError as err:
            raise TerminalUnreachable(target.terminal, str(err) or type(err).__name__) from err
        if allow_not_found and response.status_code == HTTP_NOT_FOUND:
            return response
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise TerminalRejected(
                target.terminal,
                response.status_code,
                response.text[:_MAX_ERROR_BODY_CHARS],
            )
        return response

    async def _json(self, target: Target, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._call(target, method, path, **kwargs)
        try:
            return response.json()
        except ValueError as err:
            raise TerminalRejected(
                target.terminal, response.status_code, _NOT_JSON
            ) from err

    async def symbol(self, target: Target, symbol: str) -> dict[str, Any] | None:
        """GET /symbols/<symbol>, or None when the broker has no such symbol."""
        response = await self._call(
            target, "GET", f"/symbols/{quote(symbol, safe='')}", allow_not_found=True
        )
        if response.status_code == HTTP_NOT_FOUND:
            return None
        try:
            data = response.json()
        except ValueError as err:
            raise TerminalRejected(
                target.terminal, response.status_code, _NOT_JSON
            ) from err
        return data if isinstance(data, dict) else None

    async def account(self, target: Target) -> dict[str, Any]:
        data = await self._json(target, "GET", "/account")
        return data if isinstance(data, dict) else {}

    async def positions(self, target: Target) -> list[dict[str, Any]]:
        data = await self._json(target, "GET", "/positions")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def orders(self, target: Target) -> list[dict[str, Any]]:
        data = await self._json(target, "GET", "/orders")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def place(self, target: Target, order: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """POST /orders. Returns the status (201 done, 200 broker result or a
        duplicate) and the body."""
        response = await self._call(target, "POST", "/orders", body=order)
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data if isinstance(data, dict) else {}
