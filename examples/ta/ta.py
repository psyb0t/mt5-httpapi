#!/usr/bin/env python3
"""
Technical analysis example for mt5-httpapi.

Pulls candle data from the API, calculates indicators, detects signals.

Usage:
    python ta.py
    python ta.py BTCUSD H1 100
    MT5_API_URL=http://10.0.0.5:6542 python ta.py EURUSD D1 200

Requirements:
    pip install -r requirements.txt
"""

import os
import sys

import pandas as pd
import requests

from indicators import add_all
from signals import detect_signals

API_URL = os.environ.get("MT5_API_URL", "http://localhost:6542")

symbol = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
timeframe = sys.argv[2] if len(sys.argv) > 2 else "H4"
count = int(sys.argv[3]) if len(sys.argv) > 3 else 200


def get_candles(symbol, timeframe, count):
    url = f"{API_URL}/symbols/{symbol}/rates?timeframe={timeframe}&count={count}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    return pd.DataFrame(data)


def print_report(df, symbol, timeframe):
    latest = df.iloc[-1]
    dt = pd.to_datetime(latest["time"], unit="s")

    print(f"\n{'='*50}")
    print(f" {symbol} {timeframe} — {dt}")
    print(f"{'='*50}")
    print(f" Price:     {latest['close']}")
    print(f" Open:      {latest['open']}")
    print(f" High:      {latest['high']}")
    print(f" Low:       {latest['low']}")

    print(f"{'─'*50}")
    for col, label in [
        ("ema_21", "EMA 21"), ("sma_50", "SMA 50"),
        ("sma_100", "SMA 100"), ("sma_200", "SMA 200"),
    ]:
        val = latest.get(col)
        print(f" {label+':':<12}{val:.6f}" if pd.notna(val) else f" {label+':':<12}N/A")

    print(f"{'─'*50}")
    for col, label in [
        ("atr", "ATR(14)"), ("rsi", "RSI(14)"), ("mfi", "MFI(14)"), ("ADX_14", "ADX(14)"),
    ]:
        val = latest.get(col)
        print(f" {label+':':<12}{val:.4f}" if pd.notna(val) else f" {label+':':<12}N/A")

    print(f"{'─'*50}")
    macd_val = latest.get("MACD_12_26_9")
    if pd.notna(macd_val):
        print(f" {'MACD:':<12}{macd_val:.6f}")
        print(f" {'Signal:':<12}{latest['MACDs_12_26_9']:.6f}")
        print(f" {'Histogram:':<12}{latest['MACDh_12_26_9']:.6f}")
    else:
        print(f" {'MACD:':<12}N/A")

    bbu = latest.get("BBU_20_2.0")
    if pd.notna(bbu):
        print(f"{'─'*50}")
        print(f" {'BB Upper:':<12}{bbu:.6f}")
        print(f" {'BB Middle:':<12}{latest['BBM_20_2.0']:.6f}")
        print(f" {'BB Lower:':<12}{latest['BBL_20_2.0']:.6f}")

    stoch_k = latest.get("STOCHk_14_3_3")
    if pd.notna(stoch_k):
        print(f"{'─'*50}")
        print(f" {'Stoch %K:':<12}{stoch_k:.2f}")
        print(f" {'Stoch %D:':<12}{latest['STOCHd_14_3_3']:.2f}")

    vwap_val = latest.get("vwap")
    if pd.notna(vwap_val):
        print(f"{'─'*50}")
        print(f" {'VWAP:':<12}{vwap_val:.6f}")

    print(f"{'='*50}")

    # signals
    sigs = detect_signals(df)
    if sigs:
        print(f"\n Signals:")
        for s in sigs:
            print(f"  • {s}")
    else:
        print(f"\n No signals.")
    print()


def main():
    print(f"Fetching {count} {timeframe} candles for {symbol}...")

    df = get_candles(symbol, timeframe, count)
    if df is None:
        print(f"No data for {symbol}")
        sys.exit(1)

    df = add_all(df)
    print_report(df, symbol, timeframe)


if __name__ == "__main__":
    main()
