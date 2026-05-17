"""GET /history/orders and /history/deals — both require `from`+`to` unix
timestamps. We pull a 30-day window ending now; demo has been open long
enough that there must be SOME deals. If not, we accept an empty list
(brand-new account) but require valid JSON shape.
"""
from __future__ import annotations

import time

import requests

DAY = 86400


def _get_history(client, kind, frm, to):
    base = client.base_url.rstrip("/")
    return requests.get(
        f"{base}/history/{kind}",
        params={"from": frm, "to": to},
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=30,
    )


def test_history_orders_30d(client):
    now = int(time.time())
    resp = _get_history(client, "orders", now - 30 * DAY, now)
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.text[:300]}"
    data = resp.json()
    assert isinstance(data, list)
    for o in data[:3]:
        for key in ("ticket", "symbol", "type", "time_setup"):
            assert key in o, f"history order missing {key}: {o}"


def test_history_deals_30d(client):
    now = int(time.time())
    resp = _get_history(client, "deals", now - 30 * DAY, now)
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.text[:300]}"
    data = resp.json()
    assert isinstance(data, list)
    for d in data[:3]:
        for key in ("ticket", "symbol", "type", "time"):
            assert key in d, f"history deal missing {key}: {d}"


def test_history_orders_missing_params_returns_400(client):
    base = client.base_url.rstrip("/")
    resp = requests.get(
        f"{base}/history/orders",
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=10,
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text[:200]}"


def test_history_deals_garbage_params_returns_400(client):
    base = client.base_url.rstrip("/")
    resp = requests.get(
        f"{base}/history/deals",
        params={"from": "not-a-number", "to": "also-not"},
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=10,
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text[:200]}"


def test_history_after_a_trade_contains_our_magic(client, config, cleanup_after):
    """End-to-end: open + close a position, then verify the resulting deals
    show up in /history/deals tagged with our magic number."""
    RETCODE_DONE = 10009
    from tests.real.helpers import find_position_by_magic, wait_until

    open_t = int(time.time())
    result = client.post(
        "/orders",
        json={
            "symbol": config["symbol"],
            "type": "BUY",
            "volume": config["volume"],
            "magic": config["magic"],
            "comment": "test-history",
        },
    )
    assert result.get("retcode") == RETCODE_DONE

    pos = wait_until(lambda: find_position_by_magic(client, config["magic"]))
    assert pos is not None

    close = client.delete(f"/positions/{pos['ticket']}")
    assert close.get("retcode") == RETCODE_DONE

    # Window: 60s before open, 60s buffer after now
    now = int(time.time())
    resp = _get_history(client, "deals", open_t - 60, now + 60)
    assert resp.status_code == 200
    deals = resp.json()
    matching = [d for d in deals if int(d.get("magic", 0)) == config["magic"]]
    assert matching, f"no deals found tagged with magic {config['magic']}"
    assert cleanup_after is None or True
