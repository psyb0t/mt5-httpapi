"""Thin HTTP client for hitting a live mt5-httpapi terminal.

Wraps requests with the auth header + base URL once so each test reads
like API usage, not URL plumbing.
"""
from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import urljoin

import requests


DEFAULT_TIMEOUT = 30


class APIError(AssertionError):
    """Raised when the API returns a non-2xx HTTP status."""


class Client:
    def __init__(self, base_url: str, token: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _check(self, resp: requests.Response, expect: Optional[int] = None) -> Any:
        if expect is not None and resp.status_code != expect:
            raise APIError(
                f"{resp.request.method} {resp.request.url} -> {resp.status_code} "
                f"(expected {expect}): {resp.text[:500]}"
            )
        if not 200 <= resp.status_code < 300:
            raise APIError(
                f"{resp.request.method} {resp.request.url} -> {resp.status_code}: "
                f"{resp.text[:500]}"
            )
        ctype = resp.headers.get("Content-Type", "")
        return resp.json() if "json" in ctype else resp.text

    def get(self, path: str, params: Optional[dict] = None, expect: Optional[int] = None):
        return self._check(
            self.session.get(self._url(path), params=params, timeout=self.timeout),
            expect=expect,
        )

    def post(self, path: str, json: Optional[dict] = None, expect: Optional[int] = None):
        return self._check(
            self.session.post(self._url(path), json=json, timeout=self.timeout),
            expect=expect,
        )

    def put(self, path: str, json: Optional[dict] = None, expect: Optional[int] = None):
        return self._check(
            self.session.put(self._url(path), json=json, timeout=self.timeout),
            expect=expect,
        )

    def delete(self, path: str, json: Optional[dict] = None, expect: Optional[int] = None):
        return self._check(
            self.session.delete(self._url(path), json=json, timeout=self.timeout),
            expect=expect,
        )


def from_env() -> Client:
    url = os.environ.get("MT5_API_URL")
    token = os.environ.get("MT5_API_TOKEN")
    if not url or not token:
        raise RuntimeError(
            "MT5_API_URL and MT5_API_TOKEN must be set (see tests/real/.env.example)"
        )
    return Client(url, token)
