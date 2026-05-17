"""Rates + ticks endpoints — historical market data."""


def test_get_rates_count(client, config):
    bars = client.get(
        f"/symbols/{config['symbol']}/rates",
        params={"timeframe": "H1", "count": 100},
    )
    assert isinstance(bars, list)
    assert len(bars) == 100
    for bar in bars[:3]:
        for key in ("time", "open", "high", "low", "close", "tick_volume", "spread"):
            assert key in bar, f"bar missing {key}"
        assert bar["high"] >= bar["low"]


def test_get_rates_timeframes(client, config):
    for tf in ("M5", "M15", "H1", "H4", "D1", "W1"):
        bars = client.get(
            f"/symbols/{config['symbol']}/rates",
            params={"timeframe": tf, "count": 10},
        )
        assert isinstance(bars, list) and len(bars) > 0, f"empty bars for {tf}"


def test_get_ticks(client, config):
    ticks = client.get(
        f"/symbols/{config['symbol']}/ticks",
        params={"count": 50},
    )
    assert isinstance(ticks, list) and len(ticks) > 0
    for t in ticks[:3]:
        assert "time" in t
        assert "bid" in t or "ask" in t


def test_rates_invalid_timeframe(client, config):
    """Bad timeframe -> 400, not 500."""
    import requests
    base = client.base_url.rstrip("/")
    resp = requests.get(
        f"{base}/symbols/{config['symbol']}/rates",
        params={"timeframe": "GARBAGE", "count": 10},
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=10,
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text[:200]}"
