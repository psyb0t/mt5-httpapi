"""Tests for parse_duration_to_seconds — the per-terminal utc_offset parser.
Bare numbers are interpreted as hours (most brokers run on whole-hour
offsets), explicit suffixed forms ('3h30m', '-2h', '90m') are exact.
"""

import pytest

from mt5api.config import parse_duration_to_seconds


@pytest.mark.parametrize("value,expected", [
    # Empty / None
    (None, 0),
    ("", 0),
    (0, 0),
    # Bare numbers → hours
    (3, 10800),
    ("3", 10800),
    ("3.5", 12600),
    ("-2", -7200),
    # Suffixed forms
    ("3h", 10800),
    ("3h30m", 12600),
    ("90m", 5400),
    ("45s", 45),
    ("1h2m3s", 3723),
    ("-2h", -7200),
    ("-1h30m", -5400),
    # Whitespace tolerated
    ("  3h  ", 10800),
])
def test_parse_duration_valid(value, expected):
    assert parse_duration_to_seconds(value) == expected


@pytest.mark.parametrize("value", [
    "garbage",
    "3x",          # unknown unit
    "h3",          # number must precede unit
    "3h-",         # malformed sign
])
def test_parse_duration_invalid(value):
    with pytest.raises(ValueError):
        parse_duration_to_seconds(value)
