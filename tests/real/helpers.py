"""Polling + lookup helpers shared by the integration tests.

Lives outside conftest so test modules can import it without tripping
pyright's `tests.real.conftest` resolution quirk.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional


def wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float = 10.0,
    interval: float = 0.5,
) -> Any:
    """Poll predicate() until truthy or timeout. Returns the last value."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


def find_position_by_magic(client, magic: int) -> Optional[dict]:
    positions = client.get("/positions") or []
    for p in positions:
        if int(p.get("magic", 0)) == magic:
            return p
    return None


def find_order_by_magic(client, magic: int) -> Optional[dict]:
    orders = client.get("/orders") or []
    for o in orders:
        if int(o.get("magic", 0)) == magic:
            return o
    return None
