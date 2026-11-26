#!/usr/bin/env python3
"""Measure each terminal's broker-clock offset from real UTC.

config.yaml's per-terminal `utc_offset` is either a static number that
mt5api subtracts from every broker timestamp (mt5client.broker_to_utc_seconds)
or `auto`, which makes the API measure it itself (mt5api/broker_clock.py). A
static value that is right in August is one hour wrong after the autumn
rollover: re-run this then and update config.yaml, or switch to `auto`. For an
`auto` terminal the `mode` column shows what the API last decided.

Reads the latest tick and compares its broker timestamp to local UTC, so
it only works while the market is open and the terminal can log in.

A `mode: backtest` terminal has no SDK attached and answers SDK routes with
503, so this can only measure `mode: live` terminals. Those report the
failure and the run moves on.

Usage:
    python3 scripts/measure-broker-offsets.py [broker/account ...]

With no arguments it measures every terminal in config.yaml.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, os.pardir, "config", "config.yaml")
BASE = os.environ.get("MT5_HTTPAPI_BASE_URL", "http://127.0.0.1:8888")
TIMEOUT = int(os.environ.get("MEASURE_TIMEOUT", "120"))
# Broker clocks sit on quarter-hour boundaries; snapping absorbs tick latency.
QUANTUM = 900
PREFERRED = ("EURUSD", "BTCUSD", "XAUUSD")


def load_config():
    with open(CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def api(token, path):
    req = urllib.request.Request(
        f"{BASE}/{path}", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def pick_symbol(token, route):
    """Shortest name matching a liquid, near-24h instrument."""
    symbols = api(token, f"{route}/symbols")
    if not isinstance(symbols, list):
        raise RuntimeError(f"unexpected /symbols payload: {symbols!r}")
    for prefix in PREFERRED:
        matches = [s for s in symbols if s.upper().startswith(prefix)]
        if matches:
            return sorted(matches, key=len)[0]
    raise RuntimeError("no EURUSD/BTCUSD/XAUUSD variant in the symbol list")


def measure(token, route):
    symbol = pick_symbol(token, route)
    before = time.time()
    tick = api(token, f"{route}/symbols/{symbol}/tick")
    after = time.time()
    broker_time = tick.get("time")
    if not broker_time:
        raise RuntimeError(f"tick carried no time: {tick!r}")
    # The route already subtracts the CONFIGURED offset, so add it back to
    # recover the raw broker clock — otherwise this reports 0 once the config
    # is correct instead of confirming it.
    terminal = api(token, f"{route}/terminal")
    configured = terminal.get("broker_utc_offset_seconds", 0)
    mode = terminal.get("broker_utc_offset_mode", "static")
    if mode == "auto":
        mode = f"auto/{terminal.get('broker_utc_offset_status', '?')}"
    raw = (broker_time + configured) - (before + after) / 2
    offset = round(raw / QUANTUM) * QUANTUM
    return symbol, offset, raw, configured, mode


def main():
    cfg = load_config()
    token = cfg.get("api_token") or ""
    routes = sys.argv[1:]
    if not routes:
        seen, routes = set(), []
        for term in cfg.get("terminals", []):
            route = f"{term['broker']}/{term['account']}"
            if route not in seen:
                seen.add(route)
                routes.append(route)

    failures = 0
    print(f"{'terminal':<32}{'symbol':<14}{'measured':>10}{'configured':>12}  mode")
    for route in routes:
        try:
            symbol, offset, raw, configured, mode = measure(token, route)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
            failures += 1
            print(f"{route:<32}FAILED: {exc}")
            continue
        flag = "" if offset == configured else "   <-- MISMATCH"
        print(
            f"{route:<32}{symbol:<14}{offset / 3600:>+9.2f}h"
            f"{configured / 3600:>+11.2f}h  {mode}{flag}  (raw {raw:.1f}s)"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
