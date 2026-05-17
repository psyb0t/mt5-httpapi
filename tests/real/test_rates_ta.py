"""Wickworks TA passthrough — POST /symbols/<symbol>/rates/ta with an
indicators spec returns bars + computed TA.

Wickworks v0.3.x is primitives-only (rsi/ema/sma/atr/macd/bbands/stoch/adx/...)
— no divergences/signals/divTrends. If wickworks is down the endpoint
returns 502; we skip rather than fail in that case.
"""
from __future__ import annotations

import pytest

import requests


def _post_ta(client, symbol, indicators, **params):
    base = client.base_url.rstrip("/")
    resp = requests.post(
        f"{base}/symbols/{symbol}/rates/ta",
        params=params or None,
        json={"indicators": indicators},
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=30,
    )
    return resp


def test_rates_ta_rsi_ema_atr(client, config):
    resp = _post_ta(
        client,
        config["symbol"],
        indicators={
            "rsi14": {"type": "rsi", "params": {"period": 14}},
            "ema21": {"type": "ema", "params": {"period": 21}},
            "atr14": {"type": "atr", "params": {"period": 14}},
        },
        timeframe="H1",
        count=200,
    )
    if resp.status_code == 502:
        pytest.skip(f"wickworks unavailable: {resp.text[:200]}")
    assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body["symbol"] == config["symbol"]
    assert body["timeframe"] == "H1"
    assert isinstance(body["bars"], list) and len(body["bars"]) > 0
    assert body["ta"] is not None, f"ta is null: {body}"
    # Wickworks puts results under the keys we supplied.
    ta = body["ta"]
    assert "rsi14" in ta or "indicators" in ta, f"unexpected ta shape: {list(ta.keys())[:10]}"


def test_rates_ta_macd(client, config):
    resp = _post_ta(
        client,
        config["symbol"],
        indicators={
            "macd": {
                "type": "macd",
                "params": {"fastPeriod": 12, "slowPeriod": 26, "signalPeriod": 9},
            },
        },
        timeframe="H1",
        count=200,
    )
    if resp.status_code == 502:
        pytest.skip(f"wickworks unavailable: {resp.text[:200]}")
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.text[:300]}"


def test_rates_ta_empty_indicators_returns_400(client, config):
    base = client.base_url.rstrip("/")
    resp = requests.post(
        f"{base}/symbols/{config['symbol']}/rates/ta",
        params={"timeframe": "H1", "count": 50},
        json={"indicators": {}},
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=15,
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text[:200]}"


def test_rates_ta_missing_body_returns_400(client, config):
    base = client.base_url.rstrip("/")
    resp = requests.post(
        f"{base}/symbols/{config['symbol']}/rates/ta",
        params={"timeframe": "H1", "count": 50},
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=15,
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text[:200]}"
