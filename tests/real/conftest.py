"""Real-API test fixtures.

Loads tests/real/.env, exposes a session-scoped HTTP client + symbol info,
and registers a cleanup that purges any test-tagged positions/orders before
AND after the run. Tagging is by magic number (MT5_TEST_MAGIC) so concurrent
manual trading on the same account is untouched.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.real.client import Client, from_env


def _load_env() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()


def _purge(client: Client, magic: int) -> None:
    """Close every position and cancel every pending order tagged with our magic."""
    positions = client.get("/positions") or []
    for pos in positions:
        if int(pos.get("magic", 0)) != magic:
            continue
        try:
            client.delete(f"/positions/{pos['ticket']}")
        except Exception as exc:
            print(f"warn: could not close position {pos.get('ticket')}: {exc}")

    orders = client.get("/orders") or []
    for o in orders:
        if int(o.get("magic", 0)) != magic:
            continue
        try:
            client.delete(f"/orders/{o['ticket']}")
        except Exception as exc:
            print(f"warn: could not cancel order {o.get('ticket')}: {exc}")


@pytest.fixture(scope="session")
def client() -> Client:
    c = from_env()
    pong = c.get("/ping")
    assert pong, "API /ping returned empty — terminal unreachable or not initialized"
    return c


@pytest.fixture(scope="session")
def config() -> dict:
    return {
        "symbol": os.environ.get("MT5_TEST_SYMBOL", "ADAUSD"),
        "volume": float(os.environ.get("MT5_TEST_VOLUME", "100")),
        "magic": int(os.environ.get("MT5_TEST_MAGIC", "99999")),
    }


@pytest.fixture(scope="session", autouse=True)
def _session_cleanup(client: Client, config: dict):
    """Purge stale test artifacts before the session AND after it ends."""
    _purge(client, config["magic"])
    yield
    _purge(client, config["magic"])


@pytest.fixture
def cleanup_after(client: Client, config: dict):
    """Per-test cleanup — closes everything our magic placed in this test."""
    yield
    _purge(client, config["magic"])


@pytest.fixture(scope="session")
def symbol_info(client: Client, config: dict) -> dict:
    info = client.get(f"/symbols/{config['symbol']}")
    assert isinstance(info, dict), f"symbol info for {config['symbol']} not a dict: {info}"
    assert info.get("name") == config["symbol"]
    return info


@pytest.fixture
def current_tick(client: Client, config: dict) -> dict:
    tick = client.get(f"/symbols/{config['symbol']}/tick")
    assert tick.get("ask", 0) > 0 and tick.get("bid", 0) > 0, f"bad tick: {tick}"
    return tick


