"""Turn a message's text into a Signal with the source's regexes.

A message is a signal when the parser's ``open`` regex matches it with a
side the side map knows. SL and TP lines are found with ``finditer``: a match
with an ``n`` group (SL2, TP3) takes that number, one without takes the next
free number in the order it appears, so "TP 2360 / TP 2370" reads as TP1 and
TP2.

TODO: follow-up messages (close now, move SL to breakeven, cancel, TP1 hit)
and edits of a signal already traded are not parsed. Trades opened from a
signal are managed by hand after they open.
TODO: pending orders. A signal is always traded at market; "BUY LIMIT"
signals are traded at market too when the entry guard allows it.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from signals.config import Parser
from signals.model import Signal


def _number(text: str | None) -> Decimal | None:
    if text is None:
        return None
    cleaned = text.strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value


def _numbered(pattern: re.Pattern[str] | None, text: str) -> tuple[Decimal, ...]:
    """Prices from every match, ordered by their number."""
    if pattern is None:
        return ()
    found: dict[int, Decimal] = {}
    unnumbered: list[Decimal] = []
    for match in pattern.finditer(text):
        price = _number(match.group("price"))
        if price is None:
            continue
        number_text = match.groupdict().get("n")
        if number_text and number_text.isdigit() and int(number_text) > 0:
            found.setdefault(int(number_text), price)
            continue
        unnumbered.append(price)
    next_number = 1
    for price in unnumbered:
        while next_number in found:
            next_number += 1
        found[next_number] = price
    return tuple(found[number] for number in sorted(found))


def parse(parser: Parser, text: str) -> Signal | None:
    """The signal in ``text``, or None when it is not one."""
    match = parser.open.search(text)
    if match is None:
        return None
    groups = match.groupdict()
    side = parser.side_map.get((groups.get("side") or "").strip().lower())
    symbol = (groups.get("symbol") or "").strip().upper()
    if side is None or not symbol:
        return None

    entry = _number(groups.get("entry"))
    entry2 = _number(groups.get("entry2"))
    entry_low = entry_high = entry
    if entry is not None and entry2 is not None:
        entry_low, entry_high = min(entry, entry2), max(entry, entry2)

    lots = _number(groups.get("lots"))
    if lots is None and parser.lots is not None:
        lots_match = parser.lots.search(text)
        if lots_match is not None:
            lots = _number(lots_match.group("lots"))

    return Signal(
        symbol=symbol,
        side=side,
        entry_low=entry_low,
        entry_high=entry_high,
        sls=_numbered(parser.sl, text),
        tps=_numbered(parser.tp, text),
        lots=lots,
    )
