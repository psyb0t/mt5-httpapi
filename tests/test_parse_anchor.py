"""Tests for the `from`/`to` query value parser. Covers unix-seconds,
date-only, full-datetime, malformed input, and the underscore-as-numeric-
literal trap that bit us during dev.
"""

import pytest

from mt5api.handlers.symbols import _parse_anchor


@pytest.mark.parametrize("value,expected", [
    # Unix seconds
    ("0", 0),
    ("1700000000", 1700000000),
    ("-1", -1),
    # Date only — midnight UTC
    ("2024_01_15", 1705276800),
    ("1970_01_01", 0),
    # Full datetime — real UTC
    ("2024_01_15_14_30_00", 1705329000),
    ("2024_01_15_00_00_00", 1705276800),
    # Whitespace tolerated
    ("  1700000000  ", 1700000000),
    ("  2024_01_15  ", 1705276800),
])
def test_parse_anchor_valid(value, expected):
    assert _parse_anchor(value) == expected


@pytest.mark.parametrize("value", [
    None,
    "",
    "   ",
    "not_a_date",
    "2024_13_01",          # invalid month
    "2024_01_32",          # invalid day
    "2024_01_15_25_00_00",  # invalid hour
    "2024_01",              # 2 components — neither date nor datetime
    "2024_01_15_14_30",     # 5 components — bogus
    "abc_def_ghi",          # all non-numeric
    # Python's int() accepts digit separators ("2024_01_15" → 20240115).
    # The parser must reject any underscored input from the int branch.
    "1_700_000_000",
])
def test_parse_anchor_invalid(value):
    assert _parse_anchor(value) is None


def test_parse_anchor_int_input():
    """Non-string inputs are coerced via str()."""
    assert _parse_anchor(1700000000) == 1700000000
