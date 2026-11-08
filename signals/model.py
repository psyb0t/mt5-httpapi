"""Plain data passed between sources, the parser and the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Message:
    """One text message from a source, whatever its type.

    ``id`` is unique within the source and short enough for a client_order_id
    (constants.MESSAGE_ID_RE); sources hash anything longer.
    """

    source: str
    id: str
    time: datetime
    text: str


@dataclass(frozen=True)
class Signal:
    """A parsed trade signal. Prices are as the channel wrote them.

    ``sls`` and ``tps`` are ordered by their number (SL1, SL2 ... / TP1, TP2
    ...). ``entry_low``/``entry_high`` are equal for a single entry price and
    None when the signal gives none.
    """

    symbol: str
    side: str
    entry_low: Decimal | None = None
    entry_high: Decimal | None = None
    sls: tuple[Decimal, ...] = field(default_factory=tuple)
    tps: tuple[Decimal, ...] = field(default_factory=tuple)
    lots: Decimal | None = None


@dataclass(frozen=True)
class Leg:
    """One order to send: a share of the signal's volume with its own SL/TP.
    ``tp`` is 0 for no take profit."""

    number: int
    volume: Decimal
    sl: Decimal
    tp: Decimal
