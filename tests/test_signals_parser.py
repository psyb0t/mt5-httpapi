"""The parser turns message text into a Signal with the source's regexes."""

from decimal import Decimal

from signals.parser import parse
from tests.signals_helpers import SIGNAL_TEXT, rules_from


def _parser(tmp_path):
    return rules_from(tmp_path).parsers["classic"]


def test_a_full_signal_is_read(tmp_path):
    signal = parse(_parser(tmp_path), SIGNAL_TEXT)
    assert signal is not None
    assert signal.symbol == "TESTUSD"
    assert signal.side == "buy"
    assert signal.entry_low == signal.entry_high == Decimal("100.00")
    assert signal.sls == (Decimal("99.00"),)
    assert signal.tps == (Decimal("101.00"), Decimal("102.00"), Decimal("103.00"))
    assert signal.lots == Decimal("1")


def test_unnumbered_levels_are_numbered_in_order(tmp_path):
    signal = parse(_parser(tmp_path), "TESTUSD sell\nSL 101\nTP 99\nTP 98")
    assert signal.side == "sell"
    assert signal.tps == (Decimal("99"), Decimal("98"))


def test_numbered_levels_keep_their_number_whatever_the_order(tmp_path):
    signal = parse(_parser(tmp_path), "TESTUSD buy\nTP3 103\nTP1 101\nTP2 102\nSL2 98\nSL1 99")
    assert signal.tps == (Decimal("101"), Decimal("102"), Decimal("103"))
    assert signal.sls == (Decimal("99"), Decimal("98"))


def test_an_entry_range_is_read_low_to_high(tmp_path):
    signal = parse(_parser(tmp_path), "TESTUSD buy 100.50 - 100.10\nSL 99")
    assert (signal.entry_low, signal.entry_high) == (Decimal("100.10"), Decimal("100.50"))


def test_the_side_map_and_comma_decimals(tmp_path):
    signal = parse(_parser(tmp_path), "testusd LONG @ 100,25\nsl 99,5\ntp 101,75")
    assert signal.side == "buy"
    assert signal.entry_low == Decimal("100.25")
    assert signal.sls == (Decimal("99.5"),)
    assert signal.tps == (Decimal("101.75"),)


def test_the_channel_symbol_is_kept_for_the_symbol_map(tmp_path):
    signal = parse(_parser(tmp_path), "TESTGOLD short\nSL 101")
    assert signal.symbol == "TESTGOLD"
    assert signal.side == "sell"


def test_a_message_that_is_not_a_signal_gives_none(tmp_path):
    assert parse(_parser(tmp_path), "Good morning traders, TP1 hit on yesterday's trade") is None
    assert parse(_parser(tmp_path), "") is None


def test_missing_optional_parts_stay_empty(tmp_path):
    signal = parse(_parser(tmp_path), "TESTUSD buy")
    assert signal.entry_low is None
    assert signal.sls == ()
    assert signal.tps == ()
    assert signal.lots is None


def test_the_example_regexes_read_unnumbered_and_numbered_levels(monkeypatch):
    """The SL/TP number group once swallowed the first digit of the price:
    "SL 990" read as SL9 at 90. It now only matches a digit glued to SL/TP."""
    from pathlib import Path

    import yaml

    from signals.config import load_rules, terminal_keys

    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("MYBOT_TOKEN", "fake-token")
    config = yaml.safe_load((repo / "config" / "config.yaml.example").read_text(encoding="utf-8"))
    rules = load_rules(str(repo / "config" / "signals.yml.example"), terminal_keys(config))
    parser = rules.parsers["classic"]

    signal = parse(parser, "GOLD BUY 1000 - 1005\nSL 990\nTP 1010\nTP 1020\n0.5 lots")
    assert signal.sls == (Decimal("990"),)
    assert signal.tps == (Decimal("1010"), Decimal("1020"))
    assert (signal.entry_low, signal.entry_high, signal.lots) == (Decimal("1000"), Decimal("1005"), Decimal("0.5"))

    signal = parse(parser, "GOLD SELL now @ 1000\nSL1: 1010\nSL2: 1015\nTP1: 990\nTP2: 980")
    assert signal.side == "sell"
    assert signal.sls == (Decimal("1010"), Decimal("1015"))
    assert signal.tps == (Decimal("990"), Decimal("980"))
