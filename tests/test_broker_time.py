"""Tests for broker-time ↔ real-UTC conversion. UTC_OFFSET_SECONDS is a
module-level constant set at import; we monkeypatch it per-test to
exercise different broker offsets without re-importing.
"""

from datetime import datetime, timezone

import pytest

import mt5api.mt5client as mc


@pytest.mark.parametrize("offset_secs,broker_unix,expected_utc", [
    # No offset — passthrough
    (0, 1700000000, 1700000000),
    # UTC+3 (RoboForex / FTMO style)
    (10800, 1700010800, 1700000000),
    # UTC+2 (TeleTrade / many EU brokers)
    (7200, 1700007200, 1700000000),
    # West-of-UTC (negative)
    (-18000, 1699982000, 1700000000),
])
def test_broker_to_utc_seconds(monkeypatch, offset_secs, broker_unix, expected_utc):
    monkeypatch.setattr(mc, "UTC_OFFSET_SECONDS", offset_secs)
    assert mc.broker_to_utc_seconds(broker_unix) == expected_utc


@pytest.mark.parametrize("offset_secs,broker_ms,expected_utc_ms", [
    (0, 1700000000123, 1700000000123),
    (10800, 1700010800123, 1700000000123),
    (-18000, 1699982000456, 1700000000456),
])
def test_broker_to_utc_ms(monkeypatch, offset_secs, broker_ms, expected_utc_ms):
    monkeypatch.setattr(mc, "UTC_OFFSET_SECONDS", offset_secs)
    assert mc.broker_to_utc_ms(broker_ms) == expected_utc_ms


@pytest.mark.parametrize("offset_secs,utc_unix,expected_clock_face", [
    # Offset shifts the clock face we hand to MT5 — same instant in real
    # UTC, different wall clock for different brokers.
    (0, 1700000000, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)),
    (10800, 1700000000, datetime(2023, 11, 15, 1, 13, 20, tzinfo=timezone.utc)),
    (-18000, 1700000000, datetime(2023, 11, 14, 17, 13, 20, tzinfo=timezone.utc)),
])
def test_utc_seconds_to_broker_dt(monkeypatch, offset_secs, utc_unix, expected_clock_face):
    monkeypatch.setattr(mc, "UTC_OFFSET_SECONDS", offset_secs)
    got = mc.utc_seconds_to_broker_dt(utc_unix)
    assert got == expected_clock_face


def test_roundtrip(monkeypatch):
    """A broker timestamp converted to real UTC and back to a broker
    datetime should land on the same wall-clock face."""
    monkeypatch.setattr(mc, "UTC_OFFSET_SECONDS", 10800)
    broker_unix = 1700010800
    real_utc = mc.broker_to_utc_seconds(broker_unix)
    broker_dt = mc.utc_seconds_to_broker_dt(real_utc)
    # Compare the wall-clock seconds the broker would see
    assert int(broker_dt.timestamp()) == broker_unix
