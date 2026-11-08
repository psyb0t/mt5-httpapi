"""Signal sources: one module per ``type`` in signals.yml.

Every source turns its feed into model.Message values and hands them to the
engine. Parsing, sizing, limits and orders do not know where a message came
from, so a new type is one module here plus its branch in
config._build_source.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from signals.model import Message

Emit = Callable[[Message], Awaitable[None]]
