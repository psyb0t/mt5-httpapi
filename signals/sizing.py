"""Volume and legs for one signal on one target.

All arithmetic is Decimal: lots and prices are exact decimals at the broker,
and a float like 0.1 + 0.2 would round a volume onto the wrong lot step.
Volumes are always rounded down, so rounding never adds risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from signals.config import Target
from signals.constants import (
    MAX_LEGS,
    NETTING_TP_LAST,
    SIDE_BUY,
    SIZING_FIXED,
    SIZING_MULTIPLIER,
)
from signals.model import Leg, Signal

ZERO = Decimal(0)
HUNDRED = Decimal(100)


class Skip(Exception):
    """The signal is not traded on this target. ``reason`` is a stable
    snake_case word for the logs, ``detail`` the numbers behind it."""

    def __init__(self, reason: str, **detail: Any) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


@dataclass(frozen=True)
class SymbolSpec:
    """The parts of GET /symbols/<symbol> that sizing needs."""

    point: Decimal
    tick_size: Decimal
    tick_value: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    bid: Decimal
    ask: Decimal

    @classmethod
    def from_info(cls, info: dict[str, Any]) -> SymbolSpec:
        def dec(key: str) -> Decimal:
            value = info.get(key)
            if value is None:
                raise Skip("symbol_info_incomplete", field=key)
            return Decimal(str(value))

        spec = cls(
            point=dec("point"),
            tick_size=dec("trade_tick_size"),
            tick_value=dec("trade_tick_value"),
            volume_min=dec("volume_min"),
            volume_max=dec("volume_max"),
            volume_step=dec("volume_step"),
            bid=dec("bid"),
            ask=dec("ask"),
        )
        if spec.volume_step <= 0 or spec.tick_size <= 0 or spec.point <= 0:
            raise Skip("symbol_info_incomplete", field="volume_step, trade_tick_size or point")
        return spec


def floor_to_step(volume: Decimal, step: Decimal) -> Decimal:
    """``volume`` rounded down to a whole number of ``step``."""
    return (volume / step).to_integral_value(rounding=ROUND_FLOOR) * step


def entry_price(signal: Signal, spec: SymbolSpec) -> Decimal:
    """The price a market order fills at: ask for a buy, bid for a sell."""
    return spec.ask if signal.side == SIDE_BUY else spec.bid


def total_volume(
    target: Target,
    signal: Signal,
    spec: SymbolSpec,
    equity: Decimal,
    price: Decimal,
) -> Decimal:
    """The signal's whole volume on this target, before the split into legs."""
    sizing = target.sizing
    if sizing.mode == SIZING_FIXED:
        volume = sizing.lots
    elif sizing.mode == SIZING_MULTIPLIER:
        lots = signal.lots if signal.lots is not None else sizing.default_lots
        if lots is None:
            raise Skip("no_lots_in_signal")
        volume = lots * sizing.factor
    else:
        if not signal.sls:
            raise Skip("no_sl")
        # The farthest SL is the most a leg can lose, so the risk stays
        # within percent even when legs carry different SLs.
        distance = max(abs(price - sl) for sl in signal.sls)
        if distance <= 0:
            raise Skip("sl_at_entry")
        loss_per_lot = distance / spec.tick_size * spec.tick_value
        if loss_per_lot <= 0:
            raise Skip("symbol_info_incomplete", field="trade_tick_value")
        volume = equity * sizing.percent / HUNDRED / loss_per_lot

    if target.max_lots is not None and volume > target.max_lots:
        volume = target.max_lots
    return volume


def _weights(target: Target, legs: int) -> list[Decimal]:
    if not target.tp_split:
        return [Decimal(1)] * legs
    return list(target.tp_split[:legs])


def _split_steps(volume: Decimal, step: Decimal, weights: list[Decimal]) -> list[int]:
    """Whole lot steps per leg, in proportion to ``weights``.

    Flooring each leg on its own lost volume: 0.03 lots split 34/33/33 is
    0.0102/0.0099/0.0099, which floored to 0.01/0/0. The steps of the floored
    total are handed out by largest remainder instead (ties to the nearer
    leg), so the legs add up to the total and 0.03 becomes 0.01 three times.
    """
    total = int((volume / step).to_integral_value(rounding=ROUND_FLOOR))
    weight_sum = sum(weights)
    shares = [total * weight / weight_sum for weight in weights]
    steps = [int(share.to_integral_value(rounding=ROUND_FLOOR)) for share in shares]
    by_remainder = sorted(range(len(shares)), key=lambda i: (-(shares[i] - steps[i]), i))
    for index in by_remainder[: total - sum(steps)]:
        steps[index] += 1
    return steps


def build_legs(
    target: Target,
    signal: Signal,
    spec: SymbolSpec,
    volume: Decimal,
    hedging: bool,
) -> list[Leg]:
    """Split ``volume`` into one leg per TP.

    Leg n gets TPn and SLn, or SL1 when the signal has fewer SLs than TPs.
    With tp_split, only as many TPs as it has entries are used. On a netting
    account one position per symbol is all there is, so the whole volume is
    one leg with the first or last TP (netting_tp).

    Legs that would fall below the symbol's minimum volume are dropped from
    the far end, and their volume goes to the remaining legs.
    """
    sls = signal.sls
    tps = signal.tps[:MAX_LEGS]
    first_sl = sls[0] if sls else ZERO

    if not hedging or len(tps) <= 1:
        if tps:
            tp = tps[-1] if (not hedging and target.netting_tp == NETTING_TP_LAST) else tps[0]
        else:
            tp = ZERO
        legs_tps = [tp]
    else:
        legs_tps = list(tps[: len(_weights(target, len(tps)))])

    count = len(legs_tps)
    while count >= 1:
        weights = _weights(target, count) if count > 1 else [Decimal(1)]
        volumes = [
            min(steps * spec.volume_step, spec.volume_max)
            for steps in _split_steps(volume, spec.volume_step, weights)
        ]
        if all(leg_volume >= spec.volume_min for leg_volume in volumes):
            return [
                Leg(
                    number=index + 1,
                    volume=leg_volume,
                    sl=sls[index] if index < len(sls) else first_sl,
                    tp=legs_tps[index],
                )
                for index, leg_volume in enumerate(volumes)
            ]
        count -= 1

    raise Skip(
        "volume_below_minimum",
        volume=str(volume),
        volume_min=str(spec.volume_min),
    )
