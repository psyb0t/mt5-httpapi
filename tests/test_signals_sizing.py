"""Volume and legs: rounded down to the lot step, split by tp_split, never
below the symbol's minimum."""

from dataclasses import replace
from decimal import Decimal

import pytest

from signals.config import Sizing
from signals.model import Signal
from signals.sizing import Skip, SymbolSpec, build_legs, floor_to_step, total_volume
from tests.signals_helpers import SYMBOL_INFO, rules_from

D = Decimal
SPEC = SymbolSpec.from_info(SYMBOL_INFO)
SIGNAL = Signal(
    symbol="TESTUSD",
    side="buy",
    entry_low=D("100"),
    entry_high=D("100"),
    sls=(D("99"),),
    tps=(D("101"), D("102"), D("103")),
    lots=D("1"),
)


def _target(tmp_path, **changes):
    return replace(rules_from(tmp_path).targets[0], **changes)


def test_floor_to_step_never_rounds_up():
    assert floor_to_step(D("0.129"), D("0.01")) == D("0.12")
    assert floor_to_step(D("0.3"), D("0.1")) == D("0.3")
    assert floor_to_step(D("7"), D("5")) == D("5")


def test_multiplier_scales_the_channel_lots(tmp_path):
    assert total_volume(_target(tmp_path), SIGNAL, SPEC, D("10000"), D("100.10")) == D("0.1")


def test_multiplier_uses_default_lots_when_the_signal_has_none(tmp_path):
    signal = replace(SIGNAL, lots=None)
    assert total_volume(_target(tmp_path), signal, SPEC, D("10000"), D("100.10")) == D("0.01")


def test_multiplier_without_any_lots_skips(tmp_path):
    target = _target(tmp_path, sizing=Sizing(mode="multiplier", factor=D("0.1")))
    with pytest.raises(Skip, match="no_lots_in_signal"):
        total_volume(target, replace(SIGNAL, lots=None), SPEC, D("10000"), D("100.10"))


def test_fixed_sizing_and_the_max_lots_cap(tmp_path):
    target = _target(tmp_path, sizing=Sizing(mode="fixed", lots=D("3")), max_lots=D("2"))
    assert total_volume(target, SIGNAL, SPEC, D("10000"), D("100.10")) == D("2")


def test_risk_percent_risks_the_percent_at_the_farthest_sl(tmp_path):
    target = _target(tmp_path, sizing=Sizing(mode="risk_percent", percent=D("1")))
    signal = replace(SIGNAL, sls=(D("99.10"), D("98.10")))
    # 1% of 10000 is 100. The farthest SL is 2.00 away at 100.10: 200 ticks
    # of 1.0 is 200 per lot, so 0.5 lots.
    assert total_volume(target, signal, SPEC, D("10000"), D("100.10")) == D("0.5")


def test_legs_follow_tp_split_with_their_own_levels(tmp_path):
    signal = replace(SIGNAL, sls=(D("99"), D("98.5")))
    legs = build_legs(_target(tmp_path), signal, SPEC, D("1"), hedging=True)
    assert [(leg.number, leg.volume, leg.sl, leg.tp) for leg in legs] == [
        (1, D("0.50"), D("99"), D("101")),
        (2, D("0.30"), D("98.5"), D("102")),
        (3, D("0.20"), D("99"), D("103")),
    ]


def test_legs_split_equally_without_tp_split(tmp_path):
    legs = build_legs(_target(tmp_path, tp_split=()), SIGNAL, SPEC, D("0.3"), hedging=True)
    assert [leg.volume for leg in legs] == [D("0.10")] * 3


def test_tp_split_shorter_than_the_tps_uses_only_that_many(tmp_path):
    legs = build_legs(_target(tmp_path, tp_split=(D("1"), D("1"))), SIGNAL, SPEC, D("0.2"), hedging=True)
    assert [leg.tp for leg in legs] == [D("101"), D("102")]


def test_legs_below_the_minimum_are_dropped_from_the_far_end(tmp_path):
    # 0.02 is two steps of 0.01, so a third leg would be empty: two legs of
    # 0.01 are kept.
    legs = build_legs(_target(tmp_path), SIGNAL, SPEC, D("0.02"), hedging=True)
    assert [(leg.volume, leg.tp) for leg in legs] == [(D("0.01"), D("101")), (D("0.01"), D("102"))]


@pytest.mark.parametrize("split", [(D("34"), D("33"), D("33")), (D("50"), D("30"), D("20"))])
def test_the_legs_add_up_to_the_whole_volume(tmp_path, split):
    """Regression: each leg was floored on its own, so 0.03 split 34/33/33
    became 0.0102/0.0099/0.0099, floored to 0.01/0/0, and the copier opened
    two legs of 0.01 and dropped TP3 and a third of the volume."""
    legs = build_legs(_target(tmp_path, tp_split=split), SIGNAL, SPEC, D("0.03"), hedging=True)
    assert [(leg.volume, leg.tp) for leg in legs] == [
        (D("0.01"), D("101")),
        (D("0.01"), D("102")),
        (D("0.01"), D("103")),
    ]


def test_left_over_steps_go_to_the_largest_remainders(tmp_path):
    # 0.07 split 50/30/20 is 3.5/2.1/1.4 steps: 3/2/1 plus the spare step for
    # leg 1, whose remainder is largest.
    legs = build_legs(_target(tmp_path), SIGNAL, SPEC, D("0.07"), hedging=True)
    assert [leg.volume for leg in legs] == [D("0.04"), D("0.02"), D("0.01")]


def test_a_volume_under_the_minimum_skips(tmp_path):
    with pytest.raises(Skip, match="volume_below_minimum"):
        build_legs(_target(tmp_path), SIGNAL, SPEC, D("0.005"), hedging=True)


def test_a_netting_account_gets_one_leg_with_the_chosen_tp(tmp_path):
    first = build_legs(_target(tmp_path), SIGNAL, SPEC, D("1"), hedging=False)
    last = build_legs(_target(tmp_path, netting_tp="last"), SIGNAL, SPEC, D("1"), hedging=False)
    assert [(leg.volume, leg.tp) for leg in first] == [(D("1.00"), D("101"))]
    assert [(leg.volume, leg.tp) for leg in last] == [(D("1.00"), D("103"))]


def test_a_signal_without_tps_is_one_leg_without_tp(tmp_path):
    legs = build_legs(_target(tmp_path), replace(SIGNAL, tps=()), SPEC, D("0.1"), hedging=True)
    assert [(leg.volume, leg.tp) for leg in legs] == [(D("0.10"), D("0"))]


def test_a_leg_never_exceeds_the_symbol_maximum(tmp_path):
    legs = build_legs(_target(tmp_path, tp_split=()), replace(SIGNAL, tps=(D("101"),)), SPEC, D("80"), hedging=True)
    assert [leg.volume for leg in legs] == [D("50")]
