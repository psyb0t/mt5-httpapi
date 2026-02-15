"""
mt5-httpapi client. Thin wrapper around the REST API.
"""

import os

import pandas as pd
import requests

API_URL = os.environ.get("MT5_API_URL", "http://localhost:6542")


def get_candles(symbol, timeframe="H4", count=200):
    """Fetch OHLCV candles as a DataFrame."""
    url = f"{API_URL}/symbols/{symbol}/rates?timeframe={timeframe}&count={count}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    df = pd.DataFrame(data)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_tick(symbol):
    """Get latest tick (bid/ask)."""
    url = f"{API_URL}/symbols/{symbol}/tick"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def get_symbol_info(symbol):
    """Get full symbol info."""
    url = f"{API_URL}/symbols/{symbol}"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def get_account():
    """Get account info."""
    url = f"{API_URL}/account"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()
