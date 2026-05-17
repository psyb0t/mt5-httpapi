"""SL/TP modification on an open position via PUT /positions/<ticket>.

Open a BUY at market, set SL well below entry and TP well above, verify
both values land in /positions, then close.
"""
from __future__ import annotations

from tests.real.helpers import find_position_by_magic, wait_until

RETCODE_DONE = 10009


def _round(p, digits):
    return round(p, digits)


def test_modify_sl_tp_on_open_position(client, config, symbol_info, current_tick, cleanup_after):
    digits = symbol_info["digits"]
    assert current_tick["ask"] > 0  # sanity — broker has live price

    open_body = {
        "symbol": config["symbol"],
        "type": "BUY",
        "volume": config["volume"],
        "magic": config["magic"],
        "comment": "test-sltp",
    }
    result = client.post("/orders", json=open_body)
    assert result.get("retcode") == RETCODE_DONE, f"open failed: {result}"

    pos = wait_until(lambda: find_position_by_magic(client, config["magic"]))
    assert pos is not None
    entry = pos["price_open"]

    # SL 30% below entry, TP 30% above. Wide enough that an intra-test price
    # blip can't trigger either.
    sl = _round(entry * 0.70, digits)
    tp = _round(entry * 1.30, digits)
    mod = client.put(f"/positions/{pos['ticket']}", json={"sl": sl, "tp": tp})
    assert mod.get("retcode") == RETCODE_DONE, f"SL/TP modify failed: {mod}"

    def _has_sltp():
        p = find_position_by_magic(client, config["magic"])
        if not p:
            return None
        tol = 10 ** (-digits) * 10
        if abs(p.get("sl", 0) - sl) < tol and abs(p.get("tp", 0) - tp) < tol:
            return p
        return None
    updated = wait_until(_has_sltp)
    assert updated is not None, "SL/TP not reflected in position"

    close = client.delete(f"/positions/{pos['ticket']}")
    assert close.get("retcode") == RETCODE_DONE
    assert cleanup_after is None or True


def test_modify_sl_only(client, config, symbol_info, current_tick, cleanup_after):
    digits = symbol_info["digits"]
    assert current_tick["ask"] > 0
    open_body = {
        "symbol": config["symbol"],
        "type": "BUY",
        "volume": config["volume"],
        "magic": config["magic"],
    }
    result = client.post("/orders", json=open_body)
    assert result.get("retcode") == RETCODE_DONE

    pos = wait_until(lambda: find_position_by_magic(client, config["magic"]))
    assert pos is not None
    sl = _round(pos["price_open"] * 0.70, digits)

    mod = client.put(f"/positions/{pos['ticket']}", json={"sl": sl})
    assert mod.get("retcode") == RETCODE_DONE

    close = client.delete(f"/positions/{pos['ticket']}")
    assert close.get("retcode") == RETCODE_DONE
    assert cleanup_after is None or True


def test_get_position_by_ticket(client, config, cleanup_after):
    """GET /positions/<ticket> returns the same position as the list view.

    Order endpoint auto-selects the symbol — no pre-call to /tick or
    /symbols/<s> needed before placing the order.
    """
    open_body = {
        "symbol": config["symbol"],
        "type": "BUY",
        "volume": config["volume"],
        "magic": config["magic"],
        "type_filling": "IOC",
    }
    result = client.post("/orders", json=open_body)
    assert result.get("retcode") == RETCODE_DONE, f"open failed: {result}"

    pos = wait_until(lambda: find_position_by_magic(client, config["magic"]))
    assert pos is not None

    single = client.get(f"/positions/{pos['ticket']}")
    assert isinstance(single, dict), f"unexpected response: {single}"
    assert single["ticket"] == pos["ticket"]
    assert single["symbol"] == config["symbol"]
    assert single["magic"] == config["magic"]
    assert single["volume"] == config["volume"]

    close = client.delete(f"/positions/{pos['ticket']}")
    assert close.get("retcode") == RETCODE_DONE
    assert cleanup_after is None or True


def test_get_position_not_found(client, config):
    """GET /positions/<bogus_ticket> -> 404, not 500."""
    import requests
    base = client.base_url.rstrip("/")
    resp = requests.get(
        f"{base}/positions/999999999",
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=10,
    )
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text[:200]}"
    _ = config


def test_modify_position_not_found(client, config):
    """Modifying a non-existent ticket -> 404, not 500."""
    import requests
    base = client.base_url.rstrip("/")
    resp = requests.put(
        f"{base}/positions/999999999",
        json={"sl": 0.1, "tp": 0.9},
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=10,
    )
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text[:200]}"
    _ = config  # fixture is session-scoped, requesting it primes env validation
