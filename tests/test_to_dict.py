"""Tests for to_dict — converts MT5 namedtuple structs into JSON-ready
dicts and normalizes broker-time fields to real UTC using UTC_OFFSET_SECONDS.
"""

from collections import namedtuple

import pytest

import mt5api.mt5client as mc


def _make_nt(**fields):
    NT = namedtuple("NT", list(fields.keys()))
    return NT(**fields)


def test_to_dict_none_returns_none():
    assert mc.to_dict(None) is None


@pytest.mark.parametrize("offset,fields_in,fields_out,desc", [
    # Zero offset — pure passthrough, time fields untouched
    (0,
     {"time": 1700000000, "time_msc": 1700000000123, "price": 1.5},
     {"time": 1700000000, "time_msc": 1700000000123, "price": 1.5},
     "zero offset passthrough"),
    # UTC+3 — seconds-resolution time fields shift back by 3h
    (10800,
     {"time": 1700010800, "time_setup": 1700014400, "price": 1.5},
     {"time": 1700000000, "time_setup": 1700003600, "price": 1.5},
     "UTC+3 shifts seconds time fields"),
    # UTC+3 — ms-resolution fields shift by 3h*1000ms
    (10800,
     {"time_msc": 1700010800123, "time_setup_msc": 1700014400456},
     {"time_msc": 1700000000123, "time_setup_msc": 1700003600456},
     "UTC+3 shifts ms time fields"),
    # Mixed — both seconds and ms in same struct
    (10800,
     {"time": 1700010800, "time_msc": 1700010800999, "volume": 0.1},
     {"time": 1700000000, "time_msc": 1700000000999, "volume": 0.1},
     "mixed seconds + ms + non-time"),
    # Negative offset (west-of-UTC broker)
    (-18000,
     {"time": 1699982000},
     {"time": 1700000000},
     "negative offset (UTC-5)"),
    # Zero/falsy time values are 'unset' in MT5 — must NOT be shifted
    (10800,
     {"time": 0, "time_done": 0, "time_msc": 0},
     {"time": 0, "time_done": 0, "time_msc": 0},
     "zero time values stay zero"),
    # Non-time fields are never touched even when their name looks numeric
    (10800,
     {"price": 1.18, "spread": 30, "volume": 0.5},
     {"price": 1.18, "spread": 30, "volume": 0.5},
     "non-time fields never shifted"),
])
def test_to_dict(monkeypatch, offset, fields_in, fields_out, desc):
    monkeypatch.setattr(mc, "UTC_OFFSET_SECONDS", offset)
    assert mc.to_dict(_make_nt(**fields_in)) == fields_out
