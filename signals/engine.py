"""From a source message to orders on every target that copies the source.

Per target, in order: map the symbol, read the symbol from the terminal, check
that the price has not already passed the SL or TP1 and is within the entry
slippage, check the open-signal limits, size the volume, split it into legs
and send one order per leg. Any check that fails skips the signal on that
target only and logs why. A leg the terminal fails on is logged and the other
legs are still sent.

One lock per terminal keeps two signals for the same account from both
passing a limit before either has opened anything.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from signals.config import Rules, Source, Target
from signals.constants import (
    CLIENT_ORDER_ID_RE,
    HTTP_CREATED,
    MARGIN_MODE_HEDGING,
    SIDE_BUY,
)
from signals.errors import TerminalRejected, TerminalUnreachable
from signals.model import Leg, Message, Signal
from signals.parser import parse
from signals.sizing import SymbolSpec, Skip, build_legs, entry_price, total_volume
from signals.state import State
from signals.terminal import TerminalClient

logger = logging.getLogger(__name__)


def client_order_id(source: str, message_id: str, leg: int) -> str:
    return f"{source}:{message_id}:{leg}"


def _signal_key(item: dict[str, Any]) -> tuple[str, str]:
    """Which signal an open position or pending order belongs to.

    Legs of one signal share "<source>:<message id>" in their comment, so they
    count once. A broker that rewrites comments leaves only the ticket, and
    each leg then counts on its own.
    """
    match = CLIENT_ORDER_ID_RE.match(str(item.get("comment") or ""))
    if match is not None:
        return match.group(1), match.group(2)
    return "ticket", str(item.get("ticket"))


def open_counts(
    items: list[dict[str, Any]],
    rules: Rules,
    source: Source,
    target: Target,
    symbol: str,
) -> dict[str, int]:
    """Open signals per limit, from the terminal's positions and pending
    orders."""
    magics = rules.magics()
    copier = [item for item in items if item.get("magic") in magics]
    per_symbol_pool = items if target.limits.count_manual else copier
    return {
        "max_open_per_symbol": len(
            {_signal_key(item) for item in per_symbol_pool if item.get("symbol") == symbol}
        ),
        "max_open_per_source": len(
            {_signal_key(item) for item in copier if item.get("magic") == source.magic}
        ),
        "max_open_per_source_symbol": len(
            {
                _signal_key(item)
                for item in copier
                if item.get("magic") == source.magic and item.get("symbol") == symbol
            }
        ),
    }


def check_levels(signal: Signal, price: Decimal, target: Target, point: Decimal) -> None:
    """Skip a signal the price has already moved past."""
    if target.require_sl and not signal.sls:
        raise Skip("no_sl")
    buy = signal.side == SIDE_BUY
    for sl in signal.sls:
        if (buy and sl >= price) or (not buy and sl <= price):
            raise Skip("price_past_sl", price=str(price), sl=str(sl))
    if signal.tps:
        tp = signal.tps[0]
        if (buy and tp <= price) or (not buy and tp >= price):
            raise Skip("price_past_tp", price=str(price), tp=str(tp))

    limit = target.max_entry_slippage_points
    if limit is None or signal.entry_low is None or signal.entry_high is None:
        return
    worse = price - signal.entry_high if buy else signal.entry_low - price
    if worse > 0 and worse / point > limit:
        raise Skip(
            "entry_slippage",
            price=str(price),
            entry_low=str(signal.entry_low),
            entry_high=str(signal.entry_high),
            points=str(worse / point),
        )


def broker_symbol(rules: Rules, target: Target, symbol: str) -> str:
    """The channel's symbol as this broker names it: the global map first,
    then the target's own map, or the global name plus the target's suffix."""
    canonical = rules.symbols.get(symbol, symbol)
    mapped = target.symbols.get(canonical.upper())
    if mapped:
        return mapped
    return f"{canonical}{target.symbol_suffix}"


class Engine:
    def __init__(self, rules: Rules, client: TerminalClient, state: State) -> None:
        self._rules = rules
        self._client = client
        self._state = state
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # The websocket and a catch-up poll can hand over the same message at
        # the same moment; the second must not start before the first is
        # recorded as seen.
        self._in_flight: set[tuple[str, str]] = set()

    async def handle(self, message: Message) -> None:
        """Trade one message on every target of its source, once."""
        key = (message.source, message.id)
        if key in self._in_flight or self._state.is_seen(*key):
            return
        self._in_flight.add(key)
        source = self._rules.sources[message.source]
        fields = {"source": message.source, "message_id": message.id}
        try:
            age = (datetime.now(timezone.utc) - message.time).total_seconds()
            if age > self._rules.max_signal_age_seconds:
                logger.info("message too old to trade", extra={**fields, "age_seconds": round(age)})
                return
            signal = parse(self._rules.parsers[source.parser], message.text)
            if signal is None:
                logger.debug("message is not a signal", extra=fields)
                return
            logger.info(
                "signal parsed",
                extra={
                    **fields,
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "sls": [str(sl) for sl in signal.sls],
                    "tps": [str(tp) for tp in signal.tps],
                },
            )
            await asyncio.gather(
                *(
                    self._trade(target, source, message, signal)
                    for target in self._rules.targets_for(source.name)
                )
            )
        finally:
            self._state.mark_seen(*key)
            self._in_flight.discard(key)

    async def _trade(
        self,
        target: Target,
        source: Source,
        message: Message,
        signal: Signal,
    ) -> None:
        fields = {"source": source.name, "message_id": message.id, "terminal": target.terminal}
        async with self._locks[target.terminal]:
            try:
                await self._trade_locked(target, source, message, signal, fields)
            except Skip as skip:
                logger.info(
                    "signal skipped",
                    extra={**fields, "reason": skip.reason, **skip.detail},
                )
            except (TerminalUnreachable, TerminalRejected) as err:
                logger.warning(
                    "terminal call failed, signal not traded",
                    extra={**fields, "reason": type(err).__name__, "error": str(err)},
                )
            except Exception:  # noqa: BLE001 - one target's failure must not stop the others
                logger.exception("signal failed on this target", extra=fields)

    async def _trade_locked(
        self,
        target: Target,
        source: Source,
        message: Message,
        signal: Signal,
        fields: dict[str, Any],
    ) -> None:
        symbol = broker_symbol(self._rules, target, signal.symbol)
        fields["symbol"] = symbol
        info = await self._client.symbol(target, symbol)
        if info is None:
            raise Skip("unknown_symbol")
        spec = SymbolSpec.from_info(info)
        price = entry_price(signal, spec)
        check_levels(signal, price, target, spec.point)

        items = await self._client.positions(target) + await self._client.orders(target)
        counts = open_counts(items, self._rules, source, target, symbol)
        for name, count in counts.items():
            limit = getattr(target.limits, name)
            if limit is not None and count >= limit:
                raise Skip(name, open=count, limit=limit)

        account = await self._client.account(target)
        equity = Decimal(str(account.get("equity") or 0))
        hedging = account.get("margin_mode") == MARGIN_MODE_HEDGING
        volume = total_volume(target, signal, spec, equity, price)
        legs = build_legs(target, signal, spec, volume, hedging)
        # A TP without a leg (too little volume, a netting account, a short
        # tp_split) is otherwise invisible: only the legs sent are logged.
        logger.info(
            "legs planned",
            extra={
                **fields,
                "volume": str(volume),
                "legs": [str(leg.volume) for leg in legs],
                "tps_without_leg": max(len(signal.tps) - len(legs), 0),
            },
        )

        dry_run = self._rules.dry_run or target.dry_run
        for leg in legs:
            await self._send(target, source, message, signal, symbol, leg, dry_run, fields)

    async def _send(
        self,
        target: Target,
        source: Source,
        message: Message,
        signal: Signal,
        symbol: str,
        leg: Leg,
        dry_run: bool,
        fields: dict[str, Any],
    ) -> None:
        order: dict[str, Any] = {
            "symbol": symbol,
            "type": "BUY" if signal.side == SIDE_BUY else "SELL",
            "volume": float(leg.volume),
            "sl": float(leg.sl),
            "tp": float(leg.tp),
            "deviation": target.deviation,
            "magic": source.magic,
            "client_order_id": client_order_id(source.name, message.id, leg.number),
        }
        if target.type_filling:
            order["type_filling"] = target.type_filling
        leg_fields = {
            **fields,
            "leg": leg.number,
            "side": signal.side,
            "volume": str(leg.volume),
            "sl": str(leg.sl),
            "tp": str(leg.tp),
            "client_order_id": order["client_order_id"],
        }
        if dry_run:
            logger.info("dry run, order not sent", extra=leg_fields)
            return

        # A leg is sent on its own: when the terminal fails on this one the
        # later legs still go out, because the message is marked seen either
        # way and nothing would send them again.
        try:
            status, body = await self._client.place(target, order)
        except (TerminalUnreachable, TerminalRejected) as err:
            logger.warning(
                "terminal call failed, leg not sent",
                extra={**leg_fields, "reason": type(err).__name__, "error": str(err)},
            )
            return
        if body.get("duplicate"):
            logger.info(
                "order already placed",
                extra={**leg_fields, "existing": body.get("source")},
            )
            return
        if status == HTTP_CREATED:
            logger.info("order placed", extra={**leg_fields, "ticket": body.get("order")})
            return
        logger.warning(
            "broker refused the order",
            extra={
                **leg_fields,
                "retcode": body.get("retcode"),
                "broker_comment": body.get("comment"),
            },
        )
